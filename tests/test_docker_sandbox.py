"""Docker sandbox helpers (unit tests; optional live Docker integration)."""

from __future__ import annotations

import os
from pathlib import Path
from unittest.mock import AsyncMock, patch

import pytest

from app.core.config import get_settings
from app.models.request_models import ExecuteRequest
from app.services.code_executor import CodeExecutor
from app.services.docker_executor import (
    DockerRunResult,
    build_docker_run_argv,
    docker_cli_available,
)


def test_build_docker_run_argv_includes_memory_and_cpus() -> None:
    host = Path("/tmp/acm_test_mount")
    argv = build_docker_run_argv(
        host_mount=host,
        container_mount="/workspace",
        workdir="/workspace",
        image="ai_code_executor:latest",
        command=["python", "-c", "print(1)"],
        memory="256m",
        cpus="0.5",
        network=None,
        container_name="test_c",
    )
    assert argv[:2] == ["docker", "run"]
    assert "--rm" in argv
    assert "--memory" in argv
    assert "256m" in argv
    assert "--cpus" in argv
    assert "0.5" in argv
    assert "--name" in argv
    assert "test_c" in argv
    assert "-v" in argv
    assert "ai_code_executor:latest" in argv


@pytest.mark.asyncio
async def test_code_executor_docker_unavailable_returns_structured_error(monkeypatch) -> None:
    monkeypatch.setenv("DOCKER_ENABLED", "true")
    get_settings.cache_clear()
    try:
        with patch(
            "app.services.code_executor.docker_executor.run_with_image_fallback",
            new_callable=AsyncMock,
        ) as mock_run:
            mock_run.return_value = DockerRunResult(
                stdout="",
                stderr="",
                exit_code=None,
                docker_unavailable=True,
                error_message="Docker is not installed or not on PATH",
            )
            ex = CodeExecutor()
            resp = await ex.execute(ExecuteRequest(language="python", code="print(1)"))
        assert resp.success is False
        assert resp.execution_error is not None
        assert resp.execution_error.status == "error"
        assert "Docker" in resp.execution_error.message
    finally:
        monkeypatch.delenv("DOCKER_ENABLED", raising=False)
        get_settings.cache_clear()


@pytest.mark.asyncio
@pytest.mark.integration
async def test_live_docker_python_print_skips_without_docker() -> None:
    if os.environ.get("PYTEST_USE_DOCKER") != "1":
        pytest.skip("Set PYTEST_USE_DOCKER=1 to run live Docker test")
    if not docker_cli_available():
        pytest.skip("Docker not available")

    os.environ["DOCKER_ENABLED"] = "true"
    os.environ["DOCKER_CODE_TIMEOUT_SECONDS"] = "120"
    get_settings.cache_clear()
    try:
        ex = CodeExecutor()
        resp = await ex.execute(
            ExecuteRequest(language="python", code="print('hello_docker')", timeout=120)
        )
        assert "hello_docker" in resp.stdout or resp.stdout == ""
        if resp.execution_error:
            pytest.fail(resp.execution_error.message)
    finally:
        os.environ.pop("DOCKER_ENABLED", None)
        os.environ.pop("DOCKER_CODE_TIMEOUT_SECONDS", None)
        get_settings.cache_clear()


@pytest.mark.asyncio
async def test_invalid_python_does_not_crash_executor(monkeypatch) -> None:
    monkeypatch.setenv("DOCKER_ENABLED", "true")
    get_settings.cache_clear()
    try:
        with patch(
            "app.services.code_executor.docker_executor.run_with_image_fallback",
            new_callable=AsyncMock,
        ) as mock_run:
            mock_run.return_value = DockerRunResult(
                stdout="",
                stderr="SyntaxError: invalid syntax",
                exit_code=1,
            )
            ex = CodeExecutor()
            resp = await ex.execute(ExecuteRequest(language="python", code="for ;; bad"))
        assert resp.success is False
        assert resp.execution_error is None
        assert resp.exit_code == 1
    finally:
        monkeypatch.delenv("DOCKER_ENABLED", raising=False)
        get_settings.cache_clear()


@pytest.mark.asyncio
async def test_docker_timeout_returns_execution_error(monkeypatch) -> None:
    monkeypatch.setenv("DOCKER_ENABLED", "true")
    get_settings.cache_clear()
    try:
        with patch(
            "app.services.code_executor.docker_executor.run_with_image_fallback",
            new_callable=AsyncMock,
        ) as mock_run:
            mock_run.return_value = DockerRunResult(
                stdout="",
                stderr="",
                exit_code=None,
                timed_out=True,
                error_message="Execution timed out or container failed",
            )
            ex = CodeExecutor()
            resp = await ex.execute(ExecuteRequest(language="python", code="while True: pass"))
        assert resp.success is False
        assert resp.execution_error is not None
        assert "timed out" in resp.execution_error.message.lower()
    finally:
        monkeypatch.delenv("DOCKER_ENABLED", raising=False)
        get_settings.cache_clear()
