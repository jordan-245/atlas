"""Spec → claims extractor.

Reads research/discovery/specs/specs_*.json files (produced by the existing
discovery pipeline -- see research/discovery/discovery.py and prompts/extract.md)
and converts each spec entry into a shell claim row.

A "shell claim" has the strategy + source link + parameters populated, but
NULL claimed_sharpe / claimed_max_dd / claimed_cagr / claimed_trades.  The
existing extract.md prompt does not capture performance numbers from papers
-- the LLM metric-extraction pass (Phase 1.5) will UPDATE these rows in
place once that prompt + extractor exists.

Idempotent via deterministic claim ids: clm-<source_id>-<strategy>-<n>.
Re-running on the same specs files produces zero new rows.

Resolves a source row in this order:
  1. Match by reference.url against existing sources (URL stored at insert).
  2. Match by reference.title against existing sources.
  3. Create a new src-ref-<sha8> source row from reference metadata if neither
     of the above hits.

Strategy/universe normalisation:
  - strategy_name from spec is taken as-is (already snake_case per prompts/extract.md).
  - The first market in spec["markets"] is normalised against a small allowlist
    (sp500, sector_etfs, treasury_etfs, commodity_etfs, russell_2000).  Anything
    that doesn't match becomes NULL universe (i.e. "paper unspecified") -- safer
    than guessing.
"""

from __future__ import annotations

import hashlib
import json
import logging
import re
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from db.knowledge import get_source, insert_claim, insert_source, list_sources

logger = logging.getLogger(__name__)

# Map of common paper-language market labels -> Atlas universe keys.
# Conservative: anything not in this table becomes NULL universe.
_UNIVERSE_ALIASES = {
    "sp500": "sp500",
    "s&p500": "sp500",
    "s&p 500": "sp500",
    "s and p 500": "sp500",
    "us equities": "sp500",
    "us large cap": "sp500",
    "us large-cap": "sp500",
    "large cap us": "sp500",
    "sector etfs": "sector_etfs",
    "sector etf": "sector_etfs",
    "select sector spdrs": "sector_etfs",
    "treasury etfs": "treasury_etfs",
    "treasuries": "treasury_etfs",
    "treasury bonds": "treasury_etfs",
    "commodity etfs": "commodity_etfs",
    "commodities": "commodity_etfs",
    "russell 2000": "russell_2000",
    "russell2000": "russell_2000",
    "small cap us": "russell_2000",
    "us small cap": "russell_2000",
}


def _normalise_universe(markets: Optional[List[str]]) -> Optional[str]:
    """Map the first market label to an Atlas universe key, or None if no match."""
    if not markets:
        return None
    for raw in markets:
        if not isinstance(raw, str):
            continue
        key = raw.strip().lower()
        if key in _UNIVERSE_ALIASES:
            return _UNIVERSE_ALIASES[key]
    return None


def _ref_sha8(reference: Dict[str, Any]) -> str:
    """Stable short hash for a reference block (used as fallback source id)."""
    canonical = json.dumps(
        {
            "url": (reference.get("url") or "").strip().lower(),
            "title": (reference.get("title") or "").strip().lower(),
        },
        sort_keys=True,
    )
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()[:8]


def _slugify_strategy(name: str) -> str:
    """Conservative slug for claim-id construction.  Strategy_name is already
    snake_case per extract.md, but we defensively strip non-[a-z0-9_]."""
    return re.sub(r"[^a-z0-9_]+", "", name.lower())


def _find_or_create_source(reference: Dict[str, Any]) -> Optional[str]:
    """Resolve the source_id for a spec's reference block.

    Returns the source_id, or None if reference is too thin to act on.
    """
    if not reference or not isinstance(reference, dict):
        return None

    url = (reference.get("url") or "").strip() or None
    title = (reference.get("title") or "").strip() or None
    if url is None and title is None:
        return None

    # 1) Match by URL among existing sources.
    if url is not None:
        for s in list_sources(limit=10_000):
            if (s.get("url") or "").strip() == url:
                return s["id"]

    # 2) Match by title (exact, case-insensitive) when URL is missing or no hit.
    if title is not None:
        title_lc = title.lower()
        for s in list_sources(limit=10_000):
            if (s.get("title") or "").strip().lower() == title_lc:
                return s["id"]

    # 3) Create a new source row keyed by sha8 of (url+title).
    source_id = f"src-ref-{_ref_sha8(reference)}"
    if get_source(source_id) is not None:
        # Already created in a prior partial run, use it.
        return source_id

    authors_raw = reference.get("authors")
    authors_list: Optional[List[str]] = None
    if isinstance(authors_raw, list):
        authors_list = [str(a) for a in authors_raw]
    elif isinstance(authors_raw, str) and authors_raw.strip():
        authors_list = [authors_raw.strip()]

    insert_source(
        id=source_id,
        kind="paper",
        title=title or (url or source_id),
        url=url,
        authors=authors_list,
        venue=("arxiv" if url and "arxiv.org" in url else None),
        extracted_by="spec_to_claims",
        notes="Created from spec reference block; no PDF on disk for this source.",
    )
    return source_id


