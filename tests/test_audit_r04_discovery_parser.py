"""Tests for R-04: _browse_with_pi tolerant parser + pi CLI NDJSON parsing fix.

Root cause documented in scripts/investigate_discovery.py:
  Pi CLI ``--mode json`` outputs NDJSON (newline-delimited JSON events), not a
  single JSON document.  ``_run_pi`` was calling ``json.loads(full_stdout)``
  which always fails for NDJSON → returned ``{"error": "json parse failed"}``.
  ``_browse_with_pi`` then saw the error key and returned [] for ALL computer-use
  sources (Wed=SSRN, Fri=Quantpedia, Sat=blog).

Fixes applied (research/discovery/discovery.py):
  1. ``_extract_assistant_text_from_ndjson`` — parses NDJSON and extracts the
     final assistant ``"text"`` block from the last ``turn_end`` event.
  2. ``_run_pi`` — uses NDJSON extraction as primary fallback instead of raw
     ``json.loads`` directly; also tries code-fence and bare JSON-array patterns
     on the extracted assistant text.
  3. ``_browse_with_pi`` — tolerant parser handles all result shapes (list,
     dict with various keys, dict with JSON-string values, plain string with
     embedded array); logs 1000-char snippet on 0-paper result; fixes the
     ``source_type`` key bug (was ``source.get("type","")`` → now
     ``source.get("source","")``).

Test coverage:
  T1  dict with "papers" key                   → 1 paper
  T2  bare list                                → 2 papers
  T3  dict with "result" key (list)            → 1 paper
  T3b dict with "result" key (JSON string)     → 1 paper  (pi-wrapper shape)
  T4  string with embedded JSON array          → 1 paper
  T5a empty dict                               → [] + warning snippet
  T5b empty list                               → [] + warning snippet
  T5c prose string (no JSON)                   → [] + warning snippet
  T6  error dict {"error": "timeout"}          → []
  T6b error dict {"error": "json parse failed"}→ []
  T7  list with mixed non-dict items           → only dicts returned
  T8  NDJSON extraction — well-formed stream   → correct text
  T9  NDJSON extraction — last turn wins       → last turn text returned
  T10 NDJSON extraction — missing turn_end     → "" (no crash)
  T11 NDJSON extraction — thinking blocks skipped → text block returned
  T12 _run_pi integration: NDJSON → list       (uses real fixture)
  T13 _run_pi integration: garbage stdout → error dict
  T14 fixture file: real pi NDJSON → 1 paper   (uses captured fixture)
"""
from __future__ import annotations

import json
import logging
import sys
from pathlib import Path
from unittest.mock import patch

import pytest

# Ensure atlas root is on sys.path
ATLAS_ROOT = Path(__file__).resolve().parent.parent
if str(ATLAS_ROOT) not in sys.path:
    sys.path.insert(0, str(ATLAS_ROOT))

from research.discovery.discovery import (
    _browse_with_pi,
    _extract_assistant_text_from_ndjson,
    _run_pi,
)

# ─── Shared fixtures ─────────────────────────────────────────────────────────

SAMPLE_PAPER = {"title": "Momentum Effect", "url": "https://example.com/momentum"}
SAMPLE_PAPERS = [
    SAMPLE_PAPER,
    {"title": "Mean Reversion", "url": "https://example.com/mr"},
]

BLOG_SOURCE = {
    "source": "blog",
    "method": "computer_use",
    "name": "Alpha Architect",
    "url": "https://alphaarchitect.com/blog/",
}

FIXTURE_DIR = Path(__file__).parent / "fixtures" / "discovery"


def _make_minimal_prompts(monkeypatch, tmp_path: Path) -> None:
    """Create a minimal browse_blog.md so _browse_with_pi doesn't bail before _run_pi."""
    prompts_dir = tmp_path / "prompts"
    prompts_dir.mkdir(parents=True)
    content = (
        "Source: {source}\nQueries: {queries}\n"
        "PapersDir: {papers_dir}\nSeenURLs: {seen_urls_file}\n"
        "Return a JSON array."
    )
    (prompts_dir / "browse_blog.md").write_text(content)

    from research.discovery import discovery as disc_mod
    monkeypatch.setattr(disc_mod, "PROMPTS_DIR", prompts_dir)
    monkeypatch.setattr(disc_mod, "PAPERS_DIR", tmp_path / "papers")
    monkeypatch.setattr(disc_mod, "SEEN_URLS_FILE", tmp_path / "seen_urls.txt")


