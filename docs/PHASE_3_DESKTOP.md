# Phase 3 - Ubuntu desktop client and device agent

Status labels: **PASS** (verified live or by an automated test that was mutation-checked), **FAIL**,
**BLOCKED**, **NOT TESTED**. A test against a fake or an offscreen display is never labelled "live".

## 1. What was built

| Component | Path | What it is |
|---|---|---|
| Device agent | `desktop/agent/ultron_agent/` | A separate local process that owns every action on the machine: a strict tool registry, an independent policy engine, path scoping, a hash-chained audit log, and two Unix sockets. No TCP port. |
| Desktop app | `desktop/app/ultron_desktop/` | PySide6 window: streaming chat, conversation history, backend/engine/agent status, a permanent microphone indicator, voice start/stop/mute, device panel (system, apps, files, activity), approval dialogs. |
| Protocol | `shared/protocols/device_agent.md` | The agent's socket protocol. |
| Packaging templates | `desktop/packaging/*.example` | A systemd *user* unit and a `.desktop` launcher. Not installed; copy them yourself if you want them. |

Design rules from the spec that drove it: the LLM is never the security authority; the device agent
is separate from the backend; every tool has a name, description, input schema, permission level,
validation, timeout, result and audit record; no generic shell.

### Request path (every caller, no shortcuts)

```
validate arguments (pydantic, extra fields forbidden)
 -> policy engine: path scope (roots, symlinks, protected names) + permission level
 -> P2: human approves the exact action     P3: human types a confirmation phrase
 -> policy re-checked after approval (config may have changed while the dialog was open)
 -> audit "intent" record written first (mutating tools); refused if the log cannot be written
 -> execute with timeout -> audit result record
```

### Tools (13)

| Level | Tools |
|---|---|
| P0 (automatic) | `system.info`, `system.usage`, `system.storage`, `app.list`, `files.list`, `files.search`, `files.read` |
| P1 (automatic, capped here) | `app.launch` (allowlisted apps only, no arguments) |
| P2 (approval) | `files.create`, `files.append`, `files.copy`, `files.move` |
| P3 (typed confirmation) | `files.delete` (moves to the Trash; files only) |
| P4 | refused unless explicitly enabled; **no P4 tool exists** |

Deliberately absent: overwriting a file, deleting directories, permanent deletion, running programs
with arguments, shell access, package installation, anything privileged.

### Setup is opt-in

Nothing outside `~/ULTRON_Workspace` is readable or writable until you approve it
(`ultron-agent roots add PATH [--write]`), and no application is launchable until you allowlist it
(`ultron-agent apps add ID`). `roots add` refuses `/`, `$HOME`, `/etc`, `/usr` and similar. Shells and
interpreters (`sh`, `bash`, `python`, `env`, `sudo`, `curl`, ...) cannot be allowlisted.

## 2. Test evidence

| Suite | Result | Notes |
|---|---|---|
| `desktop/agent` pytest | **99 passed** | policy, path scoping, symlink races, tools, broker, audit chain, sockets, `.desktop` parsing |
| `desktop/app` pytest (offscreen Qt) | **30 passed** | against a *real* agent server thread and a scripted backend that checks the auth header |
| `desktop/voice` pytest | **76 passed** | 75 before + SIGTERM-cleanup test for the "Stop voice" button |
| `backend` pytest (SQLite) | **190 passed, 7 skipped** | 148 before; +42 for the personality renderings and the style guard (`docs/PERSONALITY_V4.md`). The skipped ones need PostgreSQL, which was not re-run (no database changes). |
| ruff | clean in all four projects | |

### Mutation check of the security tests

For each property, I broke the code and confirmed that the tests fail (`desktop/agent`):

| Broken on purpose | Result |
|---|---|
| P2 treated as automatic | caught |
| request socket allowed to `decide`/`pending` | caught |
| protected names (`.env`, `*.pem`, `.ssh`) allowed | caught |
| policy not re-checked after approval | caught |
| P3 typed confirmation not enforced | caught |
| launched apps inherit the full environment (API keys) | caught |
| `files.create` allowed to overwrite | caught |
| file contents written into the audit log | caught |
| `O_NOFOLLOW` removed from file opens | caught (needed a dedicated race test: path resolution alone masked it) |
| descriptor re-verification (`/proc/self/fd`) removed | caught |
| UID check on socket connections removed | caught (run hung, i.e. failed) |
| unexpected exception text returned to the caller | caught |
| shells allowlistable | caught |
| `files.move` overwrites | caught |
| tool timeout not enforced | caught |
| audit chain linkage not verified | caught |
| approval never expires | caught (run hung, i.e. failed) |
| P4 allowed by default | caught |
| delete becomes permanent unlink | caught |
| `O_NOFOLLOW` removed from the *directory* descriptor open | **survived**: redundant with the descriptor re-check, which the parent-swap test exercises. Left as defence in depth. |

### Live checks (real processes, throwaway config/state/Trash, nothing outside a temp tree touched)

| Check | Result | Evidence |
|---|---|---|
| Real `ultron-agent serve`, real sockets, drive every tool through the request socket; approve through the console socket | **PASS** | `docs/evidence/agent_e2e.json` |
| Out-of-root path, `../` traversal, `.env` inside a root, write to a read-only root, unknown tool, extra args: all refused | **PASS** | same file |
| Self-approval through the request socket is refused (`forbidden`) | **PASS** | same file |
| P2 create runs only after approval; denial leaves no file; P3 delete rejects a wrong phrase, accepts the right one, lands in the Trash | **PASS** | same file |
| Audit chain verifies; file content never appears in the log; log mode 0600; sockets 0600; runtime dir 0700; sockets removed on exit | **PASS** | same file |
| Real desktop window (offscreen display) + real backend + real local model + real agent process: chat reply, conversation persisted `local_only`, UI shows the lock, approval dialog -> file created, system panel populated, agent survives the UI closing | **PASS** | `docs/evidence/desktop_e2e.json` |
| `ultron-agent apps discover` against this machine's real `.desktop` files | **PASS** (70 candidates found) | run by hand |

