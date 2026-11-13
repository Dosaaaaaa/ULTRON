# Quick Start

Run tests (from each dir): `.venv/bin/python -m pytest -q`; backend lint: `.venv/bin/ruff check .`
Backend: `cd backend && .venv/bin/uvicorn app.main:app_factory --factory --host 127.0.0.1 --port 8000`
Window: `cd desktop/app && ULTRON_API_TOKEN=... .venv/bin/python -m ultron_desktop`
Token: read from `backend/.env` with Python; never echo it.
