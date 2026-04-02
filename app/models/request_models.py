"""Pydantic request and response schemas."""

from __future__ import annotations

from typing import Any, Dict, List, Literal, Optional

from pydantic import BaseModel, ConfigDict, Field


class HealthResponse(BaseModel):
    status: str = "ok"
    version: str
    environment: str


class ExecuteRequest(BaseModel):
    language: Literal["python", "javascript", "typescript", "bash"] = "python"
    code: str = Field(..., min_length=1)
    timeout: Optional[int] = Field(default=None, ge=1, le=300)


class ExecutionErrorInfo(BaseModel):
    """Structured error when the sandbox (e.g. Docker) cannot complete the run."""

    status: Literal["error"] = "error"
    message: str


class ExecuteResponse(BaseModel):
    success: bool
    stdout: str = ""
    stderr: str = ""
    exit_code: Optional[int] = None
    execution_time_ms: float
    execution_error: Optional[ExecutionErrorInfo] = None


class AnalysisIssue(BaseModel):
    tool: Literal["pylint", "mypy"]
    line: int
    column: int = 0
    code: str = ""
    message: str
    severity: str = "info"


class AnalysisRequest(BaseModel):
    code: str = Field(..., min_length=1)
    language: Literal["python"] = "python"
    analysis_type: Literal["lint", "type", "full"] = "full"


class Suggestion(BaseModel):
    model_config = ConfigDict(frozen=True)

    tool: str
    classifier: str
    short_description: str
    suggestion: str
    doc_link: Optional[str] = None
    severity: Literal["critical", "warning", "info"]


class TestFailureInsight(BaseModel):
    model_config = ConfigDict(frozen=True)

    test_node_id: str
    exception_type: Optional[str] = None
    description: str
    suggestion: Optional[Suggestion] = None


class LintFeedback(BaseModel):
    model_config = ConfigDict(frozen=True)

    summary: str
    groups: Dict[str, List[AnalysisIssue]] = Field(default_factory=dict)
    severity_counts: Dict[str, int] = Field(default_factory=dict)


class TestFeedback(BaseModel):
    model_config = ConfigDict(frozen=True)

    total_failures: int
    insights: List[TestFailureInsight] = Field(default_factory=list)


class FeedbackSummary(BaseModel):
    model_config = ConfigDict(frozen=True)

    status: Literal["pass", "warn", "fail"]
    summary: str
    lint: Optional[LintFeedback] = None
    tests: Optional[TestFeedback] = None
    suggestions: List[Suggestion] = Field(default_factory=list)


class AnalysisResponse(BaseModel):
    pylint_score: Optional[float] = None
    passed_threshold: bool
    issues: List[AnalysisIssue] = Field(default_factory=list)
    feedback: Optional[FeedbackSummary] = None


class TestRunRequest(BaseModel):
    test_paths: List[str] = Field(default_factory=lambda: ["tests/"])
    verbose: bool = True
    coverage: bool = False


class TestResultItem(BaseModel):
    """Single pytest case outcome (JUnit XML). Not a pytest test class."""

    __test__ = False

    node_id: str
    outcome: str
    duration_ms: float
    message: Optional[str] = None


class TestRunResponse(BaseModel):
    __test__ = False

    total: int = 0
    passed: int = 0
    failed: int = 0
    errors: int = 0
    skipped: int = 0
    duration_ms: float = 0.0
    results: List[TestResultItem] = Field(default_factory=list)
    feedback: Optional[FeedbackSummary] = None


class GitRequest(BaseModel):
    operation: Literal["commit", "branch", "diff", "log"]
    repo_path: str = Field(..., min_length=1)
    message: Optional[str] = None
    branch_name: Optional[str] = None
    max_log_entries: int = Field(default=20, ge=1, le=500)


class GitResponse(BaseModel):
    success: bool = True
    operation: str
    data: Dict[str, Any] = Field(default_factory=dict)
    message: Optional[str] = None


class AISuggestionPayload(BaseModel):
    """LLM fix suggestion; optional `error` when AI disabled or call failed."""

    fixed_code: str = ""
    explanation: str = ""
    confidence: float = Field(default=0.0, ge=0.0, le=1.0)
    error: Optional[str] = None


class PipelineRunRequest(BaseModel):
    code: str = Field(..., min_length=1)
    tests: str = Field(..., min_length=1)
    repo_path: str = Field(..., min_length=1)
    branch: str = Field(default="feature/test", min_length=1)
    code_filename: str = Field(default="user_code.py")
    test_filename: str = Field(default="test_user_code.py")
    commit_message_prefix: str = Field(default="Auto commit")
    auto_fix: bool = Field(
        default=False,
        description="If true and AI is enabled, apply suggested fix and re-run the pipeline once",
    )


class PipelineCommitInfo(BaseModel):
    message: str
    branch: str
    hexsha: Optional[str] = None


class PipelineFeedbackPayload(BaseModel):
    analysis_feedback: Optional[FeedbackSummary] = None
    test_feedback: Optional[FeedbackSummary] = None
    pylint_score: Optional[float] = None
    passed_threshold: bool = True
    test_total: int = 0
    test_passed: int = 0
    test_failed: int = 0
    test_errors: int = 0


class PipelineRunResponse(BaseModel):
    status: Literal["success", "failed"]
    decision: Literal["pass", "fail"]
    reason: Optional[str] = None
    commit: Optional[PipelineCommitInfo] = None
    feedback: Optional[PipelineFeedbackPayload] = None
    ai_suggestion: Optional[AISuggestionPayload] = None
    auto_fixed: Optional[bool] = None
    auto_fix_retry_feedback: Optional[PipelineFeedbackPayload] = None
