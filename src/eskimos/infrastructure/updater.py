"""
Eskimos Updater - Automatic Update System

Obsluguje:
1. Sprawdzanie dostepnosci aktualizacji (GitHub releases lub OVH)
2. Pobieranie paczki update
3. Weryfikacja SHA256 checksum
4. Backup obecnej wersji
5. Atomic replace (backup -> delete -> extract)
6. Rollback przy bledzie

Uzycie:
    from eskimos.infrastructure.updater import perform_update, check_for_update

    has_update, version = await check_for_update()
    if has_update:
        await perform_update(version)
"""

from __future__ import annotations

import hashlib
import os
import shutil
import zipfile
from datetime import datetime
from pathlib import Path
from typing import Optional, Tuple

# Lazy import
httpx = None
HAS_HTTPX = False

try:
    import httpx as _httpx
    httpx = _httpx
    HAS_HTTPX = True
except ImportError:
    pass


# ==================== Configuration ====================

PORTABLE_ROOT = Path(__file__).parent.parent.parent  # EskimosGateway/
ESKIMOS_DIR = PORTABLE_ROOT / "eskimos"
BACKUP_DIR = PORTABLE_ROOT / "_backups"
UPDATE_DIR = PORTABLE_ROOT / "_updates"
LOG_FILE = PORTABLE_ROOT / "updater.log"

CENTRAL_API = os.getenv("ESKIMOS_CENTRAL_API", "https://app.ninjabot.pl/api/eskimos")
DOWNLOAD_BASE_URL = os.getenv("ESKIMOS_DOWNLOAD_URL", "https://app.ninjabot.pl/eskimos/downloads")
GITHUB_REPO = os.getenv("ESKIMOS_GITHUB_REPO", "slawis/eskimos-2.0")

# Ile backupow trzymac
MAX_BACKUPS = 3


# ==================== Logging ====================

def log(message: str) -> None:
    """Log message to file."""
    timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    line = f"[{timestamp}] {message}"
    print(line)

    try:
        with open(LOG_FILE, "a", encoding="utf-8") as f:
            f.write(line + "\n")
    except Exception:
        pass


# ==================== Version Check ====================

async def check_for_update() -> Tuple[bool, Optional[str]]:
    """Check if update is available.

    Returns:
        Tuple of (update_available, latest_version)
    """
    if not HAS_HTTPX:
        return False, None

    try:
        from eskimos import __version__ as current_version
    except ImportError:
        current_version = "0.0.0"

    # Try central API first
    try:
        async with httpx.AsyncClient() as client:
            response = await client.get(
                f"{CENTRAL_API}/versions/latest",
                timeout=10.0
            )

            if response.status_code == 200:
                data = response.json()
                latest_version = data.get("version", "")

                if compare_versions(latest_version, current_version) > 0:
                    log(f"Update available: {current_version} -> {latest_version}")
                    return True, latest_version

                return False, current_version

    except Exception as e:
        log(f"Central API check failed: {e}")

    # Fallback to GitHub
    try:
        async with httpx.AsyncClient() as client:
            response = await client.get(
                f"https://api.github.com/repos/{GITHUB_REPO}/releases/latest",
                headers={"Accept": "application/vnd.github.v3+json"},
                timeout=10.0
            )

            if response.status_code == 200:
                data = response.json()
                latest_version = data.get("tag_name", "").lstrip("v")

                if compare_versions(latest_version, current_version) > 0:
                    log(f"Update available (GitHub): {current_version} -> {latest_version}")
                    return True, latest_version

    except Exception as e:
        log(f"GitHub check failed: {e}")

    return False, None


def compare_versions(v1: str, v2: str) -> int:
    """Compare two version strings.

    Returns:
        > 0 if v1 > v2
        < 0 if v1 < v2
        = 0 if v1 == v2
    """
    def parse(v):
        try:
            return tuple(int(x) for x in v.split("."))
        except ValueError:
            return (0, 0, 0)

    p1 = parse(v1)
    p2 = parse(v2)

    if p1 > p2:
        return 1
    elif p1 < p2:
        return -1
    return 0


# ==================== Download ====================

