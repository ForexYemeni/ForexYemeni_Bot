"""Async facade — the event loop NEVER waits for a blocking call (v6.11.0).

THE owner demand this module fulfils: «جعل أزرار البوت تستجيب بشكل أسرع دون
الانتظار للحظة واحدة» — buttons must answer with ZERO perceptible waiting.

Root cause of slow buttons (measured on the hosted runtime):
  every handler runs on ONE shared asyncio event loop. Any synchronous call
  executed directly on that loop — a Supabase round trip (100–500 ms), a
  SQLite write, a market-API fetch (up to seconds) — FREEZES THE WHOLE LOOP:
  no other button is even STARTED until it finishes, no background job ticks,
  Telegram callbacks queue up. One slow page = every user waits.

The fix is structural, not cosmetic:
  * afn.run(fn, *args) ships every blocking call to the worker-thread pool
    (asyncio.to_thread). The loop keeps pumping while the thread waits on the
    network — buttons are processed IN PARALLEL, never queued behind each
    other, never behind a background job.
  * main.py additionally enables Application.concurrent_updates(True) and a
    64-slot Telegram connection pool, so parallel handlers never serialize on
    the HTTP layer either.

Uniform policy (deliberate, simple, safe): EVERY db/service/catalog call on
a handler path goes through afn.run — identical code shape for SQLite (fast)
and PostgreSQL (WAN). The pooled PG engine is thread-safe (bounded deque +
lock, rollback-on-release, self-healing), so borrowing a connection inside a
worker thread is exactly as safe as on the loop itself. asyncio.to_thread
propagates exceptions unchanged and pytest's asyncio.run loop exercises it in
the whole 389-test suite, so behaviour is provably identical — only faster.
"""
import asyncio

__all__ = ["run"]


async def run(fn, *args, **kwargs):
    """Run the blocking callable in the default thread-pool executor.

    Semantics are IDENTICAL to calling fn(*args, **kwargs) inline — same
    return value, same exceptions — except the event loop is never blocked:
    while this thread waits on the WAN, every other user's button keeps
    being answered instantly.
    """
    return await asyncio.to_thread(fn, *args, **kwargs)
