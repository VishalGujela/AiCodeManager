"""Deterministic rule-based feedback from lint and test outputs (no I/O)."""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Dict, List, Literal, Optional, Tuple

from app.models.request_models import (
    AnalysisIssue,
    FeedbackSummary,
    LintFeedback,
    Suggestion,
    TestFailureInsight,
    TestFeedback,
    TestResultItem,
)

Severity = Literal["critical", "warning", "info"]

_SEVERITY_ORDER = {"critical": 0, "warning": 1, "info": 2}


@dataclass(frozen=True)
class SuggestionRule:
    short_description: str
    suggestion: str
    doc_link: Optional[str]
    severity: Severity


SUGGESTION_RULES: Dict[str, SuggestionRule] = {
    "ZeroDivisionError": SuggestionRule(
        short_description="Division by zero",
        suggestion="Add `if denominator == 0` guard before division",
        doc_link="https://docs.python.org/3/library/exceptions.html#ZeroDivisionError",
        severity="critical",
    ),
    "TypeError": SuggestionRule(
        short_description="Type mismatch",
        suggestion="Validate input types before the operation",
        doc_link="https://docs.python.org/3/library/exceptions.html#TypeError",
        severity="critical",
    ),
    "AttributeError": SuggestionRule(
        short_description="Attribute access on wrong type",
        suggestion="Check object type or initialisation before accessing attribute",
        doc_link="https://docs.python.org/3/library/exceptions.html#AttributeError",
        severity="critical",
    ),
    "NoneType": SuggestionRule(
        short_description="Operation on None",
        suggestion="Add `if value is not None` guard",
        doc_link="https://docs.python.org/3/library/constants.html#None",
        severity="critical",
    ),
    "IndexError": SuggestionRule(
        short_description="List index out of range",
        suggestion="Check `len(list) > index` before accessing",
        doc_link="https://docs.python.org/3/library/exceptions.html#IndexError",
        severity="warning",
    ),
    "KeyError": SuggestionRule(
        short_description="Missing dictionary key",
        suggestion="Use `dict.get(key)` or check `key in dict`",
        doc_link="https://docs.python.org/3/library/exceptions.html#KeyError",
        severity="warning",
    ),
    "ValueError": SuggestionRule(
        short_description="Invalid value",
        suggestion="Validate input range or format before processing",
        doc_link="https://docs.python.org/3/library/exceptions.html#ValueError",
        severity="warning",
    ),
    "AssertionError": SuggestionRule(
        short_description="Assertion failed",
        suggestion="Review expected vs actual values in test",
        doc_link="https://docs.python.org/3/library/exceptions.html#AssertionError",
        severity="warning",
    ),
    "E0602": SuggestionRule(
        short_description="Undefined variable",
        suggestion="Define the variable before use or check for typos",
        doc_link="https://pylint.readthedocs.io/en/stable/user_guide/messages/error/undefined-variable.html",
        severity="critical",
    ),
    "E1101": SuggestionRule(
        short_description="Module has no member",
        suggestion="Verify the attribute exists in the installed version",
        doc_link="https://pylint.readthedocs.io/en/stable/user_guide/messages/error/no-member.html",
        severity="critical",
    ),
    "W0611": SuggestionRule(
        short_description="Unused import",
        suggestion="Remove unused import or suppress with `# noqa: F401`",
        doc_link="https://pylint.readthedocs.io/en/stable/user_guide/messages/warning/unused-import.html",
        severity="info",
    ),
    "C0301": SuggestionRule(
        short_description="Line too long",
        suggestion="Break line using parentheses or a variable",
        doc_link="https://pylint.readthedocs.io/en/stable/user_guide/messages/convention/line-too-long.html",
        severity="info",
    ),
}


class SuggestionEngine:
    """Rule lookup; table is module-level `SUGGESTION_RULES`."""

    @staticmethod
    def get_suggestions(classifiers: List[str], tool: str = "lint") -> List[Suggestion]:
        seen: set = set()
        out: List[Suggestion] = []
        for c in classifiers:
            if c in seen:
                continue
            rule = SUGGESTION_RULES.get(c)
            if not rule:
                continue
            seen.add(c)
            out.append(
                Suggestion(
                    tool=tool,
                    classifier=c,
                    short_description=rule.short_description,
                    suggestion=rule.suggestion,
                    doc_link=rule.doc_link,
                    severity=rule.severity,
                )
            )
        out.sort(key=lambda s: _SEVERITY_ORDER[s.severity])
        return out


