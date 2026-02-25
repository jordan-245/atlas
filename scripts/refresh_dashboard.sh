#!/bin/bash
# Refresh dashboard data — designed for frequent cron runs during market hours.
# 1. Re-downloads prices for open position tickers only (fast, ~10 tickers)
# 2. Regenerates dashboard JSON
set -e
cd /root/atlas
TICKERS=$(python3 -c "
import json
ps = json.load(open('paper_engine/portfolio_state.json'))
print(' '.join(p['ticker'] for p in ps.get('positions', [])))
" 2>/dev/null)

if [ -n "$TICKERS" ]; then
    python3 -c "
from data.ingest import download_ticker
tickers = '$TICKERS'.split()
for t in tickers:
    try:
        download_ticker(t, market_id='asx')
    except Exception as e:
        print(f'  WARN: {t}: {e}')
print(f'Refreshed {len(tickers)} tickers')
" 2>&1
fi

python3 dashboard/generate_data.py 2>&1
