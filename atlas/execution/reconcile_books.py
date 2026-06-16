"""atlas/execution/reconcile_books.py — the missing book↔broker invariant guard.

The virtual sub-book design (virtual_book.py) rests on ONE invariant: the sum of every strategy's
virtual-book positions must equal the shared paper account's broker positions —

    Σ(all data/live/<name>/book.json positions)[ticker]  ==  broker.positions[ticker]   ∀ ticker

because every share in the shared account belongs to exactly one strategy's book. When that breaks
(OPG orders recorded as filled on *acceptance* that never actually fill; positions left behind by a
bad redeploy), the per-strategy accounting — and the forward-paper slippage / track-vs-expectation
evidence the real-capital gate depends on — corrupts SILENTLY. Nothing was checking it: reconcile_shadow
covers the legacy SP500 book, not the forge virtual books.

This module reconciles the two and classifies the drift:
  • orphan   — broker holds it, NO book claims it          (→ the dashboard 'Unattributed' bucket)
  • phantom  — a book claims it, broker does NOT hold it   (e.g. an OPG short that never filled)
  • mismatch — both have it, quantities differ

Pure core `compare()` (no IO, fully testable) + a thin CLI that loads the live books + broker and
alerts on drift. Wire into the daily shadow loop (or its own timer) so drift can never go silent again.
(Added 2026-06-16 after a 119-phantom / 10-orphan drift was found only by hand.)
"""
from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Optional

logger = logging.getLogger("atlas.execution.reconcile_books")


def compare(broker: dict, books: dict) -> dict:
    """Pure reconciliation. `broker` = {ticker: qty}; `books` = {strategy_name: {ticker: qty}}.

    Returns a structured report; `ok` is True only when Σ(books) == broker exactly.
    """
    agg: dict = {}
    for pos in books.values():
        for tk, q in (pos or {}).items():
            agg[tk] = agg.get(tk, 0) + (q or 0)
    bro = {k: v for k, v in (broker or {}).items() if v}
    agg = {k: v for k, v in agg.items() if v}
    bset, aset = set(bro), set(agg)

    orphans = {tk: bro[tk] for tk in sorted(bset - aset)}
    phantoms = {tk: agg[tk] for tk in sorted(aset - bset)}
    mismatch = {
        tk: {"broker": bro[tk], "books": agg[tk]}
        for tk in sorted(bset & aset)
        if abs(float(bro[tk]) - float(agg[tk])) > 1e-9
    }
    return {
        "ok": not (orphans or phantoms or mismatch),
        "n_broker": len(bro),
        "n_books_agg": len(agg),
        "n_orphans": len(orphans),
        "n_phantoms": len(phantoms),
        "n_mismatch": len(mismatch),
        "orphans": orphans,
        "phantoms": phantoms,
        "mismatch": mismatch,
    }


def load_books(live_dir: Optional[Path] = None) -> dict:
    """{strategy_name: {ticker: qty}} from every data/live/<name>/book.json (skips dotted dirs)."""
    from atlas.kernel.paths import PROJECT_ROOT
    live_dir = live_dir or (PROJECT_ROOT / "data" / "live")
    out: dict = {}
    for bf in sorted(Path(live_dir).glob("*/book.json")):
        if bf.parent.name.startswith("."):
            continue
        try:
            b = json.loads(bf.read_text())
        except Exception as e:  # noqa: BLE001
            logger.warning("book load failed %s: %s", bf, e)
            continue
        out[bf.parent.name] = {k: v for k, v in (b.get("positions") or {}).items() if v}
    return out


def load_broker_positions() -> dict:
    """{ticker: qty} from the shared paper account (read-only)."""
    from atlas.kernel.config import load_config
    from atlas.brokers.registry import get_live_broker
    cfg = load_config()
    cfg.setdefault("trading", {})["mode"] = "paper"
    b = get_live_broker(cfg)
    if b is None:
        raise RuntimeError("no paper broker configured")
    if hasattr(b, "connect"):
        b.connect()
    out: dict = {}
    for p in b.get_positions():
        tk = getattr(p, "ticker", None) or getattr(p, "symbol", None)
        qty = getattr(p, "shares", None)
        if qty is None:
            qty = getattr(p, "qty", None) or getattr(p, "quantity", None)
        if tk is not None and qty is not None:
            out[str(tk)] = int(round(float(qty)))
    return out


def format_report(rep: dict, limit: int = 20) -> str:
    head = (f"book↔broker reconcile: {'OK ✅' if rep['ok'] else 'DRIFT ⚠'} "
            f"(broker {rep['n_broker']} vs Σbooks {rep['n_books_agg']}; "
            f"orphans {rep['n_orphans']}, phantoms {rep['n_phantoms']}, qty-mismatch {rep['n_mismatch']})")
    lines = [head]
    if rep["orphans"]:
        lines.append(f"  orphans (broker holds, no book): {list(rep['orphans'])[:limit]}")
    if rep["phantoms"]:
        lines.append(f"  phantoms (book claims, not at broker): {list(rep['phantoms'])[:limit]}")
    if rep["mismatch"]:
        sample = {k: v for k, v in list(rep["mismatch"].items())[:limit]}
        lines.append(f"  qty-mismatch: {sample}")
    return "\n".join(lines)


def main(alert: bool = True) -> int:
    books = load_books()
    broker = load_broker_positions()
    rep = compare(broker, books)
    msg = format_report(rep)
    print(msg)
    if alert and not rep["ok"]:
        try:
            from atlas.kernel.notify import send_message
            send_message("⚠ <b>Atlas book↔broker drift</b>\n<pre>" + msg + "</pre>")
        except Exception as e:  # noqa: BLE001
            logger.warning("drift alert send failed: %s", e)
    return 0 if rep["ok"] else 1


if __name__ == "__main__":
    import sys
    sys.exit(main(alert="--no-alert" not in sys.argv))
