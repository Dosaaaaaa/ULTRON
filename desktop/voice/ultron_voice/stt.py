"""Speech-to-text through a local whisper.cpp server (CPU; the GPU stays free for the LLM)."""

from __future__ import annotations

import asyncio
import contextlib
import io
import os
import re
import socket
import wave
from pathlib import Path

import httpx
import numpy as np

from ultron_voice.audio import MIC_RATE

_NOISE = re.compile(r"\[[^\]]*\]|\([^)]*\)|\*[^*]*\*")  # [BLANK_AUDIO], (music), *cough*


def clean_transcript(text: str) -> str:
    return re.sub(r"\s+", " ", _NOISE.sub(" ", text)).strip()


def to_wav_bytes(pcm: np.ndarray, rate: int = MIC_RATE) -> bytes:
    buf = io.BytesIO()
    with wave.open(buf, "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(rate)
        w.writeframes(pcm.astype(np.int16).tobytes())
    return buf.getvalue()


def _free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


class WhisperServer:
    """Owns a `whisper-server` child bound to 127.0.0.1 only."""

    def __init__(
        self,
        binary: str | Path,
        model: str | Path,
        *,
        threads: int = 4,
        prompt: str = "",
        port: int | None = None,
    ) -> None:
        self.binary, self.model, self.threads, self.prompt = (
            str(binary),
            str(model),
            threads,
            prompt,
        )
        self.port = port or _free_port()
        bindir = Path(self.binary).resolve().parent  # whisper.cpp/build/bin
        self._ld_path = os.pathsep.join(
            [str(bindir), str(bindir.parent / "src"), os.environ.get("LD_LIBRARY_PATH", "")]
        )
        self._proc: asyncio.subprocess.Process | None = None

    @property
    def url(self) -> str:
        return f"http://127.0.0.1:{self.port}"

    async def start(self, timeout_s: float = 60) -> None:
        cmd = [
            self.binary,
            "-m",
            self.model,
            "-t",
            str(self.threads),
            "--host",
            "127.0.0.1",
            "--port",
            str(self.port),
            "--no-gpu",
            "-nt",
        ]
        if self.prompt:
            cmd += ["--prompt", self.prompt]
        self._proc = await asyncio.create_subprocess_exec(
            *cmd,
            stdout=asyncio.subprocess.DEVNULL,
            stderr=asyncio.subprocess.DEVNULL,
            env={**os.environ, "LD_LIBRARY_PATH": self._ld_path},
        )
        deadline = asyncio.get_running_loop().time() + timeout_s
        async with httpx.AsyncClient() as c:
            while asyncio.get_running_loop().time() < deadline:
                if self._proc.returncode is not None:
                    raise RuntimeError(f"whisper-server exited with {self._proc.returncode}")
                try:
                    await c.get(self.url, timeout=1)
                    return
                except httpx.HTTPError:
                    await asyncio.sleep(0.25)
        await self.stop()
        raise TimeoutError("whisper-server did not become ready")

    async def stop(self) -> None:
        proc, self._proc = self._proc, None
        if proc and proc.returncode is None:
            with contextlib.suppress(ProcessLookupError):
                proc.terminate()
            with contextlib.suppress(Exception):
                await asyncio.wait_for(proc.wait(), 5)
            if proc.returncode is None:
                with contextlib.suppress(ProcessLookupError):
                    proc.kill()


class WhisperStt:
    def __init__(self, url: str, *, prompt: str = "") -> None:
        self._url = url.rstrip("/") + "/inference"
        self._prompt = prompt
        self._client = httpx.AsyncClient(timeout=60)

    async def transcribe(self, pcm: np.ndarray, rate: int = MIC_RATE) -> str:
        data = {"temperature": "0.0", "response_format": "json"}
        if self._prompt:
            data["prompt"] = self._prompt
        resp = await self._client.post(
            self._url, data=data, files={"file": ("utt.wav", to_wav_bytes(pcm, rate), "audio/wav")}
        )
        resp.raise_for_status()
        return clean_transcript(resp.json().get("text", ""))

    async def close(self) -> None:
        await self._client.aclose()
