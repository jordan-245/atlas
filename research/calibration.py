"""Confidence score calibration — measures whether confidence predicts outcomes.

Approach: Run a walk-forward backtest with min_confidence=0 (accept ALL signals),
collect all closed trades with their confidence scores, then analyze the
relationship between confidence and trade outcomes.

Reads from:
  - Backtest results (historical signal/outcome pairs via engine)
  - config/active/{market}.json (current thresholds)

Produces:
  - Calibration curve: confidence bucket → actual win rate
  - Brier score per strategy
  - Expected value per confidence bucket (incorporating avg win/loss size)
  - Recommended threshold per strategy
  - Per-strategy report

Usage:
    from research.calibration import calibrate_from_backtest
    report = calibrate_from_backtest(config, "sp500")
    print(report)

CLI:
    atlas calibrate -m sp500
"""
import copy
import json
import logging
import sys
import time
from dataclasses import dataclass, field, asdict
from datetime import datetime
from pathlib import Path
from typing import Dict, List, Optional, Tuple

PROJECT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT))

logger = logging.getLogger(__name__)

# Confidence bucket boundaries
BUCKET_BOUNDARIES = [0.0, 0.1, 0.2, 0.3, 0.4, 0.5, 0.6, 0.7, 0.8, 0.9, 1.01]
BUCKET_LABELS = [
    "0.0-0.1", "0.1-0.2", "0.2-0.3", "0.3-0.4", "0.4-0.5",
    "0.5-0.6", "0.6-0.7", "0.7-0.8", "0.8-0.9", "0.9-1.0",
]


@dataclass
class BucketStats:
    """Statistics for one confidence bucket."""
    bucket: str
    lower: float
    upper: float
    count: int
    winners: int
    losers: int
    win_rate: float
    avg_pnl: float
    avg_win: float
    avg_loss: float
    total_pnl: float
    expected_value: float  # win_rate * avg_win - (1-win_rate) * abs(avg_loss)
    avg_confidence: float


@dataclass
class StrategyCalibration:
    """Calibration results for one strategy."""
    strategy: str
    total_trades: int
    brier_score: float  # mean((confidence - outcome)^2), lower is better
    buckets: List[BucketStats]
    recommended_threshold: float  # lowest confidence where EV > 0
    current_threshold: float
    confidence_correlation: float  # correlation between confidence and return
    calibration_quality: str  # WELL_CALIBRATED, OVERCONFIDENT, UNDERCONFIDENT, UNCORRELATED


@dataclass
class CalibrationReport:
    """Full calibration report across all strategies."""
    timestamp: str
    market: str
    total_trades: int
    runtime_s: float
    current_min_confidence: float
    strategies: Dict[str, StrategyCalibration]
    overall_brier_score: float
    overall_recommended_threshold: float
    overall_buckets: List[BucketStats]
    recommendation: str  # Human-readable recommendation


def _bucket_index(confidence: float) -> int:
    """Return bucket index for a confidence value."""
    for i in range(len(BUCKET_BOUNDARIES) - 1):
        if BUCKET_BOUNDARIES[i] <= confidence < BUCKET_BOUNDARIES[i + 1]:
            return i
    return len(BUCKET_BOUNDARIES) - 2  # last bucket for 1.0


def _compute_bucket_stats(trades: List[dict]) -> List[BucketStats]:
    """Compute per-bucket statistics from a list of trades."""
    # Initialize buckets
    bucket_trades: Dict[int, List[dict]] = {i: [] for i in range(len(BUCKET_LABELS))}

    for t in trades:
        conf = t.get("confidence", 0.0)
        idx = _bucket_index(conf)
        bucket_trades[idx].append(t)

    stats = []
    for i, label in enumerate(BUCKET_LABELS):
        bt = bucket_trades[i]
        n = len(bt)
        if n == 0:
            stats.append(BucketStats(
                bucket=label, lower=BUCKET_BOUNDARIES[i],
                upper=BUCKET_BOUNDARIES[i + 1], count=0,
                winners=0, losers=0, win_rate=0, avg_pnl=0,
                avg_win=0, avg_loss=0, total_pnl=0,
                expected_value=0, avg_confidence=0,
            ))
            continue

        pnls = [t["pnl"] for t in bt]
        confs = [t.get("confidence", 0) for t in bt]
        winners = [p for p in pnls if p > 0]
        losers = [p for p in pnls if p <= 0]
        win_rate = len(winners) / n if n > 0 else 0
        avg_win = sum(winners) / len(winners) if winners else 0
        avg_loss = sum(losers) / len(losers) if losers else 0
        ev = win_rate * avg_win + (1 - win_rate) * avg_loss  # avg_loss is negative

        stats.append(BucketStats(
            bucket=label,
            lower=BUCKET_BOUNDARIES[i],
            upper=BUCKET_BOUNDARIES[i + 1],
            count=n,
            winners=len(winners),
            losers=len(losers),
            win_rate=round(win_rate, 4),
            avg_pnl=round(sum(pnls) / n, 2),
            avg_win=round(avg_win, 2),
            avg_loss=round(avg_loss, 2),
            total_pnl=round(sum(pnls), 2),
            expected_value=round(ev, 2),
            avg_confidence=round(sum(confs) / n, 4),
        ))

    return stats


