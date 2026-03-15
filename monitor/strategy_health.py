"""Strategy Health Monitor — compares live performance vs backtest expectations.

Tracks per-strategy live metrics (Sharpe, win rate, R-multiple, drawdown)
against backtest benchmarks stored in research/best/{strategy}.json.

Status levels:
  INSUFFICIENT_DATA  — fewer than MIN_TRADES_FOR_METRICS (10) completed trades
  HEALTHY            — live 60-day Sharpe > 50% of backtest Sharpe
  WARNING            — live 60-day Sharpe ≤ 50% of backtest Sharpe (but ≥ 0)
  DEGRADED           — live 60-day Sharpe < 0 for 3+ consecutive weekly checks

Usage:
    from monitor.strategy_health import StrategyHealthMonitor
    monitor = StrategyHealthMonitor(config, "sp500")
    report = monitor.full_health_report("sp500")
    alerts = monitor.check_degradation_alerts("sp500")
"""

from __future__ import annotations

import json
import logging
import math
from dataclasses import dataclass, field, asdict
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)

PROJECT = Path(__file__).resolve().parent.parent

# ── Status constants ───────────────────────────────────────────────────────────

INSUFFICIENT_DATA = "INSUFFICIENT_DATA"
HEALTHY = "HEALTHY"
WARNING = "WARNING"
DEGRADED = "DEGRADED"

MIN_TRADES_FOR_METRICS = 10   # below this → INSUFFICIENT_DATA
MIN_TRADES_FOR_SHARPE = 5     # below this → skip Sharpe computation
SHARPE_HEALTHY_RATIO = 0.5    # live Sharpe must be > 50% of backtest
DEGRADED_CONSECUTIVE_WEEKS = 3


# ── Dataclasses ────────────────────────────────────────────────────────────────

@dataclass
class LiveMetrics:
    """Live performance metrics for a single strategy over a rolling window."""

    strategy: str
    trade_count: int
    status: str               # INSUFFICIENT_DATA, HEALTHY, WARNING, DEGRADED
    window_days: int = 60
    sharpe: Optional[float] = None
    win_rate: Optional[float] = None    # 0.0 – 1.0 (e.g. 0.56 = 56%)
    avg_r: Optional[float] = None       # average R-multiple
    max_drawdown: Optional[float] = None  # negative fraction, e.g. -0.05
    period_start: Optional[str] = None
    period_end: Optional[str] = None


@dataclass
class HealthAssessment:
    """Comparison of live metrics against backtest benchmark for one strategy."""

    strategy: str
    status: str               # INSUFFICIENT_DATA, HEALTHY, WARNING, DEGRADED
    live_sharpe: Optional[float] = None
    backtest_sharpe: Optional[float] = None
    sharpe_ratio: Optional[float] = None  # live / backtest (when both available)
    live_win_rate: Optional[float] = None
    backtest_win_rate: Optional[float] = None
    live_trade_count: int = 0
    message: str = ""
    assessed_at: str = field(
        default_factory=lambda: datetime.now().isoformat(timespec="seconds")
    )


@dataclass
class Alert:
    """An alert for a strategy that is WARNING or DEGRADED."""

    strategy: str
    status: str               # WARNING or DEGRADED
    message: str
    consecutive_degraded_weeks: int = 0
    timestamp: str = field(
        default_factory=lambda: datetime.now().isoformat(timespec="seconds")
    )
    market_id: str = ""


@dataclass
class HealthReport:
    """Full health report for a market — all strategies assessed."""

    market_id: str
    generated_at: str
    assessments: List[HealthAssessment]
    alerts: List[Alert]
    summary: Dict[str, int]  # {"HEALTHY": n, "WARNING": n, ...}

    def to_dict(self) -> dict:
        return {
            "market_id": self.market_id,
            "generated_at": self.generated_at,
            "assessments": [asdict(a) for a in self.assessments],
            "alerts": [asdict(a) for a in self.alerts],
            "summary": self.summary,
        }


