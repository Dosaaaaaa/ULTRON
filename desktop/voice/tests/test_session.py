from __future__ import annotations

import asyncio

import numpy as np

from tests.fakes import (
    SILENCE,
    SPEECH,
    BlockingPlayer,
    FakeClient,
    FakeClock,
    FakeStt,
    FakeTts,
    make_session,
    names,
)
from ultron_voice.client import BackendError
from ultron_voice.session import SessionConfig, State

AUDIO = SPEECH  # contents are irrelevant: FakeStt returns its script


async def feed(session, kind, n):
    for _ in range(n):
        await session.on_frame(SPEECH if kind == "speech" else SILENCE)


async def wait_for(session, state, limit=2.0):
    async def _w():
        while session.state is not state:  # noqa: ASYNC110 - polling a plain attribute
            await asyncio.sleep(0.005)

    await asyncio.wait_for(_w(), limit)


async def test_wake_then_command_responds_and_returns_to_standby():
    client, tts = FakeClient(), FakeTts()
    s, ev = make_session(FakeStt("Ultron", "what time is it"), client, tts=tts)
    await s.handle_utterance(AUDIO)
    assert s.state is State.LISTENING and client.turns == []
    await s.handle_utterance(AUDIO)
    assert [{k: v for k, v in t.items() if k != "conversation"} for t in client.turns] == [
        {"message": "what time is it", "privacy": "default", "modality": "voice"}
    ]
    assert tts.said == ["Hello there.", "This is the second sentence.", "And a third one."]
    assert s.player.samples > 0
    assert s.state is State.STANDBY  # command mode
    assert names(ev).count("wake") == 1 and "turn_done" in names(ev)


async def test_wake_word_and_command_in_one_breath():
    client = FakeClient()
    s, _ = make_session(FakeStt("Hey Ultron, what is the capital of France?"), client)
    await s.handle_utterance(AUDIO)
    assert client.turns[0]["message"] == "what is the capital of france"


async def test_speech_without_wake_word_is_ignored_and_never_leaves_the_device():
    client, stt = FakeClient(), FakeStt("the movie ultron was long", "remind me about lunch")
    s, ev = make_session(stt, client)
    await s.handle_utterance(AUDIO)
    await s.handle_utterance(AUDIO)
    assert client.turns == [] and s.state is State.STANDBY
    assert "wake" not in names(ev) and "heard" not in names(ev)


async def test_continuous_mode_follow_ups_need_no_wake_word_and_stand_by_ends_it():
    client = FakeClient()
    s, _ = make_session(
        FakeStt("Ultron", "first question", "second question", "stand by"),
        client,
        cfg=SessionConfig(mode="continuous"),
    )
    for _ in range(3):
        await s.handle_utterance(AUDIO)
    assert [t["message"] for t in client.turns] == ["first question", "second question"]
    assert s.state is State.LISTENING  # still in the session
    await s.handle_utterance(AUDIO)
    assert s.state is State.STANDBY and len(client.turns) == 2


async def test_command_mode_returns_to_standby_after_each_answer():
    s, _ = make_session(FakeStt("Ultron", "q1", "q2"), FakeClient())
    await s.handle_utterance(AUDIO)
    await s.handle_utterance(AUDIO)
    assert s.state is State.STANDBY
    await s.handle_utterance(AUDIO)  # "q2" without a wake word in standby
    assert len(s.client.turns) == 1


async def test_listening_times_out_back_to_standby():
    clock = FakeClock()
    s, ev = make_session(FakeStt("Ultron"), clock=clock, cfg=SessionConfig(listen_timeout_s=8))
    await s.handle_utterance(AUDIO)
    assert s.state is State.LISTENING
    clock.advance(7)
    await feed(s, "silence", 1)
    assert s.state is State.LISTENING
    clock.advance(2)
    await feed(s, "silence", 1)
    assert s.state is State.STANDBY and ("state", {"state": State.STANDBY, "why": "timeout"}) in ev


