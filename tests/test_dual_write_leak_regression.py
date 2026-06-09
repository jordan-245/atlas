"""Regression tests for dual-write leak fixes (Task #192).

Covers commit 3e3d53a5 (reconcile_ledger+positions). Protects against re-regression of:

- Bug A: reconcile_ledger hardcoding strategy='reconciled' instead of
         calling _lookup_strategy() in the record_trade_entry call.
- Bug B: reconcile_ledger filtering out state-file-only tickers (e.g. XLY)
         that are held by the market but not in the universe definition.
- Bug C: reconcile_positions --fix writing JSON only, not SQLite
         (dual-write to atlas_db.record_trade_entry was missing).

Test layout
-----------
  TestReconcileLedgerUsesRealStrategy   (Bug A — source shape checks)
  TestLookupStrategyPriority            (Bug A — functional unit tests)
  TestReconcileLedgerAcceptsStateFileTickers (Bug B — source shape checks)
  TestReconcilePositionsFixWritesSQLite (Bug C — source ordering checks)
"""
from __future__ import annotations

import json
import logging
import sys
from pathlib import Path
from unittest.mock import patch

import pytest

PROJECT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT))

import db.atlas_db as _adb
from db.atlas_db import init_db


# ─── Shared fixtures ──────────────────────────────────────────────────────────

@pytest.fixture(autouse=True)
def _isolate_db(tmp_path, monkeypatch):
    """Point atlas_db at a throw-away temp DB so tests never touch production."""
    db_path = str(tmp_path / "test_dual_write.db")
    monkeypatch.setattr(_adb, "_db_path_override", db_path)
    init_db()
    yield
    # Restore to ensure subsequent test modules are not affected
    monkeypatch.setattr(_adb, "_db_path_override", None)


# ═══════════════════════════════════════════════════════════════════════════════
# Test 1 — Bug A: reconcile_ledger must use _lookup_strategy in the backfill
# ═══════════════════════════════════════════════════════════════════════════════

