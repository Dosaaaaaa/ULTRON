"""The loudness feed that drives the desktop orb: envelope, playback timeline, file pump."""

from __future__ import annotations

import asyncio

import numpy as np
import pytest

from ultron_voice.audio import LEVEL_WINDOW_S, LevelPlayer, NullPlayer, envelope
from ultron_voice.cli import level_pump

RATE = 24000


def tone(seconds: float, amp: float) -> np.ndarray:
    t = np.arange(int(RATE * seconds)) / RATE
    return (amp * 32767 * np.sin(2 * np.pi * 220 * t)).astype(np.int16)


class Clock:
    def __init__(self) -> None:
        self.t = 100.0

    def __call__(self) -> float:
        return self.t


# ------------------------------------------------------------------ envelope
def test_silence_is_zero_and_loud_is_near_one():
    assert envelope(np.zeros(RATE, dtype=np.int16), RATE).max() == 0.0
    assert envelope(tone(0.5, 0.9), RATE).min() > 0.9


def test_louder_audio_gives_a_higher_level():
    quiet, mid, loud = (envelope(tone(0.3, a), RATE).mean() for a in (0.01, 0.1, 0.8))
    assert quiet < mid < loud


def test_one_value_per_window_including_a_partial_last_one():
    pcm = tone(0.26, 0.5)  # 5.2 windows of 50 ms -> 6 values
    assert len(envelope(pcm, RATE)) == 6
    assert len(envelope(np.zeros(0, dtype=np.int16), RATE)) == 0


# ------------------------------------------------------------------ timeline
async def test_level_follows_the_playback_clock_not_the_write_time():
    clock = Clock()
    p = LevelPlayer(NullPlayer(), clock)
    await p.play(np.concatenate([tone(0.5, 0.8), np.zeros(RATE // 2, dtype=np.int16)]), RATE)
    clock.t += 0.25
    assert p.level() > 0.9  # inside the loud half
    clock.t += 0.5  # 0.75 s in: the silent half
    assert p.level() == 0.0
    clock.t += 1.0  # past the end
    assert p.level() == 0.0 and not p._segments


async def test_second_chunk_queues_behind_the_first():
    clock = Clock()
    p = LevelPlayer(NullPlayer(), clock)
    await p.play(tone(0.5, 0.8), RATE)  # plays 0.0 - 0.5 s
    await p.play(np.zeros(RATE // 2, dtype=np.int16), RATE)  # queued: 0.5 - 1.0 s, silent
    clock.t += 0.25
    assert p.level() > 0.9
    clock.t += 0.5
    assert p.level() == 0.0
    # idle gap: a new chunk starts "now", not at the old end
    clock.t += 5
    await p.play(tone(0.3, 0.8), RATE)
    assert p.level() > 0.9


async def test_a_loud_chunk_waits_for_the_silent_one_before_it():
    clock = Clock()
    p = LevelPlayer(NullPlayer(), clock)
    await p.play(np.zeros(RATE // 2, dtype=np.int16), RATE)  # silent, plays 0.0 - 0.5 s
    await p.play(tone(0.5, 0.8), RATE)  # loud, must not start until 0.5 s
    clock.t += 0.25
    assert p.level() == 0.0
    clock.t += 0.5
    assert p.level() > 0.9


async def test_barge_in_stop_clears_the_level_at_once():
    clock = Clock()
    inner = NullPlayer()
    p = LevelPlayer(inner, clock)
    await p.play(tone(2.0, 0.8), RATE)
    assert p.level() > 0.9
    await p.stop()
    assert p.level() == 0.0 and inner.stopped == 1
    await p.play(tone(0.2, 0.8), RATE)  # the next reply starts from now, not from the old queue
    assert p.level() > 0.9


async def test_finish_clears_and_passes_through_the_audio():
    inner = NullPlayer()
    p = LevelPlayer(inner, Clock())
    await p.play(tone(0.2, 0.5), RATE)
    await p.finish()
    assert p.level() == 0.0 and inner.samples == RATE // 5


# ------------------------------------------------------------------ file pump
async def test_pump_publishes_levels_and_cleans_up(tmp_path):
    class Fixed:
        def __init__(self) -> None:
            self.v = 0.0

        def level(self) -> float:
            return self.v

    path = tmp_path / "x.level"
    fixed = Fixed()
    task = asyncio.create_task(level_pump(fixed, path, interval_s=0.01))
    await asyncio.sleep(0.05)
    assert not path.exists()  # nothing playing -> nothing written
    fixed.v = 0.625
    await asyncio.sleep(0.06)
    assert float(path.read_text()) == pytest.approx(0.625)
    fixed.v = 0.0
    await asyncio.sleep(0.06)
    assert float(path.read_text()) == 0.0  # the drop to silence is published too
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert not path.exists()
    assert not list(tmp_path.glob("*.tmp"))


def test_window_constant_is_what_the_desktop_expects():
    assert LEVEL_WINDOW_S == 0.05
