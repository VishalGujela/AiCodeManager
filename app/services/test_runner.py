"""Pytest orchestration with JUnit XML parsing (host or Docker)."""

from __future__ import annotations

import asyncio
import sys
import time
import uuid
import xml.etree.ElementTree as ET
from pathlib import Path
from typing import List, Optional

from app.core.config import Settings, get_settings
from app.models.request_models import TestResultItem, TestRunRequest, TestRunResponse
from app.services import docker_executor, feedback_service
from app.utils.logger import get_logger

logger = get_logger(__name__)


def _project_root() -> Path:
    return Path(__file__).resolve().parent.parent.parent


class TestRunner:
    __test__ = False

    def __init__(self, settings: Optional[Settings] = None) -> None:
        self._settings = settings or get_settings()

    async def run(
        self,
        req: TestRunRequest,
        *,
        project_root: Optional[Path] = None,
    ) -> TestRunResponse:
        root = (
            project_root.resolve()
            if project_root is not None
            else _project_root().resolve()
        )
        if self._settings.docker_enabled:
            return await self._run_docker(req, project_root=root)
        return await self._run_host(req, project_root=root)

    async def _run_docker(self, req: TestRunRequest, *, project_root: Path) -> TestRunResponse:
        root = project_root.resolve()
        sandbox = root / "sandbox"
        sandbox.mkdir(parents=True, exist_ok=True)
        junit_name = f"junit_docker_{uuid.uuid4().hex}.xml"
        junit_rel = f"sandbox/{junit_name}"
        junit_host = root / junit_rel

        inner: List[str] = [
            "python",
            "-m",
            "pytest",
            *req.test_paths,
            f"--junitxml={junit_rel}",
            "-o",
            "junit_family=xunit2",
        ]
        if not req.verbose:
            inner.append("-q")
        if req.coverage:
            inner.extend(["--cov=app", "--cov-report=term-missing"])

        start = time.perf_counter()
        timeout = float(self._settings.docker_pytest_timeout_seconds)
        res = await docker_executor.run_with_image_fallback(
            host_mount=root,
            workdir_container=docker_executor.CONTAINER_WORKSPACE,
            argv_inner=inner,
            settings=self._settings,
            timeout_seconds=timeout,
        )
        duration_ms = (time.perf_counter() - start) * 1000

        try:
            if res.docker_unavailable or res.timed_out or res.error_message:
                msg = res.error_message or res.stderr.strip() or "Execution timed out or container failed"
                return self._attach_test_feedback(
                    TestRunResponse(
                        total=0,
                        passed=0,
                        failed=0,
                        errors=1,
                        skipped=0,
                        duration_ms=round(duration_ms, 1),
                        results=[
                            TestResultItem(
                                node_id="pytest",
                                outcome="error",
                                duration_ms=0.0,
                                message=msg[:2000],
                            )
                        ],
                    )
                )

            results: List[TestResultItem] = []
            total = passed = failed = errors = skipped = 0

            if junit_host.exists():
                tree = ET.parse(junit_host)
                root_el = tree.getroot()
                for case in root_el.findall(".//testcase"):
                    _append_case(case, results)

                for r in results:
                    total += 1
                    if r.outcome == "passed":
                        passed += 1
                    elif r.outcome == "failed":
                        failed += 1
                    elif r.outcome == "error":
                        errors += 1
                    elif r.outcome == "skipped":
                        skipped += 1

            if not results and res.exit_code not in (0, None):
                errors = 1
                results.append(
                    TestResultItem(
                        node_id="pytest",
                        outcome="error",
                        duration_ms=0.0,
                        message=(
                            res.stderr.strip() or res.stdout.strip() or "pytest failed; no JUnit output"
                        )[:2000],
                    )
                )

            return self._attach_test_feedback(
                TestRunResponse(
                    total=total,
                    passed=passed,
                    failed=failed,
                    errors=errors,
                    skipped=skipped,
                    duration_ms=round(duration_ms, 1),
                    results=results,
                )
            )
        finally:
            try:
                junit_host.unlink(missing_ok=True)
            except OSError:
                pass

    async def _run_host(self, req: TestRunRequest, *, project_root: Path) -> TestRunResponse:
        root = project_root.resolve()
        sandbox = root / "sandbox"
        sandbox.mkdir(parents=True, exist_ok=True)
        junit_name = f"junit_host_{uuid.uuid4().hex}.xml"
        junit_rel = f"sandbox/{junit_name}"
        junit_path = root / junit_rel

        cmd: List[str] = [
            sys.executable,
            "-m",
            "pytest",
            *req.test_paths,
            f"--junitxml={junit_rel}",
            "-o",
            "junit_family=xunit2",
        ]
        if not req.verbose:
            cmd.append("-q")
        if req.coverage:
            cmd.extend(["--cov=app", "--cov-report=term-missing"])

        start = time.perf_counter()
        proc = await asyncio.create_subprocess_exec(
            *cmd,
            stdout=asyncio.subprocess.DEVNULL,
            stderr=asyncio.subprocess.DEVNULL,
            cwd=str(root),
        )
        timeout = float(self._settings.execution_timeout_seconds * 4)
        try:
            await asyncio.wait_for(proc.wait(), timeout=timeout)
        except TimeoutError:
            proc.kill()
            await proc.wait()
            try:
                junit_path.unlink(missing_ok=True)
            except OSError:
                pass
            duration_ms = (time.perf_counter() - start) * 1000
            timeout_results = [
                TestResultItem(
                    node_id="pytest",
                    outcome="error",
                    duration_ms=0.0,
                    message=f"pytest timed out after {timeout}s",
                )
            ]
            return self._attach_test_feedback(
                TestRunResponse(
                    total=0,
                    passed=0,
                    failed=0,
                    errors=1,
                    skipped=0,
                    duration_ms=round(duration_ms, 1),
                    results=timeout_results,
                )
            )

        duration_ms = (time.perf_counter() - start) * 1000
        results: List[TestResultItem] = []
        total = passed = failed = errors = skipped = 0

        try:
            if junit_path.exists():
                tree = ET.parse(junit_path)
                root_el = tree.getroot()
                for case in root_el.findall(".//testcase"):
                    _append_case(case, results)

                for r in results:
                    total += 1
                    if r.outcome == "passed":
                        passed += 1
                    elif r.outcome == "failed":
                        failed += 1
                    elif r.outcome == "error":
                        errors += 1
                    elif r.outcome == "skipped":
                        skipped += 1
        finally:
            junit_path.unlink(missing_ok=True)

        if not results and proc.returncode != 0:
            errors = 1
            results.append(
                TestResultItem(
                    node_id="pytest",
                    outcome="error",
                    duration_ms=0.0,
                    message="pytest failed; no JUnit output parsed",
                )
            )

        return self._attach_test_feedback(
            TestRunResponse(
                total=total,
                passed=passed,
                failed=failed,
                errors=errors,
                skipped=skipped,
                duration_ms=round(duration_ms, 1),
                results=results,
            )
        )

    def _attach_test_feedback(self, response: TestRunResponse) -> TestRunResponse:
        if response.failed + response.errors == 0:
            return response
        try:
            fb = feedback_service.build_test_feedback_summary(response.results)
            return response.model_copy(update={"feedback": fb})
        except Exception:  # noqa: BLE001
            logger.exception("test_feedback_generation_failed")
            return response


def _append_case(case: ET.Element, results: List[TestResultItem]) -> None:
    classname = case.get("classname") or ""
    file_attr = case.get("file") or ""
    name = case.get("name") or "unknown"
    if file_attr and classname:
        node_id = f"{file_attr}::{classname}::{name}"
    elif classname:
        node_id = f"{classname}::{name}"
    elif file_attr:
        node_id = f"{file_attr}::{name}"
    else:
        node_id = name
    t = case.get("time")
    duration_ms = float(t) * 1000 if t else 0.0

    failure = case.find("failure")
    error_el = case.find("error")
    skipped = case.find("skipped")

    message: Optional[str] = None
    if failure is not None:
        outcome = "failed"
        message = failure.get("message") or (failure.text or "")[:2000]
    elif error_el is not None:
        outcome = "error"
        message = error_el.get("message") or (error_el.text or "")[:2000]
    elif skipped is not None:
        outcome = "skipped"
        message = skipped.get("message") or (skipped.text or "")[:500]
    else:
        outcome = "passed"

    results.append(
        TestResultItem(
            node_id=node_id,
            outcome=outcome,
            duration_ms=round(duration_ms, 1),
            message=message,
        )
    )
