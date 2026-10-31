from playwright.sync_api import sync_playwright
with sync_playwright() as p:
    b = p.chromium.launch()
    pg = b.new_page()
    codes = []
    pg.on("response", lambda r: codes.append((r.status, r.url)) if "science/capabilities" in r.url else None)
    pg.goto("http://127.0.0.1:18899/", wait_until="networkidle", timeout=30000)
    # find the project selector (contains 项目)
    for sel in pg.locator("select").all():
        try:
            first = sel.inner_text(timeout=500)
        except Exception:
            continue
        if "项目" in first:
            opts = sel.locator("option").all_inner_texts()
            if "Uncategorized" in opts:
                sel.select_option(label="Uncategorized")
                print("selected project: Uncategorized")
                break
    pg.wait_for_timeout(3000)
    print("science/capabilities responses after selection:", codes)
    pg.screenshot(path="/tmp/m503r2-workbench-project.png")
    b.close()
