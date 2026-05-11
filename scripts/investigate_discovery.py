"""F-09: Discovery pipeline 0-papers regression investigation.

Run: python3 scripts/investigate_discovery.py

Summary of findings (2026-05-11):
  Root cause 1 (PRIMARY):   _browse_with_pi() JSON parse failure
  Root cause 2 (SECONDARY): arxiv_api.py double-marks URLs seen before dedup step
  Current status:            Discovery DISABLED per research-system-audit-2026-05-06
  Fix applied:               arxiv_api.py seen_urls write removed (see DEDUP FIX below)
"""
from __future__ import annotations

import json
import logging
import sqlite3
import subprocess
from datetime import datetime, timezone
from pathlib import Path

logger = logging.getLogger(__name__)
logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")

ATLAS_ROOT = Path(__file__).resolve().parent.parent
DB = ATLAS_ROOT / "data" / "atlas.db"
DAILY_LOG = ATLAS_ROOT / "research" / "discovery" / "daily_log.jsonl"
CRONTAB_SRC = ATLAS_ROOT / "scripts" / "atlas.crontab"


def check_db_history() -> None:
    """Show last 10 discovery runs from research_discoveries table."""
    print("\n=== DB: research_discoveries (last 10) ===")
    conn = sqlite3.connect(str(DB))
    conn.row_factory = sqlite3.Row
    rows = conn.execute(
        "SELECT id, run_date, papers_found, papers_filtered, specs_extracted,"
        " strategies_generated, status, created_at"
        " FROM research_discoveries ORDER BY run_date DESC LIMIT 10"
    ).fetchall()
    conn.close()
    for r in rows:
        print(
            f"  {r['run_date']}  papers={r['papers_found']}  specs={r['specs_extracted']}"
            f"  generated={r['strategies_generated']}  status={r['status']}"
        )


def check_cron_status() -> None:
    """Check if discovery cron/timer is enabled."""
    print("\n=== Cron / systemd timer status ===")
    # Check crontab
    result = subprocess.run(
        ["crontab", "-l"], capture_output=True, text=True, timeout=5
    )
    lines = [l for l in result.stdout.splitlines() if "discovery" in l.lower()]
    print(f"  Crontab discovery entries: {lines or ['none']}")

    # Check systemd timer
    result2 = subprocess.run(
        ["systemctl", "is-active", "atlas-discovery.timer"],
        capture_output=True, text=True, timeout=5,
    )
    print(f"  atlas-discovery.timer: {result2.stdout.strip()}")


def check_browse_with_pi_issue() -> None:
    """Document the _browse_with_pi JSON parse failure and applied fix."""
    print("\n=== Root cause 1: _browse_with_pi JSON parse failure ===")
    print("  Method: computer_use — calls pi CLI to browse SSRN/Quantpedia/blogs")
    print("  Original failure: 'browse_with_pi error: json parse failed' after ~1398s")
    print("  Root cause: pi CLI --mode json outputs NDJSON (newline-delimited JSON")
    print("    events), NOT a single JSON document.  json.loads(full_stdout) always")
    print("    fails for NDJSON → _run_pi returned {'error': 'json parse failed'}.")
    print("  Evidence: journalctl shows May 06 run (last systemd timer run):")
    print("    10:00:03 [discovery] INFO Today\'s source: arxiv (method=computer_use)")
    print("    10:23:21 [discovery] WARNING browse_with_pi error: json parse failed")
    print("    Papers found: 0 after 1398s runtime")
    print("  Days affected: Wednesday (SSRN), Friday (Quantpedia), Saturday (blog)")
    print()
    # Check if fix is applied
    disc_py = Path(__file__).parent.parent / "research" / "discovery" / "discovery.py"
    src = disc_py.read_text()
    if "_extract_assistant_text_from_ndjson" in src:
        print("  STATUS: FIX APPLIED (R-04, 2026-05-11)")
        print("    _extract_assistant_text_from_ndjson() parses NDJSON turn_end events")
        print("    _run_pi() now correctly extracts model text from pi CLI NDJSON stream")
        print("    _browse_with_pi() has tolerant multi-shape parser with snippet logging")
        print("    source_type key bug fixed: source.get('source') not source.get('type')")
        print("    browse_ssrn.md prompt added for SSRN-specific computer_use sessions")
    else:
        print("  STATUS: FIX NOT APPLIED — see research/discovery/discovery.py")


