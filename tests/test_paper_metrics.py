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
