"""Private per-execution tool observation; no Web imports or global environment mutation."""

from contextlib import contextmanager
from contextvars import ContextVar


class EvidenceCaptureError(RuntimeError):
    """The evidence recorder failed; the worker must not claim a complete capture."""


_sink = ContextVar("research_evidence_sink", default=None)


@contextmanager
def evidence_scope(sink):
    token = _sink.set(sink)
    try:
        yield
    finally:
        _sink.reset(token)


def observed_call(method, provider, args, kwargs, operation):
    """Observe the actual adapter call, including failed fallback attempts.

    Only the private recorder sees the response. It controls field allowlists,
    size limits and persistence. Exceptions are never sent as strings.
    """
    sink = _sink.get()
    if sink is None:
        return operation()
    try:
        result = operation()
    except Exception as error:
        sink(method, provider, args, kwargs, None, "error", type(error).__name__)
        raise
    sink(method, provider, args, kwargs, result, "success", None)
    return result
