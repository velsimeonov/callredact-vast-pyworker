# CallRedact Vast Serverless PyWorker v1.1.5

This repository is the GPU-side companion for **Call Recording Redaction / FreePBX 17**.
It turns a normal Vast.ai Serverless workergroup into a privacy-preserving Whisper scanner.

## What it does

The worker accepts JSON on `/scan` containing a base64-encoded recording and its duration.
On the GPU it:

1. decodes the recording into temporary worker storage;
2. runs Whisper `small` on CUDA with word timestamps;
3. detects possible PAN/CVV speech;
4. returns only finding type, confidence, timestamps, context type and score;
5. deletes the temporary recording in `finally`.

It does **not** return the Whisper transcript or detected card digits.

## Why a public Git repository is required

Vast custom PyWorkers are linked to a Serverless template through the `PYWORKER_REPO`
environment variable. Vast clones that repository on each recruited worker and starts
`worker.py` beside the model server. Publish the files in this directory at the **root**
of a public Git repository before creating the template.

Required root files:

```text
worker.py
model_server.py
requirements.txt
start-server.sh
```

`template-onstart.sh` is provided as the exact on-start script to paste into the Vast
template editor; it does not need to be executed from the repository itself.

## Vast template

Create a **private template in My Templates** with these settings:

```text
Name:        CallRedact Whisper Serverless
Image:       vastai/whisper:1.0.8-cuda-12.9-py312
Launch mode: SSH
Disk:        24 GB or more
```

Environment variables:

```text
PYWORKER_REPO=https://github.com/YOUR-ORG/callredact-vast-pyworker.git
CALLREDACT_WHISPER_MODEL=small
# Optional stability tuning for long recordings:
CALLREDACT_CHUNK_SECONDS=120
CALLREDACT_CHUNK_OVERLAP=10
```

Do not put the Vast API key in the worker template. The FreePBX client authenticates to
Vast Serverless; workers do not need that key in this template.

Paste the contents of `template-onstart.sh` into the template **On-start script** field.

### Why SSH launch mode

Vast replaces the Docker image ENTRYPOINT in SSH/Jupyter launch modes and then runs the
on-start script. That lets the template use the official `vastai/whisper` image for its
CUDA/PyTorch/Whisper/ffmpeg environment while starting CallRedact's model server and
PyWorker instead of the image's normal WebUI service.

## Serverless endpoint/workergroup

Create a normal Vast Serverless endpoint, for example:

```text
Endpoint name:       callredact-whisper
Minimum workers:     0
Maximum workers:     4
Minimum load:        0
Target utilization:  0.9
Cold multiplier:     0
Minimum cold load:   0
Inactivity timeout:  300 seconds
```

Add a workergroup and choose the **CallRedact Whisper Serverless** template from
**My Templates**. Do not choose the regular `Whisper WebUI & API` template; it is a
normal instance template and does not contain the CallRedact PyWorker.

Recommended test offer filters:

```text
GPU count:       1
GPU RAM:         >= 12 GB
Reliability:     >= 99%
Verified:        yes
Max $/hour:      0.25
```

A minimum price is not technically required. If you want to eliminate unusually cheap
marketplace offers while diagnosing hosts, a temporary $0.05/hour floor is reasonable.

For production payment-card recordings, apply the organization's approved Vast
Secure Cloud/datacenter/compliance filters before sending real recordings.

## Readiness lifecycle

`start-server.sh` launches the private FastAPI model backend on `127.0.0.1:18000` and
then starts Vast PyWorker. The model server writes:

```text
CALLREDACT_MODEL_INFO ...
CALLREDACT_MODEL_READY ...
CALLREDACT_MODEL_ERROR ...
```

PyWorker watches `/var/log/callredact-model.log`. On `CALLREDACT_MODEL_READY`, it runs a
five-second silent-audio benchmark against `/scan`. The worker becomes Ready only after
that benchmark succeeds.

Useful logs on a worker:

```bash
cat /var/log/callredact-bootstrap.log
cat /var/log/callredact-model.log
ps auxww | grep -E 'worker.py|uvicorn' | grep -v grep
nvidia-smi
```