def _compute_brier_score(trades: List[dict]) -> float:
    """Compute Brier score: mean((confidence - actual_outcome)^2).

    Outcome is 1 for winning trades, 0 for losing trades.
    Lower Brier score = better calibration.
    Perfect calibration = 0.0, random = 0.25.
    """
    if not trades:
        return 1.0

    total = 0.0
    for t in trades:
        conf = t.get("confidence", 0.5)
        outcome = 1.0 if t["pnl"] > 0 else 0.0
        total += (conf - outcome) ** 2

    return round(total / len(trades), 4)


def _compute_correlation(trades: List[dict]) -> float:
    """Compute Pearson correlation between confidence and return_pct."""
    if len(trades) < 5:
        return 0.0

    confs = [t.get("confidence", 0) for t in trades]
    rets = [t.get("return_pct", 0) for t in trades]

    n = len(confs)
    mean_c = sum(confs) / n
    mean_r = sum(rets) / n

    cov = sum((c - mean_c) * (r - mean_r) for c, r in zip(confs, rets)) / n
    std_c = (sum((c - mean_c) ** 2 for c in confs) / n) ** 0.5
    std_r = (sum((r - mean_r) ** 2 for r in rets) / n) ** 0.5

    if std_c < 1e-10 or std_r < 1e-10:
        return 0.0

    return round(cov / (std_c * std_r), 4)


def _find_optimal_threshold(buckets: List[BucketStats]) -> float:
    """Find the lowest confidence where expected value turns positive.

    Walks from lowest to highest bucket. Returns the lower bound of
    the first bucket where cumulative EV from that bucket upward is positive.
    If all buckets have positive EV, returns 0.0 (no threshold needed).
    If no bucket has positive EV, returns 1.0 (nothing should be traded).
    """
    # Check from lowest bucket upward
    # At each threshold, compute EV of all trades >= threshold
    for i in range(len(buckets)):
        remaining = buckets[i:]
        total_trades = sum(b.count for b in remaining)
        if total_trades == 0:
            continue
        total_pnl = sum(b.total_pnl for b in remaining)
        if total_pnl > 0:
            return buckets[i].lower
    return 1.0  # nothing is profitable


def _classify_calibration(brier: float, correlation: float) -> str:
    """Classify calibration quality."""
    if abs(correlation) < 0.05:
        return "UNCORRELATED"
    if brier < 0.20:
        return "WELL_CALIBRATED"
    if brier < 0.25:
        return "MODERATE"
    return "POORLY_CALIBRATED"