async def test_timeout_does_not_fire_while_operator_is_mid_sentence():
    clock = FakeClock()
    s, _ = make_session(FakeStt("Ultron"), clock=clock, cfg=SessionConfig(listen_timeout_s=8))
    await s.handle_utterance(AUDIO)
    await feed(s, "speech", 4)  # speech confirmed -> deadline cleared
    clock.advance(30)
    await feed(s, "speech", 1)
    assert s.state is State.LISTENING


async def test_mute_drops_all_microphone_input():
    stt = FakeStt("Ultron")
    s, ev = make_session(stt)
    s.set_muted(True)
    await feed(s, "speech", 10)
    await feed(s, "silence", 10)
    assert s._utterances.empty() and "muted" in names(ev)
    s.set_muted(False)
    await feed(s, "speech", 6)
    await feed(s, "silence", 5)
    assert s._utterances.qsize() == 1


async def test_backend_failure_is_spoken_honestly_and_session_survives():
    client = FakeClient(error=BackendError("models_unavailable", "none"))
    tts = FakeTts()
    s, ev = make_session(FakeStt("Ultron", "hello"), client, tts=tts)
    await s.handle_utterance(AUDIO)
    await s.handle_utterance(AUDIO)
    assert tts.said == ["I cannot reach my reasoning engine."]
    assert ("error", {"where": "backend", "code": "models_unavailable"}) in ev
    assert s.state is State.STANDBY


async def test_connection_drop_is_also_spoken_and_survives():
    s, ev = make_session(FakeStt("Ultron do something"), FakeClient(error=ConnectionResetError()))
    await s.handle_utterance(AUDIO)
    assert s.tts.said == ["I cannot reach my reasoning engine."] and s.state is State.STANDBY


async def test_worker_survives_an_stt_crash():
    s, ev = make_session(FakeStt(RuntimeError("whisper died"), "Ultron"))
    worker = asyncio.create_task(s._utterance_worker())
    s._utterances.put_nowait((AUDIO, 0.0))
    s._utterances.put_nowait((AUDIO, 0.0))
    await wait_for(s, State.LISTENING)
    worker.cancel()
    assert ("error", {"where": "utterance", "kind": "RuntimeError"}) in ev


async def test_latency_metrics_are_reported():
    clock = FakeClock()
    s, ev = make_session(FakeStt("Ultron what is up"), clock=clock)
    await s.handle_utterance(AUDIO, t_speech_end=clock() - 1.0)
    done = dict(ev)["turn_done"]
    assert {"stt_ms", "first_delta_ms", "first_audio_ms"} <= done.keys()
    assert done["stt_ms"] == 1000.0 and done["interrupted"] is False


# --------------------------------------------------------------------- barge-in
async def speaking_session(cfg=None, stt_texts=("Ultron tell me a story", "stop talking")):
    clock = FakeClock()
    client = FakeClient(
        deltas=(
            "Once upon a time there was a robot. ",
            "It learned to speak very slowly. ",
            "Then it learned to listen.",
        ),
        gate_after=2,
    )
    s, ev = make_session(FakeStt(*stt_texts), client, tts=FakeTts(0.5), clock=clock, cfg=cfg)
    task = asyncio.create_task(s.handle_utterance(AUDIO))
    await wait_for(s, State.SPEAKING)
    # first sentence (8 words = 4 s) started at t=100; second (7 words = 3.5 s) queued after it
    await asyncio.sleep(0.05)
    return s, client, clock, ev, task


