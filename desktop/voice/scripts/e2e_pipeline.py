"""End-to-end check of the voice pipeline against the REAL backend and REAL Ollama model.

REAL:  backend (uvicorn), Ollama qwen3:4b-instruct, Silero VAD, whisper.cpp, Kokoro TTS, WebSocket.
FAKE:  the microphone (synthesised speech replayed in real time) and the loudspeaker (NullPlayer).
So this proves the pipeline logic and measures latency; it says nothing about room acoustics,
real voices, microphone quality or echo.

    .venv/bin/python scripts/e2e_pipeline.py OUT.json
"""

from __future__ import annotations

import asyncio
import json
import os
import secrets
import subprocess
import sys
import tempfile
import time
from pathlib import Path

import httpx
import numpy as np

from ultron_voice import factory
from ultron_voice.audio import MIC_RATE, ArraySource, NullPlayer
from ultron_voice.client import UltronClient
from ultron_voice.config import VoiceConfig
from ultron_voice.session import SessionConfig
from ultron_voice.stt import WhisperStt
from ultron_voice.tts import KokoroTTS

BACKEND = Path(__file__).resolve().parents[3] / "backend"
PORT = 18100


def to16k(pcm24: np.ndarray, rate: int = 24000) -> np.ndarray:
    n = int(len(pcm24) * MIC_RATE / rate)
    return np.interp(np.linspace(0, len(pcm24) - 1, n), np.arange(len(pcm24)), pcm24).astype(
        np.int16
    )


def silence(s: float) -> np.ndarray:
    return np.zeros(int(MIC_RATE * s), dtype=np.int16)


