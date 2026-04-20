"""Patch overlay/engine.py and strategies/sector_rotation.py — Phase 3.2."""
import sys

# ══════════════════════════════════════════════════════════════════════════════
# Patch 1: overlay/engine.py
# ══════════════════════════════════════════════════════════════════════════════
ENGINE = "/root/atlas/overlay/engine.py"
with open(ENGINE) as f:
    src = f.read()

# ── Task 4: SYSTEM_PROMPT — add sector rotation guidance ──────────────────
OLD_CONSTRAINT = (
    "You CANNOT loosen.  If the regime says sizing_multiplier=0.7, you may return\n"
    "0.3 or 0.5 but NEVER 0.8 or above.  Any value >= the regime default will be\n"
    "automatically clamped by the validation layer."
)
NEW_CONSTRAINT = OLD_CONSTRAINT + (
    "\n\n"
    "SECTOR ROTATION SIGNAL:\n"
    "When defensive sectors (Utilities/XLU, Consumer Staples/XLP) rank in the top 3\n"
    "by 63-day momentum, this is a risk-off signal \u2014 money is rotating from growth\n"
    "into safety.  Weight this in your tightening assessment:\n"
    '  \u2022 severity="moderate" (1 defensive in top 3): consider mild tightening\n'
    '  \u2022 severity="high" (2+ defensives in top 3): strong case for tightening'
)
assert OLD_CONSTRAINT in src, "SYSTEM_PROMPT target not found"
src = src.replace(OLD_CONSTRAINT, NEW_CONSTRAINT, 1)
print("Task 4: SYSTEM_PROMPT updated \u2713")

# ── Task 2a: _build_user_prompt — update signature ────────────────────────
OLD_SIG = "def _build_user_prompt(regime, news: str, charts: str) -> str:"
NEW_SIG = 'def _build_user_prompt(regime, news: str, charts: str, sector_rotation: str = "") -> str:'
assert OLD_SIG in src, "_build_user_prompt signature not found"
src = src.replace(OLD_SIG, NEW_SIG, 1)
print("Task 2a: _build_user_prompt signature updated \u2713")

# ── Task 2b: _build_user_prompt — update docstring ────────────────────────
OLD_DOC = (
    "    charts : str\n"
    "        Chart / technical analysis from overlay.sources.chart_intel.\n"
    "\n"
    "    Returns"
)
NEW_DOC = (
    "    charts : str\n"
    "        Chart / technical analysis from overlay.sources.chart_intel.\n"
    "    sector_rotation : str, optional\n"
    "        Sector rotation signal from signals.sector_rotation.\n"
    "\n"
    "    Returns"
)
assert OLD_DOC in src, "docstring params section not found"
src = src.replace(OLD_DOC, NEW_DOC, 1)
print("Task 2b: docstring updated \u2713")

# ── Task 2c: _build_user_prompt — add SECTOR ROTATION section to prompt ───
OLD_CHART = (
    'CHART / TECHNICAL ANALYSIS:\n'
    '{charts if charts else "No chart analysis available."}\n'
    '\n'
    '=== YOUR TASK ==='
)
NEW_CHART = (
    'CHART / TECHNICAL ANALYSIS:\n'
    '{charts if charts else "No chart analysis available."}\n'
    '\n'
    'SECTOR ROTATION ANALYSIS:\n'
    '{sector_rotation if sector_rotation else "No sector rotation data available."}\n'
    '\n'
    '=== YOUR TASK ==='
)
assert OLD_CHART in src, "chart section in prompt template not found"
src = src.replace(OLD_CHART, NEW_CHART, 1)
print("Task 2c: prompt body updated \u2713")

# ── Task 1: Add _load_sector_rotation() before # Public entry point ────────
SECTOR_FN = '''

def _load_sector_rotation() -> str:
    """
    Load sector rotation signal for overlay context.

    Returns formatted string with sector rankings and defensive rotation status.
    Returns empty string if unavailable.
    """
    try:
        from signals.sector_rotation import get_sector_rotation_signal

        signal = get_sector_rotation_signal()
        if not signal or not signal.get("rankings"):
            return ""

        lines = ["SECTOR ROTATION (63-day momentum):"]
        lines.append(f"  Defensive rotation: {signal.get('defensive_rotation', False)}")
        lines.append(f"  Severity: {signal.get('severity', 'none')}")

        if signal.get('defensive_in_top3'):
            etf_names = ", ".join(signal['defensive_in_top3'])
            lines.append(f"  Defensive ETFs in top 3: {etf_names}")

        lines.append(f"  Top 3 sectors: {', '.join(signal.get('top3_sectors', []))}")
        lines.append(f"  Bottom 3 sectors: {', '.join(signal.get('bottom3_sectors', []))}")

        lines.append("  Rankings:")
        for r in signal.get("rankings", []):
            marker = " \u25c4 DEFENSIVE" if r["etf"] in {"XLU", "XLP"} else ""
            lines.append(f"    #{r['rank']} {r['etf']} ({r['sector']}): {r['roc_63d']:+.2f}%{marker}")

        return "\\n".join(lines)
    except ImportError:
        log.debug("signals.sector_rotation not available \u2014 skipping")
        return ""
    except Exception as exc:
        log.warning("Sector rotation source error: %s \u2014 continuing without", exc)
        return ""
'''

ANCHOR = (
    "\n# " + "\u2500" * 78 + "\n"
    "# Public entry point"
)
assert ANCHOR in src, "public entry point anchor not found"
src = src.replace(ANCHOR, SECTOR_FN + ANCHOR, 1)
print("Task 1: _load_sector_rotation() added \u2713")

