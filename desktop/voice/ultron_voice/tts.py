"""Text-to-speech: a replaceable adapter, Kokoro (ONNX) implementation, speech text cleaning."""

from __future__ import annotations

import asyncio
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol

import numpy as np


class TTSAdapter(Protocol):
    sample_rate: int

    async def synthesize(self, text: str) -> np.ndarray:
        """Return mono int16 PCM at `sample_rate`."""
        ...


# ---------------------------------------------------------------- text preparation
_URL = re.compile(r"https?://\S+")
_CODE = re.compile(r"```.*?```|`[^`]*`", re.DOTALL)
_MD = re.compile(r"[*_#>~|]+")
_BULLET = re.compile(r"^\s*(?:[-*•]|\d+[.)])\s+", re.MULTILINE)
_SPACE = re.compile(r"\s+")


def clean_for_speech(text: str) -> str:
    """Strip markup a speech synthesiser would read aloud as symbols."""
    text = _CODE.sub(" ", text)
    text = _URL.sub(" a link ", text)
    text = _BULLET.sub("", text)
    text = _MD.sub("", text)
    return _SPACE.sub(" ", text).strip()


_END = re.compile(r"([.!?]+[\"')\]]*)(\s+|$)")
_CLAUSE = re.compile(r"([,;:\u2014])\s+")


class SentenceChunker:
    """Accumulates streamed text and releases complete, speakable sentences.

    The first sentence is released as soon as it is complete and at least `min_first_chars` long
    (this is what keeps time-to-first-audio low); later fragments are merged up to `min_chars` so
    that short sentences do not become choppy separate utterances.
    """

    def __init__(self, min_chars: int = 24, min_first_chars: int = 10) -> None:
        self._buf = ""
        self._min = min_chars
        self._min_first = min_first_chars
        self._first_clause_chars = 28  # a clause this long can be spoken on its own
        self._released = 0

    def feed(self, delta: str) -> list[str]:
        self._buf += delta
        out: list[str] = []
        if not self._released:  # time-to-first-audio: do not wait for a full first sentence
            m = _CLAUSE.search(self._buf)
            if m and m.end() >= self._first_clause_chars and not _END.search(self._buf[: m.end()]):
                piece = self._buf[: m.end()].strip()
                self._buf = self._buf[m.end() :]
                self._released += 1
                cleaned = clean_for_speech(piece)
                return [cleaned] if cleaned else []
        start = 0
        for m in _END.finditer(self._buf):
            end = m.end()
            piece = self._buf[start:end].strip()
            if m.end() == len(self._buf) and not m.group(2):
                break  # "3." at the very end could be "3.5": wait for more text
            if len(piece) >= (self._min if self._released else self._min_first):
                out.append(piece)
                self._released += 1
                start = end
        self._buf = self._buf[start:]
        return [c for c in (clean_for_speech(p) for p in out) if c]

    def flush(self) -> list[str]:
        rest = clean_for_speech(self._buf)
        self._buf = ""
        self._released += 1 if rest else 0
        return [rest] if rest else []


# ---------------------------------------------------------------- voice colouring
def apply_metallic(x: np.ndarray, sample_rate: int, amount: float) -> np.ndarray:
    """Subtle metallic texture: short comb resonance plus faint ring modulation.

    amount 0 = untouched, 1 = strongly robotic. Intelligibility, not effect, is the priority.
    """
    if amount <= 0:
        return x
    x = x.astype(np.float32)
    peak = float(np.max(np.abs(x))) or 1.0
    delay = max(1, int(sample_rate * 0.0055))
    fb = 0.32 * amount
    y = x.copy()
    for k in range(1, 5):  # truncated feedback comb: x[n] + fb*x[n-d] + fb^2*x[n-2d] + ...
        if k * delay >= len(x):
            break
        y[k * delay :] += (fb**k) * x[: len(x) - k * delay]
    t = np.arange(len(y), dtype=np.float32) / sample_rate
    ring = 1.0 - 0.18 * amount + 0.18 * amount * np.sin(2 * np.pi * 70.0 * t)
    y = y * ring
    return (y * (peak / (float(np.max(np.abs(y))) or 1.0))).astype(np.float32)


