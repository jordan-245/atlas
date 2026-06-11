-- Atlas v2.0 — SQLite Schema
-- All tables use IF NOT EXISTS for idempotent init
-- Generated from docs/ARCHITECTURE.md

-- ═══════════════════════════════════════════════════════════
-- SCHEMA VERSION
-- ═══════════════════════════════════════════════════════════

CREATE TABLE IF NOT EXISTS schema_version (
    version     INTEGER PRIMARY KEY,
    applied_at  TEXT    DEFAULT (datetime('now'))
);
INSERT OR IGNORE INTO schema_version (version) VALUES (1);

-- ═══════════════════════════════════════════════════════════
-- PRICE DATA
-- ═══════════════════════════════════════════════════════════

CREATE TABLE IF NOT EXISTS ohlcv (
    ticker      TEXT    NOT NULL,
    date        TEXT    NOT NULL,  -- ISO date YYYY-MM-DD
    open        REAL    NOT NULL,
    high        REAL    NOT NULL,
    low         REAL    NOT NULL,
    close       REAL    NOT NULL,
    adj_close   REAL,
    volume      INTEGER NOT NULL,
    universe    TEXT    NOT NULL,  -- 'sp500', 'sector_etfs', 'treasury_etfs', etc.
    source      TEXT    DEFAULT 'tiingo',
    PRIMARY KEY (ticker, date)
);
CREATE INDEX IF NOT EXISTS idx_ohlcv_date ON ohlcv(date);
CREATE INDEX IF NOT EXISTS idx_ohlcv_universe ON ohlcv(universe, date);

-- ═══════════════════════════════════════════════════════════
-- REGIME HISTORY (Layer 1 output)
-- ═══════════════════════════════════════════════════════════

CREATE TABLE IF NOT EXISTS regime_history (
    date                TEXT    PRIMARY KEY,
    regime_state        TEXT    NOT NULL,  -- enum: bull_risk_on, bull_risk_off, etc.
    trend_score         REAL,
    risk_score          REAL,
    active_universes    TEXT,              -- JSON array: ["sp500","sector_etfs"]
    sizing_multiplier   REAL    DEFAULT 1.0,
    enabled_strategies  TEXT,              -- JSON array
    reasoning           TEXT,
    model_version       TEXT,
    pending_state       TEXT    DEFAULT NULL  -- raw regime awaiting N-day confirmation
);
CREATE INDEX IF NOT EXISTS idx_regime_state ON regime_history(regime_state);

CREATE TABLE IF NOT EXISTS trades (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    ticker          TEXT    NOT NULL,
    strategy        TEXT    NOT NULL,
    universe        TEXT,
    direction       TEXT    DEFAULT 'long',
    entry_date      TEXT    NOT NULL,
    entry_price     REAL    NOT NULL,
    shares          INTEGER NOT NULL,
    stop_price      REAL,
    take_profit     REAL,
    exit_date       TEXT,
    exit_price      REAL,
    exit_reason     TEXT,
    pnl             REAL,
    pnl_pct         REAL,
    mae             REAL,              -- Max adverse excursion
    mfe             REAL,              -- Max favourable excursion
    hold_days       INTEGER,
    confidence      REAL,
    regime_at_entry TEXT,
    regime_at_exit  TEXT,
    status          TEXT    DEFAULT 'open',  -- 'open', 'closed'
    config_version  TEXT,
    created_at      TEXT    DEFAULT (datetime('now')),
    updated_at      TEXT    DEFAULT (datetime('now')),
    stop_order_id   TEXT    DEFAULT '',
    tp_order_id     TEXT    DEFAULT '',
    superseded      INTEGER NOT NULL DEFAULT 0 CHECK (superseded IN (0,1)),
    CHECK (exit_date IS NULL OR exit_date >= entry_date),
    CHECK (
        stop_price IS NULL
        OR (direction = 'long'  AND stop_price < entry_price)
        OR (direction = 'short' AND stop_price > entry_price)
    )
);
CREATE INDEX IF NOT EXISTS idx_trades_status ON trades(status);
CREATE INDEX IF NOT EXISTS idx_trades_strategy ON trades(strategy);
CREATE INDEX IF NOT EXISTS idx_trades_dates ON trades(entry_date, exit_date);
CREATE UNIQUE INDEX IF NOT EXISTS idx_trades_unique_open ON trades(ticker, universe) WHERE status='open';
CREATE UNIQUE INDEX IF NOT EXISTS uq_trades_active_closed
  ON trades(ticker, strategy, DATE(exit_date), ROUND(pnl, 2))
  WHERE status = 'closed' AND superseded = 0;
