"""Browser E2E: AI suggestions appear dashed, invalid outline discarded, accept requires a measured height."""
import os, sys
from playwright.sync_api import sync_playwright
BASE = os.environ.get("E2E_BASE", "http://127.0.0.1:8770")
with sync_playwright() as p:
    b = p.chromium.launch(args=["--no-proxy-server"]); pg = b.new_page(viewport={"width": 1300, "height": 850})
    errs = []; pg.on("pageerror", lambda e: errs.append(str(e)))
    pg.goto(BASE + "/map"); pg.wait_for_timeout(600)
    pg.fill("#loc", "17.385, 78.487"); pg.click("text=Go"); pg.wait_for_timeout(200)
    pg.click("#aiBtn"); pg.wait_for_timeout(800)
    msg = pg.evaluate("document.getElementById('msg').textContent")
    assert "unverified" in msg and "1 outline(s) discarded" in msg, msg
    assert pg.evaluate("suggestions.length") == 2
    pg.screenshot(path=(sys.argv[1] + "/preview-ai-detect.png") if len(sys.argv) > 1 else "/tmp/ai.png")
    pg.click("#sugg button:has-text('Accept') >> nth=0")                    # roof: no height needed
    pg.once("dialog", lambda d: d.accept("2.4"))
    pg.click("#sugg button:has-text('Accept') >> nth=0")                    # tank: asks for measured height
    pg.wait_for_timeout(200)
    feats = pg.evaluate("mv.features.map(f=>[f.role,f.height_m])")
    assert feats == [["roof", None], ["obstacle", 2.4]], feats
    assert not errs, errs
    b.close()
print("AI detection UI E2E passed")
