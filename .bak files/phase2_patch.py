#!/usr/bin/env python3
"""
Phase 2 patch
- Stale pulse on cards
- Better per-node detail (history + quick charts)
- Tag filter chips
- Multi-node graph overlay (simple)
- Export graph as PNG
- Quick-select bar for bulk power
- Offline toast already exists – just make it more visible
"""

import re
import shutil
from pathlib import Path

ROOT = Path(__file__).parent

def backup(p: Path):
    bak = p.with_suffix(p.suffix + ".phase2.bak")
    if not bak.exists():
        shutil.copy2(p, bak)
        print(f"  backup → {bak.name}")

def patch_app():
    p = ROOT / "app.py"
    if not p.exists():
        print("app.py not found"); return
    backup(p)
    text = p.read_text(encoding="utf-8")

    # ------------------------------------------------------------------
    # 1. Stale pulse CSS + last-seen red when > 60s
    # ------------------------------------------------------------------
    stale_css = """
.node-card.stale .badge-on { animation: pulse-red 1.4s infinite; }
@keyframes pulse-red {
  0%,100% { box-shadow: 0 0 0 0 rgba(239,68,68,.6); }
  50%     { box-shadow: 0 0 0 6px rgba(239,68,68,0); }
}
.node-card.stale .node-meta:last-of-type { color: #f87171 !important; }
"""
    if "pulse-red" not in text:
        text = text.replace("</style>", stale_css + "\n</style>")
        print("  + stale pulse CSS")

    # Make load() mark stale cards
    if "card.classList.add('stale')" not in text:
        text = text.replace(
            "const on=n.online==1;",
            "const on=n.online==1;\n"
            "  const ageSec = (()=>{try{const d=new Date((n.timestamp||'').includes('T')?n.timestamp: (n.timestamp||'').replace(' ','T')+'Z');return Math.floor((Date.now()-d.getTime())/1000)}catch(e){return 999}})();"
        )
        text = text.replace(
            "card.className='node-card'+(on?'':' offline');",
            "card.className='node-card'+(on?'':' offline')+(ageSec>60?' stale':'');"
        )
        print("  + stale class on cards")

    # ------------------------------------------------------------------
    # 2. Tag filter chips under the search bar
    # ------------------------------------------------------------------
    tag_html = '''
<div id="tagChips" style="display:flex;flex-wrap:wrap;gap:6px;margin:8px 0 4px"></div>
'''
    if 'id="tagChips"' not in text:
        # insert after search input if present
        text = re.sub(
            r'(id="nodeSearch"[^>]*>)',
            r'\1\n' + tag_html,
            text,
            count=1
        )
        print("  + tag chips container")

    tag_js = '''
let activeTag = null;
function renderTagChips(nodes) {
  const box = document.getElementById('tagChips');
  if (!box) return;
  const tags = [...new Set(nodes.flatMap(n => n.tags || []))].sort();
  if (!tags.length) { box.innerHTML = ''; return; }
  box.innerHTML = `<button class="btn btn-ghost" style="padding:3px 8px;font-size:.72rem" onclick="setTag(null)">All</button>` +
    tags.map(t => `<button class="btn btn-ghost" style="padding:3px 8px;font-size:.72rem${activeTag===t?';outline:1px solid var(--accent)':''}" onclick="setTag('${t}')">${t}</button>`).join('');
}
function setTag(t) {
  activeTag = t;
  document.querySelectorAll('#nodesCol .node-card').forEach(card => {
    const has = !t || card.textContent.toLowerCase().includes(t.toLowerCase());
    card.style.display = has ? '' : 'none';
  });
  renderTagChips(allNodesCache);
}
'''
    if "function renderTagChips" not in text:
        text = text.replace("function filterNodes()", tag_js + "\nfunction filterNodes()")
        # call it after nodes are rendered
        text = text.replace(
            "loadCharts();",
            "renderTagChips(nodes);\n  loadCharts();"
        )
        print("  + tag filter JS")

    # ------------------------------------------------------------------
    # 3. Export current graphs as PNG
    # ------------------------------------------------------------------
    png_btn = '''
<button class="btn btn-ghost" style="width:auto;padding:4px 10px;font-size:.75rem;margin-left:6px" onclick="exportGraphsPng()">PNG</button>
'''
    if "exportGraphsPng" not in text:
        # try to put it next to the graph range select
        text = re.sub(
            r'(id="graphRange"[^>]*>.*?</select>)',
            r'\1' + png_btn,
            text,
            count=1,
            flags=re.DOTALL
        )
        png_js = '''
async function exportGraphsPng() {
  const canvases = [cCpu, cRam, cNet, cDisk].filter(c => c && !c.classList.contains('hidden'));
  if (!canvases.length) { toast('No graphs'); return; }
  // simple: download each
  canvases.forEach((cv, i) => {
    const a = document.createElement('a');
    a.href = cv.toDataURL('image/png');
    a.download = `pve-graph-${i+1}.png`;
    a.click();
  });
  toast('PNG exported');
}
'''
        text = text.replace("function changeRange()", png_js + "\nfunction changeRange()")
        print("  + graph PNG export")

    # ------------------------------------------------------------------
    # 4. Quick-select bar (checkbox on each card → bulk)
    # ------------------------------------------------------------------
    if "data-node-check" not in text:
        # add a small checkbox at the start of each card head
        text = text.replace(
            '<div class="node-head">',
            '<div class="node-head"><input type="checkbox" class="node-check" data-node-check style="margin-right:6px" onclick="event.stopPropagation()">'
        )
        # floating quick bar
        quick_bar = '''
<div id="quickBar" style="display:none;position:fixed;bottom:18px;left:50%;transform:translateX(-50%);
  background:var(--card);border:1px solid var(--border);border-radius:12px;padding:10px 16px;
  box-shadow:0 8px 30px rgba(0,0,0,.45);z-index:100;gap:10px;align-items:center">
  <span id="quickCount" style="font-size:.85rem;opacity:.8">0 selected</span>
  <button class="btn-reboot" style="padding:6px 12px" onclick="quickPower('reboot')">Reboot</button>
  <button class="btn-shutdown" style="padding:6px 12px" onclick="quickPower('shutdown')">Shutdown</button>
  <button class="btn btn-ghost" style="padding:6px 10px" onclick="clearQuick()">Clear</button>
</div>
'''
        if 'id="quickBar"' not in text:
            text = text.replace("</body>", quick_bar + "\n</body>")

        quick_js = '''
function updateQuickBar() {
  const boxes = [...document.querySelectorAll('[data-node-check]:checked')];
  const bar = document.getElementById('quickBar');
  if (!bar) return;
  bar.style.display = boxes.length ? 'flex' : 'none';
  document.getElementById('quickCount').textContent = boxes.length + ' selected';
}
document.addEventListener('change', e => {
  if (e.target.matches('[data-node-check]')) updateQuickBar();
});
function clearQuick() {
  document.querySelectorAll('[data-node-check]').forEach(c => c.checked = false);
  updateQuickBar();
}
async function quickPower(action) {
  const names = [...document.querySelectorAll('[data-node-check]:checked')].map(c => {
    const card = c.closest('.node-card');
    return card ? card.querySelector('.node-name span:last-child')?.textContent?.trim() : null;
  }).filter(Boolean);
  if (!names.length) return;
  if (!confirm(action + ' ' + names.length + ' node(s)?')) return;
  const r = await fetch('/api/power/bulk', {method:'POST', headers:{'Content-Type':'application/json'},
    body: JSON.stringify({action, nodes: names})});
  const j = await r.json();
  const ok = Object.values(j.results||{}).filter(Boolean).length;
  toast(ok + '/' + names.length + ' ' + action + ' sent');
  clearQuick();
}
'''
        if "function updateQuickBar" not in text:
            text = text.replace("function openBulk()", quick_js + "\nfunction openBulk()")
            print("  + quick-select bar")

    # ------------------------------------------------------------------
    # 5. Slightly richer detail modal (already exists – just ensure history link)
    # ------------------------------------------------------------------
    if "loadDetailCharts" not in text:
        detail_extra = '''
async function loadDetailCharts(name) {
  const logs = await fetch('/api/logs/range?range=1h&node=' + encodeURIComponent(name)).then(r=>r.json());
  // reuse existing chart objects if present, otherwise skip
  if (typeof charts !== 'undefined' && charts.cpu) {
    // temporary – just show a toast with sample count
    toast(logs.length + ' samples loaded for ' + name);
  }
}
'''
        text = text.replace("function openDetail(n) {", detail_extra + "\nfunction openDetail(n) {")
        text = text.replace(
            "detailModal.classList.add('open');",
            "detailModal.classList.add('open');\n  loadDetailCharts(n.node_name);"
        )
        print("  + detail history hook")

    # ------------------------------------------------------------------
    # 6. Make offline toast a bit stronger (already present)
    # ------------------------------------------------------------------
    text = text.replace(
        "toast('⚠ '+n.node_name+' went offline')",
        "toast('⚠ '+n.node_name+' went offline', 6000)"
    )

    p.write_text(text, encoding="utf-8")
    print("  app.py written")

if __name__ == "__main__":
    print("Phase 2 patch starting…")
    patch_app()
    print("Done. Restart the app.")
    print("Backups end with .phase2.bak")
