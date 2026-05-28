"""Paper metadata extractor.

Scans research/discovery/papers/*.pdf, derives a deterministic source id,
sha256, and best-effort title/url from the filename, and INSERTs one row
per file into the sources table.

Idempotent -- INSERT OR IGNORE keyed on id; re-running on the same papers
directory produces zero new rows.  Returns the list of (source_id, path)
tuples processed (including ones that already existed).

What this DOES NOT do (deferred to Phase 1.5):
  - Parse the PDF first page for the real title (needs pdftotext / vision).
  - Parse authors from the PDF.  Only filename-derived placeholders for now.
  - Read the abstract.
The LLM metric-extraction pass will overwrite the placeholder title and
populate richer fields via a separate UPDATE path.
"""

from __future__ import annotations

import hashlib
import logging
import re
from pathlib import Path
from typing import Iterable, List, Optional, Tuple

from db.knowledge import insert_source

logger = logging.getLogger(__name__)

# arxiv filename patterns produced by research/discovery/arxiv_api.py:
#   <paper_id>.pdf      e.g. 2401.12345.pdf
#   <paper_id>v1.pdf    e.g. 2401.12345v1.pdf
#   <paper_id>_v2.pdf   (legacy)
# The leading number is YYMM, then dot, then 4-5 digit sequence, optional v<N>.
_ARXIV_FILENAME_RE = re.compile(
    r"^(?P<id>\d{4}\.\d{4,5})(?:v\d+)?\.pdf$",
    re.IGNORECASE,
)

# Older arxiv IDs use category/yymmnnn format (e.g. q-fin_0801001.pdf after
# arxiv_api.py's `/` -> `_` substitution).
_ARXIV_OLD_FILENAME_RE = re.compile(
    r"^(?P<cat>[a-z\-]+)_(?P<id>\d{7})(?:v\d+)?\.pdf$",
    re.IGNORECASE,
)


def _compute_sha256(path: Path, chunk: int = 1 << 16) -> str:
    """Stream-hash a file to avoid loading large PDFs into memory."""
    h = hashlib.sha256()
    with path.open("rb") as f:
        while True:
            block = f.read(chunk)
            if not block:
                break
            h.update(block)
    return h.hexdigest()


def _parse_arxiv_id(filename: str) -> Optional[str]:
    """Return the canonical arxiv id (without version suffix) or None."""
    m = _ARXIV_FILENAME_RE.match(filename)
    if m:
        return m.group("id")
    m = _ARXIV_OLD_FILENAME_RE.match(filename)
    if m:
        return f"{m.group('cat').replace('_', '/')}/{m.group('id')}"
    return None


def _derive_source_id(pdf_path: Path, sha256: str) -> Tuple[str, Optional[str]]:
    """Return (source_id, arxiv_id_or_None).

    Prefers arxiv ID when filename parses, falls back to sha8 prefix.
    """
    arxiv_id = _parse_arxiv_id(pdf_path.name)
    if arxiv_id is not None:
        return f"src-arxiv-{arxiv_id.replace('/', '-')}", arxiv_id
    return f"src-sha-{sha256[:8]}", None


def _arxiv_url(arxiv_id: str) -> str:
    return f"https://arxiv.org/abs/{arxiv_id}"


def extract_one(pdf_path: Path, *, atlas_root: Path) -> Tuple[str, bool]:
    """Process a single PDF.  Returns (source_id, was_new).

    was_new is best-effort: True if this is the first time we've seen this id,
    False if it was already in the DB.  Determined by querying after the
    INSERT OR IGNORE.
    """
    from db.knowledge import get_source  # local import to avoid cycle at module load

    if not pdf_path.exists():
        raise FileNotFoundError(pdf_path)

    sha256 = _compute_sha256(pdf_path)
    source_id, arxiv_id = _derive_source_id(pdf_path, sha256)

    # If already present, return early without re-inserting.
    if get_source(source_id) is not None:
        return source_id, False

    try:
        local_path = pdf_path.relative_to(atlas_root).as_posix()
    except ValueError:
        local_path = pdf_path.as_posix()

    title = f"arxiv:{arxiv_id}" if arxiv_id else pdf_path.stem
    url = _arxiv_url(arxiv_id) if arxiv_id else None
    venue = "arxiv" if arxiv_id else None

    insert_source(
        id=source_id,
        kind="paper",
        title=title,
        url=url,
        venue=venue,
        sha256=sha256,
        local_path=local_path,
        extracted_by="paper_metadata",
        notes=("Title is a placeholder derived from filename; replace via Phase 1.5 "
               "LLM metric-extraction pass." if arxiv_id else None),
    )
    return source_id, True


def extract_all(
    papers_dir: Path,
    *,
    atlas_root: Path,
) -> List[dict]:
    """Process every *.pdf in papers_dir.  Returns one result dict per file.

    Each result: {"path": str, "source_id": str, "was_new": bool, "error": str|None}
    """
    if not papers_dir.exists():
        logger.warning("papers_dir does not exist: %s", papers_dir)
        return []

    results: List[dict] = []
    for pdf in sorted(papers_dir.glob("*.pdf")):
        try:
            source_id, was_new = extract_one(pdf, atlas_root=atlas_root)
            results.append({
                "path": str(pdf),
                "source_id": source_id,
                "was_new": was_new,
                "error": None,
            })
        except Exception as exc:  # noqa: BLE001 -- continue across bad PDFs
            logger.warning("Failed to extract %s: %s", pdf.name, exc)
            results.append({
                "path": str(pdf),
                "source_id": None,
                "was_new": False,
                "error": str(exc),
            })

    return results
