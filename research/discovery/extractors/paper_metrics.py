"""Phase 1.5 LLM metric extractor.

Reads each shell claim (NULL claimed_sharpe) joined to a source with a local
PDF, extracts headline backtest metrics via Claude through the pi CLI, and
UPDATEs the claim row with the extracted numbers.

Routing: every pi invocation goes through utils.pi_subprocess.call_pi, which
forces the --system-prompt flag that routes to the Claude Max subscription
(per the CRITICAL rule in CLAUDE.md).  Never construct the subprocess
manually -- it will route to extra-usage billing and fail when credits run.

Idempotency: only claims with NULL claimed_sharpe are picked up.  After a
successful extraction the claim row has non-NULL metrics and is skipped on
subsequent runs.  Failed extractions (LLM said "found: false" or the call
errored) bump notes and extraction_confidence='low' so they can be inspected
without re-running by default; pass --include-low-confidence to retry.

PDF -> text: uses `pdftotext` (poppler-utils) when available.  If pdftotext
isn't on PATH the extractor records the failure mode in notes and continues
to the next claim.  No silent skips.
"""

from __future__ import annotations

import json
import logging
import re
import shutil
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional

logger = logging.getLogger(__name__)

# Truncation cap for the prompt's pdf_text slot.  ~20K chars ≈ 5K tokens, leaves
# headroom for the prompt scaffolding and the model's own reasoning budget.
# Headline metrics almost always sit in the abstract + first results table,
# which fits comfortably in the first ~15 pages of any quant-finance paper.
_PDF_TEXT_CHARS = 20_000

# Where the prompt template lives.
_PROMPT_PATH = Path(__file__).resolve().parent.parent / "prompts" / "extract_metrics.md"


# ─── Result dataclass ─────────────────────────────────────────────────────────

@dataclass
class ExtractionResult:
    claim_id: str
    source_id: str
    strategy: str
    ok: bool
    skipped: bool
    reason: Optional[str]              # short tag: 'no_pdf' | 'pdftotext_missing' | 'llm_error' | 'not_found' | 'extracted'
    extracted: Optional[Dict[str, Any]] # the parsed model JSON if ok

    def as_log_dict(self) -> Dict[str, Any]:
        return {
            "claim_id": self.claim_id,
            "source_id": self.source_id,
            "strategy": self.strategy,
            "ok": self.ok,
            "skipped": self.skipped,
            "reason": self.reason,
        }


# ─── pdftotext helpers ────────────────────────────────────────────────────────

def pdftotext_available() -> bool:
    return shutil.which("pdftotext") is not None


def pdf_to_text(pdf_path: Path, *, max_chars: int = _PDF_TEXT_CHARS) -> str:
    """Extract plain text from a PDF using poppler's `pdftotext`.

    Truncates to max_chars (default 20K) -- headline tables sit near the front.
    Raises RuntimeError if pdftotext is unavailable or returns non-zero; the
    caller is expected to catch and log a per-claim failure.
    """
    if not pdftotext_available():
        raise RuntimeError("pdftotext not on PATH (install poppler-utils)")
    if not pdf_path.is_file():
        raise FileNotFoundError(pdf_path)
    try:
        result = subprocess.run(
            ["pdftotext", "-layout", str(pdf_path), "-"],
            capture_output=True,
            text=True,
            timeout=60,
            encoding="utf-8",
            errors="replace",
        )
    except subprocess.TimeoutExpired as exc:
        raise RuntimeError(f"pdftotext timeout on {pdf_path.name}") from exc

    if result.returncode != 0:
        raise RuntimeError(
            f"pdftotext rc={result.returncode}: {result.stderr[:200]}"
        )
    text = result.stdout or ""
    return text[:max_chars]


# ─── Prompt rendering ─────────────────────────────────────────────────────────

def _load_prompt_template() -> str:
    return _PROMPT_PATH.read_text(encoding="utf-8")


