"""Audio I/O through PipeWire's pw-cat (no PortAudio / system packages needed).

Privacy: microphone audio lives only in memory. Nothing in this package writes it to disk.
"""

from __future__ import annotations

import asyncio
import contextlib
import signal
import time
from collections.abc import AsyncIterator, Callable
from typing import Protocol

import numpy as np

MIC_RATE = 16000
FRAME_SAMPLES = 512  # 32 ms at 16 kHz: the frame size Silero VAD expects
FRAME_BYTES = FRAME_SAMPLES * 2
FRAME_MS = FRAME_SAMPLES * 1000 // MIC_RATE


class AudioSource(Protocol):
    def frames(self) -> AsyncIterator[np.ndarray]: ...
    async def close(self) -> None: ...


class PipeWireMic:
    """16 kHz mono int16 frames from `pw-cat --record`. Opening this opens the microphone."""

    def __init__(self, target: str | None = None) -> None:
        self._target = target
        self._proc: asyncio.subprocess.Process | None = None

    async def frames(self) -> AsyncIterator[np.ndarray]:
        cmd = [
            "pw-cat",
            "--record",
            "--raw",
            "--rate",
            str(MIC_RATE),
            "--channels",
            "1",
            "--format",
            "s16",
            "--latency",
            "32ms",
        ]
        if self._target:
            cmd += ["--target", self._target]
        cmd.append("-")
        self._proc = await asyncio.create_subprocess_exec(
            *cmd, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.DEVNULL
        )
        assert self._proc.stdout is not None
        try:
            while True:
                data = await self._proc.stdout.readexactly(FRAME_BYTES)
                yield np.frombuffer(data, dtype=np.int16).copy()
        except asyncio.IncompleteReadError:
            return  # recorder exited (device unplugged, killed)
        finally:
            await self.close()

    async def close(self) -> None:
        proc, self._proc = self._proc, None
        if proc and proc.returncode is None:
            with contextlib.suppress(ProcessLookupError):
                proc.send_signal(signal.SIGINT)
            with contextlib.suppress(TimeoutError):
                await asyncio.wait_for(proc.wait(), 2)
            if proc.returncode is None:
                with contextlib.suppress(ProcessLookupError):
                    proc.kill()


class ArraySource:
    """Replays an int16 array as mic frames (tests, offline evaluation). No hardware involved."""

    def __init__(self, audio: np.ndarray, *, realtime: bool = False, tail_silence_s: float = 1.5):
        pad = np.zeros(int(MIC_RATE * tail_silence_s), dtype=np.int16)
        self._audio = np.concatenate([audio.astype(np.int16), pad])
        self._realtime = realtime

    async def frames(self) -> AsyncIterator[np.ndarray]:
        for i in range(0, len(self._audio) - FRAME_SAMPLES + 1, FRAME_SAMPLES):
            yield self._audio[i : i + FRAME_SAMPLES].copy()
            await asyncio.sleep(FRAME_MS / 1000 if self._realtime else 0)

    async def close(self) -> None:
        return None


class Player(Protocol):
    async def play(self, pcm: np.ndarray, sample_rate: int) -> None: ...
    async def finish(self) -> None: ...  # let queued audio play out
    async def stop(self) -> None: ...  # cut audio immediately (barge-in)


