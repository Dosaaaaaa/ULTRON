# Phase 5 results (2026-11-12): phone, background mode, measurements

Everything here was run; what was NOT verified is listed at the end. See `docs/PLAN_PHASE_5.md` for the plan and
`shared/protocols/device_push.md` for the protocol.

## Built
**Backend** (`backend/app/devices/`, migration `0004_devices`, additive: three new tables)
- Pairing: owner makes a single-use code (5 min); the phone swaps it for its own token (shown once, only the SHA-256 is
  stored). 10 devices max; 5 pairing attempts a minute for everyone together. Revoking closes the phone's open socket.
- A device token reaches only `POST /v1/chat`, `WS /v1/ws/chat` (needs `chat`) and `WS /v1/ws/device` (needs `notify`).
  Everything else is owner-only (403 to a device).
- Push with an outbox (acknowledged items only are removed; re-sent on reconnect), reminders (stored, survive restarts,
  delivered when due; wait up to 24 h for a device), notify API, and calls: "call Asha" / "dial 555 0100" asks the
  phone to show a confirmation. ULTRON never dials; the model is told so (`actions` in the reply).
- Optional TLS fingerprint in the pairing response for the phone to pin (`make_lan_cert.sh`, you run it).
- History budget (`ULTRON_HISTORY_CHAR_BUDGET`, default 8000 characters): older turns drop out of the prompt, the stored
  chat is untouched.

**Desktop**: tray icon, close-to-tray (only when the desktop has a tray; otherwise closing quits as before), orb page
frozen while the window is hidden, optional user-level service example + LAN certificate script in `desktop/packaging/`
(nothing installed by me).

**Android** (`android/`, native Kotlin + Compose): pairing screen, always-on foreground service (specialUse), boot
restart, reminder/notify/call notifications, call confirmation screen (contacts searched on the phone only, proposals
expire after 5 minutes, dials only after a tap on Call), chat, hold-to-talk (Android SpeechRecognizer, mic only while
held), certificate pinning (the one pinned self-signed certificate only), https/wss only (debug build allows
`http://10.0.2.2` for the emulator).

## Verified
| check | result |
|---|---|
| backend suite | 652 passed, 9 skipped (SQLite); ruff clean |
| agent / voice / desktop app suites | 115 / 151 / 110 passed; ruff clean |
| Android JVM tests (real TLS + real WebSocket against a local test server) | 25 passed |
| deliberate breaks (code broken on purpose, tests must fail) | backend devices/calls/revoke/history, desktop orb freeze, Android pin/dedupe/expiry: all caught in the end. Three first survived and were fixed: acknowledging another device's item (test raced), call-proposal expiry (test used the constant itself), the history budget (end-to-end test could not tell). The revoke check is caught only as a hang (the test waits for a close that never comes), not as a fast failure. |
| Android lint | 0 errors (11 warnings; the custom trust manager warning is the pin, covered by tests that refuse any other certificate) |
| real emulator (Android 15, isolated temporary AVD, since deleted) against a real backend over http://10.0.2.2 | app installed and launched; paired; foreground service ran; the backend saw it connected; a notification, a reminder (fired by the scheduler) and a call proposal ("call 555 0100" by chat) all arrived as notifications; tapping the call notification opened the confirmation screen; the phone reconnected by itself after the backend restarted; revoking from the server closed its connection |
| bugs found by actually running it | text invisible (dark on dark) and drawn under the status bar; debug network config in the wrong folder; no reconnect after a polite server close (found by the TLS/WebSocket tests); foreground-service type on Android < 14 (found by lint) |

## Measurements (same machine; software WebGL, so a real GPU will differ)
- Orb page CPU, offscreen with software rendering: **175% of one core while visible, 1% while hidden** (frozen).
- Prompt size, synthetic 30-turn chat with 600-character replies: turn 30 = **10,825 characters with the budget vs
  12,665 without (-15%)**. The fixed part (personality + context) is about 3,100 characters, so long chats gain most.
