#!/usr/bin/env python3
"""AST lint: flag bare-except and silent except-Exception handlers.

Violations
----------
1. Bare ``except:``  (handler.type is None)
2. ``except Exception:``  without a ``logger.exception``, ``logger.error``,
   ``logger.critical`` call or a ``raise`` statement anywhere in the body.

Comparison against ``bare_except_baseline.txt`` (path:lineno lines):
  - Offenders IN the baseline are GRANDFATHERED (exit 0).
  - Offenders NOT in baseline are NEW VIOLATIONS (exit 1 in --check mode).

Usage
-----
    python3 scripts/lint_bare_except.py            # same as --check
    python3 scripts/lint_bare_except.py --check
    python3 scripts/lint_bare_except.py --update-baseline
    python3 scripts/lint_bare_except.py --show-all
    python3 scripts/lint_bare_except.py --show-all --update-baseline  # no-op combo
"""
from __future__ import annotations

import argparse
import ast
import sys
from pathlib import Path
from typing import Optional

PROJECT_ROOT = Path(__file__).resolve().parent.parent
BASELINE_FILE = PROJECT_ROOT / "bare_except_baseline.txt"

# Directory segments to skip during the walk.
# Each entry is a sequence of path parts that, if appearing as a
# consecutive slice anywhere in a file's relative path, causes the file to
# be skipped.
_SKIP_SEGS: list[tuple[str, ...]] = [
    ("tests", "archive"),
    ("scripts", "migrations", "archive"),
    ("scripts", "tools", "archive"),
    (".venv",),
    ("__pycache__",),
    (".git",),
    ("node_modules",),
    (".mypy_cache",),
    (".pytest_cache",),
]


def _should_skip(rel: Path) -> bool:
    """Return True if *rel* is inside one of the excluded directories."""
    parts = rel.parts
    n = len(parts)
    for seg in _SKIP_SEGS:
        seg_len = len(seg)
        for i in range(n - seg_len + 1):
            if parts[i : i + seg_len] == seg:
                return True
    return False


def _body_has_logger_or_raise(stmts: list) -> bool:
    """Return True if *stmts* (recursively) contains a raise or a call to
    logger.exception / .error / .critical."""
    _LOG_METHODS = frozenset({"exception", "error", "critical"})
    for stmt in stmts:
        for node in ast.walk(stmt):
            if isinstance(node, ast.Raise):
                return True
            if isinstance(node, ast.Call):
                func = node.func
                if isinstance(func, ast.Attribute) and func.attr in _LOG_METHODS:
                    return True
    return False


def _collect_violations(project_root: Path) -> list[tuple[str, int, str]]:
    """Return list of *(rel_path_str, lineno, reason)* for all offenders."""
    violations: list[tuple[str, int, str]] = []

    for py_file in sorted(project_root.rglob("*.py")):
        try:
            rel = py_file.relative_to(project_root)
        except ValueError:
            continue
        if _should_skip(rel):
            continue

        try:
            source = py_file.read_text(encoding="utf-8", errors="replace")
            tree = ast.parse(source, filename=str(py_file))
        except SyntaxError:
            continue
        except Exception:  # noqa: BLE001  (lint-script can't use our own lint)
            continue

        for node in ast.walk(tree):
            if not isinstance(node, ast.ExceptHandler):
                continue
            rel_str = str(rel)
            lineno = node.lineno

            if node.type is None:
                # Bare `except:`
                violations.append((rel_str, lineno, "bare except (no logger call)"))

            elif (
                isinstance(node.type, ast.Name)
                and node.type.id == "Exception"
                and not _body_has_logger_or_raise(node.body)
            ):
                violations.append((
                    rel_str,
                    lineno,
                    "except Exception without logger.exception or raise",
                ))

    return violations


def _load_baseline(baseline_file: Optional[Path] = None) -> set[str]:
    """Return set of ``path:lineno`` strings from the baseline file."""
    bf = baseline_file if baseline_file is not None else BASELINE_FILE
    if not bf.exists():
        return set()
    return {ln.strip() for ln in bf.read_text().splitlines() if ln.strip()}


def _save_baseline(
    violations: list[tuple[str, int, str]],
    baseline_file: Optional[Path] = None,
) -> None:
    bf = baseline_file if baseline_file is not None else BASELINE_FILE
    entries = sorted(f"{p}:{ln}" for p, ln, _ in violations)
    bf.write_text("\n".join(entries) + ("\n" if entries else ""))


def _run(
    *,
    check: bool,
    update_baseline: bool,
    show_all: bool,
    project_root: Optional[Path] = None,
    baseline_file: Optional[Path] = None,
) -> int:
    root = project_root if project_root is not None else PROJECT_ROOT
    violations = _collect_violations(root)
    baseline = _load_baseline(baseline_file)

    if update_baseline:
        _save_baseline(violations, baseline_file)
        bf = baseline_file or BASELINE_FILE
        print(f"Baseline updated: {len(violations)} offenders → {bf}")
        return 0

    new_violations = [
        (p, ln, reason)
        for p, ln, reason in violations
        if f"{p}:{ln}" not in baseline
    ]
    grandfathered = [
        (p, ln, reason)
        for p, ln, reason in violations
        if f"{p}:{ln}" in baseline
    ]

    for p, ln, reason in new_violations:
        print(f"NEW VIOLATION: {p}:{ln}: {reason}")

    if show_all:
        for p, ln, reason in grandfathered:
            print(f"GRANDFATHERED: {p}:{ln}: {reason}")
    elif grandfathered:
        print(
            f"GRANDFATHERED: {len(grandfathered)} existing offenders "
            f"(run --show-all to list)"
        )

    if check and new_violations:
        return 1
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="AST lint: no new bare-except or silent except-Exception handlers."
    )
    parser.add_argument(
        "--check",
        action="store_true",
        default=False,
        help="Exit 1 on new violations (default behavior when no flag given)",
    )
    parser.add_argument(
        "--update-baseline",
        action="store_true",
        help="Regenerate bare_except_baseline.txt with all current offenders",
    )
    parser.add_argument(
        "--show-all",
        action="store_true",
        help="Print all violations including grandfathered",
    )
    args = parser.parse_args(argv)

    update = args.update_baseline
    # Default is check mode; --check flag is explicit alias for same behaviour
    check = not update

    return _run(
        check=check,
        update_baseline=update,
        show_all=args.show_all,
    )


if __name__ == "__main__":
    sys.exit(main())
