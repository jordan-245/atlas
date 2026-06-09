"""Tests for the Phase 1.5 LLM metric extractor.

No real pi CLI calls -- a fake call_pi_fn is injected.  pdftotext is also
monkeypatched so tests don't depend on poppler-utils being installed in CI.

Run:
    python3 -m pytest tests/test_paper_metrics.py -v
"""
from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any

import pytest

PROJECT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT))

import db.atlas_db as atlas_db_module
from db.atlas_db import init_db
from db import knowledge as kn
from research.discovery.extractors import paper_metrics


# ═══════════════════════════════════════════════════════════════════════════════
# Fixtures
# ═══════════════════════════════════════════════════════════════════════════════

@pytest.fixture
def db_file(tmp_path):
    return tmp_path / "test_paper_metrics.db"


@pytest.fixture(autouse=True)
def db(db_file, monkeypatch):
    monkeypatch.setattr(atlas_db_module, "DB_PATH", db_file)
    monkeypatch.setattr(atlas_db_module, "_db_path_override", None)
    init_db()
    yield db_file


@pytest.fixture
def atlas_root(tmp_path):
    """Each test gets its own atlas_root so PDFs live under it."""
    root = tmp_path / "atlas_root"
    root.mkdir()
    (root / "research" / "discovery" / "papers").mkdir(parents=True)
    return root


def _seed_shell_claim(
    atlas_root: Path,
    *,
    source_id: str = "src-arxiv-2401.12345",
    pdf_name: str = "2401.12345.pdf",
    strategy: str = "rsi_volume_reversal",
    parameters: dict | None = None,
    write_pdf: bool = True,
) -> tuple[str, str, Path]:
    """Insert a source + shell claim, optionally write a fake PDF on disk.

    Returns (claim_id, source_id, pdf_path).
    """
    pdf_path = atlas_root / "research" / "discovery" / "papers" / pdf_name
    if write_pdf:
        pdf_path.write_bytes(b"%PDF-1.4\nfake")
    local_path = pdf_path.relative_to(atlas_root).as_posix() if write_pdf else None

    kn.insert_source(
        id=source_id,
        kind="paper",
        title=f"Paper: {strategy}",
        url=f"https://arxiv.org/abs/{source_id.split('-', 2)[-1]}",
        local_path=local_path,
        extracted_by="test",
    )
    claim_id = f"clm-{source_id}-{strategy}-0"
    kn.insert_claim(
        id=claim_id,
        source_id=source_id,
        strategy=strategy,
        universe="sp500",
        notes=json.dumps({
            "parameters": parameters or {"rsi_period": 14},
            "timeframe": "daily",
            "markets_raw": ["S&P 500"],
            "description": "shell",
        }),
        extraction_confidence="low",
    )
    return claim_id, source_id, pdf_path


def _fake_pi(response_dict: dict | str):
    """Build a fake call_pi_fn that returns NDJSON wrapping the given response.

    If response_dict is a dict, it's serialised as JSON and embedded as the
    final assistant text block of one turn_end event (matches real pi output).
    If it's a str, it's wrapped verbatim as the text block payload.
    """
    if isinstance(response_dict, dict):
        text = json.dumps(response_dict)
    else:
        text = response_dict
    turn_end = {
        "type": "turn_end",
        "message": {
            "role": "assistant",
            "content": [{"type": "text", "text": text}],
        },
    }
    raw = json.dumps(turn_end) + "\n"

    def _call(*_a, **_kw) -> str:
        return raw
    return _call


def _patch_pdftotext(monkeypatch, text: str = "Synthetic paper text.") -> None:
    """Replace pdf_to_text with a stub.  Tests don't need poppler-utils."""
    def _fake(_pdf_path, *, max_chars: int = 20_000):
        return text[:max_chars]
    monkeypatch.setattr(paper_metrics, "pdf_to_text", _fake)
    monkeypatch.setattr(paper_metrics, "pdftotext_available", lambda: True)


# ═══════════════════════════════════════════════════════════════════════════════
# parse_llm_response
# ═══════════════════════════════════════════════════════════════════════════════

