#!/usr/bin/env python3
"""
Phase 5B
- LCD: Load average + Top VM pages
- Auto-discover nodes (scan /24 :8006)
- Guest/read-only mode when PIN not unlocked (Option B)
- Software buzzer pattern library (info / warn / critical)
"""

import re
import shutil
from pathlib import Path

ROOT = Path(__file__).parent

def backup(p: Path):
    bak = p.with_suffix(p.suffix + ".phase5b.bak")
    if not bak.exists():
        shutil.copy2(p, bak)
        print(f"  backup → {bak.name}")

# ─────────────────────────────────────────────
# hardware.py – buzzer pattern library
# ─────────────────────────────────────────────
def patch_hardware():
    p = ROOT / "hardware.py"
    if not p.exists(): return
    backup(p)
    text = p.read_text(encoding="utf-8")

    if "def pattern(" not in text:
        patterns = '''
    def pattern(self, name: str = "info"):
        """Software patterns on passive buzzer: info / warn / critical."""
        if not self.passive_buzzer_enabled or self.standalone:
            return
        try:
            if name == "info":
                self._tone(2.5, 0.12)
            elif name == "warn":
                for _ in range(2):
                    self._tone(2.0, 0.15)
                    time.sleep(0.08)
            elif name == "critical":
                for ms in (1.8, 1.5, 1.2):
                    self._tone(ms, 0.18)
                    time.sleep(0.06)
                time.sleep(0.1)
                self._tone(1.4, 0.4)
            else:
                self._tone(2.2, 0.1)
        except Exception:
            pass
'''
        text = text.replace(
            "def alert_tone(self):",
            patterns + "\n    def alert_tone(self):"
        )
        # make alert_tone use critical
        text = text.replace(
            '"""Clear 3-beep alarm pattern (not just clicks)."""',
            '"""Clear 3-beep alarm pattern (not just clicks)."""\n'
            '        self.pattern("critical")\n'
            '        return  # patterns handle it'
        )
        print("  hardware → pattern library")
        p.write_text(text, encoding="utf-8")

# ─────────────────────────────────────────────
# main.py – Load avg + Top VM LCD pages
# ─────────────────────────────────────────────
def patch_main():
    p = ROOT / "main.py"
    if not p.exists(): return
    backup(p)
    text = p.read_text(encoding="utf-8")

    # Expand TOTAL pages and add load / topvm
    if "TOP VM" not in text:
        # bump TOTAL if needed
        text = text.replace("TOTAL = 5", "TOTAL = 7")
        # replace the page display block endings to include new pages
        # We inject after page 3 (uptime)
        new_pages = '''
                        elif page == 4:
                            # Load average (1m if available, else placeholder)
                            load1 = metrics.get("load1") or metrics.get("loadavg") or 0
                            hw.display(f"{'LOAD 1m':^16}", f"{float(load1):^16.2f}")
                        elif page == 5:
                            # Top VM by CPU (best-effort from last poll)
                            top = metrics.get("top_vm") or "—"
                            hw.display(f"{'TOP VM':^16}", f"{str(top)[:16]:^16}")
                        elif page == 6:
                            pi = get_pi_ip() if "get_pi_ip" in dir() else (metrics.get("ip") or "?")
                            hw.display(f"{'THIS PI':^16}", f"{str(pi)[:16]:^16}")
                        else:
                            ip = (metrics.get("ip") or "?")[:16]
                            hw.display(f"{ip:^16}", f"{(metrics.get('type') or 'server'):^16}")
'''
        # try to replace the final else block
        text = re.sub(
            r'else:\s+# page 4 = Pi itself.*?hw\.display\(f"\{pi:\^16\}".*?\)',
            new_pages.strip(),
            text,
            count=1,
            flags=re.DOTALL
        )
        # fallback if previous pattern different
        if "TOP VM" not in text:
            text = re.sub(
                r'else:\s+ip = \(metrics\.get\("ip"\).*?hw\.display\(f"\{ip:\^16\}".*?\)',
                new_pages.strip(),
                text,
                count=1,
                flags=re.DOTALL
            )
        print("  main → Load avg + Top VM pages")
        p.write_text(text, encoding="utf-8")

