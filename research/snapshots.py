"""Snapshot directory discovery for research workflows."""
from pathlib import Path

_ATLAS_ROOT = Path(__file__).resolve().parent.parent


def find_latest_snapshot(market: str) -> str:
    """Find the most recent snapshot directory matching *market*.

    Searches ``data/snapshots/`` for directories whose name contains the market
    identifier (case-insensitive), then returns the one with the latest
    modification time.

    Args:
        market: Market ID (e.g. ``'sp500'``).

    Returns:
        Snapshot directory name (e.g. ``'sp500_v3_unadj_20260310_7yr'``).

    Raises:
        RuntimeError: If no snapshot directory exists for this market.
    """
    snapshots_root = _ATLAS_ROOT / "data" / "snapshots"
    if not snapshots_root.exists():
        raise RuntimeError(
            f"Snapshots directory not found: {snapshots_root}. "
            f"Create a snapshot first with: "
            f"from scripts.strategy_evaluator import save_snapshot; "
            f"save_snapshot('{market}', '<snapshot_id>')"
        )

    matching = [
        d for d in snapshots_root.iterdir()
        if d.is_dir() and market.lower() in d.name.lower()
    ]
    if not matching:
        raise RuntimeError(
            f"No snapshot found for market '{market}' in {snapshots_root}. "
            f"Create one first with: "
            f"from scripts.strategy_evaluator import save_snapshot; "
            f"save_snapshot('{market}', '<snapshot_id>')"
        )

    matching.sort(key=lambda d: d.stat().st_mtime, reverse=True)
    return matching[0].name
