#!/usr/bin/env python3
"""One-shot backfill of the knowledge layer from existing discovery artifacts.

Reads research/discovery/papers/*.pdf -> sources rows.
Reads research/discovery/specs/specs_*.json -> shell claims rows
(plus source rows for references that lack a local PDF).

Source-derived fallback (#395): when no specs_*.json files exist, the spec ->
claim bridge is empty and ingested PDFs never become shell claims.  In that
case the backfill derives ONE shell claim per claim-less source directly from
source/PDF metadata (placeholder strategy 'paper__<source slug>', NULL claimed
metrics) so the Phase 1.5 LLM metric extractor has work to do.  Controlled by
--from-sources {auto,always,never} (default: auto = run only when no specs).

Idempotent.  Safe to re-run -- a second invocation reports zero new rows
(source-derived path sees zero claim-less sources once claims exist).

Run:
    python3 scripts/backfill_knowledge.py                # dry-run, prints counts
    python3 scripts/backfill_knowledge.py --apply        # actually writes
    python3 scripts/backfill_knowledge.py --apply --from-sources always
    python3 scripts/backfill_knowledge.py --apply --db /tmp/fixture.db  # temp DB smoke
    python3 scripts/backfill_knowledge.py --apply --verbose

Outputs a JSON summary to stdout and a detailed log to logs/backfill_knowledge.log.
"""

from __future__ import annotations

import argparse
import json
import logging
import sys
from pathlib import Path

_HERE = Path(__file__).resolve()
_ATLAS_ROOT = _HERE.parent.parent
sys.path.insert(0, str(_ATLAS_ROOT))

PAPERS_DIR = _ATLAS_ROOT / "research" / "discovery" / "papers"
SPECS_DIR = _ATLAS_ROOT / "research" / "discovery" / "specs"
LOG_PATH = _ATLAS_ROOT / "logs" / "backfill_knowledge.log"


def _setup_logging(verbose: bool) -> None:
    LOG_PATH.parent.mkdir(parents=True, exist_ok=True)
    level = logging.DEBUG if verbose else logging.INFO
    logging.basicConfig(
        level=level,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
        handlers=[
            logging.FileHandler(LOG_PATH, encoding="utf-8"),
            logging.StreamHandler(sys.stderr),
        ],
    )


def _summarise_paper_results(rows: list) -> dict:
    new = sum(1 for r in rows if r.get("was_new"))
    existing = sum(1 for r in rows if r.get("source_id") and not r.get("was_new"))
    errors = sum(1 for r in rows if r.get("error"))
    return {
        "files_scanned": len(rows),
        "sources_inserted": new,
        "sources_already_existed": existing,
        "errors": errors,
    }


def _summarise_spec_results(rows: list) -> dict:
    inserted = sum(1 for r in rows if r.get("claim_id") and not r.get("skipped"))
    skipped = sum(1 for r in rows if r.get("skipped"))
    errors = sum(1 for r in rows if r.get("error"))
    distinct_strategies = len({r.get("strategy") for r in rows if r.get("strategy")})
    return {
        "specs_processed": len(rows),
        "claims_inserted_or_existed": inserted,
        "skipped": skipped,
        "errors": errors,
        "distinct_strategies": distinct_strategies,
    }


def _summarise_source_results(rows: list) -> dict:
    # list_sources_without_claims already excludes sources that have a claim, so
    # every considered row is a genuinely-new shell claim.  A re-run sees zero
    # candidates (clean idempotency signal).
    created = sum(1 for r in rows if r.get("claim_id") and not r.get("skipped"))
    skipped = sum(1 for r in rows if r.get("skipped"))
    errors = sum(1 for r in rows if r.get("error"))
    return {
        "sources_considered": len(rows),
        "shell_claims_created": created,
        "skipped": skipped,
        "errors": errors,
    }


def _count_spec_files(specs_dir: Path) -> int:
    return len(list(specs_dir.glob("specs_*.json"))) if specs_dir.exists() else 0