-- Natural-key dedup index (#315): blocks reconciler from re-recording the same
-- logical fill across consecutive days. Key: ticker + fill-date + price + shares.
CREATE UNIQUE INDEX IF NOT EXISTS uq_trades_natural_key
  ON trades(ticker, DATE(exit_date), exit_price, shares)
 WHERE exit_date IS NOT NULL AND status = 'closed';

-- Convenience view: all non-superseded trades (used by P&L consumers)
DROP VIEW IF EXISTS trades_active;
CREATE VIEW IF NOT EXISTS trades_active AS
  SELECT * FROM trades WHERE superseded = 0;

-- ═══════════════════════════════════════════════════════════
-- PORTFOLIO STATE
-- ═══════════════════════════════════════════════════════════

CREATE TABLE IF NOT EXISTS equity_curve (
    date            TEXT    NOT NULL,
    market_id       TEXT    NOT NULL,
    equity          REAL    NOT NULL,
    cash            REAL,
    positions_value REAL,
    day_pnl         REAL,
    regime_state    TEXT,
    PRIMARY KEY (date, market_id)
);

-- ═══════════════════════════════════════════════════════════
-- SYSTEM
-- ═══════════════════════════════════════════════════════════

CREATE TABLE IF NOT EXISTS heartbeats (
    service     TEXT    PRIMARY KEY,
    timestamp   TEXT    NOT NULL,
    status      TEXT    NOT NULL,
    detail      TEXT               -- JSON
);

CREATE TABLE IF NOT EXISTS system_log (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    timestamp   TEXT    DEFAULT (datetime('now')),
    level       TEXT    NOT NULL,  -- 'info', 'warning', 'error', 'critical'
    service     TEXT    NOT NULL,
    message     TEXT,
    detail      TEXT               -- JSON
);
CREATE INDEX IF NOT EXISTS idx_syslog_ts ON system_log(timestamp);
CREATE INDEX IF NOT EXISTS idx_syslog_service ON system_log(service);

CREATE TABLE IF NOT EXISTS equity_history (
  market_id  TEXT NOT NULL,
  date       TEXT NOT NULL,
  equity     REAL NOT NULL,
  pnl        REAL,
  PRIMARY KEY (market_id, date)
);
CREATE INDEX IF NOT EXISTS idx_equity_history_market_date ON equity_history(market_id, date);

-- ═══════════════════════════════════════════════════════════
-- CONFIG OVERRIDES (dashboard universe/strategy toggles — 2026-05-05)
-- DB-resident override layer on top of config/active/*.json.
-- Enables operators to toggle universe and strategy state from
-- the dashboard with a full audit trail.
-- ═══════════════════════════════════════════════════════════

CREATE TABLE IF NOT EXISTS config_overrides (
  id           INTEGER PRIMARY KEY AUTOINCREMENT,
  scope        TEXT    NOT NULL CHECK(scope IN ('universe','strategy')),
  -- universe: market_id (e.g. 'sp500')
  -- strategy: 'market_id.strategy_name' (e.g. 'commodity_etfs.connors_rsi2')
  key          TEXT    NOT NULL,
  -- For scope='universe': 'live' | 'passive' | 'disabled'
  -- For scope='strategy': 'enabled' | 'disabled'
  state        TEXT    NOT NULL,
  reason       TEXT,                           -- mandatory at API layer; nullable at DB
  created_by   TEXT    NOT NULL,               -- 'human:<username>' | 'system' | 'telegram'
  created_at   TEXT    NOT NULL DEFAULT (datetime('now')),
  -- Optional auto-expiry. NULL = never expires.
  expires_at   TEXT,
  -- Effective state immediately before this override was applied.
  prev_state   TEXT,
  -- Lifecycle: 1=active (consulted by readers), 0=superseded/reverted/expired.
  active       INTEGER NOT NULL DEFAULT 1 CHECK(active IN (0,1)),
  ended_at     TEXT,
  ended_reason TEXT CHECK(ended_reason IN ('reverted','expired','superseded') OR ended_reason IS NULL)
);

-- Only one ACTIVE override per (scope, key). Historical rows are unconstrained.
CREATE UNIQUE INDEX IF NOT EXISTS uq_config_overrides_active
  ON config_overrides(scope, key) WHERE active = 1;

-- Sweep job index (find rows due for expiry).
CREATE INDEX IF NOT EXISTS idx_config_overrides_expires
  ON config_overrides(expires_at) WHERE active = 1 AND expires_at IS NOT NULL;

-- Lookup index for read-side resolution.
CREATE INDEX IF NOT EXISTS idx_config_overrides_lookup
  ON config_overrides(scope, key, active);


CREATE TABLE IF NOT EXISTS config_override_audit (
  id           INTEGER PRIMARY KEY AUTOINCREMENT,
  ts           TEXT NOT NULL DEFAULT (datetime('now')),
  override_id  INTEGER REFERENCES config_overrides(id),
  scope        TEXT NOT NULL,
  key          TEXT NOT NULL,
  action       TEXT NOT NULL CHECK(action IN ('create','revert','expire','supersede')),
  from_state   TEXT,
  to_state     TEXT,
  reason       TEXT,
  actor        TEXT NOT NULL,
  source       TEXT NOT NULL CHECK(source IN ('dashboard','cli','telegram','sweep')),
  remote_ip    TEXT,
  payload_json TEXT
);

CREATE INDEX IF NOT EXISTS idx_config_override_audit_ts ON config_override_audit(ts DESC);
CREATE INDEX IF NOT EXISTS idx_config_override_audit_key ON config_override_audit(scope, key, ts DESC);

-- Immutability — model copied verbatim from fix_audit_log.
CREATE TRIGGER IF NOT EXISTS config_override_audit_no_update
  BEFORE UPDATE ON config_override_audit
  BEGIN SELECT RAISE(ABORT, 'config_override_audit is immutable (append-only)'); END;

CREATE TRIGGER IF NOT EXISTS config_override_audit_no_delete
  BEFORE DELETE ON config_override_audit
  BEGIN SELECT RAISE(ABORT, 'config_override_audit is immutable (append-only)'); END;

-- ═══════════════════════════════════════════════════════════
-- PAPER TRADING TABLES
-- Exact mirrors of `trades` and `position_protective_orders`
-- for strategy paper-trading runs.  Schema added 2026-05-06.
-- ═══════════════════════════════════════════════════════════

CREATE TABLE IF NOT EXISTS paper_trades (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    ticker          TEXT    NOT NULL,
    strategy        TEXT    NOT NULL,
    universe        TEXT,
    direction       TEXT    DEFAULT 'long',
    entry_date      TEXT    NOT NULL,
    entry_price     REAL    NOT NULL,
    shares          INTEGER NOT NULL,
    stop_price      REAL,
    take_profit     REAL,
    exit_date       TEXT,
    exit_price      REAL,
    exit_reason     TEXT,
    pnl             REAL,
    pnl_pct         REAL,
    mae             REAL,              -- Max adverse excursion
    mfe             REAL,              -- Max favourable excursion
    hold_days       INTEGER,
    confidence      REAL,
    regime_at_entry TEXT,
    regime_at_exit  TEXT,
    status          TEXT    DEFAULT 'open',  -- 'open', 'closed'
    config_version  TEXT,
    created_at      TEXT    DEFAULT (datetime('now')),
    updated_at      TEXT    DEFAULT (datetime('now')),
    stop_order_id   TEXT    DEFAULT '',
    tp_order_id     TEXT    DEFAULT '',
    superseded      INTEGER NOT NULL DEFAULT 0 CHECK (superseded IN (0,1)),
    paper_account_id TEXT,             -- Alpaca paper account number (e.g. "PA3TTBLZM6M7")
    CHECK (exit_date IS NULL OR exit_date >= entry_date),
    CHECK (
        stop_price IS NULL
        OR (direction = 'long'  AND stop_price < entry_price)
        OR (direction = 'short' AND stop_price > entry_price)
    )
);
CREATE INDEX IF NOT EXISTS idx_paper_trades_status   ON paper_trades(status);
CREATE INDEX IF NOT EXISTS idx_paper_trades_strategy ON paper_trades(strategy);
CREATE INDEX IF NOT EXISTS idx_paper_trades_dates    ON paper_trades(entry_date, exit_date);
CREATE UNIQUE INDEX IF NOT EXISTS idx_paper_trades_unique_open
    ON paper_trades(ticker, universe) WHERE status='open';
CREATE UNIQUE INDEX IF NOT EXISTS uq_paper_trades_active_closed
    ON paper_trades(ticker, strategy, DATE(exit_date), ROUND(pnl, 2))
    WHERE status = 'closed' AND superseded = 0;

-- Convenience view: non-superseded paper trades (mirrors trades_active)
DROP VIEW IF EXISTS paper_trades_active;
CREATE VIEW IF NOT EXISTS paper_trades_active AS
  SELECT * FROM paper_trades WHERE superseded = 0;

CREATE TABLE IF NOT EXISTS paper_position_protective_orders (
    market_id       TEXT NOT NULL,
    ticker          TEXT NOT NULL,
    trade_id        INTEGER,               -- FK to paper_trades.id (nullable for legacy)
    position_qty    REAL NOT NULL,
    stop_order_id   TEXT,                  -- Alpaca order_id of stop
    stop_price      REAL,                  -- The stop trigger price
    tp_order_id     TEXT,                  -- Alpaca order_id of TP limit
    tp_price        REAL,                  -- The TP limit price
    oco_class       TEXT,                  -- 'oco' | 'bracket' | NULL (independent)
    last_synced_at  TEXT NOT NULL,         -- ISO timestamp of last sync from broker truth
    status          TEXT NOT NULL DEFAULT 'active',  -- 'active' | 'closed' | 'detached'
    created_at      TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    PRIMARY KEY (market_id, ticker)
);
CREATE INDEX IF NOT EXISTS idx_paper_protective_status
    ON paper_position_protective_orders(status);
CREATE INDEX IF NOT EXISTS idx_paper_protective_trade_id
    ON paper_position_protective_orders(trade_id);


-- ═══════════════════════════════════════════════════════════
-- TELEGRAM MESSAGE CAPTURE (bidirectional observability)
-- ═══════════════════════════════════════════════════════════

CREATE TABLE IF NOT EXISTS telegram_messages (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    direction    TEXT NOT NULL CHECK (direction IN ('outbound', 'inbound')),
    chat_id      TEXT NOT NULL,
    message_id   INTEGER,
    user_id      TEXT,
    username     TEXT,
    body         TEXT NOT NULL,
    parse_mode   TEXT,
    sent_at      TEXT NOT NULL,
    api_status   INTEGER,
    api_error    TEXT,
    is_command   INTEGER DEFAULT 0,
    command_name TEXT,
    created_at   TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE INDEX IF NOT EXISTS idx_tgm_chat_time ON telegram_messages(chat_id, sent_at DESC);
CREATE INDEX IF NOT EXISTS idx_tgm_direction_time ON telegram_messages(direction, sent_at DESC);
CREATE INDEX IF NOT EXISTS idx_tgm_command ON telegram_messages(command_name) WHERE command_name IS NOT NULL;

-- Candidate contradictions view.  Computes (claim x research_best)
-- deltas with severity classification.  Read by sync_contradictions()
-- which INSERTs the WHERE severity IS NOT NULL rows into contradictions.
-- COALESCE(solo_sharpe, sharpe) mirrors db/research.py::get_research_best
-- behaviour (solo_sharpe is the post-M2 canonical column).
DROP VIEW IF EXISTS v_candidate_contradictions;

-- Operator-facing view: unresolved contradictions joined to source info.
DROP VIEW IF EXISTS v_open_contradictions;

-- Per-strategy roll-up.  Powers the wiki materializer (Phase 7) and
-- operator dashboard.  One row per (strategy, universe) cross-regime.
DROP VIEW IF EXISTS v_strategy_summary;

-- ═══════════════════════════════════════════════════════════════════════════
-- Tables formerly created by one-time migrations (migrations deleted in the
-- 2026-06 great-deletion; schema.sql is now self-contained).
-- ═══════════════════════════════════════════════════════════════════════════

-- Per-market equity attribution history (read by /api/portfolio + dashboard).
CREATE TABLE IF NOT EXISTS market_equity_history (
    id               INTEGER PRIMARY KEY AUTOINCREMENT,
    date             TEXT NOT NULL,
    market_id        TEXT NOT NULL,
    allocated_equity REAL NOT NULL,
    position_mv      REAL NOT NULL,
    cash_attributed  REAL NOT NULL,
    broker_equity    REAL NOT NULL,
    broker_cash      REAL NOT NULL,
    snapshot_time    TEXT NOT NULL,
    created_at       TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    UNIQUE(date, market_id)
);
CREATE INDEX IF NOT EXISTS idx_market_equity_history_date
    ON market_equity_history(date);
CREATE INDEX IF NOT EXISTS idx_market_equity_history_market
    ON market_equity_history(market_id, date);

-- Error log written by atlas.kernel.logging_config.SQLiteErrorWriter.
-- (Slimmed from the retired auto-remediation schema: triage/fix-attempt
-- columns kept only where the writer still populates them.)
CREATE TABLE IF NOT EXISTS errors (
  id                   INTEGER PRIMARY KEY AUTOINCREMENT,
  fingerprint          TEXT    NOT NULL,
  first_seen_ts        TEXT    NOT NULL,
  last_seen_ts         TEXT    NOT NULL,
  occurrence_count     INTEGER NOT NULL DEFAULT 1,
  ts                   TEXT    NOT NULL,
  source               TEXT    NOT NULL,
  service              TEXT,
  level                TEXT    NOT NULL CHECK(level IN ('WARNING','ERROR','CRITICAL')),
  logger_name          TEXT,
  message              TEXT    NOT NULL,
  exc_type             TEXT,
  exc_message          TEXT,
  traceback            TEXT,
  file_path            TEXT,
  line_number          INTEGER,
  function_name        TEXT,
  pid                  INTEGER,
  hostname             TEXT,
  classification       TEXT    NOT NULL DEFAULT 'UNCLASSIFIED',
  tier                 INTEGER NOT NULL DEFAULT 99,
  remediation_status   TEXT    NOT NULL DEFAULT 'NEW',
  created_at           TEXT    NOT NULL DEFAULT (datetime('now'))
);
CREATE UNIQUE INDEX IF NOT EXISTS idx_errors_fingerprint ON errors(fingerprint);

-- Strategy expected-value snapshots (atlas.analytics.strategy_ev).
CREATE TABLE IF NOT EXISTS signal_ev (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    as_of TEXT NOT NULL,
    strategy TEXT NOT NULL,
    n_trades INTEGER NOT NULL,
    n_wins INTEGER,
    n_losses INTEGER,
    win_rate REAL,
    avg_win REAL,
    avg_loss REAL,
    ev_per_trade REAL,
    ev_per_trade_pct REAL,
    profit_factor REAL,
    ci_low REAL,
    ci_high REAL,
    classification TEXT,
    status TEXT,
    created_at TEXT DEFAULT CURRENT_TIMESTAMP,
    UNIQUE(as_of, strategy)
);
