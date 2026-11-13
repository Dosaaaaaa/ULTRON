"""Optional cloud voice: Fish Audio text-to-speech with a privacy-gated local fallback.

This is OFF unless you configure it (`ULTRON_VOICE_TTS_PROVIDER=fish`). When on, the text of each
spoken reply is sent to Fish Audio. The rules, enforced in `HybridTTS` and tested:

* A private turn (session `local_only`, or a conversation the backend has locked to local) is always
  spoken by the local voice. The cloud adapter is not even called.
* What the operator says is never sent: only ULTRON's reply sentences.
* Any cloud failure falls back to the local voice for that sentence, and after repeated failures the
  cloud voice is paused for a while so speech does not stall. The fallback is reported, not silent.
"""

from __future__ import annotations

import time
from collections.abc import Callable

import httpx
import numpy as np

from ultron_voice.tts import clean_for_speech

API_URL = "https://api.fish.audio/v1/tts"
RATE = 24000  # same as the local voice, so playback code never has to care which one spoke


class CloudTtsError(Exception):
    """`kind` is a short, log-safe label. It never contains the API key or response bodies."""

    def __init__(self, kind: str) -> None:
        super().__init__(kind)
        self.kind = kind


def pcm_from_body(body: bytes, expected_rate: int = RATE) -> np.ndarray:
    """Mono int16 PCM from a WAV (streaming headers may declare a bogus length) or raw PCM body."""
    rate, channels, data = expected_rate, 1, body
    if body[:4] == b"RIFF":
        pos, data = 12, b""
        while pos + 8 <= len(body):
            cid, size = body[pos : pos + 4], int.from_bytes(body[pos + 4 : pos + 8], "little")
            chunk = body[pos + 8 : pos + 8 + size]
            if cid == b"fmt " and len(chunk) >= 16:
                channels = int.from_bytes(chunk[2:4], "little")
                rate = int.from_bytes(chunk[4:8], "little")
                if int.from_bytes(chunk[14:16], "little") != 16:
                    raise CloudTtsError("unsupported_audio")
            if cid == b"data":
                data = body[pos + 8 :] if size == 0 or pos + 8 + size > len(body) else chunk
                break
            pos += 8 + size + (size & 1)
    data = data[: len(data) // 2 * 2]
    pcm = np.frombuffer(data, dtype="<i2")
    if channels > 1:
        pcm = pcm[: len(pcm) // channels * channels].reshape(-1, channels)[:, 0]
    if len(pcm) == 0:
        raise CloudTtsError("empty_audio")
    if rate != expected_rate:
        positions = np.arange(int(len(pcm) * expected_rate / rate)) * rate / expected_rate
        pcm = np.interp(positions, np.arange(len(pcm)), pcm.astype(np.float32)).astype(np.int16)
    return pcm.astype(np.int16)


class FishTTS:
    sample_rate = RATE

    def __init__(
        self,
        *,
        client: httpx.AsyncClient,
        api_key: str,
        reference_id: str,
        model: str = "s2.1-pro-free",
        timeout_s: float = 8.0,
        url: str = API_URL,
    ) -> None:
        self._client = client
        self._key = api_key
        self._ref = reference_id
        self._model = model
        self._timeout = timeout_s
        self._url = url

    async def synthesize(self, text: str) -> np.ndarray:
        text = clean_for_speech(text)
        if not text:
            return np.zeros(0, dtype=np.int16)
        try:
            resp = await self._client.post(
                self._url,
                json={
                    "text": text,
                    "reference_id": self._ref,
                    "format": "wav",
                    "sample_rate": RATE,
                    "latency": "balanced",
                },
                headers={"Authorization": f"Bearer {self._key}", "model": self._model},
                timeout=self._timeout,
            )
        except httpx.TimeoutException as exc:
            raise CloudTtsError("timeout") from exc
        except httpx.TransportError as exc:
            raise CloudTtsError("unreachable") from exc
        if resp.status_code in (401, 403):
            raise CloudTtsError("rejected_credentials")
        if resp.status_code == 402:
            raise CloudTtsError("no_credit")
        if resp.status_code == 429:
            raise CloudTtsError("rate_limited")
        if resp.status_code != 200:
            raise CloudTtsError(f"http_{resp.status_code}")
        return pcm_from_body(resp.content)

    async def close(self) -> None:
        await self._client.aclose()


class HybridTTS:
    """Cloud voice where allowed, local voice everywhere else. See the module docstring."""

    routes_by_privacy = True  # the session passes `private=` only to adapters that declare this

    def __init__(
        self,
        local,
        cloud,
        *,
        max_failures: int = 2,
        pause_s: float = 60.0,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        if local.sample_rate != cloud.sample_rate:
            raise ValueError("local and cloud voices must share a sample rate")
        self.sample_rate = local.sample_rate
        self._local, self._cloud = local, cloud
        self._max_failures, self._pause_s, self._clock = max_failures, pause_s, clock
        self._failures = 0
        self._paused_until = 0.0
        self.last_engine = "local"
        self.last_reason: str | None = None

    async def warm_up(self) -> None:
        await self._local.warm_up()  # warming the cloud voice would mean sending text to it

    async def synthesize(self, text: str, private: bool = True) -> np.ndarray:
        """`private` defaults to True: a caller that forgets to say gets the safe, local answer."""
        if private:
            return await self._use_local(text, "private conversation")
        if self._clock() < self._paused_until:
            return await self._use_local(text, "cloud voice paused after errors")
        try:
            pcm = await self._cloud.synthesize(text)
        except CloudTtsError as exc:
            self._failures += 1
            if self._failures >= self._max_failures:
                self._paused_until = self._clock() + self._pause_s
                self._failures = 0
            return await self._use_local(text, f"cloud voice failed ({exc.kind})")
        self._failures = 0
        self.last_engine, self.last_reason = "cloud", None
        return pcm

    async def _use_local(self, text: str, reason: str) -> np.ndarray:
        self.last_engine, self.last_reason = "local", reason
        return await self._local.synthesize(text)