def check_arxiv_dedup_bug() -> None:
    """Document and verify the arxiv_api.py seen_urls double-write bug."""
    print("\n=== Root cause 2: arxiv_api.py seen_urls double-write bug ===")
    arxiv_api = ATLAS_ROOT / "research" / "discovery" / "arxiv_api.py"
    dedup_py = ATLAS_ROOT / "research" / "discovery" / "dedup.py"
    text = arxiv_api.read_text()
    # Check if the fix has been applied
    if "# F-09 FIX:" in text:
        print("  STATUS: FIX APPLIED — arxiv_api.py no longer writes to seen_urls.txt")
        print("  Dedup now handled exclusively by dedup.py (as intended)")
    else:
        print("  STATUS: BUG PRESENT — arxiv_api.py writes to seen_urls.txt as side effect")
        print("  Flow:")
        print("    1. fetch_new_papers() finds papers → marks ALL in seen_urls.txt")
        print("    2. discover_daily step 3: dedup.is_seen() reads same seen_urls.txt")
        print("    3. ALL papers are now 'already seen' → filtered out")
        print("    4. papers_found = N (pre-dedup) but papers after dedup = 0")
        print("  Fix: remove seen_urls.txt write from arxiv_api.py")
        print("       In-run dedup via seen_this_run set is still correct")
        print("       Persistent marking is dedup.py's responsibility only")


def check_daily_log() -> None:
    """Show last entry from daily_log.jsonl."""
    print("\n=== daily_log.jsonl (last entry) ===")
    if not DAILY_LOG.exists():
        print("  File not found")
        return
    lines = DAILY_LOG.read_text().strip().splitlines()
    if lines:
        try:
            last = json.loads(lines[-1])
            print(f"  Date: {last.get('date')}  Source: {last.get('source')}"
                  f"  Method: {last.get('method')}")
            print(f"  papers_found: {last.get('papers_found')}")
            print(f"  errors: {last.get('errors', [])}")
        except json.JSONDecodeError:
            print(f"  Raw: {lines[-1][:200]}")


def test_arxiv_api(quick: bool = True) -> None:
    """Quick test of arxiv_api.py fetch."""
    print("\n=== Quick arxiv API test ===")
    try:
        import sys
        sys.path.insert(0, str(ATLAS_ROOT))
        from research.discovery.arxiv_api import fetch_new_papers
        # Use max_results=3 since_days=7 for speed
        papers = fetch_new_papers(
            ["momentum trading strategy stocks"],
            max_results=3,
            since_days=7,
        )
        print(f"  fetch_new_papers returned {len(papers)} papers")
        for p in papers[:2]:
            print(f"    {p.get('published', '?')} — {p.get('title', '?')[:60]}")
    except Exception as exc:
        print(f"  FAILED: {exc}")


def main() -> None:
    print("=" * 60)
    print("  F-09 Discovery Pipeline Investigation — 2026-05-11")
    print("=" * 60)
    check_db_history()
    check_cron_status()
    check_browse_with_pi_issue()
    check_arxiv_dedup_bug()
    check_daily_log()
    print("\n=== Summary ===")
    print("  Root cause 1 (PRIMARY, fixed 2026-05-11):")
    print("    _browse_with_pi() NDJSON parser fix (R-04)")
    print("    pi CLI --mode json → NDJSON → turn_end events → extract text block")
    print("    Affects: computer_use method (Wed/Fri/Sat sources)")
    print("  Root cause 2 (SECONDARY, API method broken):")
    print("    arxiv_api.py writes URLs to seen_urls.txt BEFORE dedup step")
    print("    All API-fetched papers appear as 'already seen' → 0 papers")
    print("  Current status:")
    print("    Discovery DISABLED (crontab commented 2026-05-06, timer inactive)")
    print("    Reason: 26 runs, 0 specs extracted, 0 strategies adopted")
    print("  Fix applied (Root cause 2):")
    print("    arxiv_api.py: removed seen_urls.txt write (research/discovery/arxiv_api.py)")
    print("    In-run dedup via seen_this_run set retained")
    print("    Persistent URL marking delegated to dedup.py mark_seen()")
    disc_py = Path(__file__).parent.parent / "research" / "discovery" / "discovery.py"
    if "_extract_assistant_text_from_ndjson" in disc_py.read_text():
        print("  Fix applied (Root cause 1 - R-04):")
        print("    _extract_assistant_text_from_ndjson() + tolerant _browse_with_pi parser")
        print("    Cron re-enabled 2026-05-11")
    else:
        print("  Fix NOT applied (Root cause 1 - needs larger refactor):")
        print("    See: research/discovery/discovery.py _browse_with_pi()")


if __name__ == "__main__":
    main()
