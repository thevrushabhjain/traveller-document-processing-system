"""FastAPI application entry-point."""
from __future__ import annotations

import logging
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles

from .api import router as api_router
from .config import configure_logging, get_settings
from .database import init_db
from .services.ocr_service import get_ocr_service

logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI):  # noqa: D401
    """Startup / shutdown hooks."""
    settings = get_settings()
    configure_logging(settings)
    logger.info(
        "Starting %s v%s (debug=%s, ocr_langs=%s)",
        settings.app_name, settings.app_version, settings.debug, settings.ocr_language_list,
    )
    init_db()
    settings.upload_path  # ensures dir exists
    settings.output_path
    # Warmup OCR in background - if models must be downloaded, first request may still be slow
    try:
        get_ocr_service().warmup()
    except Exception:  # pragma: no cover
        logger.exception("PaddleOCR warmup failed - will retry lazily per request")
    yield
    logger.info("Shutting down %s", settings.app_name)


def _create_app() -> FastAPI:
    settings = get_settings()
    app = FastAPI(
        title=settings.app_name,
        version=settings.app_version,
        description=(
            "AI-powered Traveller Document Processing System. Extracts structured "
            "traveller data from passports, Aadhaar, PAN, driving licences, voter "
            "ID cards and other government-issued documents using PaddleOCR "
            "(offline, no API key required)."
        ),
        lifespan=lifespan,
    )

    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.cors_origin_list,
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    # Serve raw uploads so the frontend can preview them
    upload_dir = Path(settings.upload_dir)
    upload_dir.mkdir(parents=True, exist_ok=True)
    app.mount("/api/files", StaticFiles(directory=str(upload_dir)), name="files")

    app.include_router(api_router, prefix="/api")

    @app.exception_handler(Exception)
    async def _global_exception_handler(request: Request, exc: Exception):  # noqa: D401
        logger.exception("Unhandled error while handling %s %s", request.method, request.url.path)
        return JSONResponse(status_code=500, content={"message": "Internal server error", "detail": str(exc)})

    @app.get("/", tags=["meta"], include_in_schema=False)
    async def _root():
        return {"app": settings.app_name, "version": settings.app_version, "docs": "/docs"}

    return app


app = _create_app()
