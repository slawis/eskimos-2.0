"""Modem control - backup, reboot, factory reset with auto-restore."""

from __future__ import annotations

import asyncio
import re

from eskimos.infrastructure.daemon.config import DaemonConfig
from eskimos.infrastructure.daemon.log import log
from eskimos.infrastructure.daemon.sms_incoming import SmsDedup

# Lazy httpx
httpx = None
HAS_HTTPX = False
try:
    import httpx as _httpx
    httpx = _httpx
    HAS_HTTPX = True
except ImportError:
    pass


async def _modem_api(client, base_url: str, method: str, params: dict = None,
                     headers: dict = None) -> dict:
    """Call modem JSON-RPC method. Returns parsed result or error dict."""
    try:
        resp = await client.post(f"{base_url}/jrd/webapi",
            json={"jsonrpc": "2.0", "method": method,
                  "params": params or {}, "id": "1"},
            headers=headers)
        data = resp.json()
        if "error" in data:
            return {"_error": data["error"]}
        return data.get("result", {})
    except Exception as e:
        return {"_error": str(e)}


async def _modem_login(client, base_url: str, modem_host: str) -> tuple:
    """Login to modem. Returns (headers, error_or_None)."""
    try:
        resp = await client.get(base_url)
        m = re.search(r'name="header-meta"\s+content="([^"]+)"', resp.text)
        if not m:
            return None, "Cannot extract token"

        tok = m.group(1)
        hdrs = {"_TclRequestVerificationKey": tok,
                "Referer": f"http://{modem_host}/index.html"}

        lr = await client.post(f"{base_url}/jrd/webapi",
            json={"jsonrpc": "2.0", "method": "Login",
                  "params": {"UserName": "admin", "Password": "admin"},
                  "id": "0"}, headers=hdrs)
        lr_data = lr.json()
        if "error" in lr_data:
            return None, f"Login failed: {lr_data['error']}"
        return hdrs, None
    except Exception as e:
        return None, str(e)


