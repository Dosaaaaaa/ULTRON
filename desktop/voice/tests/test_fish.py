"""Cloud voice: request shape, error mapping, audio parsing, and (above all) the privacy gating."""

from __future__ import annotations

import json
import struct

import httpx
import numpy as np
import pytest

from tests.fakes import FakeClient, FakeStt, FakeTts, make_session, names
from tests.test_session import AUDIO
from ultron_voice import config as voice_config
from ultron_voice.config import VoiceConfig
from ultron_voice.factory import make_tts
from ultron_voice.fish import CloudTtsError, FishTTS, HybridTTS, pcm_from_body
from ultron_voice.session import SessionConfig

KEY = "fish-secret-key-0123456789"
VOICE_ID = "092277500c51491a9e20d1acd1525f58"


def wav(
    samples: np.ndarray, rate: int = 24000, channels: int = 1, bogus_len: bool = False
) -> bytes:
    data = samples.astype("<i2").tobytes()
    size = 0xFFFFFFFF if bogus_len else len(data)
    fmt = struct.pack("<HHIIHH", 1, channels, rate, rate * 2 * channels, 2 * channels, 16)
    return (
        b"RIFF" + struct.pack("<I", 36 + len(data)) + b"WAVE"
        + b"fmt " + struct.pack("<I", 16) + fmt
        + b"data" + struct.pack("<I", size) + data
    )  # fmt: skip


SAMPLES = (np.arange(2400) % 200 - 100).astype(np.int16)


def fish(handler) -> FishTTS:
    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    return FishTTS(client=client, api_key=KEY, reference_id=VOICE_ID)


# ------------------------------------------------------------------ the HTTP adapter
async def test_request_has_the_documented_shape_and_the_key_only_in_the_header():
    seen = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen.update(url=str(request.url), headers=request.headers, body=json.loads(request.content))
        return httpx.Response(200, content=wav(SAMPLES))

    pcm = await fish(handler).synthesize("I am ULTRON.")
    assert seen["url"] == "https://api.fish.audio/v1/tts"
    assert seen["headers"]["authorization"] == f"Bearer {KEY}"
    assert seen["headers"]["model"] == "s2.1-pro-free"  # never falls back to the billed default
    assert seen["headers"]["content-type"] == "application/json"
    body = seen["body"]
    assert body["text"] == "I am ULTRON." and body["reference_id"] == VOICE_ID
    assert body["format"] == "wav" and body["sample_rate"] == 24000
    assert KEY not in json.dumps(body) and np.array_equal(pcm, SAMPLES)


async def test_markup_is_cleaned_before_it_leaves_the_machine():
    seen = {}

    def handler(request):
        seen["text"] = json.loads(request.content)["text"]
        return httpx.Response(200, content=wav(SAMPLES))

    await fish(handler).synthesize("See https://example.com/secret?x=1 and `code` **now**")
    assert "http" not in seen["text"] and "`" not in seen["text"] and "*" not in seen["text"]


@pytest.mark.parametrize(
    ("status", "kind"),
    [(401, "rejected_credentials"), (403, "rejected_credentials"), (402, "no_credit"),
     (429, "rate_limited"), (503, "http_503")],
)  # fmt: skip
async def test_http_errors_map_to_safe_labels_without_bodies_or_keys(status, kind):
    def handler(request):
        return httpx.Response(status, json={"message": f"bad key {KEY}"})

    with pytest.raises(CloudTtsError) as e:
        await fish(handler).synthesize("hello")
    assert e.value.kind == kind and KEY not in str(e.value) and "bad key" not in str(e.value)


async def test_timeouts_and_network_errors_are_reported_not_raised_raw():
    def slow(request):
        raise httpx.ReadTimeout("t", request=request)

    def down(request):
        raise httpx.ConnectError("c", request=request)

    with pytest.raises(CloudTtsError) as e:
        await fish(slow).synthesize("hello")
    assert e.value.kind == "timeout"
    with pytest.raises(CloudTtsError) as e:
        await fish(down).synthesize("hello")
    assert e.value.kind == "unreachable"


async def test_nothing_to_say_sends_nothing():
    def handler(request):  # pragma: no cover - must not be called
        raise AssertionError("a request was sent for empty text")

    assert len(await fish(handler).synthesize("   `code only`  ")) == 0


# ------------------------------------------------------------------ audio parsing
def test_wav_with_a_streaming_header_of_unknown_length_is_read_to_the_end():
    assert np.array_equal(pcm_from_body(wav(SAMPLES, bogus_len=True)), SAMPLES)


def test_wav_whose_header_declares_zero_length_is_read_to_the_end():
    body = bytearray(wav(SAMPLES))
    body[40:44] = struct.pack("<I", 0)  # data chunk size written as 0 by a streaming encoder
    assert np.array_equal(pcm_from_body(bytes(body)), SAMPLES)


