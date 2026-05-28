#!/usr/bin/env python3
"""Atlas Research Discovery Orchestrator.

Drives the daily paper → strategy pipeline:
  source rotation → fetch/browse → filter → extract specs → deduplicate
  → generate code → quick_check → log → Telegram digest

Entry point: discover_daily() -> DailyReport
"""

import json
import logging
import re
import subprocess
import sys
import tempfile
import time
from dataclasses import dataclass, field, asdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

DISCOVERY_DIR = Path(__file__).resolve().parent
ATLAS_ROOT = DISCOVERY_DIR.parent.parent
if str(ATLAS_ROOT) not in sys.path:
    sys.path.insert(0, str(ATLAS_ROOT))

# Lazy import: research.db available only after sys.path is configured above.
# Import is deferred to call-time inside discover_daily() to avoid module-level
# ImportError if atlas root is not on the path in some execution contexts.

logger = logging.getLogger("discovery")

# ─── Paths ───────────────────────────────────────────────────────────────────

PAPERS_DIR = DISCOVERY_DIR / "papers"
SPECS_DIR = DISCOVERY_DIR / "specs"
LOGS_DIR = DISCOVERY_DIR / "logs"
PROMPTS_DIR = DISCOVERY_DIR / "prompts"
DAILY_LOG = DISCOVERY_DIR / "daily_log.jsonl"
CUMULATIVE_STATS = DISCOVERY_DIR / "cumulative_stats.json"
SEEN_URLS_FILE = DISCOVERY_DIR / "seen_urls.txt"
MCP_CONFIG = DISCOVERY_DIR / "config" / "mcp_config.json"


# ─── DailyReport dataclass ───────────────────────────────────────────────────

@dataclass
class DailyReport:
    date: str
    source: str
    method: str
    papers_found: int
    papers_filtered: int
    specs_extracted: int
    strategies_generated: list = field(default_factory=list)   # list of str (strategy names)
    strategies_passed_quickcheck: list = field(default_factory=list)  # list of str
    errors: list = field(default_factory=list)  # list of str
    runtime_s: float = 0.0
    # Phase 4: knowledge-layer signals folded into the daily digest.
    new_contradictions: int = 0                # count opened since last digest
    new_lifecycle_transitions: int = 0         # count since last digest
    top_contradictions: list = field(default_factory=list)  # [{strategy, metric, claimed, measured, severity}]




def _extract_assistant_text_from_ndjson(ndjson: str) -> str:
    """Extract the final assistant text block from pi CLI NDJSON output.

    Pi CLI ``--mode json`` produces newline-delimited JSON (NDJSON) where each
    line is a structured event object.  The ``turn_end`` event for the LAST
    completed turn contains ``message.content`` — an array of blocks with types
    such as ``"thinking"`` and ``"text"``.  We scan the stream in reverse so the
    LAST ``turn_end`` event (the final assistant response) is found first.

    Content block types:
      - ``"text"``     — the model's actual reply; this is what we want.
      - ``"thinking"`` — extended-thinking scratchpad; skip.
      - ``"tool_use"`` — tool invocation; skip (no text to extract).

    Returns the text of the first suitable text block, or ``""`` if the stream
    has no parseable ``turn_end`` event with a text block.
    """
    lines = ndjson.splitlines()
    # Scan in reverse: the LAST turn_end = the final assistant turn
    for line in reversed(lines):
        line = line.strip()
        if not line:
            continue
        try:
            event = json.loads(line)
        except json.JSONDecodeError:
            continue
        if event.get("type") != "turn_end":
            continue
        message = event.get("message", {})
        if not isinstance(message, dict) or message.get("role") != "assistant":
            continue
        for block in message.get("content", []):
            if isinstance(block, dict) and block.get("type") == "text":
                text = block.get("text", "")
                if text.strip():
                    return text
    return ""

# ─── Core Pi CLI helper ──────────────────────────────────────────────────────

