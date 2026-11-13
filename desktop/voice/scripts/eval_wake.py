"""Measures the STT-keyword wake path: VAD -> whisper.cpp -> match_wake.

POSITIVES / CONFUSABLES are SYNTHETIC (Kokoro voices). They show how the pipeline behaves on clean
and noisy synthetic speech; they do NOT measure recall on real human voices - that needs recordings
of the actual operator (see docs/PHASE_2_VOICE.md, 'collect real samples').
REAL SPEECH negatives are LibriSpeech test-clean (read English audiobooks, CC BY 4.0).

    .venv/bin/python scripts/eval_wake.py synth  OUT.json
    .venv/bin/python scripts/eval_wake.py real   OUT.json [N_UTTERANCES]
    .venv/bin/python scripts/eval_wake.py text   OUT.json
"""

from __future__ import annotations

import asyncio
import json
import os
import sys
import time
from pathlib import Path

import numpy as np
import soundfile as sf

from ultron_voice import factory
from ultron_voice.audio import FRAME_SAMPLES, MIC_RATE
from ultron_voice.config import VoiceConfig
from ultron_voice.stt import WhisperStt
from ultron_voice.tts import KokoroTTS
from ultron_voice.vad import Segmenter, SileroVad, VadConfig
from ultron_voice.wake import match_wake

LIBRI = Path.home() / ".cache/ultron/data/librispeech"
VOICES_M = ["am_adam", "am_michael", "am_onyx", "am_fenrir", "bm_george", "bm_lewis"]
VOICES_F = ["af_heart", "af_bella", "af_nicole", "af_sky", "bf_emma", "bf_isabella"]
POSITIVES = [
    "Ultron.",
    "Hey Ultron.",
    "Ultron, what time is it?",
    "Okay Ultron, tell me a joke.",
    "Ultron, stand by.",
]
CONFUSABLES = [
    "Ultra.",
    "Hey Siri.",
    "Electron.",
    "Elton John.",
    "Alderon.",
    "Patron saint.",
    "Hello there.",
    "Hey Alexa.",
    "Okay Google.",
    "Turn on the lights.",
    "The ultimate test.",
    "Neutron star.",
    "Ultra marathon.",
    "Alterna.",
    "Hey Aaron.",
    "Cutron.",
    "Retro.",
    "Ultimate frisbee.",
    "I saw Age of Ultron last night.",
    "Tell me about Ultron.",
]


# WAKE_NAME=friday swaps the wake name into the positives and uses name-specific confusables.
NAME = os.environ.get("WAKE_NAME", "ultron").lower()
if NAME != "ultron":
    POSITIVES = [p.replace("Ultron", NAME.capitalize()) for p in POSITIVES]
    CONFUSABLES = [
        "Freddy.", "Birthday.", "Fridge.", "Thursday.", "Frida.", "Hey Siri.", "Okay Google.",
        "Hello there.", "Hey Alexa.", "Turn on the lights.", "Iron Man.", "Saturday.", "Friendly.",
        "Fry day.", "Friday night is movie night.", "See you on Friday.", "Tell me about Friday.",
        "It is a fine day.", "Right away.", "Frying pan.",
    ]  # the last few are legitimate non-wake speech or names that mention/resemble it


def to16k(pcm: np.ndarray, rate: int) -> np.ndarray:
    if rate == MIC_RATE:
        return pcm.astype(np.int16)
    n = int(len(pcm) * MIC_RATE / rate)
    return np.interp(np.linspace(0, len(pcm) - 1, n), np.arange(len(pcm)), pcm).astype(np.int16)


def add_noise(x: np.ndarray, snr_db: float, rng) -> np.ndarray:
    sig = x.astype(np.float32)
    p = float(np.mean(sig**2)) or 1.0
    noise = rng.standard_normal(len(sig)).astype(np.float32) * np.sqrt(p / 10 ** (snr_db / 10))
    return np.clip(sig + noise, -32768, 32767).astype(np.int16)


