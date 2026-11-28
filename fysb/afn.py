"""Offload blocking work without making button reads queue behind background I/O.

`run` preserves return values, exceptions and context variables. Callback routing
opts into a small dedicated pool; other work continues using asyncio's default
executor. A child task does NOT inherit priority (e.g. a broadcast started from
a button), so long-lived background jobs cannot occupy the reserved UI workers.

This reduces one source of latency; finite workers, database locks, Telegram
and external providers still prevent any zero-latency guarantee.
"""
import asyncio
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
from contextvars import ContextVar, copy_context
import functools
import threading
import weakref

__all__ = ["run", "interactive", "shutdown"]

_UI_WORKERS = 8
_ui_pool = None
_pool_lock = threading.Lock()
_interactive_owner: ContextVar = ContextVar("interactive_owner", default=None)


def _executor():
    global _ui_pool
    with _pool_lock:
        if _ui_pool is None:
            _ui_pool = ThreadPoolExecutor(max_workers=_UI_WORKERS, thread_name_prefix="fysb-ui")
        return _ui_pool


@contextmanager
def interactive():
    """Reserve UI priority only for this task, not tasks it creates."""
    token = _interactive_owner.set(weakref.ref(asyncio.current_task()))
    try:
        yield
    finally:
        _interactive_owner.reset(token)


async def run(fn, *args, **kwargs):
    """Execute a blocking callable off-loop, retaining context and exceptions."""
    owner = _interactive_owner.get()
    if owner is not None and owner() is asyncio.current_task():
        call = functools.partial(copy_context().run, fn, *args, **kwargs)
        return await asyncio.get_running_loop().run_in_executor(_executor(), call)
    return await asyncio.to_thread(fn, *args, **kwargs)


def stats() -> dict:
    """v8.14.13 — live UI-pool depth: the royal main valve's telemetry.
    queued > 0 for a sustained window = worker starvation (stalled WAN DB
    calls pinning all 8 workers) — the exact «كل الأزرار لا تفتح شي»."""
    with _pool_lock:
        pool = _ui_pool
    if pool is None:
        return {"workers": _UI_WORKERS, "active": 0, "queued": 0}
    return {"workers": _UI_WORKERS,
            "active": len(getattr(pool, "_threads", []) or []),
            "queued": getattr(pool, "_work_queue", None).qsize()
            if getattr(pool, "_work_queue", None) is not None else 0}


def recycle() -> None:
    """v8.14.13 — starved-pool self-heal: drop the executor (idle threads
    die; stuck threads finish or error on their own) — the next afn.run
    builds a fresh pool instantly. Never cancels DB writes mid-flight."""
    global _ui_pool
    with _pool_lock:
        pool, _ui_pool = _ui_pool, None
    if pool is not None:
        pool.shutdown(wait=False)


def shutdown():
    """Release idle UI workers on application shutdown; never cancel DB writes.

    Running calls finish normally. The pool is lazy/recreatable for test loops
    and application restarts inside the same interpreter.
    """
    global _ui_pool
    with _pool_lock:
        pool, _ui_pool = _ui_pool, None
    if pool is not None:
        pool.shutdown(wait=False)