def _run_pi(
    prompt: str,
    mcp: bool = False,
    schema_path: Optional[str] = None,
    allowed_tools: str = "Bash,Read,Write",
) -> dict:
    """Run pi CLI with the given prompt and return parsed JSON output.

    Uses subprocess.run with a 30-minute timeout. Writes prompt to a temp file
    and passes it via stdin for safety with special characters.

    Args:
        prompt: The prompt text to send to pi.
        mcp: If True, (ignored — pi CLI does not support --mcp-config).
        schema_path: Optional path to a JSON schema file (ignored — pi CLI does not support --json-schema).
        allowed_tools: Comma-separated list of tools pi may use.

    Returns:
        dict — parsed JSON result, or {"error": "<msg>", "raw": "<stdout>"} on failure.
    """
    from utils.pi_subprocess import call_pi, PiSubprocessError  # noqa: PLC0415

    # Quick auth check via helper — validates OAuth routing before a 30-min call
    try:
        call_pi("echo ok", mode=None, timeout=15, extra_args=["--no-tools"])
    except PiSubprocessError:
        logger.warning("Pi CLI not working — skipping LLM call. Ensure pi is installed and configured.")
        return {"error": "not_authenticated", "raw": "Pi CLI not working"}
    except Exception as exc:
        logger.debug("Pi CLI auth pre-check failed (will attempt main call anyway): %s", exc, exc_info=True)

    # Note: pi CLI does not support --mcp-config; MCP browsing tools not available
    # The LLM will use bash/read tools for web access instead

    # Note: pi CLI does not support --json-schema; relying on prompt instructions for structure

    extra: list[str] = []
    if allowed_tools:
        extra = ["--tools", allowed_tools.lower()]

    try:
        raw = call_pi(prompt, extra_args=extra or None, timeout=1800)
        stdout = raw.strip()
        if not stdout:
            return {"error": "empty output from pi", "raw": ""}

        # Parse JSON.
        # Pi CLI --mode json outputs NDJSON (newline-delimited JSON events).
        # A bare json.loads() of the full stdout therefore fails.
        # Strategy:
        #   1. Try json.loads directly (works when pi outputs a single JSON doc).
        #   2. Parse NDJSON: find the last turn_end event, extract assistant text,
        #      then try json.loads on that text (+ code-fence and bare-array fallbacks).
        #   3. Code-fence extraction on raw stdout (legacy fallback).
        #   4. Return {"error": "json parse failed", "raw": ...}.
        try:
            return json.loads(stdout)
        except json.JSONDecodeError:
            # ── NDJSON path ───────────────────────────────────────────────────
            assistant_text = _extract_assistant_text_from_ndjson(stdout)
            if assistant_text:
                try:
                    return json.loads(assistant_text)
                except json.JSONDecodeError:
                    pass
                # Code fence inside assistant text
                m = re.search(r"```json\s*([\s\S]+?)\s*```", assistant_text)
                if m:
                    try:
                        return json.loads(m.group(1))
                    except json.JSONDecodeError:
                        pass
                # Bare JSON array inside assistant text (model added prose prefix)
                m2 = re.search(r"\[\s*\{[\s\S]+?\}\s*\]", assistant_text)
                if m2:
                    try:
                        return json.loads(m2.group(0))
                    except json.JSONDecodeError:
                        pass
                logger.debug("_run_pi: assistant_text not JSON; snippet=%s", assistant_text[:200])
                return {"error": "json parse failed", "raw": assistant_text[:2000]}
            # ── Legacy code-fence fallback on raw stdout ──────────────────────
            m = re.search(r"```json\s*([\s\S]+?)\s*```", stdout)
            if m:
                try:
                    return json.loads(m.group(1))
                except json.JSONDecodeError:
                    pass
            return {"error": "json parse failed", "raw": stdout[:2000]}

    except PiSubprocessError as e:
        err_msg = str(e)
        if "timed out" in err_msg:
            logger.error("Pi CLI timed out after 1800s")
            return {"error": "timeout", "raw": ""}
        if "not found on PATH" in err_msg:
            logger.warning("pi CLI not found — skipping LLM step (graceful degradation)")
            return {"error": "pi not found", "raw": ""}
        logger.error("_run_pi error: %s", e)
        return {"error": str(e), "raw": ""}
    except Exception as exc:
        logger.error("_run_pi unexpected error", exc_info=True)
        return {"error": str(exc), "raw": ""}


# ─── Browse helpers ──────────────────────────────────────────────────────────