async def download_update(version: str) -> Path:
    """Download update package.

    Tries sources in order:
    1. OVH direct download (EskimosGateway.zip)
    2. Central API endpoint
    3. GitHub (fallback)

    Args:
        version: Version to download

    Returns:
        Path to downloaded zip file
    """
    UPDATE_DIR.mkdir(exist_ok=True)
    output_file = UPDATE_DIR / f"eskimos-{version}.zip"

    # 1. Try OVH direct download (primary)
    try:
        download_url = f"{DOWNLOAD_BASE_URL}/EskimosGateway.zip"
        log(f"Downloading from OVH: {download_url}")

        async with httpx.AsyncClient() as client:
            async with client.stream("GET", download_url, timeout=300.0,
                                      follow_redirects=True) as response:
                if response.status_code == 200:
                    with open(output_file, "wb") as f:
                        async for chunk in response.aiter_bytes(chunk_size=65536):
                            f.write(chunk)
                    size_mb = output_file.stat().st_size / (1024 * 1024)
                    log(f"Downloaded from OVH: {output_file.name} ({size_mb:.1f} MB)")
                    return output_file

    except Exception as e:
        log(f"OVH download failed: {e}")

    # 2. Try central API (may have versioned packages)
    try:
        async with httpx.AsyncClient() as client:
            response = await client.get(
                f"{CENTRAL_API}/update/download",
                params={"version": version},
                timeout=60.0,
                follow_redirects=True
            )

            if response.status_code == 200:
                with open(output_file, "wb") as f:
                    f.write(response.content)
                log(f"Downloaded from central API: {output_file.name}")
                return output_file

    except Exception as e:
        log(f"Central API download failed: {e}")

    # 3. Fallback to GitHub
    try:
        download_url = f"https://github.com/{GITHUB_REPO}/archive/refs/heads/master.zip"

        async with httpx.AsyncClient() as client:
            response = await client.get(
                download_url,
                timeout=120.0,
                follow_redirects=True
            )

            if response.status_code == 200:
                with open(output_file, "wb") as f:
                    f.write(response.content)
                log(f"Downloaded from GitHub: {output_file.name}")
                return output_file

    except Exception as e:
        log(f"GitHub download failed: {e}")
        raise RuntimeError(f"Failed to download update: {e}")

    raise RuntimeError("All download sources failed")


async def verify_checksum(file_path: Path, expected_hash: Optional[str]) -> bool:
    """Verify SHA256 checksum of downloaded file."""
    if not expected_hash:
        log("No checksum provided, skipping verification")
        return True

    sha256 = hashlib.sha256()
    with open(file_path, "rb") as f:
        for chunk in iter(lambda: f.read(8192), b""):
            sha256.update(chunk)

    actual_hash = sha256.hexdigest()

    if actual_hash.lower() == expected_hash.lower():
        log(f"Checksum verified: {actual_hash[:12]}...")
        return True
    else:
        log(f"Checksum mismatch! Expected: {expected_hash[:12]}, Got: {actual_hash[:12]}")
        return False


# ==================== Backup ====================

def create_backup() -> Path:
    """Create backup of current eskimos folder.

    Returns:
        Path to backup directory
    """
    BACKUP_DIR.mkdir(exist_ok=True)

    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    backup_name = f"eskimos-{timestamp}"
    backup_path = BACKUP_DIR / backup_name

    if ESKIMOS_DIR.exists():
        shutil.copytree(ESKIMOS_DIR, backup_path)
        log(f"Backup created: {backup_name}")
    else:
        log("No eskimos folder to backup")
        backup_path.mkdir()

    # Cleanup old backups
    cleanup_old_backups()

    return backup_path


def cleanup_old_backups() -> None:
    """Keep only MAX_BACKUPS most recent backups."""
    if not BACKUP_DIR.exists():
        return

    backups = sorted(
        BACKUP_DIR.glob("eskimos-*"),
        key=lambda p: p.stat().st_mtime,
        reverse=True
    )

    for old_backup in backups[MAX_BACKUPS:]:
        try:
            shutil.rmtree(old_backup)
            log(f"Removed old backup: {old_backup.name}")
        except Exception as e:
            log(f"Failed to remove backup: {e}")


def get_latest_backup() -> Optional[Path]:
    """Get most recent backup."""
    if not BACKUP_DIR.exists():
        return None

    backups = sorted(
        BACKUP_DIR.glob("eskimos-*"),
        key=lambda p: p.stat().st_mtime,
        reverse=True
    )

    return backups[0] if backups else None


# ==================== Apply Update ====================

