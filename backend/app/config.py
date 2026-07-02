# config.py
"""Application configuration loaded from environment variables."""
from __future__ import annotations

import logging
import sys
from functools import lru_cache
from pathlib import Path
from typing import List, Literal, Optional

from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Global application settings.

    Values are loaded from environment variables and/or a `.env` file
    located next to the backend package.
    """

    # --- App ---
    app_name: str = "Traveller Document Processing System"
    app_version: str = "1.0.0"
    debug: bool = False
    log_level: str = "INFO"
    log_json_format: bool = False  # Enable JSON structured logging

    # --- Database ---
    database_url: str = "postgresql+psycopg2://traveldocs:traveldocs@localhost:5432/traveldocs"

    # --- Storage ---
    upload_dir: str = "app/uploads"
    output_dir: str = "app/outputs"
    max_upload_size_mb: int = 25

    # --- OCR ---
    ocr_languages: str = "en,hi"
    ocr_use_gpu: bool = False
    ocr_det_model_dir: Optional[str] = None
    ocr_rec_model_dir: Optional[str] = None
    ocr_cls_model_dir: Optional[str] = None
    
    # OCR Backend: "auto" (prefer PaddleOCR, fallback to rapidocr), 
    # "paddleocr", or "rapidocr"
    ocr_backend: Literal["auto", "paddleocr", "rapidocr"] = "auto"
    
    # Orientation detection: "auto" (OCR-based), "off", or "heuristic"
    orientation_detection: Literal["auto", "off", "heuristic"] = "auto"

    # --- Duplicate detection ---
    fuzzy_match_threshold: int = 88

    # --- CORS ---
    cors_origins: str = "*"

    # --- Poppler (Windows support) ---
    poppler_path: Optional[str] = None  # Path to Poppler bin directory for Windows

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
    )

    @field_validator("log_level")
    @classmethod
    def _upper_log_level(cls, v: str) -> str:
        return v.upper()

    # --- Convenience helpers ---
    @property
    def upload_path(self) -> Path:
        p = Path(self.upload_dir)
        p.mkdir(parents=True, exist_ok=True)
        return p

    @property
    def output_path(self) -> Path:
        p = Path(self.output_dir)
        p.mkdir(parents=True, exist_ok=True)
        return p

    @property
    def ocr_language_list(self) -> List[str]:
        return [x.strip() for x in self.ocr_languages.split(",") if x.strip()]

    @property
    def cors_origin_list(self) -> List[str]:
        raw = self.cors_origins.strip()
        if raw == "*" or raw == "":
            return ["*"]
        return [x.strip() for x in raw.split(",") if x.strip()]

    @property
    def poppler_path_resolved(self) -> Optional[str]:
        """Resolve Poppler path for Windows compatibility."""
        if self.poppler_path:
            path = Path(self.poppler_path)
            if path.exists():
                return str(path)
            logger = logging.getLogger(__name__)
            logger.warning(
                "Poppler path configured but does not exist",
                extra={"poppler_path": self.poppler_path}
            )
        return None


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    """Cached singleton settings accessor."""
    return Settings()


def configure_logging(settings: Optional[Settings] = None) -> None:
    """Configure structured logging for the entire application."""
    settings = settings or get_settings()
    level = getattr(logging, settings.log_level, logging.INFO)

    root = logging.getLogger()
    # Clear existing handlers so uvicorn/paddle don't duplicate lines
    for h in list(root.handlers):
        root.removeHandler(h)
    
    if settings.log_json_format:
        # JSON structured logging for production
        try:
            import pythonjsonlogger.jsonlogger as json_logger
            handler = logging.StreamHandler(stream=sys.stdout)
            formatter = json_logger.JsonFormatter(
                fmt="%(asctime)s %(levelname)s %(name)s %(message)s",
                timestamp=True
            )
            handler.setFormatter(formatter)
            root.addHandler(handler)
        except ImportError:
            # Fallback to plain logging if python-json-logger not installed
            settings.log_json_format = False
            _configure_text_logging(root, level)
    else:
        _configure_text_logging(root, level)
    
    root.setLevel(level)

    # Quiet very chatty libraries
    logging.getLogger("ppocr").setLevel(logging.WARNING)
    logging.getLogger("paddle").setLevel(logging.WARNING)
    logging.getLogger("PIL").setLevel(logging.WARNING)
    logging.getLogger("pdf2image").setLevel(logging.INFO)


def _configure_text_logging(root: logging.Logger, level: int) -> None:
    """Configure text-based logging with timestamps."""
    fmt = "%(asctime)s | %(levelname)-8s | %(name)s | %(message)s"
    handler = logging.StreamHandler(stream=sys.stdout)
    handler.setFormatter(logging.Formatter(fmt))
    root.addHandler(handler)


def log_startup_diagnostics(settings: Settings) -> None:
    """Log system diagnostics at startup."""
    logger = logging.getLogger("startup")
    
    # System info
    import platform
    import sys
    
    logger.info(
        "Startup diagnostics",
        extra={
            "app_name": settings.app_name,
            "app_version": settings.app_version,
            "python_version": sys.version,
            "platform": platform.platform(),
            "processor": platform.processor(),
            "debug": settings.debug,
        }
    )
    
    # Check Poppler
    try:
        from pdf2image import convert_from_path
        logger.info(
            "Poppler availability",
            extra={
                "poppler_configured": bool(settings.poppler_path),
                "poppler_path": settings.poppler_path,
            }
        )
    except Exception as e:
        logger.warning("Poppler/PDF2Image check failed", extra={"error": str(e)})
    
    # Check OpenCV
    try:
        import cv2
        logger.info(
            "OpenCV info",
            extra={
                "opencv_version": cv2.__version__,
                "build_info": cv2.getBuildInformation().split("\n")[:3] if hasattr(cv2, "getBuildInformation") else "N/A",
            }
        )
    except Exception as e:
        logger.warning("OpenCV check failed", extra={"error": str(e)})
    
    # Check database connectivity
    try:
        from .database import get_session
        with get_session() as session:
            from sqlalchemy import text
            session.execute(text("SELECT 1"))
        logger.info("Database connection OK", extra={"url": settings.database_url.split("@")[-1]})
    except Exception as e:
        logger.error("Database connection failed", extra={"error": str(e)}, exc_info=True)
    
    # Check OCR engine
    try:
        from .services.ocr_service import get_ocr_service
        ocr = get_ocr_service()
        ocr.warmup()
        logger.info(
            "OCR engine ready",
            extra={
                "backend": ocr.backend,
                "languages": ocr.languages,
                "ready": ocr.is_ready(),
                "config_backend": settings.ocr_backend,
            }
        )
    except Exception as e:
        logger.error("OCR engine failed to initialize", extra={"error": str(e)}, exc_info=True)
    
    # Output directories
    logger.info(
        "Storage paths",
        extra={
            "upload_dir": str(settings.upload_path),
            "output_dir": str(settings.output_path),
            "max_upload_mb": settings.max_upload_size_mb,
        }
    )