def _browse_with_pi(source: dict) -> list:
    """Use pi CLI with tools to browse SSRN or a blog.

    Reads the appropriate prompt template (browse_ssrn.md or browse_blog.md),
    substitutes placeholders, and calls _run_pi with tools enabled.

    Returns:
        list of paper dicts (may be empty if pi CLI unavailable).
    """
    # Determine prompt file using the "source" key (e.g. "ssrn", "blog", "quantpedia").
    # Note: source dicts use the key "source", not "type".
    source_type = source.get("source", "")
    if source_type == "ssrn":
        prompt_file = PROMPTS_DIR / "browse_ssrn.md"
    else:
        prompt_file = PROMPTS_DIR / "browse_blog.md"

    if not prompt_file.exists():
        # Graceful fallback: use browse_blog.md for unknown source types
        fallback = PROMPTS_DIR / "browse_blog.md"
        if fallback.exists():
            logger.info(
                "browse_with_pi: no prompt for source_type=%r — falling back to browse_blog.md",
                source_type,
            )
            prompt_file = fallback
        else:
            logger.warning("Browse prompt not found: %s", prompt_file)
            return []

    PAPERS_DIR.mkdir(parents=True, exist_ok=True)
    prompt = prompt_file.read_text()
    queries = source.get("queries", [])
    prompt = prompt.replace("{queries}", json.dumps(queries, indent=2))
    prompt = prompt.replace("{papers_dir}", str(PAPERS_DIR.resolve()))
    prompt = prompt.replace("{seen_urls_file}", str(SEEN_URLS_FILE.resolve()))
    prompt = prompt.replace("{source}", json.dumps(source, indent=2))

    allowed = "Bash,Read,Write,computer_use,browser"
    result = _run_pi(prompt, mcp=True, allowed_tools=allowed)

    # Log raw shape for diagnostics
    logger.info(
        "browse_with_pi: source=%s result_type=%s",
        source.get("name", source_type or "?"),
        type(result).__name__,
    )

    if isinstance(result, dict) and "error" in result:
        logger.warning("browse_with_pi error: %s", result.get("error"))
        return []

    # Tolerant parser — pi CLI returns inconsistent shapes depending on model
    # behaviour and session length:
    #   - bare list of paper dicts           (ideal path after NDJSON fix)
    #   - dict with "papers" key
    #   - dict with "result" key (list OR JSON-encoded string "[{...}]")
    #   - dict with other list-valued keys
    #   - plain string with embedded JSON array (model added prose prefix/suffix)
    papers: list[dict] = []

    if isinstance(result, list):
        papers = [p for p in result if isinstance(p, dict)]

    elif isinstance(result, dict):
        # Try known keys in priority order
        for key in ("papers", "result", "items", "data", "results"):
            candidate = result.get(key)
            if isinstance(candidate, list):
                papers = [p for p in candidate if isinstance(p, dict)]
                if papers:
                    logger.info(
                        "browse_with_pi: parsed via dict[%r] key (%d papers)", key, len(papers)
                    )
                    break
            elif isinstance(candidate, str):
                # Pi wrapper shape: {"result": "[{...}]"} — value is JSON string
                try:
                    parsed = json.loads(candidate)
                    if isinstance(parsed, list):
                        papers = [p for p in parsed if isinstance(p, dict)]
                        if papers:
                            logger.info(
                                "browse_with_pi: parsed JSON string via dict[%r] key (%d papers)",
                                key, len(papers),
                            )
                            break
                except json.JSONDecodeError:
                    pass

        if not papers:
            # Last-resort: scan all dict values for any list of dicts
            for k, v in result.items():
                if isinstance(v, list) and v and all(isinstance(x, dict) for x in v):
                    papers = v
                    logger.info("browse_with_pi: parsed via fallback scan of key %r", k)
                    break

    elif isinstance(result, str):
        # Pi returned raw text — extract embedded JSON array
        m = re.search(r"\[\s*\{[\s\S]+?\}\s*\]", result)
        if m:
            try:
                papers = json.loads(m.group(0))
            except json.JSONDecodeError:
                pass

    if not papers:
        snippet = str(result)[:1000] if result else "<empty>"
        logger.warning(
            "browse_with_pi: 0 papers parsed from result; snippet=%s", snippet
        )

    return papers


# ─── Filter helpers ───────────────────────────────────────────────────────────

def _filter_papers(papers: list) -> list:
    """Filter papers by relevance score using Claude.

    Reads prompts/filter.md, substitutes paper data, calls Claude (no MCP).
    Returns papers with score >= 6.

    If Claude CLI unavailable, passes all papers through (graceful degradation).
    """
    if not papers:
        return []

    logger.info("filter_papers: evaluating %d input papers via Claude", len(papers))

    prompt_file = PROMPTS_DIR / "filter.md"
    if not prompt_file.exists():
        logger.warning("filter.md prompt not found — passing all %d papers", len(papers))
        return papers

    PAPERS_DIR.mkdir(parents=True, exist_ok=True)
    prompt = prompt_file.read_text()
    prompt = prompt.replace("{papers_dir}", str(PAPERS_DIR.resolve()))
    prompt = prompt.replace("{items_json}", json.dumps(papers, indent=2))

    result = _run_pi(prompt, mcp=False, allowed_tools="Bash,Read")

    if "error" in result:
        logger.warning("_filter_papers error: %s — passing all papers", result["error"])
        return papers

    # Expect list of scored papers, or dict with 'papers' key
    scored = []
    if isinstance(result, list):
        scored = result
    elif isinstance(result, dict):
        scored = result.get("papers", result.get("filtered", result.get("result", [])))
        if not isinstance(scored, list):
            scored = []

    # Filter by score >= 6
    filtered = [p for p in scored if isinstance(p, dict) and p.get("score", 0) >= 6]
    if not filtered and scored:
        # If claude returned papers without scores, keep all
        filtered = [p for p in scored if isinstance(p, dict)]

    logger.info("filter_papers: %d → %d (score ≥ 6)", len(papers), len(filtered))

    # Verbose diagnostics
    scored_count = len(scored)
    rejected = [p for p in scored if isinstance(p, dict) and p.get("score", 0) < 6]
    logger.info("filter_papers diagnostics: input=%d, scored=%d, kept(>=6)=%d, rejected(<6)=%d",
                len(papers), scored_count, len(filtered), len(rejected))

    if rejected:
        # Show up to 5 rejected papers with titles and scores
        for p in rejected[:5]:
            logger.info("  REJECT score=%s: %s",
                        p.get("score", "?"),
                        (p.get("title") or p.get("url") or "<untitled>")[:80])

    if scored_count == 0 and len(papers) > 0:
        logger.warning("filter_papers: claude returned 0 scored items from %d input — "
                       "likely parse error; first raw result type=%s",
                       len(papers), type(result).__name__)

    return filtered


