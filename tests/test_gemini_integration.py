"""Gemini integration tested at the HTTP level with a fake transport (no network, no real key)."""
import io, json, urllib.error
from backend.ai.providers import GeminiProvider, ProviderError
from backend.ai.assistant import ask
from backend.ai import vision
from tests.test_ai_assistant import _setup
from tests.test_identity_tenancy import _raises

KEY = "test-key-123"


class FakeHTTP:
    def __init__(self, replies):
        self.replies, self.requests = list(replies), []

    def __call__(self, req, timeout=None):
        self.requests.append({"url": req.full_url, "headers": dict(req.header_items()),
                              "body": json.loads(req.data.decode()) if req.data else None, "method": req.get_method()})
        r = self.replies.pop(0)
        if isinstance(r, int):
            raise urllib.error.HTTPError(req.full_url, r, "err", {"Retry-After": "0"}, io.BytesIO(json.dumps({"error": {"message": f"boom {KEY}"}}).encode()))
        resp = io.BytesIO(json.dumps(r).encode())
        resp.__enter__ = lambda *a: resp; resp.__exit__ = lambda *a: None
        return resp


def _cand(parts, usage=None):
    return {"candidates": [{"content": {"role": "model", "parts": parts}, "finishReason": "STOP"}],
            "usageMetadata": usage or {"promptTokenCount": 100, "candidatesTokenCount": 20, "totalTokenCount": 120}}


def test_key_in_header_never_in_url_and_tool_round_trip():
    con, admin, d, pid = _setup()
    http = FakeHTTP([
        _cand([{"functionCall": {"name": "get_design_summary", "args": {"project_id": pid}, "id": "call-1"},
                "thoughtSignature": "sig-abc"}])])
    # second reply must quote numbers from the tool output: build it lazily from the first request's tool result
    def second(req, timeout=None):
        body = json.loads(req.data.decode())
        model_turn, tool_turn = body["contents"][-2], body["contents"][-1]
        assert model_turn["parts"][0]["thoughtSignature"] == "sig-abc"           # replayed verbatim
        fr = tool_turn["parts"][0]["functionResponse"]
        assert fr["id"] == "call-1" and fr["name"] == "get_design_summary"
        kwp = fr["response"]["result"]["dc_kwp"]
        resp = io.BytesIO(json.dumps(_cand([{"text": f"About {kwp} kWp fits."}])).encode())
        resp.__enter__ = lambda *a: resp; resp.__exit__ = lambda *a: None
        http.requests.append({"url": req.full_url, "headers": dict(req.header_items())})
        return resp
    calls = iter([http, second])
    prov = GeminiProvider(key=KEY, model="gemini-flash-latest", opener=lambda req, timeout=None: next(calls)(req, timeout), sleep=lambda s: None)
    out = ask(con, d, "How big?", project_id=pid, provider=prov)
    assert out["status"] == "ok" and out["grounding"]["grounded"], out
    assert out["usage"]["totalTokenCount"] == 240
    for r in http.requests:
        assert KEY not in r["url"] and r["headers"].get("X-goog-api-key") == KEY
    assert http.requests[0]["url"].endswith("/models/gemini-flash-latest:generateContent")
    first = http.requests[0]["body"]
    assert first["tools"][0]["functionDeclarations"][0]["name"] == "get_design_summary"
    assert first["systemInstruction"]["parts"][0]["text"].startswith("You are the SUNITE")


def test_retry_then_success_and_errors_hide_key():
    http = FakeHTTP([429, 503, _cand([{"text": "ok"}])])
    slept = []
    p = GeminiProvider(key=KEY, opener=http, sleep=slept.append, max_retries=3)
    assert p.complete("s", [{"role": "user", "content": "hi"}])["text"] == "ok" and len(slept) == 2
    p404 = GeminiProvider(key=KEY, model="gemini-retired", opener=FakeHTTP([404]), sleep=lambda s: None)
    e = None
    try:
        p404.complete("s", [{"role": "user", "content": "hi"}])
    except ProviderError as x:
        e = x
    assert e and e.status == 404 and "ai-check" in str(e) and KEY not in str(e)
    assert _raises(ProviderError, GeminiProvider(key=KEY, opener=FakeHTTP([429, 429]), max_retries=1, sleep=lambda s: None).complete,
                   "s", [{"role": "user", "content": "x"}])


