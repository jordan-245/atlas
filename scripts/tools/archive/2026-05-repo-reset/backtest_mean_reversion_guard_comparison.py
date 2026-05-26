#!/usr/bin/env python3
"""Backtest comparison: mean_reversion falling-knife guard variants.

Runs 4 solo backtests for mean_reversion/sp500 with different guard configs:
  - Baseline : sma200_filter=False, relative_strength.enabled=False (current)
  - Option A : sma200_filter=True  (trend filter only)
  - Option B : relative_strength enabled, metric=roc_60, low_threshold=-0.20,
               low_penalty=0.30  (momentum floor guard)
  - Option C : both A + B

Decision rule (negative-Sharpe-aware):
  - Baseline Sharpe <= 0 : pick highest Sharpe variant ("stop the bleeding").
  - Baseline Sharpe  > 0 : pick first variant with Sharpe >= 0.80 * baseline.
  In both cases require rejection count > 0 (proxy: trade count delta vs Baseline).
  Waived if Baseline already has zero falling-knife entries in the window.

Outputs results to data/mean_reversion_guard_comparison_<ISO>.json
and prints a markdown table + decision to stdout.

Usage:
    python3 scripts/tools/archive/2026-05-repo-reset/backtest_mean_reversion_guard_comparison.py
"""

from __future__ import annotations

import copy
import json
import logging
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

PROJECT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT))

from utils.logging_config import setup_logging
setup_logging("mr_guard_comparison", level=logging.WARNING)
logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Option B RS patch (falling-knife guard via roc_60 floor)
# ---------------------------------------------------------------------------
_OPTION_B_RS = {
    "enabled": True,
    "metric": "roc_60",
    "low_threshold": -0.20,
    "high_threshold": 0.0,
    "low_penalty": 0.30,
    "high_boost": 0.0,
}

# ---------------------------------------------------------------------------
# Variant definitions
# ---------------------------------------------------------------------------
VARIANTS: list[dict] = [
    {
        "name": "Baseline",
        "description": "No guard (sma200=false, RS=false) -- current config",
        "patch": {},
    },
    {
        "name": "Option A",
        "description": "SMA-200 trend filter only (sma200_filter=True)",
        "patch": {"sma200_filter": True},
    },
    {
        "name": "Option B",
        "description": "ROC-60 momentum floor guard (RS penalty -0.30 when roc_60 < -0.20)",
        "patch": {"relative_strength": _OPTION_B_RS},
    },
    {
        "name": "Option C",
        "description": "Both: SMA-200 + ROC-60 floor",
        "patch": {"sma200_filter": True, "relative_strength": _OPTION_B_RS},
    },
]


def _run_variant(
    variant: dict,
    base_cfg: dict,
    data: dict,
) -> dict:
    """Run a single solo backtest for mean_reversion with a patch applied."""
    from scripts.strategy_evaluator import make_config_with_strategy, run_backtest

    result: dict = {
        "variant": variant["name"],
        "description": variant["description"],
        "started_at": datetime.now(timezone.utc).isoformat(),
    }
    t0 = time.time()
    try:
        # Build solo config: only mean_reversion enabled
        solo_cfg = make_config_with_strategy(base_cfg, "mean_reversion", None, solo=True)

        # Apply variant patches on top of the solo config's mean_reversion block
        patch = variant["patch"]
        if patch:
            mr_block = solo_cfg["strategies"]["mean_reversion"]
            for k, v in patch.items():
                mr_block[k] = v

        # Ensure mean_reversion is actually enabled
        solo_cfg["strategies"]["mean_reversion"]["enabled"] = True

        metrics = run_backtest(solo_cfg, data)
        elapsed = round(time.time() - t0, 1)
        result.update({
            "status": "ok",
            "metrics": metrics,
            "elapsed_s": elapsed,
        })
        logger.info(
            "OK  %s  sharpe=%.4f  trades=%d  elapsed=%ss",
            variant["name"],
            metrics.get("sharpe", 0) or 0,
            metrics.get("total_trades", 0) or 0,
            elapsed,
        )
    except Exception as exc:
        elapsed = round(time.time() - t0, 1)
        result.update({
            "status": "error",
            "error": str(exc),
            "elapsed_s": elapsed,
        })
        logger.error("FAIL  %s  error=%s", variant["name"], exc, exc_info=True)

    result["finished_at"] = datetime.now(timezone.utc).isoformat()
    return result


