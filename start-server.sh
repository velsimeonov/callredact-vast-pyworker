#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "$0")" && pwd)"
LOG=/var/log/callredact-model.log
mkdir -p /var/log
: > "$LOG"

find_python() {
  local c
  for c in /app/.venv/bin/python /venv/bin/python python3 python; do
    if command -v "$c" >/dev/null 2>&1; then
      if "$c" - <<'PY' >/dev/null 2>&1
import torch, whisper
PY
      then
        command -v "$c"
        return 0
      fi
    elif [ -x "$c" ]; then
      if "$c" - <<'PY' >/dev/null 2>&1
import torch, whisper
PY
      then
        echo "$c"
        return 0
      fi
    fi
  done
  return 1
}

PYTHON="$(find_python || true)"
if [ -z "$PYTHON" ]; then
  echo "CALLREDACT_BOOT_ERROR Unable to find Python with torch + whisper in the template image" >&2
  exit 2
fi

echo "CALLREDACT_BOOT using Python: $PYTHON"
"$PYTHON" -m pip install --disable-pip-version-check --no-cache-dir -q -r "$ROOT/requirements.txt"

# Start the private model backend first. It binds only localhost and never returns
# transcripts/card digits to the Serverless client.
nohup "$PYTHON" -u -m uvicorn model_server:app \
  --app-dir "$ROOT" --host 127.0.0.1 --port 18000 \
  >>"$LOG" 2>&1 &
MODEL_PID=$!

cleanup() {
  kill "$MODEL_PID" >/dev/null 2>&1 || true
}
trap cleanup EXIT INT TERM

# Start PyWorker immediately; it tails the model log and transitions the Vast
# worker to ready after CALLREDACT_MODEL_READY and the benchmark complete.
cd "$ROOT"
exec "$PYTHON" -u worker.py
