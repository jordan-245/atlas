"""
Regression test: scripts/cli.py argparse flag ordering contract.

-m/--market is a GLOBAL flag defined on the top-level parser BEFORE
add_subparsers().  It MUST appear BEFORE the subcommand on the command line.
Subcommand-specific flags (--date, --days) appear AFTER.

This test locks in the contract so that anyone changing argparse or the
TypeScript buildCliInvocation() in:
    pi-package/atlas-ops/extensions/atlas-jobs/src/index.ts
will see failures here and know to keep -m BEFORE the subcommand.

Bug history: 7f39fea3 (Mar 3) put -m AFTER the subcommand; dormant until
Apr 28 when the agent started passing args.market, causing premarket
cli_ingest + cli_plan to exit(2) and triggering kill_switch fallback.
"""

import subprocess
import sys
from pathlib import Path

ATLAS_ROOT = Path(__file__).parent.parent
CLI = str(ATLAS_ROOT / "scripts" / "cli.py")
PYTHON = sys.executable


def _run(args: list[str]) -> subprocess.CompletedProcess:
    return subprocess.run(
        [PYTHON, CLI] + args,
        capture_output=True,
        text=True,
        timeout=10,
    )


def test_market_flag_before_subcommand_succeeds() -> None:
    """python3 scripts/cli.py -m sp500 status --help  ->  exit 0."""
    result = _run(["-m", "sp500", "status", "--help"])
    assert result.returncode == 0, (
        f"Expected exit 0 with -m BEFORE subcommand, got {result.returncode}.\n"
        f"stderr: {result.stderr}"
    )


def test_market_flag_after_subcommand_fails() -> None:
    """python3 scripts/cli.py status -m sp500  ->  exit 2 (argparse error)."""
    result = _run(["status", "-m", "sp500"])
    assert result.returncode == 2, (
        f"Expected exit 2 with -m AFTER subcommand, got {result.returncode}.\n"
        "If this passes, argparse has changed and the TS extension may need review.\n"
        f"stderr: {result.stderr}"
    )
    # Double-check argparse error message is present
    assert "unrecognized arguments" in result.stderr, (
        f"Expected 'unrecognized arguments' in stderr, got: {result.stderr}"
    )


def test_plan_subcommand_with_market_before_and_date_after() -> None:
    """python3 scripts/cli.py -m sp500 plan --date 2026-04-28 --help  ->  exit 0.

    Validates the full correct ordering: global flag -> subcommand -> subcommand flag.
    This mirrors exactly what buildCliInvocation() must produce.
    """
    result = _run(["-m", "sp500", "plan", "--date", "2026-04-28", "--help"])
    assert result.returncode == 0, (
        f"Expected exit 0, got {result.returncode}.\n"
        f"stderr: {result.stderr}"
    )
