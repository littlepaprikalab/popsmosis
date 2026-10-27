#!/bin/bash
set -euo pipefail

# Only for Claude Code cloud sessions.
if [ "${CLAUDE_CODE_REMOTE:-}" != "true" ]; then
  exit 0
fi

cd "$CLAUDE_PROJECT_DIR"

# skia-python loads libEGL.so.1, which a bare container may not have.
if ! ldconfig -p | grep -q libEGL.so.1; then
  apt-get install -y -q libegl1 >/dev/null 2>&1 || (apt-get update -q >/dev/null && apt-get install -y -q libegl1 >/dev/null)
fi

# The studio (python -m studio) and its tests need these; pip is idempotent.
python -m pip install -q -r studio/requirements.txt
