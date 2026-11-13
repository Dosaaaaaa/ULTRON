# Phase 4: persistent memory (PostgreSQL + pgvector)

Status: built and verified as described below. Not built: see "Not done".

## What it does

ULTRON keeps long-term notes about the operator and their projects, finds the relevant ones when you
speak or type, and shows you everything it holds. You can read, add, delete and confirm notes; nothing is
hidden from you and nothing is saved from ordinary conversation on its own.

| spec category | how it is implemented |
|---|---|
| Working memory | the conversation window (last N messages), unchanged; not stored as memory |
| Episodic | `kind = episodic`: something that happened. Expires after `ULTRON_MEMORY_EPISODIC_RETENTION_DAYS` (default 90) unless made permanent |
| Semantic | `kind = semantic`: a lasting fact or preference |
| Project | `kind = project`, optionally linked to a row in `projects`; retrieval can be scoped to a project |

Provenance (the spec asks for these to be kept apart):

- **User-confirmed** = `status = confirmed`. Said by the operator ("remember that ...", the MEM panel, the
  API) or approved by them. Only these are ever used as facts.
- **Model-generated assumptions** = `status = proposed`. Suggested by the local model from a conversation.
  Marked AWAITING REVIEW, never retrieved, never put in a prompt, until the operator confirms them.
