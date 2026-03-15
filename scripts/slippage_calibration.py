#!/usr/bin/env python3
"""Atlas Slippage Calibration — measures actual vs configured slippage.

Reads filled orders from logs/live_executions.jsonl, computes actual
average slippage (buy-side and sell-side separately), and compares
to config.fees.slippage_pct. Recommends update if actuals differ
from config by >50%.

Run monthly via cron (1st of month, 09:00 AEST), gated on ≥20 fills.

Usage:
    python3 scripts/slippage_calibration.py --market sp500
    python3 scripts/slippage_calibration.py --market sp500 --dry-run
"""
import argparse
import json
import logging
import sys
from dataclasses import dataclass, asdict
from datetime import datetime
from pathlib import Path
from typing import List, Optional

PROJECT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT))

from utils.telegram import send_message

logger = logging.getLogger(__name__)
logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")

MIN_FILLS = 20  # Minimum fills required for calibration
DEVIATION_THRESHOLD = 0.50  # Recommend update if actual differs >50% from config


@dataclass
class SlippageStats:
    """Slippage statistics for one side (buy or sell)."""
    side: str
    count: int
    mean_bps: float
    median_bps: float
    std_bps: float
    min_bps: float
    max_bps: float
    pct_positive: float  # % of fills with positive slippage (unfavorable)
    pct_negative: float  # % of fills with negative slippage (favorable / price improvement)


@dataclass
class CalibrationReport:
    """Full slippage calibration report."""
    timestamp: str
    market: str
    status: str  # INSUFFICIENT_DATA, CALIBRATED
    total_fills: int
    buy_fills: int
    sell_fills: int
    config_slippage_pct: float
    config_slippage_bps: float
    buy_stats: Optional[dict]
    sell_stats: Optional[dict]
    combined_mean_bps: float
    combined_mean_pct: float
    deviation_pct: float  # (actual - config) / config as percentage
    recommendation: str   # KEEP, LOWER, RAISE
    recommended_slippage_pct: Optional[float]
    fills_analyzed: List[dict]  # summary of each fill used


def load_config(market_id: str) -> dict:
    config_path = PROJECT / "config" / "active" / f"{market_id}.json"
    with open(config_path) as f:
        return json.load(f)


def load_fills(market_id: str) -> List[dict]:
    """Load filled orders from live execution log.

    Filters for entries/exits with actual fill data (fill_price > 0
    and planned_price > 0).
    """
    log_path = PROJECT / "logs" / "live_executions.jsonl"
    if not log_path.exists():
        logger.warning("No live executions log found at %s", log_path)
        return []

    fills = []
    with open(log_path) as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                entry = json.loads(line)
            except json.JSONDecodeError:
                continue

            event = entry.get("event", "")
            if event not in ("live_entry", "live_exit"):
                continue

            fill_price = entry.get("fill_price", 0)
            planned_price = entry.get("planned_price", 0)
            success = entry.get("success", False)

            # Only include successful fills with actual fill data
            if not success or fill_price <= 0 or planned_price <= 0:
                continue

            # Determine side
            side_raw = entry.get("side", "").upper()
            if event == "live_entry":
                side = "BUY" if side_raw == "BUY" else "SELL"
            else:
                side = "SELL" if side_raw == "SELL" else "BUY"

            # Compute slippage in basis points
            # Positive = unfavorable (bought higher / sold lower than planned)
            if side == "BUY":
                slippage_bps = (fill_price - planned_price) / planned_price * 10000
            else:
                slippage_bps = (planned_price - fill_price) / planned_price * 10000

            # Use pre-computed slippage_bps if available and fill-derived matches
            recorded_bps = entry.get("slippage_bps")

            fills.append({
                "timestamp": entry.get("timestamp", ""),
                "ticker": entry.get("ticker", ""),
                "side": side,
                "event": event,
                "planned_price": planned_price,
                "fill_price": fill_price,
                "slippage_bps": round(slippage_bps, 2),
                "recorded_bps": recorded_bps,
                "strategy": entry.get("strategy", ""),
                "order_type": entry.get("order_type", ""),
            })

    return fills


