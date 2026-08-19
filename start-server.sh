#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "$0")" && pwd)"
LOG=/var/log/callredact-model.log
PYDEPS="$ROOT/.pyworker-deps"
mkdir -p /var/log
: > "$LOG"

find_python() {
  local c
  for c in \
    /venv/main/bin/python \
    /app/.venv/bin/python \
    /venv/bin/python \
    /usr/local/bin/python \
    /usr/bin/python3 \
    python3 \
    python
  do
    if [ -x "$c" ] || command -v "$c" >/dev/null 2>&1; then
      if "$c" - <<'PY' >/dev/null 2>&1
import torch, whisper
PY
      then
        if [ -x "$c" ]; then
          echo "$c"
        else
          command -v "$c"
        fi
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

echo "CALLREDACT_BOOT using model Python: $PYTHON"

# Keep PyWorker dependencies isolated from the vendor Whisper environment.
# In particular, do not let pip upgrade/downgrade packages used by the stock
# Whisper WebUI/API image (Gradio/Pillow/etc.).
rm -rf "$PYDEPS"
mkdir -p "$PYDEPS"
"$PYTHON" -m pip install \
  --disable-pip-version-check \
  --no-cache-dir \
  -q \
  --target "$PYDEPS" \
  -r "$ROOT/requirements.txt"

# Start the private model backend using the untouched vendor environment.
nohup "$PYTHON" -u -m uvicorn model_server:app \
  --app-dir "$ROOT" --host 127.0.0.1 --port 18000 \
  >>"$LOG" 2>&1 &
MODEL_PID=$!

cleanup() {
  kill "$MODEL_PID" >/dev/null 2>&1 || true
}
trap cleanup EXIT INT TERM

# PyWorker itself uses the isolated dependency directory. Vast injects the
# Serverless worker environment (CONTAINER_ID, REPORT_ADDR, worker port, etc.)
# when this script is launched as part of a managed Serverless worker startup.
export PYTHONPATH="$PYDEPS${PYTHONPATH:+:$PYTHONPATH}"
cd "$ROOT"
exec "$PYTHON" -u worker.py