# ─── Spec extraction ─────────────────────────────────────────────────────────

def _extract_specs(papers: list) -> list:
    """Extract strategy specs from filtered papers using Claude.

    Reads prompts/extract.md, builds a prompt with paper details, calls Claude.
    Returns list of strategy spec dicts.
    """
    if not papers:
        return []

    prompt_file = PROMPTS_DIR / "extract.md"
    if not prompt_file.exists():
        logger.warning("extract.md prompt not found — skipping spec extraction")
        return []

    PAPERS_DIR.mkdir(parents=True, exist_ok=True)
    prompt = prompt_file.read_text()
    prompt = prompt.replace("{papers_dir}", str(PAPERS_DIR.resolve()))
    # Include vision-extracted figure summaries in the prompt context (#256)
    papers_for_prompt = papers  # already enriched in place with vision_summary keys
    prompt = prompt.replace("{papers_json}", json.dumps(papers_for_prompt, indent=2))

    result = _run_pi(prompt, mcp=False, allowed_tools="Bash,Read")

    if "error" in result:
        logger.warning("_extract_specs error: %s", result["error"])
        return []

    specs = []
    if isinstance(result, list):
        specs = result
    elif isinstance(result, dict):
        specs = result.get("specs", result.get("strategies", result.get("result", [])))
        if not isinstance(specs, list):
            specs = []

    logger.info("_extract_specs: %d papers → %d specs", len(papers), len(specs))
    return specs


# ─── Strategy generation ──────────────────────────────────────────────────────

def _generate_strategies(specs: list) -> list:
    """Generate Python strategy files for each spec using Claude.

    Reads prompts/generate.md, substitutes spec JSON, calls Claude with file-write tools.
    Runs quick_check() on each generated strategy.

    Returns:
        list of dicts: {"spec": dict, "quick_check": dict, "strategy_name": str}
    """
    if not specs:
        return []

    prompt_file = PROMPTS_DIR / "generate.md"
    if not prompt_file.exists():
        logger.warning("generate.md prompt not found — skipping code generation")
        return []

    generate_template = prompt_file.read_text()
    results = []

    for spec in specs:
        strategy_name = spec.get("strategy_name", "unknown_strategy")
        logger.info("Generating strategy: %s", strategy_name)

        prompt = generate_template.replace("{spec_json}", json.dumps(spec, indent=2))
        prompt = prompt.replace("{strategy_name}", strategy_name)
        prompt = prompt.replace("{atlas_root}", str(ATLAS_ROOT.resolve()))
        prompt = prompt.replace("{strategies_dir}", str(ATLAS_ROOT / "research" / "strategies"))

        gen_result = _run_pi(
            prompt,
            mcp=False,
            allowed_tools="Bash,Read,Write,Edit",
        )

        if "error" in gen_result and gen_result.get("error"):
            logger.warning("Generation failed for %s: %s", strategy_name, gen_result["error"])
            results.append({
                "spec": spec,
                "strategy_name": strategy_name,
                "quick_check": {"alive": False, "reason": gen_result["error"]},
            })
            continue

        # Run quick_check on the generated strategy
        qc_result = {"alive": False, "reason": "not attempted"}
        try:
            from research.loop import quick_check
            qc_result = quick_check(strategy_name, "sp500")
            logger.info(
                "quick_check %s: alive=%s reason=%s",
                strategy_name, qc_result.get("alive"), qc_result.get("reason", "")
            )
        except Exception as exc:
            qc_result = {"alive": False, "reason": str(exc)}
            logger.warning("quick_check error for %s", strategy_name, exc_info=True)

        results.append({
            "spec": spec,
            "strategy_name": strategy_name,
            "quick_check": qc_result,
        })

    return results


# ─── Backlog review ───────────────────────────────────────────────────────────

