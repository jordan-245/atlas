#!/usr/bin/env python3
"""Seed the position monitor with the XOP test position + Oil/Energy template."""

import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from monitor.models import Position, Condition, Template, PositionStore

store = PositionStore()

# ── Seed XOP position ──
xop = Position(
    ticker="XOP",
    asset_type="ETF",
    entry_price=142.00,
    entry_date="2026-02-15",
    quantity=1,
    direction="long",
    thesis="E&P valuations cheap, oil has asymmetric geopolitical upside from Iran/Hormuz tensions, contrarian play against bearish consensus of $51-58 WTI",
    timeframe="6-12 months",
    invalidation_price=120.00,
    tags=["energy", "oil", "geopolitical"],
    conditions=[
        Condition(
            label="WTI crude above $60",
            type="price_above",
            source="CL=F",
            threshold=60,
            weight=3,
            warning_threshold=63,
            status="passing",
        ),
        Condition(
            label="WTI curve in backwardation",
            type="manual_toggle",
            weight=2,
            status="passing",
        ),
        Condition(
            label="US rig count below 700",
            type="indicator_threshold",
            source="FRED:RIGS",
            threshold=700,
            direction="below",
            weight=1,
            status="unknown",
        ),
        Condition(
            label="XOP above 200-day MA",
            type="ma_position",
            source="XOP",
            threshold=200,
            weight=2,
            status="unknown",
        ),
        Condition(
            label="Geopolitical risk premium active",
            type="manual_toggle",
            weight=1,
            status="passing",
        ),
    ],
)
xop.update_health()

# Check if XOP already exists
existing = store.load_positions()
xop_exists = any(p.ticker == "XOP" and p.status == "open" for p in existing)

if not xop_exists:
    store.add_position(xop)
    print(f"✅ Seeded XOP position (id={xop.id}, health={xop.health_score})")
else:
    print("⚠️ XOP position already exists, skipping seed")

# ── Seed Oil/Energy Long template ──
templates = store.load_templates()
tmpl_exists = any(t.name == "Oil/Energy Long" for t in templates)

if not tmpl_exists:
    tmpl = Template(
        name="Oil/Energy Long",
        description="Template for energy sector long positions. Pre-configured with WTI price, rig count, MA, and geopolitical conditions.",
        tags=["energy", "oil", "geopolitical"],
        conditions=[
            {"label": "WTI crude above $60", "type": "price_above", "source": "CL=F",
             "threshold": 60, "weight": 3, "warning_threshold": 63},
            {"label": "WTI curve in backwardation", "type": "manual_toggle", "weight": 2},
            {"label": "US rig count below 700", "type": "indicator_threshold",
             "source": "FRED:RIGS", "threshold": 700, "direction": "below", "weight": 1},
            {"label": "Ticker above 200-day MA", "type": "ma_position",
             "source": "", "threshold": 200, "weight": 2},
            {"label": "Geopolitical risk premium active", "type": "manual_toggle", "weight": 1},
        ],
    )
    store.save_template(tmpl)
    print(f"✅ Seeded 'Oil/Energy Long' template (id={tmpl.id})")
else:
    print("⚠️ Oil/Energy Long template already exists, skipping seed")
