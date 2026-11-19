"""LLM provider abstraction. Gemini (Google AI Studio key) is the preferred provider.

Gemini REST details (ai.google.dev, checked Oct 2026):
  * POST {base}/models/{model}:generateContent with header x-goog-api-key (the key is never put in the URL,
    so it cannot leak into proxy/server logs).
  * Function calling: the model returns functionCall parts (with an id on newer models). The model's
    content is replayed VERBATIM on the next turn (this preserves thought signatures required by Gemini 3
    models) and each result is sent back as a functionResponse with the same name and id.
  * Model ids change frequently; default is the auto-updated alias 'gemini-flash-latest'. Pin a stable id
    in production with GEMINI_MODEL (e.g. a current 'gemini-3.x-flash'); `python -m backend.cli ai-check`
    lists the models your key can use.
Providers return {"text", "tool_calls", "raw", "usage", "finish_reason"} and never touch the database."""
from __future__ import annotations
import json, os, time, urllib.error, urllib.request

DEFAULT_BASE = "https://generativelanguage.googleapis.com/v1beta"
RETRY_STATUS = {429, 500, 502, 503, 504}


class ProviderUnavailable(RuntimeError):
    pass


class ProviderError(RuntimeError):
    def __init__(self, message, status=None):
        super().__init__(message); self.status = status


