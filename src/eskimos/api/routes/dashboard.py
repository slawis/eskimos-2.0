"""Dashboard HTML routes."""

from __future__ import annotations

from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse

from eskimos import __version__

router = APIRouter()


@router.get("/dashboard", response_class=HTMLResponse)
async def dashboard_page(request: Request):
    """Main dashboard page."""
    templates = request.app.state.templates
    return templates.TemplateResponse("dashboard.html", {
        "request": request,
        "version": __version__,
        "page_title": "Dashboard",
    })


@router.get("/sms", response_class=HTMLResponse)
async def sms_page(request: Request):
    """SMS management page."""
    templates = request.app.state.templates
    return templates.TemplateResponse("sms.html", {
        "request": request,
        "version": __version__,
        "page_title": "SMS",
    })


@router.get("/settings", response_class=HTMLResponse)
async def settings_page(request: Request):
    """Settings and updates page."""
    templates = request.app.state.templates
    return templates.TemplateResponse("settings.html", {
        "request": request,
        "version": __version__,
        "page_title": "Ustawienia",
    })