def _apply_decision_rule(results: list[dict]) -> dict:
    """Apply the decision rule and return a decision dict."""
    baseline_r = next(r for r in results if r["variant"] == "Baseline")
    baseline_sharpe = baseline_r.get("metrics", {}).get("sharpe", None)
    baseline_trades = baseline_r.get("metrics", {}).get("total_trades", 0) or 0

    ok_results = [r for r in results if r["status"] == "ok"]

    # Rejection count proxy: trade-count delta vs Baseline
    for r in ok_results:
        trades = r.get("metrics", {}).get("total_trades", 0) or 0
        delta = baseline_trades - trades
        r["_rejection_proxy"] = max(0, delta)

    # Waive rejection requirement if Baseline traded 0 entries
    rejection_waived = baseline_trades == 0
    rejection_note = (
        "Waived: Baseline traded 0 entries in window."
        if rejection_waived
        else f"Proxy: trade-count delta vs Baseline (Baseline={baseline_trades} trades)."
    )

    if baseline_sharpe is None:
        return {
            "baseline_sharpe": None,
            "rule_applied": "error",
            "chosen": None,
            "rationale": "Baseline backtest errored -- cannot apply decision rule.",
            "rejection_waived": rejection_waived,
            "rejection_note": rejection_note,
        }

    candidates = [r for r in ok_results if r["variant"] != "Baseline"]

    if baseline_sharpe <= 0:
        rule = "baseline_sharpe_le_0__pick_highest"
        eligible = [
            r for r in candidates
            if rejection_waived or r["_rejection_proxy"] > 0
        ]
        if not eligible:
            chosen_name = "Option B"
            rationale = (
                f"Baseline Sharpe={baseline_sharpe:.4f} (<=0). No variant shows "
                f"rejection count >0 in the data window. Defaulting to Option B "
                f"(ROC-60 floor) per spec: falling-knife protection in real money "
                f"outweighs marginal Sharpe loss."
            )
        else:
            best = max(eligible, key=lambda r: r.get("metrics", {}).get("sharpe", -999))
            chosen_name = best["variant"]
            bsharp = best.get("metrics", {}).get("sharpe", 0)
            rej = best["_rejection_proxy"]
            rationale = (
                f"Baseline Sharpe={baseline_sharpe:.4f} (<=0). Applied rule: "
                f"pick highest Sharpe. {chosen_name} has Sharpe={bsharp:.4f} "
                f"and rejection proxy={rej}. {rejection_note}"
            )
    else:
        rule = "baseline_sharpe_gt_0__retention_gte_80pct"
        threshold = 0.80 * baseline_sharpe
        eligible = [
            r for r in candidates
            if (
                (r.get("metrics", {}).get("sharpe") or 0) >= threshold
                and (rejection_waived or r["_rejection_proxy"] > 0)
            )
        ]
        if not eligible:
            chosen_name = "Option B"
            rationale = (
                f"Baseline Sharpe={baseline_sharpe:.4f} (>0) but no variant "
                f"meets retention >=80% ({threshold:.4f}) with rejection count >0. "
                f"Defaulting to Option B (real-money protection rationale)."
            )
        else:
            best = max(eligible, key=lambda r: r.get("metrics", {}).get("sharpe", -999))
            chosen_name = best["variant"]
            bsharp = best.get("metrics", {}).get("sharpe", 0)
            retained_pct = (bsharp / baseline_sharpe) * 100
            rej = best["_rejection_proxy"]
            rationale = (
                f"Baseline Sharpe={baseline_sharpe:.4f} (>0). Applied retention rule "
                f"(>=80% = {threshold:.4f}). {chosen_name} retains "
                f"{retained_pct:.1f}% of Sharpe ({bsharp:.4f}) "
                f"with rejection proxy={rej}. {rejection_note}"
            )

    return {
        "baseline_sharpe": baseline_sharpe,
        "rule_applied": rule,
        "chosen": chosen_name,
        "rationale": rationale,
        "rejection_waived": rejection_waived,
        "rejection_note": rejection_note,
    }