def render_prompt(
    *,
    strategy_name: str,
    source_title: str,
    pdf_text: str,
    parameters: Optional[Dict[str, Any]] = None,
    text_chars: int = _PDF_TEXT_CHARS,
) -> str:
    template = _load_prompt_template()
    return (
        template
        .replace("{strategy_name}", strategy_name)
        .replace("{source_title}", source_title)
        .replace("{parameters_json}", json.dumps(parameters or {}, indent=2))
        .replace("{pdf_text}", pdf_text)
        .replace("{text_chars}", str(text_chars))
    )


# ─── Response parsing ────────────────────────────────────────────────────────

def _extract_assistant_text_from_ndjson(ndjson: str) -> str:
    """Pull the final assistant text block from pi CLI --mode json NDJSON.

    Mirrors research.discovery.discovery._extract_assistant_text_from_ndjson
    (single source-of-truth would be nice; left local for now to avoid pulling
    the entire discovery module into this extractor's import graph).
    """
    lines = ndjson.splitlines()
    for line in reversed(lines):
        line = line.strip()
        if not line:
            continue
        try:
            event = json.loads(line)
        except json.JSONDecodeError:
            continue
        if not isinstance(event, dict) or event.get("type") != "turn_end":
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


def parse_llm_response(raw: str) -> Optional[Dict[str, Any]]:
    """Parse pi CLI stdout into the metric-extraction JSON object.

    Tries (in order):
      1. NDJSON path: find last turn_end event -> assistant text -> JSON parse.
         Must come first because a single-line NDJSON envelope is itself
         json.loads-parseable -- naive top-level json.loads would return the
         event envelope instead of the inner payload.
      2. Code-fence inside assistant text.
      3. Bare {...} object inside assistant text.
      4. json.loads on the entire stdout (when pi returns a bare JSON doc).
      5. Code fence directly on raw (legacy paths).
    Returns the dict on success, None on failure.
    """
    raw = (raw or "").strip()
    if not raw:
        return None

    # ── NDJSON path (try first) ──────────────────────────────────────────────
    assistant_text = _extract_assistant_text_from_ndjson(raw)
    if assistant_text:
        try:
            loaded = json.loads(assistant_text)
            if isinstance(loaded, dict):
                return loaded
        except json.JSONDecodeError:
            pass
        m = re.search(r"```json\s*([\s\S]+?)\s*```", assistant_text)
        if m:
            try:
                loaded = json.loads(m.group(1))
                if isinstance(loaded, dict):
                    return loaded
            except json.JSONDecodeError:
                pass
        m = re.search(r"\{[\s\S]+\}", assistant_text)
        if m:
            try:
                loaded = json.loads(m.group(0))
                if isinstance(loaded, dict):
                    return loaded
            except json.JSONDecodeError:
                pass

    # ── Direct json.loads (pi returned a bare doc, not NDJSON) ───────────────
    try:
        loaded = json.loads(raw)
        if isinstance(loaded, dict):
            # Reject NDJSON envelopes that happened to be single-line --
            # the NDJSON path above would have caught them if they had
            # parseable assistant text; the fact we got here means the
            # envelope had no usable inner content.
            if loaded.get("type") in ("turn_end", "turn_start", "tool_use", "tool_result"):
                return None
            return loaded
    except json.JSONDecodeError:
        pass

    # ── Last-ditch: code fence directly on raw ───────────────────────────────
    m = re.search(r"```json\s*([\s\S]+?)\s*```", raw)
    if m:
        try:
            loaded = json.loads(m.group(1))
            if isinstance(loaded, dict):
                return loaded
        except json.JSONDecodeError:
            pass
    return None


# ─── Type coercion ────────────────────────────────────────────────────────────

def _f(v: Any) -> Optional[float]:
    if v is None:
        return None
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def _i(v: Any) -> Optional[int]:
    if v is None:
        return None
    try:
        return int(v)
    except (TypeError, ValueError):
        return None