async def test_barge_in_stops_audio_cancels_generation_and_reports_what_was_heard():
    s, client, clock, ev, task = await speaking_session()
    clock.advance(5.0)  # 4 s into... sentence one is 4 s long; we are 1 s into sentence two
    await feed(s, "speech", 9)  # 9 * 32 ms = 288 ms: not yet
    assert s.state is State.SPEAKING and not client.cancelled.is_set()
    await feed(s, "speech", 3)  # >= 320 ms of confident speech
    assert s.player.stopped == 1
    assert client.cancelled.is_set()
    spoken = client.cancelled_with[0]
    assert spoken.startswith("Once upon a time there was a robot.")  # first sentence fully heard
    assert "Then it learned" not in spoken  # never reached
    assert s.state is State.LISTENING
    client.gate.set()
    await task
    assert ("barge_in", {"spoken": spoken}) in ev
    assert dict(ev)["turn_done"]["interrupted"] is True
    assert (
        s.tts.said[-1] != "Then it learned to listen."
    )  # generation after the cancel is not voiced


async def test_partial_sentence_is_counted_proportionally():
    s, client, clock, _, task = await speaking_session()
    clock.advance(4.0 + 1.75)  # whole first sentence + half of the second (7 words -> 3 words)
    await feed(s, "speech", 12)
    spoken = client.cancelled_with[0]
    assert spoken == "Once upon a time there was a robot. It learned to"
    client.gate.set()
    await task


async def test_interrupting_speech_becomes_the_next_command():
    s, client, clock, _, task = await speaking_session()
    clock.advance(1.0)
    await feed(s, "speech", 12)  # barge-in; segmenter keeps this speech as an utterance
    await feed(s, "silence", 4)  # end of utterance
    client.gate.set()
    await task
    assert s._utterances.qsize() == 1
    audio, _ = s._utterances.get_nowait()
    await s.handle_utterance(audio)
    assert client.turns[-1]["message"] == "stop talking"


async def test_half_duplex_ignores_the_microphone_while_speaking():
    s, client, clock, _, task = await speaking_session(cfg=SessionConfig(barge_in=False))
    clock.advance(1.0)
    await feed(s, "speech", 30)
    assert s.player.stopped == 0 and not client.cancelled.is_set() and s._utterances.empty()
    client.gate.set()
    await task


async def test_short_noise_does_not_trigger_barge_in():
    s, client, clock, _, task = await speaking_session()
    for _ in range(5):
        await feed(s, "speech", 4)  # bursts below the threshold, separated by silence
        await feed(s, "silence", 2)
    assert not client.cancelled.is_set() and s.state is State.SPEAKING
    client.gate.set()
    await task


async def test_privacy_setting_is_forwarded_to_the_backend():
    client = FakeClient()
    s, _ = make_session(FakeStt("Ultron hello"), client, cfg=SessionConfig(privacy="local_only"))
    await s.handle_utterance(AUDIO)
    assert client.turns[0]["privacy"] == "local_only" and client.turns[0]["modality"] == "voice"


async def test_barge_in_after_server_finished_amends_history_to_what_was_heard():
    """No gate: the fake server streams everything instantly, long before playback ends."""
    clock = FakeClock()
    client = FakeClient(
        deltas=("First sentence here. ", "Second sentence is longer. ", "Third one.")
    )
    s, ev = make_session(FakeStt("Ultron go"), client, tts=FakeTts(0.5), clock=clock)
    s.player = BlockingPlayer()
    task = asyncio.create_task(s.handle_utterance(AUDIO))
    await wait_for(s, State.SPEAKING)
    await asyncio.sleep(0.05)
    clock.advance(1.0)  # one second into "First sentence here." (3 words = 1.5 s)
    await feed(s, "speech", 12)
    await task
    assert client.amendments, "history must be amended: the server saved the full reply"
    message_id, spoken = client.amendments[0]
    assert message_id == 42 and spoken == "First sentence"  # 2 of 3 words heard
    assert dict(ev)["turn_done"]["interrupted"] is True


async def test_no_amendment_when_the_operator_never_interrupts():
    client = FakeClient()
    s, _ = make_session(FakeStt("Ultron go"), client)
    await s.handle_utterance(AUDIO)
    assert client.amendments == [] and client.cancelled_with == []


