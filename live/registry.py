"""live/registry.py — registry of DEPLOYED strategies for the forge->live pipeline.

Each entry is a target-weight book + its lifecycle state (shadow -> canary -> live) + capital slice + broker +
modeled expectation. Backed by ``config/live_strategies.json``. Starts EMPTY — a strategy enters only after a
stage-2 PASS + human approval (board 2026-06-09). A book produces ``{symbol: weight}`` via a named PROVIDER
(registered in code), so the JSON stays declarative and no callables are serialized.
"""
from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Callable, Optional

PROJECT_ROOT = Path(__file__).resolve().parents[1]
REGISTRY_PATH = PROJECT_ROOT / "config" / "live_strategies.json"

# name -> callable(asof_date) -> {symbol: target_weight}.  Providers are registered in code (BOREAS, a frozen
# forge spec, etc.); the registry JSON references them by name.
PROVIDERS: dict[str, Callable] = {}


def register_provider(name: str):
    def deco(fn: Callable) -> Callable:
        PROVIDERS[name] = fn
        return fn
    return deco


@dataclass
class DeployedStrategy:
    name: str
    provider: str                       # key into PROVIDERS
    state: str = "shadow"               # shadow | canary | live
    broker: str = "alpaca"             # registry broker name (alpaca | ib)
    capital: float = 0.0               # deployable equity slice (USD); canary <= 250 per board
    approved: bool = False             # human-approved for real-money execution
    specs: dict = field(default_factory=dict)        # {symbol: {multiplier, lot, min_notional}}
    expectation: dict = field(default_factory=dict)  # {daily_mean, daily_std, sharpe} (modeled backtest)

    def target_portfolio(self, asof_date) -> dict:
        fn = PROVIDERS.get(self.provider)
        if fn is None:
            return {}
        return fn(asof_date) or {}


def load() -> list[DeployedStrategy]:
    if not REGISTRY_PATH.exists():
        return []
    try:
        rows = json.loads(REGISTRY_PATH.read_text()) or []
        return [DeployedStrategy(**r) for r in rows]
    except Exception:
        return []


def save(strategies: list[DeployedStrategy]) -> None:
    REGISTRY_PATH.parent.mkdir(parents=True, exist_ok=True)
    REGISTRY_PATH.write_text(json.dumps([asdict(s) for s in strategies], indent=2))


def deployed(state: Optional[str] = None) -> list[DeployedStrategy]:
    out = load()
    return [s for s in out if state is None or s.state == state]
