#!/usr/bin/env python3
"""Atlas Strategy Factory — generate strategy code from descriptions.

V1: Template-based generation with validation.
Future: Full LLM code generation via coordinator agent.

Usage:
    from research.strategy_factory import build_strategy
    result = build_strategy("donchian_breakout", description="...", reference="...")
"""

import importlib
import importlib.util
import logging
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict

ATLAS_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ATLAS_ROOT))

logger = logging.getLogger("strategy_factory")

STRATEGIES_DIR = ATLAS_ROOT / "research" / "strategies"
VAULT_DIR = ATLAS_ROOT / "research" / "vault"


# ─── Strategy Template ──────────────────────────────────────────────────────

STRATEGY_TEMPLATE = '''"""
Atlas {human_name} Strategy
========================================
{description}

Reference: {reference}
Generated: {timestamp}

Config Section: strategies.{strategy_name}
"""

import logging
from datetime import datetime
from typing import Any, Dict, List

import numpy as np
import pandas as pd

from strategies.base import BaseStrategy, Signal
from utils.helpers import calc_atr, calc_rsi, calc_position_size

logger = logging.getLogger(__name__)


class {class_name}(BaseStrategy):
    """{short_description}"""

    def __init__(self, config: Dict[str, Any]):
        super().__init__(config)
        strat_cfg = config.get("strategies", {{}}).get("{strategy_name}", {{}})

        # Core parameters
        self.atr_period = strat_cfg.get("atr_period", 14)
        self.atr_stop_mult = strat_cfg.get("atr_stop_mult", 2.0)
        self.max_hold_days = strat_cfg.get("max_hold_days", 10)
        self.sma200_filter = strat_cfg.get("sma200_filter", True)
        # TODO: Add strategy-specific parameters from description

        self._logger.info(f"{class_name} initialized")

    @property
    def name(self) -> str:
        return "{strategy_name}"

    def generate_signals(
        self,
        data: Dict[str, pd.DataFrame],
        equity: float,
        existing_positions: List[Dict[str, Any]],
    ) -> List[Signal]:
        """Generate {strategy_name} entry signals."""
        signals: List[Signal] = []
        held = self._get_held_tickers(existing_positions)

        for ticker, df in data.items():
            if ticker in held:
                continue
            if not self._can_open_position(existing_positions):
                break
            if not self._has_sufficient_data(df, 252):
                continue

            # TODO: Implement entry logic
            # {description}
            pass

        self._logger.info(f"{{self.name}}: {{len(signals)}} signals from {{len(data)}} tickers")
        return signals

    def check_exits(
        self,
        data: Dict[str, pd.DataFrame],
        positions: List[Dict[str, Any]],
    ) -> List[Dict[str, Any]]:
        """Check positions for exit conditions."""
        exits = []
        for pos in positions:
            if pos.get("strategy") != self.name:
                continue
            ticker = pos.get("ticker")
            if not ticker or ticker not in data:
                continue

            df = data[ticker]
            if df.empty:
                continue

            current_price = float(df["close"].iloc[-1])
            stop_price = pos.get("stop_price", 0)

            # Stop-loss
            if stop_price and current_price <= stop_price:
                exits.append({{
                    "ticker": ticker,
                    "reason": "stop_hit",
                    "exit_price": current_price,
                    "details": f"Price {{current_price:.2f}} <= stop {{stop_price:.2f}}",
                }})
                continue

            # Time exit
            entry_date = pos.get("entry_date")
            if entry_date:
                if isinstance(entry_date, str):
                    entry_date = pd.Timestamp(entry_date)
                days_held = (df.index[-1] - entry_date).days
                if days_held >= self.max_hold_days:
                    exits.append({{
                        "ticker": ticker,
                        "reason": "time_exit",
                        "exit_price": current_price,
                        "details": f"Held {{days_held}} days >= max {{self.max_hold_days}}",
                    }})
                    continue

            # TODO: Add strategy-specific exit logic

        return exits


# Default parameter grid for optimization
PARAM_GRID = {{
    "atr_stop_mult": [1.5, 2.0, 2.5, 3.0],
    "max_hold_days": [5, 10, 15, 20],
}}
'''


