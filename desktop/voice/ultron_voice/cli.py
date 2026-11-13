"""Command line entry: `python -m ultron_voice run|say|transcribe|devices|selftest-mic`."""

from __future__ import annotations

import argparse
import asyncio
import contextlib
import os
import signal
import sys
import time
from pathlib import Path

import numpy as np

from ultron_voice import factory
from ultron_voice.audio import FRAME_MS, MIC_RATE, PipeWireMic, PipeWirePlayer
from ultron_voice.client import UltronClient
from ultron_voice.config import VoiceConfig
from ultron_voice.session import SessionConfig, State
from ultron_voice.stt import WhisperStt
from ultron_voice.vad import SileroVad

LABEL = {
    State.STANDBY: "STANDBY   (say the wake name)",
    State.LISTENING: "LISTENING (microphone is live)",
    State.THINKING: "THINKING",
    State.SPEAKING: "SPEAKING  (talk to interrupt)",
}


def _state_file() -> Path:
    return Path(os.environ.get("XDG_RUNTIME_DIR", "/tmp")) / "ultron-voice.state"


def _level_file() -> Path:
    """How loud the speech playing right now is (0..1); the desktop orb follows it."""
    return _state_file().with_suffix(".level")


async def level_pump(player, path: Path, interval_s: float = 0.05) -> None:
    """Publish player.level() until cancelled. Writes only while there is something to show."""
    last = 0.0
    try:
        while True:
            level = player.level()
            if level != last and (level > 0 or last > 0):
                tmp = path.with_name(path.name + ".tmp")
                with contextlib.suppress(OSError):
                    tmp.write_text(f"{level:.3f}\n")
                    tmp.replace(path)  # atomic: the reader never sees half a number
            last = level
            await asyncio.sleep(interval_s)
    finally:
        with contextlib.suppress(OSError):
            path.unlink()  # noqa: ASYNC240 - one tiny file on the in-memory runtime dir


class Console:
    """Visible status. The state file lets a tray icon (Phase 3) mirror it."""

    def __init__(self, show_text: bool) -> None:
        self.show_text = show_text
        self.muted = False
        self.state = State.STANDBY

    def _write_state(self) -> None:
        label = "muted" if self.muted else self.state.value
        with contextlib.suppress(OSError):
            _state_file().write_text(label + "\n")

    def __call__(self, name: str, **kw) -> None:
        if name == "state":
            self.state = kw["state"]
            print(f"\n[{LABEL[self.state]}]", flush=True)
        elif name == "muted":
            self.muted = True
            print("\n[MUTED - microphone input is being discarded]", flush=True)
        elif name == "unmuted":
            self.muted = False
            print("\n[UNMUTED]", flush=True)
        elif name == "wake":
            print(f"  wake word detected (score {kw['score']})", flush=True)
        elif name == "heard" and self.show_text:
            print(f"  you: {kw['text']}", flush=True)
        elif name == "engine":
            note = f"  ! {kw['notice']}" if kw.get("notice") else ""
            print(f"  engine: {kw['provider']}/{kw['model']} ({kw['tier']}){note}", flush=True)
        elif name == "voice":
            if kw["engine"] == "cloud":
                print(
                    "  voice: Fish Audio (cloud) - this reply's text is sent to Fish Audio",
                    flush=True,
                )
            else:
                why = f" ({kw['reason']})" if kw.get("reason") else ""
                print(f"  voice: local{why}", flush=True)
        elif name == "new_conversation":
            # the desktop window reads this exact marker; keep the wording stable
            if kw.get("erased"):
                print("\n[NEW CONVERSATION] previous chat erased", flush=True)
            elif kw.get("had_chat") is False:
                print("\n[NEW CONVERSATION] nothing to erase yet", flush=True)
            else:
                print(
                    f"\n[NEW CONVERSATION] previous chat NOT erased ({kw.get('reason')}); "
                    "it is still stored",
                    flush=True,
                )
        elif name == "barge_in":
            print("  interrupted", flush=True)
        elif name == "turn_done":
            reply = " ".join(str(kw.get("reply") or "").split())
            if reply and self.show_text:
                print(f"  reply: {reply[:600]}", flush=True)  # the window shows it as a caption
            ms = {k: int(v) for k, v in kw.items() if k.endswith("_ms")}
            print(f"  timing: {ms}", flush=True)
        elif name == "error":
            print(f"  error: {kw}", flush=True)
        self._write_state()