# ── Core monitor ───────────────────────────────────────────────────────────────

class StrategyHealthMonitor:
    """Monitors live trading performance vs backtest expectations.

    Args:
        config: Active market config dict (from utils.config.get_active_config).
        market_id: Market identifier, e.g. 'sp500' or 'asx'.
    """

    def __init__(self, config: Dict[str, Any], market_id: str) -> None:
        self.config = config
        self.market_id = market_id
        self._live_trades_cache: Optional[List[dict]] = None

    # ── Data loading ───────────────────────────────────────────────────────────

    def _load_live_trades(self) -> List[dict]:
        """Load all live execution events from disk.

        Sources:
          1. logs/live_executions.jsonl — broker execution events (fill_price > 0, success=True)
          2. journal/trade_ledger.json  — trade ledger (fill_price > 0)

        Returns deduplicated list of event dicts.
        """
        if self._live_trades_cache is not None:
            return self._live_trades_cache

        trades: List[dict] = []
        seen_order_ids: set = set()

        # Source 1: live_executions.jsonl
        executions_path = PROJECT / "logs" / "live_executions.jsonl"
        if executions_path.exists():
            try:
                with open(executions_path) as fh:
                    for line in fh:
                        line = line.strip()
                        if not line:
                            continue
                        try:
                            event = json.loads(line)
                            if event.get("fill_price", 0) > 0 and event.get("success", False):
                                oid = event.get("order_id", "")
                                if oid and oid in seen_order_ids:
                                    continue
                                if oid:
                                    seen_order_ids.add(oid)
                                trades.append(event)
                        except json.JSONDecodeError:
                            continue
            except Exception as exc:
                logger.warning("Failed to read live_executions.jsonl: %s", exc)

        # Source 2: journal/trade_ledger.json
        ledger_path = PROJECT / "journal" / "trade_ledger.json"
        if ledger_path.exists():
            try:
                with open(ledger_path) as fh:
                    ledger = json.load(fh)
                if isinstance(ledger, list):
                    for entry in ledger:
                        if not isinstance(entry, dict):
                            continue
                        if entry.get("fill_price", 0) <= 0:
                            continue
                        oid = entry.get("order_id", "")
                        if oid and oid in seen_order_ids:
                            continue  # already loaded from jsonl
                        if oid:
                            seen_order_ids.add(oid)
                        trades.append(entry)
            except Exception as exc:
                logger.warning("Failed to read trade_ledger.json: %s", exc)

        logger.info("Loaded %d live trade events (market=%s)", len(trades), self.market_id)
        self._live_trades_cache = trades
        return trades

    def _get_completed_trades(
        self, strategy: str, window_days: int = 60
    ) -> List[dict]:
        """Extract completed (entry+exit paired) trades for a strategy within the window.

        A trade is "completed" when:
          - It has a 'pnl' field (already computed by the executor), OR
          - It is an 'exit' event that can be paired with a preceding 'entry'

        Returns list of dicts with keys: pnl, pnl_pct, holding_days, r_multiple, timestamp.
        """
        all_events = self._load_live_trades()
        cutoff = datetime.now() - timedelta(days=window_days)

        # Filter to this strategy
        strat_events = [
            e for e in all_events
            if e.get("strategy") == strategy
        ]

        # Sort by timestamp
        def _ts(e: dict) -> datetime:
            try:
                return datetime.fromisoformat(e.get("timestamp", "1970-01-01T00:00:00"))
            except Exception:
                return datetime.min

        strat_events.sort(key=_ts)

        completed: List[dict] = []

        # Pass 1: events that already carry a P&L
        remaining: List[dict] = []
        for event in strat_events:
            if event.get("pnl") is not None:
                event_dt = _ts(event)
                if event_dt >= cutoff:
                    completed.append({
                        "ticker": event.get("ticker", ""),
                        "strategy": strategy,
                        "pnl": float(event["pnl"]),
                        "pnl_pct": float(event.get("pnl_pct", 0)),
                        "holding_days": int(event.get("holding_days", 1)),
                        "r_multiple": event.get("r_multiple"),
                        "entry_price": event.get("entry_price"),
                        "exit_price": event.get("exit_price"),
                        "timestamp": event.get("timestamp", ""),
                    })
            else:
                remaining.append(event)

        # Pass 2: pair entry/exit events by ticker (FIFO queue per ticker)
        entry_queue: Dict[str, List[dict]] = {}  # ticker → [entry, ...]
        for event in remaining:
            event_type = (event.get("type") or event.get("event_type") or "").lower()
            ticker = event.get("ticker", "")

            if event_type in ("entry", "buy", "open"):
                entry_queue.setdefault(ticker, []).append(event)

            elif event_type in ("exit", "sell", "close"):
                if ticker in entry_queue and entry_queue[ticker]:
                    entry = entry_queue[ticker].pop(0)
                    entry_price = float(entry.get("fill_price", 0))
                    exit_price = float(event.get("fill_price", 0))
                    shares = float(entry.get("shares", event.get("shares", 1)) or 1)
                    stop_price = float(entry.get("stop_price", 0))

                    if entry_price > 0 and exit_price > 0:
                        pnl = (exit_price - entry_price) * shares
                        pnl_pct = (exit_price - entry_price) / entry_price

                        try:
                            entry_dt = datetime.fromisoformat(entry.get("timestamp", ""))
                            exit_dt = datetime.fromisoformat(event.get("timestamp", ""))
                            holding_days = max(1, (exit_dt - entry_dt).days)
                        except Exception:
                            holding_days = 1

                        r_multiple = None
                        if stop_price > 0 and entry_price > stop_price:
                            risk = (entry_price - stop_price) * shares
                            if risk > 0:
                                r_multiple = pnl / risk

                        exit_dt_check = _ts(event)
                        if exit_dt_check >= cutoff:
                            completed.append({
                                "ticker": ticker,
                                "strategy": strategy,
                                "pnl": pnl,
                                "pnl_pct": pnl_pct,
                                "holding_days": holding_days,
                                "r_multiple": r_multiple,
                                "entry_price": entry_price,
                                "exit_price": exit_price,
                                "timestamp": event.get("timestamp", ""),
                            })

        return completed

    # ── Metric computation ─────────────────────────────────────────────────────

    def compute_live_metrics(
        self, strategy: str, window_days: int = 60
    ) -> LiveMetrics:
        """Compute rolling window performance metrics for a strategy.

        Args:
            strategy: Strategy name (e.g. 'mean_reversion').
            window_days: Rolling window in calendar days (default 60).

        Returns:
            LiveMetrics with computed fields. Status = INSUFFICIENT_DATA when
            fewer than MIN_TRADES_FOR_METRICS (10) completed trades exist.
        """
        trades = self._get_completed_trades(strategy, window_days=window_days)
        trade_count = len(trades)

        now_str = datetime.now().isoformat(timespec="seconds")
        cutoff = datetime.now() - timedelta(days=window_days)

        base = LiveMetrics(
            strategy=strategy,
            trade_count=trade_count,
            status=INSUFFICIENT_DATA,
            window_days=window_days,
            period_start=cutoff.isoformat(timespec="seconds"),
            period_end=now_str,
        )

        if trade_count < MIN_TRADES_FOR_METRICS:
            return base

        # Gather P&L values
        pnls = [t["pnl"] for t in trades if t.get("pnl") is not None]
        entry_prices = [
            t["entry_price"] for t in trades
            if t.get("entry_price") and t["entry_price"] > 0
        ]

        if not pnls:
            return base

        # ── Win rate ──────────────────────────────────────────────────────────
        wins = sum(1 for p in pnls if p > 0)
        win_rate = wins / len(pnls)

        # ── Average R-multiple ────────────────────────────────────────────────
        r_multiples = [t["r_multiple"] for t in trades if t.get("r_multiple") is not None]
        avg_r = float(sum(r_multiples) / len(r_multiples)) if r_multiples else None

        # ── Trade returns (as fraction of position value) ─────────────────────
        returns: List[float] = []
        for t in trades:
            pnl = t.get("pnl")
            entry_px = t.get("entry_price")
            shares = 1  # default
            if pnl is not None and entry_px and entry_px > 0:
                shares_val = t.get("shares")
                if shares_val:
                    try:
                        shares = float(shares_val)
                    except (TypeError, ValueError):
                        shares = 1
                pos_value = entry_px * shares
                trade_return = pnl / pos_value if pos_value > 0 else pnl / abs(pnl) * 0.01
                returns.append(trade_return)

        # Fallback: use normalised P&L if no entry prices
        if not returns and pnls:
            abs_pnls = [abs(p) for p in pnls if p != 0]
            avg_abs = sum(abs_pnls) / len(abs_pnls) if abs_pnls else 1.0
            returns = [p / avg_abs for p in pnls]

        # ── Sharpe ratio ──────────────────────────────────────────────────────
        sharpe: Optional[float] = None
        if len(returns) >= MIN_TRADES_FOR_SHARPE:
            n = len(returns)
            mean_r = sum(returns) / n
            variance = sum((r - mean_r) ** 2 for r in returns) / (n - 1) if n > 1 else 0.0
            std_r = math.sqrt(variance) if variance > 0 else 0.0

            if std_r > 0:
                # Annualize: scale by sqrt(252). Each trade is treated as ~1 event.
                # More accurate: spread by holding_days.
                avg_holding_days = sum(
                    t.get("holding_days", 1) for t in trades
                ) / max(len(trades), 1)
                if avg_holding_days <= 0:
                    avg_holding_days = 1
                # Scale from per-trade to per-day then annualize
                annual_scale = math.sqrt(252.0 / avg_holding_days)
                sharpe = round((mean_r / std_r) * annual_scale, 4)
            else:
                # Zero std → either all wins or all losses
                sharpe = 10.0 if mean_r > 0 else (-10.0 if mean_r < 0 else 0.0)

        # ── Max drawdown from equity curve ────────────────────────────────────
        max_drawdown: Optional[float] = None
        if pnls:
            equity = 0.0
            peak = 0.0
            max_dd = 0.0
            for p in pnls:
                equity += p
                if equity > peak:
                    peak = equity
                dd = (equity - peak) / abs(peak) if peak != 0 else 0.0
                if dd < max_dd:
                    max_dd = dd
            max_drawdown = round(max_dd, 4)

        # ── Assign status ─────────────────────────────────────────────────────
        # (status is finalized in compare_to_backtest; here we return raw metrics)
        status = HEALTHY  # placeholder; real status set by compare_to_backtest

        return LiveMetrics(
            strategy=strategy,
            trade_count=trade_count,
            status=status,
            window_days=window_days,
            sharpe=sharpe,
            win_rate=round(win_rate, 4),
            avg_r=round(avg_r, 4) if avg_r is not None else None,
            max_drawdown=max_drawdown,
            period_start=cutoff.isoformat(timespec="seconds"),
            period_end=now_str,
        )

    # ── Backtest comparison ────────────────────────────────────────────────────

    def _load_backtest_metrics(self, strategy: str) -> Optional[dict]:
        """Load expected backtest metrics from research/best/{strategy}.json."""
        best_path = PROJECT / "research" / "best" / f"{strategy}.json"
        if not best_path.exists():
            logger.debug("No best-results file for strategy %s", strategy)
            return None
        try:
            with open(best_path) as fh:
                data = json.load(fh)
            return data.get("metrics", {})
        except Exception as exc:
            logger.warning("Failed to load backtest metrics for %s: %s", strategy, exc)
            return None

    def compare_to_backtest(self, strategy: str) -> HealthAssessment:
        """Compare live 60-day performance against backtest benchmark.

        Status determination:
          INSUFFICIENT_DATA  — fewer than 10 completed live trades
          HEALTHY            — live Sharpe > 50% of backtest Sharpe
          WARNING            — live Sharpe ≤ 50% of backtest Sharpe (but ≥ 0)
          DEGRADED           — live Sharpe < 0 (checked for consecutive weeks by caller)

        Args:
            strategy: Strategy name.

        Returns:
            HealthAssessment dataclass.
        """
        live = self.compute_live_metrics(strategy, window_days=60)
        bt_metrics = self._load_backtest_metrics(strategy)
        backtest_sharpe: Optional[float] = None
        backtest_win_rate: Optional[float] = None

        if bt_metrics:
            raw_sharpe = bt_metrics.get("sharpe")
            if raw_sharpe is not None:
                try:
                    backtest_sharpe = float(raw_sharpe)
                except (TypeError, ValueError):
                    pass
            raw_wr = bt_metrics.get("win_rate_pct")
            if raw_wr is not None:
                try:
                    backtest_win_rate = float(raw_wr) / 100.0  # convert % to fraction
                except (TypeError, ValueError):
                    pass

        now_str = datetime.now().isoformat(timespec="seconds")

        if live.status == INSUFFICIENT_DATA:
            msg = (
                f"Insufficient live data ({live.trade_count} completed trades, "
                f"need {MIN_TRADES_FOR_METRICS})"
            )
            return HealthAssessment(
                strategy=strategy,
                status=INSUFFICIENT_DATA,
                live_sharpe=None,
                backtest_sharpe=backtest_sharpe,
                sharpe_ratio=None,
                live_win_rate=live.win_rate,
                backtest_win_rate=backtest_win_rate,
                live_trade_count=live.trade_count,
                message=msg,
                assessed_at=now_str,
            )

        live_sharpe = live.sharpe
        sharpe_ratio: Optional[float] = None
        status: str
        msg: str

        # Compute Sharpe ratio (live / backtest) when both available and backtest > 0
        if live_sharpe is not None and backtest_sharpe and backtest_sharpe > 0:
            sharpe_ratio = round(live_sharpe / backtest_sharpe, 4)

        # Determine status
        if live_sharpe is None:
            # Couldn't compute Sharpe but have enough trades → insufficient
            status = INSUFFICIENT_DATA
            msg = f"Live Sharpe unavailable ({live.trade_count} trades, need {MIN_TRADES_FOR_SHARPE} for Sharpe)"
        elif live_sharpe < 0:
            # Negative Sharpe → DEGRADED (consecutive check done in full_health_report)
            status = DEGRADED
            msg = (
                f"Live Sharpe {live_sharpe:.3f} is negative"
                + (f" (backtest: {backtest_sharpe:.3f})" if backtest_sharpe else "")
            )
        elif backtest_sharpe and backtest_sharpe > 0 and live_sharpe < SHARPE_HEALTHY_RATIO * backtest_sharpe:
            status = WARNING
            msg = (
                f"Live Sharpe {live_sharpe:.3f} < {SHARPE_HEALTHY_RATIO:.0%} of "
                f"backtest Sharpe {backtest_sharpe:.3f}"
            )
        else:
            status = HEALTHY
            if backtest_sharpe:
                msg = (
                    f"Live Sharpe {live_sharpe:.3f} vs backtest {backtest_sharpe:.3f} "
                    f"(ratio {sharpe_ratio:.2f})"
                )
            else:
                msg = f"Live Sharpe {live_sharpe:.3f} (no backtest benchmark available)"

        return HealthAssessment(
            strategy=strategy,
            status=status,
            live_sharpe=live_sharpe,
            backtest_sharpe=backtest_sharpe,
            sharpe_ratio=sharpe_ratio,
            live_win_rate=live.win_rate,
            backtest_win_rate=backtest_win_rate,
            live_trade_count=live.trade_count,
            message=msg,
            assessed_at=now_str,
        )

    # ── Consecutive degradation check ─────────────────────────────────────────

    def _count_consecutive_degraded_weeks(self, strategy: str) -> int:
        """Count how many consecutive recent weekly reports show DEGRADED for this strategy.

        Scans logs/health_reports/ for reports ordered newest-first and counts
        the unbroken streak of DEGRADED status at the top.
        """
        reports_dir = PROJECT / "logs" / "health_reports"
        if not reports_dir.exists():
            return 0

        report_files = sorted(reports_dir.glob(f"health_{self.market_id}_*.json"), reverse=True)
        if not report_files:
            return 0

        consecutive = 0
        for report_file in report_files:
            try:
                with open(report_file) as fh:
                    report_data = json.load(fh)
                assessments = report_data.get("assessments", [])
                # Find this strategy's assessment
                for a in assessments:
                    if a.get("strategy") == strategy:
                        if a.get("status") == DEGRADED:
                            consecutive += 1
                        else:
                            # Streak broken
                            return consecutive
                        break
                else:
                    # Strategy not in this report — stop counting
                    return consecutive
            except Exception as exc:
                logger.debug("Could not read report %s: %s", report_file, exc)
                return consecutive

        return consecutive

    # ── Full report ────────────────────────────────────────────────────────────

    def full_health_report(self, market_id: str) -> HealthReport:
        """Run a full health assessment for all enabled strategies.

        Args:
            market_id: Market to assess (used to find strategies in config).

        Returns:
            HealthReport with per-strategy assessments and alerts.
        """
        strategies_cfg = self.config.get("strategies", {})
        enabled_strategies = [
            name for name, cfg in strategies_cfg.items()
            if isinstance(cfg, dict) and cfg.get("enabled", False)
        ]

        if not enabled_strategies:
            logger.warning("No enabled strategies found in config for %s", market_id)

        assessments: List[HealthAssessment] = []
        alerts: List[Alert] = []
        now_str = datetime.now().isoformat(timespec="seconds")

        for strategy in enabled_strategies:
            assessment = self.compare_to_backtest(strategy)

            # Check for escalation to 3+ consecutive DEGRADED weeks
            if assessment.status == DEGRADED:
                consec = self._count_consecutive_degraded_weeks(strategy)
                # +1 for the current check
                total_consec = consec + 1
                if total_consec >= DEGRADED_CONSECUTIVE_WEEKS:
                    alerts.append(Alert(
                        strategy=strategy,
                        status=DEGRADED,
                        message=(
                            f"{strategy} has been DEGRADED for {total_consec} "
                            f"consecutive weekly checks — immediate review needed"
                        ),
                        consecutive_degraded_weeks=total_consec,
                        timestamp=now_str,
                        market_id=market_id,
                    ))
            elif assessment.status == WARNING:
                alerts.append(Alert(
                    strategy=strategy,
                    status=WARNING,
                    message=(
                        f"{strategy} is underperforming vs backtest: "
                        f"live Sharpe {assessment.live_sharpe} vs "
                        f"backtest {assessment.backtest_sharpe}"
                    ),
                    consecutive_degraded_weeks=0,
                    timestamp=now_str,
                    market_id=market_id,
                ))

            assessments.append(assessment)

        # Build summary counts
        summary: Dict[str, int] = {
            HEALTHY: 0,
            WARNING: 0,
            DEGRADED: 0,
            INSUFFICIENT_DATA: 0,
        }
        for a in assessments:
            summary[a.status] = summary.get(a.status, 0) + 1

        return HealthReport(
            market_id=market_id,
            generated_at=now_str,
            assessments=assessments,
            alerts=alerts,
            summary=summary,
        )

    def check_degradation_alerts(self, market_id: str) -> List[Alert]:
        """Return only DEGRADED and WARNING alerts for the market.

        Convenience wrapper around full_health_report that returns only
        the alerts (not the full report).
        """
        report = self.full_health_report(market_id)
        return report.alerts
