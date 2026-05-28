"""Extractors -- turn discovery artifacts (PDFs, specs JSON) into knowledge-layer rows.

Phase 1 of the research-system DB consolidation.  Each extractor is a small,
idempotent unit that reads from a known on-disk location and writes to the
sources/claims tables via db.knowledge.
"""
