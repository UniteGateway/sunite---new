# Connecting Gemini (about 10 minutes)

1. Create a key in **Google AI Studio → API keys** (aistudio.google.com). For production volumes, enable billing on
   that Google Cloud project (paid tier raises rate limits). Do **not** paste the key into chats, email or code.
2. On the server, add to `.env` (or the hosting provider's secret settings):
   `GEMINI_API_KEY=...` and optionally `GEMINI_MODEL=` a current stable model id.
3. Restart the app and run `python -m backend.cli ai-check`. It lists the models your key can use, checks the configured
   model and makes one tiny test call. It never prints the key.
4. Use it:
   * `/assistant` — engineering Q&A grounded in project data; design changes appear as proposals you approve.
   * `/map` → **Detect with AI** — outlines roofs/obstacles on the current view (needs `SUNITE_TILE_SATELLITE_URL` from a
     licensed imagery provider whose terms allow automated analysis), or upload an image of exactly the current view.

What is protected
* The key is sent only in the `x-goog-api-key` header from the server; error messages strip it.
* `AI_DAILY_REQUESTS_PER_ORG` caps usage per organisation; token usage is logged per request in `ai_conversations`.
* The AI cannot change designs or invent heights: proposals need human approval; detected obstacles need measured heights.
* Model ids change often (Gemini 1.5 is retired and 2.5 was scheduled for shutdown in October 2026). The default alias
  `gemini-flash-latest` follows Google's current Flash model; pin a stable id once tested.