def _print_markdown_table(results: list[dict], decision: dict) -> None:
    """Print a markdown comparison table + decision block to stdout."""
    header = (
        "| Variant   | Sharpe | CAGR    | MaxDD    | Trades | WinRate | ProfitFactor "
        "| Rejection\u2020 |"
    )
    sep = (
        "|-----------|-------:|--------:|---------:|-------:|--------:|-------------:"
        "|-----------:|"
    )
    print()
    print(header)
    print(sep)

    for r in results:
        name = r["variant"]
        if r["status"] != "ok":
            print(f"| {name:<9} | ERROR  |  ERROR  |  ERROR   |    -- |     --  "
                  f"|          -- |         -- |")
            continue
        m = r["metrics"]
        sharpe = m.get("sharpe", 0) or 0
        cagr = m.get("cagr_full_period_pct", m.get("cagr_pct", 0)) or 0
        maxdd = m.get("max_drawdown_pct", 0) or 0
        trades = m.get("total_trades", 0) or 0
        wr = m.get("win_rate_pct", 0) or 0
        pf = m.get("profit_factor", 0) or 0
        rej = r.get("_rejection_proxy", "--")
        print(
            f"| {name:<9} | {sharpe:6.3f} | {cagr:7.2f}% | {maxdd:8.2f}% "
            f"| {trades:6d} | {wr:5.1f}%  | {pf:12.3f} | {rej!s:>10} |"
        )

    bs = decision["baseline_sharpe"] or 0
    rule_label = "<=0 -- pick highest Sharpe" if bs <= 0 else ">0 -- retention >=80%"
    print()
    print(
        "\u2020 Rejection proxy = trade-count delta vs Baseline "
        "(entries blocked by guard). Not a direct signal-level rejection count."
    )
    print()
    print(f"**Decision rule applied**: baseline Sharpe = {bs:.4f} ({rule_label})")
    print(f"**Chosen**: {decision['chosen']}")
    print(f"**Rationale**: {decision['rationale']}")
    print()


def main() -> None:
    from utils.config import get_active_config
    from scripts.strategy_evaluator import load_market_data

    print("=" * 70)
    print("mean_reversion falling-knife guard -- backtest comparison")
    print(f"Run date: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    print("=" * 70)

    print("\n[1/3] Loading sp500 config and market data...")
    cfg = get_active_config("sp500")
    data = load_market_data("sp500")
    n_tickers = len(data)
    print(f"      Loaded {n_tickers} tickers.")

    print("\n[2/3] Running 4 backtest variants (sequential, ~30-60s each)...")
    results: list[dict] = []
    for v in VARIANTS:
        print(f"      -> {v['name']}: {v['description']}")
        r = _run_variant(v, cfg, data)
        results.append(r)
        if r["status"] == "ok":
            print(
                f"         Sharpe={r['metrics'].get('sharpe', 0):.4f}  "
                f"Trades={r['metrics'].get('total_trades', 0)}  "
                f"elapsed={r['elapsed_s']}s"
            )
        else:
            print(f"         ERROR: {r.get('error', 'unknown')}")

    print("\n[3/3] Applying decision rule...")
    decision = _apply_decision_rule(results)

    _print_markdown_table(results, decision)

    out = {
        "run_at": datetime.now(timezone.utc).isoformat(),
        "n_tickers": n_tickers,
        "variants": results,
        "decision": decision,
    }
    data_dir = PROJECT / "data"
    data_dir.mkdir(exist_ok=True)
    ts = datetime.now().strftime("%Y%m%dT%H%M%S")
    out_path = data_dir / f"mean_reversion_guard_comparison_{ts}.json"
    out_path.write_text(json.dumps(out, indent=2, default=str))
    print(f"Results persisted to: {out_path}")

    print(f"\nCHOSEN VARIANT: {decision['chosen']}")
    print(f"RATIONALE     : {decision['rationale']}")
    print()


if __name__ == "__main__":
    main()
