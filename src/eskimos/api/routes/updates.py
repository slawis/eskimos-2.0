"""Update management endpoints."""

from __future__ import annotations

from datetime import datetime

from fastapi import APIRouter, HTTPException, BackgroundTasks
from pydantic import BaseModel

from eskimos import __version__

router = APIRouter()


# ==================== Models ====================

class UpdateInfo(BaseModel):
    """Information about available update."""

    current_version: str
    latest_version: str | None = None
    update_available: bool = False
    release_notes: str | None = None
    release_url: str | None = None
    checked_at: datetime


class UpdateStatusResponse(BaseModel):
    """Update operation status."""

    status: str  # checking, downloading, installing, completed, failed
    progress: int = 0  # 0-100
    message: str | None = None
    error: str | None = None


# Global update state (in production, use Redis or similar)
_update_status = UpdateStatusResponse(status="idle", progress=0)


# ==================== Endpoints ====================

@router.get("/check", response_model=UpdateInfo)
async def check_for_updates() -> UpdateInfo:
    """Check if updates are available.

    Checks GitHub releases for newer version.
    """
    import httpx

    try:
        # GitHub API - check releases
        # TODO: Replace with actual repo URL
        async with httpx.AsyncClient() as client:
            response = await client.get(
                "https://api.github.com/repos/slawis/eskimos-2.0/releases/latest",
                timeout=10.0,
                headers={"Accept": "application/vnd.github.v3+json"},
            )

            if response.status_code == 200:
                data = response.json()
                latest_version = data.get("tag_name", "").lstrip("v")

                # Compare versions
                current_parts = [int(x) for x in __version__.split(".")]
                latest_parts = [int(x) for x in latest_version.split(".")] if latest_version else current_parts

                update_available = latest_parts > current_parts

                return UpdateInfo(
                    current_version=__version__,
                    latest_version=latest_version or __version__,
                    update_available=update_available,
                    release_notes=data.get("body"),
                    release_url=data.get("html_url"),
                    checked_at=datetime.utcnow(),
                )

    except Exception as e:
        # If check fails, return current version info
        pass

    return UpdateInfo(
        current_version=__version__,
        latest_version=None,
        update_available=False,
        checked_at=datetime.utcnow(),
    )


@router.get("/status", response_model=UpdateStatusResponse)
async def get_update_status() -> UpdateStatusResponse:
    """Get current update operation status."""
    return _update_status


@router.post("/install")
async def install_update(background_tasks: BackgroundTasks) -> dict:
    """Install available update.

    Downloads and installs the latest version, then restarts the service.
    """
    global _update_status

    # Check if already updating
    if _update_status.status in ["downloading", "installing"]:
        raise HTTPException(
            status_code=409,
            detail="Update already in progress",
        )

    # Start update in background
    background_tasks.add_task(_perform_update)

    return {
        "status": "started",
        "message": "Update started in background. Check /api/update/status for progress.",
    }


async def _perform_update():
    """Background task to perform update."""
    global _update_status
    import subprocess
    import sys

    try:
        _update_status = UpdateStatusResponse(
            status="downloading",
            progress=10,
            message="Pobieranie aktualizacji...",
        )

        # Pull latest from git (if available)
        try:
            subprocess.run(
                ["git", "pull", "origin", "master"],
                capture_output=True,
                timeout=60,
            )
        except Exception:
            pass  # Git may not be available

        _update_status = UpdateStatusResponse(
            status="installing",
            progress=50,
            message="Instalowanie zaleznosci...",
        )

        # Reinstall package
        subprocess.run(
            [sys.executable, "-m", "pip", "install", "-e", ".", "--quiet"],
            capture_output=True,
            timeout=300,
        )

        _update_status = UpdateStatusResponse(
            status="completed",
            progress=100,
            message="Aktualizacja zakonczona! Zrestartuj serwer.",
        )

    except Exception as e:
        _update_status = UpdateStatusResponse(
            status="failed",
            progress=0,
            error=str(e),
        )


@router.post("/restart")
async def restart_server() -> dict:
    """Restart the API server.

    Note: This will terminate the current process.
    The service manager (NSSM, systemd) should restart it automatically.
    """
    import os
    import signal

    # Send SIGTERM to self
    os.kill(os.getpid(), signal.SIGTERM)

    return {"status": "restarting"}
