"""FastAPI application factory and uvicorn entry point."""

from contextlib import asynccontextmanager

from fastapi import FastAPI

from app.api.routes import router
from app.core.config import get_settings
from app.utils.logger import get_logger, setup_logging

logger = get_logger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI):
    setup_logging()
    settings = get_settings()
    logger.info("Starting AI Code Manager environment=%s", settings.environment)
    yield
    logger.info("Shutting down AI Code Manager")


def create_app() -> FastAPI:
    settings = get_settings()
    application = FastAPI(
        title="AI Code Manager",
        version=settings.app_version,
        lifespan=lifespan,
    )
    application.include_router(router)
    return application


app = create_app()


def main() -> None:
    import uvicorn

    settings = get_settings()
    uvicorn.run(
        "app.main:app",
        host="0.0.0.0",
        port=settings.port,
        reload=settings.debug,
    )


if __name__ == "__main__":
    main()
