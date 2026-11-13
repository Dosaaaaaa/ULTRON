#!/usr/bin/env bash
# Builds whisper.cpp (CPU only; leaves the GPU free for Ollama) into ./vendor. No system install.
set -euo pipefail
cd "$(dirname "$0")/.."
mkdir -p vendor
[ -d vendor/whisper.cpp ] || git clone --depth 1 https://github.com/ggml-org/whisper.cpp vendor/whisper.cpp
cd vendor/whisper.cpp
git log -1 --format='whisper.cpp commit %H (%cd)' --date=short
cmake -B build -DCMAKE_BUILD_TYPE=Release -DWHISPER_BUILD_EXAMPLES=ON -DGGML_NATIVE=ON >/dev/null
cmake --build build -j"$(nproc)" --target whisper-server whisper-cli 2>&1 | tail -3
ls -la build/bin/whisper-server build/bin/whisper-cli
