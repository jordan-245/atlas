#!/usr/bin/env bash
# Daily forward-paper cycle for deployed shadow strategies (board 2026-06-09 gate).
# Order matters: (1) record realized return from PRE-rebalance equity, (2) refresh today's target
# weights from live data (Crucible), (3) run the shadow loop (paper orders + track-vs-expectation).
set -uo pipefail
LOG=/root/atlas/data/live/forward_paper.log
echo "=== forward-paper cycle $(date -Is) ===" >> "$LOG"
cd /root/atlas    && python3 -m atlas.db.refresh_benchmark           >> "$LOG" 2>&1 || echo "refresh_benchmark FAILED" >> "$LOG"
cd /root/atlas    && python3 -m atlas.execution.record_returns      >> "$LOG" 2>&1 || echo "record_returns FAILED" >> "$LOG"
cd /root/atlas    && python3 -m atlas.execution.record_fills        >> "$LOG" 2>&1 || echo "record_fills FAILED" >> "$LOG"
cd /root/crucible && python3 live/deploy.py refresh                 >> "$LOG" 2>&1 || echo "weight refresh FAILED" >> "$LOG"
# #34 soak: shadow-compare crucible's file artifacts vs the registry (no writes; Telegram on divergence)
cd /root/atlas    && python3 -m atlas.execution.intake              >> "$LOG" 2>&1 || echo "intake soak DIVERGED" >> "$LOG"
cd /root/atlas    && python3 -m atlas.execution.daily --mode shadow >> "$LOG" 2>&1 || echo "daily shadow FAILED" >> "$LOG"
# Invariant guard: Σ(virtual books) must equal broker positions. Alerts (Telegram) on any drift so the
# accounting that feeds the forward-paper evidence can never silently corrupt again (added 2026-06-16).
cd /root/atlas    && python3 -m atlas.execution.reconcile_books     >> "$LOG" 2>&1 || echo "book<->broker DRIFT — see reconcile_books" >> "$LOG"
echo "=== done $(date -Is) ===" >> "$LOG"