def _shelf(freqs: np.ndarray, corner_hz: float, db: float) -> np.ndarray:
    """Gain curve: +db below `corner_hz`, flat above, smooth transition (a low shelf)."""
    below = 1.0 / (1.0 + np.exp((freqs - corner_hz) / (0.35 * corner_hz)))
    return 1.0 + (10.0 ** (db / 20.0) - 1.0) * below


def character_fx(
    x: np.ndarray, sample_rate: int, *, gravel: float = 0.0, body: float = 0.0, reverb: float = 0.0
) -> np.ndarray:
    """Original sound design for a deep, gravelly, cold, large voice. Each knob is 0..1.

    body    low-shelf boost (up to +7 dB under 250 Hz): weight and richness.
    gravel  roughness: slow amplitude flutter (vocal-fry like), a band of rasp noise that follows
            the speech envelope (so silence stays silent), then soft saturation.
    reverb  a short, dark room tail: the voice sounds large and cold rather than dry.
    Deterministic (fixed noise seed) so every sentence has the same character. Pure numpy.
    """
    if gravel <= 0 and body <= 0 and reverb <= 0:
        return x
    x = x.astype(np.float32)
    peak = float(np.max(np.abs(x))) or 1.0
    n = len(x)
    rng = np.random.default_rng(7)
    if body > 0:
        spec = np.fft.rfft(x) * _shelf(np.fft.rfftfreq(n, 1 / sample_rate), 250.0, 7.0 * body)
        x = np.fft.irfft(spec, n).astype(np.float32)
    if gravel > 0:
        t = np.arange(n, dtype=np.float32) / sample_rate
        flutter = 0.5 + 0.5 * np.sin(2 * np.pi * 45.0 * t + 3.0 * np.sin(2 * np.pi * 3.1 * t))
        x = x * (1.0 - 0.30 * gravel * flutter)
        win = max(int(0.012 * sample_rate), 1)
        env = np.convolve(np.abs(x), np.ones(win, dtype=np.float32) / win, mode="same")
        noise = np.fft.rfft(rng.standard_normal(n).astype(np.float32))
        freqs = np.fft.rfftfreq(n, 1 / sample_rate)
        noise *= ((freqs > 800) & (freqs < 4500)).astype(np.float32)  # rasp lives in the mids
        noise = np.fft.irfft(noise, n).astype(np.float32)
        noise /= float(np.std(noise)) or 1.0
        x = x + 0.55 * gravel * env * noise
        drive = 1.0 + 3.0 * gravel
        x = np.tanh(drive * x / peak) / np.tanh(drive) * peak
    if reverb > 0:
        length = int(0.28 * sample_rate)
        decay = np.exp(-np.arange(length, dtype=np.float32) / (0.07 * sample_rate))
        ir = rng.standard_normal(length).astype(np.float32) * decay
        ir /= float(np.sqrt(np.sum(ir**2))) or 1.0
        ir_spec = np.fft.rfft(ir * np.linspace(1.0, 0.35, length, dtype=np.float32), n + length)
        wet = np.fft.irfft(np.fft.rfft(x, n + length) * ir_spec, n + length).astype(np.float32)
        x = np.concatenate([x, np.zeros(length, dtype=np.float32)]) + 0.9 * reverb * wet
    return (x * (peak / (float(np.max(np.abs(x))) or 1.0))).astype(np.float32)


