import logging
import time
from typing import Any, Dict, List, Optional

import requests
import urllib3

urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)
log = logging.getLogger("pve_node_monitor.monitor")

class NodeClient:
    def __init__(self, name: str, ip: str, node: str, user: str, password: str):
        self.name = name
        self.ip = ip
        self.node = node
        self.user = user
        self.password = password
        self.base = f"https://{ip}:8006/api2/json"
        self.ticket = self.csrf = None
        self.session = requests.Session()
        self.session.verify = False
        self.last_ok = 0.0
        self.online = False

    def authenticate(self) -> bool:
        try:
            r = self.session.post(
                f"{self.base}/access/ticket",
                data={"username": self.user, "password": self.password},
                timeout=5,
            )
            if r.status_code == 200:
                d = r.json()["data"]
                self.ticket = d["ticket"]
                self.csrf = d["CSRFPreventionToken"]
                return True
        except Exception as e:
            log.debug("%s auth error: %s", self.name, e)
        return False

    def _h(self):
        return {"CSRFPreventionToken": self.csrf or ""}

    def _c(self):
        return {"PVEAuthCookie": self.ticket or ""}

    def get_stats(self) -> Optional[Dict[str, Any]]:
        if not self.ticket and not self.authenticate():
            self.online = False
            return None
        try:
            r = self.session.get(
                f"{self.base}/nodes/{self.node}/status",
                headers=self._h(), cookies=self._c(), timeout=6,
            )
            if r.status_code == 401:
                if self.authenticate():
                    return self.get_stats()
                self.online = False
                return None
            if r.status_code != 200:
                log.warning("%s status HTTP %s", self.name, r.status_code)
                self.online = False
                return None

            d = r.json()["data"]
            cpu = round(float(d["cpu"]) * 100, 1)
            ram_u = round(d["memory"]["used"] / 1024**3, 1)
            ram_t = round(d["memory"]["total"] / 1024**3, 1)
            disk = round((d["rootfs"]["used"] / d["rootfs"]["total"]) * 100, 1)
            uptime = int(d.get("uptime", 0))

            active = 0
            for kind in ("qemu", "lxc"):
                rr = self.session.get(
                    f"{self.base}/nodes/{self.node}/{kind}",
                    headers=self._h(), cookies=self._c(), timeout=5,
                )
                if rr.status_code == 200:
                    active += sum(1 for g in rr.json().get("data", []) if g.get("status") == "running")

            net_in = net_out = 0.0
            try:
                rrd = self.session.get(
                    f"{self.base}/nodes/{self.node}/rrddata",
                    params={"timeframe": "hour", "cf": "AVERAGE"},
                    headers=self._h(), cookies=self._c(), timeout=5,
                )
                if rrd.status_code == 200:
                    for s in reversed(rrd.json().get("data", [])):
                        if s.get("netin") is not None:
                            net_in = round(float(s["netin"]) / 1024, 1)
                            net_out = round(float(s["netout"]) / 1024, 1)
                            break
            except Exception:
                pass

            self.online = True
            self.last_ok = time.time()
            return {
                "name": self.name,
                "cpu": cpu, "ram_used": ram_u, "ram_total": ram_t,
                "disk_pct": disk, "active_vms": active,
                "net_in": net_in, "net_out": net_out,
                "uptime": uptime, "online": True,
            }
        except Exception as e:
            log.debug("%s get_stats: %s", self.name, e)
            self.online = False
            return None

    def power(self, action: str) -> bool:
        """action = 'shutdown' or 'reboot'"""
        if not self.ticket and not self.authenticate():
            return False
        try:
            r = self.session.post(
                f"{self.base}/nodes/{self.node}/status",
                headers=self._h(), cookies=self._c(),
                data={"command": action}, timeout=10,
            )
            return r.status_code in (200, 202)
        except Exception as e:
            log.error("Power %s failed: %s", action, e)
            return False


class ProxmoxManager:
    def __init__(self, nodes_cfg: List[Dict]):
        self.clients = [
            NodeClient(n["name"], n["ip"], n["node"], n["user"], n["password"])
            for n in nodes_cfg
        ]

    def poll_all(self) -> List[Dict]:
        results = []
        for c in self.clients:
            s = c.get_stats()
            if s:
                results.append(s)
            else:
                results.append({
                    "name": c.name, "online": False,
                    "cpu": 0, "ram_used": 0, "ram_total": 0,
                    "disk_pct": 0, "active_vms": 0,
                    "net_in": 0, "net_out": 0, "uptime": 0,
                })
        return results

    def get_client(self, name: str) -> Optional[NodeClient]:
        for c in self.clients:
            if c.name == name:
                return c
        return None