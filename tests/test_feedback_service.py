"""Unit tests for Smart Feedback Engine (no subprocess / network)."""

from __future__ import annotations

from app.models.request_models import AnalysisIssue, FeedbackSummary, TestResultItem
from app.services.feedback_service import (
    LintFeedbackProcessor,
    SuggestionEngine,
    TestFailureAnalyzer,
    build_lint_feedback_summary,
)

def test_zero_division_message_yields_guard_suggestion() -> None:
    r = TestResultItem(
        node_id="t::test_z",
        outcome="failed",
        duration_ms=1.0,
        message="ZeroDivisionError: division by zero",
    )
    fb = TestFailureAnalyzer.analyze([r])
    assert fb.insights[0].exception_type == "ZeroDivisionError"
    assert fb.insights[0].suggestion is not None
    assert "denominator" in fb.insights[0].suggestion.suggestion.lower()


def test_type_error_exception_and_critical_severity() -> None:
    r = TestResultItem(
        node_id="t::test_t",
        outcome="failed",
        duration_ms=1.0,
        message="TypeError: cannot add str and int",
    )
    fb = TestFailureAnalyzer.analyze([r])
    assert fb.insights[0].exception_type == "TypeError"
    assert fb.insights[0].suggestion is not None
    assert fb.insights[0].suggestion.severity == "critical"


def test_nonetype_wins_over_typeerror_in_message() -> None:
    r = TestResultItem(
        node_id="t::test_n",
        outcome="error",
        duration_ms=1.0,
        message="TypeError: unsupported operand type(s) for +: 'int' and 'NoneType'",
    )
    fb = TestFailureAnalyzer.analyze([r])
    assert fb.insights[0].exception_type == "NoneType"
    assert fb.insights[0].suggestion is not None
    assert fb.insights[0].suggestion.classifier == "NoneType"


def test_assertion_error_extracts_lhs_rhs() -> None:
    r = TestResultItem(
        node_id="tests/test_math.py::test_divide_by_zero",
        outcome="failed",
        duration_ms=1.0,
        message="AssertionError: assert divide(5, 0) == 0",
    )
    fb = TestFailureAnalyzer.analyze([r])
    desc = fb.insights[0].description
    assert "divide(5, 0)" in desc
    assert "0" in desc


def test_pylint_e0602_in_syntax_errors_and_fail_status() -> None:
    issues = [
        AnalysisIssue(
            tool="pylint",
            line=1,
            code="E0602",
            message="Undefined variable 'x'",
            severity="error",
        )
    ]
    lf = LintFeedbackProcessor.process(issues)
    assert "syntax_errors" in lf.groups
    assert lf.groups["syntax_errors"][0].code == "E0602"
    summary = build_lint_feedback_summary(issues)
    assert isinstance(summary, FeedbackSummary)
    assert summary.status == "fail"


def test_pylint_w0611_style_warnings_and_warning_count() -> None:
    issues = [
        AnalysisIssue(
            tool="pylint",
            line=3,
            code="W0611",
            message="Unused import os",
            severity="warning",
        )
    ]
    lf = LintFeedbackProcessor.process(issues)
    assert "style_warnings" in lf.groups
    assert lf.severity_counts.get("warning", 0) >= 1


def test_mixed_critical_and_warning_lint_status_fail() -> None:
    issues = [
        AnalysisIssue(
            tool="pylint",
            line=1,
            code="E0602",
            message="undefined",
            severity="error",
        ),
        AnalysisIssue(
            tool="pylint",
            line=2,
            code="W0611",
            message="unused",
            severity="warning",
        ),
    ]
    summary = build_lint_feedback_summary(issues)
    assert summary.status == "fail"
    severities = [s.severity for s in summary.suggestions]
    assert "critical" in severities


def test_suggestion_engine_deduplicates_classifiers() -> None:
    got = SuggestionEngine.get_suggestions(
        ["TypeError", "TypeError", "ValueError"], tool="lint"
    )
    classifiers = [s.classifier for s in got]
    assert classifiers == ["TypeError", "ValueError"]


def test_all_info_suggestions_yield_pass_status() -> None:
    issues = [
        AnalysisIssue(
            tool="pylint",
            line=1,
            code="C0301",
            message="line too long",
            severity="convention",
        )
    ]
    summary = build_lint_feedback_summary(issues)
    assert summary.status == "pass"
    assert all(s.severity == "info" for s in summary.suggestions)


def test_empty_analysis_issues_no_crash_empty_summary() -> None:
    lf = LintFeedbackProcessor.process([])
    assert lf.summary == ""
    assert lf.severity_counts == {"critical": 0, "warning": 0, "info": 0}


def test_empty_test_results_zero_failures() -> None:
    fb = TestFailureAnalyzer.analyze([])
    assert fb.total_failures == 0
    assert fb.insights == []