class ModemControlService:
    """Modem backup, reboot, and factory reset operations."""

    def __init__(self, config: DaemonConfig, dedup: SmsDedup) -> None:
        self.config = config
        self.dedup = dedup

    def _base_url(self) -> str:
        return f"http://{self.config.modem_host}:{self.config.modem_port}"

    async def _login(self, client) -> tuple:
        return await _modem_login(client, self._base_url(), self.config.modem_host)

    async def backup_settings(self) -> dict:
        """Backup all modem settings via Get* methods."""
        base_url = self._base_url()
        result = {"success": False, "backup": {}, "errors": {}}

        if not HAS_HTTPX:
            result["error"] = "httpx not available"
            return result

        backup_methods = [
            "GetProfileList",
            "GetConnectionSettings",
            "GetNetworkSettings",
            "GetLanSettings",
            "GetSMSSettings",
            "GetWlanSettings",
            "GetPowerSavingMode",
            "GetLanguage",
            "GetSMSStorageState",
            "GetSystemInfo",
        ]

        try:
            async with httpx.AsyncClient(timeout=15.0, follow_redirects=True) as client:
                hdrs, err = await self._login(client)
                if err:
                    result["error"] = err
                    return result

                for method in backup_methods:
                    data = await _modem_api(client, base_url, method, headers=hdrs)
                    if "_error" in data:
                        result["errors"][method] = data["_error"]
                    else:
                        result["backup"][method] = data

                # Also try built-in backup
                bk = await _modem_api(client, base_url, "SetDeviceBackup", headers=hdrs)
                result["builtin_backup"] = bk

                await _modem_api(client, base_url, "Logout", headers=hdrs)

            result["success"] = len(result["backup"]) > 0
            log(
                f"Modem backup: {len(result['backup'])} settings, "
                f"{len(result['errors'])} errors",
                self.config.log_file,
            )
        except Exception as e:
            result["error"] = str(e)

        return result

    async def reboot(self) -> dict:
        """Safe reboot - no data loss, modem comes back with same settings."""
        base_url = self._base_url()
        result = {"success": False}

        if not HAS_HTTPX:
            result["error"] = "httpx not available"
            return result

        try:
            async with httpx.AsyncClient(timeout=15.0, follow_redirects=True) as client:
                hdrs, err = await self._login(client)
                if err:
                    result["error"] = err
                    return result

                # Get SMS count before
                storage = await _modem_api(
                    client, base_url, "GetSMSStorageState", headers=hdrs)
                result["sms_before"] = storage.get("TUseCount", -1)

                # Reboot
                rb = await _modem_api(
                    client, base_url, "SetDeviceReboot", headers=hdrs)
                result["reboot_response"] = rb
                log("Modem reboot sent, waiting for restart...",
                    self.config.log_file)

            # Wait for modem to come back
            came_back = await self._wait_for_modem(base_url, 60, 60, 5)
            if not came_back:
                result["error"] = "Modem did not come back after 360s"
                return result
            result["restart_time_s"] = came_back

            # Check SMS after reboot
            await asyncio.sleep(5)
            async with httpx.AsyncClient(timeout=15.0, follow_redirects=True) as client:
                hdrs, err = await self._login(client)
                if err:
                    result["error"] = f"Post-reboot login failed: {err}"
                    return result
                storage = await _modem_api(
                    client, base_url, "GetSMSStorageState", headers=hdrs)
                result["sms_after"] = storage.get("TUseCount", -1)
                await _modem_api(client, base_url, "Logout", headers=hdrs)

            result["success"] = True

        except Exception as e:
            result["error"] = str(e)

        return result

    async def factory_reset(self) -> dict:
        """Factory reset modem with automatic backup/restore of settings."""
        base_url = self._base_url()
        result = {"success": False, "phases": {},
                  "sms_before": -1, "sms_after": -1}

        if not HAS_HTTPX:
            result["error"] = "httpx not available"
            return result

        # --- PHASE 1: BACKUP ---
        log("Factory reset PHASE 1: Backing up settings...",
            self.config.log_file)
        backup_result = await self.backup_settings()
        result["phases"]["backup"] = {
            "success": backup_result.get("success"),
            "settings_count": len(backup_result.get("backup", {})),
            "errors": backup_result.get("errors", {}),
        }
        backup = backup_result.get("backup", {})

        if not backup_result.get("success"):
            result["error"] = "Backup failed, aborting reset"
            return result

        result["backup"] = backup
        result["sms_before"] = backup.get(
            "GetSMSStorageState", {}).get("TUseCount", -1)
        log(
            f"Backup complete: {len(backup)} settings, SMS={result['sms_before']}",
            self.config.log_file,
        )

        # --- PHASE 2: RESET ---
        log("Factory reset PHASE 2: Sending SetDeviceReset...",
            self.config.log_file)
        try:
            async with httpx.AsyncClient(timeout=15.0, follow_redirects=True) as client:
                hdrs, err = await self._login(client)
                if err:
                    result["error"] = f"Login before reset failed: {err}"
                    return result

                reset_resp = await _modem_api(
                    client, base_url, "SetDeviceReset", headers=hdrs)
                result["phases"]["reset"] = {"response": reset_resp}
                log(f"SetDeviceReset response: {reset_resp}",
                    self.config.log_file)
        except Exception as e:
            result["error"] = f"Reset call failed: {e}"
            return result

        # --- PHASE 3: WAIT ---
        log("Factory reset PHASE 3: Waiting for modem to restart...",
            self.config.log_file)
        came_back = await self._wait_for_modem(base_url, 60, 78, 5)

        if not came_back:
            result["error"] = "Modem did not come back after 450s. Backup saved in result."
            result["phases"]["wait"] = {"error": "timeout"}
            return result

        result["phases"]["wait"] = {"restart_time_s": came_back}
        log(f"Modem back after {came_back}s", self.config.log_file)
        await asyncio.sleep(10)  # Extra wait for services to stabilize

        # --- PHASE 4: VERIFY SMS CLEARED ---
        log("Factory reset PHASE 4: Verifying SMS cleared...",
            self.config.log_file)
        try:
            async with httpx.AsyncClient(timeout=15.0, follow_redirects=True) as client:
                hdrs, err = await self._login(client)
                if err:
                    result["phases"]["verify"] = {
                        "error": f"Post-reset login: {err}"}
                else:
                    storage = await _modem_api(
                        client, base_url, "GetSMSStorageState", headers=hdrs)
                    result["sms_after"] = storage.get("TUseCount", -1)
                    result["phases"]["verify"] = {
                        "sms_after": result["sms_after"],
                        "sms_cleared": result["sms_after"] == 0,
                    }
                    log(f"SMS after reset: {result['sms_after']}",
                        self.config.log_file)

                    sysinfo = await _modem_api(
                        client, base_url, "GetSystemInfo", headers=hdrs)
                    result["phases"]["verify"]["imei"] = sysinfo.get("IMEI", "?")
        except Exception as e:
            result["phases"]["verify"] = {"error": str(e)}

        # --- PHASE 5: RESTORE ---
        log("Factory reset PHASE 5: Restoring settings...",
            self.config.log_file)
        restore_results = {}
        try:
            async with httpx.AsyncClient(timeout=15.0, follow_redirects=True) as client:
                hdrs, err = await self._login(client)
                if err:
                    result["phases"]["restore"] = {
                        "error": f"Login for restore: {err}"}
                    result["error"] = (
                        "Cannot login to restore settings. Backup saved in result.")
                    return result

                restore_results = await self._restore_settings(
                    client, base_url, hdrs, backup)

                await _modem_api(client, base_url, "Logout", headers=hdrs)

        except Exception as e:
            restore_results["_exception"] = str(e)

        result["phases"]["restore"] = restore_results

        # --- PHASE 6: FINAL VERIFY ---
        log("Factory reset PHASE 6: Final verification...",
            self.config.log_file)
        try:
            await asyncio.sleep(5)
            async with httpx.AsyncClient(timeout=15.0, follow_redirects=True) as client:
                hdrs, err = await self._login(client)
                if not err:
                    storage = await _modem_api(
                        client, base_url, "GetSMSStorageState", headers=hdrs)
                    result["sms_after"] = storage.get(
                        "TUseCount", result["sms_after"])
                    profiles = await _modem_api(
                        client, base_url, "GetProfileList", headers=hdrs)
                    conn_state = await _modem_api(
                        client, base_url, "GetConnectionState", headers=hdrs)
                    result["phases"]["final_verify"] = {
                        "sms": result["sms_after"],
                        "profiles": profiles,
                        "connection": conn_state,
                    }
                    await _modem_api(client, base_url, "Logout", headers=hdrs)
        except Exception as e:
            result["phases"]["final_verify"] = {"error": str(e)}

        result["success"] = result["sms_after"] == 0

        # Clear processed SMS IDs after factory reset
        if result["success"]:
            self.dedup.clear()
            log("Cleared processed SMS IDs after factory reset",
                self.config.log_file)

        log(
            f"Factory reset complete: SMS {result['sms_before']} -> {result['sms_after']}",
            self.config.log_file,
        )
        return result

    async def _wait_for_modem(
        self, base_url: str, initial_wait: int, retries: int, interval: int,
    ) -> int | None:
        """Wait for modem to come back online.

        Returns seconds elapsed if online, None if timeout.
        """
        await asyncio.sleep(initial_wait)
        for i in range(retries):
            await asyncio.sleep(interval)
            try:
                async with httpx.AsyncClient(timeout=5.0) as client:
                    resp = await client.get(base_url)
                    if resp.status_code == 200:
                        return (i + 1) * interval + initial_wait
            except Exception:
                pass
        return None

    async def _restore_settings(
        self, client, base_url: str, hdrs: dict, backup: dict,
    ) -> dict:
        """Restore modem settings from backup dict."""
        results = {}

        # 1. APN Profile (CRITICAL)
        profiles = backup.get("GetProfileList", {})
        profile_list = profiles.get("ProfileList", [])
        if profile_list:
            for profile in profile_list:
                pr = await _modem_api(
                    client, base_url, "AddNewProfile",
                    params=profile, headers=hdrs)
                results["AddNewProfile"] = pr
                log(f"APN restore: {pr}", self.config.log_file)
            # Set first profile as default
            dp = await _modem_api(
                client, base_url, "SetDefaultProfile",
                params={"ProfileID": 1}, headers=hdrs)
            results["SetDefaultProfile"] = dp

        # 2-7: Other settings
        restore_map = [
            ("GetConnectionSettings", "SetConnectionSettings"),
            ("GetNetworkSettings", "SetNetworkSettings"),
            ("GetLanSettings", "SetLanSettings"),
            ("GetSMSSettings", "SetSMSSettings"),
            ("GetPowerSavingMode", "SetPowerSavingMode"),
            ("GetLanguage", "SetLanguage"),
        ]
        for get_method, set_method in restore_map:
            data = backup.get(get_method)
            if data:
                r = await _modem_api(
                    client, base_url, set_method, params=data, headers=hdrs)
                results[set_method] = r

        # Try built-in restore
        br = await _modem_api(
            client, base_url, "SetDeviceRestore", headers=hdrs)
        results["SetDeviceRestore_builtin"] = br

        return results
