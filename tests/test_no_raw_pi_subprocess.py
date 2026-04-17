"""Fails if any non-test, non-helper file calls pi/claude CLI as a raw subprocess
instead of going through utils.pi_subprocess. This prevents re-introduction of
calls that bypass the Claude Max OAuth routing guard.
"""
import re
from pathlib import Path

ATLAS_ROOT = Path(__file__).resolve().parent.parent
ALLOWED = {
    ATLAS_ROOT / "utils" / "pi_subprocess.py",
    # job_server.py uses shell-pipeline construction — grandfathered because
    # it already has explicit --system-prompt inline. Re-audit if this fails.
    ATLAS_ROOT / "services" / "job_server.py",
    # pi_session.py uses asyncio.create_subprocess_exec (async streaming) —
    # a different execution model that cannot use call_pi directly.
    # --system-prompt is present inline; equivalent routing guarantee.
    ATLAS_ROOT / "services" / "pi_session.py",
}

PATTERN = re.compile(r'["\'](?:pi|claude)["\'],\s*["\'](?:-p|--print)["\']')


def test_no_raw_pi_subprocess():
    offenders = []
    for py in ATLAS_ROOT.rglob("*.py"):
        if py.resolve() in {p.resolve() for p in ALLOWED}:
            continue
        s = str(py)
        if "/tests/" in s or "/test_" in py.name or "__pycache__" in s:
            continue
        try:
            text = py.read_text()
        except Exception:
            continue
        if PATTERN.search(text):
            offenders.append(str(py.relative_to(ATLAS_ROOT)))
    assert not offenders, (
        f"Found raw pi/claude subprocess calls outside utils.pi_subprocess in: "
        f"{offenders}. Use call_pi() / call_pi_exec() from utils.pi_subprocess instead."
    )
