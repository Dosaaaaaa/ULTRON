# Cloud budget limiter

Date: 2026-11-11. Code: `backend/app/models/budget.py`. Tests: `backend/tests/test_budget.py` (38).

## Why it is not key rotation

Groq documents its limits as **organization-level** (<https://console.groq.com/docs/rate-limits>);
projects can only be set lower than the organization (<https://console.groq.com/docs/projects.md>).
Several API keys from one account therefore share one bucket: 200,000 tokens/day, 8,000/minute,
1,000 requests/day for the configured model. Rotating keys would add no capacity, and using separate
accounts to get around a quota is evading the provider's limits, so ULTRON does not do it. Use one key.

## What it does

| Behaviour | Detail |
|---|---|
| Rolling ledger | tokens and requests over the last 24 h (not a midnight reset), plus a 1-minute window |
| Admit before sending | a request that would not fit is refused locally; the router answers from the local model with no wasted round trip; the attempt is recorded as `budget` |
| Reservation | prompt estimate + `max_output_tokens` is held while in flight, then replaced by the provider's real usage; failures that spent nothing give it back |
| Short shortfalls | a per-minute wait of at most 4 s is waited out; longer ones go local |
| Provider is the authority | on a 429 it reads Groq's `Used N` and `Retry-After`, raises its ledger if other programs spend the same quota, and pauses cloud calls for the stated time |
| Persistence | `data/cloud_budget.json`, mode 0600, survives restarts; a corrupt file or future-dated entries cannot block the budget |
| Privacy | `local_only` requests never reach it (the cloud adapter is not even a candidate) |
| Visibility | `/v1/health/ready` -> `cloud_budget`; desktop status strip: `cloud: 43% of day · 86K/200K tokens` or `PAUSED 5m · using local` |

Settings (`.env.example`): `ULTRON_CLOUD_LIMIT_TOKENS_PER_DAY`, `..._PER_MINUTE`, `..._REQUESTS_PER_DAY`,
`ULTRON_CLOUD_BUDGET_SAFETY` (default 0.9). 0 means unknown: only the provider's 429 guards then.

## Evidence

- Backend 229 passed / 7 skipped (needs PostgreSQL); desktop app 31 passed; ruff clean.
- Mutation check, 13 deliberate breaks: 12 caught. The survivor (daily wait special case) was equivalent and removed.
  An earlier mutation script crashed before restoring the file; I noticed from the failing suite and restored it from a backup.
- Live: the real backend (`uvicorn`, real `.env`) reports `cloud_budget` with 0/200000 on `/v1/health/ready`.
  **No Groq tokens were spent**, so the live 429 path and the estimate-vs-real-usage drift are **NOT TESTED** against Groq.
- Also found and fixed: a fresh checkout failed to start because migration ran before the SQLite directory was created.

## Limits to know

- The ledger is an estimate. Other programs (my evaluation scripts, another machine) spend the same quota and are
  only noticed when Groq answers 429.
- `CHARS_PER_TOKEN = 3.2` is a guess until compared with real usage numbers.
- Usage from separate processes (the evaluation harness) is not shared with the backend's ledger file.

## Other providers

A second provider is a separate organization with its own quota and terms, so it is a legitimate way to get
more headroom. The router takes one cloud adapter today; a fallback chain (Groq, then provider B, then local)
is not built. `github.com/freellms/free-llm-api-keys` is a one-file README whose every "Get Key" link goes to an
unvetted site (freellms.org); it contains no keys and its Groq figures differ from what was measured for this
model. Use the providers' own sites for signup and limits.