def generate_strategy_file(
    strategy_name: str,
    description: str = "",
    reference: str = "",
    overwrite: bool = False,
) -> Path:
    """Generate a strategy Python file from template.

    Args:
        strategy_name: snake_case name (e.g., 'donchian_breakout')
        description: strategy description
        reference: academic/practical reference
        overwrite: overwrite existing file

    Returns:
        Path to generated file.
    """
    output_path = STRATEGIES_DIR / f"{strategy_name}.py"

    if output_path.exists() and not overwrite:
        logger.info("Strategy file already exists: %s", output_path)
        return output_path

    class_name = "".join(word.capitalize() for word in strategy_name.split("_"))
    human_name = strategy_name.replace("_", " ").title()
    short_description = description.split(".")[0] if description else human_name

    code = STRATEGY_TEMPLATE.format(
        human_name=human_name,
        strategy_name=strategy_name,
        class_name=class_name,
        description=description or "TODO: Add description",
        reference=reference or "TODO: Add reference",
        short_description=short_description,
        timestamp=datetime.now(timezone.utc).isoformat(),
    )

    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(code)
    logger.info("Generated strategy file: %s", output_path)
    return output_path


def validate_strategy(strategy_name: str) -> Dict[str, Any]:
    """Validate that a strategy file is loadable and functional.

    Returns:
        {"valid": bool, "errors": [...], "class_name": str,
         "has_signals": bool, "has_exits": bool}
    """
    result = {
        "valid": False, "errors": [],
        "class_name": None, "has_signals": False, "has_exits": False,
    }

    module_path = STRATEGIES_DIR / f"{strategy_name}.py"
    if not module_path.exists():
        result["errors"].append(f"File not found: {module_path}")
        return result

    try:
        spec = importlib.util.spec_from_file_location(
            f"research.strategies.{strategy_name}", module_path,
        )
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
    except Exception as e:
        result["errors"].append(f"Import error: {e}")
        return result

    # Find BaseStrategy subclass
    from strategies.base import BaseStrategy
    strategy_cls = None
    for attr_name in dir(mod):
        attr = getattr(mod, attr_name)
        if (isinstance(attr, type) and issubclass(attr, BaseStrategy)
                and attr is not BaseStrategy and attr_name != "BaseStrategy"):
            strategy_cls = attr
            result["class_name"] = attr_name
            break

    if strategy_cls is None:
        result["errors"].append("No BaseStrategy subclass found")
        return result

    # Try instantiation
    try:
        config = {"strategies": {strategy_name: {}}, "risk": {"max_open_positions": 5}}
        instance = strategy_cls(config)
    except Exception as e:
        result["errors"].append(f"Instantiation error: {e}")
        return result

    result["has_signals"] = callable(getattr(instance, "generate_signals", None))
    result["has_exits"] = callable(getattr(instance, "check_exits", None))

    if not result["has_signals"]:
        result["errors"].append("generate_signals not callable")
    if not result["has_exits"]:
        result["errors"].append("check_exits not callable")

    result["valid"] = len(result["errors"]) == 0
    return result


def create_strategy_vault_card(
    strategy_name: str, description: str = "", reference: str = "",
) -> Path:
    """Create a vault strategy card for a newly generated strategy."""
    human_name = strategy_name.replace("_", " ").title()
    card_path = VAULT_DIR / "Strategies" / f"{human_name}.md"

    if card_path.exists():
        return card_path

    card_path.parent.mkdir(parents=True, exist_ok=True)

    stype = strategy_name.split("_")[0] if "_" in strategy_name else "unknown"
    content = f"""---
tags: [strategy, strategy/{strategy_name.replace('_', '-')}, status/not-built]
status: not_built
type: {stype}
tier: 1
---

# {human_name}

## Overview
{description or 'TODO: Add description'}

**Reference:** {reference or 'TODO'}
**Status:** Not Built
**Tier:** 1

## Parameters
_Not yet configured — awaiting strategy build._

## Experiment History
| Date | Experiment | Stage | Verdict | Sharpe | Trades |
|------|-----------|-------|---------|--------|--------|

## Key Learnings
_No experiments run yet._
"""
    card_path.write_text(content)
    return card_path


def build_strategy(
    strategy_name: str,
    description: str = "",
    reference: str = "",
) -> Dict[str, Any]:
    """Full pipeline: generate file → validate → create vault card.

    Returns:
        {"success": bool, "file_path": str, "validation": dict, "vault_card": str}
    """
    file_path = generate_strategy_file(strategy_name, description, reference)
    validation = validate_strategy(strategy_name)
    vault_card = create_strategy_vault_card(strategy_name, description, reference)

    return {
        "success": validation["valid"],
        "file_path": str(file_path),
        "validation": validation,
        "vault_card": str(vault_card),
    }
