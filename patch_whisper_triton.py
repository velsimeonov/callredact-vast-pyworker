#!/usr/bin/env python3
"""
CallRedact Whisper/Triton compatibility bootstrap.

Newer Triton releases reject direct writes to JITFunction.src. Some OpenAI
Whisper releases still generate median-filter kernels by assigning:

    kernel.src = kernel.src.replace(...)

This script rewrites every such assignment in whisper/triton_ops.py to use
Triton's supported unsafe source-update method and clears the kernel hash.

It discovers the Whisper installation used by the Python interpreter that
executes this script, so it does not depend on a hard-coded Python version or
site-packages path.

The transformation is AST-based rather than an exact text/indentation match.
It is idempotent and validates the resulting Python source before writing it.
"""

from __future__ import annotations

import ast
import py_compile
import shutil
import sys
from pathlib import Path


def whisper_triton_ops_path() -> Path:
    try:
        import whisper
    except Exception as exc:
        raise RuntimeError(f"unable to import whisper: {exc}") from exc

    whisper_init = Path(whisper.__file__).resolve()
    target = whisper_init.with_name("triton_ops.py")
    if not target.is_file():
        raise RuntimeError(f"Whisper triton_ops.py not found at {target}")
    return target


def line_offsets(source: str) -> list[int]:
    offsets = [0]
    for line in source.splitlines(keepends=True):
        offsets.append(offsets[-1] + len(line))
    return offsets


def absolute_offset(offsets: list[int], lineno: int, col: int) -> int:
    return offsets[lineno - 1] + col


def is_kernel_src_target(node: ast.AST) -> bool:
    return (
        isinstance(node, ast.Attribute)
        and node.attr == "src"
        and isinstance(node.value, ast.Name)
        and node.value.id == "kernel"
    )


def is_kernel_src_replace(node: ast.AST) -> bool:
    return (
        isinstance(node, ast.Call)
        and isinstance(node.func, ast.Attribute)
        and node.func.attr == "replace"
        and is_kernel_src_target(node.func.value)
    )


def find_direct_src_assignments(tree: ast.AST) -> list[ast.Assign]:
    matches: list[ast.Assign] = []
    for node in ast.walk(tree):
        if (
            isinstance(node, ast.Assign)
            and len(node.targets) == 1
            and is_kernel_src_target(node.targets[0])
            and is_kernel_src_replace(node.value)
        ):
            matches.append(node)
    return sorted(matches, key=lambda n: (n.lineno, n.col_offset))


def patch_source(source: str) -> tuple[str, int]:
    tree = ast.parse(source)
    matches = find_direct_src_assignments(tree)

    if not matches:
        return source, 0

    offsets = line_offsets(source)
    replacements: list[tuple[int, int, str]] = []

    for node in matches:
        rhs = ast.get_source_segment(source, node.value)
        if not rhs:
            raise RuntimeError(
                f"unable to obtain source for kernel.src assignment at line {node.lineno}"
            )

        indent = " " * node.col_offset
        replacement = (
            f"kernel._unsafe_update_src({rhs})\n"
            f"{indent}kernel.hash = None"
        )

        start = absolute_offset(offsets, node.lineno, node.col_offset)
        end = absolute_offset(offsets, node.end_lineno, node.end_col_offset)
        replacements.append((start, end, replacement))

    patched = source
    for start, end, replacement in reversed(replacements):
        patched = patched[:start] + replacement + patched[end:]

    # Syntax validation before touching the installed Whisper file.
    ast.parse(patched)

    remaining_tree = ast.parse(patched)
    remaining = find_direct_src_assignments(remaining_tree)
    if remaining:
        raise RuntimeError(
            f"{len(remaining)} direct kernel.src assignment(s) remain after patch"
        )

    return patched, len(matches)


def main() -> int:
    try:
        target = whisper_triton_ops_path()
        source = target.read_text(encoding="utf-8")

        patched, count = patch_source(source)

        if count == 0:
            if "_unsafe_update_src" in source:
                print(
                    f"CALLREDACT_BOOT Triton patch already active: {target}",
                    flush=True,
                )
                return 0

            # No incompatible assignment exists. This may be a newer Whisper
            # version that no longer needs the compatibility modification.
            print(
                f"CALLREDACT_BOOT Triton patch not required: {target}",
                flush=True,
            )
            return 0

        backup = target.with_suffix(target.suffix + ".callredact-original")
        if not backup.exists():
            shutil.copy2(target, backup)

        target.write_text(patched, encoding="utf-8")

        # Compile the actual installed file after writing it.
        py_compile.compile(str(target), doraise=True)

        verify = target.read_text(encoding="utf-8")
        verify_tree = ast.parse(verify)
        if find_direct_src_assignments(verify_tree):
            raise RuntimeError("verification failed: direct kernel.src assignment remains")

        print(
            f"CALLREDACT_BOOT Triton patched assignments={count}: {target}",
            flush=True,
        )
        return 0

    except Exception as exc:
        print(
            f"CALLREDACT_BOOT_ERROR Whisper/Triton compatibility patch failed: "
            f"{type(exc).__name__}: {exc}",
            file=sys.stderr,
            flush=True,
        )
        return 10


if __name__ == "__main__":
    raise SystemExit(main())
