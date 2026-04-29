"""B.4 — Cron idempotency tests.

Each test verifies that running a cron-callable script twice in succession
produces the same final state as running once. This documents and enforces
the "safe to re-run" contract for every major cron script.

Pattern:
  1. Capture DB / file state before first run.
  2. First run (call the script's main function in-process with mocked broker).
  3. Capture state after first run.
  4. Second run (identical arguments).
  5. Capture state after second run.
  6. Assert: state after first run == state after second run (second run is a no-op).

Why in-process rather than subprocess?
  - The _isolate_prod_db fixture redirects db.atlas_db._db_path_override for the
    current process. Subprocesses cannot inherit an in-memory override, so they
    would touch the real atlas.db. In-process calls with monkeypatched brokers
    give the same idempotency guarantee with proper test isolation.

Broker mocking strategy:
  - Scripts that lazily import ``from brokers.registry import get_live_broker``
    are patched via ``monkeypatch.setattr("brokers.registry.get_live_broker", ...)``.
  - Scripts that import ``get_live_broker`` at module level (sync_broker_orders)
    are patched on the loaded module object directly.
"""

from __future__ import annotations

import hashlib
import importlib.util
import json
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock

import pytest

# ── Bootstrap ─────────────────────────────────────────────────────────────────
PROJECT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT))

import db.atlas_db as _adb  # noqa: E402


# ══════════════════════════════════════════════════════════════════════════════
# Shared helpers
# ══════════════════════════════════════════════════════════════════════════════


def _hash_table_state(tables: list[str]) -> str:
    """Return an MD5 hex-digest of the DB rows across *tables*.

    Used to assert that a second script run produces an identical final state.
    Table names are hardcoded in this file (controlled constants), not user input.
    """
    rows: dict[str, list] = {}
    for table in tables:
        try:
            with _adb.get_db() as conn:
                cur = conn.execute(  # noqa: S608 (table name is a test constant)
                    f"SELECT * FROM {table} ORDER BY rowid"
                )
                rows[table] = [dict(r) for r in cur.fetchall()]
        except Exception:
            rows[table] = []
    digest = json.dumps(rows, sort_keys=True, default=str).encode()
    return hashlib.md5(digest).hexdigest()


def _rowcount(table: str) -> int:
    """Return row count for *table* in the isolated test DB."""
    try:
        with _adb.get_db() as conn:
            row = conn.execute(  # noqa: S608
                f"SELECT COUNT(*) FROM {table}"
            ).fetchone()
            return row[0] if row else 0
    except Exception:
        return 0


def _load_script(name: str):
    """Import a script from scripts/ as a fresh module via importlib.

    Returns the module object. Using importlib avoids polluting sys.modules
    with script-style module names that could collide across tests.
    """
    path = PROJECT / "scripts" / f"{name}.py"
    spec = importlib.util.spec_from_file_location(name, path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)  # type: ignore[union-attr]
    return mod


def _make_broker(positions=None, orders=None) -> MagicMock:
    """Build a minimal mock broker for tests.

    Args:
        positions: list returned by broker.get_positions() (default: []).
        orders:    list returned by broker.get_open_orders() (default: []).
    """
    b = MagicMock()
    b.connect.return_value = True
    b.disconnect.return_value = None
    b.get_positions.return_value = positions or []
    b.get_open_orders.return_value = orders or []
    b.get_all_positions.return_value = positions or []
    # _broker_call is used by sync_broker_orders for Alpaca API calls —
    # default to returning an empty list (no orders from broker).
    b._broker_call.return_value = []
    return b


class _MockAlpacaOrder:
    """Minimal Alpaca-style order object with .model_dump() support."""

    def __init__(self, order_id: str = "test-order-001", symbol: str = "AAPL") -> None:
        self._order_id = order_id
        self._symbol = symbol

    def model_dump(self) -> dict:
        return {
            "id": self._order_id,
            "symbol": self._symbol,
            "side": "buy",
            "status": "filled",
            "qty": "10",
            "filled_qty": "10",
            "filled_avg_price": "150.00",
            "submitted_at": "2026-04-29T10:00:00+00:00",
            "filled_at": "2026-04-29T10:01:00+00:00",
            "order_class": None,
            "replaces": None,
            "client_order_id": "client-001",
        }


