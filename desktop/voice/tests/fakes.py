from __future__ import annotations

import asyncio

import numpy as np

from ultron_voice.audio import FRAME_SAMPLES, NullPlayer
from ultron_voice.client import BackendError
from ultron_voice.vad import Segmenter, VadConfig

SPEECH = np.full(FRAME_SAMPLES, 1000, dtype=np.int16)
SILENCE = np.zeros(FRAME_SAMPLES, dtype=np.int16)


class FakeVad:
    """Frames of constant 1000 are 'speech', zeros are 'silence'."""

    def prob(self, frame):
        return 1.0 if frame[0] != 0 else 0.0

    def reset(self):
        pass


class FakeStt:
    def __init__(self, *texts: str):
        self.texts = list(texts)
        self.calls = 0

    async def transcribe(self, pcm, rate=16000):
        self.calls += 1
        if isinstance(self.texts[0] if self.texts else None, Exception):
            raise self.texts.pop(0)
        return self.texts.pop(0) if self.texts else ""


class FakeTts:
    sample_rate = 24000

    def __init__(self, seconds_per_word: float = 0.5):
        self.spw = seconds_per_word
        self.said: list[str] = []

    async def synthesize(self, text):
        self.said.append(text)
        return np.ones(int(self.sample_rate * self.spw * len(text.split())), dtype=np.int16)


class FakePlayer(NullPlayer):
    pass


class BlockingPlayer(NullPlayer):
    """Like a real device: finish() waits for playback to end, which only stop() cuts short."""

    def __init__(self):
        super().__init__()
        self._done = asyncio.Event()

    async def stop(self):
        await super().stop()
        self._done.set()

    async def finish(self):
        await self._done.wait()


class FakeClock:
    def __init__(self):
        self.t = 100.0

    def __call__(self):
        return self.t

    def advance(self, s):
        self.t += s


class FakeClient:
    """Scripted backend. `gate_after=n` pauses the stream after n deltas until `gate` is set."""

    def __init__(
        self,
        deltas=("Hello there. ", "This is the second sentence. ", "And a third one."),
        gate_after: int | None = None,
        error: BackendError | Exception | None = None,
        privacy_locked: bool | None = None,  # None = the start event omits the flag (old server)
    ):
        self.deltas, self.gate_after, self.error = list(deltas), gate_after, error
        self.privacy_locked = privacy_locked
        self.gate = asyncio.Event()
        self.turns: list[dict] = []
        self.cancelled_with: list[str | None] = []
        self.cancelled = asyncio.Event()
        self.amendments: list[tuple[int, str | None]] = []
        self.conversation_id: str | None = None
        self.erased: list[str] = []
        self.erase_error: Exception | None = None

    async def erase_conversation(self) -> str:
        if self.erase_error:
            raise self.erase_error
        if self.conversation_id is None:
            return "nothing"
        self.erased.append(self.conversation_id)
        self.conversation_id = None
        return "erased"

    async def turn(self, message, *, privacy="default", modality="voice"):
        self.turns.append(
            {
                "message": message,
                "privacy": privacy,
                "modality": modality,
                "conversation": self.conversation_id,
            }
        )
        if self.error:
            raise self.error
        start = {"type": "start", "engine": {"provider": "fake", "model": "m", "tier": "local"}}
        if self.privacy_locked is not None:
            start["privacy_locked"] = self.privacy_locked
        yield start
        sent = []
        for i, d in enumerate(self.deltas):
            if self.gate_after is not None and i == self.gate_after:
                await self.gate.wait()
            if self.cancelled.is_set():
                break
            sent.append(d)
            yield {"type": "delta", "text": d}
        yield {
            "type": "end",
            "reply": "".join(sent).strip(),
            "message_id": 42,
            "interrupted": self.cancelled.is_set(),
            "conversation_id": "c",
        }
        self.conversation_id = "c"

    async def cancel(self, spoken=None, message_id=None):
        if message_id is not None:  # late cancel: the turn had already ended
            self.amendments.append((message_id, spoken))
            return
        self.cancelled_with.append(spoken)
        self.cancelled.set()


def make_session(stt, client=None, *, tts=None, clock=None, cfg=None, events=None):
    from ultron_voice.session import SessionConfig, VoiceSession  # noqa: PLC0415

    events = events if events is not None else []
    return VoiceSession(
        vad=FakeVad(),
        segmenter=Segmenter(VadConfig(min_speech_ms=64, end_silence_ms=96, preroll_ms=64)),
        stt=stt,
        tts=tts or FakeTts(),
        player=FakePlayer(),
        client=client or FakeClient(),
        config=cfg or SessionConfig(),
        on_event=lambda name, **kw: events.append((name, kw)),
        clock=clock or FakeClock(),
    ), events


def names(events):
    return [n for n, _ in events]
