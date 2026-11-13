# ULTRON architecture (Phase 1)

## Request flow

```
POST /v1/chat
  -> auth (bearer, constant-time) -> rate limit
  -> orchestrator: load last N messages (per-conversation lock)
  -> classify complexity (heuristic)
  -> router.plan(privacy, complexity, policy) -> ordered candidate adapters
  -> for each candidate: build system prompt (personality + runtime context) -> adapter.complete
        failure (timeout / unavailable / rejected / bad response) => next candidate
  -> persist user + assistant message in ONE transaction (with engine, tokens, latency, route)
  -> response includes engine, route.attempts, fallback/degraded flags and a human-readable notice
```

If every candidate fails the API returns 503 and **saves nothing**.

## Decisions

1. **Personality is data, not code.** `shared/prompts/ultron.v{N}.md`, selected by
   `ULTRON_PERSONALITY_VERSION`; its SHA-256 is shown in `/v1/health/ready`; the version is stored on
   every assistant message. Every provider gets the same rules; since v4 a version may ship two
   renderings of them (`ultron.v4.md` for cloud, a longer `ultron.v4.local.md` for the small local
   model, which measurably needs explicit wording and examples; see `docs/PERSONALITY_V4.md`).
   A generated *runtime context* block differs per request (engine, tier, capabilities,
   degradation), so the model cannot claim abilities the build lacks.
2. **Router, not the model, enforces privacy.** `local_only` requests, the `local_only` policy and
   `ULTRON_ALLOW_CLOUD=false` all remove the cloud adapter from the candidate list *before* any
   call, so no failure path can reach it. Tested.
3. **Honest degradation.** A cloud->local fallback sets `fallback`, `degraded` and `notice`, and
   tells the local model it is degraded.
4. **One generic cloud adapter** for any OpenAI-compatible endpoint (Gemini, OpenAI, OpenRouter,
   Groq...). Provider-specific adapters (e.g. native Anthropic) are added behind `ModelAdapter`
   when needed; nothing outside `app/models` knows about providers.
5. **SQLite default, PostgreSQL later.** Phase 1 persistence needs no server. Schema/ORM are
   portable; PostgreSQL + pgvector arrive in Phase 4 with the memory work. Schema is managed only by
   Alembic (a test asserts the migration matches the ORM models).
6. **Logging never contains bodies or credentials.** Request logging records method/path/status
   only; a redaction filter scrubs bearer tokens, known secrets and key patterns; chatty libraries
   (which log SQL parameters at DEBUG) are pinned to WARNING.
7. **No Redis / queue / extra infra** — no measured need.

## Update (2026-11-10, after Phase 2)

Phase 1 findings F2 (conversation-level privacy lock), F3 (no message text in logs) and F11 (voice latency:
the plain `qwen3:4b` tag is thinking-only; default is now `qwen3:4b-instruct`) are fixed. Streaming exists
(`/v1/ws/chat`, `shared/protocols/chat_ws.md`); personality v3 is the default. See `docs/PHASE_1_VERIFICATION.md`
and `docs/PHASE_2_VOICE.md` for evidence and for the findings that remain open.

## Update (2026-11-10, style guard)

"No emoji, no exclamation marks" are mechanical, so `app/core/style.py` enforces them in the router for
every engine and both transports (a small model was measured appending emoji to refusals on 4-6 of 20
adversarial prompts). It is chunking-invariant (property-tested), rewrites nothing else, leaves code
such as `x != y` alone, and can be disabled with `ULTRON_ENFORCE_STYLE=false`. Personality evaluations
run with it OFF so they measure the model, not the filter.

## Update (2026-11-10, Phase 3)

Cloud adapter is now verified live against Groq (see `docs/PHASE_1_VERIFICATION.md`); the
"never exercised live" limitation below is out of date.

Phase 3 adds two components that live beside, not inside, the backend (spec rule 16: device-specific
execution stays separate from the backend):

```
desktop/app (PySide6)  --WebSocket/REST-->  backend  (chat, history, model router)
        |  console.sock (0600, UID-checked)
        v
desktop/agent  <--request.sock--  (Phase 6: model relay; can ask, cannot approve)
   validate -> policy engine -> approval -> audit(intent) -> execute(timeout) -> audit(result)
```

- The device agent is the only component that touches files, apps or system information. Its policy
  engine does not read model output; automatic execution is capped at P1 in code.
- Two sockets enforce who may approve: the request socket cannot `decide`. See
  `shared/protocols/device_agent.md` and `docs/PHASE_3_DESKTOP.md` (including the same-user limitation).
- There is still no connection from the model to the agent. Phase 6 adds task planning on top.

## Update (2026-11-11, Phase 4)

Long-term memory: `memories`, `projects`, `memory_events` (migration 0003), a store with pgvector search on
PostgreSQL and numpy search on SQLite, local-only embeddings (Ollama `nomic-embed-text`), per-engine prompt
building so private memories never reach a cloud model, proposals that need confirmation, secret refusal,
retention, and a desktop panel. The runtime-context block no longer says "NO memory" when memory is on.
Details, measurements and limits: `docs/PHASE_4_MEMORY.md`.

Decision 5 above ("SQLite default, PostgreSQL later") now reads: SQLite default; PostgreSQL + pgvector supported
and verified in a throwaway container; your running backend has not been moved to PostgreSQL.

## Update (2026-11-12, intelligence)

A private reasoning pass for problem-like messages (solved locally first, then the reply is written with the
working), an optional web-search tool (off by default; never in local_only chats; never when
`ULTRON_ALLOW_CLOUD=false`), and a code-checked benchmark. Measured on questions written after everything was
frozen: 62% to 88% on problem-type questions. Methods, numbers, rejected alternatives and limits:
`docs/INTELLIGENCE.md`. The "NO tools, NO web access" runtime-context line now says "web search exists but
returned nothing" or "results are below" depending on the turn, so the model cannot claim a lookup it did not do.

## Known limitations (still true)

- Complexity classification is a keyword/length heuristic, not an intent model.
- No circuit breaker: a dead engine is retried on every request.
- Context = last N messages, no token budgeting or summarisation. The v3 personality prompt is ~1,100 tokens.
- Single shared bearer token; per-device credentials and revocation are Phase 5/7. Rate limiter and usage
  counters are in-memory, single process.
- `max_tokens` is sent as-is; OpenAI reasoning models require `max_completion_tokens`.
- The cloud model is not probed in `/health/ready` (deliberate: probing costs money).
- No content moderation, no tools, no permission engine (Phase 6). Memory exists since Phase 4 (see below); its
  retrieval thresholds are soft (docs/PHASE_4_MEMORY.md).
- Open Phase 1 findings F4-F10 (NUL input, pool_pre_ping, /docs auth, failed-auth throttle, URL validation,
  file modes, body-size cap).