class Pipe:
    """The real standby path: VAD segmenter then whisper then matcher."""

    def __init__(self, cfg, stt, vad):
        self.stt, self.vad = stt, vad

    def utterances(self, audio: np.ndarray) -> list[np.ndarray]:
        self.vad.reset()
        seg = Segmenter(VadConfig(preroll_ms=int(os.environ.get("PREROLL_MS", "320"))))
        padded = np.concatenate(
            [np.zeros(MIC_RATE, np.int16), audio, np.zeros(MIC_RATE * 2, np.int16)]
        )
        out = []
        for i in range(0, len(padded) - FRAME_SAMPLES + 1, FRAME_SAMPLES):
            f = padded[i : i + FRAME_SAMPLES]
            _, utt = seg.push(f, self.vad.prob(f))
            if utt is not None:
                out.append(utt)
        return out

    async def wake(self, audio: np.ndarray):
        """-> (n_utterances, transcript(s), WakeMatch|None)"""
        utts = self.utterances(audio)
        texts = [await self.stt.transcribe(u) for u in utts]
        for t in texts:
            m = match_wake(t, name=NAME)
            if m:
                return len(utts), texts, m
        return len(utts), texts, None


async def setup():
    cfg = VoiceConfig()
    whisper = factory.make_whisper(cfg)
    await whisper.start()
    return (
        cfg,
        whisper,
        WhisperStt(whisper.url, prompt=cfg.stt_prompt),
        SileroVad(cfg.model(cfg.vad_model)),
    )


async def run_synth(out: Path) -> None:
    cfg, whisper, stt, vad = await setup()
    rng = np.random.default_rng(7)
    voices = VOICES_M + VOICES_F
    tts = {}
    for v in voices:
        lang = "en-gb" if v.startswith("b") else "en-us"
        tts[v] = KokoroTTS(
            cfg.model(cfg.tts_model),
            cfg.model("voices-v1.0.bin"),
            voice=v,
            speed=1.0,
            lang=lang,
            metal=0.0,
            threads=6,
        )
    pipe = Pipe(cfg, stt, vad)
    t0 = time.time()
    try:
        pos = {
            "total": 0,
            "vad_missed": 0,
            "wake": 0,
            "by_noise": {},
            "by_phrase": {},
            "by_voice": {},
            "command_ok": 0,
            "command_total": 0,
            "misses": [],
        }
        for v in voices:
            for phrase in POSITIVES:
                for speed in (0.9, 1.1):
                    tts[v].speed = speed
                    clean = to16k(await tts[v].synthesize(phrase), 24000)
                    for cond, audio in (("clean", clean), ("snr10dB", add_noise(clean, 10, rng))):
                        n, texts, m = await pipe.wake(audio)
                        pos["total"] += 1
                        pos["vad_missed"] += n == 0
                        pos["wake"] += m is not None
                        for key, name in (
                            ("by_noise", cond),
                            ("by_phrase", phrase),
                            ("by_voice", v),
                        ):
                            b = pos[key].setdefault(name, [0, 0])
                            b[0] += m is not None
                            b[1] += 1
                        expect_cmd = "," in phrase
                        if m is not None and expect_cmd:
                            pos["command_total"] += 1
                            pos["command_ok"] += bool(m.command)
                        if m is None and len(pos["misses"]) < 25:
                            pos["misses"].append(
                                {
                                    "voice": v,
                                    "phrase": phrase,
                                    "speed": speed,
                                    "noise": cond,
                                    "heard": texts,
                                }
                            )
        neg = {"total": 0, "false_accepts": 0, "examples": []}
        for v in voices:
            for phrase in CONFUSABLES:
                tts[v].speed = 1.0
                audio = to16k(await tts[v].synthesize(phrase), 24000)
                n, texts, m = await pipe.wake(audio)
                neg["total"] += 1
                if m is not None:
                    neg["false_accepts"] += 1
                    neg["examples"].append(
                        {"voice": v, "phrase": phrase, "heard": texts, "score": m.score}
                    )
    finally:
        await stt.close()
        await whisper.stop()
    result = {
        "kind": "SYNTHETIC speech (Kokoro). Not a measurement of real-voice recall.",
        "positives": pos,
        "confusable_negatives": neg,
        "seconds": round(time.time() - t0),
    }
    out.write_text(json.dumps(result, indent=2))  # noqa: ASYNC240
    print(
        json.dumps(
            {
                k: (v if k != "positives" else {x: y for x, y in v.items() if x not in ("misses",)})
                for k, v in result.items()
            },
            indent=1,
        )[:3500]
    )


