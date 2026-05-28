"""Tests for the knowledge-layer extractors (Phase 1).

Covers:
  - paper_metadata: sha256, arxiv-id parsing, source insertion, idempotency.
  - spec_to_claims: source resolution (URL match, title match, new src-ref-*),
    universe normalisation, claim insertion, idempotency, dedup across runs.

No LLM calls.  PDFs are synthetic (a few bytes -- we only hash + parse filename).

Run:
    python3 -m pytest tests/test_extractors.py -v
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

PROJECT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT))

import db.atlas_db as atlas_db_module
from db.atlas_db import init_db
from db import knowledge as kn
from research.discovery.extractors import paper_metadata, spec_to_claims


# ═══════════════════════════════════════════════════════════════════════════════
# Fixtures
# ═══════════════════════════════════════════════════════════════════════════════

@pytest.fixture
def db_file(tmp_path):
    return tmp_path / "test_extractors.db"


@pytest.fixture(autouse=True)
def db(db_file, monkeypatch):
    """Isolated DB per test."""
    monkeypatch.setattr(atlas_db_module, "DB_PATH", db_file)
    monkeypatch.setattr(atlas_db_module, "_db_path_override", None)
    init_db()
    yield db_file


def _fake_pdf(path: Path, content_seed: str | None = None) -> Path:
    """Write a small file with a PDF header so the filename ends in .pdf.

    paper_metadata.py only sha256s the file and parses the filename; no real
    PDF parser is invoked.  Default content_seed is the filename, ensuring
    every file has a distinct sha256 (real-world papers also have distinct
    contents -- identical sha collisions would trip the UNIQUE(sha256)
    constraint on sources).
    """
    seed = content_seed if content_seed is not None else path.name
    path.write_bytes(b"%PDF-1.4\n" + seed.encode("utf-8"))
    return path


# ═══════════════════════════════════════════════════════════════════════════════
# paper_metadata
# ═══════════════════════════════════════════════════════════════════════════════

class TestParseArxivId:
    def test_modern_format(self):
        assert paper_metadata._parse_arxiv_id("2401.12345.pdf") == "2401.12345"

    def test_modern_format_with_version(self):
        assert paper_metadata._parse_arxiv_id("2401.12345v2.pdf") == "2401.12345"

    def test_old_format_with_category(self):
        # arxiv_api.py turns 'q-fin/0801001' into 'q-fin_0801001.pdf'
        assert paper_metadata._parse_arxiv_id("q-fin_0801001.pdf") == "q-fin/0801001"

    def test_non_arxiv_filename_returns_none(self):
        assert paper_metadata._parse_arxiv_id("some_random_paper.pdf") is None
        assert paper_metadata._parse_arxiv_id("notes.pdf") is None


class TestExtractOnePaper:
    def test_arxiv_pdf_inserts_source(self, tmp_path):
        pdf = _fake_pdf(tmp_path / "2401.12345.pdf")
        source_id, was_new = paper_metadata.extract_one(pdf, atlas_root=tmp_path)
        assert was_new is True
        assert source_id == "src-arxiv-2401.12345"

        s = kn.get_source(source_id)
        assert s is not None
        assert s["kind"] == "paper"
        assert s["venue"] == "arxiv"
        assert s["url"] == "https://arxiv.org/abs/2401.12345"
        assert s["title"] == "arxiv:2401.12345"
        assert s["sha256"] is not None and len(s["sha256"]) == 64
        assert s["local_path"].endswith("2401.12345.pdf")

    def test_non_arxiv_pdf_uses_sha_prefix(self, tmp_path):
        pdf = _fake_pdf(tmp_path / "random_paper.pdf", "unique-content")
        source_id, was_new = paper_metadata.extract_one(pdf, atlas_root=tmp_path)
        assert was_new is True
        assert source_id.startswith("src-sha-")
        assert len(source_id) == len("src-sha-") + 8

    def test_idempotent_extract_one(self, tmp_path):
        pdf = _fake_pdf(tmp_path / "2401.12345.pdf")
        sid1, new1 = paper_metadata.extract_one(pdf, atlas_root=tmp_path)
        sid2, new2 = paper_metadata.extract_one(pdf, atlas_root=tmp_path)
        assert sid1 == sid2
        assert new1 is True
        assert new2 is False


class TestExtractAllPapers:
    def test_empty_dir_returns_empty(self, tmp_path):
        empty = tmp_path / "papers"
        empty.mkdir()
        assert paper_metadata.extract_all(empty, atlas_root=tmp_path) == []

    def test_missing_dir_returns_empty(self, tmp_path):
        assert paper_metadata.extract_all(tmp_path / "nonexistent",
                                           atlas_root=tmp_path) == []

    def test_processes_all_pdfs(self, tmp_path):
        papers = tmp_path / "papers"
        papers.mkdir()
        _fake_pdf(papers / "2401.11111.pdf", "a")
        _fake_pdf(papers / "2401.22222.pdf", "b")
        _fake_pdf(papers / "2401.33333.pdf", "c")
        results = paper_metadata.extract_all(papers, atlas_root=tmp_path)
        assert len(results) == 3
        assert all(r["was_new"] for r in results)
        assert all(r["error"] is None for r in results)

    def test_rerun_inserts_zero_new(self, tmp_path):
        papers = tmp_path / "papers"
        papers.mkdir()
        _fake_pdf(papers / "2401.11111.pdf")
        _fake_pdf(papers / "2401.22222.pdf")
        first = paper_metadata.extract_all(papers, atlas_root=tmp_path)
        second = paper_metadata.extract_all(papers, atlas_root=tmp_path)
        assert sum(r["was_new"] for r in first) == 2
        assert sum(r["was_new"] for r in second) == 0


# ═══════════════════════════════════════════════════════════════════════════════
# spec_to_claims
# ═══════════════════════════════════════════════════════════════════════════════

def _spec(strategy_name="rsi_volume_reversal",
          url="https://arxiv.org/abs/2401.12345",
          title="Volume-Confirmed RSI Reversals",
          authors="Smith et al. (2024)",
          markets=None,
          parameters=None,
          description="Buys oversold stocks showing volume confirmation."):
    return {
        "strategy_name": strategy_name,
        "description": description,
        "entry_rules": ["RSI(14) < 30", "Close > SMA(200)"],
        "exit_rules": ["Stop loss: entry - 2.0 * ATR(14)"],
        "indicators": ["RSI(14)", "ATR(14)", "SMA(200)"],
        "timeframe": "daily",
        "markets": markets if markets is not None else ["US equities", "S&P 500"],
        "parameters": parameters if parameters is not None else {
            "rsi_period": 14, "rsi_oversold": 30,
        },
        "risk_management": {"stop_loss": "ATR-based"},
        "reference": {"url": url, "title": title, "authors": authors},
    }


class TestUniverseNormalisation:
    def test_sp500_aliases(self):
        assert spec_to_claims._normalise_universe(["S&P 500"]) == "sp500"
        assert spec_to_claims._normalise_universe(["US equities"]) == "sp500"
        assert spec_to_claims._normalise_universe(["us large cap"]) == "sp500"

    def test_sector_etfs(self):
        assert spec_to_claims._normalise_universe(["Sector ETFs"]) == "sector_etfs"

    def test_unknown_market_returns_none(self):
        assert spec_to_claims._normalise_universe(["Crypto"]) is None
        assert spec_to_claims._normalise_universe([]) is None
        assert spec_to_claims._normalise_universe(None) is None

    def test_takes_first_matching(self):
        # Crypto first -> no match.  Then S&P 500 -> sp500.
        assert spec_to_claims._normalise_universe(["Crypto", "S&P 500"]) == "sp500"


class TestSourceResolution:
    def test_creates_new_source_when_no_match(self):
        sid, _ = spec_to_claims.extract_one_spec(_spec(), n=0)
        assert sid is not None
        s = kn.get_source(spec_to_claims.extract_one_spec(_spec(), n=1)[1])
        assert s is not None
        assert s["title"] == "Volume-Confirmed RSI Reversals"
        assert s["url"] == "https://arxiv.org/abs/2401.12345"
        # authors stored as JSON list
        assert s["authors"] == ["Smith et al. (2024)"]

    def test_matches_existing_source_by_url(self, tmp_path):
        # Pre-seed a source via paper_metadata.
        pdf = _fake_pdf(tmp_path / "2401.12345.pdf")
        seeded_id, _ = paper_metadata.extract_one(pdf, atlas_root=tmp_path)

        # Spec with the same URL must reuse seeded source, not create new.
        _, src_via_spec = spec_to_claims.extract_one_spec(_spec(), n=0)
        assert src_via_spec == seeded_id

    def test_matches_existing_source_by_title_when_no_url(self):
        kn.insert_source(
            id="src-manual-1",
            kind="paper",
            title="Hand-Entered Paper",
        )
        spec = _spec(url="", title="Hand-Entered Paper")
        _, src = spec_to_claims.extract_one_spec(spec, n=0)
        assert src == "src-manual-1"


class TestClaimInsertion:
    def test_inserts_shell_claim_with_null_metrics(self):
        claim_id, source_id = spec_to_claims.extract_one_spec(_spec(), n=0)
        assert claim_id is not None
        c = kn.get_claim(claim_id)
        assert c is not None
        assert c["strategy"] == "rsi_volume_reversal"
        assert c["universe"] == "sp500"
        assert c["claimed_sharpe"] is None
        assert c["claimed_max_dd_pct"] is None
        assert c["extraction_confidence"] == "low"

        # notes carries the structured parameters for later LLM upgrade.
        notes = json.loads(c["notes"])
        assert notes["parameters"] == {"rsi_period": 14, "rsi_oversold": 30}
        assert notes["timeframe"] == "daily"
        assert "S&P 500" in notes["markets_raw"]

    def test_skips_spec_without_strategy_name(self):
        bad = _spec()
        del bad["strategy_name"]
        cid, sid = spec_to_claims.extract_one_spec(bad, n=0)
        assert cid is None and sid is None

    def test_skips_spec_without_reference(self):
        bad = _spec()
        bad["reference"] = {}
        cid, sid = spec_to_claims.extract_one_spec(bad, n=0)
        # _is_implementable requires reference to be a dict -- empty dict is dict,
        # but _find_or_create_source returns None for empty -> claim is skipped.
        assert cid is None and sid is None

    def test_unknown_universe_stored_as_null(self):
        spec = _spec(markets=["Cryptocurrency", "DEX tokens"])
        claim_id, _ = spec_to_claims.extract_one_spec(spec, n=0)
        c = kn.get_claim(claim_id)
        assert c["universe"] is None


class TestSpecsFileProcessing:
    def test_extract_specs_file(self, tmp_path):
        specs_path = tmp_path / "specs_2026-05-28.json"
        specs_path.write_text(json.dumps([
            _spec(strategy_name="strat_a"),
            _spec(strategy_name="strat_b",
                  url="https://arxiv.org/abs/2402.99999",
                  title="Strat B"),
        ]), encoding="utf-8")

        results = spec_to_claims.extract_specs_file(specs_path)
        assert len(results) == 2
        assert {r["strategy"] for r in results} == {"strat_a", "strat_b"}
        assert all(not r["skipped"] for r in results)

    def test_idempotent_extract_all(self, tmp_path):
        specs_dir = tmp_path / "specs"
        specs_dir.mkdir()
        (specs_dir / "specs_2026-05-28.json").write_text(
            json.dumps([_spec(strategy_name="strat_x")]),
            encoding="utf-8",
        )

        first = spec_to_claims.extract_all(specs_dir)
        second = spec_to_claims.extract_all(specs_dir)

        # Both runs see the same row count.  Second run should not create
        # additional claim rows (INSERT OR IGNORE on stable id).
        assert len(first) == 1
        assert len(second) == 1

        claims_total = kn.list_claims(strategy="strat_x", status=None)
        assert len(claims_total) == 1

    def test_malformed_specs_file_does_not_raise(self, tmp_path):
        specs_dir = tmp_path / "specs"
        specs_dir.mkdir()
        (specs_dir / "specs_broken.json").write_text("not valid json {",
                                                      encoding="utf-8")
        (specs_dir / "specs_wrong_shape.json").write_text(
            '{"not": "a list"}', encoding="utf-8"
        )
        results = spec_to_claims.extract_all(specs_dir)
        assert results == []


# ═══════════════════════════════════════════════════════════════════════════════
# End-to-end: paper + spec share a source
# ═══════════════════════════════════════════════════════════════════════════════

class TestEndToEnd:
    def test_paper_then_spec_unifies_on_source(self, tmp_path):
        # 1. Paper extractor lands first (sha256 + arxiv id).
        pdf = _fake_pdf(tmp_path / "2401.12345.pdf")
        seeded_id, _ = paper_metadata.extract_one(pdf, atlas_root=tmp_path)

        # 2. Spec extractor sees a reference.url matching the seeded source.
        spec = _spec(url="https://arxiv.org/abs/2401.12345",
                     title="The Real Title")
        claim_id, source_id_from_spec = spec_to_claims.extract_one_spec(spec, n=0)
        assert source_id_from_spec == seeded_id

        # 3. Only one sources row.
        sources = kn.list_sources(limit=10)
        assert len(sources) == 1
        assert sources[0]["id"] == seeded_id

        # 4. The source row still has the paper extractor's placeholder title
        # (spec extractor doesn't overwrite existing rows).  Title upgrade is a
        # Phase 1.5 concern.
        assert sources[0]["title"] == "arxiv:2401.12345"
