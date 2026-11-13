"""Voice session: wake -> listen -> think -> speak, with command/continuous modes and barge-in.

States: STANDBY (waiting for the wake word), LISTENING (waiting for a command), THINKING (model
has not produced audio yet), SPEAKING. Mute is orthogonal: while muted no microphone frame is
processed at all.

Hardware is injected (mic frames, VAD, STT, TTS, player, backend client), so the logic is tested
without audio devices.
"""

from __future__ import annotations

import asyncio
import contextlib
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any, Protocol

import numpy as np

from ultron_voice.audio import FRAME_MS, AudioSource, NullPlayer, Player
from ultron_voice.client import BackendError
from ultron_voice.tts import SentenceChunker, TTSAdapter
from ultron_voice.vad import Segmenter
from ultron_voice.wake import is_standby, match_wake


class State(StrEnum):
    STANDBY = "standby"
    LISTENING = "listening"
    THINKING = "thinking"
    SPEAKING = "speaking"


@dataclass
class SessionConfig:
    wake_name: str = "ultron"  # the word that wakes it from standby
    mode: str = "command"  # "command" | "continuous"
    privacy: str = "default"  # sent to the backend; "local_only" keeps every turn on-device
    listen_timeout_s: float = 8.0  # wait for a command after the wake word
    follow_up_timeout_s: float = 12.0  # continuous mode: idle time before returning to standby
    barge_in: bool = True  # False = half-duplex (mic ignored while speaking); use with speakers
    barge_in_prob: float = 0.8
    barge_in_ms: int = 320
    error_line: str = "I cannot reach my reasoning engine."
    sentence_gap_s: float = 0.0  # silence before every spoken sentence except the first of a reply


class Vad(Protocol):
    def prob(self, frame: np.ndarray) -> float: ...
    def reset(self) -> None: ...


class Stt(Protocol):
    async def transcribe(self, pcm: np.ndarray, rate: int = ...) -> str: ...


class Client(Protocol):
    conversation_id: str | None

    def turn(self, message: str, *, privacy: str = ..., modality: str = ...): ...
    async def erase_conversation(self) -> str: ...
    async def cancel(self, spoken: str | None = None, message_id: int | None = None) -> None: ...


@dataclass
class _Played:
    text: str
    begin: float
    end: float


@dataclass
class _Turn:
    interrupt: asyncio.Event = field(default_factory=asyncio.Event)
    played: list[_Played] = field(default_factory=list)
    expected_end: float = 0.0
    metrics: dict[str, float] = field(default_factory=dict)
    heard_at_barge_in: str | None = None  # what the operator heard when they cut in
    speaker: asyncio.Task | None = None
    # May this turn's reply text go to a cloud voice? Starts False and only becomes True when the
    # backend's `start` event says the conversation is not locked to local.
    cloud_voice_ok: bool = False


