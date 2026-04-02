"""Pylint and mypy via subprocess or Docker (isolated from server process)."""

from __future__ import annotations

import asyncio
import json
import re
import sys
import uuid
from pathlib import Path
from typing import List, Optional, Tuple

from app.core.config import Settings, get_settings
from app.models.request_models import AnalysisIssue, AnalysisRequest, AnalysisResponse
from app.services import docker_executor, feedback_service
from app.utils.logger import get_logger

logger = get_logger(__name__)


class Analyzer:
    def __init__(self, settings: Optional[Settings] = None) -> None:
        self._settings = settings or get_settings()

    async def analyse(self, req: AnalysisRequest) -> AnalysisResponse:
        if req.language != "python":
            issues = [
                AnalysisIssue(
                    tool="pylint",
                    line=0,
                    column=0,
                    code="UNSUPPORTED",
                    message=f"Analysis for '{req.language}' is not implemented; only python is supported.",
                    severity="warning",
                )
            ]
            return self._attach_feedback(
                AnalysisResponse(
                    pylint_score=None,
                    passed_threshold=True,
                    issues=issues,
                ),
                issues,
            )

        sandbox = Path(self._settings.sandbox_dir).resolve()
        sandbox.mkdir(parents=True, exist_ok=True)
        path = sandbox / f"analyse_{uuid.uuid4().hex}.py"
        path.write_text(req.code, encoding="utf-8")

        timeout_host = float(self._settings.execution_timeout_seconds)
        timeout_docker = float(self._settings.docker_analyze_timeout_seconds)
        issues: List[AnalysisIssue] = []
        pylint_score: Optional[float] = None

        try:
            if req.analysis_type in ("lint", "full"):
                if self._settings.docker_enabled:
                    pylint_score, lint_issues = await self._run_pylint_docker(
                        path, timeout_docker
                    )
                else:
                    pylint_score, lint_issues = await self._run_pylint_host(
                        path, timeout_host
                    )
                issues.extend(lint_issues)
            if req.analysis_type in ("type", "full"):
                if self._settings.docker_enabled:
                    issues.extend(await self._run_mypy_docker(path, timeout_docker))
                else:
                    issues.extend(await self._run_mypy_host(path, timeout_host))
        finally:
            path.unlink(missing_ok=True)

        threshold = self._settings.pylint_score_threshold
        if req.analysis_type == "type":
            passed = not any(
                i.tool == "mypy" for i in issues if i.severity in ("error", "warning")
            )
        elif req.analysis_type == "lint":
            passed = pylint_score is not None and pylint_score >= threshold
        else:
            lint_ok = pylint_score is not None and pylint_score >= threshold
            type_ok = not any(
                i.tool == "mypy" and i.severity == "error" for i in issues
            )
            passed = lint_ok and type_ok

        return self._attach_feedback(
            AnalysisResponse(
                pylint_score=pylint_score,
                passed_threshold=passed,
                issues=issues,
            ),
            issues,
        )

    def _attach_feedback(
        self, response: AnalysisResponse, issues: List[AnalysisIssue]
    ) -> AnalysisResponse:
        if not issues:
            return response
        try:
            summary = feedback_service.build_lint_feedback_summary(issues)
            return response.model_copy(update={"feedback": summary})
        except Exception:  # noqa: BLE001
            logger.exception("lint_feedback_generation_failed")
            return response

    async def _run_pylint_docker(
        self, path: Path, timeout: float
    ) -> Tuple[Optional[float], List[AnalysisIssue]]:
        rel = path.name
        res = await docker_executor.run_with_image_fallback(
            host_mount=path.parent.resolve(),
            workdir_container=docker_executor.CONTAINER_WORKSPACE,
            argv_inner=[
                "python",
                "-m",
                "pylint",
                f"/workspace/{rel}",
                "--output-format=json",
                "--score=y",
            ],
            settings=self._settings,
            timeout_seconds=timeout,
        )
        if res.docker_unavailable or res.timed_out or res.error_message:
            msg = res.error_message or res.stderr.strip() or "Docker execution failed"
            return None, [
                AnalysisIssue(
                    tool="pylint",
                    line=0,
                    code="DOCKER",
                    message=msg[:2000],
                    severity="error",
                )
            ]
        return self._parse_pylint_output(res.stdout, res.stderr, res.exit_code)

    async def _run_pylint_host(
        self, path: Path, timeout: float
    ) -> Tuple[Optional[float], List[AnalysisIssue]]:
        proc = await asyncio.create_subprocess_exec(
            sys.executable,
            "-m",
            "pylint",
            str(path),
            "--output-format=json",
            "--score=y",
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            cwd=str(path.parent),
        )
        try:
            stdout_b, stderr_b = await asyncio.wait_for(proc.communicate(), timeout=timeout)
        except TimeoutError:
            proc.kill()
            await proc.wait()
            return None, [
                AnalysisIssue(
                    tool="pylint",
                    line=0,
                    code="TIMEOUT",
                    message="pylint timed out",
                    severity="error",
                )
            ]

        text_out = (stdout_b or b"").decode("utf-8", errors="replace").strip()
        err_out = (stderr_b or b"").decode("utf-8", errors="replace").strip()
        return self._parse_pylint_output(text_out, err_out, proc.returncode)

    def _parse_pylint_output(
        self,
        text_out: str,
        err_out: str,
        returncode: Optional[int],
    ) -> Tuple[Optional[float], List[AnalysisIssue]]:
        issues: List[AnalysisIssue] = []
        score: Optional[float] = None
        for line in (err_out + "\n" + text_out).splitlines():
            m = re.search(r"Your code has been rated at ([\d.]+)/10", line)
            if m:
                score = float(m.group(1))
                break

        if text_out:
            try:
                rows = json.loads(text_out)
                if isinstance(rows, list):
                    for row in rows:
                        if not isinstance(row, dict):
                            continue
                        msg_id = str(
                            row.get("message-id")
                            or row.get("messageId")
                            or row.get("symbol")
                            or ""
                        )
                        issues.append(
                            AnalysisIssue(
                                tool="pylint",
                                line=int(row.get("line") or 0),
                                column=int(row.get("column") or 0),
                                code=msg_id,
                                message=str(row.get("message") or ""),
                                severity=str(row.get("type") or "convention"),
                            )
                        )
            except json.JSONDecodeError:
                if text_out:
                    issues.append(
                        AnalysisIssue(
                            tool="pylint",
                            line=0,
                            code="PARSE",
                            message=text_out[:2000],
                            severity="warning",
                        )
                    )

        if score is None and returncode == 0 and not issues:
            score = 10.0
        elif score is None and issues:
            score = max(0.0, 10.0 - min(10.0, len(issues) * 0.5))

        return score, issues

    async def _run_mypy_docker(self, path: Path, timeout: float) -> List[AnalysisIssue]:
        rel = path.name
        args = [
            "python",
            "-m",
            "mypy",
            f"/workspace/{rel}",
            "--show-column-numbers",
            "--show-error-codes",
            "--no-color-output",
        ]
        if self._settings.mypy_strict:
            args.append("--strict")

        res = await docker_executor.run_with_image_fallback(
            host_mount=path.parent.resolve(),
            workdir_container=docker_executor.CONTAINER_WORKSPACE,
            argv_inner=args,
            settings=self._settings,
            timeout_seconds=timeout,
        )
        if res.docker_unavailable or res.timed_out or res.error_message:
            msg = res.error_message or res.stderr.strip() or "Docker execution failed"
            return [
                AnalysisIssue(
                    tool="mypy",
                    line=0,
                    code="DOCKER",
                    message=msg[:2000],
                    severity="error",
                )
            ]
        combined = res.stdout + "\n" + res.stderr
        return self._parse_mypy_output(combined, res.exit_code)

    async def _run_mypy_host(self, path: Path, timeout: float) -> List[AnalysisIssue]:
        args = [
            sys.executable,
            "-m",
            "mypy",
            str(path),
            "--show-column-numbers",
            "--show-error-codes",
            "--no-color-output",
        ]
        if self._settings.mypy_strict:
            args.append("--strict")

        proc = await asyncio.create_subprocess_exec(
            *args,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            cwd=str(path.parent),
        )
        try:
            stdout_b, stderr_b = await asyncio.wait_for(proc.communicate(), timeout=timeout)
        except TimeoutError:
            proc.kill()
            await proc.wait()
            return [
                AnalysisIssue(
                    tool="mypy",
                    line=0,
                    code="TIMEOUT",
                    message="mypy timed out",
                    severity="error",
                )
            ]

        combined = ((stdout_b or b"") + b"\n" + (stderr_b or b"")).decode(
            "utf-8", errors="replace"
        )
        return self._parse_mypy_output(combined, proc.returncode)

    def _parse_mypy_output(self, combined: str, returncode: Optional[int]) -> List[AnalysisIssue]:
        issues: List[AnalysisIssue] = []
        line_re = re.compile(
            r"^(?P<file>[^:]+):(?P<line>\d+):(?P<col>\d+):\s*"
            r"(?P<sev>error|warning|note):\s*(?P<msg>.+?)(?:\s*\[(?P<code>[^\]]+)\])?\s*$",
            re.MULTILINE,
        )
        for m in line_re.finditer(combined):
            sev = m.group("sev") or "error"
            code = m.group("code") or ""
            issues.append(
                AnalysisIssue(
                    tool="mypy",
                    line=int(m.group("line")),
                    column=int(m.group("col")),
                    code=code,
                    message=m.group("msg").strip(),
                    severity=sev,
                )
            )

        if returncode not in (0, None) and not issues and combined.strip():
            issues.append(
                AnalysisIssue(
                    tool="mypy",
                    line=0,
                    code="MYPY",
                    message=combined.strip()[:2000],
                    severity="error",
                )
            )
        return issues
