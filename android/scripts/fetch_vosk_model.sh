#!/usr/bin/env bash
# Downloads the small English Vosk models (Apache-2.0) into app/src/main/assets:
#   model-en-us  (US English, ~40 MB zip)    and    model-en-in  (Indian English, ~38 MB zip).
# Each zip is checked against a pinned SHA-256 before it is unpacked; the models are git-ignored.
# Usage: fetch_vosk_model.sh [us|in|all]   (default: all)
set -euo pipefail
WHICH="${1:-all}"
ASSETS="$(cd "$(dirname "$0")/.." && pwd)/app/src/main/assets"

fetch() { # name zip-url sha256 dir-in-zip dest-dir-name
  local name="$1" url="$2" sha="$3" dir="$4" dest="$ASSETS/$5"
  [ -f "$dest/uuid" ] && { echo "model already present: $dest"; return 0; }
  local work
  work="$(mktemp -d "${TMPDIR:-/tmp}/vosk-model.XXXXXX")"
  trap 'rm -rf "$work"' RETURN
  curl -fsSL -o "$work/model.zip" "$url"
  echo "$sha  $work/model.zip" | sha256sum -c -
  python3 -I -c 'import sys, zipfile; zipfile.ZipFile(sys.argv[1]).extractall(sys.argv[2])' "$work/model.zip" "$work/x"
  mkdir -p "$dest"
  cp -r "$work/x/$dir/." "$dest/"
  # StorageService.unpack re-copies the model only when this id changes.
  echo "$name" > "$dest/uuid"
  echo "model installed: $dest"
}

if [ "$WHICH" = us ] || [ "$WHICH" = all ]; then
  fetch vosk-model-small-en-us-0.15 https://alphacephei.com/vosk/models/vosk-model-small-en-us-0.15.zip \
    30f26242c4eb449f948e42cb302dd7a686cb29a3423a8367f99ff41780942498 vosk-model-small-en-us-0.15 model-en-us
fi
if [ "$WHICH" = in ] || [ "$WHICH" = all ]; then
  fetch vosk-model-small-en-in-0.4 https://alphacephei.com/vosk/models/vosk-model-small-en-in-0.4.zip \
    20663dcac4d5cb783a579c54d98339344a688e4ec6e1b4a4b059fd1235454cc7 vosk-model-small-en-in-0.4 model-en-in
fi