Bugs found by the live runs and fixed: stopping the agent while a UI was connected hung until the UI
disconnected (now closes clients and cancels pending approvals; regression test added); a failed audit
write let a mutating action proceed unrecorded (now fails closed); the mic indicator and status labels
were clipped (screenshots, fixed); the apps/system panels double-unwrapped results (caught by UI tests).

## 3. What was NOT tested

- **A real Wayland/X11 session.** The window was rendered with Qt's `offscreen` platform and
  screenshotted; I have not seen it on your desktop. Dialog stacking, focus, HiDPI and the window
  manager are NOT TESTED.
- **Launching a real GUI application** through `app.launch` (tests launch `/bin/true` and a script).
  Command for you below.
- **CORRECTION 2026-11-11 (found by you running it): the "Start voice" button did not work at all.**
  It launched `python -m ultron_voice` from the app's directory, but the voice package is not installed into
  its venv, so it died instantly with `No module named ultron_voice`, and the window hid the error (it only
  reset the label to MIC OFF). My tests used a stand-in script, so they could not see it. Fixed: the process
  now starts in `desktop/voice`, and an unexpected exit shows its exit code and last output lines in the chat
  area (token scrubbed). Checked without the microphone: config, speech-recognition server, text-to-speech
  warm-up and a backend WebSocket connect all succeed from that directory. **The microphone path after that
  is still NOT TESTED.**
- **Unexplained test crash.** Connecting `voice.failed` to a bound method made the app test suite segfault in
  `~QObject` (QtNetwork deferred delete) on every run, while a lambda never did (35 passed x4). Root cause not
  found; one hypothesis (in-flight REST replies at window close) was tried and did not fix it, so it was reverted.
  Impact in the real app is unknown, at worst a crash on exit.
- (original line) **Starting the real voice pipeline from the "Start voice" button.** The button was tested against a
  stand-in script that writes the same status file and handles SIGUSR1/SIGTERM. The real microphone is
  still NOT TESTED (Phase 2 status unchanged).
- Tray icon, notifications, autostart, the systemd unit and the `.desktop` launcher (templates only).
- AppArmor confinement (the spec says "where appropriate"): **not implemented**.
- TLS (`wss://`) from the app to a remote backend; only loopback `ws://` was exercised.
- Wake word / model quality: unchanged from Phase 2.

## 4. Known limitations and honest security notes

1. **The approval boundary protects against the model path, not against malware running as you.**
   Both sockets only check that the connecting process has your UID. Any program running as your user
   could connect to `console.sock` and approve its own request. The design works when the (future)
   model relay is given `request.sock` only; closing the same-user gap needs OS confinement
   (AppArmor/seccomp profile for the agent plus a secret handed to the UI at spawn) and is not done.
2. The agent is not sandboxed: a bug in a tool is a bug with your user's privileges.
3. The audit log is tamper-*evident* for edits and deletions in the middle (hash chain), but truncating
   the end of the file is not detectable without an external anchor. It is also writable by you.
4. File reads (`files.read`) are P0 and return text to the UI. Once the model can call tools (Phase 6),
   sending file content to a *cloud* model is a P4 sensitive transmission and must be gated there; Phase 3
   has no model-to-agent link at all.
5. Path checks use resolved paths plus descriptor re-verification via `/proc/self/fd` (Linux-only).
   This closes symlink swaps between check and use; it does not defend against a hostile root user.
6. A tool that exceeds its timeout is reported as `timeout`, but the worker thread cannot be killed; a
   slow file operation may still finish afterwards.
7. `files.search` is name-only (no content search). Search results hide dot-files unless asked, and never
   list protected names.
8. The default workspace `~/ULTRON_Workspace` is created (mode 0700) the first time `ultron-agent serve` runs.
9. The app reads `ULTRON_API_TOKEN` from the environment or, for development, `backend/.env`. It never
   stores, logs or displays it, sends it only as a header, and passes it to the voice process through the
   environment (not the command line). A per-device credential model is Phase 5/7.
10. ~~Memory controls in the UI are a disabled button labelled "not available until Phase 4".~~ Superseded: the
    voice-only window has a MEM panel since Phase 4 (docs/PHASE_4_MEMORY.md).

## 5. Commands for you to run (I did not run these; they open windows or the microphone)

```bash
# 1. approve a folder and an app, then look at the window on your real desktop
A=desktop/agent/.venv/bin/python
$A -m ultron_agent roots add ~/Documents --write        # or any folder you want ULTRON to reach
$A -m ultron_agent apps add org.gnome.Calculator
$A -m ultron_agent serve                                 # terminal 1  (or press "Start agent" in the window)
cd desktop/app && .venv/bin/python -m ultron_desktop     # terminal 2
#   - Apps tab -> Calculator -> Open   (real application launch)
#   - Files tab -> New file...         (approval dialog must appear; Deny must create nothing)
#   - Files tab -> select a file -> Move to Trash   (must require typing "delete <name>")
#   - Start voice, then watch the top-right indicator: LISTENING must be red and must match what you hear
# 2. verify the audit log any time
$A -m ultron_agent audit verify
```