def _review_backlog() -> list:
    """Load papers/specs that previously errored from daily_log.jsonl for retry.

    Returns list of specs from entries that had errors and no generated strategies.
    """
    if not DAILY_LOG.exists():
        return []

    retry_specs = []
    try:
        lines = DAILY_LOG.read_text().splitlines()
        # Check last 30 days of logs (at most last 90 lines)
        for line in lines[-90:]:
            line = line.strip()
            if not line:
                continue
            try:
                entry = json.loads(line)
            except json.JSONDecodeError:
                continue

            if entry.get("errors") and not entry.get("strategies_generated"):
                # Re-queue specs from this failed run
                for spec in entry.get("specs", []):
                    if isinstance(spec, dict) and spec.get("strategy_name"):
                        retry_specs.append(spec)
    except Exception:
        logger.warning("_review_backlog error", exc_info=True)

    logger.info("_review_backlog: found %d specs to retry", len(retry_specs))
    return retry_specs


# ─── Logging & stats ─────────────────────────────────────────────────────────

def _log_daily_run(report: DailyReport) -> None:
    """Append one JSON line to daily_log.jsonl and update cumulative_stats.json."""
    DISCOVERY_DIR.mkdir(parents=True, exist_ok=True)

    # Append to daily log
    entry = asdict(report)
    with open(DAILY_LOG, "a") as f:
        f.write(json.dumps(entry) + "\n")

    # Update cumulative stats
    stats = {}
    if CUMULATIVE_STATS.exists():
        try:
            stats = json.loads(CUMULATIVE_STATS.read_text())
        except (json.JSONDecodeError, OSError):
            stats = {}

    stats["total_runs"] = stats.get("total_runs", 0) + 1
    stats["papers_found"] = stats.get("papers_found", 0) + report.papers_found
    stats["papers_filtered"] = stats.get("papers_filtered", 0) + report.papers_filtered
    stats["specs_extracted"] = stats.get("specs_extracted", 0) + report.specs_extracted
    stats["strategies_generated"] = stats.get("strategies_generated", 0) + len(report.strategies_generated)
    stats["strategies_passed_quickcheck"] = (
        stats.get("strategies_passed_quickcheck", 0) + len(report.strategies_passed_quickcheck)
    )

    # Monthly breakdown
    month_key = report.date[:7]  # "2026-03"
    monthly = stats.setdefault("monthly", {})
    mo = monthly.setdefault(month_key, {
        "runs": 0, "papers_found": 0, "papers_filtered": 0,
        "specs_extracted": 0, "strategies_generated": 0, "strategies_passed": 0,
    })
    mo["runs"] += 1
    mo["papers_found"] += report.papers_found
    mo["papers_filtered"] += report.papers_filtered
    mo["specs_extracted"] += report.specs_extracted
    mo["strategies_generated"] += len(report.strategies_generated)
    mo["strategies_passed"] += len(report.strategies_passed_quickcheck)

    stats["last_run"] = report.date

    CUMULATIVE_STATS.write_text(json.dumps(stats, indent=2))
    logger.info("Daily run logged: %s", report.date)


# ─── Telegram digest ─────────────────────────────────────────────────────────

# Phase 4: knowledge-layer counts folded into the daily digest.  Defensive --
# any DB error here is logged and ignored; the digest still sends its primary
# discovery content even when the knowledge layer is unreachable.
def _enrich_report_with_knowledge_counts(report: DailyReport) -> None:
    """Populate report.new_contradictions / new_lifecycle_transitions /
    top_contradictions from the DB.  In-place mutation."""
    try:
        from db.atlas_db import get_db
        from db.knowledge import get_last_digest, get_open_contradictions

        last = get_last_digest(kind="daily")
        since_iso = last["sent_at"] if last else None

        with get_db() as conn:
            # New contradictions since the last digest (or all-time on first run).
            sql_c = "SELECT COUNT(*) AS n FROM contradictions WHERE resolution IS NULL"
            params: list = []
            if since_iso:
                sql_c += " AND first_seen_at > ?"
                params.append(since_iso)
            report.new_contradictions = int(
                conn.execute(sql_c, params).fetchone()["n"]
            )

            # New lifecycle transitions since the last digest.
            sql_l = "SELECT COUNT(*) AS n FROM strategy_lifecycle_history WHERE 1=1"
            lparams: list = []
            if since_iso:
                sql_l += " AND transitioned_at > ?"
                lparams.append(since_iso)
            report.new_lifecycle_transitions = int(
                conn.execute(sql_l, lparams).fetchone()["n"]
            )

        # Top 3 open contradictions (severity-ordered) for the digest body.
        top = get_open_contradictions(limit=3)
        report.top_contradictions = [
            {
                "strategy": r.get("strategy"),
                "metric": r.get("metric"),
                "claimed": r.get("claimed_value"),
                "measured": r.get("measured_value"),
                "severity": r.get("severity"),
            }
            for r in top
        ]
    except Exception as exc:
        logger.warning("_enrich_report_with_knowledge_counts failed: %s", exc)


