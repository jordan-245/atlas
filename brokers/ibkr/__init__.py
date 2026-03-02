"""Interactive Brokers (IBKR) broker integration for Atlas.

Uses the Client Portal Web API (REST) via a local gateway.
Gateway can be run via IBeam (Docker) for headless authentication.

Requirements:
    - IBeam Docker container or Client Portal Gateway running
    - Gateway serves REST API at https://localhost:5000
    - No extra Python packages needed (uses stdlib requests)
"""

from brokers.ibkr.broker import IBKRBroker
from brokers.ibkr.mapper import to_atlas, to_conid_lookup

__all__ = ["IBKRBroker", "to_atlas", "to_conid_lookup"]
