"""Pipeline orchestration tests (mocked analysis/tests; real git in temp dir)."""

from __future__ import annotations

import subprocess
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import pytest

from app.models.request_models import (
    AnalysisResponse,
    PipelineRunRequest,
    TestRunResponse,
)
from app.services.analyzer import Analyzer
from app.services.pipeline_service import PipelineService
from app.services.test_runner import TestRunner


def _git_init(path: Path) -> None:
    subprocess.run(["git", "init"], cwd=path, check=True, capture_output=True)
    subprocess.run(
        ["git", "config", "user.email", "pipeline@test.local"],
        cwd=path,
        check=True,
    )
    subprocess.run(["git", "config", "user.name", "Pipeline Test"], cwd=path, check=True)
    (path / "README.md").write_text("# tmp\n", encoding="utf-8")
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
async def test_pipeline_commits_only_when_decision_passes(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    _git_init(repo)

    good_analysis = AnalysisResponse(passed_threshold=True, issues=[], pylint_score=10.0)
    good_tests = TestRunResponse(passed=1, failed=0, errors=0, skipped=0, total=1)

    mock_an = MagicMock(spec=Analyzer)
    mock_an.analyse = AsyncMock(return_value=good_analysis)
    mock_tr = MagicMock(spec=TestRunner)
    mock_tr.run = AsyncMock(return_value=good_tests)

    before = _commit_count(repo)
    svc = PipelineService(analyzer=mock_an, test_runner=mock_tr)
    res = await svc.run_full_pipeline(
        PipelineRunRequest(
            code="def add(a, b):\n    return a + b\n",
            tests="def test_add():\n    import user_code as u\n    assert u.add(1, 2) == 3\n",
            repo_path=str(repo),
            branch="feature/ok",
        )
    )
    after = _commit_count(repo)

    assert res.status == "success"
    assert res.decision == "pass"
    assert res.commit is not None
    assert after == before + 1
    assert "1 tests passed" in res.commit.message
    assert "0 critical lint errors" in res.commit.message
    mock_tr.run.assert_awaited_once()


@pytest.mark.asyncio
async def test_pipeline_skips_commit_on_failure(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    _git_init(repo)

    good_analysis = AnalysisResponse(passed_threshold=True, issues=[], pylint_score=10.0)
    bad_tests = TestRunResponse(passed=0, failed=1, errors=0, skipped=0, total=1)

    mock_an = MagicMock(spec=Analyzer)
    mock_an.analyse = AsyncMock(return_value=good_analysis)
    mock_tr = MagicMock(spec=TestRunner)
    mock_tr.run = AsyncMock(return_value=bad_tests)

    before = _commit_count(repo)
    svc = PipelineService(analyzer=mock_an, test_runner=mock_tr)
    res = await svc.run_full_pipeline(
        PipelineRunRequest(
            code="x=1\n",
            tests="def test_x():\n    assert False\n",
            repo_path=str(repo),
            branch="feature/bad",
        )
    )
    after = _commit_count(repo)

    assert res.status == "failed"
    assert res.decision == "fail"
    assert res.commit is None
    assert after == before
    assert res.feedback is not None
    assert res.feedback.test_failed == 1