class TestParseLlmResponse:
    def test_bare_json_object(self):
        raw = json.dumps({"found": True, "claimed_sharpe": 1.4})
        out = paper_metrics.parse_llm_response(raw)
        assert out == {"found": True, "claimed_sharpe": 1.4}

    def test_ndjson_envelope(self):
        payload = {"found": True, "claimed_sharpe": 1.4, "claimed_max_dd_pct": 12.0}
        raw = _fake_pi(payload)()
        out = paper_metrics.parse_llm_response(raw)
        assert out == payload

    def test_code_fenced_inside_assistant_text(self):
        # Assistant text contains markdown fences -- we should still parse.
        payload = {"found": True, "claimed_sharpe": 0.5}
        text = "Here's the result:\n```json\n" + json.dumps(payload) + "\n```\nDone."
        raw = _fake_pi(text)()
        out = paper_metrics.parse_llm_response(raw)
        assert out == payload

    def test_bare_object_inside_prose(self):
        payload = {"found": True, "claimed_sharpe": 0.9}
        text = "Per the abstract: " + json.dumps(payload) + " (Sharpe of 0.9)."
        raw = _fake_pi(text)()
        out = paper_metrics.parse_llm_response(raw)
        assert out == payload

    def test_empty_string_returns_none(self):
        assert paper_metrics.parse_llm_response("") is None
        assert paper_metrics.parse_llm_response("   ") is None

    def test_garbage_returns_none(self):
        # Valid NDJSON event but no parseable JSON in the text block.
        raw = _fake_pi("just some prose, no json at all")()
        assert paper_metrics.parse_llm_response(raw) is None

    def test_non_dict_top_level_returns_none(self):
        # A JSON array at top level is not what we want.
        raw = json.dumps([1, 2, 3])
        assert paper_metrics.parse_llm_response(raw) is None


# ═══════════════════════════════════════════════════════════════════════════════
# render_prompt
# ═══════════════════════════════════════════════════════════════════════════════

class TestRenderPrompt:
    def test_substitutions(self, monkeypatch):
        prompt = paper_metrics.render_prompt(
            strategy_name="donchian_breakout",
            source_title="Turtle Trading Rules",
            pdf_text="Sharpe ratio of 1.4 reported.",
            parameters={"lookback": 20},
        )
        assert "donchian_breakout" in prompt
        assert "Turtle Trading Rules" in prompt
        assert "Sharpe ratio of 1.4 reported." in prompt
        assert "\"lookback\": 20" in prompt
        # The template's literal placeholders should all be gone.
        assert "{strategy_name}" not in prompt
        assert "{pdf_text}" not in prompt
        assert "{parameters_json}" not in prompt


# ═══════════════════════════════════════════════════════════════════════════════
# list_shell_claims
# ═══════════════════════════════════════════════════════════════════════════════

class TestListShellClaims:
    def test_only_returns_null_metric_claims(self, atlas_root):
        # Shell claim with no metrics.
        _seed_shell_claim(atlas_root, source_id="src-a", strategy="strat_a")
        # Populated claim -- should NOT appear in the shell list.
        _seed_shell_claim(atlas_root, source_id="src-b", strategy="strat_b",
                          pdf_name="2401.22222.pdf")
        kn.update_claim_metrics(
            id=f"clm-src-b-strat_b-0",
            claimed_sharpe=1.0,
        )
        rows = kn.list_shell_claims()
        assert len(rows) == 1
        assert rows[0]["strategy"] == "strat_a"

    def test_excludes_dismissed(self, atlas_root):
        claim_id, _, _ = _seed_shell_claim(atlas_root, source_id="src-c",
                                            strategy="strat_c")
        kn.dismiss_claim(claim_id, reason="test")
        assert kn.list_shell_claims() == []

    def test_require_local_pdf_filter(self, atlas_root):
        # Source without local_path
        kn.insert_source(id="src-no-pdf", kind="paper",
                         title="Reference only", url="https://example.org/p")
        kn.insert_claim(id="clm-no-pdf", source_id="src-no-pdf",
                        strategy="strat_nopdf")
        # Source WITH local_path
        _seed_shell_claim(atlas_root, source_id="src-with-pdf",
                          strategy="strat_withpdf")

        with_filter = kn.list_shell_claims(require_local_pdf=True)
        assert {r["strategy"] for r in with_filter} == {"strat_withpdf"}

        without_filter = kn.list_shell_claims(require_local_pdf=False)
        assert {r["strategy"] for r in without_filter} == {"strat_nopdf", "strat_withpdf"}