def install_stop_handlers(loop: asyncio.AbstractEventLoop, task: asyncio.Task) -> None:
    """SIGTERM (what the desktop app sends on 'Stop voice') cancels the run so cleanup executes:
    whisper-server is stopped and the status file is removed."""
    loop.add_signal_handler(signal.SIGTERM, task.cancel)


async def cmd_run(args) -> int:
    cfg = VoiceConfig()
    if not cfg.token:
        print("ULTRON_API_TOKEN is not set.", file=sys.stderr)
        return 2
    console = Console(show_text=not args.hide_transcripts)
    whisper = factory.make_whisper(cfg)
    print("starting speech recognition...", flush=True)
    await whisper.start()
    stt = WhisperStt(whisper.url, prompt=cfg.stt_prompt)
    tts = factory.make_tts(cfg)
    await tts.warm_up()
    player = factory.make_player(cfg)
    pump = asyncio.create_task(level_pump(player, _level_file()))
    mic = factory.make_mic(cfg)
    scfg = SessionConfig(
        wake_name=cfg.wake_name,
        mode=args.mode,
        privacy=args.privacy,
        barge_in=not args.half_duplex,
        sentence_gap_s=cfg.tts_sentence_gap,
    )
    try:
        async with UltronClient(cfg.backend_url, cfg.token) as client:
            session = factory.make_session(
                cfg,
                stt=stt,
                client=client,
                player=player,
                tts=tts,
                session_cfg=scfg,
                on_event=console,
            )
            loop = asyncio.get_running_loop()
            loop.add_signal_handler(signal.SIGUSR1, lambda: session.set_muted(not session.muted))
            background: set[asyncio.Task] = set()  # keeps NEW tasks alive until they finish

            def _new_chat() -> None:
                task = asyncio.create_task(session.new_conversation())
                background.add(task)
                task.add_done_callback(background.discard)

            loop.add_signal_handler(signal.SIGUSR2, _new_chat)  # the NEW button
            install_stop_handlers(loop, asyncio.current_task())  # type: ignore[arg-type]
            if sys.stdin.isatty():
                loop.add_reader(sys.stdin.fileno(), lambda: _on_key(session, loop))
            print(
                f"mode={args.mode} privacy={args.privacy} barge_in={not args.half_duplex}. "
                "Keys: m = mute/unmute, q = quit. Audio is processed in memory and never saved.",
                flush=True,
            )
            await session.run(mic)
    finally:
        pump.cancel()
        await stt.close()
        await whisper.stop()
        await player.stop()
        with contextlib.suppress(OSError):
            _state_file().unlink()
    return 0


def _on_key(session, loop) -> None:
    key = sys.stdin.readline().strip().lower()
    if key == "m":
        session.set_muted(not session.muted)
    elif key == "q":
        for task in asyncio.all_tasks(loop):
            task.cancel()


async def cmd_say(args) -> int:
    cfg = VoiceConfig()
    tts = factory.make_tts(cfg)
    t0 = time.perf_counter()
    pcm = await tts.synthesize(args.text)
    print(f"synthesised {len(pcm) / tts.sample_rate:.2f}s in {time.perf_counter() - t0:.2f}s")
    if args.wav:
        import soundfile as sf  # noqa: PLC0415

        sf.write(args.wav, pcm, tts.sample_rate, subtype="PCM_16")
        print("wrote", args.wav)
    else:
        player = PipeWirePlayer(cfg.speaker_target)
        await player.play(pcm, tts.sample_rate)
        await player.finish()
    return 0


