"""
Tests for the pre-commit hook's Python syntax-check section.

Each test:
 - Creates a fresh git repo in tmp_path
 - Copies the canonical hook into .git/hooks/pre-commit
 - Stages files via ``git add`` / ``git rm``
 - Invokes the hook directly
 - Asserts on returncode and output

No real Atlas commits are made; tests are fully self-contained.
"""
import os
import shutil
import subprocess
from pathlib import Path

import pytest

ATLAS_ROOT = Path("/root/atlas")
HOOK_SOURCE = ATLAS_ROOT / "scripts" / "git-hooks" / "pre-commit"


# ── helpers ──────────────────────────────────────────────────────────────────

def _make_repo(tmp_path: Path) -> Path:
    """Initialise a throwaway git repo and install the hook."""
    subprocess.run(["git", "init", "-q"], cwd=tmp_path, check=True)
    subprocess.run(["git", "config", "user.email", "test@test.invalid"], cwd=tmp_path, check=True)
    subprocess.run(["git", "config", "user.name", "Test Runner"], cwd=tmp_path, check=True)
    hooks_dir = tmp_path / ".git" / "hooks"
    hooks_dir.mkdir(parents=True, exist_ok=True)
    shutil.copy(HOOK_SOURCE, hooks_dir / "pre-commit")
    os.chmod(hooks_dir / "pre-commit", 0o755)
    return tmp_path


def _run_hook(repo: Path) -> subprocess.CompletedProcess:
    """Execute the hook in repo context and return the CompletedProcess."""
    return subprocess.run(
        [str(repo / ".git" / "hooks" / "pre-commit")],
        cwd=repo,
        capture_output=True,
        text=True,
    )


def _stage_file(repo: Path, name: str, content: str) -> None:
    """Write a file to repo and ``git add`` it."""
    (repo / name).write_text(content)
    subprocess.run(["git", "add", name], cwd=repo, check=True)


def _make_initial_commit(repo: Path) -> None:
    """Create an initial commit so ``git rm`` has something to remove."""
    (repo / ".gitkeep").write_text("")
    subprocess.run(["git", "add", ".gitkeep"], cwd=repo, check=True)
    subprocess.run(
        ["git", "commit", "-q", "-m", "init", "--no-verify"],
        cwd=repo,
        check=True,
    )


# ── tests ─────────────────────────────────────────────────────────────────────

def test_hook_blocks_syntactically_broken_python(tmp_path: Path) -> None:
    """A staged .py file with a syntax error must cause the hook to exit 1."""
    repo = _make_repo(tmp_path)
    # Write the broken code using write_bytes to avoid any escape confusion.
    (repo / "bad.py").write_bytes(b"def foo(:\n    pass\n")
    subprocess.run(["git", "add", "bad.py"], cwd=repo, check=True)
    result = _run_hook(repo)
    assert result.returncode == 1, (
        f"Expected hook to block (rc=1), got rc={result.returncode}\n"
        f"stdout: {result.stdout}\nstderr: {result.stderr}"
    )
    combined = (result.stdout + result.stderr).lower()
    assert "py_compile" in combined or "syntax" in combined, (
        f"Expected py_compile/syntax mention in output.\nstdout: {result.stdout}\nstderr: {result.stderr}"
    )


def test_hook_passes_valid_python(tmp_path: Path) -> None:
    """A staged .py file with valid syntax must allow the hook to exit 0."""
    repo = _make_repo(tmp_path)
    _stage_file(repo, "good.py", "def foo():\n    return 42\n")
    result = _run_hook(repo)
    assert result.returncode == 0, (
        f"Expected hook to pass (rc=0), got rc={result.returncode}\n"
        f"stdout: {result.stdout}\nstderr: {result.stderr}"
    )


def test_hook_ignores_deleted_python_file(tmp_path: Path) -> None:
    """Deleting a .py file must NOT cause py_compile to run on a missing path."""
    repo = _make_repo(tmp_path)
    # Commit the file first so we can delete it.
    _make_initial_commit(repo)
    (repo / "will_be_deleted.py").write_text("x = 1\n")
    subprocess.run(["git", "add", "will_be_deleted.py"], cwd=repo, check=True)
    subprocess.run(
        ["git", "commit", "-q", "-m", "add file", "--no-verify"],
        cwd=repo,
        check=True,
    )
    # Stage the deletion — this exercises the --diff-filter=ACM logic.
    subprocess.run(["git", "rm", "-q", "will_be_deleted.py"], cwd=repo, check=True)
    result = _run_hook(repo)
    assert result.returncode == 0, (
        f"Expected hook to pass for a staged deletion (rc=0), got rc={result.returncode}\n"
        f"stdout: {result.stdout}\nstderr: {result.stderr}"
    )


def test_hook_blocks_broken_python_among_valid(tmp_path: Path) -> None:
    """One bad file alongside good files must still cause the hook to exit 1."""
    repo = _make_repo(tmp_path)
    _stage_file(repo, "good_a.py", "a = 1\n")
    (repo / "bad.py").write_bytes(b"def foo(:\n    pass\n")
    subprocess.run(["git", "add", "bad.py"], cwd=repo, check=True)
    _stage_file(repo, "good_b.py", "b = 2\n")
    result = _run_hook(repo)
    assert result.returncode == 1, (
        f"Expected hook to block (rc=1) when one bad file is present, "
        f"got rc={result.returncode}\nstdout: {result.stdout}\nstderr: {result.stderr}"
    )
    combined = (result.stdout + result.stderr).lower()
    assert "bad.py" in combined, (
        f"Expected bad.py to be mentioned in output.\nstdout: {result.stdout}\nstderr: {result.stderr}"
    )