# ═══════════════════════════════════════════════════════════════════════════════
# #395 hardening: failed Phase 1.5 extractions must not be re-eligible forever
# ═══════════════════════════════════════════════════════════════════════════════
class TestLowConfidenceFilter:
    """A failed Phase 1.5 attempt leaves the claim with NULL metrics, notes
    prefixed 'phase1.5:' and extraction_confidence='low'.  list_shell_claims /
    extract_pending must exclude these by default (so the cron never retries the
    same not_found claim forever) but include them with the retry flag.  Fresh
    shell claims also start at confidence='low' but carry JSON notes, so they
    are NEVER excluded.
    """

    def _mark_phase15_failed(self, claim_id: str, note: str) -> None:
        """Simulate a prior failed Phase 1.5 attempt on an existing shell claim."""
        kn.update_claim_metrics(
            id=claim_id,
            extraction_confidence="low",
            notes=note,
        )

    def test_fresh_low_confidence_claim_is_listed(self, atlas_root):
        # Fresh shell claim: extraction_confidence='low' + JSON notes (no
        # 'phase1.5:' prefix) -> must still be listed by default.
        _seed_shell_claim(atlas_root, source_id="src-fresh", strategy="strat_fresh")
        rows = kn.list_shell_claims()
        assert {r["strategy"] for r in rows} == {"strat_fresh"}
        # Sanity: the seed really is low-confidence.
        c = kn.get_claim(f"clm-src-fresh-strat_fresh-0")
        assert c["extraction_confidence"] == "low"

    def test_phase15_failed_claim_excluded_by_default(self, atlas_root):
        claim_id, _, _ = _seed_shell_claim(
            atlas_root, source_id="src-failed", strategy="strat_failed")
        self._mark_phase15_failed(
            claim_id, "phase1.5: LLM reported no metrics for this strategy: lit review")
        assert kn.list_shell_claims() == []

    def test_retry_flag_includes_failed_claim(self, atlas_root):
        claim_id, _, _ = _seed_shell_claim(
            atlas_root, source_id="src-failed", strategy="strat_failed")
        self._mark_phase15_failed(claim_id, "phase1.5: LLM reported no metrics")
        rows = kn.list_shell_claims(include_low_confidence=True)
        assert {r["strategy"] for r in rows} == {"strat_failed"}

    def test_mixed_fresh_and_failed(self, atlas_root):
        # One fresh, one failed -> default lists only fresh; retry lists both.
        _seed_shell_claim(atlas_root, source_id="src-fresh", strategy="strat_fresh",
                          pdf_name="2401.11111.pdf")
        failed_id, _, _ = _seed_shell_claim(
            atlas_root, source_id="src-failed", strategy="strat_failed",
            pdf_name="2401.22222.pdf")
        self._mark_phase15_failed(failed_id, "phase1.5: pi error: RuntimeError: boom")

        assert {r["strategy"] for r in kn.list_shell_claims()} == {"strat_fresh"}
        assert {r["strategy"] for r in kn.list_shell_claims(include_low_confidence=True)} \
            == {"strat_fresh", "strat_failed"}

    def test_not_found_extraction_self_excludes_then_retryable(
        self, atlas_root, monkeypatch
    ):
        # End-to-end: a not_found extraction marks the claim; the next default
        # batch no longer picks it up, but the retry flag re-includes it.
        _seed_shell_claim(atlas_root, source_id="src-nf", strategy="strat_nf")
        _patch_pdftotext(monkeypatch)
        joined = kn.list_shell_claims()[0]
        result = paper_metrics.extract_one(
            joined, atlas_root=atlas_root,
            call_pi_fn=_fake_pi({"found": False, "notes": "no backtest"}))
        assert result.reason == "not_found"

        # Default batch is now empty (claim self-excluded), so the cron stops
        # hammering the same claim.
        assert paper_metrics.extract_pending(
            atlas_root=atlas_root, call_pi_fn=_fake_pi({"found": False})) == []
        # Retry flag re-includes it.
        retry = kn.list_shell_claims(include_low_confidence=True)
        assert {r["strategy"] for r in retry} == {"strat_nf"}

    def test_extract_pending_honors_flag(self, atlas_root, monkeypatch):
        # A phase1.5-failed claim is skipped by default extract_pending, but
        # reprocessed when include_low_confidence=True.
        claim_id, _, _ = _seed_shell_claim(
            atlas_root, source_id="src-retry", strategy="strat_retry")
        self._mark_phase15_failed(claim_id, "phase1.5: LLM reported no metrics")
        _patch_pdftotext(monkeypatch)

        # Default: nothing to do (the failed claim is excluded).
        assert paper_metrics.extract_pending(
            atlas_root=atlas_root, call_pi_fn=_fake_pi({"found": True})) == []

        # Retry flag: the claim is reprocessed; this time the LLM finds metrics.
        good = {"found": True, "claimed_sharpe": 1.5,
                "extraction_confidence": "high", "notes": "Table 2"}
        results = paper_metrics.extract_pending(
            atlas_root=atlas_root, include_low_confidence=True,
            call_pi_fn=_fake_pi(good))
        assert len(results) == 1 and results[0].ok
        c = kn.get_claim(claim_id)
        assert c["claimed_sharpe"] == 1.5


