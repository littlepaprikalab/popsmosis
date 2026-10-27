#!/bin/bash
set -euo pipefail

# Only for Claude Code cloud sessions.
if [ "${CLAUDE_CODE_REMOTE:-}" != "true" ]; then
  exit 0
fi

cd "$CLAUDE_PROJECT_DIR"

# The studio (python -m studio) and its tests need these; pip is idempotent.
python -m pip install -q -r studio/requirements.txt