def test_raw_pcm_and_odd_trailing_byte():
    assert np.array_equal(pcm_from_body(SAMPLES.astype("<i2").tobytes() + b"\x01"), SAMPLES)


def test_other_sample_rates_are_resampled_and_stereo_takes_the_first_channel():
    out = pcm_from_body(wav(np.tile(SAMPLES[:1200], 1), rate=16000))
    assert abs(len(out) - 1200 * 24000 / 16000) <= 1
    stereo = np.stack([SAMPLES[:100], np.zeros(100, dtype=np.int16)], axis=1).reshape(-1)
    assert np.array_equal(pcm_from_body(wav(stereo, channels=2)), SAMPLES[:100])


def test_unsupported_or_empty_audio_is_an_error():
    bad = bytearray(wav(SAMPLES))
    bad[34:36] = struct.pack("<H", 8)  # claims 8-bit samples
    for body in (bytes(bad), b"", wav(np.zeros(0, dtype=np.int16))):
        with pytest.raises(CloudTtsError):
            pcm_from_body(body)


# ------------------------------------------------------------------ the privacy-gated hybrid
class CountingVoice:
    sample_rate = 24000

    def __init__(self, fail: str | None = None):
        self.calls: list[str] = []
        self.fail = fail
        self.warmed = False

    async def synthesize(self, text):
        self.calls.append(text)
        if self.fail:
            raise CloudTtsError(self.fail)
        return np.ones(10, dtype=np.int16)

    async def warm_up(self):
        self.warmed = True


async def test_private_turns_never_touch_the_cloud_voice():
    local, cloud = CountingVoice(), CountingVoice()
    h = HybridTTS(local, cloud)
    await h.synthesize("secret reply", private=True)
    assert cloud.calls == [] and local.calls == ["secret reply"]
    assert h.last_engine == "local" and h.last_reason == "private conversation"


async def test_forgetting_to_say_means_private():
    local, cloud = CountingVoice(), CountingVoice()
    await HybridTTS(local, cloud).synthesize("hello")  # no `private=` given
    assert cloud.calls == []


async def test_allowed_turns_use_the_cloud_voice():
    local, cloud = CountingVoice(), CountingVoice()
    h = HybridTTS(local, cloud)
    await h.synthesize("hello", private=False)
    assert cloud.calls == ["hello"] and local.calls == [] and h.last_engine == "cloud"


async def test_a_cloud_failure_falls_back_for_that_sentence_and_says_why():
    local, cloud = CountingVoice(), CountingVoice(fail="timeout")
    h = HybridTTS(local, cloud)
    pcm = await h.synthesize("hello", private=False)
    assert len(pcm) == 10 and local.calls == ["hello"]
    assert h.last_engine == "local" and "timeout" in (h.last_reason or "")


async def test_repeated_failures_pause_the_cloud_voice_then_it_is_retried():
    now = [100.0]
    local, cloud = CountingVoice(), CountingVoice(fail="unreachable")
    h = HybridTTS(local, cloud, max_failures=2, pause_s=60, clock=lambda: now[0])
    for _ in range(2):
        await h.synthesize("a", private=False)
    assert len(cloud.calls) == 2
    await h.synthesize("b", private=False)  # paused: no third attempt, no delay
    assert len(cloud.calls) == 2 and "paused" in (h.last_reason or "")
    now[0] += 61
    cloud.fail = None
    await h.synthesize("c", private=False)
    assert len(cloud.calls) == 3 and h.last_engine == "cloud"


async def test_a_success_resets_the_failure_count():
    local, cloud = CountingVoice(), CountingVoice(fail="timeout")
    h = HybridTTS(local, cloud, max_failures=2)
    await h.synthesize("a", private=False)
    cloud.fail = None
    await h.synthesize("b", private=False)
    cloud.fail = "timeout"
    await h.synthesize("c", private=False)
    await h.synthesize("d", private=False)  # only one failure since the reset: still not paused
    assert len(cloud.calls) == 4


async def test_warm_up_warms_only_the_local_voice():
    local, cloud = CountingVoice(), CountingVoice()
    await HybridTTS(local, cloud).warm_up()
    assert local.warmed and not cloud.warmed and cloud.calls == []


def test_mismatched_sample_rates_are_rejected():
    other = CountingVoice()
    other.sample_rate = 16000
    with pytest.raises(ValueError, match="sample rate"):
        HybridTTS(CountingVoice(), other)


