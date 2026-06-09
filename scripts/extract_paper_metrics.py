#!/usr/bin/env python3
"""Phase 1.5 LLM metric extraction CLI.

Drives research.discovery.extractors.paper_metrics over shell claims (NULL
claimed_sharpe) joined to sources with a local PDF.  Each invocation makes one
pi CLI call per claim, so for large batches use --limit to scope the run.

Run:
    python3 scripts/extract_paper_metrics.py                        # dry-run, lists pending
    python3 scripts/extract_paper_metrics.py --apply                # process up to 25 claims
    python3 scripts/extract_paper_metrics.py --apply --limit 5
    python3 scripts/extract_paper_metrics.py --apply --claim-id clm-...    # single claim
    python3 scripts/extract_paper_metrics.py --apply --include-no-pdf      # also try reference-only sources (no-op until vision pass)
    python3 scripts/extract_paper_metrics.py --apply --include-low-confidence  # retry claims whose prior phase1.5 attempt failed (#395)

Per-claim outcomes are appended to logs/extract_paper_metrics.log.  A JSON
summary is printed to stdout.
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

LOG_PATH = _ATLAS_ROOT / "logs" / "extract_paper_metrics.log"


def _setup_logging(verbose: bool) -> None:
    LOG_PATH.parent.mkdir(parents=True, exist_ok=True)
    logging.basicConfig(
        level=logging.DEBUG if verbose else logging.INFO,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
        handlers=[
            logging.FileHandler(LOG_PATH, encoding="utf-8"),
            logging.StreamHandler(sys.stderr),
        ],
    )


def _summary(results: list) -> dict:
    summary = {
        "total": len(results),
        "ok": sum(1 for r in results if r.ok),
        "skipped": sum(1 for r in results if r.skipped),
        "failed": sum(1 for r in results if not r.ok and not r.skipped),
        "by_reason": {},
    }
    for r in results:
        reason = r.reason or "unknown"
        summary["by_reason"][reason] = summary["by_reason"].get(reason, 0) + 1
    return summary


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--apply", action="store_true",
                        help="Actually call pi and write to the DB (default: dry-run)")
    parser.add_argument("--limit", type=int, default=25,
                        help="Max claims to process per run (default: 25)")
    parser.add_argument("--claim-id", default=None,
                        help="Process exactly one claim by id (overrides --limit)")
    parser.add_argument("--include-no-pdf", action="store_true",
                        help="Include claims whose source has no local PDF "
                             "(currently they all skip with reason=no_pdf)")
    parser.add_argument("--include-low-confidence", action="store_true",
                        help="Retry claims whose prior Phase 1.5 attempt failed "
                             "(notes prefixed 'phase1.5:' + confidence='low'). "
                             "Excluded by default so the cron never retries the "
                             "same not_found claim forever (#395).")
    parser.add_argument("--timeout", type=int, default=600,
                        help="Per-call pi timeout in seconds (default: 600)")
    parser.add_argument("--verbose", action="store_true")
    args = parser.parse_args(argv)

    _setup_logging(args.verbose)
    log = logging.getLogger("extract_paper_metrics")

    # Defer import so dry-run does not pull pi_subprocess.
    from db.knowledge import list_shell_claims, get_claim

    log.info("Phase 1.5 LLM metric extraction")
    log.info("  mode:           %s", "APPLY" if args.apply else "DRY-RUN")
    log.info("  limit:          %d", args.limit)
    log.info("  claim_id:       %s", args.claim_id or "<batch>")
    log.info("  include_no_pdf: %s", args.include_no_pdf)
    log.info("  include_low_confidence: %s", args.include_low_confidence)

    # ── Dry-run: just list candidates ────────────────────────────────────────
    if not args.apply:
        if args.claim_id:
            c = get_claim(args.claim_id)
            summary = {
                "mode": "dry-run",
                "claim_id": args.claim_id,
                "exists": c is not None,
                "current_metrics_null": (
                    c is not None
                    and c.get("claimed_sharpe") is None
                    and c.get("claimed_max_dd_pct") is None
                ),
            }
        else:
            candidates = list_shell_claims(
                require_local_pdf=not args.include_no_pdf,
                include_low_confidence=args.include_low_confidence,
                limit=args.limit,
            )
            summary = {
                "mode": "dry-run",
                "would_process": len(candidates),
                "limit": args.limit,
                "sample": [
                    {
                        "claim_id": c["claim_id"],
                        "strategy": c["strategy"],
                        "source_id": c["source_id"],
                        "local_path": c.get("local_path"),
                    }
                    for c in candidates[:5]
                ],
            }
        print(json.dumps(summary, indent=2))
        log.info("Dry-run complete -- no LLM calls or DB writes.  Re-run with --apply.")
        return 0

    # ── Apply: run the extractor ──────────────────────────────────────────────
    from research.discovery.extractors.paper_metrics import (
        extract_one, extract_pending,
    )

    if args.claim_id:
        c = get_claim(args.claim_id)
        if c is None:
            log.error("Claim not found: %s", args.claim_id)
            print(json.dumps({"error": "claim_not_found", "claim_id": args.claim_id}))
            return 2

        # extract_one wants the join-shape dict that list_shell_claims returns.
        # Hydrate the joined fields manually for the single-claim path.
        from db.knowledge import get_source
        source = get_source(c["source_id"]) if c.get("source_id") else None
        joined = {
            "claim_id": c["id"],
            "source_id": c["source_id"],
            "strategy": c["strategy"],
            "universe": c.get("universe"),
            "notes": c.get("notes"),
            "source_title": (source or {}).get("title", c["strategy"]),
            "source_url": (source or {}).get("url"),
            "local_path": (source or {}).get("local_path"),
            "source_kind": (source or {}).get("kind"),
        }
        result = extract_one(joined, atlas_root=_ATLAS_ROOT, timeout=args.timeout)
        results = [result]
    else:
        results = extract_pending(
            atlas_root=_ATLAS_ROOT,
            limit=args.limit,
            require_local_pdf=not args.include_no_pdf,
            include_low_confidence=args.include_low_confidence,
            timeout=args.timeout,
        )

    for r in results:
        log.info("claim %-40s strategy=%-30s ok=%s reason=%s",
                 r.claim_id, r.strategy, r.ok, r.reason)

    summary = _summary(results)
    summary["mode"] = "apply"
    print(json.dumps(summary, indent=2))
    return 0 if summary["failed"] == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
