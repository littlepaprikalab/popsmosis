#!/bin/bash
# Start drive.py for each topic id given, detached, with every key the round must not use unset.
set -eu
cd "$(dirname "$0")/../.."
for topic in "$@"; do
  mkdir -p "jobs/$topic/_insession"
  env -u ANTHROPIC_API_KEY -u POPSMOSIS_ANTHROPIC_API_KEY -u ELEVENLABS_API_KEY -u ANTHROPIC_BASE_URL \
      -u STUDIO_BRAIN_MODEL -u STUDIO_BRAIN_EFFORT -u BRAIN_PROVIDER -u SONG_PROVIDER -u ART_PROVIDER -u POPSMOSIS_API_URL -u POPSMOSIS_API_KEY \
      -u INKMORY_PLANNER_API_KEY -u INKMORY_OPERATOR_API_KEY -u INKMORY_API_URL \
      PYTHONUNBUFFERED=1 nohup python tools/insession/drive.py "$topic" \
      >> "jobs/$topic/_insession/driver.log" 2>&1 < /dev/null &
  echo "$topic: pid $!"
done
