#!/bin/bash
set -euo pipefail

# Only for Claude Code cloud sessions.
if [ "${CLAUDE_CODE_REMOTE:-}" != "true" ]; then
  exit 0
fi

cd "$CLAUDE_PROJECT_DIR"

# The renderer (studio/render) encodes with ffmpeg; its tests skip without it.
if ! command -v ffmpeg >/dev/null; then
  { apt-get install -y -q --no-install-recommends ffmpeg \
    || { apt-get update -q && apt-get install -y -q --no-install-recommends ffmpeg; }; } >/dev/null 2>&1 \
    || echo "session-start: could not install ffmpeg; the render tests will skip" >&2
fi

# The studio (python -m studio) and its tests need these; pip is idempotent.
python -m pip install -q -r studio/requirements.txt

# skia-python (the art and the renderer) loads libEGL.so.1, which a bare container may
# not have. Best effort: without apt the hook still succeeds, and `import skia` says why.
if ! ldconfig -p | grep -q libEGL.so.1; then
  { apt-get install -y -q libegl1 || { apt-get update -q && apt-get install -y -q libegl1; }; } >/dev/null 2>&1 \
    || echo "session-start: could not install libegl1; skia will not import" >&2
fi