- Backend cold start with a fresh database and all four migrations: **1.4 s** (3 runs).
- Android: debug APK 10.8 MB; release APK (R8, unsigned) 1.7 MB.
- `claude-token-optimizer`'s own estimate (98%) counts documents that Claude Code does not load at start; the real
  session-start cost was not measured.

## NOT verified
- A real phone, a day of battery use, Doze/vendor battery killers (the app asks the user to exempt it; OEMs differ).
- Real TLS from a real phone to your computer over Wi-Fi (only tested against a local test server and plain http on
  the emulator).
- Real calls: nothing was ever dialled. The Call button was not pressed. Contact lookup on the phone was not run
  against real contacts (only unit-tested).
- Hold-to-talk speech recognition and spoken replies (emulator has no audio/mic).
- The minified release build (it compiles; it was not installed or run, and needs signing).
- Your real database (a backup is advised before the first start: migration 0004 adds three tables), PostgreSQL
  for the new tables, the desktop tray on your real desktop (GNOME needs the AppIndicator extension), and the orb
  CPU on your real GPU.
- The device-token capability model is verified, but a stolen phone token can chat and use the cloud budget until it
  is revoked; revoke it in the desktop app / `DELETE /v1/devices/{id}`.


## Follow-up (same day): reminders by chat, TEL panel, installed on the real phone
- **Reminders by chat or voice** (`backend/app/devices/remind_cmd.py`): "remind me in 20 minutes to call the dentist",
  "remind me tomorrow at 9am about the invoice", "set a reminder for 9am tomorrow: pay rent". Plain parsing, no model;
  a time that is not clear ("at 5", "tomorrow" with no clock time, a time that has passed, "in 5 seconds") creates
  NOTHING and the model is told to say what to say instead. A failed turn rolls its reminder back. 36 tests; three
  deliberate breaks, all caught.
- **TEL panel in the desktop window** (key P): pairing code with chosen powers, paired phones with state, remove (asks,
  defaults to No), warns when TLS is not set up. 12 tests against a stand-in backend.
- **Installed on the real phone** (Motorola Edge 50 Fusion, Android 16) over wireless debugging with the user's
  permission: install succeeded and the app launched. The screen was locked, so its UI there was NOT seen.
- Tests now: backend 688 (+9 skipped), app 121, agent 115, voice 151, Android JVM 25.


## Final product pass (same day)
- `bin/ultron` (doctor, start, stop, status, pair, backup, service): 8 tests against a real server and real TLS.
- Desktop: live captions (what you said / what ULTRON answered; hidden by `--hide-transcripts`), status dots (backend, agent,
  phone), HUD-styled side panels, a pairing QR in the TEL panel (decodes with an independent QR reader), reminders power on by
  default. 129 app tests.
- Phone app 1.0.0: QR pairing (Google code scanner) or `ultron://pair` link (confirmed by the user, never auto-applied), chat
  bubbles with the actions ULTRON took, Reminders tab (list/add/cancel with the reminders power), Settings (connection,
  pinned certificate, powers, permission status, battery, unpair), animated orb. Signed release APK (1.8 MB, v2 signature)
  with a key stored OUTSIDE the repo in `~/.config/ultron/android-signing/` (BACK IT UP). 38 JVM tests.
- Tests: backend 698 (+9 skipped), agent 115, voice 152, app 129, Android 38.

## Hands-free wake name (2026-11-12)
Offline engine: Vosk 0.3.75 + `vosk-model-small-en-us-0.15` (Apache-2.0; zip SHA-256 pinned in `android/scripts/fetch_vosk_model.sh`). The model's vocabulary does not contain "ultron" (`find_word` = -1), so it can never be written correctly; that is the technical reason "Ultron" kept being missed.
Measured with SYNTHETIC speech (12 Kokoro voices x 3 phrases = 36 per name; clean and 10 dB white noise) and 40 LibriSpeech clips (4.6 min of real read speech) for false triggers. Name must be in the first 3 words:

| name | clean | 10 dB noise | false in 4.6 min real speech |
|---|---|---|---|
| Jarvis | 36/36 | 36/36 | 0 |
| Computer / Orion / Friday | 36/36 | 35-36/36 | 0-1 |
| Nova / Athena | 35-36/36 | 35/36 | 2 |

Free-text decoding (name + command in one breath) with the name at the start: Jarvis 36/36, 0/120 synthetic and 0/40 real clips false. Jarvis is the default. NOT measured: real human voices, phone microphones, a pocket, a noisy room.
On Android: `VoskOnDeviceTest` (instrumented, debug build, emulator, no microphone) heard "Jarvis", "Hey Jarvis", "Jarvis play some jazz" (male and female voice) and rejected 2 other phrases. The shrunk release build was also run on an emulator: Hands-free switched on in Settings, microphone foreground service started, model unpacked and loaded through JNA, no crash, process stayed up (the emulator has no audio, so real listening was not exercised). Release needs the keep rules in `proguard-rules.pro` (without them R8 renames JNA classes the native code looks up by name).

## Desktop Friday wake name (2026-11-13)
Synthetic speech (12 Kokoro voices x 5 phrases x 4 speeds, clean and 10 dB noise) through the real standby path (Silero VAD -> whisper.cpp base.en with prompt "Friday." -> `match_wake(name="friday")`). NOT real voices, NOT a live mic.
- Wake: 210/240 (clean 111/120, noise 99/120). Command carried in the same breath was correct 118/119 times when it woke.
- Weak spot: "Friday, what time is it?" 37/48 and "Friday, stand by." 36/48: Whisper often drops a short leading word before a question/command and returns only "What time is it?". A longer VAD pre-roll (640 ms) did not fix it (216/240, clean unchanged), so the default stays 320 ms.
- Near-words that wake it: "Frida." 8/12, "Freddy." 1/12 (the "Friday." prompt biases Whisper toward the name, same effect seen with "Ultra"). "Fry day." woke 12/12 (same sound as the word). "Friday night is movie night." woke 12/12 because it STARTS with the name. Birthday, Fridge, Thursday, Saturday, Friendly, Hey Siri/Alexa/Google and 8 more: 0 false accepts.
- Not measured: real human voices, room noise, a real microphone.

## Phone voice training and status (Android 1.3.0, 2026-11-13)
- Bug fixed: installing an update (or Android killing the app) stopped the listening service but left the Hands-free switch ON, so it looked active and heard nothing. The app now restarts the service whenever it is opened with the switch on, and Settings shows the real state ("Listening for Friday", "Starting...", "Stopped: reason") plus what the listener understood for the last utterance (only while Settings is open).
- Train my voice: six tries of the wake name; keeps the spellings the recogniser consistently writes for that person (never everyday words; at most 12). Logic unit-tested (6 tests). Emulator (no audio): screen opens, loads the model, listens, pauses the service and resumes it on Cancel. NOT exercised with speech: the result screen and Save.