def robot_fx(
    x: np.ndarray, sample_rate: int, *, resonance: float = 0.0, ring: float = 0.0
) -> np.ndarray:
    """The robotic layer: a short metallic resonance plus a light ring modulation.

    resonance  0..1  a 3 ms feedback comb (1.0 = feedback 0.55): the voice rings like a metal tube.
    ring       0..1  mix of 90 Hz ring modulation: a low robotic buzz (0.1 is a hint).
    Both 0 returns the input unchanged. The peak level of the input is preserved.
    """
    if resonance <= 0 and ring <= 0:
        return x
    x = x.astype(np.float32)
    peak = float(np.max(np.abs(x))) or 1.0
    y = x.copy()
    if resonance > 0:
        delay = max(1, int(sample_rate * 0.003))
        fb = 0.55 * min(resonance, 1.0)
        for k in range(1, 7):  # truncated feedback comb: x[n] + fb*x[n-d] + fb^2*x[n-2d] + ...
            if k * delay >= len(x):
                break
            y[k * delay :] += (fb**k) * x[: len(x) - k * delay]
    if ring > 0:
        mix = min(ring, 1.0)
        t = np.arange(len(y), dtype=np.float32) / sample_rate
        y = (1.0 - mix) * y + mix * y * np.sin(2 * np.pi * 90.0 * t)
    return (y * (peak / (float(np.max(np.abs(y))) or 1.0))).astype(np.float32)


@dataclass(frozen=True)
class Beat:
    """One spoken piece of a sentence: what to say, how fast, and the silence after it."""

    text: str
    speed_mult: float
    pause_s: float


_BEAT_SPLIT = re.compile(r"(?<=[,;:\u2014\u2013\u2026])\s+|(?<=\.\.\.)\s+|(?<=[.!?])\s+")
_ABBREV = {"mr.", "mrs.", "ms.", "dr.", "vs.", "etc.", "e.g.", "i.e."}


def plan_delivery(text: str, *, pause_s: float = 0.0, delivery: float = 0.0) -> list[Beat]:
    """Split a sentence (or a few) into beats with silences and pace, so speech has real gaps.

    pause_s   seconds of silence after a comma; other marks scale from it (colon/semicolon/dash
              x1.4, sentence end inside the text x1.9, ellipsis x2.7). 0 = no added silence.
    delivery  0..1 pace variation: very short pieces (3 words or fewer) are spoken more slowly and
              the last piece of the text lands a little slower, which reads as weight and
              deliberation. This is pacing only; the voice model has no emotion control.
    The last beat has no pause: gaps between sentences are the session's job, so the end of a
    reply is not delayed.
    """
    pieces: list[str] = []
    for part in _BEAT_SPLIT.split(text.strip()):
        part = part.strip()
        if not part:
            continue
        if pieces and pieces[-1].split()[-1].lower() in _ABBREV:
            pieces[-1] = f"{pieces[-1]} {part}"  # "Dr. Smith" is not two sentences
        else:
            pieces.append(part)
    beats: list[Beat] = []
    for i, piece in enumerate(pieces):
        last = i == len(pieces) - 1
        tail = piece.rstrip("\"')]")
        if tail.endswith(("...", "\u2026")):
            scale = 2.7
        elif tail.endswith((".", "!", "?")):
            scale = 1.9
        elif tail.endswith((";", ":", "\u2014", "\u2013")):
            scale = 1.4
        else:
            scale = 1.0
        mult = 1.0
        if len(piece.split()) <= 3:
            mult -= 0.12 * delivery
        if last:
            mult -= 0.05 * delivery
        beats.append(Beat(piece, mult, 0.0 if last else pause_s * scale))
    return beats


def lower_pitch(x: np.ndarray, factor: float) -> np.ndarray:
    """Lower the pitch by `factor` (0.88 = about two semitones down) by resampling.

    This also slows speech by the same factor, so the caller asks the synthesiser for
    `speed / factor` to land on the intended tempo. 1.0 returns the input unchanged. Raising the
    pitch (factor > 1) is allowed but not what ULTRON wants.
    """
    if not 0.5 <= factor <= 1.5:
        raise ValueError("pitch factor must be between 0.5 and 1.5")
    if factor == 1.0 or len(x) < 2:
        return x
    positions = np.arange(int(len(x) / factor)) * factor
    return np.interp(positions, np.arange(len(x)), x.astype(np.float32)).astype(np.float32)