def calibrate_from_backtest(
    config: dict,
    market_id: str,
    data: dict = None,
) -> CalibrationReport:
    """Run calibration by backtesting with min_confidence=0.

    This runs a full walk-forward backtest with confidence threshold removed,
    collects all trades, and analyzes the confidence-outcome relationship.

    Args:
        config: Market config dict
        market_id: Market identifier (e.g., "sp500")
        data: Pre-loaded OHLCV data dict. If None, loads from cache.

    Returns:
        CalibrationReport with per-strategy and overall results
    """
    from backtest.engine import BacktestEngine
    from strategies.momentum_breakout import MomentumBreakout
    from strategies.mean_reversion import MeanReversion
    from strategies.trend_following import TrendFollowing
    from strategies.opening_gap import OpeningGap
    from strategies.sector_rotation import SectorRotation
    from strategies.short_term_mr import ShortTermMR
    from strategies.connors_rsi2 import ConnorsRSI2
    import pandas as pd

    t0 = time.time()

    # Record current threshold
    current_min_conf = config.get("risk", {}).get("min_confidence", 0.0)

    # Create a modified config with min_confidence = 0
    cal_config = copy.deepcopy(config)
    cal_config["risk"]["min_confidence"] = 0.0
    # Also remove per-strategy min_confidence overrides
    for strat_name, strat_cfg in cal_config.get("strategies", {}).items():
        if isinstance(strat_cfg, dict) and "min_confidence" in strat_cfg:
            strat_cfg["min_confidence"] = 0.0

    # Load data if not provided
    if data is None:
        from universe.builder import load_universe
        universe_info = load_universe(market_id)
        tickers = universe_info.get("tickers", [])

        base_cache = PROJECT / config["data"]["cache_dir"]
        market_cache = base_cache / market_id
        data = {}
        for ticker in tickers:
            fname = ticker.replace(".", "_") + ".parquet"
            path = market_cache / fname
            if not path.exists():
                path = base_cache / fname
            if path.exists():
                data[ticker] = pd.read_parquet(path)

    if not data:
        raise ValueError(f"No data loaded for market {market_id}")

    logger.info(
        "Running calibration backtest with min_confidence=0 on %d tickers...",
        len(data),
    )

    # Build strategies
    strategies = []
    sc = cal_config["strategies"]
    if sc.get("momentum_breakout", {}).get("enabled"):
        strategies.append(MomentumBreakout(cal_config))
    if sc.get("mean_reversion", {}).get("enabled"):
        strategies.append(MeanReversion(cal_config))
    if sc.get("trend_following", {}).get("enabled"):
        strategies.append(TrendFollowing(cal_config))
    if sc.get("opening_gap", {}).get("enabled"):
        strategies.append(OpeningGap(cal_config))
    if sc.get("sector_rotation", {}).get("enabled"):
        strategies.append(SectorRotation(cal_config))
    if sc.get("short_term_mr", {}).get("enabled"):
        strategies.append(ShortTermMR(cal_config))
    if sc.get("connors_rsi2", {}).get("enabled"):
        strategies.append(ConnorsRSI2(cal_config))

    # Run backtest
    engine = BacktestEngine(cal_config, market_id=market_id)
    result = engine.run_walkforward(data, strategies)
    trades = result.trades if hasattr(result, "trades") else result.get("trades", [])

    logger.info("Calibration backtest complete: %d trades", len(trades))

    # Group trades by strategy
    by_strategy: Dict[str, List[dict]] = {}
    for t in trades:
        strat = t.get("strategy", "unknown")
        by_strategy.setdefault(strat, []).append(t)

    # Compute per-strategy calibration
    strategy_results: Dict[str, StrategyCalibration] = {}
    for strat_name, strat_trades in sorted(by_strategy.items()):
        buckets = _compute_bucket_stats(strat_trades)
        brier = _compute_brier_score(strat_trades)
        corr = _compute_correlation(strat_trades)
        threshold = _find_optimal_threshold(buckets)
        strat_current = config.get("strategies", {}).get(strat_name, {})
        current_thresh = strat_current.get("min_confidence", current_min_conf)
        quality = _classify_calibration(brier, corr)

        strategy_results[strat_name] = StrategyCalibration(
            strategy=strat_name,
            total_trades=len(strat_trades),
            brier_score=brier,
            buckets=buckets,
            recommended_threshold=threshold,
            current_threshold=current_thresh,
            confidence_correlation=corr,
            calibration_quality=quality,
        )

    # Overall calibration
    overall_buckets = _compute_bucket_stats(trades)
    overall_brier = _compute_brier_score(trades)
    overall_threshold = _find_optimal_threshold(overall_buckets)

    # Build recommendation
    rec_parts = []
    if overall_threshold < current_min_conf - 0.05:
        rec_parts.append(
            f"LOWER threshold from {current_min_conf:.2f} to {overall_threshold:.2f} "
            f"— signals above {overall_threshold:.2f} have positive expected value"
        )
    elif overall_threshold > current_min_conf + 0.05:
        rec_parts.append(
            f"RAISE threshold from {current_min_conf:.2f} to {overall_threshold:.2f} "
            f"— signals below {overall_threshold:.2f} have negative expected value"
        )
    else:
        rec_parts.append(
            f"KEEP threshold at {current_min_conf:.2f} — "
            f"optimal is {overall_threshold:.2f}, within tolerance"
        )

    # Check calibration quality
    overall_corr = _compute_correlation(trades)
    if abs(overall_corr) < 0.03:
        rec_parts.append(
            "WARNING: Confidence is UNCORRELATED with returns "
            f"(r={overall_corr:.3f}). The confidence formula needs redesign, "
            "not just threshold adjustment."
        )
    elif overall_brier > 0.25:
        rec_parts.append(
            f"Note: Brier score {overall_brier:.3f} indicates poor calibration. "
            "Consider redesigning confidence formula."
        )

    recommendation = "\n".join(rec_parts)

    elapsed = time.time() - t0
    report = CalibrationReport(
        timestamp=datetime.now().isoformat(),
        market=market_id,
        total_trades=len(trades),
        runtime_s=round(elapsed, 1),
        current_min_confidence=current_min_conf,
        strategies=strategy_results,
        overall_brier_score=overall_brier,
        overall_recommended_threshold=overall_threshold,
        overall_buckets=overall_buckets,
        recommendation=recommendation,
    )

    return report


