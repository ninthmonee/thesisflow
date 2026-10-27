"""Optional private run budget for direct auxiliary model requests, including retries."""

from contextlib import contextmanager
from contextvars import ContextVar

_observer = ContextVar("tradingagents_execution_budget", default=None)


@contextmanager
def execution_budget_scope(observer):
    token = _observer.set(observer)
    try:
        yield
    finally:
        _observer.reset(token)


def reserve_model_call():
    observer = _observer.get()
    return observer.reserve_model_call() if observer is not None else None


def finish_model_call(reservation, usage=None):
    observer = _observer.get()
    if observer is not None and reservation is not None:
        observer.finish_model_call(reservation, usage)
