"""Position Monitor — Data Models & Storage.

JSON-backed storage for manually tracked positions with rule-based
health scoring. Each position has a thesis + conditions that are
evaluated daily to produce a health score (1-10).

Storage: /root/atlas/data/position_monitor/positions.json
"""

from __future__ import annotations

import json
import logging
import uuid
from copy import deepcopy
from dataclasses import asdict, dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Literal, Optional

logger = logging.getLogger(__name__)

PROJECT = Path(__file__).resolve().parent.parent
STORE_DIR = PROJECT / "data" / "position_monitor"
POSITIONS_FILE = STORE_DIR / "positions.json"
TEMPLATES_FILE = STORE_DIR / "templates.json"
ALERTS_FILE = STORE_DIR / "alerts.json"

ConditionType = Literal["price_above", "price_below", "ma_position",
                        "manual_toggle", "indicator_threshold"]
ConditionStatus = Literal["passing", "warning", "failing", "unknown"]
PositionDirection = Literal["long", "short"]
PositionStatus = Literal["open", "closed"]


@dataclass
class Condition:
    """A single rule that defines part of the thesis."""
    id: str = ""
    label: str = ""
    type: ConditionType = "manual_toggle"
    source: str = ""                # ticker or FRED:SERIES_ID
    threshold: float = 0.0
    warning_threshold: Optional[float] = None
    direction: str = "above"        # for indicator_threshold
    weight: int = 1                 # 1-3 importance
    status: ConditionStatus = "unknown"
    current_value: Optional[float] = None
    last_checked: Optional[str] = None
    notes: str = ""

    def __post_init__(self):
        if not self.id:
            self.id = uuid.uuid4().hex[:8]


@dataclass
class Position:
    """A manually tracked position with thesis + conditions."""
    id: str = ""
    ticker: str = ""
    asset_type: str = ""            # ETF, stock, commodity, crypto
    entry_price: float = 0.0
    entry_date: str = ""
    quantity: float = 0.0
    direction: PositionDirection = "long"
    thesis: str = ""
    timeframe: str = ""
    invalidation_price: float = 0.0
    target_price: Optional[float] = None
    tags: List[str] = field(default_factory=list)
    status: PositionStatus = "open"
    conditions: List[Condition] = field(default_factory=list)
    health_score: float = 0.0
    current_price: Optional[float] = None
    unrealized_pnl: Optional[float] = None
    unrealized_pnl_pct: Optional[float] = None
    notes: List[Dict[str, str]] = field(default_factory=list)  # [{timestamp, text}]
    created_at: str = ""
    updated_at: str = ""
    closed_at: Optional[str] = None
    close_price: Optional[float] = None
    close_reason: Optional[str] = None
    template_id: Optional[str] = None

    def __post_init__(self):
        if not self.id:
            self.id = uuid.uuid4().hex[:12]
        now = datetime.now().isoformat(timespec="seconds")
        if not self.created_at:
            self.created_at = now
        if not self.updated_at:
            self.updated_at = now
        # Ensure conditions are Condition objects
        parsed = []
        for c in self.conditions:
            if isinstance(c, dict):
                parsed.append(Condition(**{k: v for k, v in c.items()
                                          if k in Condition.__dataclass_fields__}))
            else:
                parsed.append(c)
        self.conditions = parsed

    def calc_health_score(self) -> float:
        """Compute health score: (sum passing weights) / (total weights) × 10."""
        if not self.conditions:
            return 10.0
        total_weight = sum(c.weight for c in self.conditions)
        if total_weight == 0:
            return 10.0
        passing_weight = sum(
            c.weight for c in self.conditions if c.status == "passing"
        )
        warning_weight = sum(
            c.weight for c in self.conditions if c.status == "warning"
        )
        # Warnings count as half
        score = (passing_weight + warning_weight * 0.5) / total_weight * 10
        return round(score, 1)

    def update_health(self):
        """Recalculate and store health score."""
        self.health_score = self.calc_health_score()
        self.updated_at = datetime.now().isoformat(timespec="seconds")


@dataclass
class Template:
    """Reusable condition set for spinning up similar positions fast."""
    id: str = ""
    name: str = ""
    description: str = ""
    tags: List[str] = field(default_factory=list)
    conditions: List[Dict[str, Any]] = field(default_factory=list)
    created_at: str = ""

    def __post_init__(self):
        if not self.id:
            self.id = uuid.uuid4().hex[:8]
        if not self.created_at:
            self.created_at = datetime.now().isoformat(timespec="seconds")


# ── Storage ──────────────────────────────────────────────────

