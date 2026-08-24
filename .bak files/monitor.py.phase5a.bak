import logging
import time
from typing import Any, Dict, List, Optional

import requests
import urllib3

urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)
log = logging.getLogger("pve_node_monitor")

class NodeClient:
    def __init__(self, name: str, ip: str, node: str, user: str, password: str, ntype: str = "server"):
        self.name = name
        self.ip = ip
        self.node = node
        self.user = user
        self.password = password
        self.ntype = ntype
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
            log.debug("%s auth: %s", self.name, e)
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
                "ip": self.ip,
                "node": self.node,
                "type": self.ntype,
                "cpu": cpu, "ram_used": ram_u, "ram_total": ram_t,
                "disk_pct": disk, "active_vms": active,
                "net_in": net_in, "net_out": net_out,
                "uptime": uptime, "online": True,
            }
        except Exception as e:
            log.debug("%s stats: %s", self.name, e)
            self.online = False
            return None

    def power(self, action: str) -> bool:
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
            log.error("Power %s: %s", action, e)
            return False

    def test_connection(self) -> Dict[str, Any]:
        import time as _t
        t0 = _t.time()
        ok = self.authenticate()
        ms = round((_t.time() - t0) * 1000)
        return {"ok": ok, "message": "Connected" if ok else "Auth failed", "latency_ms": ms}

    def list_guests(self):
        if not self.ticket and not self.authenticate():
            return []
        out = []
        for kind in ("qemu", "lxc"):
            try:
                r = self.session.get(
                    f"{self.base}/nodes/{self.node}/{kind}",
                    headers=self._h(), cookies=self._c(), timeout=6,
                )
                if r.status_code != 200:
                    continue
                for g in r.json().get("data", []):
                    out.append({
                        "vmid": g.get("vmid"),
                        "name": g.get("name") or str(g.get("vmid")),
                        "type": kind,
                        "status": g.get("status", "unknown"),
                        "cpu": round(float(g.get("cpu", 0)) * 100, 1) if g.get("cpu") is not None else 0,
                        "mem": round(float(g.get("mem", 0)) / 1024**3, 2) if g.get("mem") else 0,
                        "maxmem": round(float(g.get("maxmem", 0)) / 1024**3, 2) if g.get("maxmem") else 0,
                    })
            except Exception as e:
                log.debug("%s guests %s: %s", self.name, kind, e)
        out.sort(key=lambda x: (0 if x["status"] == "running" else 1, x["vmid"] or 0))
        return out

    def guest_power(self, vmid: int, kind: str, action: str) -> bool:
        if kind not in ("qemu", "lxc"):
            return False
        if action not in ("start", "stop", "shutdown", "reboot", "suspend", "resume"):
            return False
        if not self.ticket and not self.authenticate():
            return False
        try:
            r = self.session.post(
                f"{self.base}/nodes/{self.node}/{kind}/{vmid}/status/{action}",
                headers=self._h(), cookies=self._c(), timeout=12,
            )
            return r.status_code in (200, 202)
        except Exception as e:
            log.error("Guest power %s %s/%s: %s", action, kind, vmid, e)
            return False


class ProxmoxManager:
    def __init__(self, nodes_cfg: List[Dict]):
        self.clients = [
            NodeClient(
                n["name"], n["ip"], n["node"], n["user"], n["password"],
                n.get("type", "server")
            )
            for n in nodes_cfg
        ]

    def reload(self, nodes_cfg: List[Dict]):
        self.clients = [
            NodeClient(
                n["name"], n["ip"], n["node"], n["user"], n["password"],
                n.get("type", "server")
            )
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
                    "name": c.name, "ip": c.ip, "node": c.node, "type": c.ntype,
                    "online": False,
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
