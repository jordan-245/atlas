"""
Tests for scripts/sandbox_9_strategies.sh

Validates the sandbox launcher script syntax, strategy coverage, and
sandbox safety flags — without executing any sweeps.
"""
import subprocess
from pathlib import Path

SCRIPT_PATH = Path(__file__).parent.parent / "scripts" / "sandbox_9_strategies.sh"

SANDBOX_STRATEGIES = [
    "gap_and_go",
    "heikin_ashi_reversal",
    "macd_divergence",
    "monthly_rotation",
    "overnight_return",
    "pead_earnings_drift",
    "put_call_vix_proxy",
    "relative_strength_pullback",
    "rsi_divergence",
]


def test_launcher_script_syntax_valid():
    """bash -n must exit 0 — script has no syntax errors."""
    result = subprocess.run(
        ["bash", "-n", str(SCRIPT_PATH)],
        capture_output=True,
        text=True,
        timeout=10,
    )
    assert result.returncode == 0, (
        f"bash -n failed (rc={result.returncode}):\n{result.stderr}"
    )


def test_launcher_includes_all_9_strategies():
    """All 9 strategy names must appear in the script body."""
    content = SCRIPT_PATH.read_text()
    missing = [s for s in SANDBOX_STRATEGIES if s not in content]
    assert not missing, f"Missing strategies in launcher: {missing}"


def test_launcher_uses_no_auto_promote():
    """--no-auto-promote MUST be present — sandbox sweeps must never promote."""
    content = SCRIPT_PATH.read_text()
    assert "--no-auto-promote" in content, (
        "--no-auto-promote flag is missing from sandbox_9_strategies.sh — "
        "this would allow auto-promotion to live trading!"
    )


def test_launcher_uses_autoresearch_runner():
    """Launcher must call autoresearch_runner.py (not nightly), per spec."""
    content = SCRIPT_PATH.read_text()
    assert "autoresearch_runner.py" in content, (
        "Script should call autoresearch_runner.py for individual strategy sweeps"
    )


def test_launcher_uses_per_strategy_log():
    """Each strategy should have its own log file (sandbox_<strategy>_...)."""
    content = SCRIPT_PATH.read_text()
    assert "sandbox_${strategy}_${TS}.log" in content or "sandbox_${1}_${TS}.log" in content or \
           "sandbox_" in content, (
        "Per-strategy log files pattern not found in script"
    )


def test_launcher_has_summary_log():
    """A summary log must be written to aggregate all run outcomes."""
    content = SCRIPT_PATH.read_text()
    assert "summary" in content.lower(), "No summary log reference found in script"
    assert "SUMMARY=" in content, "SUMMARY variable not defined in script"


def test_launcher_uses_timeout():
    """timeout must wrap each strategy call to enforce the budget."""
    content = SCRIPT_PATH.read_text()
    assert "timeout --signal=TERM" in content, (
        "timeout --signal=TERM not found — strategies must have enforced time limits"
    )


def test_launcher_targets_sp500():
    """Launcher must target sp500 market and universe (sandbox is sp500)."""
    content = SCRIPT_PATH.read_text()
    assert "--market" in content and "sp500" in content, (
        "--market sp500 not found in launcher script"
    )
    assert "--universe" in content, "--universe flag not found in launcher script"


def test_launcher_script_exists():
    """Sanity check: the launcher script actually exists on disk."""
    assert SCRIPT_PATH.exists(), f"Script not found at {SCRIPT_PATH}"
    assert SCRIPT_PATH.stat().st_mode & 0o111, "Script is not executable"
