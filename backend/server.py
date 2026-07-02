"""Supervisor entry-point.

This file exists purely so the process manager in this environment (which
is hard-coded to run ``uvicorn server:app``) can boot the application. All
real logic lives in the ``app`` package (see ``app/main.py``); this module
only re-exports the FastAPI instance built there.
"""
from app.main import app

__all__ = ["app"]
