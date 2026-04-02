"""Rule-based pipeline pass/fail from analysis, tests, and feedback (no I/O)."""

from __future__ import annotations

from typing import List, Literal

from pydantic import BaseModel

from app.models.request_models import AnalysisIssue, AnalysisResponse, TestRunResponse


class PipelineDecision(BaseModel):
    """Outcome of policy evaluation for auto-commit."""

    decision: Literal["pass", "fail"]
    reason: str
    can_commit: bool


def _has_critical_lint(issues: List[AnalysisIssue]) -> bool:
    for i in issues:
        if i.tool == "mypy" and i.severity == "error":
            return True
        if i.tool == "pylint":
            code = (i.code or "").strip().upper()
            if code and code[0] in ("E", "F"):
                return True
            if i.severity in ("error", "fatal"):
                return True
    return False


def _feedback_status_fail(analysis: AnalysisResponse, tests: TestRunResponse) -> bool:
    if analysis.feedback and analysis.feedback.status == "fail":
        return True
    if tests.feedback and tests.feedback.status == "fail":
        return True
    return False


def _critical_from_feedback(analysis: AnalysisResponse) -> bool:
    fb = analysis.feedback
    if not fb or not fb.lint:
        return False
    return int(fb.lint.severity_counts.get("critical", 0)) > 0


class DecisionEngine:
    """Evaluate whether automation may commit."""

    @staticmethod
    def evaluate(analysis: AnalysisResponse, tests: TestRunResponse) -> PipelineDecision:
        if tests.failed > 0 or tests.errors > 0:
            return PipelineDecision(
                decision="fail",
                reason="Test failures detected",
                can_commit=False,
            )

        if _feedback_status_fail(analysis, tests):
            return PipelineDecision(
                decision="fail",
                reason="Feedback engine reported failing status (critical issues or test insights)",
                can_commit=False,
            )

        if _has_critical_lint(analysis.issues) or _critical_from_feedback(analysis):
            return PipelineDecision(
                decision="fail",
                reason="Critical lint or type errors present",
                can_commit=False,
            )

        if not analysis.passed_threshold:
            return PipelineDecision(
                decision="fail",
                reason="Static analysis did not meet project thresholds",
                can_commit=False,
            )

        return PipelineDecision(
            decision="pass",
            reason="All checks passed",
            can_commit=True,
        )