class TestReconcileLedgerUsesRealStrategy:
    """Source shape checks that protect Bug A from regressing.

    Bug A (AMD strategy drift): before the fix, reconcile_ledger passed
    strategy="reconciled" literally to record_trade_entry, permanently
    losing the real strategy name (e.g. 'momentum_breakout').  After the
    fix, record_trade_entry receives the result of _lookup_strategy().
    """

    def test_record_trade_entry_uses_lookup_strategy_not_hardcoded(self):
        """record_trade_entry strategy= arg comes from a variable, not a literal.

        AST-based shape check (robust to variable-binding refactors):
          1. Parse reconcile_ledger.py.
          2. Find the record_trade_entry() call in section 4 (backfill block).
          3. Assert the strategy= keyword argument is an ast.Name node
             (a variable reference), not an ast.Constant (hardcoded string).
          4. Assert the variable is named _backfill_strategy.
          5. Assert _backfill_strategy is assigned from _lookup_strategy().

        This replaces the brittle 400-char raw-string window that broke when
        _lookup_strategy() was moved to a variable binding above the call.
        """
        import ast as _ast

        src = (PROJECT / "scripts" / "reconcile_ledger.py").read_text()
        tree = _ast.parse(src)
        lines = src.splitlines()

        # Locate section 4 start line (1-indexed)
        section4_line = next(
            i + 1
            for i, line in enumerate(lines)
            if "# 4. Broker has position NOT in ledger" in line
        )

        # ── Step 1: find the record_trade_entry call in section 4 ────────────
        class _RecordTradeFinder(_ast.NodeVisitor):
            def __init__(self, min_line):
                self.min_line = min_line
                self.found_calls = []

            def visit_Call(self, node):
                func = node.func
                name = (
                    func.attr if isinstance(func, _ast.Attribute) else
                    func.id if isinstance(func, _ast.Name) else ""
                )
                if name == "record_trade_entry" and node.lineno >= self.min_line:
                    self.found_calls.append(node)
                self.generic_visit(node)

        finder = _RecordTradeFinder(section4_line)
        finder.visit(tree)

        assert finder.found_calls, (
            "Bug A: no record_trade_entry() call found in section 4 "
            "(after '# 4. Broker has position NOT in ledger')"
        )
        call = finder.found_calls[0]

        # ── Step 2: strategy= kwarg must be a Name, not a Constant ───────────
        strategy_kw = next(
            (kw for kw in call.keywords if kw.arg == "strategy"), None
        )
        assert strategy_kw is not None, (
            "Bug A: record_trade_entry() in section 4 must have a strategy= kwarg"
        )
        assert isinstance(strategy_kw.value, _ast.Name), (
            f"Bug A: strategy= must be a variable (ast.Name), got "
            f"{type(strategy_kw.value).__name__!r} — "
            "hardcoded literal detected; use _lookup_strategy() via a variable binding"
        )
        assert strategy_kw.value.id == "_backfill_strategy", (
            f"Bug A: strategy= variable must be '_backfill_strategy', "
            f"got '{strategy_kw.value.id}'"
        )

        # ── Step 3: _backfill_strategy must be assigned from _lookup_strategy ─
        class _AssignmentFinder(_ast.NodeVisitor):
            def __init__(self):
                self.found = False

            def visit_Assign(self, node):
                for target in node.targets:
                    if isinstance(target, _ast.Name) and target.id == "_backfill_strategy":
                        if isinstance(node.value, _ast.Call):
                            func = node.value.func
                            fname = (
                                func.attr if isinstance(func, _ast.Attribute) else
                                func.id if isinstance(func, _ast.Name) else ""
                            )
                            if fname == "_lookup_strategy":
                                self.found = True
                self.generic_visit(node)

        av = _AssignmentFinder()
        av.visit(tree)
        assert av.found, (
            "Bug A: _backfill_strategy must be assigned from _lookup_strategy() "
            "somewhere in reconcile_ledger.py — binding not found"
        )

    def test_strategy_reconciled_not_hardcoded_in_backfill(self):
        """record_trade_entry call must NOT contain the literal strategy='reconciled'.

        Ensures the old broken pattern cannot reappear silently.
        """
        src = (PROJECT / "scripts" / "reconcile_ledger.py").read_text()

        backfill_idx = src.index("# 4. Broker has position NOT in ledger")
        record_idx = src.index("record_trade_entry(", backfill_idx)
        call_block = src[record_idx: record_idx + 400]

        assert 'strategy="reconciled"' not in call_block, (
            'Bug A: strategy="reconciled" must NOT be hardcoded in the '
            "record_trade_entry call; use _lookup_strategy() instead"
        )
        assert "strategy='reconciled'" not in call_block, (
            "Bug A: strategy='reconciled' must NOT be hardcoded in the "
            "record_trade_entry call; use _lookup_strategy() instead"
        )

    def test_lookup_strategy_function_exists_in_module(self):
        """_lookup_strategy helper must be defined in reconcile_ledger.py.

        Guards against the helper being renamed or deleted, which would
        silently re-introduce Bug A.
        """
        src = (PROJECT / "scripts" / "reconcile_ledger.py").read_text()
        assert "def _lookup_strategy(" in src, (
            "Bug A: _lookup_strategy() helper must be defined in "
            "scripts/reconcile_ledger.py"
        )


# ═══════════════════════════════════════════════════════════════════════════════
# Test 2 — Bug A: _lookup_strategy priority logic (unit tests)
# ═══════════════════════════════════════════════════════════════════════════════

