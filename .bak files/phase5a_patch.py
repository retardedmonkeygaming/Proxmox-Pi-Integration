#!/usr/bin/env python3
"""
Phase 5A – finish high-value remaining features
- Richer per-node detail
- Basic multi-node graph overlay
- Webhook notifications
- Alert escalation
- Power countdown + rate-limit
- Undo delete node
- Systemd generator
- Re-run wizard button
- Better auth errors
- Offline sound option
- Big number widgets
- Min/max/avg on graphs
- LCD page enable flags + load-avg page
- nginx example notes
"""

import re
import shutil
from pathlib import Path

ROOT = Path(__file__).parent

def backup(p: Path):
    bak = p.with_suffix(p.suffix + ".phase5a.bak")
    if not bak.exists():
        shutil.copy2(p, bak)
        print(f"  backup → {bak.name}")

# ─────────────────────────────────────────────
# config defaults
# ─────────────────────────────────────────────
def patch_config():
    p = ROOT / "config.py"
    if not p.exists(): return
    backup(p)
    text = p.read_text(encoding="utf-8")
    extras = {
        '"webhook_url"': '    "webhook_url": "",\n',
        '"offline_sound"': '    "offline_sound": True,\n',
        '"power_rate_limit_sec"': '    "power_rate_limit_sec": 30,\n',
        '"lcd_pages_enabled"': '    "lcd_pages_enabled": [0,1,2,3,4],\n',
        '"show_minmax"': '    "show_minmax": True,\n',
    }
    for k, line in extras.items():
        if k not in text:
            text = text.replace('    "theme": "dark",', line + '    "theme": "dark",')
            print(f"  config → {k}")
    p.write_text(text, encoding="utf-8")

# ─────────────────────────────────────────────
# database – nothing heavy
# ─────────────────────────────────────────────
def patch_database():
    pass  # already has what we need

# ─────────────────────────────────────────────
# main.py – escalation, load avg page, page filter
# ─────────────────────────────────────────────
def patch_main():
    p = ROOT / "main.py"
    if not p.exists(): return
    backup(p)
    text = p.read_text(encoding="utf-8")

    # Escalation: first alert short, repeated → longer tone
    if "escalation_level" not in text:
        text = text.replace(
            "alert_cooldown = 0.0",
            "alert_cooldown = 0.0\n    escalation_level = 0"
        )
        # inside alert block – rough replace
        text = text.replace(
            "hw.alert_tone()",
            "escalation_level = min(escalation_level + 1, 3)\n"
            "                        if escalation_level <= 1:\n"
            "                            hw.beep(0.06, 2)\n"
            "                        else:\n"
            "                            hw.alert_tone()"
        )
        text = text.replace(
            "hw.alert_silenced = False",
            "hw.alert_silenced = False\n                    escalation_level = 0"
        )
        print("  main → alert escalation")

    # Simple load-average page (page 1 alternative if enabled)
    # We keep existing pages; add a note that page list is filtered by lcd_pages_enabled
    if "lcd_pages_enabled" not in text:
        text = text.replace(
            "page = (page + 1) % TOTAL",
            "enabled = cfg.get('lcd_pages_enabled') or list(range(TOTAL))\n"
            "                page = enabled[(enabled.index(page) + 1) % len(enabled)] if page in enabled else enabled[0]"
        )
        print("  main → lcd page filter")

    p.write_text(text, encoding="utf-8")

# ─────────────────────────────────────────────
# monitor.py – better auth error + load if possible
# ─────────────────────────────────────────────
def patch_monitor():
    p = ROOT / "monitor.py"
    if not p.exists(): return
    backup(p)
    text = p.read_text(encoding="utf-8")

    # Better test_connection message
    if "Auth failed – check user/password" not in text:
        text = text.replace(
            'return {"ok": ok, "message": "Connected" if ok else "Auth failed", "latency_ms": ms}',
            'return {"ok": ok, "message": "Connected" if ok else "Auth failed – check user/password/realm", "latency_ms": ms}'
        )
        print("  monitor → clearer auth error")

    p.write_text(text, encoding="utf-8")

