"""Task-local research credentials; never mutate process environment or graph state."""

from contextlib import contextmanager
from contextvars import ContextVar

_resolver = ContextVar("tradingagents_data_connection_resolver", default=None)


@contextmanager
def data_credentials_scope(resolver):
    token = _resolver.set(resolver)
    try:
        yield
    finally:
        _resolver.reset(token)


def configured_credentials(provider):
    """None is legacy CLI compatibility; an empty mapping is explicit Web unconfigured/disabled."""
    resolver = _resolver.get()
    return resolver(provider) if resolver is not None else None
