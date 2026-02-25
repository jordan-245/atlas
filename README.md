# Atlas

Multi-market algorithmic swing-trading lab. Researches, backtests, paper-trades, and (optionally) live-trades systematic strategies across equity markets.

Currently operating on **ASX 200** (248 tickers, AUD) and **S&P 500** (292 tickers, USD), with the architecture open to adding new markets by implementing a single `MarketProfile` class.

## How it works

```
Data (yfinance)  →  Universe filter  →  Strategy signals  →  Trade plan
                                                                  ↓
Dashboard  ←  Journal / Ledger  ←  Paper engine  ←  Approve + Execute
```

1. **Ingest** — downloads OHLCV history for the full ticker universe via yfinance, cached per-market.
2. **Universe** — filters to liquid, tradeable tickers (volume, price, sector diversity).
3. **Strategies** — scan the universe for entry/exit signals with confidence scoring.
4. **Plan** — generates a daily trade plan (entries + exits) respecting risk limits.
5. **Approve** — human-in-the-loop approval gate before any execution.
6. **Execute** — paper engine simulates fills, tracks positions, PnL, and equity curve.
7. **Review** — self-annealing loop compares realised vs. expected performance, flags degradation.

## Active strategies

| Strategy | Style | Holding period |
|---|---|---|
| **Mean Reversion** | Buy oversold pullbacks in uptrending stocks | 3–10 days |
| **Trend Following** | Ride breakouts above key moving averages | 5–20 days |
| **Opening Gap** | Fade or follow significant overnight gaps | 1–3 days |

Additional strategies (Momentum Breakout, BB Squeeze, MTF Momentum, Sector Rotation, Short-Term MR, Dividend Capture) are implemented but disabled pending further optimisation.

## Project structure

```
atlas/
├── markets/          Market profiles (ASX, S&P 500) — tickers, fees, hours
├── strategies/       Strategy implementations (BaseStrategy ABC)
├── backtest/         Walk-forward backtest engine with metrics
├── paper_engine/     Paper trading: portfolio state, plans, execution
├── brokers/          Broker adapters (paper, Moomoo live)
├── data/             yfinance ingestion + per-market cache
├── universe/         Liquidity/quality filtering
├── config/
│   ├── active/       Per-market active configs (asx.json, sp500.json)
│   └── versions/     Versioned config snapshots
├── journal/          Decision journal, trade ledger, mistake log
├── dashboard/        HTML dashboard + data generation
├── utils/            Helpers, indicators, Telegram alerts, config management
├── scripts/          CLI, cron wrapper, EOD settlement, health checks
├── pi-package/       Pi agent skills + extensions for autonomous ops
├── docs/             Optimisation guide
└── tasks/            Task tracking
```

## CLI

All commands accept `--market` / `-m` to target a specific market (default: `asx`).

```bash
# Portfolio status
python3 scripts/cli.py status
python3 scripts/cli.py -m sp500 status

# Daily workflow
python3 scripts/cli.py ingest              # refresh market data
python3 scripts/cli.py universe            # rebuild filtered universe
python3 scripts/cli.py plan                # generate today's trade plan
python3 scripts/cli.py approve             # approve the pending plan
python3 scripts/cli.py paper-run           # execute approved plan

# Analysis
python3 scripts/cli.py backtest            # walk-forward backtest
python3 scripts/cli.py ledger              # show trade history
python3 scripts/cli.py review              # self-annealing performance review

# Broker (Moomoo live trading)
python3 scripts/cli.py broker              # connection & account status
python3 scripts/cli.py live-run            # execute via live broker
python3 scripts/cli.py orders              # show open orders
python3 scripts/cli.py halt                # emergency: cancel all orders
python3 scripts/cli.py sync                # reconcile state with broker

# Utilities
python3 scripts/cli.py markets             # list available markets
python3 scripts/cli.py setup-secrets       # configure broker credentials
```

## Automation

Cron runs a [Pi](https://github.com/mariozechner/pi-coding-agent) agent twice daily (AEST, Mon–Fri):

| Time | Job | What happens |
|---|---|---|
| **08:30** | Pre-market | Refresh data → generate plan → Telegram summary |
| **17:30** | Post-close | EOD settlement → dashboard refresh → Telegram report |

Telegram alerts are sent on both success (📊 plan summary / 📈 equity snapshot) and failure (🚨 error with log tail). See `scripts/pi-cron.sh`.

## Configuration

Each market has an independent config at `config/active/{market_id}.json` controlling:

- **Universe** — minimum price, volume, market cap filters
- **Risk** — max risk per trade, max portfolio heat, position sizing
- **Strategies** — per-strategy parameters, enable/disable flags
- **Fees** — commission structure, slippage estimates
- **Backtest** — walk-forward window sizes, train/test split

Configs are versioned — `atlas review` compares live performance against backtest expectations and flags when re-optimisation may be needed.

## Adding a new market

1. Create `markets/{market_id}.py` implementing `MarketProfile` (see `markets/base.py`)
2. Add `config/active/{market_id}.json` with market-specific parameters
3. The rest (data, strategies, paper engine, CLI) works automatically via `--market`

## Requirements

- Python 3.10+
- Core: `pandas`, `numpy`, `yfinance`
- Live trading: `moomoo-api`
- Automation: [Pi](https://github.com/mariozechner/pi-coding-agent) (for cron-driven agent workflows)

## Credentials

Broker and Telegram credentials are stored in `~/.atlas-secrets.json` (never committed):

```bash
python3 scripts/cli.py setup-secrets       # interactive setup for broker creds
```

Or manually:

```json
{
    "telegram_bot_token": "...",
    "telegram_chat_id": "...",
    "moomoo_trade_pwd": "..."
}
```