## Request schema

```json
{
  "audio_b64": "...",
  "filename": "example.wav",
  "duration": 285.0
}
```

Response schema:

```json
{
  "findings": [
    {
      "type": "possible_pan",
      "confidence": "high",
      "start_ms": 331220,
      "end_ms": 347840,
      "context_type": "luhn+card_context",
      "score": 93
    }
  ],
  "processing_ms": 12345,
  "device": "NVIDIA ...",
  "model": "small",
  "transcripts_persisted": false
}
```


## Shared endpoint / multiple PBXs

The same Serverless endpoint can serve many FreePBX systems. CallRedact v17.4.1 sends a `source` object (`pbx_id`, `scan_id`, `item_id`, `uniqueid`, `run_mode`) plus a `request_id`. The worker logs only safe source identifiers, echoes `request_id`, and still never returns or persists the Whisper transcript or detected card digits. Use a unique PBX identifier on each server and preferably a different restricted Vast API key on each PBX.

## Worker instance ownership reporting (v1.1.2)

The model response now includes the exact Vast worker instance ID from the runtime
`CONTAINER_ID` environment variable:

```json
{
  "request_id": "...",
  "instance_id": 48228230,
  "source": {"pbx_id": "...", "scan_id": 47, "item_id": 123}
}
```

CallRedact uses this value only for the exact correlated request and registers it in
`callredact_vast_instances`. It never enumerates all workers attached to a shared
endpoint. If `CONTAINER_ID` is absent, `instance_id` is returned as `null` and the PBX
safely skips lifecycle destruction.

After publishing this repository update, existing scale-to-zero workers must be
terminated/recruited again so Vast clones the new repository revision.

## Runner port restore (v1.1.3)

Restores the Vast PyWorker listener port expected by the Serverless template:

```bash
export WORKER_PORT="${WORKER_PORT:-3000}"
```

The architecture intentionally uses two ports:

- `3000/TCP` — Vast PyWorker/Serverless listener exposed by the template.
- `127.0.0.1:18000` — private Uvicorn model backend used only inside the container.

Do not move Uvicorn to port 3000; `worker.py` owns the Serverless listener and forwards `/scan` to the private model backend on port 18000.

## Runner environment hardening (v1.1.4)

The Vast Serverless SDK requires `WORKER_PORT` and uses it to resolve the
provider-injected `VAST_TCP_PORT_<port>` value. v1.1.4 sets the required
`WORKER_PORT=3000` in three places for defense-in-depth:

- `template-onstart.sh` exports it before launching the bootstrap;
- `start-server.sh` exports it and validates `VAST_TCP_PORT_3000`;
- `worker.py` sets a Python fallback before constructing the Vast worker.

The port architecture remains unchanged:

- `3000/TCP` — Vast PyWorker/Serverless listener; the Vast template must expose it.
- `127.0.0.1:18000` — private Uvicorn model backend; never expose or move this to 3000.

A healthy worker should show both processes and ports:

```bash
ps aux | grep -E 'worker.py|uvicorn' | grep -v grep
ss -lntp | grep -E '3000|18000'
```

Expected listeners are `0.0.0.0:3000` for PyWorker and `127.0.0.1:18000` for the
model backend. If the Vast template does not inject `VAST_TCP_PORT_3000`, startup
now fails immediately with a `CALLREDACT_BOOT_ERROR` explaining the template fix.

## v1.1.5 stability hotfix

This build keeps the Vast listener on port 3000 and the private model backend on
127.0.0.1:18000. It adds bounded Whisper inference for real FPBX recordings:

- default inference chunks reduced from 300 seconds to 120 seconds;
- default overlap reduced from 20 seconds to 10 seconds;
- decoded upload bytes are released after the temporary file is written;
- Python garbage collection and `torch.cuda.empty_cache()` run after each chunk;
- full traceback logging is emitted for `/scan` failures;
- chunk start/completion messages are logged for crash diagnostics.

The worker-port compatibility fix from v1.1.4 is retained unchanged.
