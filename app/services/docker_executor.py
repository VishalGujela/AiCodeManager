"""Run commands inside Docker with resource limits (host isolation)."""

from __future__ import annotations

import asyncio
import shlex
import shutil
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import List, Optional, Sequence

from app.core.config import Settings, get_settings

CONTAINER_WORKSPACE = "/workspace"
CONTAINER_APP = "/app"


@dataclass
class DockerRunResult:
    stdout: str
    stderr: str
    exit_code: Optional[int]
    timed_out: bool = False
    docker_unavailable: bool = False
    error_message: Optional[str] = None


def docker_cli_available() -> bool:
    return shutil.which("docker") is not None


def _abs_host_path(path: Path) -> str:
    return str(path.resolve())


def build_docker_run_argv(
    *,
    host_mount: Path,
    container_mount: str,
    workdir: str,
    image: str,
    command: Sequence[str],
    memory: str,
    cpus: str,
    network: Optional[str],
    container_name: Optional[str] = None,
) -> List[str]:
    """Build `docker run ...` argument list (no shell)."""
    argv: List[str] = [
        "docker",
        "run",
        "--rm",
        "--memory",
        memory,
        "--cpus",
        cpus,
    ]
    if container_name:
        argv.extend(["--name", container_name])
    argv.extend(
        [
            "-v",
            f"{_abs_host_path(host_mount)}:{container_mount}",
            "-w",
            workdir,
        ]
    )
    if network:
        argv.extend(["--network", network])
    argv.append(image)
    argv.extend(list(command))
    return argv


async def _docker_rm_force(name: str) -> None:
    proc = await asyncio.create_subprocess_exec(
        "docker",
        "rm",
        "-f",
        name,
        stdout=asyncio.subprocess.DEVNULL,
        stderr=asyncio.subprocess.DEVNULL,
    )
    await proc.wait()


async def run_in_docker(
    *,
    host_mount: Path,
    container_mount: str = CONTAINER_WORKSPACE,
    workdir_container: str = CONTAINER_WORKSPACE,
    argv_inner: Sequence[str],
    image: str,
    timeout_seconds: float,
    memory: str,
    cpus: str,
    network: Optional[str],
    container_name: Optional[str] = None,
) -> DockerRunResult:
    """
    Run `argv_inner` inside container with `host_mount` bind-mounted at `container_mount`.
    """
    if not docker_cli_available():
        return DockerRunResult(
            stdout="",
            stderr="",
            exit_code=None,
            docker_unavailable=True,
            error_message="Docker is not installed or not on PATH",
        )

    cname = container_name or f"acm_{uuid.uuid4().hex[:24]}"
    cmd = build_docker_run_argv(
        host_mount=host_mount,
        container_mount=container_mount,
        workdir=workdir_container,
        image=image,
        command=argv_inner,
        memory=memory,
        cpus=cpus,
        network=network,
        container_name=cname,
    )
    proc = await asyncio.create_subprocess_exec(
        *cmd,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    try:
        stdout_b, stderr_b = await asyncio.wait_for(
            proc.communicate(),
            timeout=timeout_seconds,
        )
    except TimeoutError:
        proc.kill()
        await proc.wait()
        await _docker_rm_force(cname)
        return DockerRunResult(
            stdout="",
            stderr="",
            exit_code=None,
            timed_out=True,
            error_message="Execution timed out or container failed",
        )

    stdout = (stdout_b or b"").decode("utf-8", errors="replace")
    stderr = (stderr_b or b"").decode("utf-8", errors="replace")
    code = proc.returncode

    if code == 125:
        return DockerRunResult(
            stdout=stdout,
            stderr=stderr,
            exit_code=code,
            error_message="Docker could not start the container (image missing or invalid flags)",
        )

    return DockerRunResult(stdout=stdout, stderr=stderr, exit_code=code)


async def run_shell_in_docker(
    *,
    host_mount: Path,
    container_mount: str = CONTAINER_WORKSPACE,
    workdir_container: str = CONTAINER_WORKSPACE,
    shell_script: str,
    image: str,
    timeout_seconds: float,
    memory: str,
    cpus: str,
    network: Optional[str],
) -> DockerRunResult:
    """Run `sh -c <script>` inside the container."""
    return await run_in_docker(
        host_mount=host_mount,
        container_mount=container_mount,
        workdir_container=workdir_container,
        argv_inner=["sh", "-c", shell_script],
        image=image,
        timeout_seconds=timeout_seconds,
        memory=memory,
        cpus=cpus,
        network=network,
        container_name=None,
    )


def docker_network_or_none(settings: Optional[Settings] = None) -> Optional[str]:
    s = settings or get_settings()
    v = (s.docker_network or "").strip()
    return v if v else None


def bootstrap_python_slim_script(inner_command: str) -> str:
    """One-shot install + run for un-baked `python:3.10-slim` (needs network)."""
    return (
        "pip install -q pytest pytest-cov pytest-asyncio flake8 pylint mypy httpx && "
        + inner_command
    )


def _looks_like_missing_image(res: DockerRunResult) -> bool:
    text = ((res.stderr or "") + (res.stdout or "")).lower()
    return (
        "unable to find image" in text
        or "pull access denied" in text
        or "no such image" in text
    )


async def run_with_image_fallback(
    *,
    host_mount: Path,
    workdir_container: str,
    argv_inner: Sequence[str],
    settings: Optional[Settings] = None,
    timeout_seconds: float,
    allow_pip_bootstrap: bool = True,
) -> DockerRunResult:
    """
    Try primary `docker_image`; if the image is missing, retry once with `docker_bootstrap_image`
    and an inline `pip install` (requires outbound network).
    """
    s = settings or get_settings()
    network = docker_network_or_none(s)

    res = await run_in_docker(
        host_mount=host_mount,
        container_mount=CONTAINER_WORKSPACE,
        workdir_container=workdir_container,
        argv_inner=argv_inner,
        image=s.docker_image,
        timeout_seconds=timeout_seconds,
        memory=s.docker_memory,
        cpus=s.docker_cpus,
        network=network,
    )
    if not allow_pip_bootstrap:
        return res
    if res.timed_out or res.docker_unavailable:
        return res
    if res.exit_code != 125 and not _looks_like_missing_image(res):
        return res

    inner_sh = " ".join(shlex.quote(x) for x in argv_inner)
    script = bootstrap_python_slim_script(inner_sh)
    return await run_shell_in_docker(
        host_mount=host_mount,
        container_mount=CONTAINER_WORKSPACE,
        workdir_container=workdir_container,
        shell_script=script,
        image=s.docker_bootstrap_image,
        timeout_seconds=timeout_seconds,
        memory=s.docker_memory,
        cpus=s.docker_cpus,
        network=None,
    )