def _format_top_contradictions(top: list) -> str:
    """Render a short Telegram-friendly block.  Empty string when nothing to show."""
    if not top:
        return ""
    lines = ["\n\n⚠️ <b>Top open contradictions</b>:"]
    for c in top:
        s = c.get("strategy") or "?"
        metric = c.get("metric") or "?"
        claimed = c.get("claimed")
        measured = c.get("measured")
        sev = c.get("severity") or "?"
        try:
            claimed_s = f"{float(claimed):.2f}" if claimed is not None else "?"
            measured_s = f"{float(measured):.2f}" if measured is not None else "?"
        except (TypeError, ValueError):
            claimed_s = str(claimed)
            measured_s = str(measured)
        lines.append(
            f"  • <code>{s}</code> {metric}: paper {claimed_s} vs measured "
            f"{measured_s} ({sev})"
        )
    return "\n".join(lines)


# PERF-TG-CONSOLIDATE: KEPT — ~40 LOC body (>30 LOC threshold). Inlining would produce
# a 40-line block at the call site inside discover_daily(); abstraction value retained.
def _send_telegram_digest(report: DailyReport) -> None:
    """Send a formatted Telegram message with the daily discovery summary."""
    passed_emoji = "✅" if report.strategies_passed_quickcheck else "⚪"
    error_note = f"\n⚠️ Errors: {len(report.errors)}" if report.errors else ""

    month_key = report.date[:7]
    # Load cumulative monthly stats for footer
    monthly_stats = ""
    if CUMULATIVE_STATS.exists():
        try:
            stats = json.loads(CUMULATIVE_STATS.read_text())
            mo = stats.get("monthly", {}).get(month_key, {})
            if mo:
                monthly_stats = (
                    f"\n\n📅 <b>{month_key} totals</b>: "
                    f"{mo['runs']} runs | "
                    f"{mo['papers_found']} papers | "
                    f"{mo['strategies_generated']} strategies | "
                    f"{mo['strategies_passed']} passed QC"
                )
        except (OSError, json.JSONDecodeError, KeyError) as exc:
            logger.debug("Monthly stats load failed for digest: %s", exc, exc_info=True)

    generated_list = ""
    if report.strategies_generated:
        generated_list = "\n" + "\n".join(
            f"  {'✅' if s in report.strategies_passed_quickcheck else '❌'} <code>{s}</code>"
            for s in report.strategies_generated
        )

    # Phase 4: knowledge-layer one-liner + top contradictions block.
    knowledge_line = ""
    if report.new_contradictions or report.new_lifecycle_transitions:
        knowledge_line = (
            f"\n🧠 Knowledge: "
            f"<b>{report.new_contradictions}</b> new contradictions"
            f" | <b>{report.new_lifecycle_transitions}</b> lifecycle transitions"
        )
    top_block = _format_top_contradictions(report.top_contradictions)

    message = (
        f"🔬 <b>Atlas Discovery — {report.date}</b>\n"
        f"📚 Source: <b>{report.source}</b> ({report.method})\n\n"
        f"📄 Papers found: <b>{report.papers_found}</b>\n"
        f"🎯 Filtered (score≥6): <b>{report.papers_filtered}</b>\n"
        f"📐 Specs extracted: <b>{report.specs_extracted}</b>\n"
        f"{passed_emoji} Strategies generated: <b>{len(report.strategies_generated)}</b>"
        + generated_list
        + f"\n{passed_emoji} Passed quick-check: <b>{len(report.strategies_passed_quickcheck)}</b>"
        + error_note
        + f"\n⏱️ Runtime: {report.runtime_s:.0f}s"
        + knowledge_line
        + top_block
        + monthly_stats
    )

    try:
        from alerting import get_alert_manager
        get_alert_manager().send(message)
        logger.info("Telegram digest sent")
    except Exception as e:
        logger.warning("Telegram digest failed: %s", e)


# ─── Main orchestrator ───────────────────────────────────────────────────────

