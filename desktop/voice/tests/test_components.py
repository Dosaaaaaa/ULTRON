from __future__ import annotations

import asyncio

import numpy as np
import pytest

from tests.fakes import SILENCE, SPEECH
from ultron_voice.audio import FRAME_SAMPLES, ArraySource
from ultron_voice.stt import clean_transcript, to_wav_bytes
from ultron_voice.tts import (
    SentenceChunker,
    apply_metallic,
    character_fx,
    clean_for_speech,
    lower_pitch,
    plan_delivery,
    robot_fx,
    to_int16,
)
from ultron_voice.vad import Segmenter, VadConfig
from ultron_voice.wake import is_standby, match_wake


# ---------------------------------------------------------------- wake word matching
@pytest.mark.parametrize(
    ("heard", "command"),
    [
        ("Ultron", ""),
        ("Ultron.", ""),
        ("ULTRON, what time is it?", "what time is it"),
        ("Hey Ultron what's the weather", "what's the weather"),
        ("okay ultron stand by", "stand by"),
        ("Ul tron, are you there", "are you there"),  # split by the recogniser
    ],
)
def test_wake_word_accepted(heard, command):
    m = match_wake(heard)
    assert m is not None and m.command == command


@pytest.mark.parametrize(
    "heard",
    [
        "",
        "hello there",
        "what time is it",
        "ultra",
        "the ultra marathon",
        "electron microscope",
        "patron saint",
        "Elton John",
        "Alderon",
        "it is not about ultron",
        "I watched Avengers Age of Ultron last night",
        "turn on the lights",
        "Hey Siri",
        "ultimate",
        "alternate",
        "neutron",
        "Cutron",
        "retron",
    ],
)
def test_wake_word_rejected(heard):
    assert match_wake(heard) is None


@pytest.mark.parametrize(
    "heard",
    ["Ultrahn open the log", "Altron what is this", "Oltron.", "Ultran, hello", "All tron play"],
)
def test_common_misspellings_now_wake(heard):
    """The consonant skeleton L-T-R-N catches recogniser spellings of 'Ultron' (the user had to
    say it several times before)."""
    assert match_wake(heard) is not None


def test_wake_word_only_counts_at_the_start():
    assert match_wake("so then ultron said nothing") is None
    assert match_wake("tell me about ultron") is None


@pytest.mark.parametrize(
    "text", ["stand by", "Stand by.", "standby", "okay stand by", "please stand by", "go to sleep"]
)
def test_standby_phrases(text):
    assert is_standby(text)


@pytest.mark.parametrize("text", ["what is a standby generator", "I stand by my decision", "stay"])
def test_standby_is_not_a_substring_match(text):
    assert not is_standby(text)


# ---------------------------------------------------------------- text for speech
def test_clean_for_speech_removes_markup():
    raw = (
        "**Result:** see https://example.com/x?y=1\n- first `code` item\n1. second\n```py\nx=1\n```"
    )
    out = clean_for_speech(raw)
    assert "*" not in out and "`" not in out and "http" not in out and "x=1" not in out
    assert "a link" in out and "first" in out and "second" in out


def test_chunker_releases_first_sentence_early_then_merges_short_ones():
    c = SentenceChunker(min_chars=24, min_first_chars=10)
    got: list[str] = []
    for piece in ["Hello th", "ere. Yes", ". Okay. That is a long", " enough sentence now. Tail"]:
        got += c.feed(piece)
    got += c.flush()
    assert got[0] == "Hello there."  # first sentence out as soon as complete
    assert got[1].startswith("Yes. Okay. That is a long enough sentence now.")  # short ones merged
    assert got[-1] == "Tail"


def test_chunker_does_not_split_decimals_or_wait_forever():
    c = SentenceChunker()
    assert c.feed("The value is 3.") == []  # might be 3.5: hold
    assert c.feed("5 percent. Next") == ["The value is 3.5 percent."]
    assert c.flush() == ["Next"]


def test_chunker_flush_on_empty_and_whitespace():
    c = SentenceChunker()
    assert c.flush() == [] and c.feed("   ") == [] and c.flush() == []


def test_clean_transcript_drops_whisper_noise_tags():
    assert clean_transcript(" [BLANK_AUDIO] ") == ""
    assert clean_transcript("Ultron (coughs) what *music* is this [MUSIC]") == "Ultron what is this"


