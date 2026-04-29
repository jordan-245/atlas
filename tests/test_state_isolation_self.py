"""Self-tests for state-isolation fixtures added in Task #284.

Each test PROVES that the autouse conftest fixture is active by:
  1. Writing to the module's normal write path
  2. Asserting the write landed in a tmp dir (not prod)
  3. Asserting the prod file was NOT modified

Three modules under test:
  - services/chat_db.py      (CHAT_DB_PATH → data/chat.db)
  - brokers/price_arbiter.py (_THROTTLE_PATH → data/price_arbiter_alert_throttle.json)
  - scripts/reconcile_shadow.py (_ALERT_STATE_FILE → data/reconcile_shadow_alert_state.json)

Root-cause class: module-level hardcoded paths — same pattern as kill_switch._HALT_FILE
(commit dede8d62) and live_portfolio._STATE_DIR (commit 4ea328fa).  Task #284 extends
coverage to these three additional modules.
"""
from __future__ import annotations

import json
import os
from pathlib import Path


# ---------------------------------------------------------------------------
# 1. services.chat_db — CHAT_DB_PATH
# ---------------------------------------------------------------------------

PROD_CHAT_DB = "/root/atlas/data/chat.db"


def test_chat_db_isolation_redirects_writes_to_tmp(tmp_path: Path) -> None:
    """Fixture must redirect CHAT_DB_PATH to a tmp path.

    We call create_session() (which opens/creates the DB) and then assert:
      - The CHAT_DB_PATH the module holds points somewhere in pytest's tmp tree
      - The prod file data/chat.db mtime is unchanged
    """
    import services.chat_db as cdb

    prod_mtime_before = os.path.getmtime(PROD_CHAT_DB) if os.path.exists(PROD_CHAT_DB) else None
    prod_size_before = os.path.getsize(PROD_CHAT_DB) if os.path.exists(PROD_CHAT_DB) else None

    # Ensure schema exists so create_session() works
    cdb.init_db(db_path=str(cdb.CHAT_DB_PATH))

    # Perform a real write via the module's public API
    session = cdb.create_session(name="isolation_test")
    assert session["id"], "create_session should return an id"

    # The path the module is currently using must NOT be the prod path
    current_path = str(cdb.CHAT_DB_PATH)
    assert current_path != PROD_CHAT_DB, (
        f"CHAT_DB_PATH still points to prod! Got: {current_path}"
    )
    # Must be inside a pytest tmp dir
    assert "/tmp" in current_path or "pytest" in current_path or "tmp_path" in current_path, (
        f"CHAT_DB_PATH is not in a tmp dir: {current_path}"
    )

    # Prod file must be untouched
    if prod_mtime_before is not None:
        prod_mtime_after = os.path.getmtime(PROD_CHAT_DB)
        prod_size_after = os.path.getsize(PROD_CHAT_DB)
        assert prod_mtime_after == prod_mtime_before, (
            f"data/chat.db mtime changed — isolation broken! "
            f"{prod_mtime_before:.3f} → {prod_mtime_after:.3f}"
        )
        assert prod_size_after == prod_size_before, (
            f"data/chat.db size changed — isolation broken! "
            f"{prod_size_before} → {prod_size_after} bytes"
        )


# ---------------------------------------------------------------------------
# 2. brokers.price_arbiter — _THROTTLE_PATH
# ---------------------------------------------------------------------------

PROD_THROTTLE_PATH = "/root/atlas/data/price_arbiter_alert_throttle.json"


