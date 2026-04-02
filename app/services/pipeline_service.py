"""Orchestrate analysis, tests, decision, optional AI fix, and auto-commit."""

from __future__ import annotations

from pathlib import Path
from typing import Any, Dict, Optional, Tuple

from git import InvalidGitRepositoryError, Repo

from app.core.config import Settings, get_settings
from app.models.request_models import (
    AISuggestionPayload,
    AnalysisRequest,
    GitRequest,
    PipelineCommitInfo,
    PipelineFeedbackPayload,
    PipelineRunRequest,
    PipelineRunResponse,
    TestRunRequest,
)
from app.services import ai_service
from app.services.analyzer import Analyzer
from app.services.decision_engine import DecisionEngine
from app.services.git_manager import GitManager
from app.services.test_runner import TestRunner
from app.utils.logger import get_logger

logger = get_logger(__name__)


def _count_critical_issues(analysis_issues) -> int:
    n = 0
    for i in analysis_issues:
        if i.tool == "mypy" and i.severity == "error":
            n += 1
        elif i.tool == "pylint":
            code = (i.code or "").strip().upper()
            if code and code[0] in ("E", "F"):
                n += 1
            elif i.severity in ("error", "fatal"):
                n += 1
    return n


def _build_commit_message(analysis, tests, prefix: str) -> str:
    critical = _count_critical_issues(analysis.issues)
    return (
        f"{prefix}: {tests.passed} tests passed, {critical} critical lint errors"
    )


def _feedback_payload(analysis, tests) -> PipelineFeedbackPayload:
    return PipelineFeedbackPayload(
        analysis_feedback=analysis.feedback,
        test_feedback=tests.feedback,
        pylint_score=analysis.pylint_score,
        passed_threshold=analysis.passed_threshold,
        test_total=tests.total,
        test_passed=tests.passed,
        test_failed=tests.failed,
        test_errors=tests.errors,
    )


def _feedback_dict_for_ai(analysis, tests) -> Dict[str, Any]:
    return {
        "issues": [i.model_dump() for i in analysis.issues],
        "pylint_score": analysis.pylint_score,
        "passed_threshold": analysis.passed_threshold,
        "analysis_feedback": analysis.feedback.model_dump() if analysis.feedback else None,
        "test_feedback": tests.feedback.model_dump() if tests.feedback else None,
        "tests_passed": tests.passed,
        "tests_failed": tests.failed,
        "tests_errors": tests.errors,
    }


