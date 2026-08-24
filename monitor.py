import time
from typing import Any, Dict, Optional

import requests
import urllib3

urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

class ProxmoxMonitor:
    def __init__(self, host: str, node: str, user: str, password: str):
        self.base_url = f"https://{host}:8006/api2/json"
        self.node = node
        self.user = user
        self.password = password
        self.ticket: Optional[str] = None
        self.csrf: Optional[str] = None
        self._session = requests.Session()
        self._session.verify = False

    def authenticate(self) -> bool:
        try:
            url = f"{self.base_url}/access/ticket"
            data = {"username": self.user, "password": self.password}
            res = self._session.post(url, data=data, timeout=4)
            if res.status_code == 200:
                d = res.json()["data"]
                self.ticket = d["ticket"]
                self.csrf = d["CSRFPreventionToken"]
                return True
        except Exception:
            pass
        return False

    def _headers(self) -> Dict[str, str]:
        return {"CSRFPreventionToken": self.csrf or ""}

    def _cookies(self) -> Dict[str, str]:
        return {"PVEAuthCookie": self.ticket or ""}

    def get_stats(self) -> Optional[Dict[str, Any]]:
        if not self.ticket and not self.authenticate():
            return None

        try:
            # Node status
            res = self._session.get(
                f"{self.base_url}/nodes/{self.node}/status",
                headers=self._headers(),
                cookies=self._cookies(),
                timeout=4,
            )
            if res.status_code == 401:
                if self.authenticate():
                    return self.get_stats()
                return None

            if res.status_code != 200:
                return None

            d = res.json()["data"]
            cpu = round(d["cpu"] * 100, 1)
            ram_u = round(d["memory"]["used"] / (1024 ** 3), 1)
            ram_t = round(d["memory"]["total"] / (1024 ** 3), 1)
            disk_pct = round((d["rootfs"]["used"] / d["rootfs"]["total"]) * 100, 1)

            # Active guests
            active_vms = 0
            for path in ("qemu", "lxc"):
                r = self._session.get(
                    f"{self.base_url}/nodes/{self.node}/{path}",
                    headers=self._headers(),
                    cookies=self._cookies(),
                    timeout=4,
                )
                if r.status_code == 200:
                    active_vms += sum(1 for g in r.json()["data"] if g.get("status") == "running")

            # Network rates from RRD (bytes/sec → KB/s)
            net_in = 0.0
            net_out = 0.0
            try:
                rrd = self._session.get(
                    f"{self.base_url}/nodes/{self.node}/rrddata",
                    params={"timeframe": "hour", "cf": "AVERAGE"},
                    headers=self._headers(),
                    cookies=self._cookies(),
                    timeout=4,
                )
                if rrd.status_code == 200:
                    samples = rrd.json().get("data", [])
                    # Walk backwards for latest non-null sample
                    for s in reversed(samples):
                        if s.get("netin") is not None and s.get("netout") is not None:
                            net_in = round(s["netin"] / 1024, 1)   # KB/s
                            net_out = round(s["netout"] / 1024, 1)
                            break
            except Exception:
                pass

            return {
                "node": self.node,
                "cpu": cpu,
                "ram_used": ram_u,
                "ram_total": ram_t,
                "disk_pct": disk_pct,
                "active_vms": active_vms,
                "net_in": net_in,
                "net_out": net_out,
            }
        except Exception:
            return None