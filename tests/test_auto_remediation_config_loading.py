"""Tests for auto_remediation config file correctness.

Validates that all three config files parse correctly and contain the exact
user-ratified settings from 2026-04-29.
"""
from __future__ import annotations

import fnmatch
from pathlib import Path

import pytest
import yaml

PROJECT_ROOT = Path(__file__).resolve().parent.parent
CFG_PATH = PROJECT_ROOT / "config" / "auto_remediation.yaml"
DENY_PATH = PROJECT_ROOT / "config" / "auto_fix_deny.yaml"
FUNCS_PATH = PROJECT_ROOT / "config" / "safety_critical_functions.txt"


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture(scope="module")
def cfg() -> dict:
    with open(CFG_PATH) as f:
        return yaml.safe_load(f) or {}


@pytest.fixture(scope="module")
def deny() -> dict:
    with open(DENY_PATH) as f:
        return yaml.safe_load(f) or {}


@pytest.fixture(scope="module")
def funcs() -> set[str]:
    with open(FUNCS_PATH) as f:
        return {ln.strip() for ln in f if ln.strip() and not ln.startswith("#")}


# ===========================================================================
# 1. Files parse without error
# ===========================================================================

class TestFilesParse:
    def test_auto_remediation_yaml_parses(self):
        with open(CFG_PATH) as f:
            data = yaml.safe_load(f)
        assert isinstance(data, dict)

    def test_auto_fix_deny_yaml_parses(self):
        with open(DENY_PATH) as f:
            data = yaml.safe_load(f)
        assert isinstance(data, dict)

    def test_safety_critical_functions_txt_readable(self):
        content = FUNCS_PATH.read_text()
        assert len(content.strip()) > 0


# ===========================================================================
# 2. Required top-level keys present
# ===========================================================================

class TestRequiredKeys:
    def test_auto_remediation_top_level_keys(self, cfg):
        # 2026-04-30 Option-C: permanent_assist tier removed (key may exist as empty dict)
        required = {
            "budget", "day1_auto_fix_whitelist", "telegram", "graduation",
            "never_fix", "defaults_applied", "phase",
            "monitor", "verify", "review", "audit_log", "ignore_patterns"
        }
        missing = required - set(cfg.keys())
        assert not missing, f"Missing top-level keys: {missing}"

    def test_deny_yaml_top_level_keys(self, deny):
        required = {"file_globs", "error_class_patterns", "message_patterns",
                    "function_names_blocked"}
        missing = required - set(deny.keys())
        assert not missing, f"Missing deny keys: {missing}"


# ===========================================================================
# 3. Day-1 whitelist has exactly 6 entries
# ===========================================================================

class TestWhitelist:
    def test_whitelist_has_exactly_six_entries(self, cfg):
        wl = cfg.get("day1_auto_fix_whitelist") or []
        assert len(wl) == 6, f"Expected 6 whitelist entries, got {len(wl)}: {wl}"

    def test_whitelist_contains_expected_classes(self, cfg):
        wl = set(cfg.get("day1_auto_fix_whitelist") or [])
        expected = {
            "test_import_error", "stale_fixture_datetime",
            "lint_non_trading_files", "markdown_typos",
            "dashboard_react_build_errors", "healthz_section_logic"
        }
        assert expected == wl, f"Whitelist mismatch: {wl}"


# ===========================================================================
# 4. permanent_assist.paths includes required globs
# ===========================================================================

class TestPermanentAssistRemoved:
    """User Option-C 2026-04-30: permanent_assist tier deleted.

    The key may exist as `{}` (empty dict) or be absent entirely. Either way,
    the previous list of paths must NO LONGER appear.
    """
    def test_permanent_assist_is_empty_or_absent(self, cfg):
        pa = cfg.get("permanent_assist") or {}
        paths = (pa or {}).get("paths") or []
        assert paths == [], f"permanent_assist.paths should be empty, got: {paths}"

    def test_no_old_permanent_assist_paths_remain(self, cfg):
        pa = cfg.get("permanent_assist") or {}
        paths = (pa or {}).get("paths") or []
        forbidden = {"services/**", "research/**", "monitor/**", "systemd/**",
                     "cron/**", "**/*.cron", "config/**", "db/migrations/**", "**/*.sql"}
        leaked = forbidden & set(paths)
        assert not leaked, f"Old permanent_assist paths leaked back in: {leaked}"


