#!/usr/bin/env python3
"""Atlas Strategy Sanity Check — validate a strategy's basic viability.

Runs a solo backtest via strategy_evaluator and checks whether the strategy
meets minimum thresholds for inclusion in the autoresearch queue.

Usage:
    python3 scripts/sanity_check.py --strategy mean_reversion [--market sp500]

Exit codes:
    0 — PASS: trades >= 30 AND sharpe > -0.5
    1 — FAIL: strategy doesn't meet minimum thresholds
    2 — ERROR: import failure, evaluation exception, or timeout

Output: JSON to stdout (always).
Timeout: 300 seconds.
"""

import argparse
import importlib.util
import json
import logging
import os
import signal
import sys
from pathlib import Path

# ─── Project Setup ───────────────────────────────────────────────────────────

PROJECT = Path(__file__).resolve().parent.parent
os.chdir(PROJECT)
sys.path.insert(0, str(PROJECT))

# ─── Constants ───────────────────────────────────────────────────────────────

PASS_MIN_TRADES = 30
PASS_MIN_SHARPE = -0.5
TIMEOUT_SECONDS = 300

# ─── Timeout ─────────────────────────────────────────────────────────────────


class _TimeoutError(Exception):
    pass


def _handle_timeout(signum, frame):
    raise _TimeoutError(f"Evaluation timed out after {TIMEOUT_SECONDS}s")


# ─── Import strategy_evaluator ───────────────────────────────────────────────


def _load_evaluate_strategy():
    """Load evaluate_strategy() from scripts/strategy_evaluator.py."""
    se_path = Path(__file__).resolve().parent / "strategy_evaluator.py"
    spec = importlib.util.spec_from_file_location("strategy_evaluator", se_path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod.evaluate_strategy


# ─── CLI ─────────────────────────────────────────────────────────────────────


def parse_args():
    p = argparse.ArgumentParser(
        description="Atlas Strategy Sanity Check",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )
    p.add_argument("--strategy", required=True, help="Strategy name to validate")
    p.add_argument("--market", default="sp500",
                   help="Market ID (default: sp500)")
    return p.parse_args()


# ─── Main ────────────────────────────────────────────────────────────────────


def main() -> int:
    args = parse_args()
    strategy = args.strategy
    market = args.market

    # ── Load evaluator ───────────────────────────────────────────────────────
    try:
        evaluate_strategy = _load_evaluate_strategy()
    except Exception as e:
        result = {
            "strategy": strategy,
            "market": market,
            "verdict": "error",
            "reason": f"Failed to import strategy_evaluator: {type(e).__name__}: {e}",
            "criteria": {"min_trades": PASS_MIN_TRADES, "min_sharpe": PASS_MIN_SHARPE},
            "metrics": {},
        }
        print(json.dumps(result, indent=2))
        return 2

    # ── Run evaluation with timeout ──────────────────────────────────────────
    signal.signal(signal.SIGALRM, _handle_timeout)
    signal.alarm(TIMEOUT_SECONDS)
    try:
        eval_result = evaluate_strategy(
            strategy_name=strategy,
            market_id=market,
        )
    except _TimeoutError as e:
        result = {
            "strategy": strategy,
            "market": market,
            "verdict": "error",
            "reason": str(e),
            "criteria": {"min_trades": PASS_MIN_TRADES, "min_sharpe": PASS_MIN_SHARPE},
            "metrics": {},
        }
        print(json.dumps(result, indent=2))
        return 2
    except Exception as e:
        result = {
            "strategy": strategy,
            "market": market,
            "verdict": "error",
            "reason": f"Evaluation failed: {type(e).__name__}: {e}",
            "criteria": {"min_trades": PASS_MIN_TRADES, "min_sharpe": PASS_MIN_SHARPE},
            "metrics": {},
        }
        print(json.dumps(result, indent=2))
        return 2
    finally:
        signal.alarm(0)

    # ── Extract metrics ──────────────────────────────────────────────────────
    solo = eval_result.get("solo", {})
    trades = int(solo.get("total_trades", 0) or 0)
    sharpe = float(solo.get("sharpe", 0) or 0)

    # ── Apply pass criteria ──────────────────────────────────────────────────
    pass_trades = trades >= PASS_MIN_TRADES
    pass_sharpe = sharpe > PASS_MIN_SHARPE
    passed = pass_trades and pass_sharpe

    reasons = []
    if not pass_trades:
        reasons.append(f"trades {trades} < {PASS_MIN_TRADES}")
    if not pass_sharpe:
        reasons.append(f"sharpe {sharpe:.4f} <= {PASS_MIN_SHARPE}")

    result = {
        "strategy": strategy,
        "market": market,
        "verdict": "pass" if passed else "fail",
        "reason": "All criteria met" if passed else f"Failed: {', '.join(reasons)}",
        "criteria": {
            "min_trades": PASS_MIN_TRADES,
            "min_sharpe": PASS_MIN_SHARPE,
        },
        "metrics": {
            "total_trades": trades,
            "sharpe": sharpe,
            "cagr_pct": round(float(solo.get("cagr_pct", 0) or 0), 4),
            "win_rate_pct": round(float(solo.get("win_rate_pct", 0) or 0), 2),
            "profit_factor": round(float(solo.get("profit_factor", 0) or 0), 4),
            "max_drawdown_pct": round(float(solo.get("max_drawdown_pct", 0) or 0), 4),
        },
        "runtime_s": eval_result.get("runtime_s"),
    }

    print(json.dumps(result, indent=2))
    return 0 if passed else 1


if __name__ == "__main__":
    sys.exit(main())