class VoiceSession:
    def __init__(
        self,
        *,
        vad: Vad,
        segmenter: Segmenter,
        stt: Stt,
        tts: TTSAdapter,
        player: Player,
        client: Client,
        config: SessionConfig | None = None,
        on_event: Callable[..., None] | None = None,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self.vad, self.seg, self.stt, self.tts, self.player, self.client = (
            vad,
            segmenter,
            stt,
            tts,
            player,
            client,
        )
        self.cfg = config or SessionConfig()
        self._emit = on_event or (lambda *a, **k: None)
        self._last_voice: tuple[str, str | None] | None = None
        self._now = clock
        self.state = State.STANDBY
        self.muted = False
        self._utterances: asyncio.Queue[tuple[np.ndarray, float]] = asyncio.Queue()
        self._turn: _Turn | None = None
        self._barge_frames = 0
        self._barged = False
        self._deadline: float | None = None
        self._timeout_s = 0.0
        self._worker: asyncio.Task | None = None
        self._pending = 0  # utterances queued or being transcribed/handled

    # ------------------------------------------------------------ public controls
    def set_muted(self, muted: bool) -> None:
        self.muted = muted
        self.seg.reset()
        self.vad.reset()
        self._barge_frames = 0
        self._emit("muted" if muted else "unmuted")

    async def run(self, mic: AudioSource) -> None:
        self._worker = asyncio.create_task(self._utterance_worker())
        self._emit("state", state=self.state)
        try:
            async for frame in mic.frames():
                await self.on_frame(frame)
        finally:
            self._worker.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self._worker
            await mic.close()

    # ------------------------------------------------------------ frame path
    async def on_frame(self, frame: np.ndarray) -> None:
        if self.muted:
            return
        responding = self.state in (State.THINKING, State.SPEAKING)
        if responding and not self.cfg.barge_in:
            self.seg.reset()  # half-duplex: never listen to our own voice
            self.vad.reset()
            return
        prob = self.vad.prob(frame)
        if responding and not self._barged:
            self._barge_frames = self._barge_frames + 1 if prob >= self.cfg.barge_in_prob else 0
            if self._barge_frames * FRAME_MS >= self.cfg.barge_in_ms:
                await self.barge_in()
        started, audio = self.seg.push(frame, prob)
        if started:
            self._deadline = None  # the operator is talking: do not time out mid-sentence
        if audio is not None and (not responding or self._barged):
            self._pending += 1
            self._utterances.put_nowait((audio, self._now()))
        if (
            self.state is State.LISTENING
            and not self.seg.speaking
            and self._pending == 0
            and self._deadline is not None
            and self._now() > self._deadline
        ):
            self._to_standby("timeout")

    # ------------------------------------------------------------ state helpers
    def _set(self, state: State, **info: Any) -> None:
        if state is not self.state:
            self.state = state
            self._emit("state", state=state, **info)

    def _to_standby(self, why: str) -> None:
        self._deadline = None
        self._set(State.STANDBY, why=why)

    def _to_listening(self, timeout_s: float) -> None:
        self._timeout_s = timeout_s
        self._deadline = self._now() + timeout_s
        self._set(State.LISTENING)

    def _rearm(self) -> None:
        self._deadline = self._now() + self._timeout_s

    # ------------------------------------------------------------ utterances
    async def _utterance_worker(self) -> None:
        while True:
            audio, t_end = await self._utterances.get()
            try:
                await self.handle_utterance(audio, t_end)
            except Exception as exc:  # noqa: BLE001 - one bad utterance must not end the session
                self._emit("error", where="utterance", kind=type(exc).__name__)
            finally:
                self._pending -= 1
                # Nothing came of this utterance and we are still waiting: restart the clock
                # from now, so the time spent transcribing is not charged to the operator.
                if self.state is State.LISTENING and self._turn is None and self._pending == 0:
                    self._rearm()

    async def handle_utterance(self, audio: np.ndarray, t_speech_end: float | None = None) -> None:
        text = await self.stt.transcribe(audio)
        t_stt = self._now()
        if self.state is State.STANDBY:
            m = match_wake(text, name=self.cfg.wake_name)
            if m is None:
                return  # not for us; the transcript is dropped right here
            self._emit("wake", heard=m.heard, score=round(m.score, 2))
            if m.command:
                await self._respond(m.command, t_speech_end, t_stt)
            else:
                self._to_listening(self.cfg.listen_timeout_s)
            return
        if self.state is State.LISTENING:
            if not text:
                return
            if is_standby(text):
                self._emit("standby_command")
                self._to_standby("command")
                return
            m = match_wake(text, name=self.cfg.wake_name)
            command = m.command if m else text
            if not command:
                self._to_listening(self.cfg.listen_timeout_s)  # just "Ultron?" again
                return
            await self._respond(command, t_speech_end, t_stt)

    # ------------------------------------------------------------ response pipeline
    async def _respond(self, text: str, t_speech_end: float | None, t_stt: float) -> None:
        turn = self._turn = _Turn()
        self._barged = False
        self._barge_frames = 0
        self._deadline = None
        if t_speech_end is not None:
            turn.metrics["stt_ms"] = (t_stt - t_speech_end) * 1000
        self._emit("heard", text=text)
        self._set(State.THINKING)
        self.vad.reset()
        sentences: asyncio.Queue[str | None] = asyncio.Queue()
        speaker = turn.speaker = asyncio.create_task(self._speak_loop(sentences, turn, t_stt))
        chunker = SentenceChunker()
        reply = ""
        end_event: dict | None = None
        try:
            async for ev in self.client.turn(text, privacy=self.cfg.privacy, modality="voice"):
                kind = ev["type"]
                if kind == "start":
                    turn.cloud_voice_ok = (
                        self.cfg.privacy != "local_only" and ev.get("privacy_locked") is False
                    )
                    self._emit("engine", **ev["engine"], notice=ev.get("notice"))
                elif kind == "delta" and not turn.interrupt.is_set():
                    turn.metrics.setdefault("first_delta_ms", (self._now() - t_stt) * 1000)
                    for s in chunker.feed(ev["text"]):
                        sentences.put_nowait(s)
                elif kind == "end":
                    end_event = ev
                    reply = ev.get("reply", "")
                    if not turn.interrupt.is_set():
                        for s in chunker.flush():
                            sentences.put_nowait(s)
        except BackendError as exc:
            self._emit("error", where="backend", code=exc.code)
            if not turn.interrupt.is_set():
                sentences.put_nowait(self.cfg.error_line)
        except Exception as exc:  # noqa: BLE001 - connection dropped etc.
            self._emit("error", where="backend", kind=type(exc).__name__)
            if not turn.interrupt.is_set():
                sentences.put_nowait(self.cfg.error_line)
        sentences.put_nowait(None)
        try:
            await speaker  # cancelled by barge_in(); the synthesis thread is simply abandoned
        except asyncio.CancelledError:
            task = asyncio.current_task()
            if task is not None and task.cancelling():
                raise  # this turn itself is being cancelled (shutdown): propagate
        # Race: the operator cut in, but the server had already finished and saved the whole reply.
        # Its history must match what was actually heard, so amend the stored message.
        if (
            turn.interrupt.is_set()
            and end_event is not None
            and not end_event.get("interrupted")
            and end_event.get("message_id") is not None
        ):
            try:
                await self.client.cancel(
                    turn.heard_at_barge_in or "", message_id=end_event["message_id"]
                )
            except Exception as exc:  # noqa: BLE001 - history may now overstate what was heard
                self._emit("error", where="amend_history", kind=type(exc).__name__)
        self._emit("turn_done", reply=reply, interrupted=self._barged, **turn.metrics)
        self._turn = None
        if self._barged:
            return  # barge_in() already moved us to LISTENING
        self.vad.reset()
        self.seg.reset()
        if self.cfg.mode == "continuous":
            self._to_listening(self.cfg.follow_up_timeout_s)
        else:
            self._to_standby("done")

    async def _speak_loop(
        self, sentences: asyncio.Queue[str | None], turn: _Turn, t_stt: float
    ) -> None:
        while True:
            sentence = await sentences.get()
            if sentence is None:
                break
            if turn.interrupt.is_set():
                continue  # drain
            try:
                t_synth = self._now()
                if getattr(self.tts, "routes_by_privacy", False):
                    pcm = await self.tts.synthesize(sentence, private=not turn.cloud_voice_ok)
                    self._report_voice()
                else:
                    pcm = await self.tts.synthesize(sentence)
                turn.metrics.setdefault("first_tts_ms", (self._now() - t_synth) * 1000)
            except Exception as exc:  # noqa: BLE001
                self._emit("error", where="tts", kind=type(exc).__name__)
                continue
            if turn.interrupt.is_set() or len(pcm) == 0:
                continue
            if turn.played and self.cfg.sentence_gap_s > 0:
                gap = np.zeros(int(self.cfg.sentence_gap_s * self.tts.sample_rate), dtype=pcm.dtype)
                pcm = np.concatenate([gap, pcm])
            now = self._now()
            if "first_audio_ms" not in turn.metrics:
                turn.metrics["first_audio_ms"] = (now - t_stt) * 1000
                self._set(State.SPEAKING)
            begin = max(now, turn.expected_end)  # playback is contiguous unless TTS underruns
            end = begin + len(pcm) / self.tts.sample_rate
            turn.played.append(_Played(sentence, begin, end))
            turn.expected_end = end
            await self.player.play(pcm, self.tts.sample_rate)
        if not turn.interrupt.is_set():
            await self.player.finish()

    def _report_voice(self) -> None:
        """Tell the operator which voice spoke (and why not the cloud one) whenever it changes."""
        engine, reason = self.tts.last_engine, self.tts.last_reason
        if (engine, reason) != self._last_voice:
            self._last_voice = (engine, reason)
            self._emit("voice", engine=engine, reason=reason)

    # ------------------------------------------------------------ barge-in
    def spoken_text(self, at: float) -> str:
        """Best estimate of the words the operator actually heard by time `at`."""
        turn = self._turn
        if turn is None:
            return ""
        out: list[str] = []
        for p in turn.played:
            if at >= p.end:
                out.append(p.text)
            elif at > p.begin:
                frac = (at - p.begin) / max(p.end - p.begin, 1e-6)
                words = p.text.split()
                out.append(" ".join(words[: int(len(words) * frac)]))
        return " ".join(w for w in out if w).strip()

    async def new_conversation(self) -> None:
        """Operator pressed NEW: stop whatever is happening, erase this chat on the backend for
        good, and start the next one empty. Long-term notes are not touched (they are not part of
        the chat). If the backend cannot erase it the working memory is still reset, and the
        operator is told plainly that the old chat is still stored."""
        if self._turn is not None:
            await self.barge_in()
            for _ in range(100):  # let the interrupted turn finish being saved server-side
                if self._turn is None:
                    break
                await asyncio.sleep(0.05)
        while not self._utterances.empty():  # anything queued belonged to the old chat
            self._utterances.get_nowait()
            self._pending = max(self._pending - 1, 0)
        old = getattr(self.client, "conversation_id", None)
        try:
            result = await self.client.erase_conversation()
        except Exception as exc:  # noqa: BLE001 - reported, and the working memory is reset anyway
            self.client.conversation_id = None
            self._emit(
                "new_conversation", erased=False, reason=getattr(exc, "code", type(exc).__name__)
            )
            return
        self._emit("new_conversation", erased=result == "erased", had_chat=old is not None)

    async def barge_in(self) -> None:
        turn = self._turn
        if turn is None or turn.interrupt.is_set():
            return
        at = self._now()
        turn.interrupt.set()
        self._barged = True
        await self.player.stop()  # silence first: latency here is what the operator perceives
        if turn.speaker is not None:
            turn.speaker.cancel()
        spoken = self.spoken_text(at)
        turn.heard_at_barge_in = spoken
        try:
            await self.client.cancel(spoken)
        except Exception as exc:  # noqa: BLE001 - generation keeps running server-side
            self._emit("error", where="cancel", kind=type(exc).__name__)
        self._emit("barge_in", spoken=spoken)
        self._to_listening(self.cfg.follow_up_timeout_s)
        if self.seg.speaking:
            self._deadline = None  # the interrupting sentence is still being spoken


__all__ = ["SessionConfig", "State", "VoiceSession", "NullPlayer"]