def _s(v: Any) -> Optional[str]:
    if v is None:
        return None
    s = str(v).strip()
    return s or None


# ─── Per-claim extraction ────────────────────────────────────────────────────

def _claim_notes_to_parameters(notes: Optional[str]) -> Dict[str, Any]:
    """Pull the spec parameters out of the claim's notes JSON (best-effort)."""
    if not notes:
        return {}
    try:
        payload = json.loads(notes)
        if isinstance(payload, dict):
            return payload.get("parameters") or {}
    except (json.JSONDecodeError, TypeError):
        pass
    return {}


def extract_one(
    claim: Dict[str, Any],
    *,
    atlas_root: Path,
    call_pi_fn: Optional[Callable[..., str]] = None,
    timeout: int = 600,
) -> ExtractionResult:
    """Process a single shell claim row (as returned by list_shell_claims).

    call_pi_fn is the pi-subprocess function used to invoke the LLM.  Defaults
    to utils.pi_subprocess.call_pi when None; tests inject a mock here to
    avoid spawning real subprocesses.
    """
    # Local imports keep test setup light (no pi_subprocess import at module
    # load time means tests can supply call_pi_fn without ever touching it).
    from db.knowledge import update_claim_metrics

    claim_id: str = claim["claim_id"]
    source_id: str = claim["source_id"]
    strategy: str = claim["strategy"]
    local_path_rel: Optional[str] = claim.get("local_path")
    source_title: str = claim.get("source_title") or strategy

    if not local_path_rel:
        return ExtractionResult(claim_id=claim_id, source_id=source_id,
                                strategy=strategy, ok=False, skipped=True,
                                reason="no_pdf", extracted=None)

    pdf_path = (atlas_root / local_path_rel).resolve()
    if not pdf_path.is_file():
        # Path stored at ingest but file moved/deleted since.
        update_claim_metrics(
            id=claim_id,
            extraction_confidence="low",
            notes=f"phase1.5: PDF missing on disk at {local_path_rel}",
        )
        return ExtractionResult(claim_id=claim_id, source_id=source_id,
                                strategy=strategy, ok=False, skipped=True,
                                reason="pdf_missing", extracted=None)

    try:
        pdf_text = pdf_to_text(pdf_path)
    except RuntimeError as exc:
        msg = str(exc)
        if "pdftotext not on PATH" in msg:
            # Surface once; subsequent claims in this run will hit the same
            # branch and the caller can bail out early.
            update_claim_metrics(
                id=claim_id,
                extraction_confidence="low",
                notes="phase1.5: pdftotext unavailable; install poppler-utils",
            )
            return ExtractionResult(claim_id=claim_id, source_id=source_id,
                                    strategy=strategy, ok=False, skipped=True,
                                    reason="pdftotext_missing", extracted=None)
        update_claim_metrics(
            id=claim_id,
            extraction_confidence="low",
            notes=f"phase1.5: pdftotext failed: {msg[:200]}",
        )
        return ExtractionResult(claim_id=claim_id, source_id=source_id,
                                strategy=strategy, ok=False, skipped=False,
                                reason="pdftotext_failed", extracted=None)

    if not pdf_text.strip():
        update_claim_metrics(
            id=claim_id,
            extraction_confidence="low",
            notes="phase1.5: pdftotext returned empty text",
        )
        return ExtractionResult(claim_id=claim_id, source_id=source_id,
                                strategy=strategy, ok=False, skipped=False,
                                reason="empty_pdf_text", extracted=None)

    parameters = _claim_notes_to_parameters(claim.get("notes"))
    prompt = render_prompt(
        strategy_name=strategy,
        source_title=source_title,
        pdf_text=pdf_text,
        parameters=parameters,
    )

    if call_pi_fn is None:
        from utils.pi_subprocess import call_pi
        call_pi_fn = call_pi  # type: ignore[assignment]

    try:
        raw = call_pi_fn(prompt, mode="json", timeout=timeout,
                        extra_args=["--no-tools"])
    except Exception as exc:  # noqa: BLE001 -- any pi error becomes an extraction error
        update_claim_metrics(
            id=claim_id,
            extraction_confidence="low",
            notes=f"phase1.5: pi error: {type(exc).__name__}: {str(exc)[:200]}",
        )
        return ExtractionResult(claim_id=claim_id, source_id=source_id,
                                strategy=strategy, ok=False, skipped=False,
                                reason="llm_error", extracted=None)

    parsed = parse_llm_response(raw)
    if parsed is None:
        update_claim_metrics(
            id=claim_id,
            extraction_confidence="low",
            notes="phase1.5: LLM response not parseable as JSON",
        )
        return ExtractionResult(claim_id=claim_id, source_id=source_id,
                                strategy=strategy, ok=False, skipped=False,
                                reason="parse_failed", extracted=None)

    if parsed.get("found") is False:
        update_claim_metrics(
            id=claim_id,
            extraction_confidence="low",
            notes=("phase1.5: LLM reported no metrics for this strategy: "
                   + (_s(parsed.get("notes")) or "<no note>"))[:500],
        )
        return ExtractionResult(claim_id=claim_id, source_id=source_id,
                                strategy=strategy, ok=False, skipped=False,
                                reason="not_found", extracted=parsed)

    confidence = _s(parsed.get("extraction_confidence")) or "medium"
    if confidence not in ("high", "medium", "low"):
        confidence = "medium"

    update_claim_metrics(
        id=claim_id,
        claimed_sharpe=_f(parsed.get("claimed_sharpe")),
        claimed_solo_sharpe=_f(parsed.get("claimed_solo_sharpe")),
        claimed_max_dd_pct=_f(parsed.get("claimed_max_dd_pct")),
        claimed_trades=_i(parsed.get("claimed_trades")),
        claimed_cagr_pct=_f(parsed.get("claimed_cagr_pct")),
        claimed_profit_factor=_f(parsed.get("claimed_profit_factor")),
        claimed_avg_hold_days=_f(parsed.get("claimed_avg_hold_days")),
        period_start=_s(parsed.get("period_start")),
        period_end=_s(parsed.get("period_end")),
        extraction_confidence=confidence,
        notes=("phase1.5: " + (_s(parsed.get("notes")) or "extracted"))[:500],
    )
    return ExtractionResult(claim_id=claim_id, source_id=source_id,
                            strategy=strategy, ok=True, skipped=False,
                            reason="extracted", extracted=parsed)