class PipelineService:
    def __init__(
        self,
        analyzer: Optional[Analyzer] = None,
        test_runner: Optional[TestRunner] = None,
        git_manager: Optional[GitManager] = None,
        settings: Optional[Settings] = None,
    ) -> None:
        self._analyzer = analyzer or Analyzer()
        self._test_runner = test_runner or TestRunner()
        self._git = git_manager or GitManager()
        self._settings = settings or get_settings()

    def _validate_repo(self, repo: Path) -> Optional[str]:
        try:
            Repo(repo, search_parent_directories=False)
        except InvalidGitRepositoryError:
            return "Not a valid git repository"
        return None

    async def _run_analysis_and_tests(
        self,
        req: PipelineRunRequest,
        repo: Path,
        code: str,
        tests: str,
    ) -> Tuple[Any, Any]:
        code_path = repo / req.code_filename
        test_path = repo / req.test_filename
        test_path.parent.mkdir(parents=True, exist_ok=True)
        code_path.write_text(code, encoding="utf-8")
        test_path.write_text(tests, encoding="utf-8")

        analysis = await self._analyzer.analyse(
            AnalysisRequest(code=code, language="python", analysis_type="full")
        )

        try:
            rel_test = test_path.relative_to(repo)
        except ValueError:
            rel_test = Path(req.test_filename)

        tests_res = await self._test_runner.run(
            TestRunRequest(
                test_paths=[str(rel_test).replace("\\", "/")],
                verbose=True,
                coverage=False,
            ),
            project_root=repo,
        )
        return analysis, tests_res

    async def _maybe_generate_ai_suggestion(
        self,
        req: PipelineRunRequest,
        analysis,
        tests,
    ) -> Optional[AISuggestionPayload]:
        if not self._settings.ai_enabled:
            return None
        try:
            return await ai_service.generate_fix(
                code=req.code,
                tests=req.tests,
                feedback=_feedback_dict_for_ai(analysis, tests),
                settings=self._settings,
            )
        except Exception as exc:  # noqa: BLE001
            logger.exception("ai_suggestion_failed")
            return AISuggestionPayload(
                fixed_code="",
                explanation="",
                confidence=0.0,
                error=str(exc)[:2000],
            )

    async def run_full_pipeline(self, req: PipelineRunRequest) -> PipelineRunResponse:
        repo = Path(req.repo_path).expanduser().resolve()
        err = self._validate_repo(repo)
        if err:
            return PipelineRunResponse(
                status="failed",
                decision="fail",
                reason=err,
                feedback=None,
            )

        branch_res = self._git.run(
            GitRequest(
                operation="branch",
                repo_path=str(repo),
                branch_name=req.branch,
            )
        )
        if not branch_res.success:
            return PipelineRunResponse(
                status="failed",
                decision="fail",
                reason=branch_res.message or "Could not checkout branch",
                feedback=None,
            )

        try:
            analysis, tests = await self._run_analysis_and_tests(
                req, repo, req.code, req.tests
            )
            decision = DecisionEngine.evaluate(analysis, tests)

            if decision.can_commit:
                return await self._do_commit(
                    req,
                    repo,
                    analysis,
                    tests,
                    auto_fixed=None,
                    ai_suggestion=None,
                )

            fb = _feedback_payload(analysis, tests)
            ai_suggestion = await self._maybe_generate_ai_suggestion(req, analysis, tests)

            if (
                req.auto_fix
                and ai_suggestion
                and ai_suggestion.fixed_code
                and not ai_suggestion.error
            ):
                fixed = ai_service.sanitize_fixed_code(ai_suggestion.fixed_code)
                analysis2, tests2 = await self._run_analysis_and_tests(
                    req, repo, fixed, req.tests
                )
                decision2 = DecisionEngine.evaluate(analysis2, tests2)
                if decision2.can_commit:
                    return await self._do_commit(
                        req,
                        repo,
                        analysis2,
                        tests2,
                        auto_fixed=True,
                        ai_suggestion=ai_suggestion,
                    )
                return PipelineRunResponse(
                    status="failed",
                    decision="fail",
                    reason=decision2.reason,
                    feedback=fb,
                    ai_suggestion=ai_suggestion,
                    auto_fixed=False,
                    auto_fix_retry_feedback=_feedback_payload(analysis2, tests2),
                )

            return PipelineRunResponse(
                status="failed",
                decision="fail",
                reason=decision.reason,
                feedback=fb,
                ai_suggestion=ai_suggestion,
            )
        except Exception as exc:  # noqa: BLE001
            logger.exception("pipeline_unhandled_error")
            return PipelineRunResponse(
                status="failed",
                decision="fail",
                reason=str(exc)[:2000],
                feedback=None,
            )

    async def _do_commit(
        self,
        req: PipelineRunRequest,
        repo: Path,
        analysis,
        tests,
        *,
        auto_fixed: Optional[bool],
        ai_suggestion: Optional[AISuggestionPayload],
    ) -> PipelineRunResponse:
        prefix = req.commit_message_prefix
        if auto_fixed:
            prefix = f"{req.commit_message_prefix} (auto-fixed)"
        message = _build_commit_message(analysis, tests, prefix)
        commit_res = self._git.run(
            GitRequest(
                operation="commit",
                repo_path=str(repo),
                message=message,
            )
        )
        if not commit_res.success:
            logger.warning("pipeline_commit_failed: %s", commit_res.message)
            return PipelineRunResponse(
                status="failed",
                decision="pass",
                reason=commit_res.message or "Commit refused by git",
                feedback=_feedback_payload(analysis, tests),
                ai_suggestion=ai_suggestion,
            )

        hexsha = (commit_res.data or {}).get("hexsha")
        return PipelineRunResponse(
            status="success",
            decision="pass",
            commit=PipelineCommitInfo(
                message=message,
                branch=req.branch,
                hexsha=hexsha,
            ),
            feedback=None,
            ai_suggestion=ai_suggestion,
            auto_fixed=auto_fixed,
        )
