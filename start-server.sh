#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "$0")" && pwd)"
LOG=/var/log/callredact-model.log

# Vast PyWorker's internal HTTP listener defaults to port 3000 in our setup.
# Keep an explicit fallback here even when the template does not define
# WORKER_PORT. Vast maps this internal port to VAST_TCP_PORT_3000 externally.
export WORKER_PORT="${WORKER_PORT:-3000}"
PYDEPS="$ROOT/.pyworker-deps"
WHISPER_OVERLAY="$ROOT/.whisper-overlay"
WHISPER_FIXED_VERSION="20250625"

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
echo "CALLREDACT_BOOT worker port: $WORKER_PORT"

# ---------------------------------------------------------------------------
# Whisper/Triton compatibility
#
# Some Vast Whisper images contain an older OpenAI Whisper triton_ops.py that
# directly assigns JITFunction.src. Newer Triton versions reject that operation:
#
#   AttributeError: Cannot set attribute 'src' directly.
#
# OpenAI Whisper 20250625 contains the compatible _unsafe_update_src() logic.
# Do NOT modify /venv/main. If the vendor copy is old, install only Whisper
# itself into a small overlay and let it reuse the vendor Torch/Triton stack.
# ---------------------------------------------------------------------------

MODEL_PYTHONPATH=""

if "$PYTHON" - <<'PY' >/dev/null 2>&1
import inspect
import whisper.triton_ops as triton_ops

src = inspect.getsource(triton_ops.median_kernel)
if "_unsafe_update_src" not in src:
    raise SystemExit(1)
PY
then
  echo "CALLREDACT_BOOT vendor Whisper Triton compatibility: OK"
else
  echo "CALLREDACT_BOOT vendor Whisper Triton compatibility: OLD — installing OpenAI Whisper ${WHISPER_FIXED_VERSION} overlay"

  rm -rf "$WHISPER_OVERLAY"
  mkdir -p "$WHISPER_OVERLAY"

  "$PYTHON" -m pip install \
    --disable-pip-version-check \
    --no-cache-dir \
    --no-deps \
    --target "$WHISPER_OVERLAY" \
    "openai-whisper==${WHISPER_FIXED_VERSION}"

  # Verify that the overlay really contains the Triton compatibility fix before
  # allowing this worker to register with Vast Serverless.
  PYTHONPATH="$WHISPER_OVERLAY${PYTHONPATH:+:$PYTHONPATH}" \
    "$PYTHON" - <<'PY'
import inspect
import whisper
import whisper.triton_ops as triton_ops

src = inspect.getsource(triton_ops.median_kernel)
if "_unsafe_update_src" not in src:
    raise RuntimeError("Whisper overlay does not contain the Triton compatibility fix")

print(f"CALLREDACT_BOOT Whisper overlay ready: {whisper.__file__}")
PY

  MODEL_PYTHONPATH="$WHISPER_OVERLAY"
fi

# ---------------------------------------------------------------------------
# PyWorker dependencies
# Keep them isolated from the vendor Whisper environment so pip cannot replace
# Torch/Whisper/Gradio/Pillow packages used by the model container.
# ---------------------------------------------------------------------------

rm -rf "$PYDEPS"
mkdir -p "$PYDEPS"

"$PYTHON" -m pip install \
  --disable-pip-version-check \
  --no-cache-dir \
  -q \
  --target "$PYDEPS" \
  -r "$ROOT/requirements.txt"

# Start the private CallRedact model backend.
# The optional Whisper overlay is visible ONLY to this process.
if [ -n "$MODEL_PYTHONPATH" ]; then
  nohup env \
    PYTHONPATH="$MODEL_PYTHONPATH${PYTHONPATH:+:$PYTHONPATH}" \
    "$PYTHON" -u -m uvicorn model_server:app \
      --app-dir "$ROOT" --host 127.0.0.1 --port 18000 \
      >>"$LOG" 2>&1 &
else
  nohup "$PYTHON" -u -m uvicorn model_server:app \
    --app-dir "$ROOT" --host 127.0.0.1 --port 18000 \
    >>"$LOG" 2>&1 &
fi

MODEL_PID=$!

cleanup() {
  kill "$MODEL_PID" >/dev/null 2>&1 || true
}
trap cleanup EXIT INT TERM

# PyWorker uses only its isolated dependency directory. Vast supplies the
# managed Serverless networking/runtime environment.
export PYTHONPATH="$PYDEPS${PYTHONPATH:+:$PYTHONPATH}"

cd "$ROOT"
exec "$PYTHON" -u worker.py
