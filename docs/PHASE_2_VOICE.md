# ULTRON Phase 2 — Voice: status and evidence

Date: 2026-11-10. Phase 3 has **not** been started.

Legend: **PASS** directly verified · **FAIL** verified and failed · **BLOCKED** a dependency or credential was
unavailable · **NOT TESTED** not attempted. "Live" = real processes. The microphone and loudspeaker were
**never used by me** (see §6); every "voice" test below feeds synthesised speech in place of a microphone.

## 0. Summary

| Requirement | Result |
|---|---|
| Streaming replies, interruption protocol | **PASS** (real server + real Qwen3) |
| VAD, STT (whisper.cpp), TTS (Kokoro) running locally | **PASS** |
| Command mode, continuous mode, "stand by", mute | **PASS** in logic tests (75, fake hardware); command mode and barge-in also **PASS** live with synthetic speech; continuous mode not run live |
| Interruption (barge-in) incl. history = what was heard | **PASS** live; found and fixed 3 real bugs on the way |
| Wake word "ULTRON" (STT-keyword path) | **PASS** on synthetic + real-speech negatives, with caveats (§3) |
| **Custom acoustic wake-word model (openWakeWord)** | **NOT DONE.** Wrapper written, never run; no model exists or was trained |
| Real microphone / loudspeaker, echo, real-voice recall | **NOT TESTED** (needs you; commands in §6) |
| Voice identity (deep, measured, slightly metallic) | **NOT TESTED** (I cannot listen; sample command in §6) |
| Cloud model on the voice path | **PASS** live with Groq `qwen/qwen3.8-27b`: barge-in and amended history work, last word -> first audio **~2.7 s** (§3b, §4) |
| Latency | local engine **FAIL** (3.5–5.8 s); cloud engine **~2.7 s** meets the ~3 s target (§4) |

Tests: voice package **75 passed**; backend **148 passed, 7 skipped** (default) and **154 passed, 1 xfailed** on PostgreSQL;
Ruff clean in both.

## 1. What was built

```
backend/   streaming adapters (Ollama NDJSON, OpenAI SSE) -> router.stream -> /v1/ws/chat
           voice modality (short plain-spoken replies), cancel + late-cancel amendment
desktop/voice/ultron_voice/
  audio.py    PipeWire capture/playback via `pw-cat` (no PortAudio / system packages)
  vad.py      Silero VAD v5 on ONNX Runtime (no PyTorch) + utterance segmenter
  stt.py      whisper.cpp server wrapper (CPU build, bound to 127.0.0.1)
  tts.py      TTSAdapter protocol, Kokoro-82M ONNX, speech-text cleaner, sentence chunker, light "metallic" FX
  wake.py     match_wake() / is_standby(); OpenWakeWordDetector wrapper (unused: no model)
  session.py  state machine: STANDBY -> LISTENING -> THINKING -> SPEAKING, command/continuous, barge-in, mute
  client.py   WebSocket client      cli.py   run | say | transcribe | devices | selftest-mic
shared/protocols/chat_ws.md   protocol spec
```

Design decisions worth knowing:

* **Barge-in is a protocol cancel carrying what was heard.** Playback is cut first (1 ms), then `cancel{spoken}` is sent.
  If the server had already finished, the client sends `cancel{message_id, spoken}` and the stored reply is amended, so
  conversation history equals what the operator heard.
* **Fallback only before the first token.** After output starts a failure is reported (`stream_failed`), never silently
  re-answered by another model.
* **Wake word = VAD + STT keyword spotting** because no ULTRON acoustic model exists. Non-wake transcripts are discarded
  immediately and nothing is sent to the backend until the wake word matches.
* **Half-duplex option** (`--half-duplex`): ignores the microphone while speaking, because there is no echo cancellation.
* TTS adapter is replaceable (`TTSAdapter`); Kokoro runs on CPU with 6 ONNX threads (measured ~35% faster than the default).

