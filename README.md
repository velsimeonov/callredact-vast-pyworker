# CallRedact Vast PyWorker v1.1.6 Whisper Triton Full Hotfix

Full replacement package (real files, no patch files).

Included changes:
- Added patch_whisper_triton.py
- Integrated automatic Triton compatibility fix into start-server.sh
- Preserved WORKER_PORT=3000 serverless behavior
- Preserved existing model_server.py and worker.py flow

Fixes:
AttributeError: Cannot set attribute 'src' directly

Deployment:
Replace repository contents with this package and rebuild the Vast template.
