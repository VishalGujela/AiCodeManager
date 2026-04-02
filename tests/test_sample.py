"""Unit and integration tests for AI Code Manager."""

import pytest
from httpx import ASGITransport, AsyncClient

from app.main import app


@pytest.mark.asyncio
class TestHealthEndpoint:
    async def test_health_returns_200(self) -> None:
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            response = await client.get("/api/v1/health")
        assert response.status_code == 200
        body = response.json()
        assert body["status"] == "ok"
        assert "version" in body
        assert "environment" in body


@pytest.mark.asyncio
class TestExecuteEndpoint:
    @pytest.mark.integration
    async def test_execute_python_prints(self) -> None:
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            response = await client.post(
                "/api/v1/execute",
                json={"language": "python", "code": "print('hello')", "timeout": 10},
            )
        assert response.status_code == 200
        data = response.json()
        assert data["success"] is True
        assert "hello" in data["stdout"]


@pytest.mark.asyncio
class TestAnalyseEndpoint:
    @pytest.mark.integration
    async def test_analyse_detects_lint_issue(self) -> None:
        transport = ASGITransport(app=app)
        code = "x=1\n"
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            response = await client.post(
                "/api/v1/analyse",
                json={"code": code, "language": "python", "analysis_type": "lint"},
            )
        assert response.status_code == 200
        data = response.json()
        assert "pylint_score" in data
        assert "issues" in data
