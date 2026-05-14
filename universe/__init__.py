"""Atlas Universe Builder — stock universe construction and filtering."""

from universe.builder import build_universe, load_universe, get_universe_tickers
from universe.definitions import (
    UNIVERSES,
    get_universe,
    get_universe_tickers as get_universe_tickers_static,
    get_all_etf_tickers,
    list_universes,
)

__all__ = [
    # builder
    "build_universe",
    "load_universe",
    "get_universe_tickers",
    # definitions
    "UNIVERSES",
    "get_universe",
    "get_universe_tickers_static",
    "get_all_etf_tickers",
    "list_universes",
]

# ── Fail-fast: validate universe disjointness at import time ──────────────────
# If a developer accidentally adds a ticker to two universes, this raises
# AssertionError immediately on import rather than silently corrupting
# per-market equity calculations downstream. (Task 2.7, audit 2026-05-14)
try:
    from universe.builder import assert_universes_disjoint as _check_disjoint
    _check_disjoint()
    del _check_disjoint
except ImportError:
    # builder may not be importable in some early-bootstrap scenarios
    pass
