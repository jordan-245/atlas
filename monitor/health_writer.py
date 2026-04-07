"""Health monitoring writer — thin wrapper around atlas_db heartbeat/log.

Usage:
    from monitor.health_writer import heartbeat, log_error, log_warning, log_info

    heartbeat("premarket", "running", {"stage": "ingest"})
    log_error("live_executor", "Order placement failed", {"ticker": "AAPL", "error": str(e)})
"""
import logging
from typing import Optional, Dict

logger = logging.getLogger(__name__)


def heartbeat(service: str, status: str, detail: Optional[Dict] = None) -> None:
    """Record a heartbeat for a service. Non-fatal on failure."""
    try:
        from db.atlas_db import record_heartbeat
        record_heartbeat(service=service, status=status, detail=detail)
    except Exception as exc:
        logger.debug("heartbeat write failed (non-fatal): %s", exc)


def log_info(service: str, message: str, detail: Optional[Dict] = None) -> None:
    """Write an info-level system log entry. Non-fatal on failure."""
    _write_log("info", service, message, detail)


def log_warning(service: str, message: str, detail: Optional[Dict] = None) -> None:
    """Write a warning-level system log entry. Non-fatal on failure."""
    _write_log("warning", service, message, detail)


def log_error(service: str, message: str, detail: Optional[Dict] = None) -> None:
    """Write an error-level system log entry. Non-fatal on failure."""
    _write_log("error", service, message, detail)


def log_critical(service: str, message: str, detail: Optional[Dict] = None) -> None:
    """Write a critical-level system log entry. Non-fatal on failure."""
    _write_log("critical", service, message, detail)


def _write_log(level: str, service: str, message: str, detail: Optional[Dict] = None) -> None:
    """Internal: write a system log entry. Catches all exceptions."""
    try:
        from db.atlas_db import record_system_log
        record_system_log(level=level, service=service, message=message, detail=detail)
    except Exception as exc:
        logger.debug("system_log write failed (non-fatal): %s", exc)
