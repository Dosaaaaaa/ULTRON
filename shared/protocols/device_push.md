# Phone ↔ backend protocol (pairing, push, chat, calls)

All traffic is on the home network. Every request carries `Authorization: Bearer <token>`; the WebSocket
sends it in the upgrade request's header, never in the URL. The backend refuses browser `Origin`s.

## Pairing (once)
1. Owner (desktop window or `curl` with the owner token): `POST /v1/devices/pairing-code`
   `{"capabilities":["chat","notify","call","reminders"]}` (default `["chat","notify"]`)
   → `{"code":"ABCD-EFGH","expires_at":...}`. Valid 5 minutes, single use.
2. Phone: `POST /v1/devices/pair` (no token) `{"code":"ABCD-EFGH","name":"Pixel 8","kind":"phone"}`
   → `{"device_id","token","capabilities","name"}`. **The token is shown once**; store it in the Android
   Keystore-backed encrypted storage. 5 attempts a minute for everyone together.
3. Owner can list (`GET /v1/devices`) and revoke (`DELETE /v1/devices/{id}`); a revoked token fails at once.

## What a device token can use
`POST /v1/chat` and `WS /v1/ws/chat` need `chat`. `WS /v1/ws/device` needs `notify`. Everything else
(conversations, memories, devices, reminders, usage) is owner-only and returns 403 to a device.

## Push socket `WS /v1/ws/device`
Server → phone: `{"type":"push","id","kind","payload","created_at"}` with `kind`:
- `notify` `{title, body}` · `reminder` `{id, text, due_at}`
- `call_proposal` `{request_id, kind:"name"|"number", target}`: show who/which number and a Confirm button;
  dial ONLY after the operator taps Confirm. Resolve a name against the phone's own contacts; the
  contacts never leave the phone.
Phone → server: `{"type":"ack","id"}` after showing an item; `{"type":"ping"}` → `{"type":"pong"}`.
Unacknowledged items are re-sent on every reconnect (at-least-once: de-duplicate on `id`).
Errors: `{"type":"error","code":"too_large|invalid_request|rate_limited"}`.

## Chat actions
A chat reply (REST body, WebSocket `start` frame) carries `actions: [{type:"call", request_id, target,
status:"awaiting_confirmation"|"no_phone", devices}]` when the message was a call command.


## Ask-before-acting switches (`GET` / `PATCH /v1/preferences`, owner or a phone with `chat`)
`{"call_confirm": true, "reminder_guess": false}`; PATCH takes any subset (strict booleans, unknown keys are 422).
- `call_confirm` true (default): a call proposal means "show a confirmation". false: the push payload carries
  `"confirm": false` and the phone places the call itself after a 3 s cancellable countdown, only for a full number or
  exactly one matching contact (anything unclear still asks). The chat action status is then `placing`.
- `reminder_guess` false (default): an unclear reminder time makes ULTRON ask. true: sensible defaults (09:00 for a day,
  20:00 for "tonight", the next occurrence of a bare "at 5"); a missing or impossible time is never invented.
