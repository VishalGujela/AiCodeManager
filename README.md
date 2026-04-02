# AI Code Management System

A production-ready FastAPI backend for executing, analysing, testing, and
version-controlling code through a clean REST API — designed to serve as
the backend for AI-powered developer tooling.

-----

## Table of Contents

1. [Purpose](#purpose)
1. [Architecture Overview](#architecture-overview)
1. [Project Structure](#project-structure)
1. [Getting Started](#getting-started)
1. [API Reference](#api-reference)
1. [Configuration](#configuration)
1. [Running Tests](#running-tests)
1. [Design Decisions](#design-decisions)
1. [Production Hardening Checklist](#production-hardening-checklist)

-----

## Purpose

This system exposes four core capabilities over HTTP:

|Capability     |Endpoint                |Description                                       |
|---------------|------------------------|--------------------------------------------------|
|Code Execution |`POST /api/v1/execute`  |Run Python/JS/TS/Bash in a sandboxed subprocess   |
|Static Analysis|`POST /api/v1/analyse`  |Lint (pylint) and type-check (mypy) source code   |
|Test Execution |`POST /api/v1/tests/run`|Run pytest and return structured pass/fail results|
|Git Operations |`POST /api/v1/git`      |Commit, branch, diff, and log via GitPython       |

A lightweight health probe (`GET /api/v1/health`) is also provided for
load-balancer and Kubernetes liveness checks.

**TypeScript execution** uses `tsx` when available (`npx -y tsx` as a fallback when `npx` is installed), otherwise falls back to `node` (which may fail for raw `.ts` files).

-----

## Architecture Overview

```
HTTP Request
     │
     ▼
┌─────────────┐
│  FastAPI    │  ← Input validation (Pydantic), HTTP error mapping
│  Routes     │
└──────┬──────┘
       │ validated models
       ▼
┌─────────────┐
│  Services   │  ← Pure business logic, no HTTP concerns
│  Layer      │    (CodeExecutor, Analyzer, TestRunner, GitManager)
└──────┬──────┘
       │
       ▼
┌─────────────┐
│  Subprocess │  ← Python interpreter, pylint, mypy, pytest, git
│  / GitPython│
└─────────────┘
```

The application follows **Clean Architecture** principles:

- **Routing layer** (`app/api/`) handles HTTP concerns only.
- **Service layer** (`app/services/`) contains all business logic; it is
  completely decoupled from FastAPI and can be called from tests or CLI tools.
- **Models** (`app/models/`) define the contract between layers using Pydantic.
- **Core** (`app/core/`) holds cross-cutting configuration.
- **Utils** (`app/utils/`) holds shared infrastructure (logging).

-----

## Project Structure

```
ai_code_manager/
├── app/
│   ├── main.py                 # FastAPI app factory + uvicorn entry point
│   ├── api/
│   │   └── routes.py           # All HTTP route definitions
│   ├── core/
│   │   └── config.py           # Pydantic-settings configuration singleton
│   ├── models/
│   │   └── request_models.py   # Pydantic request / response schemas
│   ├── services/
│   │   ├── code_executor.py    # Sandboxed subprocess execution
│   │   ├── analyzer.py         # pylint + mypy static analysis
│   │   ├── test_runner.py      # pytest orchestration
│   │   └── git_manager.py      # GitPython wrapper
│   └── utils/
│       └── logger.py           # Structured JSON / coloured text logger
│
├── tests/
│   └── test_sample.py          # Unit + integration test suite
│
├── sandbox/                    # Temp execution files (auto-created, gitignored)
├── requirements.txt
├── README.md
└── .gitignore
```

-----

## Getting Started

### Prerequisites

- Python 3.8+ (3.11+ recommended; matches `requirements.txt` pins)
- `git` available on `PATH`

### Install dependencies

```bash
cd ai_code_manager
python -m venv .venv
source .venv/bin/activate        # Windows: .venv\Scripts\activate
pip install -r requirements.txt
```

### Run the development server

```bash
# Option 1 — via uvicorn directly
uvicorn app.main:app --reload --host 0.0.0.0 --port 8000

# Option 2 — via the module entry point
python -m app.main
```

The server starts at `http://localhost:8000`.

|URL                                  |Description             |
|-------------------------------------|------------------------|
|`http://localhost:8000/docs`         |Swagger UI (interactive)|
|`http://localhost:8000/redoc`        |ReDoc documentation     |
|`http://localhost:8000/api/v1/health`|Health probe            |

### Environment variables

Copy `.env.example` to `.env` and edit as needed:

```bash
cp .env.example .env
```

Key variables:

```env
ENVIRONMENT=development
DEBUG=true
LOG_LEVEL=INFO
LOG_FORMAT=text          # text for dev, json for prod
SECRET_KEY=change_me
EXECUTION_TIMEOUT_SECONDS=30
PYLINT_SCORE_THRESHOLD=7.0
```

-----

## API Reference

### `GET /api/v1/health`

Liveness probe.  No authentication required.

**Response 200**

```json
{ "status": "ok", "version": "0.1.0", "environment": "development" }
```

-----

### `POST /api/v1/execute`

Execute code in a sandboxed subprocess.

**Request body**

```json
{
  "language": "python",
  "code": "print('hello world')",
  "timeout": 10
}
```

**Response 200**

```json
{
  "success": true,
  "stdout": "hello world\n",
  "stderr": "",
  "exit_code": 0,
  "execution_time_ms": 42.7
}
```

-----

### `POST /api/v1/analyse`

Run static analysis on source code.

**Request body**

```json
{
  "code": "x=1\n",
  "language": "python",
  "analysis_type": "full"
}
```

`analysis_type` options: `lint` | `type` | `full`

**Response 200**

```json
{
  "pylint_score": 6.5,
  "passed_threshold": false,
  "issues": [
    {
      "tool": "pylint",
      "line": 1,
      "column": 0,
      "code": "C0304",
      "message": "Final newline missing",
      "severity": "convention"
    }
  ]
}
```

-----

### `POST /api/v1/tests/run`

Run the pytest test suite.

**Request body**

```json
{
  "test_paths": ["tests/test_sample.py::TestHealthEndpoint"],
  "verbose": true,
  "coverage": false
}
```

**Response 200**

```json
{
  "total": 3, "passed": 3, "failed": 0,
  "errors": 0, "skipped": 0,
  "duration_ms": 312.4,
  "results": [
    {
      "node_id": "tests/test_sample.py::TestHealthEndpoint::test_health_returns_200",
      "outcome": "passed",
      "duration_ms": 98.1,
      "message": null
    }
  ]
}
```

-----

### `POST /api/v1/git`

Perform a git operation.

**Request body (COMMIT)**

```json
{
  "operation": "commit",
  "repo_path": "/path/to/repo",
  "message": "feat: add new feature"
}
```

`operation` options: `commit` | `branch` | `diff` | `log`

**Branch:** optional `branch_name` to create or checkout; omit to list branches.

**Log:** optional `max_log_entries` (default 20).

-----

## Configuration

All configuration is managed through `app/core/config.py` using
`pydantic-settings`.  Every setting can be overridden via environment
variable or `.env` file.

|Variable                   |Default               |Description                         |
|---------------------------|----------------------|------------------------------------|
|`APP_VERSION`              |`0.1.0`               |Reported in /health and OpenAPI docs|
|`ENVIRONMENT`              |`development`         |Used for log context                |
|`DEBUG`                    |`true`                |Enables uvicorn hot-reload          |
|`PORT`                     |`8000`                |Server listen port                  |
|`LOG_LEVEL`                |`INFO`                |Python logging level                |
|`LOG_FORMAT`               |`json`                |`json` (prod) or `text` (dev)       |
|`SANDBOX_DIR`              |`sandbox`             |Working directory for code execution|
|`EXECUTION_TIMEOUT_SECONDS`|`30`                  |Hard timeout per execution request  |
|`PYLINT_SCORE_THRESHOLD`   |`7.0`                 |Min passing pylint score (0–10)     |
|`MYPY_STRICT`              |`false`               |Enable mypy strict mode             |
|`GIT_AUTHOR_NAME`          |`AI Code Manager`     |Commit author name                  |
|`GIT_AUTHOR_EMAIL`         |`ai@codemanager.local`|Commit author email                 |

-----

## Running Tests

```bash
# Run all tests
pytest tests/ -v

# Run only fast unit tests (skip integration)
pytest tests/ -v -m "not integration"

# Run with coverage
pytest tests/ --cov=app --cov-report=term-missing

# Run a single test class
pytest tests/test_sample.py::TestHealthEndpoint -v
```

-----

## Design Decisions

**Why async subprocesses?**

Using `asyncio.create_subprocess_exec` ensures that code execution,
analysis, and test-running never block the uvicorn event loop.  Other
requests continue to be served while a slow subprocess is running.

**Why subprocesses for pylint/mypy?**

Both tools mutate global Python interpreter state.  Running them as child
processes keeps the FastAPI server isolated from analysis side-effects.

**Why Pydantic for request models?**

FastAPI integrates natively with Pydantic v2.  All validation, coercion,
and OpenAPI schema generation happens automatically — no boilerplate.

**Why a module-level settings singleton?**

A single `Settings` object read at startup is fast, cacheable, and easily
injectable in tests via `get_settings.cache_clear()` + env var overrides.

-----

## Production Hardening Checklist

- [ ] Replace `SECRET_KEY` default with a secret manager (AWS SSM, Vault)
- [ ] Run code executor subprocess as a dedicated non-privileged OS user
- [ ] Add seccomp / AppArmor profile to the sandbox subprocess
- [ ] Add request authentication (API key or OAuth2 JWT)
- [ ] Add rate limiting middleware (e.g. `slowapi`)
- [ ] Move long-running test runs to a background task queue (Celery / ARQ)
- [ ] Add distributed tracing (OpenTelemetry)
- [ ] Containerise with a minimal base image (`python:3.12-slim`)
- [ ] Set `DEBUG=false` and `LOG_FORMAT=json` in production
