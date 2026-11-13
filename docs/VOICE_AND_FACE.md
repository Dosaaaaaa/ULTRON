# ULTRON voice and face (2026-11-11)

> **2026-11-12 change:** the defaults are now a normal voice with minimal gaps (pitch 1.0, speed 1.0, no body/resonance/ring, pause 0.12 s, sentence gap 0.1 s, no pace variation) because the character processing made replies sound slow and laggy. The ULTRON character below is opt-in: set `ULTRON_VOICE_TTS_PITCH=0.80 TTS_BODY=0.8 TTS_RESONANCE=1.0 TTS_RING=0.10 TTS_PAUSE=0.28 TTS_SENTENCE_GAP=0.5 TTS_DELIVERY=1.0` (each with the `ULTRON_VOICE_` prefix) to get it back.


## The voice (local, original; not a clone)
Kokoro-82M `bm_lewis`, built by ear over several rounds with the operator:
pitch 0.80 (resampled, which also deepens the resonances), low-shelf bass 0.8, metallic resonance
(3 ms feedback comb, feedback 0.55) and a 90 Hz ring modulation at 0.10. No gravel, no reverb.
These are the defaults in `desktop/voice/ultron_voice/config.py`; every knob is an
`ULTRON_VOICE_TTS_*` environment variable (see `desktop/voice/.env.example`).
Approved sample: `desktop/voice/samples/G1_F1_ring_hint.wav`; the shipped code reproduces it to
0.04 % rms. `FINAL_ultron_lewis.wav` is the same voice with gaps.

**Gaps.** Silence after commas (0.28 s), colons/dashes (0.39 s), sentence ends inside one chunk
(0.53 s), ellipses (0.76 s) and 0.5 s between spoken sentences (added by the session, so the first
sentence and the end of a reply are never delayed). Each piece is synthesised separately.
**"Feeling" is pacing only**: short phrases are spoken ~12 % slower and the closing phrase ~5 %
slower. Kokoro has no emotion control; rage, loneliness and arrogance are not in this voice.

Not done / not claimed: the voice is not the film actor's and no recording of any person was used.
The Fish Audio option remains available but its free tier is reported to be ~8K tokens a month.
Not heard by me: all judgement of the sound was by the operator. Live use of the pauses in the
real pipeline (barge-in word estimates include the silences) is NOT TESTED with a microphone.

## The window (voice only)
`desktop/app`. Reference: the first form of Ultron in the MCU as described and pictured at
thedirect.com ("a web of blue light": a swirling sphere of crystalline electric light) and the dark
robot body with curved horn-like head plates. Used as visual reference only; the geometry is original.

- **At rest** (voice off, standby, muted): a rotating 3D **blue holographic orb**, restyled after
  github.com/SAGAR-TAMANG/ultron-by-sagar-builds (MIT; Three.js orb with an amber palette). Nothing
  was copied: it is an original QPainter implementation of the same visual language in blue:
  a hot solid core, three mutually perpendicular glowing great-circle rings, a band of 30 fine
  rings around the equator, a spherical shell of 520 tiny code-like marks (dashes, not real text),
  drifting glowing debris, dust, a sweeping scan ring, additive light blending, a baked vignette and
  faint scanlines. Rings precess at different rates; the core pulses with a pattern while speaking.
  Not reproduced from the reference: hand-gesture control, bloom/chromatic-aberration post-processing,
  film grain, real code-text sprites.
- **Awake and active** (listening, thinking, speaking): the orb contracts and fades while a 3D head
  forms (angular skull, glare eyes, mouth grille, crest, curved horn plates); ~1 s ease each way.
  The mouth animation is a pattern, not driven by the audio level.
- HUD (monospace, uppercase, blue): `U.L.T.R.O.N.` title with a rule top-left, key hints bottom-left
  (**V** voice, **M** mute, **S** system: real shortcuts), START/STOP VOICE and MUTE bottom-right,
  honest mic line bottom-centre (red only while speech is transcribed), small **SYS** button top-right. The system panel floats below the SYS button (backend, cloud budget, device agent, CPU/RAM/disk/GPU,
  "local only") exists only after SYS is clicked; nothing is polled while it is closed. Device-agent
  approval dialogs are still wired in.
