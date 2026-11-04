#!/bin/bash
# Every tool this repo uses, installed when a cloud session starts: a developer's session,
# or the teacher routine's run (docs/agent-runbook.md), which with POPSMOSIS_TEACHER_ONLY=1
# gets only the studio's part (parts 1 to 3). Nothing then stops mid-task to
# install a package. The container is cached once this finishes, so the heavy parts
# (PyTorch, the timing models, node_modules) are paid once per environment, not per session.
# Each part is idempotent, and a part that cannot be done says so and lets the rest run.
set -euo pipefail

# Only for Claude Code cloud sessions.
if [ "${CLAUDE_CODE_REMOTE:-}" != "true" ]; then
  exit 0
fi

cd "$CLAUDE_PROJECT_DIR"
log() { echo "session-start: $*" >&2; }
export PIP_DISABLE_PIP_VERSION_CHECK=1 PIP_ROOT_USER_ACTION=ignore

# A part is skipped when the files it installs from are unchanged since it last succeeded,
# so a warm session starts in seconds; a branch that changes them installs again.
STAMPS="$HOME/.cache/popsmosis-session-start"
mkdir -p "$STAMPS"
digest() { cat "$@" 2>/dev/null | sha256sum | cut -d' ' -f1; }
changed() { local name=$1; shift; [ "$(cat "$STAMPS/$name" 2>/dev/null)" != "$(digest "$@")" ]; }
mark() { local name=$1; shift; digest "$@" > "$STAMPS/$name"; }

# 1. System tools: ffmpeg encodes the videos and the karaoke fallback; skia-python (the
#    art and the renderer) loads libEGL.so.1.
missing=()
command -v ffmpeg >/dev/null 2>&1 || missing+=(ffmpeg)
# (grep reads all of ldconfig's output: with -q, pipefail could see ldconfig's SIGPIPE.)
ldconfig -p 2>/dev/null | grep 'libEGL\.so\.1' >/dev/null || missing+=(libegl1)
if [ ${#missing[@]} -gt 0 ]; then
  apt_install() { DEBIAN_FRONTEND=noninteractive apt-get install -y -q --no-install-recommends "${missing[@]}" >/dev/null 2>&1; }
  apt_install || { apt-get update -q >/dev/null 2>&1 && apt_install; } \
    || log "could not install ${missing[*]}; the render and art steps (and their tests) need them"
fi

# 2. Python: the studio, with the media steps' PyTorch (CPU), torchaudio, Demucs and skia.
if changed pip studio/requirements.txt; then
  if python -m pip install -q -r studio/requirements.txt; then mark pip studio/requirements.txt
  else log "pip could not install studio/requirements.txt; the studio will not run"
  fi
fi

# 3. The models the song's timing loads (Demucs, the MMS forced aligner; about 1.5 GB), so a
#    render never stops to download them. They land in the torch cache the container keeps.
#    A download cut short leaves a truncated file under its final name, which torch then reuses and
#    fails to read ("failed finding central directory"). So each checkpoint is checked as a zip
#    after the fetch, and a bad one is deleted and fetched once more before the stamp is written.
fetch_models() {
  python - >/dev/null 2>&1 <<'PY'
import importlib.util, pathlib, sys, zipfile
if importlib.util.find_spec("demucs") and importlib.util.find_spec("torchaudio"):
    failed = False
    try:
        from demucs.pretrained import get_model
        get_model("htdemucs")
        from torchaudio.pipelines import MMS_FA
        MMS_FA.get_model(with_star=True)
    except Exception:
        failed = True
    bad = [f for f in (pathlib.Path.home() / ".cache/torch/hub/checkpoints").glob("*.pt")
           if not zipfile.is_zipfile(f)]
    for f in bad:
        f.unlink()
    sys.exit(1 if bad or failed else 0)
PY
}
if [ ! -f "$STAMPS/models" ]; then
  if fetch_models || fetch_models; then touch "$STAMPS/models"
  else log "could not fetch the timing models; the first song timing will download them"
  fi
fi

# Whisper (faster-whisper's "small", about 0.5 GB), which the song's timing consults when MMS is unsure of a
# line, fetched into the Hugging Face cache the container keeps, with its own stamp so a container stamped for
# the models above fetches it too.
fetch_whisper() {
  python - >/dev/null 2>&1 <<'PY'
import importlib.util, sys
if not importlib.util.find_spec("faster_whisper"):
    sys.exit(1)
from faster_whisper import WhisperModel
from studio.steps.timing import WHISPER_MODEL
WhisperModel(WHISPER_MODEL, device="cpu", compute_type="int8")
PY
}
if [ ! -f "$STAMPS/whisper" ]; then
  if fetch_whisper || fetch_whisper; then touch "$STAMPS/whisper"
  else log "could not fetch Whisper; the first song timing that needs it will download it"
  fi
fi

# An environment that runs only the teacher routine sets POPSMOSIS_TEACHER_ONLY=1. The teacher
# runs the studio against the live API and needs nothing below, so its runs skip the node
# packages and the test database's start and migrations (docs/agent-runbook.md, Option A).
if [ "${POPSMOSIS_TEACHER_ONLY:-}" = 1 ]; then
  exit 0
fi

# 4. Node: the API, the web app and their tests. `install` (not `ci`) keeps what the cached
#    container already has. The Prisma client is generated again whenever the schema changes.
NODE_FILES=(package.json package-lock.json api/package.json web/package.json)
if [ ! -d node_modules ] || changed npm "${NODE_FILES[@]}"; then
  if npm install --no-audit --no-fund --loglevel=error >/dev/null; then mark npm "${NODE_FILES[@]}"
  else log "npm install failed"
  fi
fi
if [ -d node_modules ] && changed prisma prisma/schema.prisma; then
  if npm --workspace api run -s db:generate >/dev/null 2>&1; then mark prisma prisma/schema.prisma
  else log "could not generate the Prisma client; run npm --workspace api run db:generate"
  fi
fi

# 5. Postgres for the API's integration tests (INTEGRATION=1), with the database CI uses.
DB_URL=postgresql://postgres:postgres@localhost:5432/popsmosis
if command -v pg_ctlcluster >/dev/null 2>&1; then
  if service postgresql start >/dev/null 2>&1; then
    su postgres -c "psql -qtAc \"ALTER USER postgres PASSWORD 'postgres'\"" >/dev/null 2>&1 || true
    su postgres -c "psql -qtAc \"SELECT 1 FROM pg_database WHERE datname = 'popsmosis'\"" 2>/dev/null | grep -q 1 \
      || su postgres -c "createdb popsmosis" >/dev/null 2>&1 || log "could not create the popsmosis database"
    # Both set to the local database on purpose: Prisma migrates DIRECT_URL first, and reads a
    # .env too, so an inherited value could point this at a real database.
    DIRECT_URL=$DB_URL DATABASE_URL=$DB_URL npm --workspace api run -s db:deploy >/dev/null 2>&1 \
      || log "could not apply the migrations; run npm --workspace api run db:deploy"
    if [ -n "${CLAUDE_ENV_FILE:-}" ] && [ -z "${DATABASE_URL:-}" ]; then
      echo "export DATABASE_URL=$DB_URL" >> "$CLAUDE_ENV_FILE"
    fi
  else
    log "could not start Postgres; the API's integration tests need it"
  fi
fi