def test_wav_header_is_valid():
    import io
    import wave

    pcm = np.arange(1600, dtype=np.int16)
    with wave.open(io.BytesIO(to_wav_bytes(pcm)), "rb") as w:
        assert (w.getnchannels(), w.getsampwidth(), w.getframerate(), w.getnframes()) == (
            1,
            2,
            16000,
            1600,
        )


def test_metallic_fx_is_identity_at_zero_bounded_otherwise():
    x = (np.random.default_rng(0).standard_normal(24000) * 0.3).astype(np.float32)
    assert np.array_equal(apply_metallic(x, 24000, 0.0), x)
    y = apply_metallic(x, 24000, 0.5)
    assert y.shape == x.shape and np.max(np.abs(y)) <= np.max(np.abs(x)) + 1e-6
    assert not np.allclose(y, x) and np.isfinite(y).all()
    assert to_int16(np.array([2.0, -2.0, 0.0], dtype=np.float32)).tolist() == [32767, -32767, 0]


# ---------------------------------------------------------------- segmenter
def run_segmenter(pattern: str, cfg: VadConfig | None = None):
    """pattern: 's' = speech frame, '.' = silence frame."""
    seg = Segmenter(cfg or VadConfig(min_speech_ms=64, end_silence_ms=96, preroll_ms=64))
    starts, utterances = 0, []
    for ch in pattern:
        frame = SPEECH if ch == "s" else SILENCE
        started, audio = seg.push(frame, 1.0 if ch == "s" else 0.0)
        starts += started
        if audio is not None:
            utterances.append(audio)
    return starts, utterances


def test_segmenter_one_utterance_with_preroll_and_trailing_silence():
    starts, utts = run_segmenter("...sssssss....")
    assert starts == 1 and len(utts) == 1
    n = len(utts[0]) // FRAME_SAMPLES
    assert 7 + 2 <= n <= 7 + 2 + 3 + 1  # speech + preroll(2) + trailing silence (3 frames)


def test_segmenter_ignores_clicks_shorter_than_min_speech():
    assert run_segmenter("..s....s....")[1] == []


def test_segmenter_bridges_short_pauses_inside_a_sentence():
    starts, utts = run_segmenter("sssss.ssssss.....")
    assert starts == 1 and len(utts) == 1


def test_segmenter_splits_on_long_pause():
    assert len(run_segmenter("ssssss.....ssssss.....")[1]) == 2


def test_segmenter_force_ends_overlong_speech():
    cfg = VadConfig(min_speech_ms=64, end_silence_ms=96, max_utterance_s=0.5)
    assert len(run_segmenter("s" * 40, cfg)[1]) >= 1


async def test_array_source_frames_and_padding():
    src = ArraySource(np.ones(FRAME_SAMPLES * 3, dtype=np.int16), tail_silence_s=0.1)
    frames = [f async for f in src.frames()]
    assert all(len(f) == FRAME_SAMPLES for f in frames) and len(frames) >= 4
    assert frames[0][0] == 1 and frames[-1][0] == 0


def test_chunker_releases_a_long_first_clause_at_a_comma():
    c = SentenceChunker()
    out = c.feed("Once upon a time in a far off valley, ")
    assert out == ["Once upon a time in a far off valley,"]  # spoken before the sentence ends
    assert c.feed("there lived a robot. ") == []  # later text merges normally
    assert c.flush() == ["there lived a robot."]


def test_chunker_does_not_split_short_leading_clauses():
    c = SentenceChunker()
    assert c.feed("Yes, ") == [] and c.feed("I can do that. ") == ["Yes, I can do that."]


async def test_sigterm_cancels_the_run_so_cleanup_executes():
    import os
    import signal

    from ultron_voice.cli import install_stop_handlers

    cleaned = []

    async def fake_run():
        install_stop_handlers(asyncio.get_running_loop(), asyncio.current_task())
        try:
            await asyncio.sleep(30)
        finally:
            cleaned.append(True)

    task = asyncio.create_task(fake_run())
    await asyncio.sleep(0.1)
    os.kill(os.getpid(), signal.SIGTERM)
    with __import__("contextlib").suppress(asyncio.CancelledError):
        await asyncio.wait_for(task, 2)
    assert cleaned == [True]