# ─── Batch driver ────────────────────────────────────────────────────────────

def extract_pending(
    *,
    atlas_root: Path,
    limit: int = 25,
    require_local_pdf: bool = True,
    call_pi_fn: Optional[Callable[..., str]] = None,
    timeout: int = 600,
) -> List[ExtractionResult]:
    """Process up to `limit` shell claims.  Each call is independent."""
    from db.knowledge import list_shell_claims

    claims = list_shell_claims(require_local_pdf=require_local_pdf, limit=limit)
    if not claims:
        return []

    out: List[ExtractionResult] = []
    for claim in claims:
        try:
            result = extract_one(claim, atlas_root=atlas_root,
                                 call_pi_fn=call_pi_fn, timeout=timeout)
        except Exception as exc:  # noqa: BLE001 -- isolate bad claims
            logger.exception("extract_one crashed on claim_id=%s", claim.get("claim_id"))
            result = ExtractionResult(
                claim_id=claim.get("claim_id", "?"),
                source_id=claim.get("source_id", "?"),
                strategy=claim.get("strategy", "?"),
                ok=False, skipped=False,
                reason=f"crash: {type(exc).__name__}",
                extracted=None,
            )
        out.append(result)

        # If pdftotext is missing system-wide, every claim will fail the same
        # way -- stop early so the operator gets one clear log line, not 25.
        if result.reason == "pdftotext_missing":
            logger.warning("Aborting batch: pdftotext unavailable on this host")
            break

    return out