async def start_backend(token: str, tmp: str) -> subprocess.Popen:
    env = {
        **os.environ,
        "ULTRON_API_TOKEN": token,
        "ULTRON_PORT": str(PORT),
        "ULTRON_DATABASE_URL": f"sqlite+aiosqlite:///{tmp}/e2e.db",
        # E2E_ENGINE=cloud: use the cloud model configured in backend/.env (the backend runs with
        # cwd=backend/ so it reads that file itself; the key never passes through this script).
        "ULTRON_ALLOW_CLOUD": "true" if os.environ.get("E2E_ENGINE") == "cloud" else "false",
        "ULTRON_ROUTING_POLICY": "cloud_first"
        if os.environ.get("E2E_ENGINE") == "cloud"
        else "auto",
        "ULTRON_LOG_LEVEL": "WARNING",
    }
    proc = subprocess.Popen(  # noqa: ASYNC220 - non-blocking spawn, no wait
        [
            str(BACKEND / ".venv/bin/uvicorn"),
            "app.main:app_factory",
            "--factory",
            "--host",
            "127.0.0.1",
            "--port",
            str(PORT),
        ],
        cwd=BACKEND,
        env=env,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    async with httpx.AsyncClient() as c:
        for _ in range(60):
            try:
                if (await c.get(f"http://127.0.0.1:{PORT}/health", timeout=1)).status_code == 200:
                    return proc
            except httpx.HTTPError:
                await asyncio.sleep(0.5)
    proc.kill()
    raise RuntimeError("backend did not start")


async def run_scenario(name, audio, cfg, stt, tts, token, session_cfg, seconds_after=0.0):
    events: list[tuple[str, dict]] = []
    t0 = time.monotonic()
    timeline: list[str] = []
    cfg.backend_url = f"ws://127.0.0.1:{PORT}/v1/ws/chat"
    player = NullPlayer()
    async with UltronClient(cfg.backend_url, token) as client:
        session = factory.make_session(
            cfg,
            stt=stt,
            client=client,
            player=player,
            tts=tts,
            session_cfg=session_cfg,
            on_event=lambda n, **k: (
                events.append((n, k)),
                timeline.append(f"{time.monotonic() - t0:6.2f}s {n}"),
            ),
        )
        mic = ArraySource(audio, realtime=True, tail_silence_s=seconds_after)
        await session.run(mic)
    print(f"--- timeline {name} (mic time, real time) ---\n" + "\n".join(timeline), flush=True)
    return events, player


async def main(out: Path) -> None:
    token = secrets.token_urlsafe(32)
    tmp = tempfile.mkdtemp()
    cfg = VoiceConfig()
    cfg.token = token
    backend = await start_backend(token, tmp)
    whisper = factory.make_whisper(cfg)
    await whisper.start()
    stt = WhisperStt(whisper.url, prompt=cfg.stt_prompt)
    ultron_tts = factory.make_tts(cfg)
    operator = KokoroTTS(
        cfg.model(cfg.tts_model),
        cfg.model("voices-v1.0.bin"),
        voice="am_adam",
        speed=1.0,
        lang="en-us",
        metal=0.0,
    )
    report: dict = {}
    await ultron_tts.warm_up()
    try:
        # ---- warm the LLM so the first scenario measures steady state, not model load
        async with httpx.AsyncClient(timeout=120) as c:
            await c.post(
                "http://127.0.0.1:11434/api/chat",
                json={
                    "model": "qwen3:4b-instruct",
                    "stream": False,
                    "keep_alive": "10m",
                    "messages": [{"role": "user", "content": "hi"}],
                    "options": {"num_predict": 2},
                },
            )

        # ---- A: wake word + command in one breath. Run twice: cold (first prompt) then warm.
        a = to16k(await operator.synthesize("Ultron, what is seventeen times twenty three?"), 24000)
        for tag in ("A1_cold", "A2_warm", "A3_warm"):
            ev, player = await run_scenario(
                tag,
                np.concatenate([silence(1.0), a]),
                cfg,
                stt,
                ultron_tts,
                token,
                SessionConfig(mode="command"),
                seconds_after=12.0,
            )
            done = next(k for n, k in ev if n == "turn_done")
            speech_end_to_audio = 600 + done.get("stt_ms", 0) + done.get("first_audio_ms", 0)
            report[tag] = {
                "heard": next(k["text"] for n, k in ev if n == "heard"),
                "engine": next(
                    (f"{k['provider']}/{k['model']}" for n, k in ev if n == "engine"), None
                ),
                "reply": done["reply"],
                "timing_ms": {k: round(v) for k, v in done.items() if k.endswith("_ms")},
                "approx_last_word_to_first_audio_ms": round(speech_end_to_audio),
                "audio_seconds_spoken": round(player.samples / (player.rate or 24000), 2),
                "wake": [k for n, k in ev if n == "wake"],
                "errors": [k for n, k in ev if n == "error"],
            }

        # ---- B: interruption. A long answer is requested, the operator cuts in after ~8 s.
        story = to16k(
            await operator.synthesize(
                "Ultron, tell me a long story about a robot who learns to speak."
            ),
            24000,
        )
        stop = to16k(await operator.synthesize("Stop. That is enough."), 24000)
        audio_b = np.concatenate([silence(1.0), story, silence(8.5), stop])
        ev, player = await run_scenario(
            "B",
            audio_b,
            cfg,
            stt,
            ultron_tts,
            token,
            SessionConfig(mode="command", follow_up_timeout_s=3.0),
            seconds_after=16.0,
        )
        names = [n for n, _ in ev]
        barge = next((k for n, k in ev if n == "barge_in"), None)
        dones = [k for n, k in ev if n == "turn_done"]
        report["B_barge_in"] = {
            "events": names,
            "barge_in_spoken": barge["spoken"] if barge else None,
            "first_turn_interrupted": dones[0]["interrupted"] if dones else None,
            "heard_after_interrupt": [k["text"] for n, k in ev if n == "heard"][1:],
            "player_stop_calls": player.stopped,
            "errors": [k for n, k in ev if n == "error"],
        }

        # ---- what the server actually stored
        async with httpx.AsyncClient() as c:
            h = {"Authorization": f"Bearer {token}"}
            convs = (await c.get(f"http://127.0.0.1:{PORT}/v1/conversations", headers=h)).json()
            stored = []
            for cv in convs:
                d = (
                    await c.get(f"http://127.0.0.1:{PORT}/v1/conversations/{cv['id']}", headers=h)
                ).json()
                stored.append(
                    [
                        {
                            "role": m["role"],
                            "chars": len(m["content"]),
                            "interrupted": (m.get("route") or {}).get("interrupted", False),
                            "text": m["content"][:160],
                        }
                        for m in d["messages"]
                    ]
                )
        report["stored_conversations"] = stored
    finally:
        await stt.close()
        await whisper.stop()
        backend.terminate()
        backend.wait(timeout=10)
    out.write_text(json.dumps(report, indent=2))  # noqa: ASYNC240
    print(json.dumps(report, indent=2))


if __name__ == "__main__":
    asyncio.run(main(Path(sys.argv[1])))