async def test_follow_up_timeout_never_fires_while_speech_is_pending_or_transcribing():
    """Regression (found in live e2e): the interrupting sentence was dropped by a timeout."""
    clock = FakeClock()
    s, ev = make_session(FakeStt("Ultron"), clock=clock, cfg=SessionConfig(listen_timeout_s=3))
    await s.handle_utterance(AUDIO)
    assert s.state is State.LISTENING
    s._pending = 1  # an utterance is queued / in STT
    clock.advance(30)
    await feed(s, "silence", 3)
    assert s.state is State.LISTENING, "must wait for the queued utterance"
    s._pending = 0
    await feed(s, "silence", 1)
    assert s.state is State.STANDBY


async def test_empty_transcript_rearms_the_clock_instead_of_charging_stt_time():
    clock = FakeClock()
    s, _ = make_session(FakeStt("Ultron", ""), clock=clock, cfg=SessionConfig(listen_timeout_s=8))
    await s.handle_utterance(AUDIO)
    worker = asyncio.create_task(s._utterance_worker())
    clock.advance(7)
    s._pending += 1
    s._utterances.put_nowait((AUDIO, clock()))  # transcribes to ""
    await asyncio.sleep(0.05)
    worker.cancel()
    clock.advance(5)  # 12 s after wake, but only 5 s after the (empty) utterance was handled
    await feed(s, "silence", 1)
    assert s.state is State.LISTENING


async def test_interrupting_sentence_is_handled_even_if_transcription_outlasts_the_timeout():
    clock = FakeClock()
    client = FakeClient(
        deltas=("Once upon a time there was a robot. ", "More words follow here. "), gate_after=1
    )
    s, ev = make_session(
        FakeStt("Ultron story", "stop talking"),
        client,
        tts=FakeTts(0.5),
        clock=clock,
        cfg=SessionConfig(follow_up_timeout_s=3.0),
    )
    task = asyncio.create_task(s.handle_utterance(AUDIO))
    await wait_for(s, State.SPEAKING)
    await asyncio.sleep(0.05)
    clock.advance(1.0)
    await feed(s, "speech", 12)  # barge-in while the operator is still talking
    clock.advance(10)  # far beyond follow_up_timeout_s
    await feed(s, "speech", 2)
    assert s.state is State.LISTENING, "must not time out mid-sentence"
    await feed(s, "silence", 4)  # sentence ends -> queued
    clock.advance(10)
    await feed(s, "silence", 1)
    assert s.state is State.LISTENING, "must not time out while the utterance is queued"
    client.gate.set()
    await task
    audio, _ = s._utterances.get_nowait()
    await s.handle_utterance(audio)
    assert client.turns[-1]["message"] == "stop talking"


async def test_barge_in_does_not_wait_for_an_inflight_synthesis():
    entered = asyncio.Event()

    class SlowTts(FakeTts):
        async def synthesize(self, text):
            entered.set()
            await asyncio.sleep(30)  # an uninterruptible synthesis in progress
            return await super().synthesize(text)

    client = FakeClient(deltas=("A long first sentence is here. ", "And more. "), gate_after=1)
    s, _ = make_session(FakeStt("Ultron go"), client, tts=SlowTts(0.5), clock=FakeClock())
    task = asyncio.create_task(s.handle_utterance(AUDIO))
    await asyncio.wait_for(entered.wait(), 2.0)  # synthesis is now genuinely in flight
    s.state = State.SPEAKING  # no audio exists yet; force the state a real run would be in
    await feed(s, "speech", 12)
    assert client.cancelled.is_set()
    client.gate.set()
    await asyncio.wait_for(task, 2.0)  # would take 30 s if the session awaited the synthesis


