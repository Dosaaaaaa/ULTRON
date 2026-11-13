# Architecture map

- `backend/app/main.py` app factory; `core/` orchestrator, streaming, personality, reasoning, style, config;
  `models/` router + Ollama/OpenAI-compatible adapters; `memory/` store, context, embedder, guard, extract;
  `tools/web.py` web search; `api/v1/` REST + WebSocket; `db/` models + session; migrations in `backend/migrations`.
- `desktop/app/ultron_desktop/` window, web orb, memory panel, voice control, gpu.
- `desktop/voice/ultron_voice/` mic, VAD, STT, TTS, session, client. `desktop/agent/ultron_agent/` approved files/apps broker.
- `desktop/orb-ui/` Next.js orb (build -> `out/`). `shared/protocols/chat_ws.md` WebSocket frames.
- Details: `docs/ARCHITECTURE.md`.
