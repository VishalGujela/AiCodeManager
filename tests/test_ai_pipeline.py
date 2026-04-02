"""AI suggestion and auto-fix pipeline tests (mocked HTTP / generate_fix)."""

from __future__ import annotations

from app.services.ai_service import sanitize_fixed_code

import subprocess
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.models.request_models import (
    AISuggestionPayload,
    AnalysisResponse,
    PipelineRunRequest,
    TestRunResponse,
)
from app.services.analyzer import Analyzer
from app.services.pipeline_service import PipelineService
from app.services.test_runner import TestRunner


def test_sanitize_strips_markdown_fence() -> None:
    raw = "```python\nx = 1\n```"
    assert sanitize_fixed_code(raw).strip() == "x = 1"


def _git_init(path: Path) -> None:
    subprocess.run(["git", "init"], cwd=path, check=True, capture_output=True)
    subprocess.run(
        ["git", "config", "user.email", "ai@test.local"],
        cwd=path,
        check=True,
    )
    subprocess.run(["git", "config", "user.name", "AI Test"], cwd=path, check=True)
    (path / "README.md").write_text("# t\n", encoding="utf-8")
    subprocess.run(["git", "add", "."], cwd=path, check=True, capture_output=True)
    subprocess.run(
        ["git", "commit", "-m", "init"],
        cwd=path,
        check=True,
        capture_output=True,
    )


def _commit_count(path: Path) -> int:
    r = subprocess.run(
        ["git", "rev-list", "--count", "HEAD"],
        cwd=path,
        capture_output=True,
        text=True,
        check=True,
    )
    return int(r.stdout.strip())


@pytest.mark.asyncio
async def test_failure_includes_ai_suggestion_when_enabled(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("AI_ENABLED", "true")
    monkeypatch.setenv("OPENAI_API_KEY", "sk-test")
    from app.core.config import get_settings

    get_settings.cache_clear()

    repo = tmp_path / "r"
    repo.mkdir()
    _git_init(repo)

    bad_tests = TestRunResponse(passed=0, failed=1, errors=0, skipped=0, total=1)
    good_analysis = AnalysisResponse(passed_threshold=True, issues=[], pylint_score=10.0)

    mock_an = MagicMock(spec=Analyzer)
    mock_an.analyse = AsyncMock(return_value=good_analysis)
    mock_tr = MagicMock(spec=TestRunner)
    mock_tr.run = AsyncMock(return_value=bad_tests)

    suggestion = AISuggestionPayload(
        fixed_code="def x():\n    return 1\n",
        explanation="fix assert",
        confidence=0.8,
    )

    try:
        with patch(
            "app.services.pipeline_service.ai_service.generate_fix",
            new_callable=AsyncMock,
            return_value=suggestion,
        ):
            svc = PipelineService(analyzer=mock_an, test_runner=mock_tr)
            res = await svc.run_full_pipeline(
                PipelineRunRequest(
                    code="def f():\n    return 0\n",
                    tests="def test_f():\n    assert f() == 1\n",
                    repo_path=str(repo),
                    branch="feature/ai",
                    auto_fix=False,
                )
            )
        assert res.status == "failed"
        assert res.ai_suggestion is not None
        assert res.ai_suggestion.explanation == "fix assert"
        assert res.ai_suggestion.fixed_code
    finally:
        monkeypatch.delenv("AI_ENABLED", raising=False)
        monkeypatch.delenv("OPENAI_API_KEY", raising=False)
        get_settings.cache_clear()


@pytest.mark.asyncio
async def test_auto_fix_rerun_and_commit(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("AI_ENABLED", "true")
    monkeypatch.setenv("OPENAI_API_KEY", "sk-test")
    from app.core.config import get_settings

    get_settings.cache_clear()

    repo = tmp_path / "r"
    repo.mkdir()
    _git_init(repo)

    fail_tests = TestRunResponse(passed=0, failed=1, errors=0, skipped=0, total=1)
    ok_tests = TestRunResponse(passed=1, failed=0, errors=0, skipped=0, total=1)
    analysis_ok = AnalysisResponse(passed_threshold=True, issues=[], pylint_score=10.0)

    mock_an = MagicMock(spec=Analyzer)
    mock_tr = MagicMock(spec=TestRunner)
    mock_an.analyse = AsyncMock(return_value=analysis_ok)
    mock_tr.run = AsyncMock(side_effect=[fail_tests, ok_tests])

    suggestion = AISuggestionPayload(
        fixed_code="def f():\n    return 42\n",
        explanation="return 42",
        confidence=0.9,
    )

    try:
        with patch(
            "app.services.pipeline_service.ai_service.generate_fix",
            new_callable=AsyncMock,
            return_value=suggestion,
        ):
            svc = PipelineService(analyzer=mock_an, test_runner=mock_tr)
            before = _commit_count(repo)
            res = await svc.run_full_pipeline(
                PipelineRunRequest(
                    code="def f():\n    return 0\n",
                    tests="def test_f():\n    assert f() == 42\n",
                    repo_path=str(repo),
                    branch="feature/autofix",
                    auto_fix=True,
                )
            )
            after = _commit_count(repo)
        assert res.status == "success"
        assert res.auto_fixed is True
        assert after == before + 1
        assert mock_tr.run.await_count == 2
    finally:
        monkeypatch.delenv("AI_ENABLED", raising=False)
        monkeypatch.delenv("OPENAI_API_KEY", raising=False)
        get_settings.cache_clear()


@pytest.mark.asyncio
async def test_auto_fix_second_fail_no_commit(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("AI_ENABLED", "true")
    monkeypatch.setenv("OPENAI_API_KEY", "sk-test")
    from app.core.config import get_settings

    get_settings.cache_clear()

    repo = tmp_path / "r"
    repo.mkdir()
    _git_init(repo)

    fail_tests = TestRunResponse(passed=0, failed=1, errors=0, skipped=0, total=1)
    analysis_ok = AnalysisResponse(passed_threshold=True, issues=[], pylint_score=10.0)

    mock_an = MagicMock(spec=Analyzer)
    mock_tr = MagicMock(spec=TestRunner)
    mock_an.analyse = AsyncMock(return_value=analysis_ok)
    mock_tr.run = AsyncMock(return_value=fail_tests)

    suggestion = AISuggestionPayload(
        fixed_code="def f():\n    return 1\n",
        explanation="still wrong",
        confidence=0.5,
    )

    try:
        with patch(
            "app.services.pipeline_service.ai_service.generate_fix",
            new_callable=AsyncMock,
            return_value=suggestion,
        ):
            svc = PipelineService(analyzer=mock_an, test_runner=mock_tr)
            before = _commit_count(repo)
            res = await svc.run_full_pipeline(
                PipelineRunRequest(
                    code="def f():\n    return 0\n",
                    tests="def test_f():\n    assert f() == 99\n",
                    repo_path=str(repo),
                    branch="feature/autofix2",
                    auto_fix=True,
                )
            )
            after = _commit_count(repo)
        assert res.status == "failed"
        assert res.auto_fixed is False
        assert res.auto_fix_retry_feedback is not None
        assert after == before
        assert mock_tr.run.await_count == 2
    finally:
        monkeypatch.delenv("AI_ENABLED", raising=False)
        monkeypatch.delenv("OPENAI_API_KEY", raising=False)
        get_settings.cache_clear()
