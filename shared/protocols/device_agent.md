# ULTRON device agent protocol (v1)

Transport: newline-delimited JSON over **Unix domain sockets**. No TCP port is ever opened.

| socket | path | who connects | allowed ops |
|---|---|---|---|
| request | `$XDG_RUNTIME_DIR/ultron/request.sock` | anything that wants an action performed (a future model relay) | `tools`, `status`, `call` |
| console | `$XDG_RUNTIME_DIR/ultron/console.sock` | the human's UI only | request ops + `pending`, `decide`, `audit_tail`, `audit_verify`, and receives events |

The directory is mode 0700, the sockets 0600, and every connection's kernel-reported UID
(`SO_PEERCRED`) must equal the agent's UID. A requester can *ask* for a risky action but cannot
approve it: `decide` on the request socket answers `forbidden`.

Frames are JSON objects, max 4 MiB per line.

## Requests (client -> agent)

```
{"id": 1, "op": "tools"}
{"id": 2, "op": "status"}
{"id": 3, "op": "call", "tool": "files.create", "args": {"path": "/abs/path", "content": "..."}}
{"id": 4, "op": "decide", "approval_id": "...", "approve": true, "confirmation": "delete a.txt"}   # console only
{"id": 5, "op": "pending"}                                                                          # console only
{"id": 6, "op": "audit_tail", "n": 50}                                                              # console only
{"id": 7, "op": "audit_verify"}                                                                     # console only
```

## Responses (agent -> client), matched by `id`

```
{"id": 3, "ok": true,  "tool": "files.create", "result": {...}, "decision": "auto|approved"}
{"id": 3, "ok": false, "error": {"code": "...", "message": "..."}}
```

A `call` that needs a human is answered only after the decision (or timeout), so a client simply
waits. Error codes: `unknown_tool`, `invalid_arguments`, `denied` (policy, path scope, protected
name, or the operator refused), `approval_timeout`, `confirmation_mismatch` (from `decide`),
`unknown_approval`, `too_many_pending`, `audit_unavailable`, `timeout`, `exists`, `not_found`,
`not_allowlisted`, `not_installed`, `exited_immediately`, `binary_file`, `forbidden` (op not allowed
on this socket), `unknown_op`, `bad_json`, `failed`, `internal`.

## Events (agent -> console connections)

```
{"event": "approval_requested", "approval": {"id", "tool", "level", "summary", "args",
                                             "confirmation_required", "requested_by", "expires_in_s"}}
{"event": "approval_resolved", "id": "...", "outcome": "approved|denied|timeout|cancelled"}
{"event": "audit", "record": {...}}
```

## Permission levels

| level | meaning | handling |
|---|---|---|
| P0 | read approved information | automatic |
| P1 | low-risk, reversible (open an allowlisted app) | automatic |
| P2 | create/append/copy/move files | human approval of the exact action |
| P3 | destructive (delete -> Trash) | human must type the confirmation phrase |
| P4 | sensitive transmission, financial, security-critical | refused unless explicitly enabled; no P4 tool exists yet |

Automatic execution is capped at P1 in code (`levels.MAX_AUTOMATIC`); configuration cannot raise it.

## Tools (v1)

`system.info` `system.usage` `system.storage` (P0) - `app.list` (P0) `app.launch` (P1) -
`files.list` `files.search` `files.read` (P0) - `files.create` `files.append` `files.copy`
`files.move` (P2) - `files.delete` (P3, moves to the Trash). Run `ultron-agent tools` for schemas.

## Guarantees enforced by the agent, independent of the caller

- Paths must be absolute and resolve (symlinks followed) inside an approved root; writes need a
  writable root. Files are opened through verified directory descriptors with `O_NOFOLLOW`.
- Names such as `.ssh`, `.gnupg`, `.env*`, `*.pem`, `*.key`, `id_rsa*`, `credentials*` are refused
  even inside an approved root, and no approval can override that.
- Nothing is ever overwritten; delete is a move to the Trash; directories are never deleted.
- Apps are launched from an allowlist, with no caller-supplied arguments and a scrubbed environment.
  Shells, interpreters and similar programs cannot be allowlisted.
- Every call (including rejected ones) is appended to a hash-chained audit log; mutating calls are
  recorded *before* they run and are refused if the log cannot be written. File contents are never logged.