def compute_stats(fills: List[dict], side: str) -> Optional[SlippageStats]:
    """Compute slippage statistics for a given side."""
    side_fills = [f for f in fills if f["side"] == side]
    if not side_fills:
        return None

    bps_values = [f["slippage_bps"] for f in side_fills]
    n = len(bps_values)

    sorted_bps = sorted(bps_values)
    median = sorted_bps[n // 2] if n % 2 == 1 else (sorted_bps[n // 2 - 1] + sorted_bps[n // 2]) / 2

    mean = sum(bps_values) / n
    variance = sum((x - mean) ** 2 for x in bps_values) / max(n - 1, 1)
    std = variance ** 0.5

    return SlippageStats(
        side=side,
        count=n,
        mean_bps=round(mean, 2),
        median_bps=round(median, 2),
        std_bps=round(std, 2),
        min_bps=round(min(bps_values), 2),
        max_bps=round(max(bps_values), 2),
        pct_positive=round(sum(1 for x in bps_values if x > 0) / n * 100, 1),
        pct_negative=round(sum(1 for x in bps_values if x < 0) / n * 100, 1),
    )


def calibrate(market_id: str) -> CalibrationReport:
    """Run full slippage calibration for a market."""
    config = load_config(market_id)
    config_slippage_pct = config.get("fees", {}).get("slippage_pct", 0.0005)
    config_slippage_bps = config_slippage_pct * 10000

    fills = load_fills(market_id)
    total = len(fills)
    buy_fills = [f for f in fills if f["side"] == "BUY"]
    sell_fills = [f for f in fills if f["side"] == "SELL"]

    now = datetime.now().isoformat()

    if total < MIN_FILLS:
        return CalibrationReport(
            timestamp=now,
            market=market_id,
            status="INSUFFICIENT_DATA",
            total_fills=total,
            buy_fills=len(buy_fills),
            sell_fills=len(sell_fills),
            config_slippage_pct=config_slippage_pct,
            config_slippage_bps=config_slippage_bps,
            buy_stats=None,
            sell_stats=None,
            combined_mean_bps=0,
            combined_mean_pct=0,
            deviation_pct=0,
            recommendation=f"INSUFFICIENT_DATA — need {MIN_FILLS}+ fills, have {total}",
            recommended_slippage_pct=None,
            fills_analyzed=[],
        )

    # Compute per-side stats
    buy_stats = compute_stats(fills, "BUY")
    sell_stats = compute_stats(fills, "SELL")

    # Combined mean (absolute value — slippage is always a cost)
    all_bps = [abs(f["slippage_bps"]) for f in fills]
    combined_mean_bps = sum(all_bps) / len(all_bps) if all_bps else 0
    combined_mean_pct = combined_mean_bps / 10000

    # Deviation from config
    if config_slippage_bps > 0:
        deviation_pct = (combined_mean_bps - config_slippage_bps) / config_slippage_bps * 100
    else:
        deviation_pct = 100 if combined_mean_bps > 0 else 0

    # Recommendation
    if abs(deviation_pct) <= DEVIATION_THRESHOLD * 100:
        recommendation = "KEEP"
        recommended = None
    elif deviation_pct > 0:
        recommendation = "RAISE"
        # Round to nearest 0.5 bps for clean config values
        recommended = round(combined_mean_pct * 2) / 2 / 10000
        recommended = max(recommended, 0.0001)  # minimum 1 bps
    else:
        recommendation = "LOWER"
        recommended = round(combined_mean_pct * 2) / 2 / 10000
        recommended = max(recommended, 0.0001)

    # Compact fill summaries for report
    fill_summaries = [
        {
            "timestamp": f["timestamp"][:19],
            "ticker": f["ticker"],
            "side": f["side"],
            "planned": f["planned_price"],
            "filled": f["fill_price"],
            "slippage_bps": f["slippage_bps"],
            "strategy": f["strategy"],
        }
        for f in fills
    ]

    return CalibrationReport(
        timestamp=now,
        market=market_id,
        status="CALIBRATED",
        total_fills=total,
        buy_fills=len(buy_fills),
        sell_fills=len(sell_fills),
        config_slippage_pct=config_slippage_pct,
        config_slippage_bps=config_slippage_bps,
        buy_stats=asdict(buy_stats) if buy_stats else None,
        sell_stats=asdict(sell_stats) if sell_stats else None,
        combined_mean_bps=round(combined_mean_bps, 2),
        combined_mean_pct=round(combined_mean_pct, 6),
        deviation_pct=round(deviation_pct, 1),
        recommendation=recommendation,
        recommended_slippage_pct=recommended,
        fills_analyzed=fill_summaries,
    )


def format_telegram(report: CalibrationReport) -> str:
    """Format calibration report as HTML for Telegram."""
    now = datetime.now().strftime("%Y-%m-%d %H:%M")

    if report.status == "INSUFFICIENT_DATA":
        return (
            f"📐 <b>Slippage Calibration [{report.market.upper()}]</b>\n"
            f"<i>{now}</i>\n\n"
            f"<b>Status:</b> ⚠️ INSUFFICIENT DATA\n"
            f"  Fills found: {report.total_fills} (need {MIN_FILLS}+)\n"
            f"  Buy: {report.buy_fills} | Sell: {report.sell_fills}\n\n"
            f"Config slippage: {report.config_slippage_bps:.1f} bps "
            f"({report.config_slippage_pct*100:.3f}%)\n\n"
            f"<i>Will re-check next month when more fills accumulate.</i>"
        )

    icon = {"KEEP": "✅", "RAISE": "⬆️", "LOWER": "⬇️"}.get(report.recommendation, "❓")

    lines = [
        f"📐 <b>Slippage Calibration [{report.market.upper()}]</b>",
        f"<i>{now}</i>",
        "",
        f"<b>Fills analyzed:</b> {report.total_fills} (buy: {report.buy_fills}, sell: {report.sell_fills})",
        "",
        f"<b>Config slippage:</b> {report.config_slippage_bps:.1f} bps ({report.config_slippage_pct*100:.3f}%)",
        f"<b>Actual slippage:</b> {report.combined_mean_bps:.1f} bps ({report.combined_mean_pct*100:.3f}%)",
        f"<b>Deviation:</b> {report.deviation_pct:+.1f}%",
        "",
    ]

    if report.buy_stats:
        bs = report.buy_stats
        lines.append(
            f"<b>Buy side:</b> mean {bs['mean_bps']:.1f} bps, "
            f"median {bs['median_bps']:.1f} bps, "
            f"unfavorable {bs['pct_positive']:.0f}%"
        )

    if report.sell_stats:
        ss = report.sell_stats
        lines.append(
            f"<b>Sell side:</b> mean {ss['mean_bps']:.1f} bps, "
            f"median {ss['median_bps']:.1f} bps, "
            f"unfavorable {ss['pct_positive']:.0f}%"
        )

    lines.append("")
    lines.append(f"{icon} <b>Recommendation: {report.recommendation}</b>")

    if report.recommended_slippage_pct is not None:
        rec_bps = report.recommended_slippage_pct * 10000
        lines.append(
            f"  Update fees.slippage_pct: "
            f"{report.config_slippage_pct} → {report.recommended_slippage_pct} "
            f"({rec_bps:.1f} bps)"
        )

    return "\n".join(lines)


def save_report(report: CalibrationReport) -> Path:
    """Save calibration report to logs/."""
    date_str = datetime.now().strftime("%Y%m%d")
    out_path = PROJECT / "logs" / f"slippage_calibration_{date_str}.json"
    out_path.parent.mkdir(parents=True, exist_ok=True)

    with open(out_path, "w") as f:
        json.dump(asdict(report), f, indent=2, default=str)

    logger.info("Report saved to %s", out_path)
    return out_path


def main():
    parser = argparse.ArgumentParser(description="Atlas slippage calibration")
    parser.add_argument("--market", "-m", default="sp500", help="Market ID")
    parser.add_argument("--dry-run", action="store_true", help="Print report without sending Telegram")
    args = parser.parse_args()

    logger.info("Running slippage calibration for %s", args.market)

    report = calibrate(args.market)

    # Print summary to stdout
    print(f"\n{'='*60}")
    print(f"  SLIPPAGE CALIBRATION — {args.market.upper()}")
    print(f"{'='*60}")
    print(f"  Status:          {report.status}")
    print(f"  Fills:           {report.total_fills} (buy: {report.buy_fills}, sell: {report.sell_fills})")
    print(f"  Config:          {report.config_slippage_bps:.1f} bps ({report.config_slippage_pct*100:.3f}%)")

    if report.status == "CALIBRATED":
        print(f"  Actual (mean):   {report.combined_mean_bps:.1f} bps ({report.combined_mean_pct*100:.3f}%)")
        print(f"  Deviation:       {report.deviation_pct:+.1f}%")
        print(f"  Recommendation:  {report.recommendation}")

        if report.buy_stats:
            bs = report.buy_stats
            print(f"\n  Buy side ({bs['count']} fills):")
            print(f"    Mean: {bs['mean_bps']:.1f} bps | Median: {bs['median_bps']:.1f} bps | Std: {bs['std_bps']:.1f} bps")
            print(f"    Range: [{bs['min_bps']:.1f}, {bs['max_bps']:.1f}] bps")
            print(f"    Unfavorable: {bs['pct_positive']:.0f}% | Price improvement: {bs['pct_negative']:.0f}%")

        if report.sell_stats:
            ss = report.sell_stats
            print(f"\n  Sell side ({ss['count']} fills):")
            print(f"    Mean: {ss['mean_bps']:.1f} bps | Median: {ss['median_bps']:.1f} bps | Std: {ss['std_bps']:.1f} bps")
            print(f"    Range: [{ss['min_bps']:.1f}, {ss['max_bps']:.1f}] bps")
            print(f"    Unfavorable: {ss['pct_positive']:.0f}% | Price improvement: {ss['pct_negative']:.0f}%")

        if report.recommended_slippage_pct is not None:
            print(f"\n  → Update config: fees.slippage_pct = {report.recommended_slippage_pct}")
    else:
        print(f"  Need {MIN_FILLS}+ filled orders for calibration")

    # Save report
    report_path = save_report(report)
    print(f"\n  Report saved: {report_path}")

    # Send Telegram
    if not args.dry_run:
        msg = format_telegram(report)
        ok = send_message(msg, silent=True)
        print(f"  Telegram: {'sent' if ok else 'FAILED'}")
    else:
        print("  Telegram: skipped (dry-run)")

    print()


if __name__ == "__main__":
    main()
