"""Tests for scripts/lint_bare_except.py"""
from __future__ import annotations

import sys
import textwrap
from pathlib import Path

import pytest

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

import scripts.lint_bare_except as _mod
from scripts.lint_bare_except import _collect_violations, _run


# ── Helpers ───────────────────────────────────────────────────────────────────

def _write_py(tmp_path: Path, name: str, code: str) -> Path:
    """Write dedented Python source to a temp file and return the Path."""
    f = tmp_path / name
    f.write_text(textwrap.dedent(code))
    return f


# ── Detection tests ───────────────────────────────────────────────────────────

def test_bare_except_detected(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Bare `except:` must be flagged as a violation."""
    _write_py(tmp_path, "bad.py", """\
        try:
            x = 1
        except:
            pass
    """)
    monkeypatch.setattr(_mod, "PROJECT_ROOT", tmp_path)
    violations = _collect_violations(tmp_path)
    paths = [v[0] for v in violations]
    reasons = [v[2] for v in violations]
    assert any("bad.py" in p for p in paths), f"Expected violation in bad.py, got {violations}"
    assert any("bare except" in r for r in reasons)


def test_except_exception_with_logger_passes(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """`except Exception:` with logger.exception() must NOT be flagged."""
    _write_py(tmp_path, "good_logger.py", """\
        import logging
        logger = logging.getLogger(__name__)
        try:
            x = 1
        except Exception as e:
            logger.exception("Failed: %s", e)
    """)
    monkeypatch.setattr(_mod, "PROJECT_ROOT", tmp_path)
    violations = _collect_violations(tmp_path)
    assert not any("good_logger.py" in v[0] for v in violations), (
        f"Unexpected violation: {violations}"
    )


def test_except_exception_with_raise_passes(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """`except Exception:` with bare `raise` must NOT be flagged."""
    _write_py(tmp_path, "good_raise.py", """\
        try:
            x = 1
        except Exception:
            raise
    """)
    monkeypatch.setattr(_mod, "PROJECT_ROOT", tmp_path)
    violations = _collect_violations(tmp_path)
    assert not any("good_raise.py" in v[0] for v in violations)


def test_except_exception_no_logger_flagged(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """`except Exception:` with only `pass` must be flagged."""
    _write_py(tmp_path, "silent.py", """\
        try:
            x = 1
        except Exception:
            pass
    """)
    monkeypatch.setattr(_mod, "PROJECT_ROOT", tmp_path)
    violations = _collect_violations(tmp_path)
    assert any("silent.py" in v[0] for v in violations)


# ── Baseline tests ────────────────────────────────────────────────────────────

def test_grandfathered_baseline(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Violations already in baseline must not cause exit 1 (grandfathered)."""
    _write_py(tmp_path, "legacy.py", """\
        try:
            x = 1
        except:
            pass
    """)
    monkeypatch.setattr(_mod, "PROJECT_ROOT", tmp_path)
    violations = _collect_violations(tmp_path)
    assert violations, "Should detect at least one violation in legacy.py"

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
    assert exit_code == 0, "All violations are grandfathered → should exit 0"


def test_new_violation_not_in_baseline_fails(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A violation not present in baseline must cause exit 1."""
    _write_py(tmp_path, "new_bad.py", """\
        try:
            x = 1
        except:
            pass
    """)
    monkeypatch.setattr(_mod, "PROJECT_ROOT", tmp_path)

    # Empty baseline — no offenders grandfathered
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
    assert exit_code == 1, "New violation not in baseline → should exit 1"


def test_update_baseline_creates_file(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """--update-baseline should write the baseline file and exit 0."""
    _write_py(tmp_path, "some.py", """\
        try:
            x = 1
        except:
            pass
    """)
    monkeypatch.setattr(_mod, "PROJECT_ROOT", tmp_path)
    baseline_path = tmp_path / "baseline_out.txt"
    monkeypatch.setattr(_mod, "BASELINE_FILE", baseline_path)

    exit_code = _run(
        check=False,
        update_baseline=True,
        show_all=False,
        project_root=tmp_path,
        baseline_file=baseline_path,
    )
    assert exit_code == 0
    assert baseline_path.exists()
    assert "some.py" in baseline_path.read_text()