def _dominant_hz(x: np.ndarray, rate: int) -> float:
    spec = np.abs(np.fft.rfft(x * np.hanning(len(x))))
    return float(np.argmax(spec)) * rate / len(x)


def test_lower_pitch_moves_the_frequency_down_by_the_factor_and_lengthens():
    rate = 24000
    t = np.arange(rate) / rate
    x = np.sin(2 * np.pi * 200 * t).astype(np.float32)
    y = lower_pitch(x, 0.88)
    assert abs(_dominant_hz(y, rate) - 200 * 0.88) < 2.0
    assert abs(len(y) - len(x) / 0.88) <= 1
    assert float(np.max(np.abs(y))) > 0.95  # not attenuated


def test_lower_pitch_one_is_the_identity_and_bad_factors_are_rejected():
    import pytest  # noqa: PLC0415

    x = np.arange(100, dtype=np.float32)
    assert lower_pitch(x, 1.0) is x
    assert lower_pitch(np.zeros(1, dtype=np.float32), 0.8).shape == (1,)
    for bad in (0.0, 0.3, 1.6, -1.0):
        with pytest.raises(ValueError):
            lower_pitch(x, bad)


def test_pitch_comes_from_the_environment_and_defaults_to_a_normal_voice(monkeypatch):
    from ultron_voice.config import VoiceConfig  # noqa: PLC0415

    monkeypatch.delenv("ULTRON_VOICE_TTS_PITCH", raising=False)
    assert VoiceConfig().tts_pitch == 1.0
    monkeypatch.setenv("ULTRON_VOICE_TTS_PITCH", "0.88")
    assert VoiceConfig().tts_pitch == 0.88


SR = 24000