def _should_run_from_sources(mode: str, spec_file_count: int) -> bool:
    """Resolve the --from-sources policy into a boolean.

    'auto'   -> run only when there are no specs_*.json files (the gap case).
    'always' -> always run the source-derived path.
    'never'  -> never run it.
    """
    if mode == "always":
        return True
    if mode == "never":
        return False
    return spec_file_count == 0  # auto


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--apply", action="store_true",
                        help="Actually write to the DB (default: dry-run)")
    parser.add_argument("--verbose", action="store_true",
                        help="DEBUG-level logging")
    parser.add_argument("--papers-dir", default=str(PAPERS_DIR),
                        help=f"Override papers/ dir (default: {PAPERS_DIR})")
    parser.add_argument("--specs-dir", default=str(SPECS_DIR),
                        help=f"Override specs/ dir (default: {SPECS_DIR})")
    parser.add_argument("--from-sources", choices=("auto", "always", "never"),
                        default="auto",
                        help="Derive shell claims directly from claim-less sources. "
                             "'auto' (default) runs only when no specs_*.json exist; "
                             "'always' forces it; 'never' disables it.")
    parser.add_argument("--include-no-pdf", action="store_true",
                        help="Source-derived path: also create claims for sources "
                             "without a local PDF (default: require local_path).")
    parser.add_argument("--db", default=None,
                        help="Point at an alternate SQLite DB (creates schema if "
                             "absent).  Use for temp-DB/fixture smoke runs -- never "
                             "required for the production DB.")
    args = parser.parse_args(argv)

    _setup_logging(args.verbose)
    log = logging.getLogger("backfill_knowledge")

    papers_dir = Path(args.papers_dir)
    specs_dir = Path(args.specs_dir)
    spec_file_count = _count_spec_files(specs_dir)
    run_from_sources = _should_run_from_sources(args.from_sources, spec_file_count)

    if args.db:
        import db.atlas_db as _adb
        _adb.init_db(args.db)
        log.info("  db override: %s", args.db)

    log.info("Backfill knowledge layer")
    log.info("  papers_dir: %s  (exists=%s)", papers_dir, papers_dir.exists())
    log.info("  specs_dir:  %s  (exists=%s, spec_files=%d)",
             specs_dir, specs_dir.exists(), spec_file_count)
    log.info("  from_sources: %s  (will_run=%s)", args.from_sources, run_from_sources)
    log.info("  mode:       %s", "APPLY" if args.apply else "DRY-RUN")

    if not args.apply:
        # In dry-run mode we still walk the disk but do not touch the DB.
        # Easiest way to enforce that: count files, do not call extractors.
        pdf_count = len(list(papers_dir.glob("*.pdf"))) if papers_dir.exists() else 0
        spec_files = list(specs_dir.glob("specs_*.json")) if specs_dir.exists() else []
        spec_entries = 0
        for sf in spec_files:
            try:
                data = json.loads(sf.read_text(encoding="utf-8"))
                if isinstance(data, list):
                    spec_entries += len(data)
            except Exception:  # noqa: BLE001 -- diagnostic only
                pass

        # Source-derived candidate count is a pure read (no mutation).  Guarded
        # so a missing/locked DB never breaks the dry-run.
        source_candidates: int | None = 0
        if run_from_sources:
            try:
                from db.knowledge import list_sources_without_claims
                source_candidates = len(list_sources_without_claims(
                    kind="paper",
                    require_local_pdf=not args.include_no_pdf,
                ))
            except Exception as exc:  # noqa: BLE001 -- diagnostic only
                log.warning("source-candidate count failed (dry-run, non-fatal): %s", exc)
                source_candidates = None

        summary = {
            "mode": "dry-run",
            "would_process": {
                "pdf_files": pdf_count,
                "spec_files": len(spec_files),
                "spec_entries": spec_entries,
                "from_sources": run_from_sources,
                "source_shell_claim_candidates": source_candidates,
            },
        }
        print(json.dumps(summary, indent=2))
        log.info("Dry-run complete -- no DB writes.  Re-run with --apply.")
        return 0

    # Deferred imports so dry-run does not write to the DB at all.
    from research.discovery.extractors import paper_metadata, spec_to_claims

    paper_results = paper_metadata.extract_all(papers_dir, atlas_root=_ATLAS_ROOT)
    log.info("paper_metadata: %s", _summarise_paper_results(paper_results))

    spec_results = spec_to_claims.extract_all(specs_dir)
    log.info("spec_to_claims: %s", _summarise_spec_results(spec_results))

    summary = {
        "mode": "apply",
        "papers": _summarise_paper_results(paper_results),
        "specs": _summarise_spec_results(spec_results),
    }

    if run_from_sources:
        source_results = spec_to_claims.extract_claims_from_sources(
            require_local_pdf=not args.include_no_pdf,
        )
        source_summary = _summarise_source_results(source_results)
        log.info("spec_to_claims.from_sources: %s", source_summary)
        summary["from_sources"] = source_summary

    print(json.dumps(summary, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
