#!/bin/bash
set -euo pipefail

# Only for Claude Code cloud sessions.
if [ "${CLAUDE_CODE_REMOTE:-}" != "true" ]; then
  exit 0
fi

cd "$CLAUDE_PROJECT_DIR"

# The studio (python -m studio) and its tests need these; pip is idempotent.
python -m pip install -q -r studio/requirements.txt

# The renderer (studio/render) needs ffmpeg, and skia-python needs libEGL.
if ! command -v ffmpeg >/dev/null || ! ldconfig -p | grep -q libEGL.so.1; then
  (apt-get install -y -q --no-install-recommends ffmpeg libegl1 >/dev/null 2>&1 \
    || (apt-get update -q >/dev/null 2>&1 && apt-get install -y -q --no-install-recommends ffmpeg libegl1 >/dev/null 2>&1)) \
    || echo "could not install ffmpeg and libegl1; the render tests will skip" >&2
fi
