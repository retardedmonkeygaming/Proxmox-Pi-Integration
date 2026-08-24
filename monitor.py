import requests
import urllib3

urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

class ProxmoxMonitor:
    def __init__(self, host, node, user, password):
        self.base_url = f"https://{host}:8006/api2/json"
        self.node = node
        self.user = user
        self.password = password
        self.ticket, self.csrf = None, None

    def authenticate(self):
        try:
            url = f"{self.base_url}/access/ticket"
            data = {"username": self.user, "password": self.password}
            res = requests.post(url, data=data, verify=False, timeout=3)
            if res.status_code == 200:
                d = res.json()["data"]
                self.ticket, self.csrf = d["ticket"], d["CSRFPreventionToken"]
                return True
        except Exception:
            pass
        return False

    def get_stats(self):
        if not self.ticket and not self.authenticate():
            return None
        
        try:
            headers = {"CSRFPreventionToken": self.csrf}
            cookies = {"PVEAuthCookie": self.ticket}

            # Node Status
            res = requests.get(f"{self.base_url}/nodes/{self.node}/status", headers=headers, cookies=cookies, verify=False, timeout=3)
            if res.status_code == 401 and self.authenticate():
                return self.get_stats()
            
            # Active VMs / LXC Containers
            res_vms = requests.get(f"{self.base_url}/nodes/{self.node}/qemu", headers=headers, cookies=cookies, verify=False, timeout=3)
            res_lxc = requests.get(f"{self.base_url}/nodes/{self.node}/lxc", headers=headers, cookies=cookies, verify=False, timeout=3)
            
            if res.status_code == 200:
                d = res.json()["data"]
                cpu = round(d["cpu"] * 100, 1)
                ram_u = round(d["memory"]["used"] / (1024**3), 1)
                ram_t = round(d["memory"]["total"] / (1024**3), 1)
                disk_pct = round((d["rootfs"]["used"] / d["rootfs"]["total"]) * 100, 1)

                active_vms = 0
                if res_vms.status_code == 200:
                    active_vms += sum(1 for vm in res_vms.json()["data"] if vm.get("status") == "running")
                if res_lxc.status_code == 200:
                    active_vms += sum(1 for lxc in res_lxc.json()["data"] if lxc.get("status") == "running")

                return {
                    "node": self.node,
                    "cpu": cpu,
                    "ram_used": ram_u,
                    "ram_total": ram_t,
                    "disk_pct": disk_pct,
                    "active_vms": active_vms
                }
        except Exception:
            pass
        return None