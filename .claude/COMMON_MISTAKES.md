# Common mistakes

1. SQLite tests hide pgvector bugs: anything touching memory search must also run on real PostgreSQL.
2. Mocked tests hide server-level failures: start the real server once after dependency/route changes.
3. Tests must not build real network clients (conftest sets `memory_enabled=False`; inject fakes).
4. Offscreen/virtual renders are not proof of what the user sees; report them as such.
5. A too-good benchmark score means read the replies; scorer bugs inflated/deflated numbers before.
6. Background shell chains lose env vars; use script files and find PIDs with `ss -ltnp`.
