# Plan: stronger ULTRON, desktop in the background, Android phone app

Written 2026-11-12. Status: approved 2026-11-12. Step 0 (code-review fixes) and step 1 (security module) DONE; steps 2, 3, 4 and 5 are built and tested; what was NOT verified is listed in docs/PHASE_5_RESULTS.md.

User choices (2026-11-12): phone app in native Kotlin; calls = dial on command with an on-screen confirm;
connection = home Wi-Fi only to start; order = plan, then foundations, then the phone app.

## 1. What the three reference repos are, and what may be taken

| repo | licence | what it is | use |
|---|---|---|---|
| SAGAR-TAMANG/ultron-by-sagar-builds | MIT | Next.js/Three.js orb UI, no voice code | already adopted (blue orb) |
| AbuZar-Ansarii/Ultron | **no LICENSE file** (README badge says MIT) | 10k-line Python desktop assistant, 51 "tools", Gemini Live, Telegram bot | **ideas only, copy no code**: without a licence file the code is not safely reusable |
| open-jarvis/openjarvis | Apache-2.0 | local-first agent framework: ~1,400 Python files, Rust core, 30+ channels, scheduler, MCP, security layer, evals | patterns re-implemented small; if any code is copied it keeps its notice (NOTICE file) |

Cloned shallow into the session scratchpad only (not into the project). Nothing from them was run.

### Rejected on purpose (conflicts with the standing safety rules)
- AbuZar "Prometheus": the model writes Python and hot-loads it into the running process. Self-modifying code with
  the user's permissions is the opposite of a permission design.
- Ghost Pilot (mouse/keyboard takeover), bounty hunter, network scanner/auditor, face-ID sentry, camera gesture
  control: surveillance or offensive tools, or hidden sensors.
- 312 `subprocess`/`exec` call sites with no common policy layer.
- OpenJarvis: the Rust extension, 30 chat channels, the learning/mining loops and the curl-pipe-bash installer. Far
  more surface than a personal assistant on a 4 GB GPU needs.

### Taken as patterns (smaller re-implementations, each with tests)
| from | idea | where in ULTRON |
|---|---|---|
| OpenJarvis `security/injection_scanner`, `credential_stripper`, `ssrf`, `rate_limiter` | scan untrusted text (web results, phone notifications) for injection; strip secrets from logs/prompts; block private-address fetches; per-client rate limits | `backend/app/security/` used by web search, phone relay, tool layer |
| OpenJarvis `security/capabilities`, `tools/approval_store` | named capabilities per client + approval records | the Phase 6 permission design; phone and desktop get separate capability sets |
| OpenJarvis `scheduler` | reminders and scheduled briefings | `backend/app/scheduler` (SQLite-backed, survives restarts) |
| OpenJarvis `sessions/compression` | summarise old turns so long chats keep context cheaply | reduces prompt tokens on long conversations |
| OpenJarvis `mcp/` | standard tool protocol | later, only after capabilities exist |
| AbuZar `confirm.py`, `undo.py` | confirm-before-act and undo for file actions | already covered by the agent's approval dialogs; add undo for moves |
| AbuZar Chronos | proactive status briefing | scheduled briefing pushed to desktop and phone |
| AbuZar `reminder.py`, `quick_timer.py`, `weather_report.py` | everyday tools | after the permission layer, as read-only/low-risk tools |

## 2. What the phone app will and will not do

Kotlin, Jetpack Compose, one foreground service (persistent notification, restarts on boot, battery-optimisation
exemption requested by the user, not forced).

Will do:
- Pair once with a short-lived code shown in the desktop window; the phone then holds its own revocable token.
- Stay connected to the backend over the home network (WebSocket, auto-reconnect) and receive reminders,
  briefings and alerts as notifications.
- Chat and push-to-talk voice (microphone only while the user holds/taps; visible indicator). Always-listening wake
  word is OFF by default and, if ever added, is a separate explicit toggle.
- **Calls**: "call Asha" -> backend returns a `call` proposal -> phone shows the contact and number -> the user taps
  Confirm -> the phone dials. Contacts are read on the phone only with the READ_CONTACTS permission; names are not
  uploaded to the backend or the cloud.
- Announce incoming callers (name or number) and, if the user enables it, selected notifications. Per-app opt-in.

Will not do (Android/platform or safety limits, stated up front):
- Speak or inject AI audio into a normal phone call, or record calls. Android does not allow it without root and
  recording has legal consent rules.
- Dial without the user's tap. The "no confirm" option was not chosen.
- Read all notifications by default; send contacts, SMS or call logs to the cloud.
- "Do everything": every capability is an explicit, listed, revocable permission.

## 3. Backend additions (shared by desktop and phone)

1. Device registry and pairing: `/v1/devices/pair` (one-time code), per-device token, revoke, last-seen. Tokens are
   stored hashed.
2. A push channel (WebSocket) for server-initiated messages, with an outbox so nothing is lost while the phone is
   asleep.
3. Capability table per device (chat, notify, call-propose, read-contacts-locally...). Default: chat + notify only.
4. Security module (injection scan, SSRF guard, rate limit, credential strip) applied to every untrusted input.
5. Scheduler (reminders/briefings), session compression.
6. LAN transport: bind to the LAN interface only when pairing is enabled; pairing code + per-device token;
   TLS with a self-signed certificate pinned by the phone at pairing (plain HTTP on a Wi-Fi is sniffable).

## 4. Desktop in the background

- Tray icon + close-to-tray; the window is optional, the backend/voice/agent keep running.
- A **user-level** systemd unit (`~/.config/systemd/user/`) to start the backend at login. This writes a config file
  in your home, so it will be done only after you say yes. No system-wide changes.
- The orb page is CPU heavy: when the window is hidden the page is suspended, which is also the main optimisation.

## 5. Optimisation

The `claude-token-optimizer` skill is **not installed** in this environment ("Unknown skill"), so it was not used.
Optimisation will be measured instead: prompt tokens per turn (sessions compression, memory block budget), startup
time, orb CPU (suspend when hidden, lower frame rate when idle), and backend p50 latency, each before/after.
If you have that skill's folder, put it in `~/.claude/skills/` and say so; I will run it then.

## 6. Order of work (each step ends with tests and your approval)

0. Code review of the whole repo (running) -> fix confirmed findings first.
1. DONE. `backend/app/security/`: `injection.py` (prompt-injection scanner, HIGH drops an item, MEDIUM keeps title+link
   only; measured on 20 ordinary snippets: 0 flagged, 11 of 11 hand-written attacks caught; known false positive:
   headlines starting "System:") and `netguard.py` (public-address check incl. odd IP spellings, IPv6-mapped, rebinding
   check for a future fetch tool). Wired into web search (`screen_results`); the reply's `web` report shows
   `withheld`/`trimmed`. Credential stripping (`app/core/logging.redact`, `app/memory/guard`) and per-client rate
   limiting (`SlidingWindowLimiter`) already existed and are reused, not duplicated. 68 new tests.
2. Device registry, pairing, push channel, capability table, scheduler, session compression.
3. Desktop: tray, hide-suspends-orb, optional user-level service (asks first).
4. Kotlin phone app: pairing, foreground service, notifications, chat, push-to-talk, call-with-confirm.
5. Measure and report the optimisation numbers.

What cannot be verified by me: the phone app on a real handset (an emulator/`adb` only if you connect one),
battery use over a day, real calls (the call step would dial a real number; only tested with a test number you give
me), and anything on your real screen.
