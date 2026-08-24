#!/usr/bin/env python3
"""
Phase 4 – final QoL
- Simple PIN lock
- Cinema mode (fullscreen graphs)
- Theme packs (cyberpunk / terminal / nord)
- Layout presets (work / night / minimal)
- PWA manifest + basic mobile bottom nav
- Version + changelog in footer
- Copy diagnostics button
- QR code that opens the UI
- 12h / 24h clock preference
"""

import re
import shutil
from pathlib import Path

ROOT = Path(__file__).parent

def backup(p: Path):
    bak = p.with_suffix(p.suffix + ".phase4.bak")
    if not bak.exists():
        shutil.copy2(p, bak)
        print(f"  backup → {bak.name}")

def patch_config():
    p = ROOT / "config.py"
    if not p.exists(): return
    backup(p)
    text = p.read_text(encoding="utf-8")
    extras = {
        '"ui_pin"': '    "ui_pin": "",              # empty = disabled\n',
        '"clock_12h"': '    "clock_12h": False,\n',
        '"theme_pack"': '    "theme_pack": "default",  # default|cyberpunk|terminal|nord\n',
        '"layout_preset"': '    "layout_preset": "work",  # work|night|minimal\n',
    }
    for k, line in extras.items():
        if k not in text:
            text = text.replace('    "theme": "dark",', line + '    "theme": "dark",')
            print(f"  config → {k}")
    p.write_text(text, encoding="utf-8")

