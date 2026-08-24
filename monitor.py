import logging
from typing import Any, Dict, Optional

import requests
import urllib3

urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)
log = logging.getLogger("pve_node_monitor.monitor")

class ProxmoxMonitor:
    def __init__(self, host: str, node: str, user: str, password: str):
        self.base = f"https://{host}:8006/api2/json"
        self.node = node
        self.user = user
        self.password = password
        self.ticket: Optional[str] = None
        self.csrf: Optional[str] = None
        self.session = requests.Session()
        self.session.verify = False

    def authenticate(self) -> bool:
        try:
            r = self.session.post(
                f"{self.base}/access/ticket",
                data={"username": self.user, "password": self.password},
                timeout=5,
            )
            if r.status_code == 200:
                data = r.json()["data"]
                self.ticket = data["ticket"]
                self.csrf = data["CSRFPreventionToken"]
                log.info("Proxmox authentication successful")
                return True
            log.error("Auth failed: HTTP %s – %s", r.status_code, r.text[:200])
        except Exception as e:
            log.error("Auth exception: %s", e)
        return False

    def _hdr(self) -> Dict[str, str]:
        return {"CSRFPreventionToken": self.csrf or ""}

    def _ck(self) -> Dict[str, str]:
        return {"PVEAuthCookie": self.ticket or ""}

    def get_stats(self) -> Optional[Dict[str, Any]]:
        if not self.ticket and not self.authenticate():
            return None
        try:
            # Node status
            r = self.session.get(
                f"{self.base}/nodes/{self.node}/status",
                headers=self._hdr(),
                cookies=self._ck(),
                timeout=5,
            )
            if r.status_code == 401:
                log.warning("Ticket expired – re-authenticating")
                if self.authenticate():
                    return self.get_stats()
                return None
            if r.status_code != 200:
                log.error("Status endpoint HTTP %s", r.status_code)
                return None

            d = r.json()["data"]
            cpu = round(float(d["cpu"]) * 100, 1)
            ram_u = round(d["memory"]["used"] / 1024**3, 1)
            ram_t = round(d["memory"]["total"] / 1024**3, 1)
            disk = round((d["rootfs"]["used"] / d["rootfs"]["total"]) * 100, 1)

            # Active guests
            active = 0
            for kind in ("qemu", "lxc"):
                rr = self.session.get(
                    f"{self.base}/nodes/{self.node}/{kind}",
                    headers=self._hdr(),
                    cookies=self._ck(),
                    timeout=5,
                )
                if rr.status_code == 200:
                    active += sum(
                        1 for g in rr.json().get("data", []) if g.get("status") == "running"
                    )

            # Network rates from RRD (bytes/s → KB/s)
            net_in = net_out = 0.0
            try:
                rrd = self.session.get(
                    f"{self.base}/nodes/{self.node}/rrddata",
                    params={"timeframe": "hour", "cf": "AVERAGE"},
                    headers=self._hdr(),
                    cookies=self._ck(),
                    timeout=5,
                )
                if rrd.status_code == 200:
                    samples = rrd.json().get("data", [])
                    for s in reversed(samples):
                        if s.get("netin") is not None and s.get("netout") is not None:
                            net_in = round(float(s["netin"]) / 1024, 1)
                            net_out = round(float(s["netout"]) / 1024, 1)
                            break
            except Exception as e:
                log.warning("RRD parse failed: %s", e)

            result = {
                "cpu": cpu,
                "ram_used": ram_u,
                "ram_total": ram_t,
                "disk_pct": disk,
                "active_vms": active,
                "net_in": net_in,
                "net_out": net_out,
            }
            log.debug("Metrics OK: %s", result)
            return result
        except Exception as e:
            log.exception("get_stats failed: %s", e)
            return None