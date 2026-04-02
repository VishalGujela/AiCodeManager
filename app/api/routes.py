"""HTTP route definitions (API v1)."""

from fastapi import APIRouter, HTTPException

from app.core.config import get_settings
from app.models.request_models import (
    AnalysisRequest,
    AnalysisResponse,
    ExecuteRequest,
    ExecuteResponse,
    GitRequest,
    GitResponse,
    HealthResponse,
    PipelineRunRequest,
    PipelineRunResponse,
    TestRunRequest,
    TestRunResponse,
)
from app.services.analyzer import Analyzer
from app.services.code_executor import CodeExecutor
from app.services.git_manager import GitManager
from app.services.pipeline_service import PipelineService
from app.services.test_runner import TestRunner

router = APIRouter(prefix="/api/v1", tags=["v1"])

_executor = CodeExecutor()
_analyzer = Analyzer()
_test_runner = TestRunner()
_git = GitManager()
_pipeline = PipelineService(analyzer=_analyzer, test_runner=_test_runner, git_manager=_git)


@router.get("/health", response_model=HealthResponse)
async def health() -> HealthResponse:
    s = get_settings()
    return HealthResponse(
        status="ok",
        version=s.app_version,
        environment=s.environment,
    )


@router.post("/execute", response_model=ExecuteResponse)
async def execute(req: ExecuteRequest) -> ExecuteResponse:
    return await _executor.execute(req)


@router.post("/analyse", response_model=AnalysisResponse)
async def analyse(req: AnalysisRequest) -> AnalysisResponse:
    return await _analyzer.analyse(req)


@router.post("/tests/run", response_model=TestRunResponse)
async def run_tests(req: TestRunRequest) -> TestRunResponse:
    return await _test_runner.run(req)


@router.post(
    "/pipeline/run",
    response_model=PipelineRunResponse,
    tags=["pipeline"],
)
async def pipeline_run(req: PipelineRunRequest) -> PipelineRunResponse:
    return await _pipeline.run_full_pipeline(req)


@router.post("/git", response_model=GitResponse)
async def git_operation(req: GitRequest) -> GitResponse:
    res = _git.run(req)
    if not res.success and res.message:
        raise HTTPException(status_code=400, detail=res.message)
    return res
