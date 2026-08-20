CallRedact Vast PyWorker v1.1.6 Triton Hotfix

Problem:
Whisper word_timestamps=True fails with newer Triton versions:

AttributeError:
Cannot set attribute 'src' directly.

Fix:
Patches whisper/triton_ops.py to use Triton's newer _unsafe_update_src API.

Apply:
python tools/patch_whisper_triton.py

Recommended:
Run after dependency installation in start-server.sh/bootstrap.

This package is an overlay hotfix and should be applied on top of the existing
working v1.1.5 pyworker.
