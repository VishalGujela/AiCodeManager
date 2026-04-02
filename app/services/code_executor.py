"""Sandboxed code execution: Docker (preferred) or host subprocess (async)."""

from __future__ import annotations

import asyncio
import shutil
import sys
import tempfile
import time
import uuid
from pathlib import Path
from typing import Literal, Optional, Tuple

from app.core.config import Settings, get_settings
from app.models.request_models import ExecuteRequest, ExecuteResponse, ExecutionErrorInfo
from app.services import docker_executor

Language = Literal["python", "javascript", "typescript", "bash"]


def _extension(language: Language) -> str:
    return {
        "python": ".py",
        "javascript": ".js",
        "typescript": ".ts",
        "bash": ".sh",
    }[language]


def _build_host_command(language: Language, file_path: Path) -> Tuple[str, ...]:
    if language == "python":
        return (sys.executable, str(file_path))
    if language == "javascript":
        return ("node", str(file_path))
    if language == "typescript":
        if shutil.which("tsx"):
            return ("tsx", str(file_path))
        if shutil.which("npx"):
            return ("npx", "-y", "tsx", str(file_path))
        return ("node", str(file_path))
    return ("bash", str(file_path))


class CodeExecutor:
    def __init__(self, settings: Optional[Settings] = None) -> None:
        self._settings = settings or get_settings()

    def _cap_timeout(self, req_timeout: Optional[float]) -> float:
        base = float(
            req_timeout
            if req_timeout is not None
            else self._settings.execution_timeout_seconds
        )
        if self._settings.docker_enabled:
            return min(base, float(self._settings.docker_code_timeout_seconds))
        return base

    async def execute(self, req: ExecuteRequest) -> ExecuteResponse:
        if self._settings.docker_enabled:
            return await self._execute_docker(req)
        return await self._execute_host(req)

    async def _execute_docker(self, req: ExecuteRequest) -> ExecuteResponse:
        timeout = self._cap_timeout(float(req.timeout) if req.timeout else None)
        ext = _extension(req.language)
        fname = f"run_{uuid.uuid4().hex}{ext}"
        start = time.perf_counter()

        with tempfile.TemporaryDirectory(prefix="acm_exec_") as td:
            td_path = Path(td)
            file_path = td_path / fname
            file_path.write_text(req.code, encoding="utf-8")
            if req.language == "bash":
                file_path.chmod(0o700)

            rel = f"/workspace/{fname}"

            if req.language == "python":
                dres = await docker_executor.run_with_image_fallback(
                    host_mount=td_path,
                    workdir_container=docker_executor.CONTAINER_WORKSPACE,
                    argv_inner=("python", rel),
                    settings=self._settings,
                    timeout_seconds=timeout,
                )
            elif req.language == "javascript":
                dres = await docker_executor.run_in_docker(
                    host_mount=td_path,
                    workdir_container=docker_executor.CONTAINER_WORKSPACE,
                    argv_inner=("node", rel),
                    image=self._settings.docker_node_image,
                    timeout_seconds=timeout,
                    memory=self._settings.docker_memory,
                    cpus=self._settings.docker_cpus,
                    network=None,
                )
            elif req.language == "typescript":
                dres = await docker_executor.run_shell_in_docker(
                    host_mount=td_path,
                    workdir_container=docker_executor.CONTAINER_WORKSPACE,
                    shell_script=f"npx -y tsx {rel}",
                    image=self._settings.docker_node_image,
                    timeout_seconds=timeout,
                    memory=self._settings.docker_memory,
                    cpus=self._settings.docker_cpus,
                    network=None,
                )
            else:
                inner = ("sh", rel)
                dres = await docker_executor.run_with_image_fallback(
                    host_mount=td_path,
                    workdir_container=docker_executor.CONTAINER_WORKSPACE,
                    argv_inner=inner,
                    settings=self._settings,
                    timeout_seconds=timeout,
                )

            elapsed_ms = (time.perf_counter() - start) * 1000
            return self._docker_result_to_execute_response(dres, elapsed_ms)

    def _docker_result_to_execute_response(
        self, dres: docker_executor.DockerRunResult, elapsed_ms: float
    ) -> ExecuteResponse:
        err: Optional[ExecutionErrorInfo] = None
        stderr = dres.stderr or ""
        stdout = dres.stdout or ""

        if dres.docker_unavailable or dres.timed_out or dres.error_message:
            msg = dres.error_message or "Execution timed out or container failed"
            err = ExecutionErrorInfo(message=msg)
            return ExecuteResponse(
                success=False,
                stdout=stdout,
                stderr=stderr or msg,
                exit_code=dres.exit_code,
                execution_time_ms=round(elapsed_ms, 1),
                execution_error=err,
            )

        code = dres.exit_code
        return ExecuteResponse(
            success=code == 0,
            stdout=stdout,
            stderr=stderr,
            exit_code=code,
            execution_time_ms=round(elapsed_ms, 1),
        )

    async def _execute_host(self, req: ExecuteRequest) -> ExecuteResponse:
        sandbox = Path(self._settings.sandbox_dir).resolve()
        sandbox.mkdir(parents=True, exist_ok=True)

        ext = _extension(req.language)
        name = f"run_{uuid.uuid4().hex}{ext}"
        file_path = sandbox / name
        file_path.write_text(req.code, encoding="utf-8")
        if req.language == "bash":
            file_path.chmod(0o700)

        timeout = self._cap_timeout(float(req.timeout) if req.timeout else None)
        cmd = _build_host_command(req.language, file_path)

        start = time.perf_counter()
        proc: Optional[asyncio.subprocess.Process] = None
        try:
            proc = await asyncio.create_subprocess_exec(
                *cmd,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                cwd=str(sandbox),
            )
            try:
                stdout_b, stderr_b = await asyncio.wait_for(
                    proc.communicate(),
                    timeout=timeout,
                )
            except TimeoutError:
                proc.kill()
                await proc.wait()
                elapsed_ms = (time.perf_counter() - start) * 1000
                msg = f"Execution timed out after {timeout}s"
                return ExecuteResponse(
                    success=False,
                    stdout="",
                    stderr=msg,
                    exit_code=None,
                    execution_time_ms=round(elapsed_ms, 1),
                    execution_error=ExecutionErrorInfo(message=msg),
                )

            elapsed_ms = (time.perf_counter() - start) * 1000
            exit_code = proc.returncode
            stdout = (stdout_b or b"").decode("utf-8", errors="replace")
            stderr = (stderr_b or b"").decode("utf-8", errors="replace")
            return ExecuteResponse(
                success=exit_code == 0,
                stdout=stdout,
                stderr=stderr,
                exit_code=exit_code,
                execution_time_ms=round(elapsed_ms, 1),
            )
        finally:
            try:
                file_path.unlink(missing_ok=True)
            except OSError:
                pass
