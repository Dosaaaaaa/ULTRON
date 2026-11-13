"""Assembles the real pipeline from a VoiceConfig."""

from __future__ import annotations

from ultron_voice.audio import AudioSource, LevelPlayer, PipeWireMic, PipeWirePlayer, Player
from ultron_voice.client import UltronClient
from ultron_voice.config import VoiceConfig
from ultron_voice.session import SessionConfig, VoiceSession
from ultron_voice.stt import WhisperServer, WhisperStt
from ultron_voice.tts import KokoroTTS
from ultron_voice.vad import Segmenter, SileroVad, VadConfig


def make_tts(cfg: VoiceConfig):
    local = _make_local_tts(cfg)
    if cfg.tts_provider == "local":
        return local
    if cfg.tts_provider != "fish":
        raise ValueError(
            f"ULTRON_VOICE_TTS_PROVIDER must be 'local' or 'fish', not {cfg.tts_provider!r}"
        )
    # Configured on purpose, so a missing piece is an error, never a silent fall back to local.
    if not cfg.fish_api_key:
        raise ValueError("ULTRON_VOICE_TTS_PROVIDER=fish but ULTRON_VOICE_FISH_API_KEY is not set")
    if not cfg.fish_voice_id:
        raise ValueError("ULTRON_VOICE_TTS_PROVIDER=fish but ULTRON_VOICE_FISH_VOICE_ID is not set")
    import httpx  # noqa: PLC0415

    from ultron_voice.fish import FishTTS, HybridTTS  # noqa: PLC0415

    cloud = FishTTS(
        client=httpx.AsyncClient(),
        api_key=cfg.fish_api_key,
        reference_id=cfg.fish_voice_id,
        model=cfg.fish_model,
    )
    return HybridTTS(local, cloud)


def _make_local_tts(cfg: VoiceConfig) -> KokoroTTS:
    return KokoroTTS(
        cfg.model(cfg.tts_model),
        cfg.model("voices-v1.0.bin"),
        voice=cfg.tts_voice,
        speed=cfg.tts_speed,
        lang=cfg.tts_lang,
        metal=cfg.tts_metal,
        pitch=cfg.tts_pitch,
        gravel=cfg.tts_gravel,
        body=cfg.tts_body,
        reverb=cfg.tts_reverb,
        resonance=cfg.tts_resonance,
        ring=cfg.tts_ring,
        pause=cfg.tts_pause,
        delivery=cfg.tts_delivery,
        threads=cfg.tts_threads,
    )


def make_whisper(cfg: VoiceConfig) -> WhisperServer:
    return WhisperServer(
        cfg.whisper_bin, cfg.model(cfg.stt_model), threads=cfg.stt_threads, prompt=cfg.stt_prompt
    )


def make_mic(cfg: VoiceConfig) -> AudioSource:
    return PipeWireMic(cfg.mic_target)


def make_player(cfg: VoiceConfig) -> LevelPlayer:
    return LevelPlayer(PipeWirePlayer(cfg.speaker_target))


def make_session(
    cfg: VoiceConfig,
    *,
    stt: WhisperStt,
    client: UltronClient,
    player: Player,
    tts: KokoroTTS,
    session_cfg: SessionConfig,
    on_event,
    vad_cfg: VadConfig | None = None,
) -> VoiceSession:
    return VoiceSession(
        vad=SileroVad(cfg.model(cfg.vad_model)),
        segmenter=Segmenter(vad_cfg or VadConfig()),
        stt=stt,
        tts=tts,
        player=player,
        client=client,
        config=session_cfg,
        on_event=on_event,
    )
