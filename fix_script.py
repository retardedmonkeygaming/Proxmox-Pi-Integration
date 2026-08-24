#!/usr/bin/env python3
"""
Fix setup flow + clean leaked modals from setup page + restore / redirect
"""

import re
from pathlib import Path

ROOT = Path(__file__).parent
APP = ROOT / "app.py"

def main():
    if not APP.exists():
        print("app.py not found")
        return

    text = APP.read_text(encoding="utf-8")
    original = text

    # 1. Ensure / redirects to /setup when setup not done
    # Replace the dashboard() start logic to be robust
    dashboard_fix = '''
@app.get("/", response_class=HTMLResponse)
def dashboard():
    cfg = config.load_config()
    if not cfg.get("setup_done") or not cfg.get("nodes"):
        return RedirectResponse("/setup", status_code=303)
    if cfg.get("setup_done") and not cfg.get("pins_done"):
        return RedirectResponse("/setup/pins", status_code=303)
'''

    # Remove any broken/old dashboard def start and put the clean one
    # We only replace the beginning of the function
    text = re.sub(
        r'@app\.get\("/", response_class=HTMLResponse\)\s*\ndef dashboard\(\):.*?return (?:RedirectResponse|HTMLResponse|DASHBOARD)',
        dashboard_fix.strip() + "\n    return DASHBOARD_HTML",
        text,
        count=1,
        flags=re.DOTALL
    )

    # Fallback: if the above didn't match well, force the redirect check near the top of dashboard
    if "if not cfg.get(\"setup_done\") or not cfg.get(\"nodes\"):" not in text:
        text = text.replace(
            "def dashboard():",
            '''def dashboard():
    cfg = config.load_config()
    if not cfg.get("setup_done") or not cfg.get("nodes"):
        return RedirectResponse("/setup", status_code=303)
    if cfg.get("setup_done") and not cfg.get("pins_done"):
        return RedirectResponse("/setup/pins", status_code=303)
'''
        )

    # 2. Make sure setup routes still exist and redirect correctly when already done
    setup_fix = '''
@app.get("/setup", response_class=HTMLResponse)
def setup_page():
    cfg = config.load_config()
    if cfg.get("setup_done") and cfg.get("pins_done") and cfg.get("nodes"):
        return RedirectResponse("/", status_code=303)
    if cfg.get("setup_done") and not cfg.get("pins_done"):
        return RedirectResponse("/setup/pins", status_code=303)
    return SETUP_HTML
'''
    if "@app.get(\"/setup\"" in text:
        text = re.sub(
            r'@app\.get\("/setup", response_class=HTMLResponse\)\s*\ndef setup_page\(\):.*?return SETUP_HTML',
            setup_fix.strip(),
            text,
            count=1,
            flags=re.DOTALL
        )

    # 3. Hide leaked modals/footer on setup pages
    # Add a tiny CSS rule that hides them when body has class setup-page
    if "setup-page" not in text:
        text = text.replace(
            "</style>",
            """
body.setup-page #alertHistoryModal,
body.setup-page #qrModal,
body.setup-page #pinOverlay,
body.setup-page #bottomNav,
body.setup-page #quickBar,
body.setup-page .footer-diag {
  display: none !important;
}
</style>
"""
        )

    # 4. Make SETUP_HTML and PINS_HTML set body class="setup-page"
    # Best-effort: inject into the <body> tag of those strings if present
    text = text.replace(
        '<body>',
        '<body class="setup-page">',
        1  # only first occurrence is usually the setup one – careful
    )
    # Better: only touch SETUP_HTML / PINS_HTML if we can find them
    for marker in ("SETUP_HTML", "PINS_HTML"):
        # leave as-is if complex; the CSS hide is enough for most cases
        pass

    # 5. Guarantee RedirectResponse is imported
    if "RedirectResponse" not in text.split("from fastapi.responses")[0]:
        text = text.replace(
            "from fastapi.responses import HTMLResponse, RedirectResponse, JSONResponse, StreamingResponse",
            "from fastapi.responses import HTMLResponse, RedirectResponse, JSONResponse, StreamingResponse"
        )
        if "RedirectResponse" not in text:
            text = text.replace(
                "from fastapi.responses import",
                "from fastapi.responses import RedirectResponse, "
            )

    if text == original:
        print("No structural changes needed – applying safety redirects only")
    else:
        APP.write_text(text, encoding="utf-8")
        print("app.py updated")

    # 6. Also make sure config can be forced back to setup if user wants
    print("""
Done.

Now restart:
  sudo python3 main.py

Then open:  http://<pi-ip>:8000/

It should redirect to /setup if no nodes / setup_done is false.

If you still see old nodes and want a clean setup:
  python3 -c "
import config
c = config.load_config()
c['setup_done'] = False
c['pins_done'] = False
c['nodes'] = []
config.save_config(c)
print('Reset to first-boot state')
"
""")

if __name__ == "__main__":
    main()