class GeminiProvider:
    name = "gemini"

    def __init__(self, model=None, key=None, base=None, timeout=None, max_retries=None, opener=None, sleep=time.sleep):
        self.key = key or os.environ.get("GEMINI_API_KEY")
        if not self.key:
            raise ProviderUnavailable("GEMINI_API_KEY is not configured")
        self.model = model or os.environ.get("GEMINI_MODEL", "gemini-flash-latest")
        self.base = (base or os.environ.get("GEMINI_API_BASE", DEFAULT_BASE)).rstrip("/")
        self.timeout = float(timeout or os.environ.get("GEMINI_TIMEOUT_S", 60))
        self.max_retries = int(max_retries if max_retries is not None else os.environ.get("GEMINI_MAX_RETRIES", 3))
        self._open = opener or urllib.request.urlopen
        self._sleep = sleep

    # ---------------- HTTP ----------------
    def _request(self, method, path, body=None):
        url = f"{self.base}/{path}"
        data = json.dumps(body).encode() if body is not None else None
        for attempt in range(self.max_retries + 1):
            req = urllib.request.Request(url, data=data, method=method,
                                         headers={"Content-Type": "application/json", "x-goog-api-key": self.key})
            try:
                with self._open(req, timeout=self.timeout) as r:
                    return json.loads(r.read().decode())
            except urllib.error.HTTPError as e:
                detail = ""
                try:
                    detail = json.loads(e.read().decode()).get("error", {}).get("message", "")
                except Exception:
                    pass
                if e.code in RETRY_STATUS and attempt < self.max_retries:
                    ra = e.headers.get("Retry-After") if e.headers else None
                    self._sleep(min(30.0, float(ra)) if ra and str(ra).replace(".", "").isdigit() else min(30.0, 2 ** attempt))
                    continue
                raise ProviderError(self._explain(e.code, detail), e.code) from None
            except (urllib.error.URLError, TimeoutError) as e:
                if attempt < self.max_retries:
                    self._sleep(min(30.0, 2 ** attempt)); continue
                raise ProviderError(f"Cannot reach Gemini API ({getattr(e, 'reason', e)}). Check internet/proxy settings.") from None
        raise ProviderError("Gemini API retries exhausted")

    def _explain(self, code, detail):
        safe = (detail or "").replace(self.key, "***")[:300]
        hints = {400: "Request rejected", 401: "API key invalid", 403: "API key not permitted for this model/project",
                 404: f"Model '{self.model}' not found: run `python -m backend.cli ai-check` and set GEMINI_MODEL",
                 429: "Rate limit or quota exceeded (upgrade tier or retry later)"}
        return f"Gemini API error {code}: {hints.get(code, 'service error')}" + (f" - {safe}" if safe else "")

    # ---------------- API ----------------
    def list_models(self):
        out, token = [], None
        for _ in range(10):
            d = self._request("GET", "models?pageSize=200" + (f"&pageToken={token}" if token else ""))
            out += [m["name"].split("/", 1)[-1] for m in d.get("models", [])
                    if "generateContent" in m.get("supportedGenerationMethods", [])]
            token = d.get("nextPageToken")
            if not token:
                break
        return out

    @staticmethod
    def _contents(messages):
        contents = []
        for m in messages:
            if m["role"] == "tool":
                fr = {"name": m["name"], "response": {"result": json.loads(m["content"]) if isinstance(m["content"], str) else m["content"]}}
                if m.get("call_id"):
                    fr["id"] = m["call_id"]
                part = {"functionResponse": fr}
                if contents and contents[-1]["role"] == "user" and all("functionResponse" in p for p in contents[-1]["parts"]):
                    contents[-1]["parts"].append(part)        # parallel calls -> one user turn
                else:
                    contents.append({"role": "user", "parts": [part]})
            elif m["role"] == "assistant":
                contents.append(m.get("raw") or {"role": "model", "parts": (
                    [{"functionCall": {"name": c["name"], "args": c["args"], **({"id": c["id"]} if c.get("id") else {})}} for c in m.get("tool_calls", [])]
                    or [{"text": m.get("content", "")}])})
            else:
                parts = [{"text": m["content"]}] + [{"inlineData": {"mimeType": i["mime"], "data": i["b64"]}} for i in m.get("images", [])]
                contents.append({"role": "user", "parts": parts})
        return contents

    def complete(self, system: str, messages: list[dict], tools: list[dict] | None = None, *,
                 response_schema: dict | None = None, temperature=0.1) -> dict:
        body = {"systemInstruction": {"parts": [{"text": system}]}, "contents": self._contents(messages),
                "generationConfig": {"temperature": temperature}}
        if tools:
            body["tools"] = [{"functionDeclarations": [{"name": t["name"], "description": t["description"],
                                                        "parameters": t["parameters"]} for t in tools]}]
        if response_schema:
            body["generationConfig"].update(responseMimeType="application/json", responseSchema=response_schema)
        data = self._request("POST", f"models/{self.model}:generateContent", body)
        cands = data.get("candidates") or []
        if not cands:
            reason = (data.get("promptFeedback") or {}).get("blockReason", "no candidates")
            return {"text": f"The AI service declined to answer ({reason}).", "tool_calls": [], "raw": None,
                    "usage": data.get("usageMetadata", {}), "finish_reason": reason}
        content = cands[0].get("content") or {"role": "model", "parts": []}
        parts = content.get("parts", [])
        calls = [{"name": p["functionCall"]["name"], "args": p["functionCall"].get("args", {}) or {},
                  **({"id": p["functionCall"]["id"]} if p["functionCall"].get("id") else {})} for p in parts if "functionCall" in p]
        text = "".join(p.get("text", "") for p in parts if "text" in p and not p.get("thought")) or None
        return {"text": text, "tool_calls": calls, "raw": content, "usage": data.get("usageMetadata", {}),
                "finish_reason": cands[0].get("finishReason")}


class ScriptedProvider:
    """Deterministic provider for tests and offline demos: replays a list of responses."""
    name = "scripted"

    def __init__(self, script: list[dict]):
        self.script, self.seen = list(script), []

    def complete(self, system, messages, tools=None, **kw):
        self.seen.append(messages[-1])
        if not self.script:
            return {"text": "No further scripted output.", "tool_calls": []}
        step = self.script.pop(0)
        return step(messages) if callable(step) else step


def default_provider():
    return GeminiProvider()