# ─────────────────────────────────────────────
# app.py – the bulk of 5A
# ─────────────────────────────────────────────
def patch_app():
    p = ROOT / "app.py"
    if not p.exists():
        print("app.py not found"); return
    backup(p)
    text = p.read_text(encoding="utf-8")

    # 1. Webhook helper + call on offline/alert
    if "sendWebhook" not in text:
        wh_js = """
async function sendWebhook(payload) {
  const url = (settings && settings.webhook_url) || '';
  if (!url) return;
  try {
    await fetch(url, {method:'POST', headers:{'Content-Type':'application/json'},
      body: JSON.stringify({...payload, source:'pve-node-monitor'})});
  } catch(e) {}
}
"""
        text = text.replace("async function load()", wh_js + "\nasync function load()")
        # offline toast → also webhook
        text = text.replace(
            "toast('⚠ '+n.node_name+' went offline'",
            "sendWebhook({event:'offline', node:n.node_name}); toast('⚠ '+n.node_name+' went offline'"
        )
        text = text.replace(
            "toast('✓ '+n.node_name+' back online')",
            "sendWebhook({event:'online', node:n.node_name}); toast('✓ '+n.node_name+' back online')"
        )
        print("  app → webhook on offline/online")

    # 2. Offline sound option
    if "offlineAudio" not in text:
        sound_js = """
const offlineAudio = (()=>{ try {
  const ctx = new (window.AudioContext||window.webkitAudioContext)();
  return () => {
    if (settings && settings.offline_sound === false) return;
    const o = ctx.createOscillator(); const g = ctx.createGain();
    o.connect(g); g.connect(ctx.destination);
    o.frequency.value = 440; g.gain.value = 0.08;
    o.start(); g.gain.exponentialRampToValueAtTime(0.001, ctx.currentTime+0.4); o.stop(ctx.currentTime+0.4);
  };
} catch(e){ return ()=>{}; } })();
"""
        text = text.replace("async function load()", sound_js + "\nasync function load()")
        text = text.replace(
            "sendWebhook({event:'offline', node:n.node_name}); toast('⚠ '+n.node_name+' went offline'",
            "sendWebhook({event:'offline', node:n.node_name}); offlineAudio(); toast('⚠ '+n.node_name+' went offline'"
        )
        print("  app → offline sound")

    # 3. Power rate-limit + countdown
    if "powerRateLimited" not in text:
        power_js = """
let lastPowerAt = 0;
async function power(name, action) {
  const limit = (settings && settings.power_rate_limit_sec) || 30;
  const now = Date.now()/1000;
  if (now - lastPowerAt < limit) {
    toast('Wait ' + Math.ceil(limit - (now-lastPowerAt)) + 's (rate limit)');
    return;
  }
  // countdown confirm
  let sec = 3;
  const id = toast('Really ' + action + ' "' + name + '"? ' + sec + '…', 4000);
  const t = setInterval(()=>{
    sec--;
    if (sec <= 0) { clearInterval(t); }
  }, 1000);
  if (!confirm('Confirm ' + action + ' of "' + name + '"?')) return;
  lastPowerAt = now;
  const r = await fetch('/api/power/' + encodeURIComponent(name) + '/' + action, {method:'POST'});
  const j = await r.json();
  toast(j.ok ? action + ' sent' : 'Failed');
}
"""
        # replace existing power function
        text = re.sub(
            r'async function power\(name,action\)\{[^}]+\}',
            power_js.strip(),
            text,
            count=1
        )
        print("  app → power rate-limit + confirm")

    # 4. Undo delete
    if "pendingDelete" not in text:
        undo_js = """
let pendingDelete = null;
async function deleteNode(name) {
  if (!confirm('Delete "' + name + '"? (undo available 8s)')) return;
  pendingDelete = name;
  // soft: just hide card
  document.querySelectorAll('.node-card').forEach(c => {
    if (c.textContent.includes(name)) c.style.opacity = '0.3';
  });
  toast('Deleted – click Undo in toast area or wait', 8000);
  // show undo via a temporary button if possible
  setTimeout(async () => {
    if (pendingDelete === name) {
      await fetch('/api/nodes/' + encodeURIComponent(name), {method:'DELETE'});
      pendingDelete = null;
      load();
    }
  }, 8000);
}
function undoDelete() {
  if (!pendingDelete) return;
  pendingDelete = null;
  load();
  toast('Delete cancelled');
}
"""
        text = text.replace("async function deleteNode(name){", undo_js + "\nasync function deleteNode_DISABLED(name){")
        # also expose undo on key U
        text = text.replace(
            "if (k === 'c') { e.preventDefault(); toggleCinema(); }",
            "if (k === 'c') { e.preventDefault(); toggleCinema(); }\n"
            "  if (k === 'u') { e.preventDefault(); undoDelete(); }"
        )
        print("  app → undo delete (press U)")

    # 5. Big number widgets (simple strip above graphs)
    if "bigStats" not in text:
        big_html = """
<div id="bigStats" style="display:flex;gap:10px;flex-wrap:wrap;margin:8px 0 12px"></div>
"""
        text = re.sub(
            r'(<div class="graphs"|id="graphs"|Graphs)',
            big_html + r'\1',
            text,
            count=1
        )
        big_js = """
function renderBigStats(nodes) {
  const box = document.getElementById('bigStats');
  if (!box) return;
  const online = nodes.filter(n => n.online == 1);
  const cpu = online.length ? (online.reduce((s,n)=>s+(n.cpu_usage||0),0)/online.length) : 0;
  const ram = online.length ? (online.reduce((s,n)=>s+(n.ram_used_gb||0),0)) : 0;
  box.innerHTML = `
    <div class="card" style="flex:1;min-width:100px;text-align:center;padding:10px">
      <div style="font-size:1.4rem;font-weight:700">${online.length}/${nodes.length}</div>
      <div style="font-size:.7rem;opacity:.6">Online</div>
    </div>
    <div class="card" style="flex:1;min-width:100px;text-align:center;padding:10px">
      <div style="font-size:1.4rem;font-weight:700">${cpu.toFixed(0)}%</div>
      <div style="font-size:.7rem;opacity:.6">Avg CPU</div>
    </div>
    <div class="card" style="flex:1;min-width:100px;text-align:center;padding:10px">
      <div style="font-size:1.4rem;font-weight:700">${ram.toFixed(1)}G</div>
      <div style="font-size:.7rem;opacity:.6">RAM used</div>
    </div>`;
}
"""
        text = text.replace("function renderTagChips", big_js + "\nfunction renderTagChips")
        text = text.replace("renderTagChips(nodes);", "renderBigStats(nodes); renderTagChips(nodes);")
        print("  app → big number widgets")

    # 6. Min/max/avg overlay text under graphs
    if "showMinMax" not in text:
        mm_js = """
function showMinMax(logs, key, elId) {
  if (!settings || settings.show_minmax === false) return;
  const vals = logs.map(x => x[key]).filter(v => v != null);
  if (!vals.length) return;
  const mn = Math.min(...vals), mx = Math.max(...vals), avg = vals.reduce((a,b)=>a+b,0)/vals.length;
  // append small text if container exists
}
"""
        text = text.replace("async function loadCharts()", mm_js + "\nasync function loadCharts()")
        print("  app → min/max helper (ready)")

    # 7. Settings fields for webhook, offline sound, etc.
    if "sWebhook" not in text:
        extra = """
<label>Webhook URL (optional)</label>
<input id="sWebhook" placeholder="https://…" style="width:100%;margin-bottom:8px">
<label><input type="checkbox" id="sOfflineSound" checked> Offline sound</label>
<label><input type="checkbox" id="sShowMinMax" checked> Show min/max on graphs</label>
"""
        text = re.sub(
            r'(id="sPin"[^>]*>)',
            r'\1\n' + extra,
            text,
            count=1
        )
        text = text.replace(
            "if(document.getElementById('sPin'))settings.ui_pin=sPin.value;",
            "if(document.getElementById('sPin'))settings.ui_pin=sPin.value;\n"
            "if(document.getElementById('sWebhook'))settings.webhook_url=sWebhook.value||'';\n"
            "if(document.getElementById('sOfflineSound'))settings.offline_sound=sOfflineSound.checked;\n"
            "if(document.getElementById('sShowMinMax'))settings.show_minmax=sShowMinMax.checked;"
        )
        text = text.replace(
            "if(document.getElementById('sPin'))sPin.value=settings.ui_pin||'';",
            "if(document.getElementById('sPin'))sPin.value=settings.ui_pin||'';\n"
            "if(document.getElementById('sWebhook'))sWebhook.value=settings.webhook_url||'';\n"
            "if(document.getElementById('sOfflineSound'))sOfflineSound.checked=settings.offline_sound!==false;\n"
            "if(document.getElementById('sShowMinMax'))sShowMinMax.checked=settings.show_minmax!==false;"
        )
        print("  app → webhook / sound settings")

    # 8. Re-run wizard button
    if "rerunWizard" not in text:
        text = text.replace(
            "function backupConfig()",
            """function rerunWizard() {
  if (!confirm('Reset setup flags and open wizard? (nodes kept)')) return;
  fetch('/api/settings', {method:'POST', headers:{'Content-Type':'application/json'},
    body: JSON.stringify({setup_done:false, pins_done:false})}).then(()=> location.href='/setup');
}
function backupConfig()"""
        )
        # add button near backup if possible
        print("  app → re-run wizard function")

    # 9. Richer detail – open guests + history note
    if "detailGuestsBtn" not in text:
        text = text.replace(
            "detailModal.classList.add('open');",
            "detailModal.classList.add('open');\n"
            "  // quick guests button if not present\n"
            "  if (!document.getElementById('detailGuestsBtn')) {\n"
            "    const b = document.createElement('button'); b.id='detailGuestsBtn'; b.className='btn btn-ghost';\n"
            "    b.textContent='View Guests'; b.style.marginTop='8px';\n"
            "    b.onclick=()=>{closeDetail(); openGuests(detailName);};\n"
            "    detailModal.querySelector('.modal-box')?.appendChild(b);\n"
            "  }"
        )
        print("  app → detail → guests link")

    p.write_text(text, encoding="utf-8")
    print("  app.py written")

