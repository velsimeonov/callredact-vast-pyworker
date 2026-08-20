#!/usr/bin/env python3
"""
CallRedact Whisper Triton compatibility hotfix.

Fixes OpenAI Whisper triton_ops.py incompatibility with newer Triton versions:
AttributeError:
Cannot set attribute 'src' directly.
"""

from pathlib import Path
import sys

paths = [
    Path("/venv/main/lib/python3.12/site-packages/whisper/triton_ops.py"),
    Path("/usr/local/lib/python3.12/site-packages/whisper/triton_ops.py"),
]

target = None
for p in paths:
    if p.exists():
        target = p
        break

if not target:
    print("Whisper triton_ops.py not found")
    sys.exit(1)

text = target.read_text()

old = """    kernel.src = kernel.src.replace(
        "constexpr",
        f"constexpr FILTER_WIDTH: tl.constexpr = {filter_width}",
    )
"""

new = """    kernel._unsafe_update_src(
        kernel.src.replace(
            "constexpr",
            f"constexpr FILTER_WIDTH: tl.constexpr = {filter_width}",
        )
    )
    kernel.hash = None
"""

if old in text:
    target.write_text(text.replace(old, new))
    print(f"Patched {target}")
elif "_unsafe_update_src" in text:
    print(f"Already patched {target}")
else:
    print("Expected Whisper Triton code block not found")
    sys.exit(2)
