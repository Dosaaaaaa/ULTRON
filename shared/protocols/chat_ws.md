# ULTRON chat WebSocket protocol (v1)

Endpoint: `GET /v1/ws/chat` (WebSocket upgrade). JSON text frames, max 64 KiB each.

**Auth:** `Authorization: Bearer <token>` header on the upgrade request. Tokens in the URL are not
accepted. A bad or missing token is refused before the socket is accepted (close code 1008).
Run the backend on loopback or behind TLS: the token and the conversation travel in clear text on plain `ws://`.

One turn at a time per socket. Rate limit counts `chat` frames.

## Client -> server

| frame | fields |
|---|---|
| `chat` | `message` (1-8000 chars), `conversation_id?` (omit to start one), `privacy?` (`default`\|`local_only`), `modality?` (`text`\|`voice`), `project_id?` (scope memory retrieval to a project) |
| `cancel` | `spoken?`: the text the operator actually heard before interrupting. `message_id?`: only when the turn already ended (the model finished before the interruption); the stored reply is replaced by `spoken`, or removed if `spoken` is empty. Answered with `amended` |

`modality: "voice"` asks for a short, plain-spoken reply (no markdown) capped at `voice_max_output_tokens`.

## Server -> client

| frame | fields |
|---|---|
| `start` | `engine{provider,model,tier}`, `fallback`, `degraded`, `notice?`, `privacy_locked`, `memory`. Sent once, immediately before the first `delta` |
| `delta` | `text`: next piece of the reply |
| `end` | `conversation_id`, `message_id?`, `reply`, `interrupted`, `usage?`, `privacy_locked`, `notice?`, `memory?` |
| `amended` | `message_id`, `reply`: the stored reply now equals what was heard (`reply` is empty if it was removed) |
| `error` | `code`, `message`, plus `attempts?`, `partial?`, `conversation_id?` |

Error codes: `invalid_request`, `too_large`, `busy`, `rate_limited`, `not_found`,
`models_unavailable` (nothing saved), `stream_failed` (the partial reply was saved).

## Semantics

- Fallback to another engine happens only before the first `delta`. After output has started, a failure
  produces `stream_failed`; the client is never silently switched to a different model mid-answer.
- `cancel` stops generation upstream. The saved assistant message is `spoken` if provided, else the text
  generated so far; if that is empty nothing is saved. The server then sends `end` with `interrupted: true`.
- If the client disconnects mid-turn the server cancels the same way and keeps the generated text.
- Once any turn in a conversation is `local_only`, the whole conversation is local-only from then on.
  Later turns are processed on-device and `notice` says so.

## `memory` (start / end frames and the REST `/v1/chat` response)

```json
{"enabled": true,
 "used": [{"id": "...", "kind": "semantic", "content": "..."}],
 "note": null,
 "remembered": {"saved": true, "memory_id": "...", "reason": null}}
```

- `used`: the memories that reached the engine that is answering. A `local_only` memory is never listed
  (and never sent) when the answering engine is the cloud; the list is built for the engine that answered,
  including after a cloud-to-local fallback.
- `note`: set when memory search could not run for this reply (embedder down); the conversation continues.
- `remembered`: present only when the message was a "remember that ..." command. `saved: false` has a
  `reason` (for example "it looks like a credential or secret"); the model is told the same, so it cannot
  claim a save that did not happen. A turn that ends without saving anything also removes the memory.
- `enabled: false` when memory is switched off (`ULTRON_MEMORY_ENABLED=false`).

## `web` report (start frame and REST reply)
`{enabled, used, provider, query, sources:[{title,url}], withheld, trimmed, note}`. `withheld` counts search results
dropped as unsafe (text aimed at the model, or a link to an address inside the operator's machine/network);
`trimmed` counts results kept as title + link only. Web search is off unless a provider is configured.