def test_blocked_response_and_list_models():
    p = GeminiProvider(key=KEY, opener=FakeHTTP([{"promptFeedback": {"blockReason": "SAFETY"}}]), sleep=lambda s: None)
    assert "SAFETY" in p.complete("s", [{"role": "user", "content": "x"}])["text"]
    p = GeminiProvider(key=KEY, opener=FakeHTTP([{"models": [
        {"name": "models/gemini-flash-latest", "supportedGenerationMethods": ["generateContent"]},
        {"name": "models/text-embedding", "supportedGenerationMethods": ["embedContent"]}]}]), sleep=lambda s: None)
    assert p.list_models() == ["gemini-flash-latest"]


def test_provider_error_surfaces_cleanly_and_daily_limit(monkeypatch=None):
    import os
    con, admin, d, pid = _setup()
    bad = GeminiProvider(key=KEY, opener=FakeHTTP([401]), sleep=lambda s: None, max_retries=0)
    out = ask(con, d, "hi", provider=bad)
    assert out["status"] == "error" and "invalid" in out["message"].lower() and KEY not in out["message"]
    os.environ["AI_DAILY_REQUESTS_PER_ORG"] = "1"
    try:
        good = GeminiProvider(key=KEY, opener=FakeHTTP([_cand([{"text": "hello"}])]), sleep=lambda s: None)
        assert ask(con, d, "hi", provider=good)["status"] == "ok"
        assert ask(con, d, "again", provider=good)["status"] == "limit"
    finally:
        os.environ.pop("AI_DAILY_REQUESTS_PER_ORG")


def test_vision_detection_georeferencing():
    view = {"center_lat": 17.385, "center_lon": 78.487, "zoom": 20, "width_px": 800, "height_px": 600}
    # a 200 x 200 normalised square centred in the image, plus junk the validator must reject
    model_json = {"features": [
        {"kind": "roof", "label": "Main roof", "confidence": 0.92, "polygon": [[400, 400], [600, 400], [600, 600], [400, 600]]},
        {"kind": "water_tank", "confidence": 0.81, "polygon": [[480, 480], [520, 480], [520, 520], [480, 520]]},
        {"kind": "roof", "confidence": 0.1, "polygon": [[0, 0], [10, 0], [10, 10]]},
        {"kind": "ufo", "confidence": 0.9, "polygon": [[0, 0], [10, 0], [10, 10]]},
        {"kind": "roof", "confidence": 0.9, "polygon": [[0, 0], [100, 100], [0, 100], [100, 0]]},
        {"kind": "roof", "confidence": 0.9, "polygon": [[0, 0], [2000, 0], [0, 10]]}]}
    http = FakeHTTP([_cand([{"text": json.dumps(model_json)}])])
    p = GeminiProvider(key=KEY, opener=http, sleep=lambda s: None)
    fc = vision.detect(p, b"\x89PNG fake", view)
    assert len(fc["features"]) == 2 and len(fc["rejected"]) == 4
    req = http.requests[0]["body"]
    assert req["generationConfig"]["responseMimeType"] == "application/json"
    assert req["contents"][0]["parts"][1]["inlineData"]["mimeType"] == "image/png"
    roof, tank = fc["features"]
    assert roof["properties"]["status"] == "unverified" and tank["properties"]["height_m"] is None
    assert tank["properties"]["role"] == "obstacle"
    # centre of the roof square maps back to the view centre
    ring = roof["geometry"]["coordinates"][0][:-1]
    lon = sum(p[0] for p in ring) / 4; lat = sum(p[1] for p in ring) / 4
    assert abs(lon - 78.487) < 1e-7 and abs(lat - 17.385) < 1e-7
    # 200/1000 of 800 px at z20 ~= 160 px * ~0.142 m/px ~= 22.8 m wide
    mpp = 156543.03392 * __import__("math").cos(17.385 * 3.14159265 / 180) / 2 ** 20
    assert abs(roof["properties"]["area_m2"] - (160 * mpp) * (120 * mpp)) / roof["properties"]["area_m2"] < 0.01


def test_compose_view_stitches_tiles():
    from PIL import Image
    def tile_png(color):
        b = io.BytesIO(); Image.new("RGB", (256, 256), color).save(b, "PNG"); return b.getvalue()
    fetched = []
    def opener(url):
        fetched.append(url)
        r = io.BytesIO(tile_png((10, 200, 30))); r.__enter__ = lambda *a: r; r.__exit__ = lambda *a: None
        return r
    png = vision.compose_view("https://tiles.example/{z}/{x}/{y}.jpg", 17.385, 78.487, 19, 600, 400, opener=opener)
    im = Image.open(io.BytesIO(png))
    assert im.size == (600, 400) and im.getpixel((300, 200)) == (10, 200, 30)
    assert all("/19/" in u for u in fetched) and 4 <= len(fetched) <= 12          # <= 4 columns x 3 rows
    assert _raises(ValueError, vision.compose_view, "u", 0, 0, 5, 600, 400, opener=opener)
