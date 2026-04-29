#!/usr/bin/env python3
"""AST lint: verify all pi/claude subprocess one-shot calls include --system-prompt.

Without --system-prompt the pi CLI routes to pay-per-token extra-usage
billing instead of the Claude Max subscription.  See /root/AGENTS.md.

Violations
----------
Any ``subprocess.run`` / ``Popen`` / ``call`` / ``check_output`` /
``check_call`` call where:
  - the first argument is a list literal of string constants
  - the first element is ``"pi"``, ``"claude"``, or ends with ``"/pi"``
    or ``"/claude"``
  - ``"-p"`` or ``"--prompt"`` is present (one-shot mode)
  - ``"--system-prompt"`` is NOT present

If any element of the list is a non-string-literal (e.g. a variable), the
call is skipped — we cannot statically analyse it.

Comparison against ``pi_system_prompt_baseline.txt`` (path:lineno lines):
  - Offenders IN the baseline → GRANDFATHERED (exit 0)
  - New offenders → VIOLATION (exit 1 in --check mode)

Usage
-----
    python3 scripts/lint_pi_system_prompt.py            # same as --check
    python3 scripts/lint_pi_system_prompt.py --check
    python3 scripts/lint_pi_system_prompt.py --update-baseline
    python3 scripts/lint_pi_system_prompt.py --show-all
"""
from __future__ import annotations

import argparse
import ast
import sys
from pathlib import Path
from typing import Optional

PROJECT_ROOT = Path(__file__).resolve().parent.parent
BASELINE_FILE = PROJECT_ROOT / "pi_system_prompt_baseline.txt"

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

_SUBPROCESS_FUNCS = frozenset({"run", "Popen", "call", "check_output", "check_call"})
_PI_CMDS = frozenset({"pi", "claude"})


def _should_skip(rel: Path) -> bool:
    parts = rel.parts
    n = len(parts)
    for seg in _SKIP_SEGS:
        seg_len = len(seg)
        for i in range(n - seg_len + 1):
            if parts[i : i + seg_len] == seg:
                return True
    return False


def _is_subprocess_call(node: ast.Call) -> bool:
    func = node.func
    if isinstance(func, ast.Attribute):
        return func.attr in _SUBPROCESS_FUNCS
    if isinstance(func, ast.Name):
        return func.id in _SUBPROCESS_FUNCS
    return False


def _is_pi_cmd(s: str) -> bool:
    return s in _PI_CMDS or s.endswith("/pi") or s.endswith("/claude")


def _check_call_for_violation(call: ast.Call) -> bool:
    """Return True if this call is a pi/claude one-shot missing --system-prompt.

    Returns False (no violation) when any non-string-literal element is found
    — we can't statically analyse those, so we skip rather than false-positive.
    """
    if not call.args:
        return False
    first = call.args[0]
    if not isinstance(first, ast.List) or not first.elts:
        return False

    # Collect string literals; bail if any element is not a plain string constant
    strs: list[str] = []
    for elt in first.elts:
        if isinstance(elt, ast.Constant) and isinstance(elt.value, str):
            strs.append(elt.value)
        else:
            return False  # variable/expression → skip (can't statically analyse)

    # First element must be pi / claude
    if not _is_pi_cmd(strs[0]):
        return False

    # Must be one-shot mode (-p or --prompt)
    if "-p" not in strs and "--prompt" not in strs:
        return False

    # --system-prompt present → OK
    if "--system-prompt" in strs:
        return False

    return True  # Violation: one-shot pi/claude call without --system-prompt


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
        except Exception:  # noqa: BLE001
            continue

        for node in ast.walk(tree):
            if not isinstance(node, ast.Call):
                continue
            if not _is_subprocess_call(node):
                continue
            if _check_call_for_violation(node):
                violations.append((
                    str(rel),
                    node.lineno,
                    "pi/claude one-shot call missing --system-prompt",
                ))

    return violations


def _load_baseline(baseline_file: Optional[Path] = None) -> set[str]:
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
        description="AST lint: all pi/claude one-shot calls must include --system-prompt."
    )
    parser.add_argument(
        "--check",
        action="store_true",
        default=False,
        help="Exit 1 on new violations (default behaviour when no flag given)",
    )
    parser.add_argument(
        "--update-baseline",
        action="store_true",
        help="Regenerate pi_system_prompt_baseline.txt with all current offenders",
    )
    parser.add_argument(
        "--show-all",
        action="store_true",
        help="Print all violations including grandfathered",
    )
    args = parser.parse_args(argv)

    update = args.update_baseline
    check = not update

    return _run(
        check=check,
        update_baseline=update,
        show_all=args.show_all,
    )


if __name__ == "__main__":
    sys.exit(main())