def _speechlike() -> np.ndarray:
    """0.5 s voiced burst (150 Hz + harmonics), 0.5 s silence, 0.5 s burst."""
    t = np.arange(SR // 2) / SR
    burst = sum(np.sin(2 * np.pi * 150 * k * t) / k for k in range(1, 12)) * 0.3
    return np.concatenate([burst, np.zeros(SR // 2), burst]).astype(np.float32)


def _band_energy(x: np.ndarray, lo: float, hi: float) -> float:
    spec = np.abs(np.fft.rfft(x)) ** 2
    f = np.fft.rfftfreq(len(x), 1 / SR)
    return float(spec[(f >= lo) & (f < hi)].sum())


def test_character_fx_off_is_identity_and_is_deterministic():
    x = _speechlike()
    assert character_fx(x, SR) is x
    a = character_fx(x, SR, gravel=0.6, body=0.5, reverb=0.3)
    b = character_fx(x, SR, gravel=0.6, body=0.5, reverb=0.3)
    assert np.array_equal(a, b) and np.isfinite(a).all()


def test_character_fx_keeps_the_peak_level():
    x = _speechlike()
    y = character_fx(x, SR, gravel=1.0, body=1.0, reverb=1.0)
    assert abs(float(np.max(np.abs(y))) - float(np.max(np.abs(x)))) < 0.01 * float(
        np.max(np.abs(x))
    )


def test_body_adds_low_frequency_weight():
    x = _speechlike()
    y = character_fx(x, SR, body=1.0)
    low_share = lambda v: _band_energy(v, 20, 250) / _band_energy(v, 20, 8000)  # noqa: E731
    assert low_share(y) > low_share(x) * 1.15 and len(y) == len(x)


def test_gravel_adds_rasp_to_speech_but_not_to_silence():
    x = _speechlike()
    y = character_fx(x, SR, gravel=0.8)
    assert _band_energy(y[: SR // 2], 800, 4500) > 2 * _band_energy(x[: SR // 2], 800, 4500)
    gap_before, gap_after = x[SR // 2 + 2000 : SR - 2000], y[SR // 2 + 2000 : SR - 2000]
    assert float(np.sqrt(np.mean(gap_after**2))) < 0.02 * float(np.sqrt(np.mean(y[:2000] ** 2)))
    assert float(np.max(np.abs(gap_before))) == 0.0


def test_reverb_appends_a_decaying_tail_after_the_speech_ends():
    x = np.concatenate([_speechlike()[: SR // 2], np.zeros(100, dtype=np.float32)])
    y = character_fx(x, SR, reverb=0.8)
    assert len(y) == len(x) + int(0.28 * SR)
    tail = y[len(x) : len(x) + 2000]
    late = y[-2000:]
    assert float(np.sqrt(np.mean(tail**2))) > 5 * float(np.sqrt(np.mean(late**2))) > 0


def test_a_normal_low_lag_voice_is_the_default_and_the_character_is_opt_in(monkeypatch):
    from ultron_voice.config import VoiceConfig  # noqa: PLC0415

    knobs = ("VOICE", "METAL", "PITCH", "BODY", "GRAVEL", "REVERB", "RESONANCE", "RING")
    knobs += ("PAUSE", "SENTENCE_GAP", "DELIVERY")
    for k in knobs:
        monkeypatch.delenv(f"ULTRON_VOICE_TTS_{k}", raising=False)
    c = VoiceConfig()
    assert (c.tts_voice, c.tts_pitch, c.tts_body, c.tts_gravel, c.tts_metal) == (
        "bm_lewis",
        1.0,
        0.0,
        0.0,
        0.0,
    )
    assert (c.tts_resonance, c.tts_ring, c.tts_reverb, c.tts_speed) == (0.0, 0.0, 0.0, 1.0)
    assert (c.tts_pause, c.tts_sentence_gap, c.tts_delivery) == (0.12, 0.1, 0.0)
    monkeypatch.setenv("ULTRON_VOICE_TTS_PITCH", "0.8")
    monkeypatch.setenv("ULTRON_VOICE_TTS_BODY", "0.8")
    monkeypatch.setenv("ULTRON_VOICE_TTS_RING", "0.1")
    c = VoiceConfig()
    assert (c.tts_pitch, c.tts_body, c.tts_ring) == (0.8, 0.8, 0.1)


# ---------------------------------------------------------------- robot layer
def test_robot_fx_off_is_identity_and_keeps_the_peak():
    x = _speechlike()
    assert robot_fx(x, SR) is x
    y = robot_fx(x, SR, resonance=1.0, ring=0.28)
    assert y.shape == x.shape and np.isfinite(y).all()
    assert abs(float(np.max(np.abs(y))) - float(np.max(np.abs(x)))) < 0.01 * float(
        np.max(np.abs(x))
    )


def test_resonance_and_ring_each_audibly_change_the_sound_and_more_changes_more():
    x = _speechlike()

    def change(y: np.ndarray) -> float:
        y = y * (np.sqrt(np.mean(x**2)) / np.sqrt(np.mean(y**2)))
        return float(np.mean((y - x) ** 2) / np.mean(x**2))

    res = change(robot_fx(x, SR, resonance=1.0))
    ring_low = change(robot_fx(x, SR, ring=0.1))
    ring_high = change(robot_fx(x, SR, ring=0.4))
    assert res > 0.1 and ring_high > ring_low > 0.005
    assert change(robot_fx(x, SR, resonance=0.3)) < res


# ---------------------------------------------------------------- gaps and delivery
def test_plan_delivery_without_settings_is_one_continuous_piece_per_sentence_part():
    beats = plan_delivery("Ask, and I will answer.")
    assert [b.pause_s for b in beats] == [0.0, 0.0] and [b.speed_mult for b in beats] == [1.0, 1.0]


def test_plan_delivery_puts_gaps_where_the_punctuation_is():
    beats = plan_delivery(
        "I am ULTRON. Ask, and I will answer; do not repeat yourself...", pause_s=0.3
    )
    assert [b.text for b in beats] == [
        "I am ULTRON.",
        "Ask,",
        "and I will answer;",
        "do not repeat yourself...",
    ]
    assert [round(b.pause_s, 3) for b in beats] == [0.57, 0.3, 0.42, 0.0]


def test_plan_delivery_last_beat_has_no_trailing_gap_and_text_is_never_lost():
    text = "No. I will not, and you know why."
    beats = plan_delivery(text, pause_s=0.28, delivery=1.0)
    assert beats[-1].pause_s == 0.0 and " ".join(b.text for b in beats) == text
    assert all(b.pause_s > 0 for b in beats[:-1])


def test_plan_delivery_slows_short_pieces_and_the_closing_piece_only_with_delivery():
    beats = plan_delivery(
        "Listen. Every sentence you speak is a long road to nowhere.", delivery=1.0
    )
    assert beats[0].speed_mult == pytest.approx(0.88)  # short piece: deliberate
    assert beats[1].speed_mult == pytest.approx(0.95)  # long, final: a little slower
    flat = plan_delivery(
        "Listen. Every sentence you speak is a long road to nowhere.", delivery=0.0
    )
    assert [b.speed_mult for b in flat] == [1.0, 1.0]


def test_plan_delivery_does_not_split_abbreviations_or_decimals():
    assert [b.text for b in plan_delivery("Dr. Banner is 3.5 metres tall.", pause_s=0.3)] == [
        "Dr. Banner is 3.5 metres tall."
    ]
    assert plan_delivery("   ") == []


def test_render_inserts_silence_between_beats_and_not_after_the_last():
    from ultron_voice.tts import KokoroTTS  # noqa: PLC0415

    class FakeKokoro:
        def __init__(self):
            self.calls: list[tuple[str, float]] = []

        def create(self, text, voice, speed, lang):
            self.calls.append((text, speed))
            return np.full(2400, 0.5, dtype=np.float32), SR  # 0.1 s of tone

    tts = object.__new__(KokoroTTS)
    tts._k, tts._voice, tts.lang = FakeKokoro(), "v", "en-gb"
    tts.speed, tts.pitch, tts.metal, tts.gravel, tts.body, tts.reverb = 1.0, 1.0, 0, 0, 0, 0
    tts.resonance, tts.ring, tts.pause, tts.delivery = 0, 0, 0.3, 1.0
    pcm = tts._render("Ask, and I will answer.")
    assert len(pcm) == 2400 + int(0.3 * SR) + 2400  # tone + comma gap + tone, no tail gap
    assert pcm[2400 + 100] == 0 and pcm[100] != 0 and pcm[-100] != 0
    assert tts._k.calls[0][1] == pytest.approx(0.88)  # "Ask," is short -> slower
    assert tts._k.calls[1][1] == pytest.approx(0.95)  # closing piece


# ---------------------------------------------------------------- configurable wake name (Friday)
@pytest.mark.parametrize(
    ("heard", "command"),
    [
        ("Friday", ""),
        ("Friday.", ""),
        ("Hey Friday, what's the weather", "what's the weather"),
        ("okay friday play some jazz", "play some jazz"),
        ("Fridays open the log", "open the log"),  # recogniser plural
        ("Fried a, are you there", "are you there"),  # split by the recogniser
    ],
)
def test_friday_accepted(heard, command):
    m = match_wake(heard, name="friday")
    assert m is not None and m.command == command


@pytest.mark.parametrize(
    "heard",
    [
        "",
        "hello there",
        "see you on friday",
        "it is friday night",
        "Freddy",
        "birthday",
        "fridge",
        "ultron",
        "Thursday",
    ],
)
def test_friday_rejected(heard):
    assert match_wake(heard, name="friday") is None


def test_ultron_still_works_and_friday_does_not_wake_it():
    assert match_wake("Ultron, hi", name="ultron") is not None
    assert match_wake("Friday", name="ultron") is None
    assert match_wake("Ultron") is not None  # the default name is unchanged


def test_wake_name_comes_from_env_then_file_then_friday(monkeypatch, tmp_path):
    from ultron_voice.config import VoiceConfig, wake_name_file  # noqa: PLC0415

    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))
    monkeypatch.delenv("ULTRON_VOICE_WAKE_NAME", raising=False)
    monkeypatch.delenv("ULTRON_VOICE_STT_PROMPT", raising=False)
    assert VoiceConfig().wake_name == "friday"
    assert VoiceConfig().stt_prompt == "Friday."
    wake_name_file().parent.mkdir(parents=True)
    wake_name_file().write_text("Jarvis\n")
    assert VoiceConfig().wake_name == "jarvis"
    assert VoiceConfig().stt_prompt == "Jarvis."
    monkeypatch.setenv("ULTRON_VOICE_WAKE_NAME", "ultron")
    assert VoiceConfig().wake_name == "ultron"
    monkeypatch.setenv(
        "ULTRON_VOICE_WAKE_NAME", "not a word!"
    )  # junk is ignored, the file still applies
    assert VoiceConfig().wake_name == "jarvis"
