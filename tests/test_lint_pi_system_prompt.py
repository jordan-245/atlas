"""Tests for scripts/lint_pi_system_prompt.py"""
from __future__ import annotations

import sys
import textwrap
from pathlib import Path

import pytest

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

import scripts.lint_pi_system_prompt as _mod
from scripts.lint_pi_system_prompt import _collect_violations, _run


# ── Helpers ───────────────────────────────────────────────────────────────────

def _write_py(tmp_path: Path, name: str, code: str) -> Path:
    f = tmp_path / name
    f.write_text(textwrap.dedent(code))
    return f


# ── Detection tests ───────────────────────────────────────────────────────────

def test_subprocess_with_system_prompt_passes(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """subprocess.run with --system-prompt present → no violation."""
    _write_py(tmp_path, "ok.py", """\
        import subprocess
        result = subprocess.run(
            ["pi", "-p", "--model", "claude-sonnet-4-6",
             "--system-prompt", "You are Claude Code.",
             "--mode", "json"],
            input="hello",
            capture_output=True,
            text=True,
        )
    """)
    monkeypatch.setattr(_mod, "PROJECT_ROOT", tmp_path)
    violations = _collect_violations(tmp_path)
    assert not any("ok.py" in v[0] for v in violations), (
        f"Unexpected violation: {violations}"
    )


def test_subprocess_without_system_prompt_fails(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """subprocess.run with pi -p but no --system-prompt → violation."""
    _write_py(tmp_path, "bad.py", """\
        import subprocess
        result = subprocess.run(
            ["pi", "-p", "--model", "claude-sonnet-4-6", "--mode", "json"],
            input="hello",
            capture_output=True,
            text=True,
        )
    """)
    monkeypatch.setattr(_mod, "PROJECT_ROOT", tmp_path)
    violations = _collect_violations(tmp_path)
    assert any("bad.py" in v[0] for v in violations), (
        f"Expected violation in bad.py, got {violations}"
    )


def test_subprocess_not_pi_call_skipped(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """subprocess.run with a non-pi command must be ignored."""
    _write_py(tmp_path, "other.py", """\
        import subprocess
        result = subprocess.run(
            ["python3", "-c", "print('hello')"],
            capture_output=True,
            text=True,
        )
    """)
    monkeypatch.setattr(_mod, "PROJECT_ROOT", tmp_path)
    violations = _collect_violations(tmp_path)
    assert not any("other.py" in v[0] for v in violations)


def test_claude_cmd_without_system_prompt_fails(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """`claude` (alias) without --system-prompt must also be flagged."""
    _write_py(tmp_path, "claude_bad.py", """\
        import subprocess
        result = subprocess.run(
            ["claude", "-p", "--mode", "json"],
            input="hi",
            capture_output=True,
            text=True,
        )
    """)
    monkeypatch.setattr(_mod, "PROJECT_ROOT", tmp_path)
    violations = _collect_violations(tmp_path)
    assert any("claude_bad.py" in v[0] for v in violations)


def test_grandfathered_baseline(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Violation in baseline → grandfathered → exit 0."""
    _write_py(tmp_path, "legacy.py", """\
        import subprocess
        result = subprocess.run(
            ["pi", "-p", "--mode", "json"],
            input="prompt",
            capture_output=True,
            text=True,
        )
    """)
    monkeypatch.setattr(_mod, "PROJECT_ROOT", tmp_path)
    violations = _collect_violations(tmp_path)
    assert violations, "Should detect violation in legacy.py"

    baseline_path = tmp_path / "baseline.txt"
    baseline_path.write_text(
        "\n".join(sorted(f"{p}:{ln}" for p, ln, _ in violations)) + "\n"
    )
    monkeypatch.setattr(_mod, "BASELINE_FILE", baseline_path)

    exit_code = _run(
        check=True,
        update_baseline=False,
        show_all=False,
        project_root=tmp_path,
        baseline_file=baseline_path,
    )
    assert exit_code == 0, "Grandfathered violation → should exit 0"


def test_new_violation_not_in_baseline_fails(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """New violation not in baseline → exit 1."""
    _write_py(tmp_path, "new_bad.py", """\
        import subprocess
        result = subprocess.run(
            ["pi", "-p", "--mode", "json"],
            input="prompt",
            capture_output=True,
            text=True,
        )
    """)
    monkeypatch.setattr(_mod, "PROJECT_ROOT", tmp_path)

    baseline_path = tmp_path / "baseline.txt"
    baseline_path.write_text("")
    monkeypatch.setattr(_mod, "BASELINE_FILE", baseline_path)

    exit_code = _run(
        check=True,
        update_baseline=False,
        show_all=False,
        project_root=tmp_path,
        baseline_file=baseline_path,
    )
    assert exit_code == 1, "New violation not in baseline → exit 1"


def test_call_without_onshot_flag_ignored(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """subprocess.run with pi but no -p/--prompt → not a one-shot → skip."""
    _write_py(tmp_path, "interactive.py", """\
        import subprocess
        result = subprocess.run(
            ["pi", "--model", "claude-sonnet-4-6"],
            capture_output=True,
            text=True,
        )
    """)
    monkeypatch.setattr(_mod, "PROJECT_ROOT", tmp_path)
    violations = _collect_violations(tmp_path)
    assert not any("interactive.py" in v[0] for v in violations)