def libri_utterances() -> list[tuple[Path, str]]:
    items = []
    for trans in sorted(LIBRI.rglob("*.trans.txt")):
        for line in trans.read_text().splitlines():
            uid, _, text = line.partition(" ")
            items.append((trans.parent / f"{uid}.flac", text))
    return items


def run_text(out: Path) -> None:
    """Matcher-only, on reference transcripts (ASR errors excluded; the audio run covers those)."""
    items = libri_utterances()
    starts = hits_start = windows = hits_window = 0
    words_total = 0
    examples = []
    for _, text in items:
        words = text.lower().split()
        words_total += len(words)
        starts += 1
        if (m := match_wake(" ".join(words[:6]))) is not None:
            hits_start += 1
            examples.append((text[:60], m.heard, round(m.score, 2)))
        for i in range(len(words)):  # emulate an utterance beginning anywhere
            windows += 1
            if match_wake(" ".join(words[i : i + 6])) is not None:
                hits_window += 1
    res = {
        "source": "LibriSpeech test-clean reference text",
        "utterances": len(items),
        "words": words_total,
        "utterance_start_trials": starts,
        "utterance_start_false_accepts": hits_start,
        "any_position_trials": windows,
        "any_position_false_accepts": hits_window,
        "examples": examples[:10],
    }
    out.write_text(json.dumps(res, indent=2))
    print(json.dumps(res, indent=1))


async def run_real(out: Path, n_utts: int) -> None:
    cfg, whisper, stt, vad = await setup()
    pipe = Pipe(cfg, stt, vad)
    items = libri_utterances()
    rng = np.random.default_rng(1)
    pick = rng.choice(len(items), size=min(n_utts, len(items)), replace=False)
    seconds = 0.0
    fa, details, t0 = 0, [], time.time()
    try:
        for k, idx in enumerate(pick):
            path, ref = items[int(idx)]
            data, rate = sf.read(path, dtype="int16")
            audio = to16k(data, rate)
            seconds += len(audio) / MIC_RATE
            _, texts, m = await pipe.wake(audio)
            if m is not None:
                fa += 1
                details.append(
                    {"file": path.name, "ref": ref[:80], "heard": texts, "score": m.score}
                )
            if (k + 1) % 50 == 0:
                print(
                    f"  {k + 1}/{len(pick)} utterances, {seconds / 3600:.2f} h, false accepts {fa}",
                    flush=True,
                )
    finally:
        await stt.close()
        await whisper.stop()
    hours = seconds / 3600
    res = {
        "source": "LibriSpeech test-clean (REAL speech), random sample",
        "utterances": len(pick),
        "hours_of_audio": round(hours, 3),
        "false_accepts": fa,
        "false_accepts_per_hour_observed": round(fa / hours, 2) if hours else None,
        "upper_95pct_per_hour_if_zero": round(3 / hours, 2) if hours and fa == 0 else None,
        "details": details,
        "wall_seconds": round(time.time() - t0),
    }
    out.write_text(json.dumps(res, indent=2))  # noqa: ASYNC240
    print(json.dumps(res, indent=1))


if __name__ == "__main__":
    mode, target = sys.argv[1], Path(sys.argv[2])
    if mode == "synth":
        asyncio.run(run_synth(target))
    elif mode == "text":
        run_text(target)
    else:
        asyncio.run(run_real(target, int(sys.argv[3]) if len(sys.argv) > 3 else 300))