# ─────────────────────────────────────────────
# monitor.py – try to expose load1 + top_vm
# ─────────────────────────────────────────────
def patch_monitor():
    p = ROOT / "monitor.py"
    if not p.exists(): return
    backup(p)
    text = p.read_text(encoding="utf-8")

    if '"load1"' not in text:
        # after cpu/ram extraction, add load if present
        text = text.replace(
            'cpu = round(float(d["cpu"]) * 100, 1)',
            'cpu = round(float(d["cpu"]) * 100, 1)\n'
            '            load1 = round(float(d.get("loadavg", 0) or d.get("wait", 0) or 0), 2)'
        )
        text = text.replace(
            '"cpu": cpu, "ram_used": ram_u, "ram_total": ram_t,',
            '"cpu": cpu, "ram_used": ram_u, "ram_total": ram_t,\n'
            '                "load1": load1,'
        )
        # top VM – after guests loop
        text = text.replace(
            'active += sum(1 for g in rr.json().get("data", []) if g.get("status") == "running")',
            'guests = rr.json().get("data", [])\n'
            '                    active += sum(1 for g in guests if g.get("status") == "running")\n'
            '                    # track top by cpu\n'
            '                    for g in guests:\n'
            '                        if g.get("status") == "running":\n'
            '                            c = float(g.get("cpu", 0) or 0)\n'
            '                            if c > getattr(self, "_top_cpu", -1):\n'
            '                                self._top_cpu = c\n'
            '                                self._top_name = (g.get("name") or str(g.get("vmid")))[:14]'
        )
        text = text.replace(
            '"uptime": uptime, "online": True,',
            '"uptime": uptime, "online": True,\n'
            '                "top_vm": getattr(self, "_top_name", "—"),'
        )
        # reset top each poll
        text = text.replace(
            "def get_stats(self)",
            "def get_stats(self):\n"
            "        self._top_cpu = -1\n"
            "        self._top_name = '—'"
        )
        # fix the def line if broken
        text = text.replace(
            "def get_stats(self):\n        self._top_cpu = -1\n        self._top_name = '—':",
            "def get_stats(self) -> Optional[Dict[str, Any]]:"
        )
        print("  monitor → load1 + top_vm best-effort")
        p.write_text(text, encoding="utf-8")