- Removed from the UI: chat, conversation list, text entry, memory button, device tabs. Archived
  unchanged in `desktop/app/retired/` (not imported, not tested). New app dependency: numpy.

**Cost (measured offscreen, software QPainter, no GPU):** about 40 % of ONE core at rest (14 fps) and
82 % of one core while speaking (22 fps); the machine has 12 threads, so roughly 3 % / 6 % of the
whole CPU. Drawn at 55 % of window size and scaled up. Adjust with `ULTRON_DESKTOP_REST_FPS` and
`ULTRON_DESKTOP_ACTIVE_FPS` (1..60). A GPU path (QOpenGLWidget) would be far cheaper but was not built.
NOT measured: real-screen behaviour on Wayland, and CPU use with the voice pipeline running.

Verified: 46 app tests x3 runs; deliberate breaks caught for rest form, easing, orb geometry, horns,
fade, rings, shell, vignette and shortcut mapping (two other breaks were equivalent mutants);
offscreen renders inspected. A screenshot found the open panel covering the SYS button: fixed. A symmetry test found a real one-sided feature bug in the head.


## The web orb interface (current)

The window now shows the real front end of
[ultron-by-sagar-builds](https://github.com/SAGAR-TAMANG/ultron-by-sagar-builds) (MIT) recoloured
blue, in `desktop/orb-ui` (Next.js + Three.js with bloom and chromatic aberration), inside a
locked-down QtWebEngine view. Details and the build step are in `desktop/orb-ui/README.md`.

- Served from a loopback-only, read-only static server on a random port; the view may only load
  that origin and refuses every permission request (camera, microphone, ...).
- The assistant's state is pushed into the page; the orb changes glow, spin and pulse.
- Webcam hand gestures from the original were removed. The head that forms when active exists
  only in the native fallback (`ULTRON_ORB=native`, or automatic if WebGL fails).
- Verified: offscreen with software WebGL (render is blue-dominant, states reach the page, 12
  tests, 4 deliberate breaks all caught). NOT verified: the real Wayland screen, GPU WebGL, CPU
  use with voice running.

## What the reference project uses for voice

Its repository contains no voice code. Its write-up (July 2026) says only that the voice layer is
"OpenAI Realtime over WebRTC" with a "custom in-character audio treatment" (Web Audio), proprietary
and unreleased; no voice name is given. ULTRON does not use it. A fresh render of ULTRON's own
finished voice is in `desktop/voice/samples/DEMO_ultron_full.wav`.


## Orb follows the real speech loudness; GPU choice

- **Loudness feed.** The voice process wraps its player in `LevelPlayer`, which keeps a loudness
  envelope (50 ms windows, dBFS -45..-10 mapped to 0..1) on the *playback* clock, and clears it on
  barge-in. A small pump writes the current value to `$XDG_RUNTIME_DIR/ultron-voice.level` (only
  while something plays). The desktop reads it every 50 ms while the state is "speaking" and
  pushes it into the page; the core pulses with each word. If no feed arrives (>0.4 s) the orb
  falls back to the old rhythm pattern. Accuracy is the audio device's buffering (~50-100 ms).
  Only loudness numbers cross; no audio is saved or sent anywhere.
- **GPU.** The window's process sets the standard render-offload variables before Qt starts, so the
  embedded browser renders on the NVIDIA card instead of the Intel chip. `ULTRON_GPU=nvidia`
  (default) / `intel` / `auto`. Your own exports win. Only ULTRON's process is affected; the
  startup line `graphics: ...` says what was applied. Whisper stays on CPU by design; Ollama's own
  GPU use is configured in the Ollama service, not here.
- Verified: unit tests and deliberate breaks (8 of 8 caught); the EGL vendor override picks the
  NVIDIA driver in a window-less probe. NOT verified: that the live window now shows up under the
  NVIDIA card in `nvidia-smi` (needs a restart of your running window).
- Observed in your running window before this change: the web page's JavaScript is CPU-heavy
  (renderer ~136% + app ~99% of one core at rest). That is the page's own per-frame work, not the
  GPU; moving to the NVIDIA card will not remove it. Not yet optimised.