# ------------------------------------------------------------------ through the real session
def hybrid_session(*, privacy="default", locked=False):
    local, cloud = CountingVoice(), CountingVoice()
    client = FakeClient(deltas=("Hello there. ", "Second sentence here."), privacy_locked=locked)
    s, ev = make_session(
        FakeStt("Ultron", "what time is it"),
        client,
        tts=HybridTTS(local, cloud),
        cfg=SessionConfig(privacy=privacy),
    )
    return s, ev, local, cloud


async def run_turn(s):
    await s.handle_utterance(AUDIO)  # wake word
    await s.handle_utterance(AUDIO)  # command


async def test_unlocked_default_conversation_may_use_the_cloud_voice():
    s, ev, local, cloud = hybrid_session()
    await run_turn(s)
    assert len(cloud.calls) == 2 and local.calls == []
    voice = [kw for n, kw in ev if n == "voice"]
    assert voice == [{"engine": "cloud", "reason": None}]  # reported once, on change


async def test_backend_locked_conversation_is_spoken_locally():
    s, ev, local, cloud = hybrid_session(locked=True)
    await run_turn(s)
    assert cloud.calls == [] and len(local.calls) == 2


async def test_local_only_session_is_spoken_locally_even_if_the_backend_says_unlocked():
    s, _, local, cloud = hybrid_session(privacy="local_only", locked=False)
    await run_turn(s)
    assert cloud.calls == [] and len(local.calls) == 2


async def test_a_backend_that_omits_the_flag_is_treated_as_private():
    s, _, local, cloud = hybrid_session(locked=None)
    await run_turn(s)
    assert cloud.calls == [] and len(local.calls) == 2


async def test_backend_error_line_is_spoken_locally():
    s, _ = make_session(
        FakeStt("Ultron", "hi"),
        FakeClient(error=RuntimeError("down")),
        tts=(h := HybridTTS(local := CountingVoice(), cloud := CountingVoice())),
    )
    await run_turn(s)
    assert cloud.calls == [] and local.calls and h.last_engine == "local"


async def test_plain_tts_adapters_are_untouched_by_the_new_wiring():
    tts = FakeTts()  # no routes_by_privacy, synthesize(text) only
    s, ev = make_session(FakeStt("Ultron", "hi"), FakeClient(), tts=tts)
    await run_turn(s)
    assert tts.said and "voice" not in names(ev)


# ------------------------------------------------------------------ configuration
def test_local_is_the_default_and_the_key_is_never_printed(monkeypatch):
    for k in ("TTS_PROVIDER", "FISH_API_KEY", "FISH_VOICE_ID"):
        monkeypatch.delenv(f"ULTRON_VOICE_{k}", raising=False)
    monkeypatch.setattr(voice_config, "_PKG_ROOT", voice_config._PKG_ROOT / "nonexistent")
    assert VoiceConfig().tts_provider == "local"
    monkeypatch.setenv("ULTRON_VOICE_FISH_API_KEY", KEY)
    assert KEY not in repr(VoiceConfig())


def test_dotenv_file_supplies_only_ultron_voice_keys(tmp_path, monkeypatch):
    (tmp_path / ".env").write_text(
        f'ULTRON_VOICE_FISH_API_KEY="{KEY}"\nULTRON_API_TOKEN=nope\nOTHER=x\n'
    )
    monkeypatch.setattr(voice_config, "_PKG_ROOT", tmp_path)
    monkeypatch.delenv("ULTRON_VOICE_FISH_API_KEY", raising=False)
    assert voice_config._dotenv_value("FISH_API_KEY") == KEY
    assert voice_config._dotenv_value("API_TOKEN") == ""  # not an ULTRON_VOICE_ key
    monkeypatch.setenv("ULTRON_VOICE_FISH_API_KEY", "from-env")
    assert voice_config._dotenv_value("FISH_API_KEY") == "from-env"  # environment wins


def test_choosing_fish_without_a_key_or_voice_is_an_error_not_a_silent_local_voice(monkeypatch):
    monkeypatch.setattr("ultron_voice.factory._make_local_tts", lambda cfg: CountingVoice())
    cfg = VoiceConfig(tts_provider="fish", fish_api_key="", fish_voice_id=VOICE_ID)
    with pytest.raises(ValueError, match="FISH_API_KEY"):
        make_tts(cfg)
    cfg = VoiceConfig(tts_provider="fish", fish_api_key=KEY, fish_voice_id="")
    with pytest.raises(ValueError, match="FISH_VOICE_ID"):
        make_tts(cfg)
    with pytest.raises(ValueError, match="'local' or 'fish'"):
        make_tts(VoiceConfig(tts_provider="elevenlabs"))
    ok = make_tts(VoiceConfig(tts_provider="fish", fish_api_key=KEY, fish_voice_id=VOICE_ID))
    assert isinstance(ok, HybridTTS)
