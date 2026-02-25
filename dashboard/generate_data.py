#!/usr/bin/env python3
"""Generate dashboard-data.json for Atlas static dashboard.

Produces a JSON payload consumed by the single-page dashboard.
Includes portfolio state, today's plan, backtest metrics, and task tracker.
"""

import json
import re
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import pandas as pd

BRISBANE = ZoneInfo("Australia/Brisbane")
PROJECT_ROOT = Path(__file__).resolve().parent.parent
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
    state = safe_json(PROJECT_ROOT / "paper_engine" / "portfolio_state.json", None)
    seq = config.get("risk", {}).get("starting_equity", 5000)
    if state is None:
        return {"cash": seq, "positions": [], "closed_trades": [],
                "equity_history": [], "halted": False, "starting_equity": seq}
    state["starting_equity"] = seq
    return state


def get_latest_plan():
    plans_dir = PROJECT_ROOT / "paper_engine" / "plans"
    if not plans_dir.exists():
        return None
    files = sorted(plans_dir.glob("plan_*.json"), reverse=True)
    return safe_json(files[0], None) if files else None


def get_prices(tickers):
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
                        }
                except Exception:
                    pass
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
    positions = portfolio.get("positions", [])
    cash = portfolio.get("cash", seq)
    fees_cfg = config.get("fees", {})
    commission = fees_cfg.get("commission_per_trade", 3.0)

    # Collect tickers needing prices
    tickers = {p.get("ticker", "") for p in positions}
    if plan:
        for e in plan.get("proposed_entries", []):
            tickers.add(e.get("ticker", ""))
    tickers.discard("")
    prices = get_prices(tickers)

    # Equity calculation
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

    # P&L breakdown
    total_entry_value = sum(p.get("entry_value", 0) for p in positions)
    total_commissions = round(len(positions) * commission, 2)
    # Market P&L = current value - entry value
    market_pnl = round(pos_value - total_entry_value, 2)
    # Realized P&L from closed trades
    closed = portfolio.get("closed_trades", []) or ledger or []
    realized_pnl = round(sum(t.get("pnl", 0) for t in closed), 2)

    # Open positions
    now = datetime.now(BRISBANE)
    open_pos = []
    strategy_stats = {}
    for p in positions:
        t = p.get("ticker", "")
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

        strat = p.get("strategy", "unknown")
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
    eq_hist = portfolio.get("equity_history", [])
    eq_curve = [{"date": e.get("date", ""), "equity": e.get("equity", seq)}
                for e in eq_hist] if eq_hist else []

    # Backtest data
    backtest = get_backtest_data()

    # Risk
    risk_cfg = config.get("risk", {})
    invested = sum(p.get("entry_value", 0) for p in positions)
    exposure_pct = round(invested / equity * 100, 1) if equity > 0 else 0

    # Tasks
    tasks = parse_tasks()

    # Assemble
    result = {
        "timestamp": now.isoformat(),
        "config_version": config.get("version", "unknown"),
        "project": config.get("project", "Atlas"),
        "portfolio": {
            "equity": equity, "cash": round(cash, 2),
            "starting_equity": seq,
            "total_pnl": total_pnl, "total_pnl_pct": total_pnl_pct,
            "num_open": len(positions), "win_rate": win_rate,
            "commission_per_trade": commission,
            "total_commissions": total_commissions,
            "market_pnl": market_pnl,
            "realized_pnl": realized_pnl,
            "open_positions": open_pos,
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
    print(f"  Config version : {result['config_version']}")
    print(f"  Project        : {result['project']}")
    print(f"  Equity         : ${equity:,.2f}")
    print(f"  Cash           : ${cash:,.2f}")
    print(f"  Open positions : {len(open_pos)}")
    print(f"  Closed trades  : {len(closed)}")
    print(f"  Backtest pts   : {len(backtest['equity_curve'])}")
    print(f"  Tasks          : {len(tasks['upcoming'])} upcoming, {len(tasks['in_progress'])} active, {len(tasks['done'])} done")


if __name__ == "__main__":
    generate()
