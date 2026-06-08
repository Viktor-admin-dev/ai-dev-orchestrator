"""FastAPI application factory for the orchestrator web dashboard."""

from __future__ import annotations

from pathlib import Path

from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

from orchestrator.web.serializers import state_css

_HERE = Path(__file__).resolve().parent


def create_app(db_path: Path) -> FastAPI:
    """Create and configure the FastAPI application."""
    from orchestrator.web.routes import build_router

    app = FastAPI(title="Orchestrator Dashboard")

    # Static files
    static_dir = _HERE / "static"
    static_dir.mkdir(exist_ok=True)
    app.mount("/static", StaticFiles(directory=str(static_dir)), name="static")

    # Templates
    templates = Jinja2Templates(directory=str(_HERE / "templates"))
    templates.env.globals["state_css"] = state_css

    # Register routes
    router = build_router(db_path, templates)
    app.include_router(router)

    return app
