import requests
import urllib3

urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

class ProxmoxMonitor:
    def __init__(self, host, node, user, password):
        self.base_url = f"https://{host}:8006/api2/json"
        self.node = node
        self.user = user
        self.password = password
        self.ticket = None
        self.csrf = None

    def authenticate(self):
        try:
            url = f"{self.base_url}/access/ticket"
            data = {"username": self.user, "password": self.password}
            res = requests.post(url, data=data, verify=False, timeout=3)
            if res.status_code == 200:
                d = res.json()["data"]
                self.ticket = d["ticket"]
                self.csrf = d["CSRFPreventionToken"]
                return True
        except Exception:
            pass
        return False

    def get_stats(self):
        if not self.ticket:
            if not self.authenticate():
                return None
        
        try:
            url = f"{self.base_url}/nodes/{self.node}/status"
            headers = {"CSRFPreventionToken": self.csrf}
            cookies = {"PVEAuthCookie": self.ticket}
            res = requests.get(url, headers=headers, cookies=cookies, verify=False, timeout=3)
            
            if res.status_code == 401:  # Token expired
                if self.authenticate():
                    return self.get_stats()
                return None
                
            if res.status_code == 200:
                data = res.json()["data"]
                cpu = round(data["cpu"] * 100, 1)
                ram_u = round(data["memory"]["used"] / (1024**3), 1)
                ram_t = round(data["memory"]["total"] / (1024**3), 1)
                return {"cpu": cpu, "ram_used": ram_u, "ram_total": ram_t}
        except Exception:
            pass
        return None