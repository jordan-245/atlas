"""Atlas Research System — sweep/runner pipeline with unified models."""

# research.models uses fcntl (Unix-only) for file locking.  On Windows the import
# fails; submodules of research (e.g. research.discovery.extractors) still need
# to load for tests.  Swallow the ImportError so subpackage imports keep working;
# callers that need the queue/journal helpers will get a clear ImportError when
# they reference them.
try:
    from research.models import (  # noqa: F401
        QueueEntry, ExperimentEnvelope, JournalEntry,
        ExperimentStatus, ExperimentType, Priority,
        read_queue, append_to_queue, update_queue_entry, claim_experiment,
        get_next_queued, read_journal, append_to_journal,
        load_experiment, list_experiments, generate_experiment_id,
        RESEARCH_DIR, QUEUE_PATH, JOURNAL_PATH, EXPERIMENTS_DIR, STRATEGIES_DIR,
    )

    __all__ = [
        "QueueEntry", "ExperimentEnvelope", "JournalEntry",
        "ExperimentStatus", "ExperimentType", "Priority",
        "read_queue", "append_to_queue", "update_queue_entry", "claim_experiment",
        "get_next_queued", "read_journal", "append_to_journal",
        "load_experiment", "list_experiments", "generate_experiment_id",
        "RESEARCH_DIR", "QUEUE_PATH", "JOURNAL_PATH", "EXPERIMENTS_DIR", "STRATEGIES_DIR",
    ]
except ImportError:
    __all__ = []