# ═══════════════════════════════════════════════════════════════════════════════
# extract_one
# ═══════════════════════════════════════════════════════════════════════════════

class TestExtractOne:
    def _good_response(self, **overrides) -> dict:
        base = {
            "found": True,
            "claimed_sharpe": 1.4,
            "claimed_solo_sharpe": None,
            "claimed_max_dd_pct": 12.5,
            "claimed_trades": 130,
            "claimed_cagr_pct": 8.5,
            "claimed_profit_factor": 1.85,
            "claimed_avg_hold_days": 5.0,
            "period_start": "2010-01-01",
            "period_end": "2023-12-31",
            "extraction_confidence": "high",
            "notes": "Headline from Table 2.",
        }
        base.update(overrides)
        return base

    def test_happy_path_updates_claim(self, atlas_root, monkeypatch):
        claim_id, _, _ = _seed_shell_claim(atlas_root)
        _patch_pdftotext(monkeypatch, text="The paper reports Sharpe 1.4.")

        joined = kn.list_shell_claims()[0]
        result = paper_metrics.extract_one(
            joined,
            atlas_root=atlas_root,
            call_pi_fn=_fake_pi(self._good_response()),
        )

        assert result.ok is True
        assert result.reason == "extracted"
        c = kn.get_claim(claim_id)
        assert c["claimed_sharpe"] == 1.4
        assert c["claimed_max_dd_pct"] == 12.5
        assert c["claimed_trades"] == 130
        assert c["claimed_cagr_pct"] == 8.5
        assert c["period_start"] == "2010-01-01"
        assert c["period_end"] == "2023-12-31"
        assert c["extraction_confidence"] == "high"
        assert c["notes"].startswith("phase1.5:")

    def test_idempotent_via_list_shell(self, atlas_root, monkeypatch):
        # Once a claim has metrics, list_shell_claims should not return it again.
        _seed_shell_claim(atlas_root)
        _patch_pdftotext(monkeypatch)

        joined = kn.list_shell_claims()[0]
        paper_metrics.extract_one(joined, atlas_root=atlas_root,
                                  call_pi_fn=_fake_pi(self._good_response()))
        assert kn.list_shell_claims() == []  # not a shell anymore

    def test_not_found_response_keeps_metrics_null(self, atlas_root, monkeypatch):
        claim_id, _, _ = _seed_shell_claim(atlas_root)
        _patch_pdftotext(monkeypatch)

        joined = kn.list_shell_claims()[0]
        result = paper_metrics.extract_one(
            joined,
            atlas_root=atlas_root,
            call_pi_fn=_fake_pi({"found": False, "notes": "literature review only"}),
        )

        assert result.ok is False
        assert result.reason == "not_found"
        c = kn.get_claim(claim_id)
        # Metrics stay NULL; notes records the LLM verdict.
        assert c["claimed_sharpe"] is None
        assert c["extraction_confidence"] == "low"
        assert "literature review only" in (c["notes"] or "")

    def test_unparseable_response(self, atlas_root, monkeypatch):
        claim_id, _, _ = _seed_shell_claim(atlas_root)
        _patch_pdftotext(monkeypatch)

        joined = kn.list_shell_claims()[0]
        result = paper_metrics.extract_one(
            joined,
            atlas_root=atlas_root,
            call_pi_fn=_fake_pi("absolutely no json here, just rambling"),
        )

        assert result.ok is False
        assert result.reason == "parse_failed"
        c = kn.get_claim(claim_id)
        assert c["claimed_sharpe"] is None
        assert "not parseable" in (c["notes"] or "")

    def test_pi_raises_records_llm_error(self, atlas_root, monkeypatch):
        claim_id, _, _ = _seed_shell_claim(atlas_root)
        _patch_pdftotext(monkeypatch)

        def _exploding_pi(*_a, **_kw):
            raise RuntimeError("simulated pi timeout")

        joined = kn.list_shell_claims()[0]
        result = paper_metrics.extract_one(
            joined,
            atlas_root=atlas_root,
            call_pi_fn=_exploding_pi,
        )

        assert result.ok is False
        assert result.reason == "llm_error"
        c = kn.get_claim(claim_id)
        assert "simulated pi timeout" in (c["notes"] or "")

    def test_pdftotext_missing_short_circuits(self, atlas_root, monkeypatch):
        claim_id, _, _ = _seed_shell_claim(atlas_root)
        monkeypatch.setattr(paper_metrics, "pdftotext_available", lambda: False)

        def _fake_pdf_to_text(_p, *, max_chars=20_000):
            raise RuntimeError("pdftotext not on PATH (install poppler-utils)")
        monkeypatch.setattr(paper_metrics, "pdf_to_text", _fake_pdf_to_text)

        joined = kn.list_shell_claims()[0]
        result = paper_metrics.extract_one(joined, atlas_root=atlas_root,
                                            call_pi_fn=_fake_pi({"found": True}))

        assert result.ok is False
        assert result.skipped is True
        assert result.reason == "pdftotext_missing"
        c = kn.get_claim(claim_id)
        assert "pdftotext unavailable" in (c["notes"] or "")

    def test_pdf_missing_on_disk(self, atlas_root, monkeypatch):
        # Source has a local_path but the file got deleted from the worktree.
        _seed_shell_claim(atlas_root, write_pdf=True)
        # Now delete it post-seed.
        pdf_dir = atlas_root / "research" / "discovery" / "papers"
        for p in pdf_dir.glob("*.pdf"):
            p.unlink()

        # pdf_to_text won't even be called -- extract_one short-circuits.
        joined = kn.list_shell_claims()[0]
        result = paper_metrics.extract_one(joined, atlas_root=atlas_root,
                                            call_pi_fn=_fake_pi({"found": True}))
        assert result.skipped is True
        assert result.reason == "pdf_missing"