# ===========================================================================
# 5. never_fix.paths includes required globs
# ===========================================================================

class TestNeverFixPaths:
    @pytest.fixture(autouse=True)
    def _paths(self, cfg):
        self.paths = set(cfg.get("never_fix", {}).get("paths") or [])

    def test_brokers_present(self):
        assert "brokers/**" in self.paths

    def test_risk_present(self):
        assert "risk/**" in self.paths

    def test_regime_present(self):
        assert "regime/**" in self.paths

    def test_signals_present(self):
        assert "signals/**" in self.paths

    def test_portfolio_present(self):
        assert "portfolio/**" in self.paths

    def test_overlay_present(self):
        assert "overlay/**" in self.paths

    def test_strategies_present(self):
        assert "strategies/**" in self.paths

    def test_core_reconcile_present(self):
        assert "core/reconcile.py" in self.paths


# ===========================================================================
# 6. Every glob in deny.yaml is a syntactically valid fnmatch pattern
# ===========================================================================

class TestDenyYamlGlobs:
    def test_all_file_globs_are_valid_fnmatch(self, deny):
        globs = deny.get("file_globs") or []
        assert len(globs) > 0, "file_globs must not be empty"
        invalid = []
        for g in globs:
            try:
                # fnmatch.translate will raise on truly malformed patterns
                fnmatch.translate(g)
            except Exception as exc:
                invalid.append((g, str(exc)))
        assert not invalid, f"Invalid fnmatch globs: {invalid}"

    def test_deny_has_at_least_thirty_file_globs(self, deny):
        globs = deny.get("file_globs") or []
        assert len(globs) >= 30, f"Expected ≥30 file globs, got {len(globs)}"


# ===========================================================================
# 7. safety_critical_functions.txt has ≥30 unique entries
# ===========================================================================

class TestSafetyCriticalFunctions:
    def test_at_least_thirty_unique_entries(self, funcs):
        assert len(funcs) >= 30, f"Expected ≥30 functions, got {len(funcs)}"

    def test_place_order_present(self, funcs):
        assert "place_order" in funcs

    def test_execute_entry_present(self, funcs):
        assert "_execute_entry" in funcs

    def test_halt_present(self, funcs):
        assert "halt" in funcs

    def test_no_inline_comments(self):
        """Lines in the file must not contain inline # comments (one name per line)."""
        lines = FUNCS_PATH.read_text().splitlines()
        for line in lines:
            if line.strip() and not line.strip().startswith("#"):
                assert "#" not in line, f"Inline comment found: {line!r}"

    def test_no_duplicate_entries(self):
        lines = [ln.strip() for ln in FUNCS_PATH.read_text().splitlines()
                 if ln.strip() and not ln.strip().startswith("#")]
        assert len(lines) == len(set(lines)), "Duplicate entries in safety_critical_functions.txt"


# ===========================================================================
# 8. Budget settings match user spec
# ===========================================================================

class TestBudgetSettings:
    @pytest.fixture(autouse=True)
    def _budget(self, cfg):
        self.budget = cfg.get("budget") or {}

    def test_max_commits_per_day_is_10(self):
        assert self.budget.get("max_commits_per_day") == 10

    def test_reverts_to_halt_is_2(self):
        assert self.budget.get("reverts_to_halt") == 2

    def test_revert_rate_alert_pct_is_15(self):
        assert self.budget.get("revert_rate_alert_pct") == 15

    def test_revert_rate_halt_pct_is_25(self):
        assert self.budget.get("revert_rate_halt_pct") == 25


# ===========================================================================
# 9. Telegram settings match user spec
# ===========================================================================

class TestTelegramSettings:
    @pytest.fixture(autouse=True)
    def _tg(self, cfg):
        self.tg = cfg.get("telegram") or {}

    def test_on_success_is_never(self):
        assert self.tg.get("on_success") == "never"

    def test_on_failure_is_immediate(self):
        assert self.tg.get("on_failure") == "immediate"

    def test_daily_digest_is_false(self):
        assert self.tg.get("daily_digest") is False


# ===========================================================================
# 10. Graduation thresholds match user spec
# ===========================================================================