async def cmd_transcribe(args) -> int:
    import soundfile as sf  # noqa: PLC0415

    cfg = VoiceConfig()
    data, rate = sf.read(args.wav, dtype="int16")
    if data.ndim > 1:
        data = data[:, 0]
    if rate != MIC_RATE:
        data = np.interp(
            np.linspace(0, len(data) - 1, int(len(data) * MIC_RATE / rate)),
            np.arange(len(data)),
            data,
        ).astype(np.int16)
    whisper = factory.make_whisper(cfg)
    await whisper.start()
    try:
        stt = WhisperStt(whisper.url, prompt=cfg.stt_prompt)
        print(await stt.transcribe(data))
        await stt.close()
    finally:
        await whisper.stop()
    return 0


async def cmd_devices(_args) -> int:
    for tool in (["pw-cli", "ls", "Node"],):
        proc = await asyncio.create_subprocess_exec(*tool, stdout=asyncio.subprocess.PIPE)
        out, _ = await proc.communicate()
        for line in out.decode().splitlines():
            if "node.name" in line or "media.class" in line or "node.description" in line:
                print(line.strip())
    print("\nSet ULTRON_VOICE_MIC / ULTRON_VOICE_SPEAKER to a node name to choose devices.")
    return 0


async def cmd_selftest_mic(args) -> int:
    """Opens the microphone for a few seconds and reports levels. Nothing is stored or sent."""
    cfg = VoiceConfig()
    vad = SileroVad(cfg.model(cfg.vad_model))
    mic = PipeWireMic(cfg.mic_target)
    peak, speech, frames = 0, 0, 0
    deadline = time.monotonic() + args.seconds
    print(f"Listening for {args.seconds}s - speak now. (audio is analysed in memory, not saved)")
    async for f in mic.frames():
        frames += 1
        peak = max(peak, int(np.abs(f).max()))
        speech += vad.prob(f) > 0.5
        if time.monotonic() > deadline:
            break
    await mic.close()
    print(
        f"frames={frames} ({frames * FRAME_MS / 1000:.1f}s) peak={peak}/32768 "
        f"speech_frames={speech}"
    )
    if frames == 0:
        print("FAIL: no audio frames received (wrong device, or PipeWire not running)")
        return 1
    print(
        "OK: microphone delivers audio"
        + ("; speech detected" if speech else "; but no speech detected (quiet or muted mic?)")
    )
    return 0


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(prog="ultron_voice")
    sub = p.add_subparsers(dest="cmd", required=True)
    r = sub.add_parser("run", help="start the voice assistant")
    r.add_argument("--mode", choices=["command", "continuous"], default="command")
    r.add_argument("--privacy", choices=["default", "local_only"], default="default")
    r.add_argument(
        "--half-duplex",
        action="store_true",
        help="ignore the mic while speaking (use with loudspeakers; no barge-in)",
    )
    r.add_argument("--hide-transcripts", action="store_true")
    s = sub.add_parser("say")
    s.add_argument("text")
    s.add_argument("--wav")
    t = sub.add_parser("transcribe")
    t.add_argument("wav")
    sub.add_parser("devices")
    m = sub.add_parser("selftest-mic")
    m.add_argument("--seconds", type=float, default=5.0)
    args = p.parse_args(argv)
    fn = {
        "run": cmd_run,
        "say": cmd_say,
        "transcribe": cmd_transcribe,
        "devices": cmd_devices,
        "selftest-mic": cmd_selftest_mic,
    }[args.cmd]
    try:
        return asyncio.run(fn(args))
    except (KeyboardInterrupt, asyncio.CancelledError):
        return 0


if __name__ == "__main__":
    sys.exit(main())
