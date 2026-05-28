#!/usr/bin/env python3
"""One-shot backfill of the knowledge layer from existing discovery artifacts.

Reads research/discovery/papers/*.pdf -> sources rows.
Reads research/discovery/specs/specs_*.json -> shell claims rows
(plus source rows for references that lack a local PDF).

Idempotent.  Safe to re-run -- a second invocation reports zero new rows.

Run:
    python3 scripts/backfill_knowledge.py                # dry-run, prints counts
    python3 scripts/backfill_knowledge.py --apply        # actually writes
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
    args = parser.parse_args(argv)

    _setup_logging(args.verbose)
    log = logging.getLogger("backfill_knowledge")

    papers_dir = Path(args.papers_dir)
    specs_dir = Path(args.specs_dir)

    log.info("Backfill knowledge layer")
    log.info("  papers_dir: %s  (exists=%s)", papers_dir, papers_dir.exists())
    log.info("  specs_dir:  %s  (exists=%s)", specs_dir, specs_dir.exists())
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

        summary = {
            "mode": "dry-run",
            "would_process": {
                "pdf_files": pdf_count,
                "spec_files": len(spec_files),
                "spec_entries": spec_entries,
            },
        }
        print(json.dumps(summary, indent=2))
        log.info("Dry-run complete -- no DB writes.  Re-run with --apply.")
        return 0

    # Deferred imports so dry-run does not touch the DB at all.
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
    print(json.dumps(summary, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
