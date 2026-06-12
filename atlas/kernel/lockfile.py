"""atlas.kernel.lockfile — cross-process coordination for data/live and the registry.

The 2026-06-12 architecture review: data/live + config/live_strategies.json have
multiple writers in TWO repos (atlas daily loop / record_*; crucible deploy + lifecycle)
synchronized only by cron timing. This module is the lock both sides take.

CONTRACT (also honored by crucible live/deploy.py via a duplicated ~10-line helper —
deliberately NOT imported across the repo seam):
  - Lock file: data/live/.lock (fcntl.flock, exclusive, blocking with timeout)
  - Every read-modify-write of live_strategies.json happens INSIDE the lock
  - Every JSON state write is atomic (tmp + os.replace) so readers never see a torn file
"""
from __future__ import annotations

import fcntl
import json
import os
import time
from contextlib import contextmanager
from pathlib import Path

from atlas.kernel.paths import LIVE_DATA_DIR

LOCK_PATH = LIVE_DATA_DIR / ".lock"
DEFAULT_TIMEOUT = 30.0  # seconds; writers are sub-second, so 30s means something is wedged


@contextmanager
def live_lock(timeout: float = DEFAULT_TIMEOUT):
    """Exclusive flock over data/live mutations. Blocks up to `timeout`, then raises."""
    LOCK_PATH.parent.mkdir(parents=True, exist_ok=True)
    fh = open(LOCK_PATH, "w")
    deadline = time.monotonic() + timeout
    try:
        while True:
            try:
                fcntl.flock(fh, fcntl.LOCK_EX | fcntl.LOCK_NB)
                break
            except BlockingIOError:
                if time.monotonic() >= deadline:
                    raise TimeoutError(
                        f"live_lock: could not acquire {LOCK_PATH} within {timeout}s — "
                        "another writer is wedged (check forward-paper / forge / lifecycle)")
                time.sleep(0.2)
        yield
    finally:
        try:
            fcntl.flock(fh, fcntl.LOCK_UN)
        finally:
            fh.close()


def atomic_write_json(path: Path, obj) -> None:
    """tmp + os.replace — readers never observe a torn/truncated JSON file."""
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(obj, indent=2, default=str))
    os.replace(tmp, path)