# ═══════════════════════════════════════════════════════════════════════════════
# extract_pending (batch)
# ═══════════════════════════════════════════════════════════════════════════════

class TestExtractPending:
    def test_processes_multiple(self, atlas_root, monkeypatch):
        _seed_shell_claim(atlas_root, source_id="src-1", strategy="strat_1",
                          pdf_name="2401.11111.pdf")
        _seed_shell_claim(atlas_root, source_id="src-2", strategy="strat_2",
                          pdf_name="2401.22222.pdf")
        _patch_pdftotext(monkeypatch)

        good = {"found": True, "claimed_sharpe": 1.0,
                "extraction_confidence": "high", "notes": "ok"}
        results = paper_metrics.extract_pending(
            atlas_root=atlas_root,
            call_pi_fn=_fake_pi(good),
        )
        assert len(results) == 2
        assert all(r.ok for r in results)

    def test_pdftotext_missing_aborts_batch_early(self, atlas_root, monkeypatch):
        for n in range(3):
            _seed_shell_claim(atlas_root, source_id=f"src-{n}",
                              strategy=f"strat_{n}",
                              pdf_name=f"2401.{n:05d}.pdf")
        monkeypatch.setattr(paper_metrics, "pdftotext_available", lambda: False)

        def _fake_pdf_to_text(_p, *, max_chars=20_000):
            raise RuntimeError("pdftotext not on PATH (install poppler-utils)")
        monkeypatch.setattr(paper_metrics, "pdf_to_text", _fake_pdf_to_text)

        results = paper_metrics.extract_pending(
            atlas_root=atlas_root,
            call_pi_fn=_fake_pi({"found": True}),
        )
        # 3 claims seeded; first one's missing-pdftotext aborts the batch.
        assert len(results) == 1
        assert results[0].reason == "pdftotext_missing"

    def test_empty_when_no_pending(self, atlas_root):
        results = paper_metrics.extract_pending(atlas_root=atlas_root)
        assert results == []


