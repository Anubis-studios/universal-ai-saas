from __future__ import annotations

import logging
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles

from app.api.v1.endpoints import universal
from app.core.config import settings
from app.core.errors import GatewayError
from app.core.logging import configure_logging
from app.services.agentic_skills import AgenticSkillsService
from app.services.universal_gateway import UniversalGateway
from app.services.video_engine import OUTPUT_DIR, VideoEngine

logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI):
    configure_logging(settings.DEBUG)
    app.state.gateway = UniversalGateway()
    app.state.skills = AgenticSkillsService(app.state.gateway)
    app.state.video = VideoEngine()
    logger.info("%s v%s starting", settings.APP_NAME, settings.APP_VERSION)
    try:
        yield
    finally:
        await app.state.skills.close()
        logger.info("shutdown complete")


app = FastAPI(
    title=settings.APP_NAME,
    version=settings.APP_VERSION,
    description=(
        "One API over free and self-hosted AI models: text, image, video, embeddings "
        "and agentic skills."
    ),
    lifespan=lifespan,
    docs_url="/docs",
    openapi_url="/openapi.json",
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origins,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


OPEN_PATHS = {"/health", "/health/ready", "/openapi.json"}


@app.middleware("http")
async def require_api_key(request: Request, call_next):
    """Gate /api/v1 behind a bearer token when PLATFORM_API_KEY is set."""
    key = settings.PLATFORM_API_KEY
    path = request.url.path
    if not key or not path.startswith(settings.API_PREFIX):
        return await call_next(request)
    header = request.headers.get("authorization", "")
    token = header[7:].strip() if header.lower().startswith("bearer ") else ""
    if token != key:
        return JSONResponse(
            {"detail": "Invalid or missing API key."}, status_code=401,
            headers={"WWW-Authenticate": "Bearer"},
        )
    return await call_next(request)


@app.exception_handler(GatewayError)
async def gateway_error_handler(_: Request, exc: GatewayError) -> JSONResponse:
    return JSONResponse(
        status_code=exc.status_code,
        content={"error": exc.message, "provider": exc.provider, "type": type(exc).__name__},
    )


@app.get("/health", tags=["system"])
async def health() -> dict:
    return {"status": "ok", "app": settings.APP_NAME, "version": settings.APP_VERSION}


@app.get("/health/ready", tags=["system"])
async def readiness(request: Request) -> dict:
    status = await request.app.state.gateway.provider_status()
    return {"status": "ok" if any(status.values()) else "degraded", "providers": status}


OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
app.include_router(universal.router, prefix=settings.API_PREFIX)
app.mount("/media", StaticFiles(directory=OUTPUT_DIR), name="media")

# Serve the bundled single-page frontend when it ships alongside the backend.
_frontend = Path(settings.FRONTEND_DIR).resolve()
if (_frontend / "index.html").is_file():
    app.mount("/", StaticFiles(directory=_frontend, html=True), name="frontend")
    logger.info("serving frontend from %s", _frontend)