def _ndjson(*assistant_texts: str) -> str:
    """Build a minimal pi CLI NDJSON stream with one or more assistant turns."""
    lines = [
        '{"type":"session","version":3}',
        '{"type":"agent_start"}',
    ]
    for text in assistant_texts:
        content_block = json.dumps({"type": "text", "text": text})
        turn_end_msg = json.dumps({
            "role": "assistant",
            "content": [json.loads(content_block)],
        })
        lines.append(json.dumps({
            "type": "turn_end",
            "message": json.loads(turn_end_msg),
        }))
    lines.append('{"type":"agent_end","messages":[]}')
    return "\n".join(lines)


# ─── T1-T7: _browse_with_pi tolerant parser ──────────────────────────────────

class TestBrowseWithPiParser:
    """_browse_with_pi returns the correct list for each pi result shape."""

    # T1 — dict with "papers" key
    def test_t1_dict_with_papers_key(self, monkeypatch, tmp_path):
        _make_minimal_prompts(monkeypatch, tmp_path)
        result = {"papers": [SAMPLE_PAPER], "summary": "z"}
        with patch("research.discovery.discovery._run_pi", return_value=result):
            papers = _browse_with_pi(BLOG_SOURCE)
        assert len(papers) == 1
        assert papers[0]["title"] == "Momentum Effect"

    # T2 — bare list
    def test_t2_bare_list(self, monkeypatch, tmp_path):
        _make_minimal_prompts(monkeypatch, tmp_path)
        with patch("research.discovery.discovery._run_pi", return_value=SAMPLE_PAPERS):
            papers = _browse_with_pi(BLOG_SOURCE)
        assert len(papers) == 2

    # T3 — dict with "result" key (list value)
    def test_t3_dict_with_result_key_list(self, monkeypatch, tmp_path):
        _make_minimal_prompts(monkeypatch, tmp_path)
        result = {"result": [SAMPLE_PAPER]}
        with patch("research.discovery.discovery._run_pi", return_value=result):
            papers = _browse_with_pi(BLOG_SOURCE)
        assert len(papers) == 1
        assert papers[0]["url"] == SAMPLE_PAPER["url"]

    # T3b — dict with "result" key whose value is a JSON-encoded string
    def test_t3b_dict_with_result_key_json_string(self, monkeypatch, tmp_path):
        """Pi wrapper shape: {"result": "[{...}]"} — value is a JSON-encoded string."""
        _make_minimal_prompts(monkeypatch, tmp_path)
        result = {"result": json.dumps([SAMPLE_PAPER])}
        with patch("research.discovery.discovery._run_pi", return_value=result):
            papers = _browse_with_pi(BLOG_SOURCE)
        assert len(papers) == 1
        assert papers[0]["title"] == SAMPLE_PAPER["title"]

    # T4 — string with embedded JSON array (model added prose prefix/suffix)
    def test_t4_string_with_embedded_json_array(self, monkeypatch, tmp_path):
        _make_minimal_prompts(monkeypatch, tmp_path)
        raw = f"Here are the papers I found:\n{json.dumps([SAMPLE_PAPER])}\nDone."
        with patch("research.discovery.discovery._run_pi", return_value=raw):
            papers = _browse_with_pi(BLOG_SOURCE)
        assert len(papers) == 1
        assert papers[0]["title"] == SAMPLE_PAPER["title"]

    # T5a — empty dict
    def test_t5a_empty_dict_returns_empty_list_with_warning(
        self, monkeypatch, tmp_path, caplog
    ):
        _make_minimal_prompts(monkeypatch, tmp_path)
        with patch("research.discovery.discovery._run_pi", return_value={}):
            with caplog.at_level(logging.WARNING, logger="discovery"):
                papers = _browse_with_pi(BLOG_SOURCE)
        assert papers == []
        assert any("0 papers" in r.message for r in caplog.records)

    # T5b — empty list
    def test_t5b_empty_list_returns_empty_list_with_warning(
        self, monkeypatch, tmp_path, caplog
    ):
        _make_minimal_prompts(monkeypatch, tmp_path)
        with patch("research.discovery.discovery._run_pi", return_value=[]):
            with caplog.at_level(logging.WARNING, logger="discovery"):
                papers = _browse_with_pi(BLOG_SOURCE)
        assert papers == []
        assert any("0 papers" in r.message for r in caplog.records)

    # T5c — prose string (no JSON)
    def test_t5c_garbage_string_returns_empty_list_with_snippet(
        self, monkeypatch, tmp_path, caplog
    ):
        _make_minimal_prompts(monkeypatch, tmp_path)
        with patch(
            "research.discovery.discovery._run_pi",
            return_value="some prose without JSON",
        ):
            with caplog.at_level(logging.WARNING, logger="discovery"):
                papers = _browse_with_pi(BLOG_SOURCE)
        assert papers == []
        warning_msgs = [r.message for r in caplog.records if r.levelno >= logging.WARNING]
        assert any("0 papers" in m for m in warning_msgs)
        assert any("some prose" in m for m in warning_msgs)  # snippet is logged

    # T6 — error dict {"error": "timeout"}
    def test_t6_error_dict_returns_empty_list(self, monkeypatch, tmp_path, caplog):
        _make_minimal_prompts(monkeypatch, tmp_path)
        with patch(
            "research.discovery.discovery._run_pi",
            return_value={"error": "timeout"},
        ):
            with caplog.at_level(logging.WARNING, logger="discovery"):
                papers = _browse_with_pi(BLOG_SOURCE)
        assert papers == []
        assert any("timeout" in r.message for r in caplog.records)

    # T6b — error dict {"error": "json parse failed"}
    def test_t6b_json_parse_failed_error_dict(self, monkeypatch, tmp_path):
        _make_minimal_prompts(monkeypatch, tmp_path)
        result = {"error": "json parse failed", "raw": "raw pi output"}
        with patch("research.discovery.discovery._run_pi", return_value=result):
            papers = _browse_with_pi(BLOG_SOURCE)
        assert papers == []

    # T7 — list with mixed types; only dicts returned
    def test_t7_list_with_non_dict_items_filtered(self, monkeypatch, tmp_path):
        _make_minimal_prompts(monkeypatch, tmp_path)
        mixed = [SAMPLE_PAPER, "not a dict", 42, None, {"title": "P2"}]
        with patch("research.discovery.discovery._run_pi", return_value=mixed):
            papers = _browse_with_pi(BLOG_SOURCE)
        assert len(papers) == 2
        assert all(isinstance(p, dict) for p in papers)

    # Additional key variants via parametrize
    @pytest.mark.parametrize(
        "key",
        ["papers", "result", "items", "data", "results"],
    )
    def test_dict_key_variants(self, monkeypatch, tmp_path, key):
        _make_minimal_prompts(monkeypatch, tmp_path)
        result = {key: [SAMPLE_PAPER]}
        with patch("research.discovery.discovery._run_pi", return_value=result):
            papers = _browse_with_pi(BLOG_SOURCE)
        assert len(papers) == 1, f"Expected 1 paper via key={key!r}"

    # source_type key bug fix: source.get("source") not source.get("type")
    def test_source_type_key_uses_source_field(self, monkeypatch, tmp_path):
        """SSRN source with 'source':'ssrn' key should route to browse_ssrn.md (or fallback)."""
        # Create both prompts so we can verify routing
        prompts_dir = tmp_path / "prompts"
        prompts_dir.mkdir(parents=True)
        content = (
            "Source: {source}\nQueries: {queries}\n"
            "PapersDir: {papers_dir}\nSeenURLs: {seen_urls_file}\n"
        )
        (prompts_dir / "browse_blog.md").write_text(content)
        (prompts_dir / "browse_ssrn.md").write_text(content + "SSRN-specific")

        from research.discovery import discovery as disc_mod
        monkeypatch.setattr(disc_mod, "PROMPTS_DIR", prompts_dir)
        monkeypatch.setattr(disc_mod, "PAPERS_DIR", tmp_path / "papers")
        monkeypatch.setattr(disc_mod, "SEEN_URLS_FILE", tmp_path / "seen_urls.txt")

        ssrn_source = {
            "source": "ssrn",
            "method": "computer_use",
            "name": "SSRN",
        }
        captured_prompts = []

        def mock_run_pi(prompt, **kwargs):
            captured_prompts.append(prompt)
            return [SAMPLE_PAPER]

        with patch("research.discovery.discovery._run_pi", side_effect=mock_run_pi):
            papers = _browse_with_pi(ssrn_source)

        assert len(papers) == 1
        # The SSRN-specific content should be in the prompt used
        assert captured_prompts, "Expected _run_pi to be called"
        assert "SSRN-specific" in captured_prompts[0], (
            "SSRN source should use browse_ssrn.md prompt"
        )