def _claim_id(source_id: str, strategy: str, n: int) -> str:
    return f"clm-{source_id}-{_slugify_strategy(strategy)}-{n}"


def _is_implementable(spec: Dict[str, Any]) -> bool:
    """Skip specs missing the fields that make a claim meaningful."""
    return bool(spec.get("strategy_name")) and isinstance(spec.get("reference"), dict)


def extract_one_spec(spec: Dict[str, Any], *, n: int = 0) -> Tuple[Optional[str], Optional[str]]:
    """Process a single spec dict.  Returns (claim_id, source_id) or (None, None) if skipped.

    Idempotent at the claim level: re-running with the same (source_id, strategy, n)
    is a no-op via INSERT OR IGNORE inside db.knowledge.insert_claim.
    """
    if not _is_implementable(spec):
        logger.debug("Skipping spec (missing strategy_name or reference): %r",
                     spec.get("strategy_name"))
        return None, None

    strategy = str(spec["strategy_name"]).strip()
    reference = spec.get("reference", {}) or {}

    source_id = _find_or_create_source(reference)
    if source_id is None:
        logger.warning("Could not resolve source for strategy=%s ref=%r",
                       strategy, reference)
        return None, None

    universe = _normalise_universe(spec.get("markets"))
    claim_id = _claim_id(source_id, strategy, n)
    description = spec.get("description") or ""

    # Stash parameters + rules in the notes column so Phase 1.5 has them when
    # populating claimed_* metric fields.  We keep this compact -- full spec
    # remains on disk in the specs_*.json file.
    notes_payload = {
        "parameters": spec.get("parameters") or {},
        "timeframe": spec.get("timeframe"),
        "markets_raw": spec.get("markets") or [],
        "description": description[:500],
    }

    insert_claim(
        id=claim_id,
        source_id=source_id,
        strategy=strategy,
        universe=universe,
        extraction_confidence="low",  # shell claim -- metrics not yet extracted
        notes=json.dumps(notes_payload, ensure_ascii=False),
    )
    return claim_id, source_id


def extract_specs_file(specs_path: Path) -> List[dict]:
    """Process one specs_*.json file.  Returns one result dict per spec entry."""
    try:
        raw = json.loads(specs_path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError) as exc:
        logger.warning("Failed to read specs file %s: %s", specs_path, exc)
        return []

    if not isinstance(raw, list):
        logger.warning("Specs file %s does not contain a list at the top level", specs_path)
        return []

    results: List[dict] = []
    for n, spec in enumerate(raw):
        if not isinstance(spec, dict):
            continue
        try:
            claim_id, source_id = extract_one_spec(spec, n=n)
            results.append({
                "specs_file": str(specs_path),
                "spec_index": n,
                "strategy": spec.get("strategy_name"),
                "claim_id": claim_id,
                "source_id": source_id,
                "skipped": claim_id is None,
                "error": None,
            })
        except Exception as exc:  # noqa: BLE001 -- keep processing siblings
            logger.warning("Failed extract_one_spec(%s[%d]): %s", specs_path.name, n, exc)
            results.append({
                "specs_file": str(specs_path),
                "spec_index": n,
                "strategy": spec.get("strategy_name"),
                "claim_id": None,
                "source_id": None,
                "skipped": True,
                "error": str(exc),
            })

    return results


def extract_all(specs_dir: Path) -> List[dict]:
    """Process every specs_*.json in specs_dir.  Returns flat list of result dicts."""
    if not specs_dir.exists():
        logger.warning("specs_dir does not exist: %s", specs_dir)
        return []

    out: List[dict] = []
    for path in sorted(specs_dir.glob("specs_*.json")):
        out.extend(extract_specs_file(path))
    return out
