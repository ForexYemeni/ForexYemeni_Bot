"""Shared safety gates and a cross-process database issuance mutex.

No network request belongs inside serialized(): validate transport beforehand.
SQLite uses BEGIN IMMEDIATE; PostgreSQL uses a transaction advisory lock.
"""
from contextlib import contextmanager
from fysb import db, config
from fysb.engines import risk


@contextmanager
def serialized():
    with db.tx(immediate=True) as c:
        if config.USE_PG:
            c.execute("SELECT pg_advisory_xact_lock(8914001)")
        yield c


def policy(symbol, strategy, direction, rr, volatility, *, source="AUTO", user_id=0):
    if db.kill_switch_on():
        return False, "global kill switch engaged"
    if strategy and strategy != "TRUE_ANALYSIS":
        from fysb.engines.signal_engine import _allowed_strategies
        if strategy not in _allowed_strategies(symbol, ""):
            return False, "strategy disabled or unverified"
    decision = risk.check_signal(symbol, rr, volatility, source=source,
                                 created_by=user_id, direction=direction)
    if not decision.allowed:
        return False, decision.reason
    from fysb.engines.intelligence import strategy_quarantine_status
    quarantine = strategy_quarantine_status(strategy, symbol)
    if quarantine.get("quarantined"):
        return False, quarantine.get("reason") or "strategy quarantined"
    return True, ""