def print_report(report: CalibrationReport) -> None:
    """Print a formatted calibration report to stdout."""
    print(f"\n{'='*70}")
    print(f"  CONFIDENCE CALIBRATION REPORT — {report.market.upper()}")
    print(f"{'='*70}")
    print(f"  Trades analyzed:       {report.total_trades}")
    print(f"  Runtime:               {report.runtime_s:.0f}s")
    print(f"  Current min_confidence: {report.current_min_confidence}")
    print(f"  Overall Brier score:   {report.overall_brier_score:.4f}")
    print(f"  Recommended threshold: {report.overall_recommended_threshold:.2f}")

    print(f"\n{'─'*70}")
    print(f"  OVERALL CALIBRATION CURVE")
    print(f"{'─'*70}")
    print(f"  {'Bucket':<10} {'Count':>6} {'WinRate':>8} {'AvgPnL':>9} {'AvgWin':>9} "
          f"{'AvgLoss':>9} {'TotPnL':>10} {'EV':>8}")
    print(f"  {'─'*10} {'─'*6} {'─'*8} {'─'*9} {'─'*9} {'─'*9} {'─'*10} {'─'*8}")
    for b in report.overall_buckets:
        if b.count == 0:
            continue
        marker = "◀" if b.lower <= report.current_min_confidence < b.upper else " "
        ev_icon = "+" if b.expected_value > 0 else " "
        print(
            f"  {b.bucket:<10} {b.count:>6} {b.win_rate:>7.1%} "
            f"${b.avg_pnl:>8.2f} ${b.avg_win:>8.2f} ${b.avg_loss:>8.2f} "
            f"${b.total_pnl:>9.2f} {ev_icon}${b.expected_value:>6.2f} {marker}"
        )

    print(f"\n{'─'*70}")
    print(f"  PER-STRATEGY CALIBRATION")
    print(f"{'─'*70}")
    for name, sc in sorted(report.strategies.items()):
        print(f"\n  {name} ({sc.total_trades} trades)")
        print(f"    Brier: {sc.brier_score:.4f}  |  "
              f"Corr: {sc.confidence_correlation:+.4f}  |  "
              f"Quality: {sc.calibration_quality}")
        print(f"    Current threshold: {sc.current_threshold:.2f}  |  "
              f"Recommended: {sc.recommended_threshold:.2f}")

        # Show non-empty buckets
        active = [b for b in sc.buckets if b.count > 0]
        if active:
            print(f"    {'Bucket':<10} {'N':>5} {'WinRate':>8} {'AvgPnL':>9} {'EV':>8}")
            for b in active:
                ev_icon = "✓" if b.expected_value > 0 else "✗"
                print(
                    f"    {b.bucket:<10} {b.count:>5} {b.win_rate:>7.1%} "
                    f"${b.avg_pnl:>8.2f} {ev_icon}${b.expected_value:>6.2f}"
                )

    print(f"\n{'─'*70}")
    print(f"  RECOMMENDATION")
    print(f"{'─'*70}")
    for line in report.recommendation.split("\n"):
        print(f"  {line}")
    print()


def save_report(report: CalibrationReport, market_id: str) -> Path:
    """Save calibration report to research/reports/."""
    date_str = datetime.now().strftime("%Y%m%d")
    out_dir = PROJECT / "research" / "reports"
    out_dir.mkdir(parents=True, exist_ok=True)
    out_path = out_dir / f"calibration_{market_id}_{date_str}.json"

    # Convert to serializable dict
    report_dict = {
        "timestamp": report.timestamp,
        "market": report.market,
        "total_trades": report.total_trades,
        "runtime_s": report.runtime_s,
        "current_min_confidence": report.current_min_confidence,
        "overall_brier_score": report.overall_brier_score,
        "overall_recommended_threshold": report.overall_recommended_threshold,
        "recommendation": report.recommendation,
        "overall_buckets": [asdict(b) for b in report.overall_buckets],
        "strategies": {},
    }
    for name, sc in report.strategies.items():
        report_dict["strategies"][name] = {
            "strategy": sc.strategy,
            "total_trades": sc.total_trades,
            "brier_score": sc.brier_score,
            "confidence_correlation": sc.confidence_correlation,
            "calibration_quality": sc.calibration_quality,
            "recommended_threshold": sc.recommended_threshold,
            "current_threshold": sc.current_threshold,
            "buckets": [asdict(b) for b in sc.buckets],
        }

    with open(out_path, "w") as f:
        json.dump(report_dict, f, indent=2, default=str)

    logger.info("Report saved to %s", out_path)
    return out_path
