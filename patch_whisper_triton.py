#!/usr/bin/env python3
"""CallRedact Triton compatibility fix applied during startup."""
from pathlib import Path
import sys

TARGETS = [
    Path("/venv/main/lib/python3.12/site-packages/whisper/triton_ops.py"),
    Path("/venv/main/lib/python3.11/site-packages/whisper/triton_ops.py"),
]

OLD = """    kernel.src = kernel.src.replace(
        "constexpr",
        f"constexpr FILTER_WIDTH: tl.constexpr = {filter_width}",
    )
"""

NEW = """    kernel._unsafe_update_src(
        kernel.src.replace(
            "constexpr",
            f"constexpr FILTER_WIDTH: tl.constexpr = {filter_width}",
        )
    )
    kernel.hash = None
"""

for target in TARGETS:
    if target.exists():
        text = target.read_text()
        if "_unsafe_update_src" in text:
            print("CALLREDACT_BOOT Triton patch already active")
            sys.exit(0)
        if OLD in text:
            target.write_text(text.replace(OLD, NEW))
            print(f"CALLREDACT_BOOT Triton patched: {target}")
            sys.exit(0)

print("CALLREDACT_BOOT Triton patch target not found")
sys.exit(0)
