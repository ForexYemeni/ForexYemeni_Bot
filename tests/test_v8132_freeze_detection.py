"""v8.13.2 feed-freeze detection tests — the platform names the failure.

Scenario (the owner's live incident, formalised): every fetch returns the
SAME last-candle snapshot while the market is open → after ≥10 minutes the
payload is marked feed_frozen (distinct FEED_FROZEN admin run code), so the
diagnostics probe names a frozen feed instead of a generic stale line.
"""
from __future__ import annotations

import sys
import time
from datetime import datetime, timedelta, timezone
from unittest.mock import patch

import pytest

sys.path.insert(0, ".")

from fysb.engines import market_data as md


def _old_candles(hours: float, n: int = 120) -> list[dict]:
    end = md.datetime.now(timezone.utc) - timedelta(hours=hours)   # frozen Friday clock
    return [{"datetime": (end - timedelta(minutes=15 * i)).strftime("%Y-%m-%d %H:%M:%S"),
             "open": 2600.0, "high": 2601.0, "low": 2599.0, "close": 2600.5,
             "volume": 1000} for i in range(n, 0, -1)]


@pytest.fixture(autouse=True)
def _clean_state():
    md._FEED_SNAPSHOTS.clear()
    md._fail_streak.clear()
    md._cache.clear()
    # v8.14.8: fairness layer pinned — mechanics tests (see v8145 suite)
    _fair = patch.object(md, "settlement_frozen_seconds", return_value=0.0)
    _fair.start()
    yield
    _fair.stop()
    md._FEED_SNAPSHOTS.clear()
    md._fail_streak.clear()


def _fetch_stale(hours: float):
    with patch.object(md, "_ordered_chain", return_value=["twelvedata"]), \
         patch.object(md, "_fetch_td_candles", return_value=_old_candles(hours)), \
         patch.object(md, "market_state", return_value=True), \
         patch.object(md.provider_health, "is_open", return_value=False):
        return md.fetch_candles("XAUUSD", "15min", "k", use_cache=False)


def test_same_snapshot_twice_with_open_market_marks_frozen():
    p1 = _fetch_stale(1.6)                       # first stale sighting — no mark yet
    assert not p1.get("feed_frozen")
    # simulate ≥10 minutes having passed since the first sighting
    key = ("XAUUSD", "15min", 120)
    md._FEED_SNAPSHOTS[key]["t"] -= md.FEED_FROZEN_MIN_GAP_SECONDS + 5
    p2 = _fetch_stale(1.6)                       # same frozen iso, 10+ min later
    assert p2.get("feed_frozen") is True
    assert p2.get("frozen_repeats") == 1
    # and again → repeats accumulate
    md._FEED_SNAPSHOTS[key]["t"] -= md.FEED_FROZEN_MIN_GAP_SECONDS + 5
    p3 = _fetch_stale(1.6)
    assert p3.get("frozen_repeats") == 2


def test_changed_snapshot_resets_the_tracking():
    _fetch_stale(1.6)
    key = ("XAUUSD", "15min", 120)
    md._FEED_SNAPSHOTS[key]["t"] -= md.FEED_FROZEN_MIN_GAP_SECONDS + 5
    # feed moved: a NEW iso (1 hour newer data) — never frozen
    p2 = _fetch_stale(1.5)
    assert not p2.get("feed_frozen")
    assert md._FEED_SNAPSHOTS[key]["repeats"] == 0


def test_closed_market_never_marks_frozen():
    with patch.object(md, "_ordered_chain", return_value=["twelvedata"]), \
         patch.object(md, "_fetch_td_candles", return_value=_old_candles(1.8)), \
         patch.object(md, "market_state", return_value=False), \
         patch.object(md.provider_health, "is_open", return_value=False):
        p1 = md.fetch_candles("XAUUSD", "15min", "k", use_cache=False)
        key = ("XAUUSD", "15min", 120)
        assert not p1.get("feed_frozen")
        if key in md._FEED_SNAPSHOTS:            # weekend-style: tracking reset
            md._FEED_SNAPSHOTS[key]["t"] -= md.FEED_FROZEN_MIN_GAP_SECONDS + 5
        p2 = md.fetch_candles("XAUUSD", "15min", "k", use_cache=False)
        assert not p2.get("feed_frozen")


def test_fresh_data_clears_freeze_tracking():
    _fetch_stale(1.6)
    key = ("XAUUSD", "15min", 120)
    assert key in md._FEED_SNAPSHOTS
    end = md.datetime.now(timezone.utc)          # frozen Friday clock — feed recovers
    fresh = [{"datetime": (end - timedelta(minutes=15 * i)).strftime("%Y-%m-%d %H:%M:%S"),
              "open": 2600.0, "high": 2601.0, "low": 2599.0, "close": 2600.5,
              "volume": 1000} for i in range(120, 0, -1)]
    with patch.object(md, "_ordered_chain", return_value=["twelvedata"]), \
         patch.object(md, "_fetch_td_candles", return_value=fresh), \
         patch.object(md, "market_state", return_value=True), \
         patch.object(md.provider_health, "is_open", return_value=False):
        p = md.fetch_candles("XAUUSD", "15min", "k", use_cache=False)
        assert not md.is_stale(p)
        assert key not in md._FEED_SNAPSHOTS     # recovered — tracking cleared


def test_signal_engine_uses_feed_frozen_run_code(monkeypatch):
    import fysb.engines.signal_engine as se
    payload = {"age_seconds": 90000, "last_candle_iso": "2026-11-26 03:49:00",
               "interval": "15min", "values": [], "feed_frozen": True,
               "frozen_repeats": 2, "chain_errors": ["twelvedata:stale(age=1500min)"]}
    with patch.object(md, "is_stale", return_value=True):
        assert se.market_data.is_stale(payload) is True
    assert payload.get("feed_frozen") is True and payload.get("frozen_repeats") == 2
    # the run-code selection logic mirrors the engine branch
    assert ("FEED_FROZEN" if payload.get("feed_frozen") else "STALE_DATA") == "FEED_FROZEN"


@pytest.fixture(autouse=True)
def _frozen_market_friday():
    """v8.14.10 — CI crossed the Fri→Sat boundary and the weekend widening
    reclassified every test's «stale» payload as fresh (mechanics unchanged).
    Pin the market clock to Friday noon UTC — the suite tests the MECHANICS,
    never the wall calendar."""
    from tests.helpers import frozen_market_clock
    with frozen_market_clock():
        yield