def discover_daily() -> DailyReport:
    """Run the daily paper discovery → strategy generation pipeline.

    Workflow:
    1. Determine today's source via rotation
    2. Fetch/browse papers (API or computer-use)
    3. Filter by relevance (Claude)
    4. Extract strategy specs (Claude)
    5. Deduplicate against existing strategies
    6. Generate Python strategy code (Claude)
    7. Run quick_check on each generated strategy
    8. Log results and send Telegram digest
    9. Return DailyReport

    Returns:
        DailyReport dataclass with full run summary.
    """
    start_time = time.time()
    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    errors = []

    # Ensure output directories exist
    for d in [PAPERS_DIR, SPECS_DIR, LOGS_DIR]:
        d.mkdir(parents=True, exist_ok=True)

    # ── Step 1: get today's source ────────────────────────────────────────
    source = {}
    method = "api"
    source_name = "arxiv"
    try:
        from research.discovery.sources import get_today_source, get_queries_for_source
        source = get_today_source()
        source_name = source.get("name", "arxiv")
        method = source.get("method", "api")
        logger.info("Today's source: %s (method=%s)", source_name, method)
    except ImportError:
        logger.warning("research.discovery.sources not available — defaulting to arxiv API")
        source = {"name": "arxiv", "method": "api"}
        source_name = "arxiv"
        method = "api"
    except Exception as e:
        errors.append(f"get_today_source: {e}")
        logger.error("get_today_source failed: %s", e)
        source = {"name": "arxiv", "method": "api"}

    # ── Step 2: fetch / browse papers ─────────────────────────────────────
    papers = []
    if method == "api":
        try:
            from research.discovery.arxiv_api import fetch_new_papers
            from research.discovery.sources import get_queries_for_source
            queries = get_queries_for_source(source)
            if not queries:
                logger.warning("No queries for source %s (categories=%s)", source_name, source.get("categories"))
            papers = fetch_new_papers(queries=queries)
            logger.info("fetch_new_papers returned %d papers (from %d queries)", len(papers), len(queries))
        except ImportError:
            logger.warning("research.discovery.arxiv_api not available — no papers fetched")
        except Exception as e:
            errors.append(f"fetch_new_papers: {e}")
            logger.error("fetch_new_papers failed: %s", e)

    elif method == "computer_use":
        try:
            papers = _browse_with_pi(source)
            logger.info("_browse_with_pi returned %d papers", len(papers))
        except Exception as e:
            errors.append(f"_browse_with_pi: {e}")
            logger.error("_browse_with_pi failed: %s", e)

    elif method == "review":
        try:
            papers = _review_backlog()
            logger.info("_review_backlog returned %d items", len(papers))
        except Exception as e:
            errors.append(f"_review_backlog: {e}")
            logger.error("_review_backlog failed: %s", e)

    papers_found = len(papers)

    # ── Step 3: dedup seen URLs ───────────────────────────────────────────
    try:
        from research.discovery.dedup import is_seen, mark_seen
        unseen_papers = []
        for p in papers:
            url = p.get("url", p.get("pdf_url", p.get("arxiv_id", "")))
            if url and is_seen(url):
                logger.debug("Skipping seen: %s", url)
            else:
                unseen_papers.append(p)
        papers = unseen_papers
        logger.info("After URL dedup: %d papers", len(papers))
    except ImportError:
        pass
    except Exception as e:
        logger.warning("URL dedup error: %s", e)

    # ── Step 4: filter papers ─────────────────────────────────────────────
    filtered_papers = []
    try:
        filtered_papers = _filter_papers(papers)
    except Exception as e:
        errors.append(f"_filter_papers: {e}")
        logger.error("_filter_papers failed: %s", e)
        filtered_papers = papers  # fallback: pass all through

    papers_filtered = len(filtered_papers)

    # Mark filtered papers as seen
    try:
        from research.discovery.dedup import mark_seen
        for p in filtered_papers:
            url = p.get("url", p.get("pdf_url", p.get("arxiv_id", "")))
            if url:
                mark_seen(url, "filtered")
    except Exception as exc:
        logger.warning("URL mark_seen (filtered) failed: %s", exc, exc_info=True)

    # ── Step 4.5: vision pre-pass — extract figure content from PDFs (#256) ──
    try:
        from research.discovery.pdf_vision import enrich_papers_with_vision
        enrich_papers_with_vision(filtered_papers, max_pages=8)
    except Exception as e:
        logger.warning("vision pre-pass failed (non-fatal): %s", e)

    # ── Step 5: extract specs ─────────────────────────────────────────────
    specs = []
    try:
        specs = _extract_specs(filtered_papers)
    except Exception as e:
        errors.append(f"_extract_specs: {e}")
        logger.error("_extract_specs failed: %s", e)

    specs_extracted = len(specs)

    # ── Step 6: dedup strategy specs ─────────────────────────────────────
    unique_specs = []
    try:
        from research.discovery.dedup import is_duplicate_strategy, load_existing_strategies
        existing = load_existing_strategies()
        for spec in specs:
            if is_duplicate_strategy(spec, existing):
                logger.info("Duplicate spec skipped: %s", spec.get("strategy_name"))
            else:
                unique_specs.append(spec)
        logger.info("Strategy dedup: %d → %d", len(specs), len(unique_specs))
    except ImportError:
        unique_specs = specs
    except Exception as e:
        logger.warning("Strategy dedup error: %s", e)
        unique_specs = specs

    # Save specs to disk for reference
    if unique_specs:
        SPECS_DIR.mkdir(parents=True, exist_ok=True)
        specs_file = SPECS_DIR / f"specs_{today}.json"
        try:
            specs_file.write_text(json.dumps(unique_specs, indent=2))
        except (OSError, TypeError) as exc:
            logger.warning("Failed to write specs file %s: %s", specs_file, exc, exc_info=True)

    # ── Step 7: generate strategies ───────────────────────────────────────
    gen_results = []
    try:
        gen_results = _generate_strategies(unique_specs)
    except Exception as e:
        errors.append(f"_generate_strategies: {e}")
        logger.error("_generate_strategies failed: %s", e)

    strategies_generated = [r["strategy_name"] for r in gen_results]
    strategies_passed_quickcheck = [
        r["strategy_name"] for r in gen_results
        if r.get("quick_check", {}).get("alive", False)
    ]

    # ── Step 8: assemble report ───────────────────────────────────────────
    runtime_s = time.time() - start_time
    report = DailyReport(
        date=today,
        source=source_name,
        method=method,
        papers_found=papers_found,
        papers_filtered=papers_filtered,
        specs_extracted=specs_extracted,
        strategies_generated=strategies_generated,
        strategies_passed_quickcheck=strategies_passed_quickcheck,
        errors=errors,
        runtime_s=round(runtime_s, 1),
    )

    # ── Step 9: log & notify ──────────────────────────────────────────────
    try:
        _log_daily_run(report)
    except Exception as e:
        logger.error("_log_daily_run failed: %s", e)

    # Log to research_discoveries SQLite table for dashboard visibility
    try:
        from research.db import log_discovery  # noqa: PLC0415 — deferred (sys.path)
        log_discovery(
            run_date=today,
            papers_found=papers_found,
            papers_filtered=papers_filtered,
            specs_extracted=specs_extracted,
            strategies_generated=len(strategies_generated),
            paper_titles=[
                (p.get("title") if isinstance(p, dict) else str(p))
                for p in papers[:20]  # cap to 20 to keep the row small
            ],
            status="completed" if not errors else "partial",
        )
    except Exception as exc:
        logger.warning("log_discovery failed (non-fatal): %s", exc)

    # Phase 4: enrich the report with knowledge-layer counts before the
    # digest renders.  Both calls are defensive -- a knowledge-layer outage
    # must not block the digest from sending.
    try:
        _enrich_report_with_knowledge_counts(report)
    except Exception as exc:  # noqa: BLE001 -- defensive
        logger.warning("knowledge enrichment failed: %s", exc)

    delivery_status: str = "ok"
    try:
        _send_telegram_digest(report)
    except Exception as e:
        delivery_status = f"failed:{type(e).__name__}"
        logger.warning("_send_telegram_digest failed: %s", e)

    # Phase 4: record one row per send so the next digest's "since last sent"
    # window has a reference point and so we can dedup re-sends.  Defensive --
    # the digest is the load-bearing operation; logging it is best-effort.
    try:
        from db.knowledge import log_digest
        log_digest(
            kind="daily",
            new_papers=report.papers_found,
            new_experiments=len(report.strategies_generated),
            new_contradictions=report.new_contradictions,
            lifecycle_transitions=report.new_lifecycle_transitions,
            summary=f"discover_daily {report.date}",
            delivery_status=delivery_status,
            payload={
                "date": report.date,
                "source": report.source,
                "papers_filtered": report.papers_filtered,
                "top_contradictions": report.top_contradictions,
            },
        )
    except Exception as exc:  # noqa: BLE001 -- defensive
        logger.warning("log_digest failed: %s", exc)

    logger.info(
        "discover_daily complete: found=%d filtered=%d specs=%d generated=%d passed=%d runtime=%.0fs",
        papers_found, papers_filtered, specs_extracted,
        len(strategies_generated), len(strategies_passed_quickcheck), runtime_s,
    )
    return report


def discover_full() -> list:
    """Run discovery across ALL sources (full sweep mode).

    Iterates through every configured source regardless of rotation schedule.
    Returns list of DailyReport objects.
    """
    reports = []
    try:
        from research.discovery.sources import get_all_sources
        sources = get_all_sources()
    except ImportError:
        logger.warning("sources module not available — running single default source")
        sources = [{"name": "arxiv", "method": "api"}]
    except Exception as e:
        logger.error("get_all_sources failed: %s", e)
        sources = [{"name": "arxiv", "method": "api"}]

    for source in sources:
        logger.info("discover_full: processing source %s", source.get("name"))
        report = discover_daily()  # discovers_daily uses today's source internally
        reports.append(report)

    return reports
