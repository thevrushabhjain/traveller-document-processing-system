"""SQLAlchemy database engine, session factory and ORM models."""
from __future__ import annotations

import logging
import uuid
from datetime import datetime, timezone
from typing import Iterator

from sqlalchemy import (
    JSON,
    Column,
    DateTime,
    Float,
    Integer,
    String,
    Text,
    create_engine,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.exc import OperationalError
from sqlalchemy.orm import DeclarativeBase, Session, sessionmaker
from sqlalchemy.types import TypeDecorator

from .config import get_settings

logger = logging.getLogger(__name__)
settings = get_settings()


class JSONType(TypeDecorator):
    """Use JSONB on PostgreSQL, generic JSON elsewhere (SQLite fallback)."""

    impl = JSON
    cache_ok = True

    def load_dialect_impl(self, dialect):
        if dialect.name == "postgresql":
            return dialect.type_descriptor(JSONB())
        return dialect.type_descriptor(JSON())


def _build_engine():
    url = settings.database_url
    connect_args = {}
    if url.startswith("sqlite"):
        connect_args = {"check_same_thread": False}
    engine = create_engine(
        url,
        pool_pre_ping=True,
        future=True,
        connect_args=connect_args,
    )
    return engine


engine = _build_engine()
SessionLocal = sessionmaker(bind=engine, autoflush=False, autocommit=False, future=True)


class Base(DeclarativeBase):
    pass


def _uuid() -> str:
    return str(uuid.uuid4())


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


class ProcessedDocument(Base):
    __tablename__ = "processed_documents"

    id = Column(String(64), primary_key=True, default=_uuid)
    filename = Column(String(512), nullable=False)
    stored_path = Column(String(1024), nullable=True)
    status = Column(String(32), nullable=False, default="PENDING", index=True)

    document_type = Column(String(32), nullable=False, default="UNKNOWN", index=True)
    document_number = Column(String(128), nullable=True, index=True)
    full_name = Column(String(256), nullable=True, index=True)
    normalized_name = Column(String(256), nullable=True, index=True)
    date_of_birth = Column(String(32), nullable=True, index=True)

    classification_confidence = Column(Float, nullable=False, default=0.0)
    overall_confidence = Column(Float, nullable=False, default=0.0)

    result_json = Column(JSONType, nullable=True)
    ocr_metadata = Column(JSONType, nullable=True)
    error = Column(Text, nullable=True)

    created_at = Column(DateTime(timezone=True), nullable=False, default=_utcnow, index=True)
    processed_at = Column(DateTime(timezone=True), nullable=True)


def init_db() -> None:
    """Create tables if they do not exist. Falls back to SQLite on failure."""
    global engine, SessionLocal
    try:
        with engine.connect() as conn:
            conn.execute(text("SELECT 1"))
    except OperationalError as exc:
        logger.warning(
            "Cannot reach configured database (%s). Falling back to local SQLite.",
            exc.__class__.__name__,
        )
        fallback_url = "sqlite:///./traveldocs.db"
        engine = create_engine(fallback_url, connect_args={"check_same_thread": False}, future=True)
        SessionLocal = sessionmaker(bind=engine, autoflush=False, autocommit=False, future=True)

    Base.metadata.create_all(bind=engine)
    logger.info("Database initialised (dialect=%s)", engine.dialect.name)


def get_session() -> Iterator[Session]:
    """FastAPI dependency yielding a SQLAlchemy session."""
    session = SessionLocal()
    try:
        yield session
    finally:
        session.close()


def db_dialect() -> str:
    return engine.dialect.name