def to_int16(x: np.ndarray) -> np.ndarray:
    return (np.clip(x, -1.0, 1.0) * 32767.0).astype(np.int16)


# ---------------------------------------------------------------- Kokoro
class KokoroTTS:
    """Kokoro-82M via ONNX Runtime on CPU. Not an actor impersonation: a stock voice, optionally
    blended, slowed slightly, with a light metallic texture."""

    sample_rate = 24000

    def __init__(
        self,
        model_path: str | Path,
        voices_path: str | Path,
        *,
        voice: str = "bm_george",
        speed: float = 0.92,
        lang: str = "en-gb",
        metal: float = 0.15,
        pitch: float = 1.0,
        gravel: float = 0.0,
        body: float = 0.0,
        reverb: float = 0.0,
        resonance: float = 0.0,
        ring: float = 0.0,
        pause: float = 0.0,
        delivery: float = 0.0,
        threads: int = 6,
    ) -> None:
        import onnxruntime as ort  # noqa: PLC0415 - heavy imports, keep lazy
        from kokoro_onnx import Kokoro  # noqa: PLC0415

        # Measured on the reference laptop (12 logical cores): the default thread count
        # over-subscribes and is ~35% slower than 6 threads while the LLM is also running.
        opts = ort.SessionOptions()
        opts.intra_op_num_threads = threads
        session = ort.InferenceSession(
            str(model_path), sess_options=opts, providers=["CPUExecutionProvider"]
        )
        self._k = Kokoro.from_session(session, str(voices_path))
        self._voice = self._resolve_voice(voice)
        self.speed, self.lang, self.metal, self.pitch = speed, lang, metal, pitch
        self.gravel, self.body, self.reverb = gravel, body, reverb
        self.resonance, self.ring = resonance, ring
        self.pause, self.delivery = pause, delivery

    def _resolve_voice(self, spec: str):
        """'bm_george' or a weighted blend 'bm_george:0.7,am_onyx:0.3'."""
        if ":" not in spec:
            return spec
        parts = [p.split(":") for p in spec.split(",")]
        total = sum(float(w) for _, w in parts)
        return sum(self._k.get_voice_style(n) * (float(w) / total) for n, w in parts)

    def _render(self, text: str) -> np.ndarray:
        # Resampling for a lower pitch also slows the speech: ask for a faster take to compensate.
        base_speed = self.speed / self.pitch
        parts: list[np.ndarray] = []
        for beat in plan_delivery(text, pause_s=self.pause, delivery=self.delivery):
            speed = min(max(base_speed * beat.speed_mult, 0.5), 2.0)
            samples, sr = self._k.create(beat.text, voice=self._voice, speed=speed, lang=self.lang)
            assert sr == self.sample_rate
            parts.append(lower_pitch(samples, self.pitch))
            if beat.pause_s > 0:
                parts.append(np.zeros(int(beat.pause_s * sr), dtype=np.float32))
        samples = np.concatenate(parts)
        # The effects run on the whole sentence so its level and character stay consistent.
        samples = apply_metallic(samples, sr, self.metal)
        samples = character_fx(samples, sr, gravel=self.gravel, body=self.body, reverb=self.reverb)
        return to_int16(robot_fx(samples, sr, resonance=self.resonance, ring=self.ring))

    async def warm_up(self) -> None:
        """The first ONNX inference is much slower than later ones; pay for it before the user."""
        await self.synthesize("Ready.")

    async def synthesize(self, text: str) -> np.ndarray:
        text = clean_for_speech(text)
        if not text:
            return np.zeros(0, dtype=np.int16)
        return await asyncio.to_thread(self._render, text)  # ONNX releases the GIL