# ─── T8-T11: _extract_assistant_text_from_ndjson ─────────────────────────────

class TestExtractAssistantTextFromNdjson:
    """_extract_assistant_text_from_ndjson correctly parses pi NDJSON streams."""

    # T8 — well-formed NDJSON stream with JSON array in text block
    def test_t8_extracts_json_array_from_turn_end(self):
        ndjson = _ndjson('[{"title": "T1", "url": "http://x"}]')
        text = _extract_assistant_text_from_ndjson(ndjson)
        assert text == '[{"title": "T1", "url": "http://x"}]'

    # T9 — multiple turns; last one wins
    def test_t9_returns_last_turn_end_text(self):
        ndjson = _ndjson("first turn", "final answer")
        text = _extract_assistant_text_from_ndjson(ndjson)
        assert text == "final answer"

    # T10 — no turn_end → returns empty string (no crash)
    def test_t10_no_turn_end_returns_empty_string(self):
        ndjson = '{"type":"session"}\n{"type":"agent_start"}\n{"type":"agent_end","messages":[]}'
        text = _extract_assistant_text_from_ndjson(ndjson)
        assert text == ""

    # T11 — thinking blocks are skipped; text block is returned
    def test_t11_skips_thinking_blocks_returns_text_block(self):
        thinking_block = {
            "type": "thinking",
            "thinking": "Let me reason...",
            "thinkingSignature": "abc123",
        }
        text_block = {"type": "text", "text": '[{"title": "Paper"}]'}
        turn_end = {
            "type": "turn_end",
            "message": {
                "role": "assistant",
                "content": [thinking_block, text_block],
            },
        }
        ndjson = json.dumps(turn_end)
        text = _extract_assistant_text_from_ndjson(ndjson)
        assert text == '[{"title": "Paper"}]'

    # T11b — empty text block skipped; subsequent non-empty text returned
    def test_t11b_skips_empty_text_block(self):
        turn_end = {
            "type": "turn_end",
            "message": {
                "role": "assistant",
                "content": [
                    {"type": "text", "text": "  "},   # whitespace only — skip
                    {"type": "text", "text": "good"},  # first non-empty
                ],
            },
        }
        ndjson = json.dumps(turn_end)
        text = _extract_assistant_text_from_ndjson(ndjson)
        assert text == "good"

    # Garbage lines between valid NDJSON lines are tolerated
    def test_tolerates_garbage_lines(self):
        ndjson_lines = [
            "not json at all",
            '{"type":"session"}',
            "another garbage line ///",
            json.dumps({
                "type": "turn_end",
                "message": {
                    "role": "assistant",
                    "content": [{"type": "text", "text": "result"}],
                },
            }),
        ]
        text = _extract_assistant_text_from_ndjson("\n".join(ndjson_lines))
        assert text == "result"