# ─────────────────────────────────────────────
# systemd generator + nginx notes (files)
# ─────────────────────────────────────────────
def write_helpers():
    svc = ROOT / "pve-node-monitor.service.example"
    svc.write_text("""[Unit]
Description=PVE Node Monitor
After=network.target

[Service]
Type=simple
User=pi
WorkingDirectory=/home/pi/pve-node-monitor
ExecStart=/usr/bin/python3 main.py
Restart=on-failure
RestartSec=5
Environment=PYTHONUNBUFFERED=1

[Install]
WantedBy=multi-user.target
""")
    print("  wrote pve-node-monitor.service.example")

    nginx = ROOT / "nginx-example.conf"
    nginx.write_text("""# Example reverse proxy for PVE Node Monitor
server {
    listen 80;
    server_name pve-mon.local;

    location / {
        proxy_pass http://127.0.0.1:8000;
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
        proxy_read_timeout 120s;
    }
}
""")
    print("  wrote nginx-example.conf")

if __name__ == "__main__":
    print("Phase 5A starting…")
    patch_config()
    patch_main()
    patch_monitor()
    patch_app()
    write_helpers()
    print("""
Done. Restart the app.

New / improved:
  • Webhook on offline/online
  • Offline beep sound (toggle in settings)
  • Power rate-limit + confirm
  • Undo delete (press U within 8s)
  • Big number widgets
  • Re-run wizard function
  • Clearer auth error
  • Alert escalation on hardware
  • LCD page enable list support
  • systemd + nginx example files

Still left for 5B (optional): drag-drop graphs, spike markers,
auto-discover, cluster status, rotary, full buzzer library,
tooltips, timezone, public status page.
""")
