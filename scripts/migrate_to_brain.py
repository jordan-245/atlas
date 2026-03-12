#!/usr/bin/env python3
"""One-time migration: seed research/brain/ from existing data.

Sources:
  - research/best/*.json → brain/strategies/, brain/state.json
  - research/journal.json → brain/experiments/
  - research/vault/Patterns/ → brain/patterns/
  - KNOWLEDGE_BASE.md closed decisions → brain/decisions/
  - KNOWLEDGE_BASE.md confirmed patterns → brain/patterns/ (supplement)
"""
import json
import sys
from pathlib import Path

ATLAS_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ATLAS_ROOT))

from research.brain.writer import (
    BRAIN_ROOT, _atomic_write, _now_iso,
    update_strategy, rebuild_all_indexes,
    save_state,
)

BEST_DIR = ATLAS_ROOT / "research" / "best"
JOURNAL_PATH = ATLAS_ROOT / "research" / "journal.json"
VAULT_DIR = ATLAS_ROOT / "research" / "vault"

# ─── Strategy status classification ─────────────────────────────────────────

ACTIVE = {"mean_reversion", "trend_following", "opening_gap"}
PROMISING = {
    "momentum_breakout", "consecutive_down_days", "adx_trend_pullback",
    "short_term_mr", "bb_squeeze", "connors_rsi2", "lower_band_reversion",
}

def _classify(name: str, sharpe: float, trades: int) -> str:
    if name in ACTIVE:
        return "active"
    if trades == 0 or sharpe == 0:
        return "untested"
    if sharpe < -2 or trades < 10:
        return "failed"
    if name in PROMISING and sharpe > 0.2:
        return "promising"
    return "dormant"


def migrate_strategies():
    """Seed brain/strategies/ from best/*.json."""
    print("Migrating strategies...")
    state_strategies = {}

    for f in sorted(BEST_DIR.glob("*.json")):
        data = json.loads(f.read_text())
        name = data.get("strategy", f.stem)
        params = data.get("params", {})
        metrics = data.get("metrics", {})
        sharpe = metrics.get("sharpe", 0)
        trades = metrics.get("total_trades", 0)
        status = _classify(name, sharpe, trades)

        update_strategy(
            strategy=name,
            metrics=metrics,
            params=params,
            status=status,
            description=f"migrated from best/{f.name}",
        )

        state_strategies[name] = {
            "status": status,
            "sharpe": sharpe,
            "trades": trades,
            "params": params,
        }
        print(f"  {name}: {status}, sharpe={sharpe:.4f}, trades={trades}")

    return state_strategies


def migrate_experiments():
    """Seed brain/experiments/ from journal.json (last 100 entries)."""
    print("Migrating experiments...")
    if not JOURNAL_PATH.exists():
        print("  No journal.json found")
        return

    journal = json.loads(JOURNAL_PATH.read_text())
    recent = journal[-100:] if len(journal) > 100 else journal

    for entry in recent:
        exp_id = entry.get("experiment_id", "unknown")
        strategy = entry.get("strategy", "unknown")
        verdict = entry.get("verdict", "unknown")
        metrics = entry.get("key_metrics", {})
        hypothesis = entry.get("hypothesis", "")
        ts = entry.get("timestamp", "")

        kept = verdict in ("pass", "promoted")
        path = BRAIN_ROOT / "experiments" / f"{exp_id}.md"

        content = f"""# {exp_id}

> **Strategy:** {strategy} | **Status:** {"kept" if kept else "discarded"} | **{ts}**

## Change
- **Parameter:** {hypothesis}
- **Sharpe Δ:** n/a (migrated)

## Metrics
| Metric | Value |
|--------|-------|
| Sharpe | {metrics.get('sharpe', 0):.4f} |
| CAGR | {metrics.get('cagr_pct', 0):.1f}% |
| Profit Factor | {metrics.get('profit_factor', 0):.2f} |
| Max Drawdown | {metrics.get('max_drawdown_pct', 0):.1f}% |
| Total Trades | {metrics.get('total_trades', 0)} |
"""
        _atomic_write(path, content)

    print(f"  Migrated {len(recent)} experiments")


def migrate_patterns():
    """Seed brain/patterns/ from vault/Patterns/."""
    print("Migrating patterns...")
    src = VAULT_DIR / "Patterns"
    if not src.exists():
        print("  No vault/Patterns/ found")
        return

    for f in sorted(src.glob("*.md")):
        # Clean the filename to snake_case
        name = f.stem.lower().replace(" ", "_").replace("-", "_")
        dst = BRAIN_ROOT / "patterns" / f"{name}.md"

        # Simplify: keep content but strip YAML frontmatter
        text = f.read_text()
        lines = text.splitlines()
        if lines and lines[0].strip() == "---":
            end = next((i for i, l in enumerate(lines[1:], 1) if l.strip() == "---"), 0)
            lines = lines[end + 1:]
        content = "\n".join(lines).strip() + "\n"

        _atomic_write(dst, content)
        print(f"  {f.stem} → {name}.md")


