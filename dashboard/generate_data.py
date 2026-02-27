#!/usr/bin/env python3
"""Generate dashboard-data.json for Atlas static dashboard.

Produces a JSON payload consumed by the single-page dashboard.
Includes portfolio state, today's plan, backtest metrics, and task tracker.

When trading.mode == "live" and broker == "moomoo", equity/cash/positions
are fetched from the live Moomoo account. Paper state is used for metadata
(strategy, entry_date, stop_price, confidence, rationale) that the broker
doesn't track.
"""

import json
import logging
import re
import sys
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import pandas as pd

logger = logging.getLogger("atlas.dashboard")
BRISBANE = ZoneInfo("Australia/Brisbane")
PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))
OUTPUT = PROJECT_ROOT / "dashboard" / "data" / "dashboard-data.json"


def safe_json(path, default=None):
    try:
        with open(path) as f:
            return json.load(f)
    except Exception:
        return default if default is not None else {}


def get_config():
    return safe_json(PROJECT_ROOT / "config" / "active" / "asx.json", {})


def get_portfolio(config):
    # Load from per-market state file first, fall back to legacy
    market_id = config.get("market", "asx")
    per_market = PROJECT_ROOT / "paper_engine" / "state" / f"{market_id}.json"
    legacy = PROJECT_ROOT / "paper_engine" / "portfolio_state.json"

    state = None
    if per_market.exists():
        state = safe_json(per_market, None)
    if state is None:
        state = safe_json(legacy, None)

    seq = config.get("risk", {}).get("starting_equity", 5000)
    if state is None:
        return {"cash": seq, "positions": [], "closed_trades": [],
                "equity_history": [], "halted": False, "starting_equity": seq}
    state["starting_equity"] = seq
    return state


def get_live_broker_data(config):
    """Fetch account info and positions from Moomoo broker.

    Returns (account_info_dict, positions_list, connected) or (None, [], False)
    on failure. Enriches broker positions with paper-state metadata
    (strategy, entry_date, stop_price, confidence, sector).
    """
    trading = config.get("trading", {})
    if trading.get("broker") != "moomoo" or not trading.get("live_enabled"):
        return None, [], False

    try:
        from brokers.moomoo.broker import MomooBroker

        broker = MomooBroker(config, live=True)
        if not broker.connect():
            logger.warning("Dashboard: broker connect failed")
            return None, [], False

        try:
            acct = broker.get_account_info()
            positions = broker.get_positions()
        finally:
            broker.disconnect()

        if not acct:
            return None, [], False

        # Build account dict
        acct_data = {
            "equity": round(acct.equity, 2),
            "cash": round(acct.cash, 2),
            "market_value": round(acct.market_value, 2),
            "buying_power": round(acct.buying_power, 2),
            "total_pnl": round(acct.total_pnl, 2),
            "total_pnl_pct": round(acct.total_pnl_pct, 2),
            "num_positions": acct.num_positions,
            "currency": acct.currency,
        }

        # Build positions list — enrich with paper-state metadata
        paper_state = get_portfolio(config)
        paper_by_ticker = {}
        for p in paper_state.get("positions", []):
            paper_by_ticker[p.get("ticker", "")] = p

        pos_list = []
        for pos in positions:
            # Skip non-Atlas positions (e.g. manually held WDS, XOP)
            paper = paper_by_ticker.get(pos.ticker)

            pos_dict = {
                "ticker": pos.ticker,
                "entry_price": round(pos.entry_price, 4),
                "shares": pos.shares,
                "current_price": round(pos.current_price, 4),
                "market_value": round(pos.market_value, 2),
                "unrealized_pnl": round(pos.unrealized_pnl, 2),
                "unrealized_pnl_pct": round(pos.unrealized_pnl_pct, 2),
                "cost_basis": round(pos.cost_basis, 2),
                # Metadata from paper state (broker doesn't track these)
                "strategy": paper.get("strategy", "") if paper else "",
                "entry_date": paper.get("entry_date", "") if paper else "",
                "stop_price": paper.get("stop_price", 0) if paper else 0,
                "confidence": paper.get("confidence", 0) if paper else 0,
                "sector": paper.get("sector", "Unknown") if paper else pos.sector,
                "entry_value": paper.get("entry_value", pos.cost_basis) if paper else pos.cost_basis,
                "is_atlas": paper is not None,
            }
            pos_list.append(pos_dict)

        logger.info("Dashboard: broker data OK — equity=$%.2f, %d positions",
                     acct_data["equity"], len(pos_list))
        return acct_data, pos_list, True

    except Exception as e:
        logger.error("Dashboard: broker fetch failed: %s", e, exc_info=True)
        return None, [], False


