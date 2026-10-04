"""Control redundant store-open logs without changing the retained error state."""
from contextvars import ContextVar

# App/server callers retain raw diagnostics; CLI emits its own error envelope.
emit_open_failure = ContextVar("emit_store_open_failure", default=True)