async def test_sentence_gap_is_added_between_sentences_but_not_before_the_first():
    from tests.fakes import FakePlayer  # noqa: PLC0415

    class RecordingPlayer(FakePlayer):
        def __init__(self):
            super().__init__()
            self.chunks: list[np.ndarray] = []

        async def play(self, pcm, sample_rate):
            self.chunks.append(pcm)
            await super().play(pcm, sample_rate)

    tts = FakeTts(seconds_per_word=0.1)
    s, _ = make_session(FakeStt("Ultron", "hello"), tts=tts, cfg=SessionConfig(sentence_gap_s=0.5))
    s.player = RecordingPlayer()
    await s.handle_utterance(AUDIO)
    await s.handle_utterance(AUDIO)
    lengths = [len(c) for c in s.player.chunks]
    words = [len(t.split()) for t in tts.said]
    expected = [int(24000 * 0.1 * w) for w in words]
    assert lengths[0] == expected[0]  # no delay before the reply starts
    assert lengths[1:] == [e + int(0.5 * 24000) for e in expected[1:]]
    assert (s.player.chunks[1][: int(0.5 * 24000)] == 0).all()


async def test_no_sentence_gap_by_default():
    s, _ = make_session(FakeStt("Ultron", "hello"), tts=FakeTts(seconds_per_word=0.1))
    await s.handle_utterance(AUDIO)
    await s.handle_utterance(AUDIO)
    assert s.player.samples == sum(int(24000 * 0.1 * n) for n in (2, 5, 4))


# ---------------------------------------------------------------- NEW: erase the chat, start empty
async def test_new_erases_the_chat_and_the_next_turn_starts_a_fresh_conversation():
    client = FakeClient()
    s, ev = make_session(FakeStt("Ultron", "first question", "Ultron", "second question"), client)
    await s.handle_utterance(AUDIO)
    await s.handle_utterance(AUDIO)
    assert client.conversation_id == "c" and client.turns[0]["conversation"] is None
    await s.new_conversation()
    assert client.erased == ["c"] and client.conversation_id is None
    assert ("new_conversation", {"erased": True, "had_chat": True}) in ev
    await s.handle_utterance(AUDIO)
    await s.handle_utterance(AUDIO)
    assert client.turns[1]["conversation"] is None  # not the erased conversation: empty history


async def test_new_with_nothing_said_yet_says_there_was_nothing_to_erase():
    client = FakeClient()
    s, ev = make_session(FakeStt(), client)
    await s.new_conversation()
    assert client.erased == [] and ("new_conversation", {"erased": False, "had_chat": False}) in ev


async def test_new_while_speaking_stops_the_audio_and_the_generation_first():
    s, client, clock, ev, task = await speaking_session()
    clock.advance(5.0)
    client.conversation_id = "c"  # the conversation exists on the backend by now
    new = asyncio.create_task(s.new_conversation())
    await asyncio.sleep(0.05)
    assert s.player.stopped >= 1 and client.cancelled.is_set()  # silence and cancel came first
    client.gate.set()
    await task
    await new
    assert client.erased == ["c"] and client.conversation_id is None
    assert names(ev).index("barge_in") < names(ev).index("new_conversation")


async def test_if_the_backend_cannot_erase_it_the_operator_is_told_and_memory_still_resets():
    client = FakeClient()
    client.conversation_id = "c"
    client.erase_error = BackendError("unreachable", "ConnectError")
    s, ev = make_session(FakeStt(), client)
    await s.new_conversation()
    assert client.conversation_id is None  # the next turn will not carry the old history
    assert ("new_conversation", {"erased": False, "reason": "unreachable"}) in ev


async def test_utterances_queued_before_new_are_dropped():
    client = FakeClient()
    client.conversation_id = "c"
    s, _ = make_session(FakeStt(), client)
    s._utterances.put_nowait((AUDIO, 0.0))
    s._pending = 1
    await s.new_conversation()
    assert s._utterances.empty() and s._pending == 0
