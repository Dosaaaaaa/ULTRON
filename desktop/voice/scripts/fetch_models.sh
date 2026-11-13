#!/usr/bin/env bash
# Downloads voice models into ~/.cache/ultron/models (outside the repo). Idempotent.
# Sources: Silero VAD (MIT), whisper.cpp ggml models (MIT), Kokoro-82M ONNX (Apache-2.0).
set -euo pipefail
DEST="${ULTRON_MODELS_DIR:-$HOME/.cache/ultron/models}"
mkdir -p "$DEST"
get() { # url file
  if [ -s "$DEST/$2" ]; then echo "have  $2"; return; fi
  echo "fetch $2"; curl -fL --retry 3 -o "$DEST/$2.part" "$1" && mv "$DEST/$2.part" "$DEST/$2"
}
get https://raw.githubusercontent.com/snakers4/silero-vad/master/src/silero_vad/data/silero_vad.onnx silero_vad.onnx
get https://huggingface.co/ggerganov/whisper.cpp/resolve/main/ggml-base.en.bin ggml-base.en.bin
K=https://github.com/thewh1teagle/kokoro-onnx/releases/download/model-files-v1.0
get $K/kokoro-v1.0.int8.onnx kokoro-v1.0.int8.onnx
get $K/voices-v1.0.bin voices-v1.0.bin
( cd "$DEST" && sha256sum silero_vad.onnx ggml-base.en.bin kokoro-v1.0.int8.onnx voices-v1.0.bin | tee MANIFEST.sha256 )