## Phone 1.4.0: chime, calls, apps, music (2026-11-13)
Root causes found for "cannot make calls / open Apple Music / play music":
1. The manifest declared no package visibility (`<queries>`), so on Android 11+ the app could not see Apple Music or any other app: "open X" found nothing. Fixed (launcher + play-from-search + geo/navigation/web-search queries).
2. Opening an app from the background needs "Display over other apps", which was off on the user's phone (appops showed a recent reject). Android 15+ also requires a visible overlay window at that moment, so the app shows an invisible 1x1 window for ~2.5 s around the launch. Without the permission the reply now says exactly that and a tap-notification is posted.
3. The spoken reply "Calling X in a moment" was said before the app knew whether a call would happen. Replies now state the real outcome (dialling, no such contact, several matches, "is it Asha Sharma?" for a mis-heard name, missing Phone/Contacts permission).
4. Mis-heard contact names: spelling-or-sound matching (Soundex) is used, but a sound-alike is never dialled without confirmation.
Verified on an Android 15 emulator with the app in the background (debug-only adb receiver `DebugCommandReceiver`, absent from release): Settings and Calendar opened, YouTube Music started "play some jazz", number call reached Telecom ACTIVE, exact contact dialled after the 3 s countdown, "aasha" asked to confirm Asha Sharma, unknown name answered, chime played. NOT verified: Apple Music itself (not on the emulator; on the user's phone it declares a play-from-search handler `VoiceSearchHandlerActivity`), a real call on the user's phone, how the chime sounds, recognition of real speech.
Chime: two rising notes (784 then 1175 Hz, 0.21 s), generated in code, played as an assistant sound; fires from the live partial hearing 450 ms after the name when nothing follows it; the listener is paused while it plays; switch + "Play the chime" button in Settings. Name+command in one breath gets a shorter single-note chime.

## Phone 1.5.0: reminders, calls, music, interrupting, accents (2026-11-13)
User report: reminders made it ask questions; Apple Music opened but "play <song>" did nothing; saying the name over a reply should stop it and listen; "call mom" / names not understood; more phone control.

Measured first (6 synthetic Kokoro voices x 24 command sentences through both small Vosk models, scratch eval, NOT real or Indian-accented voices): US model 74/144 sentences exact, Indian model 52/144 (expected worse on non-Indian voices; its accuracy on Indian speakers is NOT measured). Systematic errors found, and fixed in `core/Spoken.kt`:
- "remind me IN ten minutes" is written "remind me AND ten minutes" (all 6 voices) and times come as words ("five thirty p m"): the digit-only parser then asked "when?". Alarm requests were also parsed as reminders ("What should I remind you about?").
- "call" is written "cool"/"called"; names become other words ("Asha" -> "a share"/"shy", "Rahul" -> "rattle", "Priya" -> "prayer"). Song titles are not in the vocabulary at all ("despacito" -> "disperse sito", "kesariya" -> "castle real").
Changes:
- Reminders: spoken numbers/times understood; when something is missing it asks ONE short question and takes the next utterance (no wake name) as the answer (`Followups`). New: timers and alarms (handed to the phone's Clock app, local reminder if that is not possible), flashlight, battery, camera, "text X saying Y" (opens the message with the text filled in; the operator taps send).
- Calls: "cool/called" accepted only if a contact matches; family words (mom/mum/mummy/amma, dad/appa, bro/anna, sis/akka...) match whatever the contact is called; sound-alike names are asked back ("I heard "a share". Do you mean Asha Rao?" yes/no) and a "yes" is remembered on the phone (`contactAliases`) so the same mishearing dials directly next time. "Confirm before calling" now confirms by voice.
- Music: three tries in turn (app's own media service like Android Auto, play-from-search with title/artist, play key), each checked with `AudioManager.isMusicActive` after 6 s; if none works the app's search for the title is opened and ULTRON says so.
- Interrupting: the microphone stays open while it speaks (own AudioRecord, VOICE_COMMUNICATION source with echo canceller + noise suppressor); the wake name at the start of what is heard stops the reply and listens. A longer end-of-sentence pause is allowed while a command is expected.
- Settings: "Understand names and songs better" (OFF by default): after the name alone, one command is heard by Google's recogniser (accents, contacts, titles); only that audio is sent. "Say the name to interrupt" (on). Offline model chips US / Indian English (Indian model bundled, 37 MB zip, pinned SHA-256; `scripts/fetch_vosk_model.sh`).
Verified (Android 15 emulator, typed sentences through the router, since deleted): reminder with "and ten minutes", follow-up "when?" -> "in twenty minutes", timer reached the Clock app and RANG, alarm set, flashlight call, battery, exact/kin/sound-alike/unknown calls, learned name, text compose, non-commands passed on; hands-free service started with the VOICE_COMMUNICATION microphone and loaded the Indian model. 97 JVM tests.
NOT verified: any real speech; Apple Music playing (not on the emulator; on the user's phone it is stuck BUFFERING from an earlier try); whether echo cancelling lets the name interrupt a reply on the user's phone; Google recogniser path; Indian model on Indian speakers; real call.

## Phone 1.6.0: the name over music, the right song (2026-11-13)
User report: "Friday" is not heard while a song plays; Apple Music opens but does not play the song asked for; there is a delay between speaking and the action.

**Why the name was lost over music.** The wake rule looked for the name in the first words of a sentence, and a sentence ends at a silence. Music has no silence, so the recogniser writes one endless running text (song words included) and the name turns up in the middle of it. Measured before changing anything (scratch `wake_music.py`: US small model, 36 synthetic Kokoro "Friday" clips, background = generated instrumental music, with and without a LibriSpeech talker standing in for vocals; the level is what reaches the recogniser, no echo canceller modelled; NOT real speech, NOT real songs):

| "Friday" found, of 36 | +20 dB | +10 dB | +5 dB | 0 dB | -5 dB | false alarms in 20 one-minute clips |
|---|---|---|---|---|---|---|
| music only: name at the start (1.5.0) | 35 | 36 | 31 | 27 | 11 | 0 |
| music only: name among the newest words | 35 | 36 | 31 | 27 | 10 | 0 |
| music only: second recogniser limited to "friday" | 36 | 36 | 36 | 36 | 28 | 0 |
| music + talker: name at the start (1.5.0) | 5 | 8 | 4 | 2 | 0 | 1 |
| music + talker: name among the newest words | 34 | 31 | 20 | 13 | 5 | 1 |
| music + talker: second recogniser limited to "friday" | 26 | 27 | 26 | 18 | 9 | 19 |

The limited recogniser hears more at low levels but calls almost every minute of talking "friday" (19 of 20), so it was not used.

Same test with the Indian English model (the other choice in Settings; the test voices are NOT Indian, so this only shows the direction): music only, start and newest-words rules both 33 / 29 / 23 / 11 / 1; music + talker, start 28 / 13 / 9 / 4 / 2, newest words 31 / 21 / 14 / 6 / 3; limited recogniser 20 false alarms in 20 clips. The new rule helps there too, but that model loses the name sooner as the music gets louder.

Changes (Android 1.6.0, versionCode 9):
- `WakeWord.late`: while other audio is playing (`AudioManager.isMusicActive`) or ULTRON is talking, the name counts wherever it appears among the words that are new since the last look. In quiet the old start-of-sentence rule is unchanged.
- `service/Focus.kt`: hearing the name takes the audio focus (transient, assistant usage), which makes music apps pause; the chime plays and the command is heard in quiet; the focus is given back after the answer (or after 9 s with no command) and the music carries on. For "pause / stop the music" the focus is taken for good for a moment, so the app does not start again.
- Over music it is always two steps: name, chime, command. Words around the name are treated as the song, never as a command.
- A reply that itself contains the name ("It is 2:04 PM on Friday...") can no longer interrupt itself.
- Settings switch "Hear the name over music" (on).
- Parser: "open Apple Music and play X", "on Apple Music play X", "play the song X", "I want to listen to X" are play requests. In 1.5.0 "open apple music and play X" was read as "open the app called 'apple music and play X'", which opened Apple Music and dropped the song.
- Play request (`AppControl.PlayRequest`): where the music app's media service accepts the connection, the title/artist it reports is compared with the request (`MusicMatch`). A different song for 3 s, or nothing for 8 s, moves on to the next route (media service -> play-from-search request -> play key). If it is still the wrong song ULTRON says what is playing and opens the app's search. Every step is logged under the tag `UltronMusic`.

Verified (Android 15 emulator, no audio device, debug build, sentences and "heard" text injected through the debug-only receiver; stand-in music app with a real MediaBrowserService/MediaSession and audio-focus handling, built in the scratchpad; emulator since deleted):
- right song through the media service: finished in 1 s; wrong song on every route: said "Test Tunes is playing Different Song by Someone Else, not ..." and opened the search; wrong through the media service but right through the play request: corrected after 3 s.
- taking the focus paused the player, giving it back resumed it, the "quiet" release left it stopped (focus was granted with ULTRON in the background, holding only its microphone service).
- hands-free chain with music playing: running text ending in "friday" -> player paused; command -> answered -> player resumed; no command -> resumed after 9 s; "pause the music" -> stayed stopped; "play ... on test tunes" after the chime -> new song.
- Settings switch present. 111 JVM tests pass. Signed release installed on the user's phone over 1.5.0.

NOT verified: anything with real speech or a real microphone; how well the name is heard over real songs at the user's volume (the table is synthetic; expect it to fail when the music is much louder than the voice at the phone); whether Apple Music pauses and resumes on audio focus; whether Apple Music's media service lets ULTRON connect and report the song (if it refuses, only "is something playing" can be checked); which song Apple Music actually starts. After a real try, `adb logcat -s UltronMusic` on the phone shows which route ran and what Apple Music reported.

### Speed notes (asked for as notes; nothing here is built)
Where the time goes for a spoken phone command (figures marked * are settings in the code, not measurements on the phone):
1. Name -> chime: recogniser writes the name, then waits 450 ms* to see whether a command follows in the same breath, then the chime (0.21 s*).
2. Command -> action: the recogniser waits for an end-of-sentence silence. After the chime it uses Vosk's "long" mode* (roughly 0.75-1.5 s of silence; about 0.5-1 s otherwise).
3. Then the spoken confirmation starts (text-to-speech start-up), calls add a 3 s countdown*, and "play X" waits for the music app's own search.
4. Only sentences that are not phone commands go to the computer's chat model. Measured on the desktop voice path (docs/PHASE_2_VOICE.md §4): cloud (Groq) about 2.7 s from last word to first sound, local model 3.5-5.8 s; questions that trigger the reasoning pass 12-16 s (memory.md §5). The phone additionally waits for the whole answer before speaking (`UltronApi.chat`, not streamed).

Options, most effect first:
- A. Act on short fixed commands (pause, next, louder, flashlight, time) from the running text as soon as it has been stable for ~300 ms, instead of waiting for the end-of-sentence silence. Saves about 0.5-1 s on those. Risk: a longer sentence that begins like a short command.
- B. For questions: stream the answer to the phone and start speaking at the first sentence. Saves most on long answers. Needs the phone to use the existing WebSocket instead of the one-shot request.
- C. `ULTRON_REASONING_VOICE=false` in `backend/.env` (exists today): spoken questions skip the second reasoning pass (12-16 s -> about 2.5 s on those questions; answers to hard problems get worse).
- D. A smaller cloud model for spoken turns only, keeping the current one for typed chat. Faster first word and fewer tokens against the daily allowance; weaker answers. Needs a per-channel model setting.
- E. Shorter end-of-sentence wait after the chime (Vosk default instead of long mode): saves ~0.3-0.5 s, cuts off slow or hesitant speech more often.
- F. Shorter name-only wait (450 -> 250 ms): the chime comes sooner, but it will sometimes cut into a command said in the same breath.
- G. Skip the spoken "Paused." / "Next." / "Louder." and only do the action.
- H. Call countdown 3 s -> 1 s or off (less time to cancel a wrong name).
Not a speed-up: a bigger offline speech model (more accurate, slower), or the local chat model instead of the cloud one on this laptop (slower, see 4).