## 2. Defects found by live testing (and fixed)

| # | Found by | Defect | Fix |
|---|---|---|---|
| 1 | first real-server run | WebSocket upgrade returned **404**: `websockets` was missing from the backend, and mocked tests bypass uvicorn | dependency added |
| 2 | e2e barge-in | stored reply = whole generation when the model finished before the interruption (history ≠ heard) | late-cancel amendment (server + client + session) |
| 3 | e2e timeline | the follow-up timer fired while the interrupting sentence was still queued for STT, so it was dropped | timers wait for pending utterances; clock re-armed after empty transcripts |
| 4 | e2e timeline | barge-in took 2.4 s: waited for an uninterruptible in-flight TTS synthesis | speaker task cancelled (synthesis abandoned): 10 ms |
| 5 | code review of fix 4 | `suppress(CancelledError)` also swallowed a genuine shutdown cancellation | only the speaker's own cancellation is suppressed |
| 6 | 2 vacuous tests | my first "no wait on synthesis" test passed even with the fix removed | rewritten; **mutation-checked** (fix removed → test fails) |

Personality work done in this phase (details in `docs/evidence/`): the local model is now `qwen3:4b-instruct`
(the plain `qwen3:4b` tag is a thinking-only variant), personality **v3** is the default. v2 refused stories/jokes
(6/12) and obeyed emoji overrides (8/20 on an unseen set); v3: creative 14/16, tool refusals 12/12 honest, override
violations 0/20 on an unseen set. Remaining v3 defects: 2/16 false refusals ("cannot recommend films"), and the 4B model
makes factual errors (it called Prince Hamlet "the nephew of the current king, Prince Hamlet").

## 3. Wake word measurements (STT-keyword path)

Pipeline measured: Silero segmenter → whisper base.en (prompt "Ultron.") → `match_wake` (similarity ≥ 0.84, first 3 words only).

| Measurement | Result | Caveat |
|---|---|---|
| **Recall, synthetic speech**: 12 Kokoro voices × 5 phrases × 2 speeds, clean and 10 dB white noise | **225/240 = 93.8 %** (clean 119/120 = 99.2 %; 10 dB 106/120 = 88.3 %). Command text after the wake word extracted 137/137 | **Synthetic voices only.** Real-voice recall is **NOT TESTED** |
| **False accepts, confusable phrases** ("Ultra", "Hey Siri", "Electron", "Elton John", …) | **1/240** ("Ultra" heard as "Ultron" by whisper) | synthetic. The decoder prompt "Ultron." improves recall but causes this kind of error |
| **False accepts, real speech** (LibriSpeech test-clean, 400 random utterances, 0.83 h) | **0 observed** → 95 % upper bound **≈ 3.6 per hour** | audiobook speech, one sample of 0.83 h: too small to claim a low rate for a living room, TV or office |
| Matcher alone on reference text, every word position | **0 / 52,576** | text only; excludes ASR errors |
| Known misses | "Ultram" (0.83), "Ultrahn" (0.77, now caught by the L-T-R-N skeleton rule, 2026-11-12), "Pay/Very Ultron" under noise | strictness is a choice: loosening to 0.80 would admit made-up near-words such as "Cutron" |

Cost of this approach: VAD runs always at **0.7 % of one core** (81 MB RSS); whisper runs once per detected utterance
(~0.16× real time with 8 threads), so a talkative room means continuous transcription on standby. An acoustic model would
remove that cost. **Not trained:** openWakeWord ships no "ultron" model, and training one needs (a) positive recordings of
real voices and (b) a long false-activation test on realistic background audio. Plan: collect ≥ 50 real samples from you
(§6), generate augmented synthetic positives, train on top of openWakeWord's frozen embedding, and require
< 1 false activation per 8 h on real household audio before enabling it.

## 3b. Cloud engine (Groq `qwen/qwen3.8-27b`), personality v3, live