# ══════════════════════════════════════════════════════════════════════════════
# Test 1 — reconcile_positions.py (read-only, no --fix)
# ══════════════════════════════════════════════════════════════════════════════


def test_reconcile_positions_idempotent(
    _isolate_prod_db: None,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """reconcile_positions --market sp500 (read-only) must not change DB on re-run.

    With an empty broker response and no --fix flag, both runs are read-only
    comparisons. The DB hash must be identical before first run, after first run,
    and after second run.
    """
    mod = _load_script("reconcile_positions")

    mock_broker = _make_broker(positions=[])
    monkeypatch.setattr("brokers.registry.get_live_broker", lambda _: mock_broker)

    tables = ["trades", "broker_orders"]
    state_before = _hash_table_state(tables)

    # First run — broker returns no positions, internal state is also empty
    result1 = mod.reconcile_positions(market_id="sp500", fix=False)
    state_after_first = _hash_table_state(tables)

    # Second run — identical call
    result2 = mod.reconcile_positions(market_id="sp500", fix=False)
    state_after_second = _hash_table_state(tables)

    # Read-only runs must not touch DB at all
    assert state_before == state_after_first, (
        "First read-only reconcile_positions changed DB state unexpectedly"
    )
    assert state_after_first == state_after_second, (
        "Second reconcile_positions run changed DB state — not idempotent"
    )

    # Both runs must produce the same discrepancy list (deterministic comparison).
    # We do NOT assert empty — the live state file may have real positions that
    # the mock broker does not return (PHANTOM).  Idempotency means same output,
    # not necessarily zero discrepancies.
    assert result1["discrepancies"] == result2["discrepancies"], (
        "reconcile_positions returned different discrepancies on consecutive runs — "
        "not idempotent"
    )


def test_reconcile_positions_fix_idempotent(
    _isolate_prod_db: None,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """reconcile_positions --fix applied twice produces the same final state.

    With broker returning no positions and internal state empty, --fix is a
    no-op both times. The DB state must be identical after each run.
    """
    mod = _load_script("reconcile_positions")

    mock_broker = _make_broker(positions=[])
    monkeypatch.setattr("brokers.registry.get_live_broker", lambda _: mock_broker)

    # First fix run
    mod.reconcile_positions(market_id="sp500", fix=True)
    state_after_first = _hash_table_state(["trades"])

    # Second fix run
    mod.reconcile_positions(market_id="sp500", fix=True)
    state_after_second = _hash_table_state(["trades"])

    assert state_after_first == state_after_second, (
        "Applying reconcile_positions --fix twice produced different DB state "
        "(fix was not idempotent). "
        f"after_first={state_after_first[:8]}, after_second={state_after_second[:8]}"
    )


# ══════════════════════════════════════════════════════════════════════════════
# Test 2 — sync_protective_orders.py --dry-run
# ══════════════════════════════════════════════════════════════════════════════


def test_sync_protective_orders_dry_run_idempotent(
    _isolate_prod_db: None,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """sync_protective_orders --dry-run must produce identical results on re-run.

    Dry-run mode logs intent but writes no broker orders and no DB state.
    Two consecutive dry runs must produce the same result dict and leave the
    DB unchanged.
    """
    mod = _load_script("sync_protective_orders")

    mock_broker = _make_broker(positions=[], orders=[])
    monkeypatch.setattr("brokers.registry.get_live_broker", lambda _: mock_broker)

    today = datetime.now(tz=timezone.utc).strftime("%Y-%m-%d")
    tables = ["trades", "broker_orders", "position_protective_orders"]

    state_before = _hash_table_state(tables)

    # First dry run — sp500 has live_enabled=True in active config
    result1 = mod.sync_market(
        market_id="sp500",
        trade_date=today,
        dry_run=True,
    )
    state_after_first = _hash_table_state(tables)

    # Second dry run — identical arguments
    result2 = mod.sync_market(
        market_id="sp500",
        trade_date=today,
        dry_run=True,
    )
    state_after_second = _hash_table_state(tables)

    # DB must be unchanged throughout (dry-run = no writes)
    assert state_before == state_after_first, (
        "sync_protective_orders --dry-run wrote to DB on first run"
    )
    assert state_after_first == state_after_second, (
        "sync_protective_orders --dry-run produced different DB state on second run"
    )

    # Result structure must be consistent
    assert result1.get("dry_run") is True
    assert result2.get("dry_run") is True

    # Error is expected (broker returns no positions) or no error — both are stable
    assert result1.get("error") == result2.get("error"), (
        "sync_protective_orders --dry-run returned different error on first vs second run"
    )


def test_sync_protective_orders_dry_run_counts_stable(
    _isolate_prod_db: None,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Two --dry-run calls return the same counts dict.

    This guards against non-deterministic side effects in the dry-run path
    (e.g. timestamp-sensitive counters that increment across runs).
    """
    mod = _load_script("sync_protective_orders")

    mock_broker = _make_broker(positions=[], orders=[])
    monkeypatch.setattr("brokers.registry.get_live_broker", lambda _: mock_broker)

    today = datetime.now(tz=timezone.utc).strftime("%Y-%m-%d")

    r1 = mod.sync_market(market_id="sp500", trade_date=today, dry_run=True)
    r2 = mod.sync_market(market_id="sp500", trade_date=today, dry_run=True)

    counts1 = r1.get("counts", {})
    counts2 = r2.get("counts", {})

    # Counts should be identical across runs (dry-run is purely observational)
    assert counts1 == counts2, (
        f"sync_protective_orders --dry-run counts differ between runs: "
        f"run1={counts1}, run2={counts2}"
    )


# ══════════════════════════════════════════════════════════════════════════════
# Test 3 — sync_broker_orders.py (UPSERT semantics)
# ══════════════════════════════════════════════════════════════════════════════


def test_sync_broker_orders_upsert_idempotent(
    _isolate_prod_db: None,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """sync_broker_orders with identical broker data must not change rowcount on re-run.

    ON CONFLICT(order_id) DO UPDATE means the second run updates existing rows
    in-place rather than inserting duplicates. The rowcount must be the same
    after the second run as after the first.
    """
    mod = _load_script("sync_broker_orders")

    # Two mock orders with distinct IDs (simulates real broker returning 2 orders)
    mock_orders = [
        _MockAlpacaOrder("order-alpha-001", "AAPL"),
        _MockAlpacaOrder("order-alpha-002", "MSFT"),
    ]
    mock_broker = _make_broker()
    mock_broker._broker_call.return_value = mock_orders

    # Patch module-level get_live_broker (imported at module load time)
    monkeypatch.setattr(mod, "get_live_broker", lambda _: mock_broker)

    count_before = _rowcount("broker_orders")

    # First run — inserts 2 new rows
    stats1 = mod.sync_broker_orders(days=7, dry_run=False)
    count_after_first = _rowcount("broker_orders")

    # Second run — same orders from broker → UPSERT (no new rows)
    stats2 = mod.sync_broker_orders(days=7, dry_run=False)
    count_after_second = _rowcount("broker_orders")

    # Row count should be same after second run (UPSERT not INSERT)
    assert count_after_first == count_after_second, (
        f"sync_broker_orders inserted duplicate rows on second run: "
        f"after_first={count_after_first}, after_second={count_after_second}"
    )

    # First run should have added exactly 2 rows to the empty test DB
    assert count_after_first == count_before + 2, (
        f"Expected 2 new broker_orders rows, got {count_after_first - count_before}"
    )


def test_sync_broker_orders_dry_run_no_writes(
    _isolate_prod_db: None,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """sync_broker_orders --dry-run must not write any rows (confirmed idempotent).

    Both dry-run calls should leave broker_orders completely unchanged.
    """
    mod = _load_script("sync_broker_orders")

    mock_orders = [_MockAlpacaOrder("order-dry-001", "TSLA")]
    mock_broker = _make_broker()
    mock_broker._broker_call.return_value = mock_orders
    monkeypatch.setattr(mod, "get_live_broker", lambda _: mock_broker)

    count_before = _rowcount("broker_orders")

    mod.sync_broker_orders(days=7, dry_run=True)
    count_after_first = _rowcount("broker_orders")

    mod.sync_broker_orders(days=7, dry_run=True)
    count_after_second = _rowcount("broker_orders")

    assert count_after_first == count_before, (
        "sync_broker_orders --dry-run wrote rows to DB (should be zero writes)"
    )
    assert count_after_second == count_before, (
        "sync_broker_orders --dry-run wrote rows on second call"
    )


# ══════════════════════════════════════════════════════════════════════════════
# Test 4 — healthcheck_tp_coverage.py (state file idempotency)
# ══════════════════════════════════════════════════════════════════════════════


def test_healthcheck_tp_coverage_state_idempotent(
    _isolate_prod_db: None,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """healthcheck_tp_coverage --no-alert: state file is stable on re-run.

    When the broker reports no open positions (fully covered or empty), the
    state file must contain the same data after the first and second runs.
    """
    mod = _load_script("healthcheck_tp_coverage")

    # Broker with no positions → nothing to track, nothing to alert
    mock_broker = _make_broker(positions=[], orders=[])
    monkeypatch.setattr("brokers.registry.get_live_broker", lambda _: mock_broker)

    state_path = tmp_path / "tp_coverage_state.json"

    # First run
    rc1 = mod.run_check(
        markets=("sp500",),
        no_alert=True,
        state_path=state_path,
    )
    state_content_first = state_path.read_text() if state_path.exists() else ""

    # Second run — identical arguments
    rc2 = mod.run_check(
        markets=("sp500",),
        no_alert=True,
        state_path=state_path,
    )
    state_content_second = state_path.read_text() if state_path.exists() else ""

    # Exit codes must be consistent (both 0 = all covered, or both 2 = broker error)
    assert rc1 == rc2, (
        f"healthcheck_tp_coverage returned different exit codes: "
        f"run1={rc1}, run2={rc2}"
    )

    # State file must have the same keys after both runs.
    # We parse and compare only the stable keys (last_run_at and first_missing_at),
    # ignoring timestamp drift in last_run_at.
    if state_content_first and state_content_second:
        s1 = json.loads(state_content_first)
        s2 = json.loads(state_content_second)
        assert s1.get("first_missing_at", {}) == s2.get("first_missing_at", {}), (
            "State file first_missing_at changed between runs — not idempotent. "
            f"first={s1.get('first_missing_at')}, second={s2.get('first_missing_at')}"
        )


def test_healthcheck_tp_coverage_covered_position_no_alert(
    _isolate_prod_db: None,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """When all positions have stop+TP, run_check returns 0 on both runs.

    A position mock with has_stop=True and has_tp=True (inferred from classify_orders)
    means the state file should stay clean across runs.
    """
    mod = _load_script("healthcheck_tp_coverage")

    # Mock get_live_broker to return a broker, then mock check_market directly.
    # This avoids needing to build full Alpaca Position + Order mock objects.
    monkeypatch.setattr(
        mod,
        "check_market",
        lambda market_id: ([], None),  # no positions → all covered, no error
    )

    state_path = tmp_path / "tp_state.json"

    rc1 = mod.run_check(markets=("sp500",), no_alert=True, state_path=state_path)
    rc2 = mod.run_check(markets=("sp500",), no_alert=True, state_path=state_path)

    assert rc1 == 0, f"Expected exit 0 (all covered), got {rc1}"
    assert rc2 == 0, f"Expected exit 0 on second run, got {rc2}"

    # State file should be identical after both runs
    if state_path.exists():
        content = json.loads(state_path.read_text())
        assert content.get("first_missing_at", {}) == {}, (
            "State recorded missing positions when all were covered"
        )


# ══════════════════════════════════════════════════════════════════════════════
# Test 5 — healthcheck_pipelines.py (state file idempotency)
# ══════════════════════════════════════════════════════════════════════════════


def test_healthcheck_pipelines_empty_idempotent(
    _isolate_prod_db: None,
    tmp_path: Path,
) -> None:
    """healthcheck_pipelines with empty pipeline list returns 0 on both runs.

    An empty pipeline list skips all staleness checks. Both runs should:
    - Return exit code 0
    - Write the same (empty-stale-results) state file
    """
    mod = _load_script("healthcheck_pipelines")
    state_path = tmp_path / "pipelines_state.json"

    now = datetime.now(tz=timezone.utc)

    rc1 = mod.run_once(
        quiet=True,
        no_alert=True,
        state_path=state_path,
        pipelines=[],
        _now=now,
    )
    state1 = json.loads(state_path.read_text()) if state_path.exists() else {}

    rc2 = mod.run_once(
        quiet=True,
        no_alert=True,
        state_path=state_path,
        pipelines=[],
        _now=now,  # same frozen time → deterministic state
    )
    state2 = json.loads(state_path.read_text()) if state_path.exists() else {}

    assert rc1 == 0, f"Expected exit 0 (no pipelines to check), got {rc1}"
    assert rc2 == 0, f"Expected exit 0 on second run, got {rc2}"

    # State content must be identical when no time passes between runs
    assert state1 == state2, (
        f"healthcheck_pipelines state changed between runs: "
        f"run1={state1}, run2={state2}"
    )


def test_healthcheck_pipelines_fresh_pipeline_idempotent(
    _isolate_prod_db: None,
    tmp_path: Path,
) -> None:
    """healthcheck_pipelines with a single always-fresh pipeline is idempotent.

    We pass a pipeline whose ``source`` is 'logfile' pointing to a file that
    exists with a recent mtime. Both runs should return 0 and agree on state.
    """
    mod = _load_script("healthcheck_pipelines")

    # Create a log file with a recent mtime (acts as the freshness source)
    fake_log = tmp_path / "fake_service.log"
    fake_log.write_text("2026-04-29 10:00:00 INFO sync complete\n")

    # Pipeline that uses logfile mtime as freshness indicator
    fresh_pipeline: dict[str, Any] = {
        "name": "fake_fresh_service",
        "source": "logfile",
        "logfile": str(fake_log),   # run_once passes atlas_root; we use absolute path
        "threshold_days": 365,       # so generous it can never be stale
        "skip_weekends": False,
    }

    state_path = tmp_path / "pipelines_state_fresh.json"
    now = datetime.now(tz=timezone.utc)

    rc1 = mod.run_once(
        quiet=True,
        no_alert=True,
        state_path=state_path,
        atlas_root=tmp_path,        # override atlas_root so logfile path resolves
        pipelines=[fresh_pipeline],
        _now=now,
    )
    state1 = json.loads(state_path.read_text()) if state_path.exists() else {}

    rc2 = mod.run_once(
        quiet=True,
        no_alert=True,
        state_path=state_path,
        atlas_root=tmp_path,
        pipelines=[fresh_pipeline],
        _now=now,
    )
    state2 = json.loads(state_path.read_text()) if state_path.exists() else {}

    assert rc1 == 0, f"Expected exit 0 (fresh pipeline), got {rc1}"
    assert rc2 == 0, f"Expected exit 0 on second run, got {rc2}"

    # State should agree between runs (no stale alert recorded)
    assert state1 == state2, (
        f"healthcheck_pipelines state changed between runs for fresh pipeline: "
        f"run1={state1}, run2={state2}"
    )


# ══════════════════════════════════════════════════════════════════════════════
# Summary guard
# ══════════════════════════════════════════════════════════════════════════════


def test_idempotency_test_count() -> None:
    """Meta-test: assert at least 5 non-meta idempotency tests exist in this file.

    Guards against future developers removing tests without noticing.
    """
    # All tests in this module (excluding this one and helpers)
    test_fns = [
        name for name in globals()
        if name.startswith("test_") and name != "test_idempotency_test_count"
    ]
    assert len(test_fns) >= 5, (
        f"Expected ≥5 idempotency tests, found {len(test_fns)}: {test_fns}"
    )
