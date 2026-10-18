"""Thread/context-local cooperative cancellation for reused numerical searches."""
from contextlib import contextmanager
from contextvars import ContextVar

_CANCEL = ContextVar("pipeforge_search_cancel", default=None)


def search_cancelled():
    callback = _CANCEL.get()
    return bool(callback and callback())


@contextmanager
def cancellation_scope(callback):
    token = _CANCEL.set(callback)
    try:
        yield
    finally:
        _CANCEL.reset(token)
