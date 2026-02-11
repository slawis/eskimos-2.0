"""Diagnostics - modem probing, API method discovery, SMS deletion tests."""

from __future__ import annotations

import re
from datetime import datetime

from eskimos.infrastructure.daemon.config import DaemonConfig
from eskimos.infrastructure.daemon.log import log
from eskimos.infrastructure.daemon.modem_status import ModemStatusProvider
from eskimos.infrastructure.daemon.sms_metrics import SmsMetrics
from eskimos.infrastructure.daemon.identity import get_system_info

# Lazy httpx
httpx = None
HAS_HTTPX = False
try:
    import httpx as _httpx
    httpx = _httpx
    HAS_HTTPX = True
except ImportError:
    pass


class DiagnosticsService:
    """Modem diagnostics: probing, API discovery, SMS deletion tests."""

    def __init__(
        self,
        config: DaemonConfig,
        modem_status: ModemStatusProvider,
        metrics: SmsMetrics,
        dedup_count_fn=None,
    ) -> None:
        self.config = config
        self.modem_status = modem_status
        self.metrics = metrics
        self._dedup_count_fn = dedup_count_fn

    def _base_url(self) -> str:
        return f"http://{self.config.modem_host}:{self.config.modem_port}"

    async def probe_modem_debug(self) -> dict:
        """Probe modem for model info via HTML/JS files and hashed login."""
        if not HAS_HTTPX:
            return {"error": "httpx not available"}

        import hashlib
        import base64

        results = {}
        base_url = self._base_url()

        async with httpx.AsyncClient(timeout=5.0, follow_redirects=True) as client:
            # Get main page (full HTML)
            token = ""
            try:
                resp = await client.get(base_url)
                html = resp.text
                results["html_length"] = len(html)

                # Extract token
                m = re.search(
                    r'name="header-meta"\s+content="([^"]+)"', html)
                if m:
                    token = m.group(1)
                    results["tcl_token"] = token

                # Extract all script src URLs
                scripts = re.findall(r'src="([^"]+\.js[^"]*)"', html)
                results["js_files"] = scripts[:10]

                # Look for model/device info in HTML
                for pattern in [
                    r'device[_\-]?[Nn]ame["\s:=]+["\']([^"\']+)',
                    r'[Mm]odel[_\-]?[Nn]ame["\s:=]+["\']([^"\']+)',
                    r'product[_\-]?[Nn]ame["\s:=]+["\']([^"\']+)',
                ]:
                    m2 = re.search(pattern, html)
                    if m2:
                        results["html_model"] = m2.group(1)
                        break
            except Exception as e:
                results["main_page_error"] = str(e)

            # Fetch JS files that might contain model info
            js_paths = [
                "/js/home.js", "/js/app.js", "/js/main.js",
                "/js/config.js", "/js/device.js", "/js/status.js",
            ]
            for path in js_paths:
                try:
                    resp = await client.get(f"{base_url}{path}")
                    if resp.status_code == 200 and len(resp.text) > 10:
                        for pat in [
                            r'"DeviceName"\s*:\s*"([^"]+)"',
                            r'"model"\s*:\s*"([^"]+)"',
                            r'IK\d+\w+', r'MW\d+\w+', r'MR\d+\w+',
                        ]:
                            m = re.search(pat, resp.text)
                            if m:
                                results[f"js_{path}_match"] = m.group(0)[:200]
                                break
                        if f"js_{path}_match" not in results:
                            results[f"js_{path}_size"] = len(resp.text)
                except Exception:
                    pass

            # Try login with hashed passwords
            if token:
                headers = {
                    "_TclRequestVerificationKey": token,
                    "Referer": f"http://{self.config.modem_host}/index.html",
                }
                pwd_variants = [
                    ("admin_plain", "admin"),
                    ("admin_b64", base64.b64encode(b"admin").decode()),
                    ("admin_sha256", hashlib.sha256(b"admin").hexdigest()),
                    ("empty_plain", ""),
                ]
                for name, pwd in pwd_variants:
                    try:
                        login_body = {
                            "jsonrpc": "2.0",
                            "method": "Login",
                            "params": {"UserName": "admin", "Password": pwd},
                            "id": "1",
                        }
                        resp = await client.post(
                            f"{base_url}/jrd/webapi",
                            json=login_body, headers=headers)
                        resp_text = resp.text[:300]
                        results[f"login_{name}"] = resp_text
                        if ("result" in resp_text
                                and "error" not in resp_text.lower()):
                            body = {
                                "jsonrpc": "2.0",
                                "method": "GetSystemInfo",
                                "params": {}, "id": "1",
                            }
                            resp2 = await client.post(
                                f"{base_url}/jrd/webapi",
                                json=body, headers=headers)
                            results["system_info_after_login"] = (
                                resp2.text[:2000])
                            await client.post(
                                f"{base_url}/jrd/webapi",
                                json={"jsonrpc": "2.0", "method": "Logout",
                                      "params": {}, "id": "1"},
                                headers=headers)
                            break
                    except Exception as e:
                        results[f"login_{name}_error"] = str(e)

        return results

    async def discover_api_methods(self) -> dict:
        """Fetch modem's web panel JS files and extract all JSON-RPC method names."""
        base_url = self._base_url()
        result = {
            "all_methods": [], "sms_methods": [], "delete_methods": [],
            "set_methods": [], "js_files_checked": [],
        }

        if not HAS_HTTPX:
            result["error"] = "httpx not available"
            return result

        try:
            async with httpx.AsyncClient(timeout=15.0, follow_redirects=True) as client:
                resp = await client.get(base_url)
                scripts = re.findall(r'src="([^"]+\.js[^"]*)"', resp.text)

                all_methods = set()
                for script_path in scripts:
                    url = f"{base_url}/{script_path.lstrip('/')}"
                    try:
                        resp = await client.get(url)
                        if resp.status_code == 200:
                            js_text = resp.text
                            result["js_files_checked"].append(
                                f"{script_path} ({len(js_text)} bytes)")
                            # Pattern 1: Known API verb prefixes
                            m1 = re.findall(
                                r'''["']((?:Get|Set|Delete|Send|Save|Clear|Remove|Check|Login|Logout|Connect|Disconnect|Start|Stop|Enable|Disable|Add|Update|Create|Reset|Change)[A-Z][a-zA-Z0-9]*?)["']''',
                                js_text,
                            )
                            all_methods.update(m1)
                            # Pattern 2: lowercase get/set variants
                            m2 = re.findall(
                                r'''["']((?:get|set)[A-Z][a-zA-Z0-9]+)["']''',
                                js_text,
                            )
                            all_methods.update(m2)
                            # Pattern 3: URL ?api=Method or ?name=Method
                            m3 = re.findall(
                                r'''[?&](?:api|name)=["']?([A-Za-z][a-zA-Z]+)["']?''',
                                js_text,
                            )
                            all_methods.update(m3)
                            # Pattern 4: "method":"MethodName"
                            m4 = re.findall(
                                r'''["']?method["']?\s*[,:]\s*["']([A-Za-z][a-zA-Z]+)["']''',
                                js_text,
                            )
                            all_methods.update(m4)
                            # Pattern 5: Property style
                            m5 = re.findall(
                                r'''((?:Get|Set|Delete|Send|Login|Logout|get|set)[A-Z][a-zA-Z]+)\s*[:=]''',
                                js_text,
                            )
                            all_methods.update(m5)
                    except Exception:
                        pass

                result["all_methods"] = sorted(all_methods)
                result["sms_methods"] = sorted(
                    m for m in all_methods if "sms" in m.lower())
                result["delete_methods"] = sorted(
                    m for m in all_methods
                    if "delete" in m.lower() or "clear" in m.lower()
                    or "remove" in m.lower())
                result["set_methods"] = sorted(
                    m for m in all_methods
                    if m.startswith("Set") or m.startswith("set"))
                result["reboot_methods"] = sorted(
                    m for m in all_methods
                    if "reboot" in m.lower() or "reset" in m.lower()
                    or "factory" in m.lower())
                result["storage_methods"] = sorted(
                    m for m in all_methods
                    if "storage" in m.lower() or "memory" in m.lower())
                result["total_methods"] = len(all_methods)

        except Exception as e:
            result["error"] = str(e)

        return result

    async def try_delete_sms(self) -> dict:
        """Try multiple methods to delete SMS from modem."""
        base_url = self._base_url()
        results = {
            "methods_tried": [], "success": False,
            "sms_before": 0, "sms_after": 0,
        }

        if not HAS_HTTPX:
            results["error"] = "httpx not available"
            return results

        try:
            async with httpx.AsyncClient(timeout=15.0, follow_redirects=True) as client:
                # Get token
                resp = await client.get(base_url)
                m = re.search(
                    r'name="header-meta"\s+content="([^"]+)"', resp.text)
                if not m:
                    results["error"] = "Cannot extract token"
                    return results

                token = m.group(1)
                headers = {
                    "_TclRequestVerificationKey": token,
                    "Referer": f"http://{self.config.modem_host}/index.html",
                }

                # Login
                resp = await client.post(
                    f"{base_url}/jrd/webapi",
                    json={"jsonrpc": "2.0", "method": "Login",
                          "params": {"UserName": "admin", "Password": "admin"},
                          "id": "1"},
                    headers=headers)
                if "error" in resp.json():
                    results["error"] = f"Login failed: {resp.text[:200]}"
                    return results

                # Count SMS before
                resp = await client.post(
                    f"{base_url}/jrd/webapi",
                    json={"jsonrpc": "2.0", "method": "GetSMSContactList",
                          "params": {"Page": 0, "ContactNum": 100},
                          "id": "2"},
                    headers=headers)
                contacts = (
                    (resp.json().get("result") or {})
                    .get("SMSContactList") or [])
                total_before = sum(
                    c.get("TSMSCount", 0) for c in contacts)
                results["sms_before"] = total_before
                results["contacts_before"] = len(contacts)

                # Check storage state
                try:
                    resp = await client.post(
                        f"{base_url}/jrd/webapi",
                        json={"jsonrpc": "2.0", "method": "GetSMSStorageState",
                              "params": {}, "id": "3"},
                        headers=headers)
                    results["storage_state"] = resp.json()
                except Exception:
                    pass

                # Prepare IDs for delete attempts
                contact_ids = [
                    c.get("ContactId") for c in contacts
                    if c.get("ContactId")]
                sms_ids = [
                    c.get("SMSId") for c in contacts if c.get("SMSId")]

                first_sms_id = None
                if contact_ids:
                    try:
                        resp = await client.post(
                            f"{base_url}/jrd/webapi",
                            json={"jsonrpc": "2.0",
                                  "method": "GetSMSContentList",
                                  "params": {"Page": 0,
                                             "ContactId": contact_ids[0]},
                                  "id": "4"},
                            headers=headers)
                        sms_list = (
                            (resp.json().get("result") or {})
                            .get("SMSContentList") or [])
                        if sms_list:
                            first_sms_id = sms_list[0].get("SMSId")
                            results["first_sms_detail"] = sms_list[0]
                    except Exception:
                        pass

                delete_attempts = [
                    ("DeleteALLsingle", {},
                     "DeleteALLsingle (no params)"),
                    ("DeleteALLsingle",
                     {"ContactId": contact_ids[0] if contact_ids else 0},
                     "DeleteALLsingle by ContactId"),
                    ("DeleteALLsingle",
                     {"SMSId": first_sms_id or (
                         sms_ids[0] if sms_ids else 0)},
                     "DeleteALLsingle by SMSId"),
                    ("DeleteSMS",
                     {"SMSId": first_sms_id or 0},
                     "DeleteSMS by content SMSId"),
                    ("DeleteSMS",
                     {"SMSId": first_sms_id or 0, "Flag": 0},
                     "DeleteSMS SMSId+Flag0"),
                    ("DeleteSMS",
                     {"ContactId": contact_ids[0] if contact_ids else 0,
                      "Flag": 0},
                     "DeleteSMS ContactId+Flag0"),
                    ("DeleteSMS",
                     {"ContactId": contact_ids[0] if contact_ids else 0,
                      "Flag": 1},
                     "DeleteSMS ContactId+Flag1"),
                    ("DeleteSMS", {"Flag": 2},
                     "DeleteSMS Flag2 (delete all)"),
                    ("SetSMSSettings", {"SaveSMS": 0},
                     "Disable SMS saving"),
                ]

                req_id = 10
                for method, params, desc in delete_attempts:
                    try:
                        resp = await client.post(
                            f"{base_url}/jrd/webapi",
                            json={"jsonrpc": "2.0", "method": method,
                                  "params": params, "id": str(req_id)},
                            headers=headers)
                        resp_data = resp.json()
                        success = (
                            "result" in resp_data
                            and "error" not in resp_data)
                        attempt = {
                            "method": method,
                            "params": params,
                            "desc": desc,
                            "success": success,
                            "response": str(resp_data)[:300],
                        }
                        if success:
                            try:
                                resp2 = await client.post(
                                    f"{base_url}/jrd/webapi",
                                    json={
                                        "jsonrpc": "2.0",
                                        "method": "GetSMSContactList",
                                        "params": {"Page": 0,
                                                   "ContactNum": 100},
                                        "id": str(req_id + 100),
                                    },
                                    headers=headers)
                                c_after = (
                                    (resp2.json().get("result") or {})
                                    .get("SMSContactList") or [])
                                count_after = sum(
                                    c.get("TSMSCount", 0) for c in c_after)
                                attempt["sms_count_after"] = count_after
                                if count_after < total_before:
                                    attempt["sms_deleted"] = (
                                        total_before - count_after)
                                    results["working_method"] = desc
                            except Exception:
                                pass
                        results["methods_tried"].append(attempt)
                        req_id += 1
                    except Exception as e:
                        results["methods_tried"].append({
                            "method": method, "desc": desc,
                            "success": False, "error": str(e),
                        })

                # Count SMS after
                if not results.get("modem_rebooted"):
                    try:
                        resp = await client.post(
                            f"{base_url}/jrd/webapi",
                            json={"jsonrpc": "2.0",
                                  "method": "GetSMSContactList",
                                  "params": {"Page": 0, "ContactNum": 100},
                                  "id": "99"},
                            headers=headers)
                        contacts_after = (
                            (resp.json().get("result") or {})
                            .get("SMSContactList") or [])
                        results["sms_after"] = sum(
                            c.get("TSMSCount", 0) for c in contacts_after)
                        results["success"] = (
                            results["sms_after"] < total_before)
                    except Exception:
                        pass

                    try:
                        await client.post(
                            f"{base_url}/jrd/webapi",
                            json={"jsonrpc": "2.0", "method": "Logout",
                                  "params": {}, "id": "100"},
                            headers=headers)
                    except Exception:
                        pass

        except Exception as e:
            results["error"] = str(e)

        return results

    async def run_diagnostic(self) -> dict:
        """Run full diagnostic checks including modem HTTP probing."""
        modem = await self.modem_status.get_status()
        metrics = self.metrics.to_heartbeat_dict()
        system = get_system_info()

        # Debug: include daemon config state
        system["modem_type"] = self.config.modem_type
        system["modem_phone"] = self.config.modem_phone
        system["config_file"] = str(self.config.config_file)
        system["config_exists"] = self.config.config_file.exists()
        try:
            system["config_content"] = (
                self.config.config_file.read_text()[:500]
                if self.config.config_file.exists() else "NOT FOUND")
        except Exception:
            system["config_content"] = "READ ERROR"

        # Direct HTTP probe to modem
        modem_debug = {}
        try:
            reachable = await self.modem_status._probe_direct()
            if reachable:
                modem_debug = await self.probe_modem_debug()
            else:
                modem_debug = {"error": "Modem not reachable via TCP"}
        except Exception as e:
            modem_debug = {"error": str(e)}

        # Test incoming SMS read from modem
        incoming_test = {}
        try:
            base_url = self._base_url()
            async with httpx.AsyncClient(
                timeout=15.0, follow_redirects=True,
            ) as hc:
                resp = await hc.get(base_url)
                m = re.search(
                    r'name="header-meta"\s+content="([^"]+)"', resp.text)
                if m:
                    token = m.group(1)
                    hdrs = {
                        "_TclRequestVerificationKey": token,
                        "Referer": (
                            f"http://{self.config.modem_host}/index.html"),
                    }
                    resp = await hc.post(
                        f"{base_url}/jrd/webapi",
                        json={"jsonrpc": "2.0", "method": "Login",
                              "params": {"UserName": "admin",
                                         "Password": "admin"},
                              "id": "1"},
                        headers=hdrs)
                    login = resp.json()
                    incoming_test["login"] = str(login)
                    if "error" not in login:
                        resp = await hc.post(
                            f"{base_url}/jrd/webapi",
                            json={"jsonrpc": "2.0",
                                  "method": "GetSMSContactList",
                                  "params": {"Page": 0, "ContactNum": 100},
                                  "id": "2"},
                            headers=hdrs)
                        contacts = resp.json()
                        incoming_test["contacts_raw"] = str(contacts)
                        clist = (
                            (contacts.get("result") or {})
                            .get("SMSContactList") or [])
                        incoming_test["conversations"] = len(clist)
                        if self._dedup_count_fn:
                            incoming_test["processed_ids"] = (
                                self._dedup_count_fn())
                        try:
                            await hc.post(
                                f"{base_url}/jrd/webapi",
                                json={"jsonrpc": "2.0", "method": "Logout",
                                      "params": {}, "id": "99"},
                                headers=hdrs)
                        except Exception:
                            pass
                else:
                    incoming_test["error"] = "no token"
        except Exception as e:
            incoming_test["error"] = str(e)

        return {
            "modem": modem,
            "modem_debug": modem_debug,
            "incoming_test": incoming_test,
            "metrics": metrics,
            "system": system,
            "timestamp": datetime.utcnow().isoformat(),
        }