# ═══════════════════════════════════════════════════════════════════════════════
# #395: source-derived shell claims are visible to extract_paper_metrics
# ═══════════════════════════════════════════════════════════════════════════════
class TestSourceDerivedClaimVisibleToExtractor:
    """The restored flow: a source-derived shell claim (no spec file) with a
    local PDF must be picked up by list_shell_claims and extract_pending."""

    def test_extract_paper_metrics_sees_source_backfilled_claim(
        self, atlas_root, monkeypatch
    ):
        from research.discovery.extractors import paper_metadata, spec_to_claims

        # 1. Ingest a PDF -> source row (no claim yet).
        pdf = atlas_root / "research" / "discovery" / "papers" / "2605.07835v1.pdf"
        pdf.write_bytes(b"%PDF-1.4\nfake paper bytes")
        source_id, _ = paper_metadata.extract_one(pdf, atlas_root=atlas_root)
        assert kn.list_shell_claims(require_local_pdf=True) == []

        # 2. Source-derived backfill creates a shell claim with NULL metrics.
        results = spec_to_claims.extract_claims_from_sources()
        assert len(results) == 1
        claim_id = results[0]["claim_id"]

        # 3. extract_paper_metrics can now SEE the shell claim (local PDF).
        shells = kn.list_shell_claims(require_local_pdf=True)
        assert len(shells) == 1
        assert shells[0]["claim_id"] == claim_id
        assert shells[0]["source_id"] == source_id
        assert shells[0]["local_path"] is not None

        # 4. And the batch extractor processes it end-to-end with a fake LLM.
        _patch_pdftotext(monkeypatch, text="Headline Sharpe 1.2 in Table 1.")
        good = {"found": True, "claimed_sharpe": 1.2,
                "extraction_confidence": "high", "notes": "Table 1"}
        proc = paper_metrics.extract_pending(
            atlas_root=atlas_root, call_pi_fn=_fake_pi(good))
        assert len(proc) == 1 and proc[0].ok is True
        c = kn.get_claim(claim_id)
        assert c["claimed_sharpe"] == 1.2
        # Now populated -> no longer a shell.
        assert kn.list_shell_claims(require_local_pdf=True) == []