class TestLookupStrategyPriority:
    """Functional unit tests for _lookup_strategy's three-tier priority.

    Priority: broker state file (strategy != 'unknown') > plan file scan
              > 'reconciled' fallback (last resort).

    These tests directly import the helper and assert its return value.
    """

    def test_case_a_returns_state_strategy_when_non_unknown(self):
        """Case A: state_positions contains a real (non-unknown) strategy.

        The helper must return that strategy immediately without scanning
        plan files or falling back.
        """
        from scripts.reconcile_ledger import _lookup_strategy

        state_positions = {
            "AMD": {
                "strategy": "momentum_breakout",
                "shares": 2,
                "entry_price": 178.5,
            }
        }
        result = _lookup_strategy("AMD", "sp500", state_positions)
        assert result == "momentum_breakout", (
            "Case A: non-unknown strategy from state_positions must be returned directly"
        )

    def test_case_a_does_not_return_unknown_strategy(self, tmp_path):
        """Case A guard: strategy='unknown' in state must NOT be returned.

        When the state file says 'unknown', the helper must fall through
        to plan files (Case B) or 'reconciled' fallback (Case C).
        """
        from scripts.reconcile_ledger import _lookup_strategy

        # No plan files → will hit 'reconciled' fallback
        (tmp_path / "plans").mkdir()

        with patch("scripts.reconcile_ledger.PROJECT", tmp_path):
            result = _lookup_strategy(
                "AMD", "sp500", {"AMD": {"strategy": "unknown"}}
            )

        assert result != "unknown", (
            "Case A: strategy='unknown' must not be returned; "
            "must fall through to plan scan or 'reconciled'"
        )
        assert result == "reconciled", (
            "Case A (fall-through): should reach 'reconciled' when state is "
            "'unknown' and no plan files exist"
        )

    def test_case_b_falls_back_to_plan_file_when_state_unknown(self, tmp_path):
        """Case B: state has 'unknown' → plan file scan returns real strategy.

        Creates a tmp plan file with AMD → mtf_momentum and patches PROJECT
        so _lookup_strategy reads it.
        """
        from scripts.reconcile_ledger import _lookup_strategy

        plans_dir = tmp_path / "plans"
        plans_dir.mkdir()
        plan_file = plans_dir / "plan_sp500_20260410.json"
        plan_file.write_text(
            json.dumps(
                {
                    "proposed_entries": [
                        {
                            "ticker": "AMD",
                            "strategy": "mtf_momentum",
                            "entry_price": 178.5,
                        }
                    ]
                }
            )
        )

        with patch("scripts.reconcile_ledger.PROJECT", tmp_path):
            result = _lookup_strategy(
                "AMD", "sp500", {"AMD": {"strategy": "unknown"}}
            )

        assert result == "mtf_momentum", (
            "Case B: strategy from plan file must be returned when state has 'unknown'"
        )

    def test_case_b_uses_newest_plan_first(self, tmp_path):
        """Case B: multiple plan files → newest (sorted descending) takes priority.

        An older plan (04-01) has 'old_strategy', a newer plan (04-10) has
        'new_strategy'. The helper must pick the newer one.
        """
        from scripts.reconcile_ledger import _lookup_strategy

        plans_dir = tmp_path / "plans"
        plans_dir.mkdir()

        (plans_dir / "plan_sp500_20260401.json").write_text(
            json.dumps(
                {"proposed_entries": [{"ticker": "AMD", "strategy": "old_strategy"}]}
            )
        )
        (plans_dir / "plan_sp500_20260410.json").write_text(
            json.dumps(
                {"proposed_entries": [{"ticker": "AMD", "strategy": "new_strategy"}]}
            )
        )

        with patch("scripts.reconcile_ledger.PROJECT", tmp_path):
            result = _lookup_strategy("AMD", "sp500", {})

        assert result == "new_strategy", (
            "Case B: newest plan file (sorted descending) must take priority "
            "over older plans"
        )

    def test_case_c_fallback_returns_reconciled_with_warning(self, tmp_path, caplog):
        """Case C: no state strategy + no plan match → 'reconciled' + WARNING logged.

        This is the last-resort fallback.  A WARNING must be emitted so that
        audit tooling can find positions with unresolved strategies.
        """
        from scripts.reconcile_ledger import _lookup_strategy

        # Empty plans dir — no matching files
        (tmp_path / "plans").mkdir()

        with caplog.at_level(logging.WARNING):
            with patch("scripts.reconcile_ledger.PROJECT", tmp_path):
                result = _lookup_strategy("AMD", "sp500", {})

        assert result == "reconciled", (
            "Case C: must return 'reconciled' when all lookups fail"
        )
        # A warning must be logged so auditors can detect this
        warning_messages = [r.message for r in caplog.records
                            if r.levelno >= logging.WARNING]
        assert any("reconciled" in msg for msg in warning_messages), (
            "Case C: a WARNING containing 'reconciled' must be logged "
            "when the fallback is reached"
        )


# ═══════════════════════════════════════════════════════════════════════════════
# Test 3 — Bug B: state-file tickers accepted even when outside universe
# ═══════════════════════════════════════════════════════════════════════════════

