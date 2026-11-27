"""v8.13.6 tests — the ancient-snapshot guard (zombie-killer).

The owner's live incident: after every honesty fix, the M15 request still
returned «بيانات أقدم من الحد المسموح (1898 دقيقة)» — because a provider
serving a ~31-hour-old snapshot (cached CDN page / frozen mirror / throttled
API pages) was treated as the «freshest stale» candidate and handed to the
refusal gate as the platform's best answer. Now anything older than 3× the
staleness limit is a FAILED provider — the chain drops it and the honest
answer becomes either fresh data, a near-stale refusal, or a clean
«تعذر جلب بيانات» — never a 31-hour zombie.
"""
from __future__ import annotations

import sys
from datetime import datetime, timedelta, timezone
from unittest.mock import patch

import pytest

sys.path.insert(0, ".")

from fysb.engines import market_data as md
from fysb import theme


def _candles(age_hours: float, n: int = 120) -> list[dict]:
    end = datetime.now(timezone.utc) - timedelta(hours=age_hours)
    return [{"datetime": (end - timedelta(minutes=15 * i)).strftime("%Y-%m-%d %H:%M:%S"),
             "open": 2600.0, "high": 2601.0, "low": 2599.0, "close": 2600.5,
             "volume": 1000} for i in range(n, 0, -1)]


@pytest.fixture(autouse=True)
def _clean_state():
    md._fail_streak.clear()
    md._cache.clear()
    md._FEED_SNAPSHOTS.clear()
    # v8.14.8: these tests exercise freshness MECHANICS on relative stamps —
    # when the lookback window crosses the live settlement pause, the v8.14.5
    # fairness layer flips the stale verdict. Fairness has its OWN dedicated
    # suite (test_v8145_settlement_fairness.py) — pin it to zero here.
    _fair = patch.object(md, "settlement_frozen_seconds", return_value=0.0)
    _fair.start()
    yield
    _fair.stop()
    md._fail_streak.clear()
    md._cache.clear()
    md._FEED_SNAPSHOTS.clear()


def _fetch(candles):
    with patch.object(md, "_ordered_chain", return_value=["twelvedata"]), \
         patch.object(md, "_fetch_td_candles", return_value=candles), \
         patch.object(md, "market_state", return_value=True), \
         patch.object(md.provider_health, "is_open", return_value=False):
        return md.fetch_candles("XAUUSD", "15min", "k", use_cache=False)


def test_ancient_snapshot_is_never_served():
    # 31.6 hours old (the 1898-minute incident) — far beyond 3×45min=135min
    with pytest.raises(RuntimeError):
        _fetch(_candles(31.6))
    assert md._fail_streak.get("twelvedata|XAUUSD|15min") is not None


def test_near_stale_snapshot_is_still_served_honestly():
    # 90min: beyond the 45min staleness limit, under the 135min ancient bar
    p = _fetch(_candles(1.5))
    assert md.is_stale(p)
    assert p["age_seconds"] < 3 * md.staleness_limit("15min")


def test_fresh_still_wins():
    p = _fetch(_candles(0.15))
    assert not md.is_stale(p)


def test_ancient_provider_does_not_shadow_fresher_stale():
    # first provider serves a zombie, the second serves near-stale → the
    # near-stale payload must win (the zombie is dropped, not compared)
    near = _candles(1.5)
    calls = {"n": 0}

    def _td(symbol, interval, key, size):
        calls["n"] += 1
        return near

    with patch.object(md, "_ordered_chain", return_value=["venue-gold", "twelvedata"]), \
         patch.object(md, "_fetch_yahoo_candles", return_value=_candles(31.6)), \
         patch.object(md, "_fetch_goldapi_price", side_effect=RuntimeError("net")), \
         patch.object(md, "_fetch_td_candles", _td), \
         patch.object(md, "market_state", return_value=True), \
         patch.object(md.provider_health, "is_open", return_value=False):
        p = md.fetch_candles("XAUUSD", "15min", "k", use_cache=False)
    assert md.is_stale(p) and calls["n"] >= 1
    assert p["age_seconds"] < 3 * md.staleness_limit("15min")


# ---------------------------------------------------------------------------
# the frozen-feed user card (no provider names — standing rule honoured)
# ---------------------------------------------------------------------------

def test_theme_maps_frozen_reason_without_provider_names():
    s = theme.ar_reason("data frozen (repeats 2, age 31min)")
    assert "متجمّد" in s  # v8.14.6: honest story, no counters
    assert "سقف" not in s and "twelvedata" not in s and "venue" not in s


def test_theme_keeps_plain_stale_mapping():
    s = theme.ar_reason("data stale (age 1898min)")
    assert "أسعار غير حية" in s  # v8.14.6: no counters on the user surface