def patch_app():
    p = ROOT / "app.py"
    if not p.exists():
        print("app.py not found"); return
    backup(p)
    text = p.read_text(encoding="utf-8")

    # ── 1. Theme packs CSS ──────────────────────────────────────
    themes = """
/* Theme packs */
body.theme-cyberpunk { --accent:#ff2a6d; --purple:#05d9e8; --bg:#0d0221; --card:#1a0a2e; }
body.theme-terminal  { --accent:#33ff33; --purple:#33ff99; --bg:#0a0a0a; --card:#111; color:#33ff33; }
body.theme-nord      { --accent:#88c0d0; --purple:#b48ead; --bg:#2e3440; --card:#3b4252; }
body.cinema .topbar, body.cinema .side, body.cinema #nodesCol { display:none !important; }
body.cinema .graphs { position:fixed; inset:0; z-index:50; background:var(--bg); padding:12px; }
"""
    if "theme-cyberpunk" not in text:
        text = text.replace("</style>", themes + "\n</style>")
        print("  + theme packs + cinema CSS")

    # ── 2. PIN lock overlay ─────────────────────────────────────
    if "pinOverlay" not in text:
        pin_html = """
<div id="pinOverlay" style="display:none;position:fixed;inset:0;background:rgba(0,0,0,.85);z-index:9999;
  align-items:center;justify-content:center;flex-direction:column;gap:12px">
  <div style="font-size:1.1rem;opacity:.8">Enter PIN</div>
  <input id="pinInput" type="password" maxlength="8" style="font-size:1.4rem;text-align:center;width:140px;
    padding:10px;border-radius:8px;border:1px solid var(--border);background:var(--card);color:inherit"
    onkeydown="if(event.key==='Enter')checkPin()">
  <button class="btn btn-primary" onclick="checkPin()">Unlock</button>
  <div id="pinErr" style="color:#f87171;font-size:.85rem;display:none">Wrong PIN</div>
</div>
"""
        text = text.replace("</body>", pin_html + "\n</body>")

        pin_js = """
let uiPin = '';
function applyPinLock() {
  if (!uiPin) return;
  const unlocked = sessionStorage.getItem('pve_unlocked') === '1';
  document.getElementById('pinOverlay').style.display = unlocked ? 'none' : 'flex';
}
function checkPin() {
  const v = document.getElementById('pinInput').value;
  if (v === uiPin) {
    sessionStorage.setItem('pve_unlocked', '1');
    document.getElementById('pinOverlay').style.display = 'none';
    document.getElementById('pinErr').style.display = 'none';
  } else {
    document.getElementById('pinErr').style.display = 'block';
  }
}
"""
        text = text.replace("async function loadSettings()", pin_js + "\nasync function loadSettings()")
        # after settings loaded
        text = text.replace(
            "applyDensity(settings.density)",
            "applyDensity(settings.density);\n"
            "  uiPin = settings.ui_pin || '';\n"
            "  applyPinLock();\n"
            "  applyThemePack(settings.theme_pack || 'default');\n"
            "  applyLayoutPreset(settings.layout_preset || 'work');\n"
            "  if (settings.clock_12h) window.clock12h = true;"
        )
        print("  + PIN lock")

    # ── 3. Theme + layout + cinema controls in settings ─────────
    if "sThemePack" not in text:
        extra_settings = """
<label>Theme pack</label>
<select id="sThemePack" style="width:100%;margin-bottom:8px">
  <option value="default">Default</option>
  <option value="cyberpunk">Cyberpunk</option>
  <option value="terminal">Terminal green</option>
  <option value="nord">Nord</option>
</select>
<label>Layout preset</label>
<select id="sLayout" style="width:100%;margin-bottom:8px">
  <option value="work">Work</option>
  <option value="night">Night</option>
  <option value="minimal">Minimal</option>
</select>
<label>UI PIN (blank = off)</label>
<input id="sPin" type="password" maxlength="8" placeholder="e.g. 1234" style="width:100%;margin-bottom:8px">
<label><input type="checkbox" id="sClock12"> 12-hour clock</label>
"""
        text = re.sub(
            r'(id="sDensity"[^>]*>.*?</select>)',
            r'\1\n' + extra_settings,
            text,
            count=1,
            flags=re.DOTALL
        )
        print("  + theme/layout/pin settings fields")

    # save them
    if "settings.theme_pack" not in text:
        text = text.replace(
            "settings.density=sDensity.value;",
            "settings.density=sDensity.value;\n"
            "if(document.getElementById('sThemePack'))settings.theme_pack=sThemePack.value;\n"
            "if(document.getElementById('sLayout'))settings.layout_preset=sLayout.value;\n"
            "if(document.getElementById('sPin'))settings.ui_pin=sPin.value;\n"
            "if(document.getElementById('sClock12'))settings.clock_12h=sClock12.checked;"
        )
        text = text.replace(
            "if(document.getElementById('sDensity'))sDensity.value=settings.density||'comfortable';",
            "if(document.getElementById('sDensity'))sDensity.value=settings.density||'comfortable';\n"
            "if(document.getElementById('sThemePack'))sThemePack.value=settings.theme_pack||'default';\n"
            "if(document.getElementById('sLayout'))sLayout.value=settings.layout_preset||'work';\n"
            "if(document.getElementById('sPin'))sPin.value=settings.ui_pin||'';\n"
            "if(document.getElementById('sClock12'))sClock12.checked=!!settings.clock_12h;"
        )
        print("  + theme/layout/pin save/load")

    # apply helpers
    if "function applyThemePack" not in text:
        helpers = """
function applyThemePack(name) {
  document.body.classList.remove('theme-cyberpunk','theme-terminal','theme-nord');
  if (name && name !== 'default') document.body.classList.add('theme-' + name);
}
function applyLayoutPreset(name) {
  document.body.classList.remove('layout-work','layout-night','layout-minimal');
  document.body.classList.add('layout-' + (name || 'work'));
  // minimal hides side cards
  const side = document.querySelector('.side');
  if (side) side.style.display = name === 'minimal' ? 'none' : '';
}
function toggleCinema() {
  document.body.classList.toggle('cinema');
  toast(document.body.classList.contains('cinema') ? 'Cinema mode' : 'Normal');
}
"""
        text = text.replace("function applyDensity(d)", helpers + "\nfunction applyDensity(d)")
        print("  + applyThemePack / cinema")

    # ── 4. Cinema mode keyboard shortcut (C) ────────────────────
    if "key === 'c'" not in text and "k === 'c'" not in text:
        text = text.replace(
            "if (k === 'a') { e.preventDefault(); openAddModal(); }",
            "if (k === 'a') { e.preventDefault(); openAddModal(); }\n"
            "  if (k === 'c') { e.preventDefault(); toggleCinema(); }"
        )
        print("  + cinema shortcut C")

    # ── 5. Version + diagnostics + QR in footer area ────────────
    if "copyDiagnostics" not in text:
        footer = """
<div style="text-align:center;padding:16px 8px 24px;opacity:.55;font-size:.75rem">
  PVE Node Monitor <span id="verLabel">v1.9.0</span> ·
  <a href="#" onclick="copyDiagnostics();return false" style="color:var(--accent)">Copy diagnostics</a> ·
  <a href="#" onclick="showQr();return false" style="color:var(--accent)">QR</a>
</div>
<div id="qrModal" class="modal">
  <div class="modal-box" style="text-align:center;max-width:280px">
    <h3>Open on phone</h3>
    <div id="qrBox" style="margin:12px auto"></div>
    <button class="btn btn-ghost" onclick="document.getElementById('qrModal').classList.remove('open')">Close</button>
  </div>
</div>
"""
        text = text.replace("</body>", footer + "\n</body>")

        diag_js = """
async function copyDiagnostics() {
  const [health, settings] = await Promise.all([
    fetch('/api/health').then(r=>r.json()).catch(()=>({})),
    fetch('/api/settings').then(r=>r.json()).catch(()=>({}))
  ]);
  const safe = {...settings};
  // already no passwords in /api/settings
  const txt = JSON.stringify({health, settings: safe, ua: navigator.userAgent, ts: new Date().toISOString()}, null, 2);
  await navigator.clipboard.writeText(txt);
  toast('Diagnostics copied');
}
function showQr() {
  const url = location.origin;
  // simple QR via external API (no dependency)
  const img = document.createElement('img');
  img.src = 'https://api.qrserver.com/v1/create-qr-code/?size=180x180&data=' + encodeURIComponent(url);
  img.alt = 'QR';
  const box = document.getElementById('qrBox');
  box.innerHTML = '';
  box.appendChild(img);
  document.getElementById('qrModal').classList.add('open');
}
"""
        text = text.replace("function applyDensity(d)", diag_js + "\nfunction applyDensity(d)")
        print("  + version footer, diagnostics, QR")

    # ── 6. Basic PWA manifest link ──────────────────────────────
    if 'rel="manifest"' not in text:
        text = text.replace(
            "<head>",
            """<head>
<link rel="manifest" href="/manifest.json">
<meta name="theme-color" content="#0f172a">
<meta name="apple-mobile-web-app-capable" content="yes">
"""
        )
        print("  + PWA meta")

    # ── 7. Tiny mobile bottom nav ───────────────────────────────
    if "bottomNav" not in text:
        nav = """
<nav id="bottomNav" style="display:none;position:fixed;bottom:0;left:0;right:0;background:var(--card);
  border-top:1px solid var(--border);padding:8px 0;z-index:40;justify-content:space-around">
  <button class="btn btn-ghost" style="flex:1;font-size:.7rem" onclick="window.scrollTo(0,0)">Nodes</button>
  <button class="btn btn-ghost" style="flex:1;font-size:.7rem" onclick="openSettings()">Settings</button>
  <button class="btn btn-ghost" style="flex:1;font-size:.7rem" onclick="openAddModal()">Add</button>
  <button class="btn btn-ghost" style="flex:1;font-size:.7rem" onclick="toggleCinema()">Cinema</button>
</nav>
"""
        text = text.replace("</body>", nav + "\n</body>")
        # show on small screens
        text = text.replace(
            "@media (max-width: 780px) {",
            """@media (max-width: 780px) {
  #bottomNav { display: flex !important; }
  body { padding-bottom: 56px; }
"""
        )
        print("  + mobile bottom nav")

    # ── 8. Serve a minimal manifest ─────────────────────────────
    if "/manifest.json" not in text:
        manifest_ep = '''
@app.get("/manifest.json")
def manifest():
    return {
        "name": "PVE Node Monitor",
        "short_name": "PVE Mon",
        "start_url": "/",
        "display": "standalone",
        "background_color": "#0f172a",
        "theme_color": "#0f172a",
        "icons": []
    }
'''
        text = text.replace(
            '@app.get("/api/health")',
            manifest_ep + '\n@app.get("/api/health")'
        )
        print("  + /manifest.json")

    p.write_text(text, encoding="utf-8")
    print("  app.py written")


if __name__ == "__main__":
    print("Phase 4 patch starting…")
    patch_config()
    patch_app()
    print("\nDone. Restart the app.")
    print("Backups end with .phase4.bak")
    print("""
Quick keys after Phase 4:
  R  = refresh
  S  = settings
  A  = add node
  C  = cinema mode
""")