class TestReconcileLedgerAcceptsStateFileTickers:
    """Source shape checks that protect Bug B from regressing.

    Bug B (XLY excluded): before the fix, the broker-position filter used
    only `universe_tickers`.  Tickers tracked in `live_{market}.json` but
    absent from the universe definition (e.g. sector ETFs like XLY) were
    silently skipped → never backfilled.  After the fix, the allow-set is
    the UNION of universe_tickers and state_tickers.
    """

    def test_source_computes_state_tickers_from_state_file(self):
        """reconcile_ledger.py must derive state_tickers from the live JSON file.

        Verifies that the variable `state_tickers` exists and is populated
        from the `live_{market_id}.json` path.
        """
        src = (PROJECT / "scripts" / "reconcile_ledger.py").read_text()
        assert "state_tickers" in src, (
            "Bug B: 'state_tickers' variable must be computed from "
            "brokers/state/live_{market_id}.json"
        )
        # The state file path must reference live_{market_id}.json
        assert "live_{market_id}.json" in src, (
            "Bug B: code must load the live_{market_id}.json state file "
            "to build state_tickers"
        )

    def test_source_allow_set_is_union_of_universe_and_state(self):
        """Broker filter allow-set must be (universe_tickers or set()) | state_tickers.

        This exact expression (or logically equivalent) is required so that
        state-file-only tickers like XLY pass the filter.
        """
        src = (PROJECT / "scripts" / "reconcile_ledger.py").read_text()
        assert "| state_tickers" in src, (
            "Bug B: allow-set must include '| state_tickers' (union operator) "
            "so that state-file-only tickers are not filtered out"
        )
        assert "(universe_tickers or set()) | state_tickers" in src, (
            "Bug B: allow-set expression must be exactly "
            "'(universe_tickers or set()) | state_tickers'"
        )

    def test_source_broker_filter_uses_allow_not_universe_alone(self):
        """Broker position filter must use _allow, not universe_tickers directly.

        Verifies that: (1) `_allow` is defined, (2) `_allow` is constructed
        from the union, (3) the broker_map filter uses `in _allow`.
        """
        src = (PROJECT / "scripts" / "reconcile_ledger.py").read_text()

        # _allow must be defined
        assert "_allow" in src, (
            "Bug B: _allow variable must be defined to hold the combined allow-set"
        )

        # _allow must be used in the broker_map filter
        assert "in _allow" in src, (
            "Bug B: broker position filter must use 'in _allow' "
            "(not 'in universe_tickers' alone)"
        )

        # Ordering: _allow must be defined before being used in the filter
        allow_def_idx = src.index("_allow =")
        in_allow_idx = src.index("in _allow")
        assert allow_def_idx < in_allow_idx, (
            "Bug B: _allow must be defined before it is used in the "
            "broker position filter"
        )


# ═══════════════════════════════════════════════════════════════════════════════
# Test 4 — Bug C: reconcile_positions --fix must dual-write to SQLite
# ═══════════════════════════════════════════════════════════════════════════════