def test_price_arbiter_isolation_redirects_writes_to_tmp(tmp_path: Path) -> None:
    """Fixture must redirect _THROTTLE_PATH to a tmp path.

    We call _should_send_alert() which writes the throttle JSON if no prior
    throttle exists. Then we assert the prod file was not touched.
    """
    import brokers.price_arbiter as pa

    prod_mtime_before = (
        os.path.getmtime(PROD_THROTTLE_PATH) if os.path.exists(PROD_THROTTLE_PATH) else None
    )
    prod_size_before = (
        os.path.getsize(PROD_THROTTLE_PATH) if os.path.exists(PROD_THROTTLE_PATH) else None
    )

    # The throttle path must NOT be the prod path
    current_path = str(pa._THROTTLE_PATH)
    assert current_path != PROD_THROTTLE_PATH, (
        f"_THROTTLE_PATH still points to prod! Got: {current_path}"
    )
    assert "/tmp" in current_path or "pytest" in current_path, (
        f"_THROTTLE_PATH is not in a tmp dir: {current_path}"
    )

    # Call the write path — _should_send_alert writes the throttle JSON
    result = pa._should_send_alert("TEST_TICKER")
    # First call with fresh (empty) tmp file should return True (allow send)
    assert result is True, "_should_send_alert should return True on first call"

    # The throttle JSON must have been written to the TMP path, not prod
    assert pa._THROTTLE_PATH.exists(), (
        f"Throttle file not written to expected tmp path: {pa._THROTTLE_PATH}"
    )
    throttle_data = json.loads(pa._THROTTLE_PATH.read_text())
    assert "TEST_TICKER" in throttle_data, "TEST_TICKER should be in throttle data"

    # Prod file must be untouched
    if prod_mtime_before is not None and os.path.exists(PROD_THROTTLE_PATH):
        prod_mtime_after = os.path.getmtime(PROD_THROTTLE_PATH)
        prod_size_after = os.path.getsize(PROD_THROTTLE_PATH)
        assert prod_mtime_after == prod_mtime_before, (
            f"data/price_arbiter_alert_throttle.json mtime changed — isolation broken! "
            f"{prod_mtime_before:.3f} → {prod_mtime_after:.3f}"
        )
        assert prod_size_after == prod_size_before, (
            f"data/price_arbiter_alert_throttle.json size changed — isolation broken! "
            f"{prod_size_before} → {prod_size_after} bytes"
        )


# ---------------------------------------------------------------------------
# 3. scripts.reconcile_shadow — _ALERT_STATE_FILE
# ---------------------------------------------------------------------------

PROD_ALERT_STATE = "/root/atlas/data/reconcile_shadow_alert_state.json"


def test_reconcile_shadow_isolation_redirects_writes_to_tmp(tmp_path: Path) -> None:
    """Fixture must redirect _ALERT_STATE_FILE to a tmp path.

    We call _write_alert_state() (or simulate a direct write to the constant path)
    and assert the prod file was not touched.
    """
    import scripts.reconcile_shadow as rs

    prod_mtime_before = (
        os.path.getmtime(PROD_ALERT_STATE) if os.path.exists(PROD_ALERT_STATE) else None
    )
    prod_size_before = (
        os.path.getsize(PROD_ALERT_STATE) if os.path.exists(PROD_ALERT_STATE) else None
    )

    # The alert state file must NOT be the prod path
    current_path = str(rs._ALERT_STATE_FILE)
    assert current_path != PROD_ALERT_STATE, (
        f"_ALERT_STATE_FILE still points to prod! Got: {current_path}"
    )
    assert "/tmp" in current_path or "pytest" in current_path, (
        f"_ALERT_STATE_FILE is not in a tmp dir: {current_path}"
    )

    # Write directly to the module-level path (as the module's code would do)
    alert_data = {"last_alert": "2026-04-30T00:00:00+00:00", "count": 1}
    rs._ALERT_STATE_FILE.write_text(json.dumps(alert_data, indent=2))
    assert rs._ALERT_STATE_FILE.exists(), (
        f"Alert state file not written to tmp path: {rs._ALERT_STATE_FILE}"
    )

    # Prod file must be untouched
    if prod_mtime_before is not None and os.path.exists(PROD_ALERT_STATE):
        prod_mtime_after = os.path.getmtime(PROD_ALERT_STATE)
        prod_size_after = os.path.getsize(PROD_ALERT_STATE)
        assert prod_mtime_after == prod_mtime_before, (
            f"data/reconcile_shadow_alert_state.json mtime changed — isolation broken! "
            f"{prod_mtime_before:.3f} → {prod_mtime_after:.3f}"
        )
        assert prod_size_after == prod_size_before, (
            f"data/reconcile_shadow_alert_state.json size changed — isolation broken! "
            f"{prod_size_before} → {prod_size_after} bytes"
        )
    elif prod_mtime_before is None:
        # Prod file didn't exist before — it must STILL not exist after our write
        assert not os.path.exists(PROD_ALERT_STATE), (
            f"reconcile_shadow wrote to prod path despite isolation fixture! "
            f"File created at: {PROD_ALERT_STATE}"
        )