class PositionStore:
    """JSON-backed CRUD for positions, templates, and alerts."""

    def __init__(self, store_dir: Path = STORE_DIR):
        self.store_dir = store_dir
        self.positions_file = store_dir / "positions.json"
        self.templates_file = store_dir / "templates.json"
        self.alerts_file = store_dir / "alerts.json"
        store_dir.mkdir(parents=True, exist_ok=True)

    def _read(self, path: Path, default=None):
        if not path.exists():
            return default if default is not None else []
        try:
            with open(path) as f:
                return json.load(f)
        except Exception:
            return default if default is not None else []

    def _write(self, path: Path, data):
        with open(path, "w") as f:
            json.dump(data, f, indent=2, default=str)

    # ── Positions ──

    def load_positions(self) -> List[Position]:
        raw = self._read(self.positions_file, [])
        positions = []
        for r in raw:
            try:
                positions.append(Position(**{k: v for k, v in r.items()
                                             if k in Position.__dataclass_fields__}))
            except Exception as e:
                logger.warning("Skipping malformed position: %s", e)
        return positions

    def save_positions(self, positions: List[Position]):
        data = [asdict(p) for p in positions]
        self._write(self.positions_file, data)

    def get_position(self, position_id: str) -> Optional[Position]:
        for p in self.load_positions():
            if p.id == position_id:
                return p
        return None

    def add_position(self, pos: Position) -> Position:
        positions = self.load_positions()
        positions.append(pos)
        self.save_positions(positions)
        return pos

    def update_position(self, pos: Position) -> Position:
        positions = self.load_positions()
        for i, p in enumerate(positions):
            if p.id == pos.id:
                pos.updated_at = datetime.now().isoformat(timespec="seconds")
                positions[i] = pos
                break
        self.save_positions(positions)
        return pos

    def delete_position(self, position_id: str) -> bool:
        positions = self.load_positions()
        before = len(positions)
        positions = [p for p in positions if p.id != position_id]
        if len(positions) < before:
            self.save_positions(positions)
            return True
        return False

    def get_open_positions(self) -> List[Position]:
        return [p for p in self.load_positions() if p.status == "open"]

    # ── Templates ──

    def load_templates(self) -> List[Template]:
        raw = self._read(self.templates_file, [])
        return [Template(**{k: v for k, v in r.items()
                            if k in Template.__dataclass_fields__}) for r in raw]

    def save_template(self, tmpl: Template) -> Template:
        templates = self.load_templates()
        # Upsert
        replaced = False
        for i, t in enumerate(templates):
            if t.id == tmpl.id:
                templates[i] = tmpl
                replaced = True
                break
        if not replaced:
            templates.append(tmpl)
        self._write(self.templates_file, [asdict(t) for t in templates])
        return tmpl

    def delete_template(self, template_id: str) -> bool:
        templates = self.load_templates()
        before = len(templates)
        templates = [t for t in templates if t.id != template_id]
        if len(templates) < before:
            self._write(self.templates_file, [asdict(t) for t in templates])
            return True
        return False

    # ── Alerts ──

    def load_alerts(self, limit: int = 50) -> List[Dict]:
        alerts = self._read(self.alerts_file, [])
        return alerts[-limit:]

    def add_alert(self, alert: Dict):
        alerts = self._read(self.alerts_file, [])
        alert.setdefault("timestamp", datetime.now().isoformat(timespec="seconds"))
        alerts.append(alert)
        # Keep last 200
        if len(alerts) > 200:
            alerts = alerts[-200:]
        self._write(self.alerts_file, alerts)

    # ── Aggregate Stats ──

    def get_summary(self) -> Dict:
        """Aggregate stats for the summary bar."""
        positions = self.get_open_positions()
        if not positions:
            return {
                "total_open": 0,
                "total_unrealized_pnl": 0,
                "worst_health": 10.0,
                "avg_health": 10.0,
                "by_tag": {},
            }
        total_pnl = sum(p.unrealized_pnl or 0 for p in positions)
        healths = [p.health_score for p in positions]
        worst = min(healths) if healths else 10.0
        avg = sum(healths) / len(healths) if healths else 10.0

        # Group by tag
        by_tag: Dict[str, Dict] = {}
        for p in positions:
            for tag in p.tags:
                if tag not in by_tag:
                    by_tag[tag] = {"count": 0, "pnl": 0, "avg_health": 0, "scores": []}
                by_tag[tag]["count"] += 1
                by_tag[tag]["pnl"] += p.unrealized_pnl or 0
                by_tag[tag]["scores"].append(p.health_score)
        for tag, d in by_tag.items():
            d["avg_health"] = round(sum(d["scores"]) / len(d["scores"]), 1) if d["scores"] else 10
            d["pnl"] = round(d["pnl"], 2)
            del d["scores"]

        return {
            "total_open": len(positions),
            "total_unrealized_pnl": round(total_pnl, 2),
            "worst_health": worst,
            "avg_health": round(avg, 1),
            "by_tag": by_tag,
        }