# ── Task 3: run_overlay() — load sector_rotation & update call ─────────────
OLD_LOAD = (
    "    news = _load_news()\n"
    "    charts = _load_charts()\n"
    "\n"
    "    data_sources: Dict = {\n"
    '        "news_available": bool(news),\n'
    '        "charts_available": bool(charts),\n'
    '        "regime_date": regime.date,\n'
    '        "regime_state": regime.state.value,\n'
    "    }\n"
    "\n"
    "    # \u2500\u2500 Step 3: Build structured prompt \u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\n"
    "    user_prompt = _build_user_prompt(regime, news, charts)"
)
NEW_LOAD = (
    "    news = _load_news()\n"
    "    charts = _load_charts()\n"
    "    sector_rotation = _load_sector_rotation()\n"
    "\n"
    "    data_sources: Dict = {\n"
    '        "news_available": bool(news),\n'
    '        "charts_available": bool(charts),\n'
    '        "sector_rotation_available": bool(sector_rotation),\n'
    '        "regime_date": regime.date,\n'
    '        "regime_state": regime.state.value,\n'
    "    }\n"
    "\n"
    "    # \u2500\u2500 Step 3: Build structured prompt \u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\n"
    "    user_prompt = _build_user_prompt(regime, news, charts, sector_rotation=sector_rotation)"
)
assert OLD_LOAD in src, "run_overlay data load block not found"
src = src.replace(OLD_LOAD, NEW_LOAD, 1)
print("Task 3: run_overlay() updated \u2713")

with open(ENGINE, "w") as f:
    f.write(src)
print("overlay/engine.py written \u2713")

# ══════════════════════════════════════════════════════════════════════════════
# Patch 2: strategies/sector_rotation.py
# ══════════════════════════════════════════════════════════════════════════════
STRAT = "/root/atlas/strategies/sector_rotation.py"
with open(STRAT) as f:
    strat_src = f.read()

# ── Task 5a: Insert SPDR cross-reference block after sector ranking ─────────
OLD_RANK_BLOCK = (
    "        # Step 3: Rank sectors\n"
    "        top_sectors, bottom_sectors = self._rank_sectors(sector_momentum)\n"
    "        self._logger.debug(f\"Sectors: top={top_sectors}, bottom={bottom_sectors}\")\n"
    "\n"
    "        # Step 4: Generate signals for top sector stocks"
)
NEW_RANK_BLOCK = (
    "        # Step 3: Rank sectors\n"
    "        top_sectors, bottom_sectors = self._rank_sectors(sector_momentum)\n"
    "        self._logger.debug(f\"Sectors: top={top_sectors}, bottom={bottom_sectors}\")\n"
    "\n"
    "        # Cross-reference with SPDR sector rotation signal\n"
    "        spdr_signal = None\n"
    "        try:\n"
    "            from signals.sector_rotation import get_sector_rotation_signal\n"
    "            spdr_signal = get_sector_rotation_signal()\n"
    "        except Exception:\n"
    "            pass\n"
    "\n"
    "        # If defensive rotation detected with high severity, reduce confidence\n"
    "        defensive_penalty = 0.0\n"
    "        if spdr_signal and spdr_signal.get(\"severity\") == \"high\":\n"
    "            defensive_penalty = 0.10  # reduce confidence by 10%\n"
    "        elif spdr_signal and spdr_signal.get(\"severity\") == \"moderate\":\n"
    "            defensive_penalty = 0.05\n"
    "\n"
    "        # Step 4: Generate signals for top sector stocks"
)
assert OLD_RANK_BLOCK in strat_src, "rank block not found in strategy"
strat_src = strat_src.replace(OLD_RANK_BLOCK, NEW_RANK_BLOCK, 1)
print("Task 5a: SPDR cross-reference block added \u2713")

# ── Task 5b: Apply defensive_penalty in confidence calculation ─────────────
OLD_CONF = (
    "                confidence = min(1.0, base_confidence + momentum_bonus + rs_bonus)"
)
NEW_CONF = (
    "                confidence = min(1.0, base_confidence + momentum_bonus + rs_bonus - defensive_penalty)"
)
assert OLD_CONF in strat_src, "confidence line not found"
strat_src = strat_src.replace(OLD_CONF, NEW_CONF, 1)
print("Task 5b: defensive_penalty applied to confidence \u2713")

# ── Task 5c: Add spdr fields to features dict ──────────────────────────────
OLD_FEATURES = (
    '                    features={\n'
    '                            "sector": sector,\n'
    '                            "sector_momentum": sect_mom,\n'
    '                            "sector_rank": top_sectors.index(sector) + 1,\n'
    '                            "rs_score": float(rs_score),\n'
    '                            "atr": float(atr),\n'
    '                        },'
)
NEW_FEATURES = (
    '                    features={\n'
    '                            "sector": sector,\n'
    '                            "sector_momentum": sect_mom,\n'
    '                            "sector_rank": top_sectors.index(sector) + 1,\n'
    '                            "rs_score": float(rs_score),\n'
    '                            "atr": float(atr),\n'
    '                            "defensive_rotation": spdr_signal.get("defensive_rotation", False) if spdr_signal else False,\n'
    '                            "defensive_severity": spdr_signal.get("severity", "none") if spdr_signal else "none",\n'
    '                        },'
)
assert OLD_FEATURES in strat_src, "features dict not found"
strat_src = strat_src.replace(OLD_FEATURES, NEW_FEATURES, 1)
print("Task 5c: defensive fields added to features dict \u2713")

with open(STRAT, "w") as f:
    f.write(strat_src)
print("strategies/sector_rotation.py written \u2713")

print("\nAll patches applied successfully.")