# ═══════════════════════════════════════════════════════════════════════════════
# #395 follow-up: source-derived placeholder claims resolve their real strategy
# ═══════════════════════════════════════════════════════════════════════════════
class TestSourceDerivedStrategyResolution:
    """A source-derived shell claim has a 'paper__<slug>' placeholder strategy
    and a notes flag.  The LLM pass must (a) be told to infer the real strategy,
    and (b) persist the resolved strategy + universe + metrics back to the row.
    """

    def _seed_placeholder_claim(self, atlas_root, monkeypatch):
        """Ingest a PDF -> source -> source-derived shell claim (placeholder)."""
        from research.discovery.extractors import paper_metadata, spec_to_claims

        pdf = atlas_root / "research" / "discovery" / "papers" / "2605.07835v1.pdf"
        pdf.write_bytes(b"%PDF-1.4\nfake paper bytes")
        source_id, _ = paper_metadata.extract_one(pdf, atlas_root=atlas_root)
        results = spec_to_claims.extract_claims_from_sources()
        assert len(results) == 1
        claim_id = results[0]["claim_id"]
        # Sanity: starts as a placeholder needing resolution.
        c = kn.get_claim(claim_id)
        assert c["strategy"].startswith("paper__")
        return claim_id, source_id

    def test_needs_resolution_detection(self, atlas_root, monkeypatch):
        claim_id, _ = self._seed_placeholder_claim(atlas_root, monkeypatch)
        shell = kn.list_shell_claims(require_local_pdf=True)[0]
        # Both signals present (placeholder prefix + notes flag).
        assert paper_metrics._needs_strategy_resolution(shell) is True

        # A known/spec-derived claim is NOT flagged for resolution.
        _seed_shell_claim(atlas_root, source_id="src-known",
                          strategy="rsi_volume_reversal",
                          pdf_name="2401.55555.pdf")
        known = [s for s in kn.list_shell_claims(require_local_pdf=True)
                 if s["claim_id"].startswith("clm-src-known")][0]
        assert paper_metrics._needs_strategy_resolution(known) is False

    def test_resolution_block_in_prompt_when_placeholder(self, atlas_root, monkeypatch):
        self._seed_placeholder_claim(atlas_root, monkeypatch)
        shell = kn.list_shell_claims(require_local_pdf=True)[0]
        prompt = paper_metrics.render_prompt(
            strategy_name=shell["strategy"],
            source_title="t",
            pdf_text="body",
            parameters={},
            needs_resolution=paper_metrics._needs_strategy_resolution(shell),
        )
        assert "Strategy Resolution Required" in prompt
        assert "{resolution_block}" not in prompt

        # Known strategy gets the "already known" block instead.
        kept = paper_metrics.render_prompt(
            strategy_name="rsi_volume_reversal", source_title="t",
            pdf_text="body", parameters={}, needs_resolution=False)
        assert "Strategy Already Known" in kept

    def test_resolution_updates_strategy_and_universe(self, atlas_root, monkeypatch):
        claim_id, _ = self._seed_placeholder_claim(atlas_root, monkeypatch)
        _patch_pdftotext(monkeypatch, text="A momentum breakout strategy on US large caps.")

        resp = {
            "found": True,
            "strategy_name": "Momentum Breakout",  # model returns Title Case
            "universe": "S&P 500",                  # paper-language label
            "claimed_sharpe": 1.7,
            "claimed_max_dd_pct": 14.0,
            "extraction_confidence": "high",
            "notes": "Table 3",
        }
        shell = kn.list_shell_claims(require_local_pdf=True)[0]
        result = paper_metrics.extract_one(
            shell, atlas_root=atlas_root, call_pi_fn=_fake_pi(resp))

        assert result.ok is True
        assert result.strategy == "momentum_breakout"  # snake_cased + persisted
        c = kn.get_claim(claim_id)
        assert c["strategy"] == "momentum_breakout"
        assert c["universe"] == "sp500"               # alias-mapped
        assert c["claimed_sharpe"] == 1.7
        assert c["claimed_max_dd_pct"] == 14.0
        assert c["notes"].startswith("phase1.5: resolved strategy -> momentum_breakout")
        # No longer a shell or a placeholder.
        assert kn.list_shell_claims(require_local_pdf=True) == []

    def test_resolution_syncs_contradiction_for_resolved_strategy(
        self, atlas_root, monkeypatch
    ):
        from db.research import upsert_research_best

        claim_id, _ = self._seed_placeholder_claim(atlas_root, monkeypatch)
        # Atlas has already measured the REAL strategy with a much lower Sharpe.
        upsert_research_best(strategy="momentum_breakout", universe="sp500",
                             params={}, solo_sharpe=0.4)
        # Before resolution: placeholder can't match -> no contradiction anywhere.
        assert kn.get_open_contradictions() == []

        _patch_pdftotext(monkeypatch, text="Momentum breakout, Sharpe 1.6.")
        resp = {
            "found": True,
            "strategy_name": "momentum_breakout",
            "universe": "sp500",
            "claimed_sharpe": 1.6,    # |1.6 - 0.4| = 1.2 -> critical
            "extraction_confidence": "high",
            "notes": "Table 1",
        }
        shell = kn.list_shell_claims(require_local_pdf=True)[0]
        paper_metrics.extract_one(shell, atlas_root=atlas_root,
                                  call_pi_fn=_fake_pi(resp))

        # Contradiction now exists for the RESOLVED strategy...
        opens = kn.get_open_contradictions(strategy="momentum_breakout")
        assert len(opens) == 1
        assert opens[0]["severity"] == "critical"
        assert opens[0]["claim_id"] == claim_id
        # ...and none linger under the old placeholder strategy.
        placeholder = kn.get_claim(claim_id)  # strategy now resolved
        assert placeholder["strategy"] == "momentum_breakout"

    def test_found_false_keeps_placeholder_and_no_metrics(self, atlas_root, monkeypatch):
        claim_id, _ = self._seed_placeholder_claim(atlas_root, monkeypatch)
        _patch_pdftotext(monkeypatch, text="A theory paper with no backtest.")

        result = paper_metrics.extract_one(
            kn.list_shell_claims(require_local_pdf=True)[0],
            atlas_root=atlas_root,
            call_pi_fn=_fake_pi({"found": False, "notes": "no backtest"}),
        )
        assert result.ok is False and result.reason == "not_found"
        c = kn.get_claim(claim_id)
        # Strategy stays a placeholder; metrics stay NULL -> retryable shell.
        assert c["strategy"].startswith("paper__")
        assert c["claimed_sharpe"] is None
        # Still detected as needing resolution on a retry (prefix survives).
        assert paper_metrics._needs_strategy_resolution(c) is True

    def test_found_true_but_no_strategy_name_keeps_placeholder(
        self, atlas_root, monkeypatch
    ):
        # Model gave metrics but failed to name the strategy: persist metrics,
        # keep placeholder (placeholder never matches research_best -> safe).
        claim_id, _ = self._seed_placeholder_claim(atlas_root, monkeypatch)
        _patch_pdftotext(monkeypatch, text="Headline Sharpe 1.1.")
        resp = {"found": True, "claimed_sharpe": 1.1,
                "extraction_confidence": "medium", "notes": "abstract"}
        result = paper_metrics.extract_one(
            kn.list_shell_claims(require_local_pdf=True)[0],
            atlas_root=atlas_root, call_pi_fn=_fake_pi(resp))
        assert result.ok is True
        c = kn.get_claim(claim_id)
        assert c["claimed_sharpe"] == 1.1
        assert c["strategy"].startswith("paper__")  # unchanged


