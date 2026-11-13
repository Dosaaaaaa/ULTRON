# ULTRON

Modular, secure, voice-first personal AI (FastAPI backend + PySide6 desktop + voice client + device agent).
Built in phases; **the user approves each phase before the next starts.**

## Read first
- `memory.md`: full hand-off context (rules, state of every phase, measurements, pending items).
- `docs/PLAN_PHASE_5.md`: current plan (security module, pairing/push, desktop background mode, Kotlin phone app).
- `docs/INDEX.md`: map of the other docs. Do not load them all.

## Hard rules
- Never print/log/commit secrets (`backend/.env` is git-ignored). Scan for secrets with Python, not grep.
- No system-wide changes or installs, no destructive commands, without asking. Never open the microphone.
- Never fabricate test/live results; offscreen renders are not the user's screen. Say what was not verified.
- Don't use `pgrep -f`/`pkill -f`; get PIDs from `ss -ltnp`.
- Temp files go in the session scratchpad, not `/tmp`.

## Commands (run from each directory; each has its own `.venv`)
```bash
cd backend      && .venv/bin/python -m pytest -q && .venv/bin/ruff check .
cd desktop/voice && .venv/bin/python -m pytest -q
cd desktop/agent && .venv/bin/python -m pytest -q
cd desktop/app   && .venv/bin/python -m pytest -q
cd backend && .venv/bin/uvicorn app.main:app_factory --factory --host 127.0.0.1 --port 8000
cd desktop/app && .venv/bin/python -m ultron_desktop
```

## Layout
`backend/app/{api,core,db,memory,models,tools}` · `backend/bench` · `desktop/{app,voice,agent,orb-ui}` · `shared/protocols`.
Knowledge graph (if built): `graphify-out/GRAPH_REPORT.md`; query it before searching the code by hand.
