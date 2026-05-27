# Broker & Portfolio — State Queries

## Broker connection check

```bash
cd /root/atlas && python3 scripts/cli.py -m sp500 broker
```

Shows: broker type, mode (live/paper), base URL, connection status, equity, cash, positions.

Key output lines:
- `AlpacaBroker connected: paper=False feed=iex equity=$3519.69 status=ACTIVE` → healthy
- Connection error → broker offline

## Portfolio status (full)

```bash
cd /root/atlas && python3 scripts/cli.py -m sp500 status
```

Shows: config version, equity, cash, open positions with entry prices, unrealized PnL, exposure.

## Open orders

```bash
cd /root/atlas && python3 scripts/cli.py -m sp500 orders
```

## Trade history

```bash
cd /root/atlas && python3 scripts/cli.py -m sp500 ledger
cd /root/atlas && python3 scripts/cli.py -m sp500 history  # with actual fees
```

## Market state

```bash
cd /root/atlas && python3 scripts/cli.py -m sp500 market-check
```

Shows: market open/closed, trading calendar, next open/close times.

---

## Equity curve

```bash
# Read latest entry
python3 -c "
import json
curve = json.load(open('logs/equity_curve_sp500.json'))
latest = curve[-1]
print(f'Date: {latest[\"date\"]}')
print(f'Equity: \${latest[\"equity\"]:.2f}')
print(f'PnL: \${latest[\"pnl\"]:.2f}')
print(f'Entries: {len(curve)}')
"
```

Each entry: `{ "date": "YYYY-MM-DD", "equity": float, "pnl": float, "fx_rate": float, "estimated": bool }`

## Performance metrics from backtest

```bash
cd /root/atlas && python3 scripts/cli.py -m sp500 backtest
```

The walk-forward window is config-driven
(`config/active/<market>.json -> backtest.{train_window_days,test_window_days,step_days}`).
The `backtest` subcommand does NOT accept `--days` or `--date` — use
`atlas_jobs_run` with job `cli_backtest` and only the `market` arg.