async def apply_update(zip_file: Path) -> None:
    """Apply update from zip file.

    Args:
        zip_file: Path to downloaded zip file
    """
    extract_dir = UPDATE_DIR / "extract"

    try:
        # Clean extract dir
        if extract_dir.exists():
            shutil.rmtree(extract_dir)
        extract_dir.mkdir()

        # Extract
        log(f"Extracting {zip_file.name}...")
        with zipfile.ZipFile(zip_file, "r") as zf:
            zf.extractall(extract_dir)

        # Find eskimos source folder
        eskimos_src = None
        for item in extract_dir.rglob("eskimos"):
            if item.is_dir() and (item / "__init__.py").exists():
                eskimos_src = item
                break

        if not eskimos_src:
            # Try alternative structure (src/eskimos)
            for item in extract_dir.rglob("src/eskimos"):
                if item.is_dir() and (item / "__init__.py").exists():
                    eskimos_src = item
                    break

        if not eskimos_src:
            raise RuntimeError("Invalid update package: eskimos folder not found")

        log(f"Found source at: {eskimos_src}")

        # Remove current (except __pycache__)
        if ESKIMOS_DIR.exists():
            for item in ESKIMOS_DIR.iterdir():
                if item.name == "__pycache__":
                    continue
                if item.is_dir():
                    shutil.rmtree(item)
                else:
                    item.unlink()

        # Copy new files
        for item in eskimos_src.iterdir():
            dest = ESKIMOS_DIR / item.name
            if item.is_dir():
                shutil.copytree(item, dest)
            else:
                shutil.copy2(item, dest)

        log("Update applied successfully")

    finally:
        # Cleanup
        if extract_dir.exists():
            shutil.rmtree(extract_dir)
        if zip_file.exists():
            zip_file.unlink()


# ==================== Rollback ====================

async def rollback() -> bool:
    """Rollback to previous version.

    Returns:
        True if rollback successful
    """
    backup = get_latest_backup()

    if not backup:
        log("No backup available for rollback")
        return False

    log(f"Rolling back to: {backup.name}")

    try:
        # Remove current
        if ESKIMOS_DIR.exists():
            shutil.rmtree(ESKIMOS_DIR)

        # Restore from backup
        shutil.copytree(backup, ESKIMOS_DIR)

        log("Rollback complete")
        return True

    except Exception as e:
        log(f"Rollback failed: {e}")
        return False


# ==================== Main Update Function ====================

async def perform_update(version: Optional[str] = None, checksum: Optional[str] = None) -> bool:
    """Perform full update process.

    Args:
        version: Target version (optional, will check for latest)
        checksum: Expected SHA256 checksum (optional)

    Returns:
        True if update successful
    """
    log("=" * 50)
    log("Starting update process")
    log("=" * 50)

    try:
        # 1. Check version if not provided
        if not version:
            has_update, version = await check_for_update()
            if not has_update:
                log("No update available")
                return False

        log(f"Target version: {version}")

        # 2. Download
        zip_file = await download_update(version)

        # 3. Verify checksum
        if checksum:
            if not await verify_checksum(zip_file, checksum):
                zip_file.unlink()
                raise RuntimeError("Checksum verification failed")

        # 4. Backup
        backup_path = create_backup()

        # 5. Apply
        try:
            await apply_update(zip_file)
        except Exception as e:
            log(f"Update failed: {e}")
            log("Attempting rollback...")
            if await rollback():
                log("Rollback successful")
            else:
                log("CRITICAL: Rollback failed!")
            raise

        log("=" * 50)
        log(f"Update to {version} complete!")
        log("Restart required to apply changes")
        log("=" * 50)

        return True

    except Exception as e:
        log(f"Update process failed: {e}")
        import traceback
        traceback.print_exc()
        return False


# ==================== CLI ====================

async def _main():
    """CLI entry point."""
    import sys

    if len(sys.argv) < 2:
        print("Usage: python -m eskimos.infrastructure.updater [check|update|rollback]")
        return

    command = sys.argv[1].lower()

    if command == "check":
        has_update, version = await check_for_update()
        if has_update:
            print(f"Update available: {version}")
        else:
            print("No update available")

    elif command == "update":
        version = sys.argv[2] if len(sys.argv) > 2 else None
        success = await perform_update(version)
        print("Update successful" if success else "Update failed")

    elif command == "rollback":
        success = await rollback()
        print("Rollback successful" if success else "Rollback failed")

    else:
        print(f"Unknown command: {command}")


def main():
    import asyncio
    asyncio.run(_main())


if __name__ == "__main__":
    main()