class PipeWirePlayer:
    """Plays int16 mono PCM via `pw-cat --playback`. stop() cuts audio immediately (barge-in)."""

    def __init__(self, target: str | None = None) -> None:
        self._target = target
        self._proc: asyncio.subprocess.Process | None = None
        self._rate: int | None = None

    async def _ensure(self, rate: int) -> asyncio.subprocess.Process:
        if self._proc and self._proc.returncode is None and self._rate == rate:
            return self._proc
        await self.stop()
        cmd = [
            "pw-cat",
            "--playback",
            "--raw",
            "--rate",
            str(rate),
            "--channels",
            "1",
            "--format",
            "s16",
            "--latency",
            "50ms",
        ]
        if self._target:
            cmd += ["--target", self._target]
        cmd.append("-")
        self._proc = await asyncio.create_subprocess_exec(
            *cmd, stdin=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.DEVNULL
        )
        self._rate = rate
        return self._proc

    async def play(self, pcm: np.ndarray, sample_rate: int) -> None:
        proc = await self._ensure(sample_rate)
        assert proc.stdin is not None
        try:
            proc.stdin.write(pcm.astype(np.int16).tobytes())
            await proc.stdin.drain()
        except (BrokenPipeError, ConnectionResetError):
            self._proc = None

    async def finish(self) -> None:
        """Let queued audio play out, then release the device."""
        proc, self._proc = self._proc, None
        if proc and proc.returncode is None and proc.stdin:
            proc.stdin.close()
            with contextlib.suppress(TimeoutError):
                await asyncio.wait_for(proc.wait(), 30)

    async def stop(self) -> None:
        proc, self._proc = self._proc, None
        if proc and proc.returncode is None:
            with contextlib.suppress(ProcessLookupError):
                proc.kill()  # drops what is buffered; SIGINT would let it drain
            with contextlib.suppress(Exception):
                await proc.wait()


LEVEL_WINDOW_S = 0.05  # one loudness value per 50 ms of speech


def envelope(pcm: np.ndarray, rate: int, window_s: float = LEVEL_WINDOW_S) -> np.ndarray:
    """Loudness per window, 0..1 (dBFS mapped -45..-10 to 0..1). This is what the orb follows."""
    n = max(1, int(rate * window_s))
    x = pcm.astype(np.float32) / 32768.0
    pad = (-len(x)) % n
    if pad:
        x = np.concatenate([x, np.zeros(pad, dtype=np.float32)])
    rms = np.sqrt(np.mean(np.square(x.reshape(-1, n)), axis=1))
    db = 20.0 * np.log10(rms + 1e-6)
    return np.clip((db + 45.0) / 35.0, 0.0, 1.0)


class LevelPlayer:
    """Wraps a Player and tracks how loud the audio playing *right now* is.

    play() hands whole chunks to the device, which plays them later in real time, so the loudness
    is kept on a timeline that follows the playback clock. barge-in (stop) clears it. Accuracy is
    about the device's own buffering (~50-100 ms): enough to drive a visual, not a meter.
    """

    def __init__(self, inner: Player, clock: Callable[[], float] = time.monotonic) -> None:
        self._inner = inner
        self._clock = clock
        self._segments: list[tuple[float, np.ndarray]] = []  # (start time, envelope)
        self._end = 0.0

    async def play(self, pcm: np.ndarray, sample_rate: int) -> None:
        await self._inner.play(pcm, sample_rate)
        start = max(self._clock(), self._end)
        self._segments.append((start, envelope(pcm, sample_rate)))
        self._end = start + len(pcm) / sample_rate

    async def finish(self) -> None:
        await self._inner.finish()
        self._segments.clear()

    async def stop(self) -> None:
        await self._inner.stop()
        self._segments.clear()
        self._end = 0.0

    def level(self) -> float:
        now = self._clock()
        self._segments = [(s, e) for s, e in self._segments if s + len(e) * LEVEL_WINDOW_S > now]
        for start, env in self._segments:
            if start <= now:
                return float(env[min(len(env) - 1, int((now - start) / LEVEL_WINDOW_S))])
        return 0.0


class NullPlayer:
    """Discards audio but records how much would have played. For tests / headless runs."""

    def __init__(self) -> None:
        self.samples = 0
        self.stopped = 0
        self.rate: int | None = None

    async def play(self, pcm: np.ndarray, sample_rate: int) -> None:
        self.samples += len(pcm)
        self.rate = sample_rate

    async def stop(self) -> None:
        self.stopped += 1

    async def finish(self) -> None:
        return None
