"""Render the same line in several candidate ULTRON voices so you can listen and choose.

Writes WAV files only (no microphone, no playback). Usage:
    PYTHONPATH=. .venv/bin/python scripts/voice_samples.py [OUT_DIR]
"""

from __future__ import annotations

import sys
from pathlib import Path

import soundfile as sf

from ultron_voice.config import VoiceConfig
from ultron_voice.tts import KokoroTTS

LINE = "I am ULTRON. Ask, and I will answer. I do not need to be told twice."  # same line as set A

# name: (voice or blend, speed, metal, pitch, gravel, body, reverb)
# Set E: D1 (Lewis, pitch 0.80, body 0.8) plus a metallic edge; no gravel. Metal below ~0.2 is
# nearly inaudible (the comb feedback is only 0.32 * amount), so the steps start at 0.25.
CANDIDATES = {
    "E1_D1_metal_little": ("bm_lewis", 0.92, 0.25, 0.80, 0.0, 0.8, 0.0),
    "E2_D1_metal_medium": ("bm_lewis", 0.92, 0.40, 0.80, 0.0, 0.8, 0.0),
    "E3_D1_metal_more": ("bm_lewis", 0.92, 0.55, 0.80, 0.0, 0.8, 0.0),
}


def main() -> None:
    out = Path(sys.argv[1] if len(sys.argv) > 1 else "samples").resolve()
    out.mkdir(parents=True, exist_ok=True)
    cfg = VoiceConfig()
    for name, (voice, speed, metal, pitch, gravel, body, reverb) in CANDIDATES.items():
        tts = KokoroTTS(
            cfg.model(cfg.tts_model),
            cfg.model("voices-v1.0.bin"),
            voice=voice,
            speed=speed,
            lang="en-gb" if voice.startswith("b") else "en-us",
            metal=metal,
            pitch=pitch,
            gravel=gravel,
            body=body,
            reverb=reverb,
            threads=cfg.tts_threads,
        )
        pcm = tts._render(LINE)
        sf.write(out / f"{name}.wav", pcm, tts.sample_rate, subtype="PCM_16")
        print(
            f"{name}: voice={voice} speed={speed} metal={metal} pitch={pitch} "
            f"gravel={gravel} body={body} reverb={reverb}",
            flush=True,
        )
    print("\nwrote", out)


if __name__ == "__main__":
    main()
