"""FastAPI application for Eskimos 2.0 Dashboard.

This module creates and configures the FastAPI application with:
- REST API endpoints for SMS and modem management
- HTML dashboard with Jinja2 templates
- Static file serving (CSS, JS)
- WebSocket for real-time updates (future)
"""

from __future__ import annotations

import logging
from contextlib import asynccontextmanager
from pathlib import Path
from typing import AsyncGenerator

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import HTMLResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

from eskimos import __version__
from eskimos.api.routes import health, sms, modems, updates, dashboard

logger = logging.getLogger(__name__)

# Paths
API_DIR = Path(__file__).parent
TEMPLATES_DIR = API_DIR / "templates"
STATIC_DIR = API_DIR / "static"


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncGenerator[None, None]:
    """Application lifespan - startup and shutdown events."""
    logger.info(f"Starting Eskimos 2.0 API v{__version__}")
    yield
    logger.info("Shutting down Eskimos 2.0 API")


def create_app() -> FastAPI:
    """Create and configure FastAPI application.

    Returns:
        Configured FastAPI app instance
    """
    app = FastAPI(
        title="Eskimos 2.0",
        description="SMS Gateway with AI - Dashboard and REST API",
        version=__version__,
        docs_url="/api/docs",
        redoc_url="/api/redoc",
        lifespan=lifespan,
    )

    # CORS middleware
    app.add_middleware(
        CORSMiddleware,
        allow_origins=["*"],  # In production, specify allowed origins
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    # Mount static files
    if STATIC_DIR.exists():
        app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")

    # Setup templates
    if TEMPLATES_DIR.exists():
        app.state.templates = Jinja2Templates(directory=TEMPLATES_DIR)

    # Include API routes
    app.include_router(health.router, prefix="/api", tags=["Health"])
    app.include_router(sms.router, prefix="/api/sms", tags=["SMS"])
    app.include_router(modems.router, prefix="/api/modems", tags=["Modems"])
    app.include_router(updates.router, prefix="/api/update", tags=["Updates"])

    # Include dashboard routes (HTML)
    app.include_router(dashboard.router, tags=["Dashboard"])

    return app


# Create default app instance
app = create_app()


# Root redirect to dashboard
@app.get("/", response_class=HTMLResponse, include_in_schema=False)
async def root(request: Request):
    """Redirect to dashboard."""
    templates = request.app.state.templates
    return templates.TemplateResponse("dashboard.html", {
        "request": request,
        "version": __version__,
    })