def get_latest_plan():
    plans_dir = PROJECT_ROOT / "paper_engine" / "plans"
    if not plans_dir.exists():
        return None
    files = sorted(plans_dir.glob("plan_*.json"), reverse=True)
    return safe_json(files[0], None) if files else None


def get_live_prices(tickers):
    """Fetch live intraday prices via yfinance.

    Uses 15m interval for the current day. Falls back gracefully
    if market is closed or data unavailable.

    Returns dict of ticker -> {"close": float, "prev_close": float|None, "date": str, "live": bool}
    """
    prices = {}
    if not tickers:
        return prices

    ticker_list = list(tickers)
    try:
        import yfinance as yf
        # Batch download — single HTTP call for all tickers
        data = yf.download(ticker_list, period="2d", interval="15m",
                           progress=False, threads=True)
        if data.empty:
            return prices

        for t in ticker_list:
            try:
                if len(ticker_list) > 1:
                    series = data["Close"][t].dropna()
                else:
                    series = data["Close"].dropna()
                if len(series) == 0:
                    continue
                last_price = float(series.iloc[-1])
                prev_price = float(series.iloc[-2]) if len(series) > 1 else None
                last_ts = series.index[-1]
                prices[t] = {
                    "close": last_price,
                    "prev_close": prev_price,
                    "date": str(last_ts),
                    "live": True,
                }
            except Exception:
                pass
    except Exception as e:
        print(f"  WARN: live price fetch failed: {e}")

    return prices


def get_cache_prices(tickers):
    """Load prices from parquet cache (daily close data)."""
    prices = {}
    for subdir in ["asx", "sp500", ""]:
        cache = PROJECT_ROOT / "data" / "cache" / subdir if subdir else PROJECT_ROOT / "data" / "cache"
        if not cache.exists():
            continue
        for t in tickers:
            if t in prices:
                continue
            fp = cache / (t.replace(".", "_") + ".parquet")
            if fp.exists():
                try:
                    df = pd.read_parquet(fp)
                    if len(df) > 0:
                        prices[t] = {
                            "close": float(df["close"].iloc[-1]),
                            "prev_close": float(df["close"].iloc[-2]) if len(df) > 1 else None,
                            "date": str(df.index[-1].date()),
                            "live": False,
                        }
                except Exception:
                    pass
    return prices


def get_prices(tickers):
    """Get latest prices — live intraday first, cache fallback.

    During market hours: returns live 15-min delayed prices.
    Outside market hours: returns last daily close from cache.
    """
    if not tickers:
        return {}

    # Try live prices first
    prices = get_live_prices(tickers)

    # Fill any missing tickers from cache
    missing = tickers - set(prices.keys())
    if missing:
        cache_prices = get_cache_prices(missing)
        prices.update(cache_prices)

    return prices


def get_backtest_data():
    """Load backtest equity curve and metrics."""
    bt_curve_path = PROJECT_ROOT / "backtest" / "results" / "backtest_equity_curve.json"
    bt_report_path = PROJECT_ROOT / "backtest" / "results" / "phase5_report.json"

    curve_data = safe_json(bt_curve_path, None)
    report = safe_json(bt_report_path, {})

    result = {"equity_curve": [], "metrics": {}, "trade_markers": []}

    if curve_data:
        result["equity_curve"] = curve_data.get("equity_curve", [])
        result["metrics"] = curve_data.get("metrics", {})
        result["trade_markers"] = curve_data.get("trade_markers", [])

    # Merge final_metrics from phase5 report if available
    final = report.get("final_metrics", {})
    if final:
        result["report_metrics"] = final

    return result


