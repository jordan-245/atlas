#!/usr/bin/env python3
"""Phase 7: render the SQL knowledge layer to markdown under research/wiki/.

Run:
    python3 scripts/materialize_wiki.py                    # dry-run, prints plan
    python3 scripts/materialize_wiki.py --apply
    python3 scripts/materialize_wiki.py --apply --out-dir /tmp/wiki-preview
"""

from __future__ import annotations

import argparse
import json
import logging
import sys
from pathlib import Path

_HERE = Path(__file__).resolve()
_ATLAS_ROOT = _HERE.parent.parent
sys.path.insert(0, str(_ATLAS_ROOT))

LOG_PATH = _ATLAS_ROOT / "logs" / "materialize_wiki.log"


def _setup_logging(verbose: bool) -> None:
    LOG_PATH.parent.mkdir(parents=True, exist_ok=True)
    logging.basicConfig(
        level=logging.DEBUG if verbose else logging.INFO,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
        handlers=[
            logging.FileHandler(LOG_PATH, encoding="utf-8"),
            logging.StreamHandler(sys.stderr),
        ],
    )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--apply", action="store_true",
                        help="Actually write files (default: dry-run)")
    parser.add_argument("--out-dir", default=None,
                        help="Output directory (default: research/wiki/ in this repo)")
    parser.add_argument("--verbose", action="store_true")
    args = parser.parse_args(argv)

    _setup_logging(args.verbose)
    log = logging.getLogger("materialize_wiki")

    from research.wiki.materializer import materialize
    target = Path(args.out_dir) if args.out_dir else None

    log.info("Wiki materializer")
    log.info("  mode:    %s", "APPLY" if args.apply else "DRY-RUN")
    log.info("  out_dir: %s", target or "<default research/wiki/>")

    result = materialize(out_dir=target, write=args.apply)
    summary = {
        "mode": "apply" if args.apply else "dry-run",
        "out_dir": str(result.out_dir),
        "strategies_rendered": result.strategies_rendered,
        "contradictions_emitted": result.contradictions_emitted,
        "files": result.files_written,
    }
    print(json.dumps(summary, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
