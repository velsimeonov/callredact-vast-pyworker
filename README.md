## v1.1.11

- Added explicit active model observability after model selection.
- Improved debugging of requested vs loaded Whisper model.

# CallRedact Vast PyWorker v1.1.10 — Whisper/Triton Compatibility

Complete replacement package. No `.patch` files are required.

## Included files

- `worker.py`
- `model_server.py`
- `start-server.sh`
- `patch_whisper_triton.py`
- `requirements.txt`
- `template-onstart.sh`
- `template.json.example`
- `README.md`

## What changed from v1.1.5

The live Vast worker proved that OpenAI Whisper's `triton_ops.py` was incompatible
with Triton 3.3.1 when `word_timestamps=True` reached the CUDA median-filter
alignment path.

Known-good live runtime:

- Python 3.12.13
- torch 2.7.1+cu128
- Triton 3.3.1
- CUDA 12.8
- Whisper from `/venv/main/lib/python3.12/site-packages/whisper`

The working manual repair converted all three direct writes of
`kernel.src = kernel.src.replace(...)` to `kernel._unsafe_update_src(...)` and
cleared `kernel.hash`.

v1.1.7 performs that transformation automatically at boot using Python AST
parsing. It does not depend on an exact text block or hard-coded site-packages
path. The patch is idempotent, validates syntax before writing, compiles the
installed `triton_ops.py` after writing, and fails startup if the compatibility
step fails.

`WORKER_PORT=3000` and the private Uvicorn model service on
`127.0.0.1:18000` are preserved.

## Expected bootstrap log

Typical lines:

    CALLREDACT_BOOT using model Python: /venv/main/bin/python
    CALLREDACT_BOOT worker port: 3000
    CALLREDACT_BOOT Vast mapped port: VAST_TCP_PORT_3000=...
    CALLREDACT_BOOT Triton patched assignments=3: /venv/main/lib/python3.12/site-packages/whisper/triton_ops.py
    CALLREDACT_BOOT runtime python=3.12.13 torch=2.7.1+cu128 triton=3.3.1 cuda=12.8 whisper=/venv/main/lib/python3.12/site-packages/whisper/__init__.py

## Deployment

Replace the repository contents with the files in this package, commit/push,
then let the Vast template clone the repository normally.


## v1.1.10

- Added explicit request/model diagnostics before Whisper model selection.
- Health endpoint now reports the active loaded model instead of startup default.
- Improved debugging for Serverless model switching and request metadata.
