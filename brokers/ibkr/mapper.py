"""Ticker mapping between Atlas and IBKR Client Portal API formats.

Atlas uses yfinance format:
  ASX:   BHP.AX, CBA.AX, IOZ.AX
  SP500: AAPL, MSFT, GOOGL  (plain symbols, no suffix)

IBKR REST API uses contract IDs (conid) — integer identifiers.
This module handles:
  1. Atlas ticker ↔ bare symbol extraction
  2. conid resolution (via /iserver/secdef/search endpoint)
  3. Caching of conid lookups to avoid repeated API calls

All conversion happens at the broker boundary — Atlas internals
never see conids.
"""

from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Optional

logger = logging.getLogger("atlas.broker.ibkr.mapper")

# ═══════════════════════════════════════════════════════════════
# Market config
# ═══════════════════════════════════════════════════════════════

_MARKET_CONFIG = {
    "asx": {"suffix": ".AX", "exchange": "ASX", "currency": "AUD"},
    "sp500": {"suffix": "", "exchange": "NASDAQ;NYSE;AMEX", "currency": "USD"},
    "hk": {"suffix": ".HK", "exchange": "SEHK", "currency": "HKD"},
    "lse": {"suffix": ".L", "exchange": "LSE", "currency": "GBP"},
}

# ═══════════════════════════════════════════════════════════════
# Conid cache — persisted to disk so we don't re-resolve every run
# ═══════════════════════════════════════════════════════════════

_CACHE_DIR = Path(__file__).parent.parent.parent / "data" / "cache" / "ibkr"
_CONID_CACHE: dict[str, int] = {}
_CACHE_LOADED = False


def _load_cache():
    global _CONID_CACHE, _CACHE_LOADED
    if _CACHE_LOADED:
        return
    cache_file = _CACHE_DIR / "conid_cache.json"
    if cache_file.exists():
        try:
            _CONID_CACHE = json.loads(cache_file.read_text())
            logger.debug("Loaded %d conids from cache", len(_CONID_CACHE))
        except Exception:
            _CONID_CACHE = {}
    _CACHE_LOADED = True


def _save_cache():
    _CACHE_DIR.mkdir(parents=True, exist_ok=True)
    cache_file = _CACHE_DIR / "conid_cache.json"
    cache_file.write_text(json.dumps(_CONID_CACHE, indent=2))


def get_cached_conid(ticker: str) -> Optional[int]:
    """Get conid from cache. Returns None if not cached."""
    _load_cache()
    return _CONID_CACHE.get(ticker)


def set_cached_conid(ticker: str, conid: int):
    """Store conid in cache."""
    _load_cache()
    _CONID_CACHE[ticker] = conid
    _save_cache()


def clear_cache():
    """Clear the conid cache."""
    global _CONID_CACHE
    _CONID_CACHE = {}
    _save_cache()


# ═══════════════════════════════════════════════════════════════
# Ticker conversion
# ═══════════════════════════════════════════════════════════════

def strip_suffix(ticker: str, market_id: str = "asx") -> str:
    """Strip yfinance suffix to get bare symbol.

    >>> strip_suffix('BHP.AX', 'asx')
    'BHP'
    >>> strip_suffix('AAPL', 'sp500')
    'AAPL'
    """
    cfg = _MARKET_CONFIG.get(market_id, _MARKET_CONFIG["asx"])
    suffix = cfg["suffix"]
    if suffix and ticker.upper().endswith(suffix):
        return ticker[: -len(suffix)].upper()
    return ticker.upper()


def to_atlas(symbol: str, exchange: str = "") -> str:
    """Convert bare symbol + exchange to Atlas/yfinance format.

    >>> to_atlas('BHP', 'ASX')
    'BHP.AX'
    >>> to_atlas('AAPL', 'NASDAQ')
    'AAPL'
    """
    exchange = exchange.upper()
    if exchange == "ASX":
        return f"{symbol.upper()}.AX"
    elif exchange in ("LSE", "LSEETF"):
        return f"{symbol.upper()}.L"
    elif exchange == "SEHK":
        return f"{symbol.upper()}.HK"
    # US / SMART / NASDAQ / NYSE / other — no suffix
    return symbol.upper()


def to_conid_lookup(ticker: str, market_id: str = "asx") -> dict:
    """Build the parameters for IBKR /iserver/secdef/search endpoint.

    Returns dict with 'symbol' and optional 'secType', 'exchange' for
    narrowing the search.

    >>> to_conid_lookup('BHP.AX', 'asx')
    {'symbol': 'BHP', 'secType': 'STK', 'exchange': 'ASX'}
    >>> to_conid_lookup('AAPL', 'sp500')
    {'symbol': 'AAPL', 'secType': 'STK'}
    """
    cfg = _MARKET_CONFIG.get(market_id, _MARKET_CONFIG["asx"])
    symbol = strip_suffix(ticker, market_id)
    result = {"symbol": symbol, "secType": "STK"}

    # For non-US markets, specify exchange to disambiguate
    if market_id != "sp500":
        result["exchange"] = cfg["exchange"]

    return result


def get_exchange(market_id: str) -> str:
    """Get the primary exchange for a market."""
    return _MARKET_CONFIG.get(market_id, {}).get("exchange", "ASX")


def get_currency(market_id: str) -> str:
    """Get the currency for a market."""
    return _MARKET_CONFIG.get(market_id, {}).get("currency", "AUD")