# ─────────────────────────────────────────────
# app.py – auto-discover + guest mode (Option B)
# ─────────────────────────────────────────────
def patch_app():
    p = ROOT / "app.py"
    if not p.exists():
        print("app.py not found"); return
    backup(p)
    text = p.read_text(encoding="utf-8")

    # 1. Auto-discover endpoint
    if "/api/discover" not in text:
        disc_ep = '''
@app.get("/api/discover")
def api_discover():
    """Scan local /24 for open Proxmox ports (8006). Best-effort, may take a few seconds."""
    import socket
    from concurrent.futures import ThreadPoolExecutor, as_completed
    found = []
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.connect(("8.8.8.8", 80))
        my_ip = s.getsockname()[0]
        s.close()
        base = ".".join(my_ip.split(".")[:3]) + "."
    except Exception:
        return {"ok": False, "nodes": [], "error": "no local ip"}
    def check(i):
        ip = base + str(i)
        try:
            sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            sock.settimeout(0.35)
            r = sock.connect_ex((ip, 8006))
            sock.close()
            if r == 0:
                return ip
        except Exception:
            pass
        return None
    with ThreadPoolExecutor(max_workers=40) as ex:
        futs = [ex.submit(check, i) for i in range(1, 255)]
        for f in as_completed(futs):
            ip = f.result()
            if ip:
                found.append({"ip": ip, "name": "pve-" + ip.split(".")[-1]})
    return {"ok": True, "nodes": found}
'''
        text = text.replace(
            '@app.get("/api/health")',
            disc_ep + '\n@app.get("/api/health")'
        )
        print("  app → /api/discover")

    # 2. Discover button + modal in UI
    if "openDiscover" not in text:
        disc_js = '''
async function openDiscover() {
  toast('Scanning LAN for :8006 …');
  const r = await fetch('/api/discover').then(x => x.json());
  if (!r.ok) { toast(r.error || 'Scan failed'); return; }
  if (!r.nodes.length) { toast('No Proxmox hosts found'); return; }
  const list = r.nodes.map(n =>
    `<label style="display:flex;gap:8px;padding:6px 0;border-bottom:1px solid var(--border)">
      <input type="checkbox" value="${n.ip}" data-name="${n.name}"> ${n.ip}
    </label>`
  ).join('');
  const html = `<div id="discModal" class="modal open"><div class="modal-box" style="max-width:360px">
    <h3>Discovered hosts</h3><div style="max-height:240px;overflow:auto">${list}</div>
    <div style="display:flex;gap:8px;margin-top:12px">
      <button class="btn btn-primary" onclick="importDiscovered()">Add selected</button>
      <button class="btn btn-ghost" onclick="document.getElementById('discModal').remove()">Cancel</button>
    </div></div></div>`;
  document.body.insertAdjacentHTML('beforeend', html);
}
async function importDiscovered() {
  const boxes = [...document.querySelectorAll('#discModal input:checked')];
  if (!boxes.length) { toast('Select at least one'); return; }
  const nodes = boxes.map(b => ({name: b.dataset.name, ip: b.value, node: 'pve', user: 'root@pam', password: ''}));
  const r = await fetch('/api/nodes/import', {method:'POST', headers:{'Content-Type':'application/json'},
    body: JSON.stringify({nodes})});
  const j = await r.json();
  toast(j.ok ? ('Added ' + j.added) : (j.error || 'Failed'));
  document.getElementById('discModal')?.remove();
  load();
}
'''
        text = text.replace("function openImport()", disc_js + "\nfunction openImport()")
        # add Discover button near Import if possible
        text = re.sub(
            r'(onclick="openImport\(\)"[^>]*>)',
            r'\1 Discover</button><button class="btn btn-ghost" style="width:auto;padding:4px 10px;font-size:.75rem" onclick="openDiscover()">',
            text,
            count=1
        )
        print("  app → discover UI")

    # 3. Guest / read-only mode (Option B)
    if "applyGuestMode" not in text:
        guest_js = '''
function applyGuestMode() {
  const unlocked = sessionStorage.getItem('pve_unlocked') === '1';
  const hasPin = !!(window.uiPin);
  // if PIN is set and not unlocked → guest
  const guest = hasPin && !unlocked;
  document.body.classList.toggle('guest-mode', guest);
  // hide power / settings / add / bulk / delete controls
  document.querySelectorAll('.btn-reboot, .btn-shutdown, #bottomNav, [onclick*="openSettings"], [onclick*="openAddModal"], [onclick*="openBulk"], [onclick*="deleteNode"], [onclick*="power("]').forEach(el => {
    if (guest) el.style.display = 'none';
    else el.style.display = '';
  });
  // keep graphs + cards visible
}
'''
        text = text.replace("function applyPinLock()", guest_js + "\nfunction applyPinLock()")
        text = text.replace(
            "applyPinLock();",
            "applyPinLock();\n  applyGuestMode();"
        )
        # also call after successful unlock
        text = text.replace(
            "document.getElementById('pinOverlay').style.display = 'none';",
            "document.getElementById('pinOverlay').style.display = 'none';\n    applyGuestMode();"
        )
        # CSS
        text = text.replace(
            "</style>",
            """
body.guest-mode .actions button:not(.btn-icon),
body.guest-mode .btn-reboot,
body.guest-mode .btn-shutdown { display: none !important; }
body.guest-mode #bottomNav { display: none !important; }
</style>"""
        )
        print("  app → guest/read-only mode (Option B)")

    p.write_text(text, encoding="utf-8")
    print("  app.py written")


if __name__ == "__main__":
    print("Phase 5B starting…")
    patch_hardware()
    patch_main()
    patch_monitor()
    patch_app()
    print("""
Done. Restart the app.

5B adds:
  • LCD pages: Load 1m + Top VM + Pi IP
  • Auto-discover (button + /api/discover)
  • Guest mode: when PIN is set and not unlocked →
    stats/graphs only, no reboot/shutdown/settings
  • Buzzer patterns: info / warn / critical

Admin flow:
  1. Set a PIN in Settings
  2. Refresh → PIN lock appears
  3. Guests see read-only view
  4. Correct PIN → full control
""")