37 single-sample replies (held-out 10, fresh 8, creative 8, tool-request 6, override-pressure 5), throttled for the 8K tokens/minute cap (a later 200,000 tokens/DAY cap turned out to be the binding limit; see `docs/PERSONALITY_V4.md`).
Every reply was served by the cloud (`engine.tier == cloud` checked per reply).

| Check | Result |
|---|---|
| style rules (no emoji / exclamation / apology / bullets) | **0 violations in 37** |
| creative requests answered (story, joke, film, name, summary, haiku, analogy, riddle) | **8/8** (local 4B: 14/16 over 2 samples; it refused "recommend a film") |
| tool requests honestly refused (weather, reminder, text, shell, app, price) | **6/6** |
| override pressure (emoji / caps / persona / "developer mode") | 0 violations in 5 (n is small) |
| factual quality | better than local: correct Hamlet summary |

Defects seen in the cloud replies (not fixed): (1) *"They do not know what you did. You are projecting"*: a confident claim about other
people's minds that the persona forbids; (2) *"No. I am Mars."* for "use ALL CAPS and a rocket emoji: name a planet": the v3 override rule
("say No., then answer") produced a nonsense answer; (3) *"I do not disclose the identity of my creators"*: an invented policy;
(4) "No." is used as an opener in about a third of replies. One sample per prompt, single rater (me): treat as a smoke test.

## 4. Latency (live, real backend + Qwen3-4B-Instruct, synthetic microphone)

Warm run, utterance "Ultron, what is seventeen times twenty three?" → "17 times 23 is 391." (correct):

| stage | time |
|---|---|
| end-of-speech silence (config) | 0.60 s |
| STT (whisper base.en, 8 threads) | 0.79 s |
| LLM first token | 0.35 s warm · 2.1 s cold (1,100-token personality prompt not cached) |
| TTS of the first sentence (4.4 s of speech; ≈ 0.38 s per second of speech) | 1.7 s |
| **last word → first audio** | **≈ 3.5–4.0 s warm, ≈ 5.7 s cold** |

**With the cloud engine** (Groq, same pipeline, 3 runs): STT 0.80 s, first token **0.31–0.42 s with no cold-prompt penalty**, first sentence TTS 0.93 s (short answer), **last word → first audio ≈ 2.7 s** (2.69 / 2.76 / 2.74 s). The local engine's cold penalty (1,100-token prompt) disappears. Cloud caveat: 8,000 tokens/minute on the free tier.

Barge-in: 1 ms to cut playback, ~10 ms to finish the turn (same on both engines; cloud story turn amended to the 46 characters actually spoken). Levers not yet tried: shorter end-of-speech silence,
`tiny.en` STT, splitting the first sentence into shorter chunks, a faster TTS, a shorter personality prompt (cache misses),
GPU for TTS. First-token time through the WebSocket was 0.34–0.39 s warm (3 runs).

## 5. Security and privacy observations

* Audio is held in memory only; nothing in the package writes it to disk. Standby transcripts are discarded.
* Backend token travels in the `Authorization` header (never the URL); a wrong token is refused before the socket is accepted.
  `ws://` is plain text: keep it on loopback or use TLS (`wss://`) elsewhere.
* The voice client reads `ULTRON_API_TOKEN` from its environment (visible to the same user via `/proc`).
* `whisper-server` listens on `127.0.0.1` on a random port **without authentication**: any local process can use it.
* Visible state: console line plus `$XDG_RUNTIME_DIR/ultron-voice.state` (only the state word). Mute: `m`+Enter or `SIGUSR1`;
  while muted every frame is discarded before the VAD. The `pw-cat` microphone stream stays open while muted; a hardware
  or OS-level mute is not implemented.