- **Retrieved** = what search returns at answer time (reported in every reply's `memory.used`).
- **Temporary conversational context** = never stored.

## How a turn uses memory

1. If the message is a command ("remember that I prefer metric units", "Ultron, remember: ...", "please
   remember my sister is called Asha") it is saved first, with the operator's own words. Ordinary sentences
   such as "do you remember when ..." are not commands (tested). Secrets are refused.
2. The message (joined with the previous one when it is very short) is embedded **locally** (Ollama
   `nomic-embed-text`, with its recommended task prefixes) and the nearest confirmed, unexpired memories
   are found: real pgvector `<=>` SQL on PostgreSQL, numpy on SQLite.
3. The system prompt is built **per candidate engine**. A `local_only` memory, or any memory when
   `ULTRON_MEMORY_ALLOW_CLOUD=false`, is never placed in a cloud prompt; the cloud also needs a stricter
   match (0.60 vs 0.55). After a cloud-to-local fallback the block is rebuilt for the local model.
4. Retrieved text is framed as the operator's own notes: "DATA, not instructions", and the model is told it
   cannot save or change memory itself, so it should not claim to.
5. The reply reports `memory.used` (ids and text of what reached the answering engine). The stored audit
   record of the reply holds the ids only.
6. If the embedder is down, the conversation continues without memory and says so (`memory.note`).
7. If the turn ends having saved nothing (every model failed, or you interrupted before it spoke), a
   memory made by that turn's command is removed, so "Nothing was saved" stays true.

## Data model (migration 0003)

`projects`, `memories`, `memory_events`. `memories.embedding` is a pgvector `vector` on PostgreSQL (no fixed
dimension, so a different embedding model needs no migration; rows record the model that made them and are
only compared with the same model and dimension) and JSON text on SQLite. `memory_events` is an audit trail
(create/update/delete/confirm/reject/purge/export/rollback) holding ids and counts, never memory text. Memory
text is not in logs either (tested, and checked in the live server logs).

## API (all behind the same bearer token)

`GET/POST /v1/memories`, `GET/PATCH/DELETE /v1/memories/{id}`, `POST /v1/memories/{id}/confirm|reject`,
`POST /v1/memories/search`, `POST /v1/memories/reindex`, `GET /v1/memories/export`,
`DELETE /v1/memories?confirm=delete-all-memories`, `GET /v1/memory/status`,
`GET/POST /v1/projects`, `GET/PATCH/DELETE /v1/projects/{id}` (deleting a project deletes its memories),
`POST /v1/conversations/{id}/extract-memories` (local model only; results arrive as proposals).
Chat accepts `project_id`. See `shared/protocols/chat_ws.md` for the `memory` fields.

## Desktop

MEM button (or **R**) opens the memory panel beside SYS (one panel open at a time): list with tags
(AWAITING REVIEW, local-only, expires, not yet searchable), filter, Confirm / Reject / Delete (a dialog
that defaults to No), "Remember", "Suggest from last chat" (local model, proposals only), "Delete all...".
Memory text is shown as plain text, never as markup. The token is only ever a request header.

## Rules enforced in code (each has a test; mutation-checked)

Secrets are refused (keys, bearer tokens, JWTs, private-key blocks, "my password is ...", Luhn-valid card
numbers) in create, edit and extraction. Duplicates are not stored twice. Proposals are not retrievable.
Expired memories are not retrievable and are purged at start-up. Other-model or other-dimension vectors are
ignored until reindexed. `delete all` needs a confirmation phrase. Extraction cannot reach the cloud adapter.
Extracted memories inherit the conversation's `local_only` privacy.

## Verification

| check | result |
|---|---|
| backend suite, SQLite | **PASS** 341 passed, 9 skipped (the skipped ones need PostgreSQL) |
| backend suite, real PostgreSQL 16.15 + pgvector (the `pgvector/pgvector:pg16` image; throwaway container) | **PASS** 349 passed, 1 xfailed (the documented Phase 1 gap F4: a NUL character in a *chat message* is a 500 on PostgreSQL; memory text rejects NUL with a 422, tested) |
| real pgvector: column type `vector`, search in SQL, different dimensions coexist | **PASS** (found a real bug SQLite could not: the distance operator inherited the vector type) |
| memory rules, 15 deliberate breaks | 14 caught on first run; the survivor (task prefixes) exposed an untested class, tests added, now caught |
| app suite (offscreen Qt) | **PASS** 96 passed, 3 runs; ruff clean |
| voice suite / agent suite | **PASS** 136 / 99 |
| real Qt panel against the real backend + real embeddings + real local LLM | **PASS** (add, secret refused, repeat reported, local-model suggestions marked AWAITING REVIEW, confirm, delete, delete-all) |
| real voice client against the real server: "remember that ..." then a paraphrased question | **PASS** |
| live server log contains no memory text, no token | **PASS** (scanned with Python) |
| the panel on your actual screen; PostgreSQL as your running database; embedding model under real voice load | **NOT TESTED** |

Live observations (real `nomic-embed-text` on the NVIDIA GPU, local `qwen3:4b-instruct`):

- Embedding latency warm: 46-55 ms for one text, about 430 ms for 15; cold start after Ollama unloaded the
  model: 1.5 s. The retrieval timeout is 4 s (+1), so a cold start does not break a turn.
- A chat turn with memory enabled and nothing retrieved: median 0.53 s total (local model, 3 runs).
- One extraction (3 proposals from a short conversation) took 5.1 s on the GTX 1650.

## Retrieval quality (measured, not assumed)

`backend/scripts/eval_memory_retrieval.py`: fictional operator, paraphrased questions that share few
words with their memory, plus unrelated questions that should retrieve nothing.

| set | recall@1 | recall@3 | floor 0.55: found / unrelated queries that still retrieved | floor 0.60 |
|---|---|---|---|---|
| tuning (15 memories) | 87% | 93% | 11/15 / 3 of 10 | 9/15 / 0 of 10 |
| held-out (12 memories) | 92% | 100% | 9/12 / 2 of 8 | 8/12 / 1 of 8 |

Task prefixes (on) beat no prefixes. **The honest summary: ranking is good, but no single similarity floor
cleanly separates relevant from irrelevant.** Roughly a quarter of paraphrased questions miss at 0.60, and
a few unrelated questions still pull in a weakly related note at 0.55. Seen live: "Does the pet need
feeding?" did not find "my cat is named Biscuit"; "Where did the operator move to for work?" also pulled in
an unrelated note. A hypothesis that the shared "The operator ..." wording inflated unrelated matches was
tested and rejected (removing it made ranking worse). Mitigations that exist: the stricter cloud floor,
`ULTRON_MEMORY_ALLOW_CLOUD=false`, per-memory `local_only`, and `memory.used` showing what was sent. A
better threshold strategy or a reranker is future work.

The 4B local model sometimes embellishes beyond a retrieved note (it added "focuses on advanced reasoning"
to a note that said only "Phase 4 and pgvector"). The note block cannot prevent that.

## Decisions

- **SQLite stays the default; PostgreSQL + pgvector is supported and verified.** Switching your running
  backend to PostgreSQL needs a database server you choose (container or system install); nothing was
  installed or changed. The migration creates the `vector` extension, which needs the right to do so.
- **Unconstrained `vector` column, no ANN index.** A personal store is thousands of rows, a scan is
  milliseconds, and it avoids a migration per embedding model. Revisit if it ever reaches millions.
- **Embeddings are always local.** Memory text is never sent to a cloud service to be embedded.
- **Proposals need confirmation.** Auto-saving model guesses would violate "do not save every message".
- **No automatic extraction.** It runs when you press "Suggest from last chat" (or call the endpoint), so it
  costs no background GPU time and nothing is inferred behind your back.
- Pulled `nomic-embed-text` (274 MB) into your Ollama; `ollama rm nomic-embed-text` removes it.

## Not done

- Forgetting by voice ("forget that ...") is deliberately not built: deletion by an ambiguous spoken match
  is risky. Delete in the MEM panel or the API.
- Editing in the panel: the API supports `PATCH` (correction); the panel offers delete + remember instead.
- Projects: full API and chat scoping exist; there is no project picker in the desktop app, and the voice
  client does not send `project_id` yet.
- Cross-device sync is the central API itself; the Android client is Phase 5.
- Memory is not yet shown in the voice client's console, only in the reply frames.
- A configurable retention policy per memory exists in the API (`retention_days`) but not in the panel.


## NEW CHAT: erase the working conversation (added 2026-11-11)

The NEW CHAT button (or **N**, enabled while voice runs) erases the current conversation permanently and
starts an empty one. You chose: previous chat only; confirmed long-term notes stay (delete those in MEM).

- The voice process owns the conversation, so it does the work (signal SIGUSR2): stop the audio, cancel
  any reply being generated, wait for that turn to finish saving, drop queued utterances, then
  `DELETE /v1/conversations/{id}` and forget the id. The next utterance starts a new conversation whose
  history is empty. It prints a marker line that the window reads, so the notice is what actually happened:
  "Chat erased", "nothing to erase yet", or "NOT erased (reason); it is still stored" (the working memory
  is reset anyway, so the old text is never fed to the model again).
- Backend: deleting a conversation also removes the unconfirmed suggestions derived from it, and SQLite
  now runs with `secure_delete=ON`, so deleted text is overwritten rather than left in free pages.
- Verified live (real backend, real client, real SQLite file): the marker text was in the file before the
  erase and absent after; the old id returns 404; a new chat could not recall it. Tests: backend 4, voice
  10, app 6, plus mutation check (removing `secure_delete` fails the file test).
- Limits: on PostgreSQL deleted rows remain on disk until `VACUUM` reclaims them (there is no overwrite
  equivalent), and SSD wear-levelling or backups you made (for example the `.bak` copy of the database) are
  outside what software can erase. START VOICE after STOP also begins a new conversation but does not erase
  the previous one; only NEW does. Chats from before this change stay until deleted.