def parse_tasks():
    """Parse tasks/todo.md into structured task lists."""
    todo_path = PROJECT_ROOT / "tasks" / "todo.md"
    if not todo_path.exists():
        return {"upcoming": [], "in_progress": [], "done": []}

    text = todo_path.read_text()
    tasks = {"upcoming": [], "in_progress": [], "done": []}
    current_section = None

    for line in text.splitlines():
        stripped = line.strip()

        lower = stripped.lower()
        if lower.startswith("## upcoming") or lower.startswith("## todo"):
            current_section = "upcoming"
            continue
        elif lower.startswith("## in progress") or lower.startswith("## active") or lower.startswith("## current"):
            current_section = "in_progress"
            continue
        elif lower.startswith("## done") or lower.startswith("## completed") or lower.startswith("## finished"):
            current_section = "done"
            continue
        elif stripped.startswith("## "):
            current_section = None
            continue

        if current_section is None:
            continue

        m = re.match(r'^-\s*\[(.)\]\s*(.+)$', stripped)
        if m:
            text_val = m.group(2).strip()
        elif stripped.startswith("- "):
            text_val = stripped[2:].strip()
        else:
            continue

        if text_val:
            tasks[current_section].append(text_val)

    return tasks


def generate():
    config = get_config()
    portfolio = get_portfolio(config)
    plan = get_latest_plan()
    ledger = safe_json(PROJECT_ROOT / "journal" / "trade_ledger.json", [])

    seq = portfolio.get("starting_equity", 5000)
    fees_cfg = config.get("fees", {})
    commission = fees_cfg.get("commission_per_trade", 3.0)

    trading = config.get("trading", {})
    is_live_mode = (trading.get("mode") == "live"
                    and trading.get("broker") == "moomoo"
                    and trading.get("live_enabled", False))

    # ── Try live broker data first ──────────────────────────────
    broker_acct, broker_positions, broker_ok = None, [], False
    if is_live_mode:
        broker_acct, broker_positions, broker_ok = get_live_broker_data(config)

    if broker_ok and broker_acct:
        # Live mode: equity/cash from broker
        account_equity = broker_acct["equity"]
        cash = broker_acct["cash"]
        positions = broker_positions
        atlas_positions = [p for p in positions if p.get("is_atlas", True)]
        manual_positions = [p for p in positions if not p.get("is_atlas", True)]
        all_positions = positions  # includes non-Atlas (WDS, XOP etc)
        data_source = "moomoo"

        # Atlas P&L: only from Atlas-managed positions
        total_entry_value = sum(p.get("entry_value", 0) for p in atlas_positions)
        atlas_value = sum(p.get("market_value", 0) for p in atlas_positions)
        market_pnl = round(atlas_value - total_entry_value, 2)
        total_commissions = round(len(atlas_positions) * commission, 2)

        # Manual positions value (not managed by Atlas)
        manual_value = sum(p.get("market_value", 0) for p in manual_positions)

        # Equity = full account value (shown in header)
        equity = account_equity
        # Atlas P&L = unrealised on Atlas positions only
        total_pnl = round(sum(p.get("unrealized_pnl", 0) for p in atlas_positions)
                          - total_commissions, 2)
        total_pnl_pct = round(total_pnl / seq * 100, 2) if seq > 0 else 0
        pos_value = broker_acct["market_value"]
    else:
        # Paper/fallback mode
        positions = portfolio.get("positions", [])
        atlas_positions = positions
        all_positions = positions
        data_source = "paper"
        cash = portfolio.get("cash", seq)

        # Collect tickers needing prices
        tickers = {p.get("ticker", "") for p in positions}
        if plan:
            for e in plan.get("proposed_entries", []):
                tickers.add(e.get("ticker", ""))
        tickers.discard("")
        prices = get_prices(tickers)

        pos_value = 0
        for p in positions:
            t = p.get("ticker", "")
            if t in prices:
                pos_value += prices[t]["close"] * p.get("shares", 0)
            else:
                pos_value += p.get("entry_value", 0)
        equity = round(cash + pos_value, 2)
        total_pnl = round(equity - seq, 2)
        total_pnl_pct = round(total_pnl / seq * 100, 2) if seq > 0 else 0

        total_entry_value = sum(p.get("entry_value", 0) for p in positions)
        total_commissions = round(len(positions) * commission, 2)
        market_pnl = round(pos_value - total_entry_value, 2)

    # Realized P&L from closed trades
    closed = portfolio.get("closed_trades", []) or ledger or []
    realized_pnl = round(sum(t.get("pnl", 0) for t in closed), 2)

    # ── Open positions ──────────────────────────────────────────
    now = datetime.now(BRISBANE)
    open_pos = []
    strategy_stats = {}

    for p in all_positions:
        t = p.get("ticker", "")
        is_atlas = p.get("is_atlas", True)

        if broker_ok:
            # Broker already provides current_price and unrealized_pnl
            ep = p.get("entry_price", 0)
            sh = p.get("shares", 0)
            cp = p.get("current_price", ep)
            upnl = p.get("unrealized_pnl", round((cp - ep) * sh, 2))
            upnl_pct = p.get("unrealized_pnl_pct", round((cp - ep) / ep * 100, 2) if ep > 0 else 0)
        else:
            ep = p.get("entry_price", 0)
            sh = p.get("shares", 0)
            cp = prices[t]["close"] if t in prices else ep
            upnl = round((cp - ep) * sh, 2)
            upnl_pct = round((cp - ep) / ep * 100, 2) if ep > 0 else 0

        ed = p.get("entry_date", "")
        dh = 0
        if ed:
            try:
                entry_dt = datetime.strptime(ed, "%Y-%m-%d").replace(tzinfo=BRISBANE)
                dh = (now - entry_dt).days
            except Exception:
                pass

        strat = p.get("strategy", "") or ("manual" if not is_atlas else "unknown")
        # Only Atlas positions in strategy breakdown
        if is_atlas:
            if strat not in strategy_stats:
                strategy_stats[strat] = {"count": 0, "pnl": 0, "value": 0}
            strategy_stats[strat]["count"] += 1
            strategy_stats[strat]["pnl"] += upnl
            strategy_stats[strat]["value"] += cp * sh

        open_pos.append({
            "ticker": t, "strategy": strat,
            "entry_date": ed, "entry_price": ep, "current_price": round(cp, 4),
            "shares": sh, "pnl": upnl, "pnl_pct": upnl_pct,
            "stop_price": p.get("stop_price", 0),
            "days_held": dh, "sector": p.get("sector", ""),
            "is_atlas": is_atlas,
        })

    # Strategy performance summary
    strat_summary = []
    for s, data in sorted(strategy_stats.items()):
        strat_summary.append({
            "strategy": s,
            "positions": data["count"],
            "unrealized_pnl": round(data["pnl"], 2),
            "market_value": round(data["value"], 2),
        })

    # Plan summary
    plan_data = None
    if plan:
        plan_data = {
            "trade_date": plan.get("trade_date", ""),
            "status": plan.get("status", "UNKNOWN"),
            "entries": plan.get("proposed_entries", []),
            "exits": plan.get("proposed_exits", []),
        }

    # Closed trade stats
    wins = sum(1 for t in closed if t.get("pnl", 0) > 0)
    win_rate = round(wins / len(closed) * 100, 1) if closed else 0

    # Equity curve (live paper trading)
    # Equity curve — use persistent live curve file when in live mode
    live_curve_path = PROJECT_ROOT / "logs" / "live_equity_curve.json"

    if broker_ok:
        # Live mode: maintain a persistent equity curve file
        eq_curve = safe_json(live_curve_path, [])
        if not isinstance(eq_curve, list):
            eq_curve = []
    else:
        eq_hist = portfolio.get("equity_history", [])
        eq_curve = [{"date": e.get("date", ""), "equity": e.get("equity", seq)}
                    for e in eq_hist] if eq_hist else []

    # Append today's mark-to-market if not already present
    today_str = now.strftime("%Y-%m-%d")
    if not eq_curve or eq_curve[-1].get("date") != today_str:
        eq_curve.append({"date": today_str, "equity": round(equity, 2)})
    elif eq_curve and eq_curve[-1].get("date") == today_str:
        # Update today's equity with latest value
        eq_curve[-1]["equity"] = round(equity, 2)

    # Persist live curve
    if broker_ok:
        with open(live_curve_path, "w") as f:
            json.dump(eq_curve, f, indent=2)

    # Backtest data
    backtest = get_backtest_data()

    # Risk
    risk_cfg = config.get("risk", {})
    invested = sum(p.get("entry_value", 0) for p in positions)
    exposure_pct = round(invested / equity * 100, 1) if equity > 0 else 0

    # Tasks
    tasks = parse_tasks()

    # Trading mode info
    dry_run = trading.get("live_safety", {}).get("dry_run_first", True)
    if is_live_mode and not dry_run:
        mode_label = "live"
    elif is_live_mode and dry_run:
        mode_label = "live_dry_run"
    else:
        mode_label = "paper"

    # Split positions into Atlas-managed and manual
    atlas_open = [p for p in open_pos if p.get("is_atlas", True)]
    manual_open = [p for p in open_pos if not p.get("is_atlas", True)]

    # Manual positions P&L
    manual_pnl = round(sum(p.get("pnl", 0) for p in manual_open), 2)
    manual_value = round(sum(p.get("current_price", 0) * p.get("shares", 0) for p in manual_open), 2)

    # Assemble
    result = {
        "timestamp": now.isoformat(),
        "config_version": config.get("version", "unknown"),
        "project": config.get("project", "Atlas"),
        "trading_mode": mode_label,
        "data_source": data_source if broker_ok else "paper",
        "broker": trading.get("broker", "paper"),
        "portfolio": {
            "equity": equity, "cash": round(cash, 2),
            "starting_equity": seq,
            "total_pnl": total_pnl, "total_pnl_pct": total_pnl_pct,
            "num_open": len(atlas_open), "num_atlas": len(atlas_open),
            "win_rate": win_rate,
            "commission_per_trade": commission,
            "total_commissions": total_commissions,
            "market_pnl": market_pnl,
            "realized_pnl": realized_pnl,
            "open_positions": atlas_open,
            "buying_power": broker_acct["buying_power"] if broker_ok else round(cash, 2),
        },
        "manual_positions": {
            "positions": manual_open,
            "num_open": len(manual_open),
            "unrealized_pnl": manual_pnl,
            "market_value": manual_value,
        },
        "strategy_summary": strat_summary,
        "equity_curve": eq_curve,
        "backtest": {
            "equity_curve": backtest["equity_curve"],
            "metrics": backtest.get("metrics", {}),
            "report_metrics": backtest.get("report_metrics", {}),
        },
        "plan": plan_data,
        "closed_trades": closed,
        "risk": {
            "exposure_pct": exposure_pct,
            "max_positions": risk_cfg.get("max_open_positions", 10),
            "halted": portfolio.get("halted", False),
            "risk_per_trade": risk_cfg.get("risk_per_trade", 0.005),
            "max_portfolio_risk": risk_cfg.get("max_portfolio_risk", 0.05),
        },
        "tasks": tasks,
    }

    OUTPUT.parent.mkdir(parents=True, exist_ok=True)
    with open(OUTPUT, "w") as f:
        json.dump(result, f, indent=2, default=str)

    print(f"Dashboard data written to {OUTPUT}")
    print(f"  Mode           : {mode_label} (source: {result['data_source']})")
    print(f"  Config version : {result['config_version']}")
    print(f"  Equity         : ${equity:,.2f}")
    print(f"  Cash           : ${cash:,.2f}")
    print(f"  Open positions : {len(open_pos)} ({len(atlas_positions)} Atlas)")
    print(f"  Closed trades  : {len(closed)}")
    print(f"  Backtest pts   : {len(backtest['equity_curve'])}")


if __name__ == "__main__":
    generate()