class TestGraduationThresholds:
    """User Option-C 2026-04-30: graduation wait skipped (0/0)."""

    @pytest.fixture(autouse=True)
    def _grad(self, cfg):
        self.grad = cfg.get("graduation") or {}

    def test_days_of_clean_assist_is_0(self):
        """Was 14 — Option-C dropped to 0 (no wait, Phase 3 day-1)."""
        assist = self.grad.get("assist_to_auto_fix") or {}
        assert assist.get("days_of_clean_assist") == 0

    def test_min_merged_assist_fixes_is_0(self):
        """Was 5 — Option-C dropped to 0 (no merge requirement)."""
        assist = self.grad.get("assist_to_auto_fix") or {}
        assert assist.get("min_merged_assist_fixes") == 0

    def test_demotion_thresholds_intact(self):
        """SAFETY BRAKE — must remain at 5 violations / 60 days per user spec."""
        demote = self.grad.get("auto_fix_to_permanent_assist") or {}
        assert demote.get("scope_violations_threshold") == 5
        assert demote.get("scope_violations_window_days") == 60


# ===========================================================================
# 11. Phase settings
# ===========================================================================

class TestPhaseSettings:
    """User Option-C 2026-04-30: Phase 3 enabled day-1."""

    @pytest.fixture(autouse=True)
    def _phase(self, cfg):
        self.phase = cfg.get("phase") or {}

    def test_current_phase_is_3(self):
        assert self.phase.get("current") == 3

    def test_phase_3_enabled_is_true(self):
        assert self.phase.get("phase_3_enabled") is True


# ===========================================================================
# 12. ignore_patterns non-empty and includes expected noise strings
# ===========================================================================

class TestIgnorePatterns:
    def test_ignore_patterns_non_empty(self, cfg):
        patterns = cfg.get("ignore_patterns") or []
        assert len(patterns) >= 4

    def test_circuit_breaker_in_ignore(self, cfg):
        patterns = cfg.get("ignore_patterns") or []
        assert any("Circuit breaker" in p for p in patterns)

    def test_execution_blocked_halted_in_ignore(self, cfg):
        patterns = cfg.get("ignore_patterns") or []
        assert any("Execution blocked: HALTED" in p for p in patterns)

# ===========================================================================
# 13. Capital-affecting + recursive protection in NEVER list (Option-C)
# ===========================================================================

class TestNeverListCapitalAffectingAdditions:
    """User Option-C 2026-04-30: extra NEVER additions for capital-affecting
    configs and recursive protection of the auto-remediation system itself."""

    @pytest.fixture(autouse=True)
    def _globs(self, deny):
        self.globs = set(deny.get("file_globs") or [])

    def test_config_active_glob_blocked(self):
        assert "config/active/**" in self.globs

    def test_config_versions_glob_blocked(self):
        assert "config/versions/**" in self.globs

    def test_config_schema_py_blocked(self):
        assert "config/schema.py" in self.globs

    def test_config_price_arbiter_blocked(self):
        assert "config/price_arbiter.json" in self.globs

    def test_config_heartbeat_blocked(self):
        assert "config/heartbeat.json" in self.globs

    def test_global_risk_blocked(self):
        assert "config/global_risk.json" in self.globs

    def test_recursive_protection_auto_remediation_yaml(self):
        assert "config/auto_remediation.yaml" in self.globs

    def test_recursive_protection_auto_fix_classes_yaml(self):
        assert "config/auto_fix_classes.yaml" in self.globs

    def test_recursive_protection_auto_fix_deny_yaml(self):
        assert "config/auto_fix_deny.yaml" in self.globs

    def test_recursive_protection_safety_critical_functions(self):
        assert "config/safety_critical_functions.txt" in self.globs


class TestPhase3ActiveExpectations:
    """Sanity checks that Phase 3 is wired through the system after Option-C."""

    def test_phase_3_enabled_propagates_to_auto_merger(self):
        """core/auto_merger._load_phase_3_state() must return True from current config."""
        from core.auto_merger import _load_phase_3_state
        assert _load_phase_3_state() is True

    def test_triage_classifier_loads_phase_3_true(self):
        """TriageClassifier must read phase_3_enabled=True from config."""
        from core.triage import TriageClassifier
        clf = TriageClassifier()
        assert clf._phase_3_enabled is True

    def test_triage_classifier_no_permanent_assist_globs(self):
        """With permanent_assist removed, classifier loads empty list."""
        from core.triage import TriageClassifier
        clf = TriageClassifier()
        assert clf._permanent_assist_globs == []