def migrate_decisions():
    """Seed brain/decisions/ from KNOWLEDGE_BASE.md closed decisions."""
    print("Migrating decisions...")
    decisions = [
        ("sma200_promoted", "SMA-200 filter ON for all strategies — promoted v2.1. Sharpe +0.28."),
        ("vix_filter_closed", "VIX filter counterproductive for MR-heavy portfolio. High-VIX = best MR signals."),
        ("rsi_period_optimal", "RSI(14) is optimal for MR on SP500. Tested 5-21. Definitive."),
        ("etf_adaptation_fails", "Don't port ETF strategies to individual stocks. ConnorsRSI2 + LBR both fail."),
        ("dormant_activation_closed", "Wave 1 dormant activation CLOSED. All fail combined due to position contention."),
        ("risk_035_promoted", "risk_per_trade=0.35% promoted (was 0.50%). 3/4 OOS tests pass. Never exceed 0.37%."),
    ]

    for name, summary in decisions:
        path = BRAIN_ROOT / "decisions" / f"{name}.md"
        content = f"""# {name.replace('_', ' ').title()}

> {summary}
"""
        _atomic_write(path, content)
        print(f"  {name}")


def migrate_hypotheses():
    """Seed brain/hypotheses/ with known open questions."""
    print("Migrating hypotheses...")
    hypotheses = [
        ("allocation_pools", "Position allocation pools needed before adding more strategies. Contention at max_pos=10 blocks all dormant strategy activation."),
        ("regime_switching", "Activate TF in trending regimes, MR in mean-reverting. Backtest regime switching vs always-on."),
        ("walk_forward_cadence", "How often should params be re-optimized? Monthly? Quarterly? Walk-forward study needed."),
    ]

    for name, summary in hypotheses:
        path = BRAIN_ROOT / "hypotheses" / f"{name}.md"
        content = f"""# {name.replace('_', ' ').title()}

> {summary}
"""
        _atomic_write(path, content)
        print(f"  {name}")


def migrate_regime():
    """Seed brain/regime/ from task #84, #89, #121 findings."""
    print("Migrating regime analysis...")

    content = """# Equity Scaling

> How edge scales with starting capital (Task #89, 2026-03-12).

| Starting Equity | Sharpe | CAGR | MaxDD | Trades | PF | Calmar |
|-----------------|--------|------|-------|--------|----|--------|
| $2,000 | 0.36 | 12.1% | 6.8% | 229 | 2.99 | 1.78 |
| $4,000 | -0.98 | 1.7% | 4.4% | 271 | 1.26 | 0.38 |
| $10,000 | -0.89 | 1.7% | 6.2% | 285 | 1.22 | 0.27 |
| $25,000 | -1.02 | 1.1% | 7.2% | 287 | 1.13 | 0.15 |
| $50,000 | -1.07 | 0.9% | 7.5% | 288 | 1.11 | 0.12 |

Lower equity → fewer trades → only highest-quality entries → better metrics.
"""
    _atomic_write(BRAIN_ROOT / "regime" / "equity_scaling.md", content)

    content = """# Per-Regime Performance

> Bull/neutral/bear strategy breakdown (Task #84, 2026-03-12).

Bull regime has highest profit factor; neutral is marginal; bear destroys edge.
Trend following drives all profit. Mean reversion net negative at all equity levels.

At $4K equity:
| Regime | Trades | WR% | PF | Avg Trade | Sharpe~ |
|--------|--------|-----|----|-----------|---------|
| Bull | 87 | 58.6% | 1.95 | $5.36 | 0.22 |
| Neutral | 179 | 48.0% | 0.94 | -$0.37 | -0.02 |
| Bear | 5 | 60.0% | 1.39 | $2.40 | 0.14 |
"""
    _atomic_write(BRAIN_ROOT / "regime" / "per_regime_performance.md", content)


def build_initial_state(strategies: dict):
    """Write initial state.json."""
    print("Building state.json...")
    save_state({
        "migrated_at": _now_iso(),
        "strategies": strategies,
        "last_sweep_session": "pre-migration",
    })


def main():
    print(f"Migrating to brain/ at {BRAIN_ROOT}\n")

    strategies = migrate_strategies()
    migrate_experiments()
    migrate_patterns()
    migrate_decisions()
    migrate_hypotheses()
    migrate_regime()
    build_initial_state(strategies)

    print("\nRebuilding indexes...")
    rebuild_all_indexes()

    print("\n✅ Migration complete.")
    print(f"   Brain root: {BRAIN_ROOT}")
    print(f"   Files: {sum(1 for _ in BRAIN_ROOT.rglob('*.md'))} markdown")
    print(f"   State: {BRAIN_ROOT / 'state.json'}")


if __name__ == "__main__":
    main()
