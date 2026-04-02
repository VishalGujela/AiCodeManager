"""Unit tests for pipeline decision rules."""

from app.models.request_models import (
    AnalysisIssue,
    AnalysisResponse,
    FeedbackSummary,
    TestRunResponse,
)
from app.services.decision_engine import DecisionEngine


def test_decision_fails_on_test_failures() -> None:
    analysis = AnalysisResponse(passed_threshold=True, issues=[], pylint_score=10.0)
    tests = TestRunResponse(passed=0, failed=1, errors=0, total=1)
    d = DecisionEngine.evaluate(analysis, tests)
    assert d.decision == "fail"
    assert d.can_commit is False
    assert "Test" in d.reason


def test_decision_fails_on_critical_pylint() -> None:
    analysis = AnalysisResponse(
        passed_threshold=False,
        issues=[
            AnalysisIssue(
                tool="pylint",
                line=1,
                code="E0602",
                message="undefined",
                severity="error",
            )
        ],
    )
    tests = TestRunResponse(passed=1, failed=0, errors=0, total=1)
    d = DecisionEngine.evaluate(analysis, tests)
    assert d.decision == "fail"
    assert d.can_commit is False


def test_decision_passes_clean_run() -> None:
    analysis = AnalysisResponse(passed_threshold=True, issues=[], pylint_score=10.0)
    tests = TestRunResponse(passed=2, failed=0, errors=0, total=2)
    d = DecisionEngine.evaluate(analysis, tests)
    assert d.decision == "pass"
    assert d.can_commit is True


def test_decision_fails_when_feedback_status_fail() -> None:
    analysis = AnalysisResponse(
        passed_threshold=True,
        issues=[],
        feedback=FeedbackSummary(
            status="fail",
            summary="lint",
            suggestions=[],
        ),
    )
    tests = TestRunResponse(passed=1, failed=0, errors=0, total=1)
    d = DecisionEngine.evaluate(analysis, tests)
    assert d.decision == "fail"
    assert "Feedback" in d.reason


def test_decision_fails_when_threshold_not_met_no_explicit_critical() -> None:
    analysis = AnalysisResponse(
        passed_threshold=False,
        issues=[
            AnalysisIssue(
                tool="pylint",
                line=1,
                code="C0301",
                message="line long",
                severity="convention",
            )
        ],
        pylint_score=5.0,
    )
    tests = TestRunResponse(passed=1, failed=0, errors=0, total=1)
    d = DecisionEngine.evaluate(analysis, tests)
    assert d.decision == "fail"