class TestSpecDerivedStrategyNotMutated:
    """Spec-derived/known claims must NEVER have their strategy rewritten, even
    if the LLM volunteers a strategy_name in its JSON."""

    def test_known_strategy_unchanged_even_if_llm_returns_name(
        self, atlas_root, monkeypatch
    ):
        claim_id, _, _ = _seed_shell_claim(atlas_root, strategy="rsi_volume_reversal")
        _patch_pdftotext(monkeypatch, text="Some paper text.")
        # LLM (mischievously) returns a different strategy_name.
        resp = {
            "found": True,
            "strategy_name": "totally_different_strategy",
            "universe": "treasury_etfs",
            "claimed_sharpe": 1.3,
            "extraction_confidence": "high",
            "notes": "Table 2",
        }
        result = paper_metrics.extract_one(
            kn.list_shell_claims(require_local_pdf=True)[0],
            atlas_root=atlas_root, call_pi_fn=_fake_pi(resp))

        assert result.ok is True
        assert result.strategy == "rsi_volume_reversal"  # NOT mutated
        c = kn.get_claim(claim_id)
        assert c["strategy"] == "rsi_volume_reversal"
        assert c["universe"] == "sp500"                  # original, not clobbered
        assert c["claimed_sharpe"] == 1.3                # metrics still applied
        # notes uses the plain (non-resolution) prefix.
        assert c["notes"].startswith("phase1.5: Table 2") or \
               c["notes"].startswith("phase1.5: ")
