# ULTRON

A voice-first personal AI with a hybrid cloud/local brain. **Status: Phase 1 verified; Phase 2 (voice) built, partly verified; Phase 3 (desktop + device agent) built, verified without a real display/microphone.**

| Phase | Scope | State |
|---|---|---|
| 1 | FastAPI core, model router, personality, text chat, persistence, health, tests | **implemented** |
| 2 | Voice (wake word, VAD, STT, TTS, barge-in) | **built, partly verified** (see `docs/PHASE_2_VOICE.md`) |
| 3 | Ubuntu desktop client (PySide6) + device agent (tool registry, policy engine, audit) | **built, partly verified** (see `docs/PHASE_3_DESKTOP.md`) |
| 4 | PostgreSQL/pgvector memory | not started |
| 5 | Android client | not started |
| 6 | Controlled autonomy / permission engine | not started |
| 7 | Production hardening | not started |

ULTRON can converse (text, or voice with Phase 2) and remember the *conversation*. The **language model**
has no tools, no web access, no device access and no long-term memory, and is told so in its prompt.
Phase 3 adds a separate *device agent* that you (not the model) drive from the desktop app; connecting
the model to it is Phase 6.

## The short way (the `ultron` command)

```bash
bin/ultron doctor          # checks the venvs, Ollama and models, the token, the database, TLS; says what to fix
bin/ultron start           # starts the backend in the background (if it is not up) and opens the window
bin/ultron pair            # makes a phone pairing code (+ QR in the terminal); the window's TEL button does the same
bin/ultron status          # what is running, how many phones are connected
bin/ultron backup          # a consistent dated copy of the database
bin/ultron stop
bin/ultron service install --yes   # optional: start the backend at login (user level, asks first)
```

**Phone over Wi-Fi** (one-time): `desktop/packaging/make_lan_cert.sh <this computer's Wi-Fi IP>`, set
`ULTRON_TLS_CERT_FILE=~/.config/ultron/tls/cert.pem` in `backend/.env`, allow the port for your home network only
(`sudo ufw allow from 192.168.0.0/24 to any port 8443 proto tcp`), then `bin/ultron start --host <Wi-Fi IP> --port 8443 --tls`.
Install the app (`android/app/build/outputs/apk/release/app-release.apk`, or `adb install` it), press **TEL** in the
window and scan the QR with the app. The phone pins the certificate; it never accepts plain http.

**What the phone can do:** chat (typed or hold-to-talk), reminders (also by saying "remind me in 10 minutes to ..."),
call confirmations ("call Asha": the phone shows the number and dials only after you tap Call), and push
notifications that arrive while the app is closed.

## Run it

```bash
cd backend
uv venv --python 3.12 .venv && uv sync --python .venv/bin/python   # once
cp ../.env.example .env      # then set ULTRON_API_TOKEN (and cloud key/model if you have them)
ollama pull qwen3:4b-instruct   # ~2.5 GB, once. NOT plain qwen3:4b: that tag is thinking-only (23-99 s/reply)
.venv/bin/uvicorn app.main:app_factory --factory --host 127.0.0.1 --port 8000
```

Migrations run automatically on start (`ULTRON_AUTO_MIGRATE=true`). Manually:
`ULTRON_DATABASE_URL=... .venv/bin/alembic upgrade head`.

```bash
export T=<your ULTRON_API_TOKEN>
curl -s localhost:8000/health
curl -s -H "Authorization: Bearer $T" localhost:8000/v1/health/ready | python -m json.tool
curl -s -X POST localhost:8000/v1/chat -H "Authorization: Bearer $T" -H 'content-type: application/json' \
  -d '{"message":"Who are you?"}'
# continue:   add "conversation_id": "<id from the reply>"
# keep local: add "privacy": "local_only"
```

Other endpoints: `GET /v1/conversations`, `GET|DELETE /v1/conversations/{id}`, `GET /v1/usage`.
Interactive docs: `http://127.0.0.1:8000/docs`.

