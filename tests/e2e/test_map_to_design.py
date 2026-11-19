"""Browser E2E: trace roof + obstacle on the map -> import -> designer generates a design -> 3-D view renders.
Run:  python tests/e2e/preview_server.py &   then   python tests/e2e/test_map_to_design.py   (needs Playwright)."""
import os, sys
from playwright.sync_api import sync_playwright
BASE = os.environ.get("E2E_BASE", "http://127.0.0.1:8770")


def run(shot_dir=None):
    with sync_playwright() as p:
        b = p.chromium.launch(args=["--no-proxy-server"])
        pg = b.new_page(viewport={"width": 1300, "height": 850})
        errors = []
        pg.on("pageerror", lambda e: errors.append(str(e)))
        pg.goto(BASE + "/map"); pg.wait_for_timeout(600)
        pg.fill("#loc", "17.385, 78.487"); pg.click("text=Go"); pg.wait_for_timeout(300)
        box = pg.locator("#cv").bounding_box(); cx, cy = box["x"] + box["width"] / 2, box["y"] + box["height"] / 2
        mpp = pg.evaluate("SuniteMap.M.metresPerPx(17.385, mv.z)")
        px = lambda m: m / mpp
        for dx, dy in [(-15, -10), (15, -10), (15, 10), (-15, 10)]:          # 30 m x 20 m roof
            pg.mouse.click(cx + px(dx), cy - px(dy)); pg.wait_for_timeout(50)
        pg.click("text=Finish shape")
        pg.click("button[data-t=obstacle]")
        for dx, dy in [(-2, -2), (2, -2), (2, 2), (-2, 2)]:
            pg.mouse.click(cx + px(dx), cy - px(dy)); pg.wait_for_timeout(50)
        pg.once("dialog", lambda d: d.accept("3"))
        pg.click("text=Finish shape"); pg.wait_for_timeout(300)
        area = pg.evaluate("SuniteMap.M.area(mv.features[0].ring)")
        assert abs(area - 600) < 15, area                                    # traced on screen pixels
        if shot_dir: pg.screenshot(path=os.path.join(shot_dir, "preview-map-trace.png"))
        pg.click("text=Use in designer"); pg.wait_for_url(BASE + "/"); pg.wait_for_timeout(500)
        pg.wait_for_function("document.getElementById('kw').textContent.includes('kWp')", timeout=60000)
        status = pg.evaluate("document.getElementById('status').textContent")
        n = int(pg.evaluate("document.getElementById('panels').textContent"))
        assert n > 50, (n, status)
        pg.goto(BASE + "/viewer"); pg.wait_for_timeout(2500)
        mods = pg.evaluate("document.getElementById('mods').textContent")
        assert mods.startswith(str(n)), (mods, n, pg.evaluate("document.getElementById('msg').textContent"))
        assert not errors, errors
        b.close()
        return n, area


if __name__ == "__main__":
    n, area = run(sys.argv[1] if len(sys.argv) > 1 else None)
    print(f"E2E passed: traced {area:.1f} m2 roof -> {n} modules -> 3-D view")
