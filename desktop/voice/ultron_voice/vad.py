"""Silero VAD (ONNX, no PyTorch) and an utterance segmenter with hysteresis and pre-roll."""

from __future__ import annotations

from collections import deque
from dataclasses import dataclass, field
from pathlib import Path

import numpy as np
import onnxruntime as ort

from ultron_voice.audio import FRAME_MS, FRAME_SAMPLES, MIC_RATE

CONTEXT = 64  # Silero v5 expects the previous 64 samples prepended to each 512-sample frame


class SileroVad:
    """Speech probability per 32 ms frame. Stateful: call reset() between audio streams."""

    def __init__(self, model_path: str | Path) -> None:
        opts = ort.SessionOptions()
        opts.intra_op_num_threads = 1
        opts.inter_op_num_threads = 1
        self._sess = ort.InferenceSession(
            str(model_path), sess_options=opts, providers=["CPUExecutionProvider"]
        )
        names = {i.name for i in self._sess.get_inputs()}
        if not {"input", "state", "sr"} <= names:
            raise RuntimeError(f"unsupported Silero model inputs {sorted(names)}; need v5+")
        self.reset()

    def reset(self) -> None:
        self._state = np.zeros((2, 1, 128), dtype=np.float32)
        self._context = np.zeros((1, CONTEXT), dtype=np.float32)

    def prob(self, frame_int16: np.ndarray) -> float:
        if len(frame_int16) != FRAME_SAMPLES:
            raise ValueError(f"need {FRAME_SAMPLES} samples, got {len(frame_int16)}")
        x = (frame_int16.astype(np.float32) / 32768.0)[None, :]
        x = np.concatenate([self._context, x], axis=1)
        out, self._state = self._sess.run(
            None, {"input": x, "state": self._state, "sr": np.array(MIC_RATE, dtype=np.int64)}
        )
        self._context = x[:, -CONTEXT:]
        return float(out[0][0])


@dataclass
class VadConfig:
    threshold: float = 0.5  # speech starts above this
    neg_threshold: float = 0.35  # speech ends below this (hysteresis)
    min_speech_ms: int = 160  # shorter bursts are discarded as clicks
    end_silence_ms: int = 600  # this much silence ends an utterance
    preroll_ms: int = 320  # audio kept from before the detected start
    max_utterance_s: float = 15.0


@dataclass
class Segmenter:
    """Turns per-frame probabilities into utterances.

    push(frame, prob) -> (started, finished_audio | None). `started` is True on the frame where
    speech is confirmed; `finished_audio` is the int16 utterance once enough silence follows.
    """

    cfg: VadConfig = field(default_factory=VadConfig)

    def __post_init__(self) -> None:
        n_pre = max(1, self.cfg.preroll_ms // FRAME_MS)
        self._pre: deque[np.ndarray] = deque(maxlen=n_pre)
        self._buf: list[np.ndarray] = []
        self._speaking = False
        self._candidate = 0  # consecutive speech frames before confirmation
        self._silence = 0

    @property
    def speaking(self) -> bool:
        return self._speaking

    def reset(self) -> None:
        self._pre.clear()
        self._buf = []
        self._speaking = False
        self._candidate = self._silence = 0

    def push(self, frame: np.ndarray, prob: float) -> tuple[bool, np.ndarray | None]:
        cfg = self.cfg
        if not self._speaking:
            self._pre.append(frame)
            if prob >= cfg.threshold:
                self._candidate += 1
                if self._candidate * FRAME_MS >= cfg.min_speech_ms:
                    self._speaking = True
                    self._buf = list(self._pre)
                    self._silence = 0
                    return True, None
            else:
                self._candidate = 0
            return False, None

        self._buf.append(frame)
        self._silence = self._silence + 1 if prob < cfg.neg_threshold else 0
        too_long = len(self._buf) * FRAME_MS >= cfg.max_utterance_s * 1000
        if self._silence * FRAME_MS >= cfg.end_silence_ms or too_long:
            audio = np.concatenate(self._buf)
            self.reset()
            return False, audio
        return False, None