## Voice (Phase 2)

```bash
cd desktop/voice
uv venv --python 3.12 .venv && uv sync --python .venv/bin/python     # once
./scripts/fetch_models.sh        # Silero VAD, whisper base.en, Kokoro (to ~/.cache/ultron/models)
./scripts/build_whisper_cpp.sh   # whisper.cpp, CPU build into ./vendor (needs cmake + a C++ compiler)
export ULTRON_API_TOKEN=<same token as the backend>

.venv/bin/python -m ultron_voice selftest-mic            # YOU run this: opens the mic for 5 s
.venv/bin/python -m ultron_voice say "Hello, operator."  # speaks through the default output
.venv/bin/python -m ultron_voice run --mode command      # or --mode continuous
```
Say "Ultron" to wake it; "stand by" ends a continuous session; `m` + Enter toggles mute; talk over
ULTRON to interrupt it. Use a headset, or `--half-duplex` with loudspeakers (no echo cancellation).
Audio is processed in memory and never written to disk. Backend protocol: `shared/protocols/chat_ws.md`.

## Desktop app and device agent (Phase 3)

```bash
cd desktop/agent && uv venv --python 3.12 .venv && uv sync --python .venv/bin/python   # once
cd ../app        && uv venv --python 3.12 .venv && uv sync --python .venv/bin/python   # once (PySide6, ~600 MB)

# approve what the agent may touch. Nothing is approved by default except ~/ULTRON_Workspace (read/write)
desktop/agent/.venv/bin/python -m ultron_agent roots add ~/Documents/notes            # read-only
desktop/agent/.venv/bin/python -m ultron_agent roots add ~/Projects/scratch --write   # read/write
desktop/agent/.venv/bin/python -m ultron_agent apps discover                          # list installed apps
desktop/agent/.venv/bin/python -m ultron_agent apps add org.gnome.Calculator          # allowlist one

export ULTRON_API_TOKEN=<same token as the backend>      # or leave unset to use backend/.env
cd desktop/app && .venv/bin/python -m ultron_desktop     # the window; "Start agent" launches the device agent
```
Every agent action goes through an independent policy engine: reads and app launches are automatic,
creating/moving files needs your approval, deleting (to the Trash) needs you to type a phrase. The model
is never the authority. Details, limits and test evidence: `docs/PHASE_3_DESKTOP.md`.

## Test

```bash
cd backend && .venv/bin/python -m pytest -q      # no network, no credentials
.venv/bin/ruff check app tests migrations scripts
cd ../desktop/voice && .venv/bin/python -m pytest -q   # voice logic with fake hardware
cd ../agent && .venv/bin/python -m pytest -q           # device agent: policy, paths, audit, sockets
cd ../app && QT_QPA_PLATFORM=offscreen .venv/bin/python -m pytest -q   # desktop UI, offscreen
```

## Layout

```
backend/app/  api/v1  core (config, logging, errors, personality, orchestrator)
              models (adapters + router)  memory (conversation repo)  db (ORM, session)
backend/migrations/   backend/tests/
desktop/voice/ultron_voice/   # audio, vad, stt, tts, wake, session, client, cli
desktop/agent/ultron_agent/   # device agent: registry, policy, paths, audit, broker, server, tools/
desktop/app/ultron_desktop/   # PySide6 window: chat, status, device panel, approvals, voice controls
shared/prompts/ultron.v{1..N}.md    # the personality, versioned separately from code
shared/protocols/chat_ws.md  device_agent.md   # WebSocket chat / device-agent socket protocols
docs/  ARCHITECTURE.md  PHASE_1_VERIFICATION.md  PHASE_2_VOICE.md  PHASE_3_DESKTOP.md  evidence/
```

## Known limitations

See `docs/ARCHITECTURE.md`, `docs/PHASE_1_VERIFICATION.md` and `docs/PHASE_2_VOICE.md`.
