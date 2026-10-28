from playwright.sync_api import sync_playwright
with sync_playwright() as p:
    b = p.chromium.launch()
    pg = b.new_page()
    errs, reqs = [], []
    pg.on("console", lambda m: errs.append(m.text) if m.type=="error" else None)
    pg.on("pageerror", lambda e: errs.append(str(e)))
    pg.on("request", lambda r: reqs.append(r.url) if "science/capabilities" in r.url else None)
    pg.goto("http://127.0.0.1:18899/", wait_until="networkidle", timeout=30000)
    pg.wait_for_timeout(3000)
    print("title:", pg.title())
    print("console_errors:", len(errs), errs[:3])
    print("auto science/capabilities requests (no project selected):", len(reqs), reqs[:3])
    # traversal
    t = pg.evaluate("fetch('/js/../etc/passwd').then(r=>r.status)")
    print("traversal:", t)
    pg.screenshot(path="/tmp/m503r2-workbench.png")
    # project selection attempt: look for a project selector
    sel = pg.locator("select").all_inner_texts()
    print("selects:", [s[:80] for s in sel][:5])
    b.close()