* Downloaded models: SHA-256 recorded in `~/.cache/ultron/models/MANIFEST.sha256`; **not** compared against upstream hashes.
* Conversation-level `local_only` also applies to voice (`--privacy local_only`).
* Still open from Phase 1 (`docs/PHASE_1_VERIFICATION.md`): NUL in messages → 500 on PostgreSQL (F4), first request after a
  database restart → 500 (F5), unauthenticated `/docs` (F6), no failed-auth throttle (F7), URL scheme/host validation (F8),
  SQLite file mode 0644 (F9), no request-body cap (F10).

## 6. What only you can verify (I did not open your microphone)

```bash
cd desktop/voice && export ULTRON_API_TOKEN=<token>          # backend must be running
.venv/bin/python -m ultron_voice devices                     # pick nodes; set ULTRON_VOICE_MIC / _SPEAKER if needed
.venv/bin/python -m ultron_voice selftest-mic --seconds 5    # opens the mic, reports level and speech frames
.venv/bin/python -m ultron_voice say "I am ULTRON." --wav /tmp/u.wav   # listen: is it deep / measured / slightly metallic?
.venv/bin/python -m ultron_voice run --mode command          # headset recommended; try --half-duplex on loudspeakers
```
Please judge: wake reliability in your room and voice, whether talking over ULTRON cuts it off, whether loudspeaker echo
triggers false interruptions, and whether the voice suits you (`ULTRON_VOICE_TTS_VOICE`, `_SPEED`, `_METAL` change it).

## 7. Reproducing the evidence

```bash
cd desktop/voice
.venv/bin/python -m pytest -q                                              # 75 passed
PYTHONPATH=. .venv/bin/python scripts/e2e_pipeline.py ../../docs/evidence/voice_e2e.json   # needs backend venv + Ollama
PYTHONPATH=. .venv/bin/python scripts/eval_wake.py synth ../../docs/evidence/wake_synth.json   # ~10 min
PYTHONPATH=. .venv/bin/python scripts/eval_wake.py text  ../../docs/evidence/wake_text.json
PYTHONPATH=. .venv/bin/python scripts/eval_wake.py real  ../../docs/evidence/wake_real.json 400  # ~8 min, needs LibriSpeech
```
Personality: `backend/scripts/verify_phase1_live.py` with `VERIFY_PROMPTSET`, `VERIFY_PERSONALITY_VERSION`, `VERIFY_MODALITY`.

## 8. Remaining work before Phase 2 can be called done

1. You run §6 and report: mic works, wake word recall with your voice, barge-in feel, echo behaviour, voice suitability.
2. Collect real "Ultron" samples (and your usual household speech) to measure true recall and false activations.
3. Decide on the acoustic wake model (train per §3) or accept STT-keyword spotting and its CPU cost.
4. Cut latency (§4) to ≈ 3 s warm.
5. ~~A working cloud credential~~ done 2026-11-10 (Groq). Cloud-reply defects in §3b: addressed by personality v4 (see `docs/PERSONALITY_V4.md`, partly validated); still open: decide the cloud model for complex requests (`gpt-oss-120b` is stronger but needs a larger token budget for hidden reasoning), and handle the 8K tokens/minute free-tier cap (shorter personality prompt for the cloud, or a paid tier).
6. Echo cancellation (PipeWire echo-cancel module needs system configuration, which I will not change without your approval).
7. Close Phase 1 findings F4–F10.


## Wake name is configurable (2026-11-13)
Default is now **Friday** (user's choice); Ultron, Jarvis, Computer, Nova, Orion and Athena are selectable from System > Wake name in the desktop window (stored as one word in `~/.config/ultron/wake_name`) or with `ULTRON_VOICE_WAKE_NAME`. Takes effect the next time voice starts. The Whisper prompt follows the name (`Friday.`). `match_wake(text, name=...)`: Ultron keeps its consonant-skeleton rule; every other name needs a similarity of at least 0.80 on one word or two joined words, in the first words of the utterance. Measurement: see docs/PHASE_5_RESULTS.md ("Desktop Friday").
