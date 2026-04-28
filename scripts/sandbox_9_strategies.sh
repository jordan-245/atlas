#!/bin/bash
# Sandbox launcher — parameter sweeps for 9 stub strategies on sp500.
# Strategies: gap_and_go, heikin_ashi_reversal, macd_divergence,
#             monthly_rotation, overnight_return, pead_earnings_drift,
#             put_call_vix_proxy, relative_strength_pullback, rsi_divergence
#
# Run via: systemctl start atlas-sandbox-9strats.service
#          (scheduled to fire AFTER atlas-resweep-20260428.service completes)
#
# SANDBOX ONLY — uses --no-auto-promote. These sweeps populate research_best
# but do NOT modify config/active/sp500.json or activate anything for live
# trading. Human review is required before any promotion.
#
# Budget: 1.5h per strategy × 9 strategies = 13.5h sequential.
#         Timeout per strategy: 7200s (2h) with 120s kill-after grace.
set -euo pipefail

LOG_DIR=/root/atlas/logs
TS=$(date '+%Y%m%d_%H%M%S')
SUMMARY=$LOG_DIR/sandbox_summary_${TS}.log

mkdir -p "$LOG_DIR"
cd /root/atlas

echo "$(date -Iseconds) ===== SANDBOX 9 STRATEGIES START =====" | tee -a "$SUMMARY"
echo "$(date -Iseconds) Market/Universe: sp500" | tee -a "$SUMMARY"
echo "$(date -Iseconds) Mode: SANDBOX — --no-auto-promote (no live config changes)" | tee -a "$SUMMARY"
echo "$(date -Iseconds) Budget: 1.5h per strategy, 9 strategies sequential (~13.5h total)" | tee -a "$SUMMARY"
echo "" | tee -a "$SUMMARY"

# Strategies to sweep, in order.
STRATEGIES=(
    gap_and_go
    heikin_ashi_reversal
    macd_divergence
    monthly_rotation
    overnight_return
    pead_earnings_drift
    put_call_vix_proxy
    relative_strength_pullback
    rsi_divergence
)

run_sandbox_sweep() {
    local strategy=$1
    local hours=1.5
    local log="$LOG_DIR/sandbox_${strategy}_${TS}.log"

    # timeout = 1.5h budget (5400s) + 30min grace (1800s) = 7200s; kill-after 120s
    local timeout_sec=7200

    echo "$(date -Iseconds) [$strategy] sweep START (hours=${hours}, timeout=${timeout_sec}s, sandbox)" | tee -a "$SUMMARY"
    echo "$(date -Iseconds) [$strategy] log: $log" | tee -a "$SUMMARY"

    local rc=0
    timeout --signal=TERM --kill-after=120 "$timeout_sec" \
        python3 research/autoresearch_runner.py \
            --strategy   "$strategy" \
            --market     sp500 \
            --universe   sp500 \
            --hours      "$hours" \
            --fast-screen \
            --no-auto-promote \
        >> "$log" 2>&1 || rc=$?

    if [[ $rc -eq 0 ]]; then
        echo "$(date -Iseconds) [$strategy] sweep DONE (ok)" | tee -a "$SUMMARY"
    elif [[ $rc -eq 124 || $rc -eq 137 ]]; then
        echo "$(date -Iseconds) [$strategy] sweep DONE (timed out — rc=$rc, check log)" | tee -a "$SUMMARY"
    else
        echo "$(date -Iseconds) [$strategy] sweep DONE (non-zero exit rc=$rc — check log)" | tee -a "$SUMMARY"
    fi
}

# ─── Sequential sweeps — one strategy at a time ──────────────────────────────
for strat in "${STRATEGIES[@]}"; do
    run_sandbox_sweep "$strat"
    echo "" | tee -a "$SUMMARY"
done

# ─── Final summary: pull updated research_best rows ──────────────────────────
echo "$(date -Iseconds) ===== FINAL RESULTS FROM research_best =====" | tee -a "$SUMMARY"
echo "" | tee -a "$SUMMARY"

python3 - << 'PYEOF' 2>&1 | tee -a "$SUMMARY"
import sqlite3, os, sys
db = os.path.join(os.path.dirname(os.path.abspath(__file__)), '..', 'data', 'atlas.db')
strategies = [
    'gap_and_go', 'heikin_ashi_reversal', 'macd_divergence', 'monthly_rotation',
    'overnight_return', 'pead_earnings_drift', 'put_call_vix_proxy',
    'relative_strength_pullback', 'rsi_divergence',
]
with sqlite3.connect(db) as conn:
    conn.row_factory = sqlite3.Row
    placeholders = ','.join('?' * len(strategies))
    rows = conn.execute(
        f"SELECT strategy, universe, solo_sharpe, trades, max_dd_pct, updated_at "
        f"FROM research_best WHERE strategy IN ({placeholders}) ORDER BY strategy",
        strategies,
    ).fetchall()

print(f"{'strategy':<28} {'universe':<10} {'sharpe':>8} {'trades':>7} {'max_dd%':>8}  updated_at")
print("-" * 80)
populated = 0
for r in rows:
    sharpe = f"{r['solo_sharpe']:.4f}" if r['solo_sharpe'] is not None else "  NULL "
    dd     = f"{r['max_dd_pct']:.1f}%" if r['max_dd_pct']  is not None else "  NULL"
    print(f"{r['strategy']:<28} {(r['universe'] or ''):<10} {sharpe:>8} {(r['trades'] or 0):>7} {dd:>8}  {r['updated_at']}")
    if r['solo_sharpe'] is not None and (r['trades'] or 0) > 0:
        populated += 1

print()
print(f"Populated: {populated}/{len(strategies)} strategies have real results.")
if populated < len(strategies):
    missing = [r['strategy'] for r in rows if r['solo_sharpe'] is None or (r['trades'] or 0) == 0]
    print(f"Still stub: {', '.join(missing)}")
PYEOF

echo "" | tee -a "$SUMMARY"
echo "$(date -Iseconds) ===== ALL SANDBOX SWEEPS DONE =====" | tee -a "$SUMMARY"
echo "$(date -Iseconds) Review results above, then manually promote via:" | tee -a "$SUMMARY"
echo "$(date -Iseconds)   python3 scripts/trigger_commodity_promotion.py --market sp500 --strategy <name> --apply" | tee -a "$SUMMARY"
touch "$LOG_DIR/sandbox_9strats_${TS}.done"
echo "$(date -Iseconds) Done sentinel: $LOG_DIR/sandbox_9strats_${TS}.done" | tee -a "$SUMMARY"