class TestReconcilePositionsFixWritesSQLite:
    """Source ordering checks that protect Bug C from regressing.

    Bug C (JSON-only fix): before the fix, reconcile_positions --fix called
    save_internal_state() but never wrote to SQLite.  After the fix, the
    same block also calls atlas_db.record_trade_entry() for each new position,
    guarded by an existence check and wrapped in try/except (non-fatal).
    """

    def test_fix_block_ordering_save_before_record_trade_entry(self):
        """fix block: save_internal_state must come before record_trade_entry.

        Verifies the required call ordering within the
        `if fix and result["discrepancies"] and not dry_run:` block:
          1. save_internal_state(  — preserves old behaviour
          2. record_trade_entry(   — new dual-write (added by Bug C fix)
        """
        src = (PROJECT / "scripts" / "reconcile_positions.py").read_text()

        fix_block_idx = src.index(
            'if fix and result["discrepancies"] and not dry_run:'
        )
        save_idx = src.index("save_internal_state(", fix_block_idx)
        dw_idx = src.index("record_trade_entry(", save_idx)

        assert fix_block_idx < save_idx < dw_idx, (
            "Bug C: within the fix block, save_internal_state must be called "
            "BEFORE record_trade_entry. "
            f"Indices: fix_block={fix_block_idx}, save={save_idx}, "
            f"record_trade_entry={dw_idx}"
        )

    def test_fix_block_dual_write_wrapped_in_try_except(self):
        """record_trade_entry dual-write must be inside try/except (non-fatal).

        The dual-write is a best-effort operation — JSON is the source of
        truth for live positions.  A failure here must not crash the fix.
        """
        src = (PROJECT / "scripts" / "reconcile_positions.py").read_text()

        fix_block_idx = src.index(
            'if fix and result["discrepancies"] and not dry_run:'
        )
        dw_idx = src.index("record_trade_entry(", fix_block_idx)

        # The last `try:` before the record_trade_entry call must be within
        # the fix block (index > fix_block_idx)
        try_idx = src.rindex("try:", 0, dw_idx)
        assert try_idx > fix_block_idx, (
            "Bug C: record_trade_entry dual-write must be inside a try/except "
            "block so that DB failures are non-fatal"
        )

    def test_fix_block_existence_check_before_insert(self):
        """fix block: record_trade_entry must be guarded by an existence check.

        AST-based check (robust to variable-name changes like _existing->existing):
          1. Parse reconcile_positions.py.
          2. Locate the fix block: if fix and result["discrepancies"] and not dry_run:
          3. Within it, find an If node whose orelse contains record_trade_entry().
             This proves record_trade_entry is in the else-branch of an existence guard.
          4. Verify a SQL execute() call with "status='open'" appears in the same
             fix block (proves the guard queries the DB, not just a local variable).

        This replaces the brittle "_existing" variable-name string check that
        broke when the variable was renamed to "existing" (no underscore).
        """
        import ast as _ast

        src = (PROJECT / "scripts" / "reconcile_positions.py").read_text()
        tree = _ast.parse(src)
        lines = src.splitlines()

        FIX_BLOCK_MARKER = 'if fix and result["discrepancies"] and not dry_run:'
        fix_block_line = next(
            i + 1
            for i, line in enumerate(lines)
            if FIX_BLOCK_MARKER in line
        )

        # ── Step 1: record_trade_entry must be in an else: branch ────────────
        # Walk all If nodes that start at or after fix_block_line.
        # A guarded insert means: exists an If node where record_trade_entry()
        # appears anywhere in node.orelse (the else-branch), NOT node.body.
        class _IfElseGuardFinder(_ast.NodeVisitor):
            def __init__(self, min_line):
                self.min_line = min_line
                self.has_guarded_insert = False

            @staticmethod
            def _has_record_trade_entry(nodes):
                """True if any node (recursively) is a record_trade_entry call."""
                for node in nodes:
                    for child in _ast.walk(node):
                        if isinstance(child, _ast.Call):
                            func = child.func
                            name = (
                                func.attr if isinstance(func, _ast.Attribute) else
                                func.id if isinstance(func, _ast.Name) else ""
                            )
                            if name == "record_trade_entry":
                                return True
                return False

            def visit_If(self, node):
                if node.lineno >= self.min_line and node.orelse:
                    if self._has_record_trade_entry(node.orelse):
                        self.has_guarded_insert = True
                self.generic_visit(node)

        guard_finder = _IfElseGuardFinder(fix_block_line)
        guard_finder.visit(tree)

        assert guard_finder.has_guarded_insert, (
            "Bug C: record_trade_entry must be in the else: branch of an "
            "existence check (if <existing>: ... else: record_trade_entry(...)). "
            "Direct unconditional INSERT detected — would create duplicates on "
            "repeated --fix runs."
        )

        # ── Step 2: SQL query with status='open' must precede the INSERT ─────
        # Walk AST for Constant string nodes (and f-string parts) that contain
        # status='open'. Fragile variable names are irrelevant here.
        class _SqlStatusOpenFinder(_ast.NodeVisitor):
            def __init__(self):
                self.found = False

            def _check_str(self, s):
                if "status='open'" in s or 'status="open"' in s:
                    self.found = True

            def visit_Constant(self, node):
                if isinstance(node.value, str):
                    self._check_str(node.value)

            def visit_JoinedStr(self, node):
                # f-string: walk Constant parts
                for part in node.values:
                    if isinstance(part, _ast.Constant) and isinstance(part.value, str):
                        self._check_str(part.value)
                self.generic_visit(node)

        sql_finder = _SqlStatusOpenFinder()
        sql_finder.visit(tree)

        assert sql_finder.found, (
            "Bug C: fix block must contain a SQL execute() call with "
            "WHERE status='open' to query existing open trades before inserting. "
            "Unconditional INSERT without DB existence check detected."
        )


