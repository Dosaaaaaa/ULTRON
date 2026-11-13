"""Voice configuration from ULTRON_VOICE_* environment variables. No secrets are stored here."""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path

_PKG_ROOT = Path(__file__).resolve().parents[1]  # desktop/voice


def _env(name: str, default: str) -> str:
    return os.environ.get(f"ULTRON_VOICE_{name}", default)


def _dotenv_value(name: str) -> str:
    """ULTRON_VOICE_<name> from the process environment, else from desktop/voice/.env.

    The file is git-ignored. Only ULTRON_VOICE_* keys are ever read from it.
    """
    full = f"ULTRON_VOICE_{name}"
    if v := os.environ.get(full):
        return v
    try:
        for line in (_PKG_ROOT / ".env").read_text().splitlines():
            key, sep, value = line.partition("=")
            if sep and key.strip() == full:
                return value.strip().strip("\"'")
    except OSError:
        pass
    return ""


WAKE_NAMES = ("friday", "jarvis", "ultron", "computer", "nova", "orion", "athena")
DEFAULT_WAKE = "friday"


def wake_name_file() -> Path:
    """The chosen wake name: one word, no secrets. The desktop window picker writes it."""
    base = os.environ.get("XDG_CONFIG_HOME") or str(Path.home() / ".config")
    return Path(base) / "ultron" / "wake_name"


def _wake_name() -> str:
    """ULTRON_VOICE_WAKE_NAME, else the picker's file, else Friday. Junk falls back."""
    for raw in (os.environ.get("ULTRON_VOICE_WAKE_NAME", ""), _read(wake_name_file())):
        name = raw.strip().lower()
        if name.isalpha() and 3 <= len(name) <= 12:
            return name
    return DEFAULT_WAKE


def _read(path: Path) -> str:
    try:
        return path.read_text()
    except OSError:
        return ""


@dataclass
class VoiceConfig:
    backend_url: str = field(
        default_factory=lambda: _env("BACKEND_URL", "ws://127.0.0.1:8000/v1/ws/chat")
    )
    token: str | None = field(default_factory=lambda: os.environ.get("ULTRON_API_TOKEN") or None)
    models_dir: Path = field(
        default_factory=lambda: Path(
            _env("MODELS_DIR", str(Path.home() / ".cache" / "ultron" / "models"))
        )
    )
    whisper_bin: Path = field(
        default_factory=lambda: Path(
            _env(
                "WHISPER_BIN",
                str(_PKG_ROOT / "vendor/whisper.cpp/build/bin/whisper-server"),
            )
        )
    )
    stt_model: str = field(default_factory=lambda: _env("STT_MODEL", "ggml-base.en.bin"))
    stt_threads: int = field(default_factory=lambda: int(_env("STT_THREADS", "8")))
    tts_threads: int = field(default_factory=lambda: int(_env("TTS_THREADS", "6")))
    # Biases the decoder toward the wake word's spelling; changes nothing else.
    wake_name: str = field(default_factory=_wake_name)
    stt_prompt: str = field(
        default_factory=lambda: (
            os.environ.get("ULTRON_VOICE_STT_PROMPT") or f"{_wake_name().capitalize()}."
        )
    )
    tts_model: str = field(default_factory=lambda: _env("TTS_MODEL", "kokoro-v1.0.int8.onnx"))
    tts_voice: str = field(default_factory=lambda: _env("TTS_VOICE", "bm_lewis"))
    tts_speed: float = field(default_factory=lambda: float(_env("TTS_SPEED", "1.0")))
    tts_lang: str = field(default_factory=lambda: _env("TTS_LANG", "en-gb"))
    tts_metal: float = field(default_factory=lambda: float(_env("TTS_METAL", "0")))
    # 1.0 = the voice as Kokoro makes it; 0.88 is about two semitones lower (deeper, heavier).
    tts_pitch: float = field(default_factory=lambda: float(_env("TTS_PITCH", "1.0")))
    # Character sound design (all 0..1, 0 = off): weight, roughness, room. See tts.character_fx.
    tts_body: float = field(default_factory=lambda: float(_env("TTS_BODY", "0")))
    tts_gravel: float = field(default_factory=lambda: float(_env("TTS_GRAVEL", "0")))
    tts_reverb: float = field(default_factory=lambda: float(_env("TTS_REVERB", "0")))
    # Robotic layer (0..1): metallic resonance and a 90 Hz ring. See tts.robot_fx.
    tts_resonance: float = field(default_factory=lambda: float(_env("TTS_RESONANCE", "0")))
    tts_ring: float = field(default_factory=lambda: float(_env("TTS_RING", "0")))
    # Gaps and pace. tts_pause = seconds of silence after a comma (other marks scale from it);
    # tts_sentence_gap = silence between spoken sentences; tts_delivery 0..1 = pace variation.
    # Set all three to 0 for continuous speech.
    tts_pause: float = field(default_factory=lambda: float(_env("TTS_PAUSE", "0.12")))
    tts_sentence_gap: float = field(default_factory=lambda: float(_env("TTS_SENTENCE_GAP", "0.1")))
    tts_delivery: float = field(default_factory=lambda: float(_env("TTS_DELIVERY", "0")))
    # Optional cloud voice. Off unless tts_provider == "fish". The key lives only in the
    # environment or desktop/voice/.env; repr=False keeps it out of any printed config.
    tts_provider: str = field(default_factory=lambda: _env("TTS_PROVIDER", "local").lower())
    fish_api_key: str = field(default_factory=lambda: _dotenv_value("FISH_API_KEY"), repr=False)
    fish_voice_id: str = field(default_factory=lambda: _dotenv_value("FISH_VOICE_ID"))
    fish_model: str = field(default_factory=lambda: _env("FISH_MODEL", "s2.1-pro-free"))
    vad_model: str = "silero_vad.onnx"
    mic_target: str | None = field(
        default_factory=lambda: os.environ.get("ULTRON_VOICE_MIC") or None
    )
    speaker_target: str | None = field(
        default_factory=lambda: os.environ.get("ULTRON_VOICE_SPEAKER") or None
    )

    def model(self, name: str) -> Path:
        return self.models_dir / name
