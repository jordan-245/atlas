"""Atlas Broker Registry.

Single source of truth for broker instantiation. All code that needs
a broker MUST use this module — never import broker classes directly.

Supported brokers:
    paper  — simulated (always available, safe default)
    moomoo — Moomoo/Futu via OpenD gateway (ASX, SP500)
    ibkr   — Interactive Brokers via TWS/Gateway (ASX, SP500, etc.)

Broker selection is driven by config:
    trading.broker   = "paper" | "moomoo" | "ibkr"
    trading.live_enabled = true | false

Live trading requires BOTH broker != "paper" AND live_enabled == true.

Usage:
    from brokers.registry import get_broker, get_live_broker

    broker = get_broker("asx", config)          # Read-only / paper
    live   = get_live_broker(config)             # Live broker (or None)
"""

from __future__ import annotations

import logging
from typing import Any, Dict, Optional

from brokers.base import BrokerAdapter

logger = logging.getLogger("atlas.brokers")

# ═══════════════════════════════════════════════════════════════
# Broker catalogue — add new brokers here
# ═══════════════════════════════════════════════════════════════

_BROKER_FACTORIES = {}  # populated lazily to avoid import cycles


def _register_defaults():
    """Register built-in broker factories (lazy, called once)."""
    if _BROKER_FACTORIES:
        return

    _BROKER_FACTORIES["paper"] = _make_paper_broker

    try:
        from brokers.moomoo.broker import MomooBroker  # noqa: F401
        _BROKER_FACTORIES["moomoo"] = _make_moomoo_broker
    except Exception:
        logger.debug("moomoo broker not available (import failed)")

    try:
        from brokers.ibkr.broker import IBKRBroker  # noqa: F401
        _BROKER_FACTORIES["ibkr"] = _make_ibkr_broker
    except Exception:
        logger.debug("ibkr broker module not available")


def available_brokers() -> list[str]:
    """Return names of all brokers whose dependencies are installed."""
    _register_defaults()
    return list(_BROKER_FACTORIES.keys())


# ═══════════════════════════════════════════════════════════════
# Public API
# ═══════════════════════════════════════════════════════════════

def get_broker(market_id: str, config: Dict[str, Any]) -> BrokerAdapter:
    """Instantiate the appropriate broker for a market.

    Returns a paper broker unless live is explicitly configured.
    This is the standard path for signal generation, EOD settlement,
    and dashboard data.
    """
    _register_defaults()
    market_id = market_id.lower().strip()
    broker_name = _resolve_broker_name(config)
    live_enabled = config.get("trading", {}).get("live_enabled", False)

    if broker_name == "paper" or not live_enabled:
        return _make_paper_broker(market_id, config)

    factory = _BROKER_FACTORIES.get(broker_name)
    if factory and factory != _make_paper_broker:
        return factory(market_id, config, live=live_enabled)

    logger.warning(
        "Broker '%s' not available (installed: %s) — falling back to paper",
        broker_name, list(_BROKER_FACTORIES.keys()),
    )
    return _make_paper_broker(market_id, config)


def get_live_broker(config: Dict[str, Any]) -> Optional[BrokerAdapter]:
    """Create a live broker instance if configured and available.

    Returns None if live trading is not enabled. The broker is NOT
    connected — call broker.connect() after pre-flight checks.

    This replaces the old get_live_executor() pattern. LiveExecutor
    now uses this internally.
    """
    _register_defaults()
    live_enabled = config.get("trading", {}).get("live_enabled", False)
    broker_name = _resolve_broker_name(config)

    if not live_enabled or broker_name == "paper":
        logger.debug(
            "Live broker not available (broker=%s, live_enabled=%s)",
            broker_name, live_enabled,
        )
        return None

    factory = _BROKER_FACTORIES.get(broker_name)
    if not factory or factory == _make_paper_broker:
        logger.warning("Broker '%s' not registered or unavailable", broker_name)
        return None

    market_id = config.get("market", "asx")
    return factory(market_id, config, live=True)


def get_live_executor(config: Dict[str, Any]) -> Optional["LiveExecutor"]:
    """Create a LiveExecutor if live trading is configured.

    Returns None if live trading is not enabled. The executor is NOT
    connected — call executor.connect() after pre-flight checks.

    Backward-compatible wrapper — new code should use get_live_broker().
    """
    live_enabled = config.get("trading", {}).get("live_enabled", False)
    broker_name = _resolve_broker_name(config)

    if not live_enabled or broker_name == "paper":
        logger.debug(
            "Live executor not available (broker=%s, live_enabled=%s)",
            broker_name, live_enabled,
        )
        return None

    from brokers.live_executor import LiveExecutor
    return LiveExecutor(config)


# ═══════════════════════════════════════════════════════════════
# Internal helpers
# ═══════════════════════════════════════════════════════════════

def _resolve_broker_name(config: Dict[str, Any]) -> str:
    """Extract and normalise broker name from config."""
    return config.get("trading", {}).get("broker", "paper").lower().strip()


def _make_paper_broker(
    market_id: str, config: Dict[str, Any], **kwargs,
) -> BrokerAdapter:
    from brokers.paper import PaperBroker
    return PaperBroker(config)


def _make_moomoo_broker(
    market_id: str, config: Dict[str, Any], live: bool = False, **kwargs,
) -> BrokerAdapter:
    from brokers.moomoo.broker import MomooBroker
    return MomooBroker(config, live=live)


def _make_ibkr_broker(
    market_id: str, config: Dict[str, Any], live: bool = False, **kwargs,
) -> BrokerAdapter:
    from brokers.ibkr.broker import IBKRBroker
    return IBKRBroker(config, live=live)
