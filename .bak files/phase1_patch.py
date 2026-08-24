#!/usr/bin/env python3
"""Phase 1 patch – LCD centering, remove accent, clean bulk, guests card, mobile CSS"""

import re
import shutil
from pathlib import Path

ROOT = Path(__file__).parent          # put this script next to main.py / app.py
# or change to: ROOT = Path("/path/to/your/project")

def backup(p: Path):
    bak = p.with_suffix(p.suffix + ".bak")
    if not bak.exists():
        shutil.copy2(p, bak)
        print(f"  backup → {bak.name}")

def patch_main():
    p = ROOT / "main.py"
    if not p.exists():
        print("main.py not found"); return
    backup(p)
    text = p.read_text(encoding="utf-8")

    # Replace the page display block (page == 0 ... else IP)
    old = re.compile(
        r'if page == 0:.*?hw\.display\(f"\{ip:\^16\}".*?\)',
        re.DOTALL
    )
    new = '''if page == 0:
                            # left-aligned (most readable primary view)
                            hw.display(f"CPU: {metrics['cpu']:5.1f}%    ",
                                       f"RAM: {metrics['ram_used']:4.1f}/{metrics['ram_total']:4.1f}G")
                        elif page == 1:
                            hw.display(f"{'DISK / VMs':^16}",
                                       f"{metrics['disk_pct']:5.1f}%   {metrics['active_vms']:2d} VMs")
                        elif page == 2:
                            up = fmt_rate(metrics["net_out"])
                            dn = fmt_rate(metrics["net_in"])
                            hw.display(f"{'NET  Up / Dn':^16}", f"{up} / {dn}")
                        elif page == 3:
                            uptime = fmt_uptime(metrics.get("uptime", 0))
                            hw.display(f"{'UPTIME':^16}", f"{uptime:^16}")
                        else:
                            ip = (metrics.get("ip") or "?")[:16]
                            hw.display(f"{ip:^16}", f"{(metrics.get('type') or 'server'):^16}")'''

    if old.search(text):
        text = old.sub(new, text)
        p.write_text(text, encoding="utf-8")
        print("  main.py  → LCD pages centered (except page 0)")
    else:
        print("  main.py  → pattern not found (already patched or different code)")

def patch_app():
    p = ROOT / "app.py"
    if not p.exists():
        print("app.py not found"); return
    backup(p)
    text = p.read_text(encoding="utf-8")

    # 1. Remove Accent color block (label + color input / swatch)
    text2 = re.sub(
        r'(?is)<label[^>]*>\s*Accent color\s*</label>.*?(?=<label[^>]*>\s*Density|</div>\s*</div>)',
        '',
        text,
        count=1
    )
    if text2 != text:
        text = text2
        print("  app.py   → Accent color removed")
    else:
        # fallback: simpler removal
        text2 = re.sub(r'(?is)Accent color.*?</div>\s*(?=<label[^>]*>Density|Density)', '', text, count=1)
        if text2 != text:
            text = text2
            print("  app.py   → Accent color removed (fallback)")
        else:
            print("  app.py   → Accent color block not found (maybe already gone)")

    # 2. Cleaner bulk modal (replace whole modal content if present)
    bulk_new = '''
<div id="bulkModal" class="modal">
  <div class="modal-box" style="max-width:340px">
    <h3 style="margin:0 0 4px">Bulk power</h3>
    <p style="margin:0 0 12px;font-size:.8rem;opacity:.7">Select nodes, then reboot or shutdown</p>
    <div id="bulkList" style="max-height:220px;overflow-y:auto;margin-bottom:14px"></div>
    <div style="display:flex;gap:8px;flex-wrap:wrap">
      <button class="btn-reboot" style="flex:1" onclick="bulkPower('reboot')">Reboot</button>
      <button class="btn-shutdown" style="flex:1" onclick="bulkPower('shutdown')">Shutdown</button>
      <button class="btn btn-ghost" style="flex:1" onclick="closeBulk()">Cancel</button>
    </div>
  </div>
</div>'''
    # try to replace existing bulk modal
    text2 = re.sub(
        r'(?is)<div id="bulkModal"[^>]*>.*?</div>\s*</div>\s*</div>',
        bulk_new.strip(),
        text,
        count=1
    )
    if text2 != text:
        text = text2
        print("  app.py   → Bulk modal cleaned")
    else:
        print("  app.py   → Bulk modal pattern not found (leave as-is)")

    # 3. Add Guests card after Activity card
    guests_card = '''
<div class="card" style="margin-top:10px">
  <div style="display:flex;align-items:center;justify-content:space-between">
    <div>
      <div style="font-weight:600">Guests</div>
      <div style="font-size:.75rem;opacity:.65">VM / LXC on active node</div>
    </div>
    <button class="btn btn-ghost" style="width:auto;padding:6px 10px;font-size:.78rem"
            onclick="openGuestsForActive()">Open</button>
  </div>
</div>'''
    if 'openGuestsForActive' not in text:
        # insert after Activity card (look for activityList)
        text2 = re.sub(
            r'(id="activityList"[^>]*>.*?</div>\s*</div>)',
            r'\1\n' + guests_card,
            text,
            count=1,
            flags=re.DOTALL
        )
        if text2 != text:
            text = text2
            print("  app.py   → Guests card added")
        else:
            print("  app.py   → Could not locate Activity card for Guests insert")
    else:
        print("  app.py   → Guests card already present")

    # 4. Add JS helper
    if 'function openGuestsForActive' not in text:
        text = text.replace(
            'function openGuests(name) {',
            '''function openGuestsForActive() {
  const n = allNodesCache.find(x => x.online == 1) || allNodesCache[0];
  if (n) openGuests(n.node_name);
  else toast('No nodes');
}
function openGuests(name) {'''
        )
        print("  app.py   → openGuestsForActive() added")

    # 5. Mobile CSS
    mobile_css = '''
@media (max-width: 780px) {
  .layout { flex-direction: column; }
  #nodesCol, .side { width: 100% !important; }
  .node-card { margin-bottom: 10px; }
  .actions { flex-wrap: wrap; }
  .btn-reboot, .btn-shutdown { flex: 1 1 40%; }
  .modal-box { margin: 12px; max-width: calc(100vw - 24px); }
  .mini-stats { gap: 6px; }
}
'''
    if '@media (max-width: 780px)' not in text:
        text = text.replace('</style>', mobile_css + '\n</style>')
        print("  app.py   → mobile CSS added")
    else:
        print("  app.py   → mobile CSS already present")

    p.write_text(text, encoding="utf-8")
    print("  app.py written")

if __name__ == "__main__":
    print("Phase 1 patch starting…")
    patch_main()
    patch_app()
    print("Done. Restart the app to see changes.")
    print("Backups: *.bak")