# ─── T12-T14: _run_pi NDJSON integration ─────────────────────────────────────

class TestRunPiNdjson:
    """_run_pi correctly parses NDJSON stdout into a Python object."""

    def _patch_call_pi(self, side_effects):
        """Return a context manager that patches utils.pi_subprocess.call_pi."""
        return patch("utils.pi_subprocess.call_pi", side_effect=side_effects)

    # T12 — NDJSON with JSON array → returns list
    def test_t12_ndjson_with_json_array_returns_list(self):
        ndjson_stdout = _ndjson(json.dumps(SAMPLE_PAPERS))
        # Auth check call returns "ok"; main call returns ndjson
        with self._patch_call_pi(["ok", ndjson_stdout]):
            result = _run_pi("test prompt", allowed_tools="")
        assert isinstance(result, list), f"Expected list, got {type(result)}: {result}"
        assert len(result) == 2

    # T13 — garbage stdout → returns error dict
    def test_t13_garbage_stdout_returns_error_dict(self):
        with self._patch_call_pi(["ok", "complete garbage no json here"]):
            result = _run_pi("test prompt", allowed_tools="")
        assert isinstance(result, dict)
        assert "error" in result

    # T14 — real captured fixture file → extracts 1 paper
    def test_t14_real_fixture_file(self):
        fixture_path = FIXTURE_DIR / "sample_pi_output_ndjson.txt"
        if not fixture_path.exists():
            pytest.skip("Fixture file not found — run capture step first")
        ndjson_stdout = fixture_path.read_text()
        with self._patch_call_pi(["ok", ndjson_stdout]):
            result = _run_pi("test prompt", allowed_tools="")
        # The fixture contains [{"title": "Test Paper", "url": "http://example.com"}]
        assert isinstance(result, list), f"Expected list, got {type(result)}: {result}"
        assert len(result) == 1
        assert result[0].get("title") == "Test Paper"
        assert result[0].get("url") == "http://example.com"

    # NDJSON with prose + embedded JSON array → bare-array extraction
    def test_ndjson_with_prose_plus_array(self):
        prose_with_json = f"Found these papers:\n{json.dumps([SAMPLE_PAPER])}\nDone."
        ndjson_stdout = _ndjson(prose_with_json)
        with self._patch_call_pi(["ok", ndjson_stdout]):
            result = _run_pi("test prompt", allowed_tools="")
        assert isinstance(result, list)
        assert len(result) == 1

    # Fallback: non-NDJSON single JSON doc still works
    def test_single_json_doc_still_works(self):
        single_doc = json.dumps({"papers": [SAMPLE_PAPER]})
        with self._patch_call_pi(["ok", single_doc]):
            result = _run_pi("test prompt", allowed_tools="")
        assert isinstance(result, dict)
        assert "papers" in result