class LintFeedbackProcessor:
    """Group `AnalysisIssue` rows by category and build human-readable summary."""

    _CATEGORY_ORDER = (
        "syntax_errors",
        "type_errors",
        "type_warnings",
        "style_warnings",
        "convention",
        "refactor",
    )

    @classmethod
    def process(cls, issues: List[AnalysisIssue]) -> LintFeedback:
        groups: Dict[str, List[AnalysisIssue]] = {k: [] for k in cls._CATEGORY_ORDER}
        severity_counts: Dict[str, int] = {"critical": 0, "warning": 0, "info": 0}

        for issue in issues:
            cat, sev = cls._categorize(issue)
            if cat not in groups:
                groups[cat] = []
            groups[cat].append(issue)
            severity_counts[sev] = severity_counts.get(sev, 0) + 1

        summary = cls._build_summary(groups)
        groups = {k: v for k, v in groups.items() if v}
        return LintFeedback(summary=summary, groups=groups, severity_counts=severity_counts)

    @staticmethod
    def _categorize(issue: AnalysisIssue) -> Tuple[str, Severity]:
        if issue.tool == "mypy":
            if issue.severity == "error":
                return "type_errors", "critical"
            if issue.severity == "warning":
                return "type_warnings", "warning"
            return "type_errors", "info"

        code = (issue.code or "").strip()
        prefix = code[:1].upper() if code else ""
        if prefix in ("E", "F"):
            return "syntax_errors", "critical"
        if prefix == "W":
            return "style_warnings", "warning"
        if prefix == "C":
            return "convention", "info"
        if prefix == "R":
            return "refactor", "info"
        if issue.severity in ("error", "fatal"):
            return "syntax_errors", "critical"
        return "style_warnings", "warning"

    @classmethod
    def _build_summary(cls, groups: Dict[str, List[AnalysisIssue]]) -> str:
        parts: List[str] = []
        labels = {
            "syntax_errors": ("syntax error", "syntax errors"),
            "type_errors": ("type error", "type errors"),
            "type_warnings": ("type warning", "type warnings"),
            "style_warnings": ("style warning", "style warnings"),
            "convention": ("convention issue", "convention issues"),
            "refactor": ("refactor hint", "refactor hints"),
        }
        for key in cls._CATEGORY_ORDER:
            n = len(groups.get(key, []))
            if n == 0:
                continue
            singular, plural = labels.get(key, ("issue", "issues"))
            parts.append(f"{n} {singular if n == 1 else plural}")
        return ", ".join(parts)


def _classify_exception_from_message(message: str) -> Optional[str]:
    if not message:
        return None
    if "NoneType" in message:
        return "NoneType"
    ordered = [
        "ZeroDivisionError",
        "TypeError",
        "AttributeError",
        "IndexError",
        "KeyError",
        "ValueError",
        "AssertionError",
    ]
    for name in ordered:
        if name in message:
            return name
    return None


_ASSERT_EQ_RE = re.compile(
    r"AssertionError:\s*assert\s+(.+?)\s*==\s+(.+)\s*$",
    re.IGNORECASE | re.DOTALL,
)


class TestFailureAnalyzer:
    """Turn pytest failure messages into structured insights."""

    @classmethod
    def analyze(cls, results: List[TestResultItem]) -> TestFeedback:
        insights: List[TestFailureInsight] = []
        for r in results:
            if r.outcome not in ("failed", "error"):
                continue
            insight = cls._one(r)
            insights.append(insight)
        return TestFeedback(total_failures=len(insights), insights=insights)

    @classmethod
    def _one(cls, r: TestResultItem) -> TestFailureInsight:
        msg = r.message or ""
        exc = _classify_exception_from_message(msg)
        description = cls._describe(msg, exc)
        suggestion_models: List[Suggestion] = []
        if exc:
            suggestion_models = SuggestionEngine.get_suggestions([exc], tool="pytest")
        suggestion: Optional[Suggestion] = suggestion_models[0] if suggestion_models else None
        return TestFailureInsight(
            test_node_id=r.node_id,
            exception_type=exc,
            description=description,
            suggestion=suggestion,
        )

    @staticmethod
    def _describe(message: str, exception_type: Optional[str]) -> str:
        if not message:
            return "No failure message provided."
        if exception_type == "AssertionError":
            m = _ASSERT_EQ_RE.search(message.replace("\n", " ").strip())
            if m:
                lhs, rhs = m.group(1).strip(), m.group(2).strip()
                return f"Expected {rhs}, got {lhs}"
            return "Assertion failed during test execution."
        if exception_type == "ZeroDivisionError":
            return "Division by zero raised during test execution."
        if exception_type:
            return f"{exception_type} raised during test execution."
        return message.strip()[:500]


def derive_feedback_status(suggestions: List[Suggestion]) -> Literal["pass", "warn", "fail"]:
    if any(s.severity == "critical" for s in suggestions):
        return "fail"
    if any(s.severity == "warning" for s in suggestions):
        return "warn"
    return "pass"


def lint_classifiers_from_issues(issues: List[AnalysisIssue]) -> List[str]:
    """Map issues to `SUGGESTION_RULES` keys (codes + mypy fallbacks)."""
    out: List[str] = []
    for i in issues:
        if i.tool == "pylint" and i.code in SUGGESTION_RULES:
            out.append(i.code)
            continue
        if i.tool == "mypy":
            guessed = _classify_exception_from_message(i.message)
            if guessed and guessed in SUGGESTION_RULES:
                out.append(guessed)
            elif i.severity == "error":
                out.append("TypeError")
            elif i.severity == "warning":
                out.append("ValueError")
    return out


def build_lint_feedback_summary(
    issues: List[AnalysisIssue],
) -> FeedbackSummary:
    lint_fb = LintFeedbackProcessor.process(issues)
    classifiers = lint_classifiers_from_issues(issues)
    suggestions = SuggestionEngine.get_suggestions(classifiers, tool="lint")
    status = derive_feedback_status(suggestions)
    return FeedbackSummary(
        status=status,
        summary=lint_fb.summary,
        lint=lint_fb,
        tests=None,
        suggestions=suggestions,
    )


def build_test_feedback_summary(results: List[TestResultItem]) -> FeedbackSummary:
    test_fb = TestFailureAnalyzer.analyze(results)
    classifiers: List[str] = []
    for ins in test_fb.insights:
        if ins.exception_type:
            classifiers.append(ins.exception_type)
    suggestions = SuggestionEngine.get_suggestions(classifiers, tool="pytest")
    status = derive_feedback_status(suggestions) if test_fb.total_failures else "pass"
    summary = (
        f"{test_fb.total_failures} test(s) failed"
        if test_fb.total_failures
        else "All tests passed"
    )
    return FeedbackSummary(
        status=status,
        summary=summary,
        lint=None,
        tests=test_fb,
        suggestions=suggestions,
    )
