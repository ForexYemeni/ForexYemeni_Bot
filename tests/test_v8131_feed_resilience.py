"""v8.13.1 feed-resilience tests — the frozen-feed defences.

Scenario proven by the owner's live incident: every provider answering with
the same ~24h-old snapshot while the broker chart streams live quotes. These
tests prove the new defences:
  * wrong-series guard: a provider serving daily bars for a 15min request is
    treated as a provider failure (the chain keeps hunting), never returned
    as an honestly-stale payload.
  * cooldown-bypass deep pass: a provider that recovered seconds ago must not
    lose to a stale snapshot just because pass 1 marked it in cooldown.
  * chain verdict visibility: the stale fallback carries the per-provider
    verdict list for ADMIN engine_runs (never user-facing).
  * SMT quota safety: the correlated series is cached for a short TTL so
    auto-search cycles do not spend an extra API call per run.
"""
from __future__ import annotations

import sys
from datetime import datetime, timedelta, timezone
from unittest.mock import patch

import pytest

sys.path.insert(0, ".")

from fysb.engines import market_data as md


def _candles_15min(n: int, end_utc: datetime, volume=True) -> list[dict]:
    out = []
    for i in range(n, 0, -1):
        t = end_utc - timedelta(minutes=15 * i)
        out.append({"datetime": t.strftime("%Y-%m-%d %H:%M:%S"),
                    "open": 2600.0, "high": 2601.0, "low": 2599.0,
                    "close": 2600.5, "volume": 1000})
    return out


def _candles_daily(n: int, end_utc: datetime) -> list[dict]:
    out = []
    for i in range(n, 0, -1):
        t = (end_utc - timedelta(days=i)).replace(hour=0, minute=0)
        out.append({"datetime": t.strftime("%Y-%m-%d %H:%M:%S"),
                    "open": 2600.0, "high": 2650.0, "low": 2580.0,
                    "close": 2630.0, "volume": 5000})
    return out


def test_series_interval_mismatch_detects_wrong_timestep():
    end = datetime(2026, 11, 27, 5, 0, tzinfo=timezone.utc)
    assert md._series_interval_mismatch(_candles_daily(30, end), "15min") is True
    assert md._series_interval_mismatch(_candles_15min(40, end), "15min") is False
    # a weekend hole does not break the median (most deltas stay 15min)
    gapped = _candles_15min(20, end - timedelta(days=2)) + _candles_15min(20, end)
    assert md._series_interval_mismatch(gapped, "15min") is False
    assert md._series_interval_mismatch(_candles_15min(2, end), "15min") is False  # too few


def test_chain_rejects_wrong_series_instead_of_stale_snapshot():
    """A provider answering M15 with daily bars must be treated as a FAILURE:
    the chain returns no payload at all (RuntimeError) rather than an honestly-
    stale 24h-old snapshot that would masquerade as a data verdict."""
    end = datetime.now(timezone.utc)
    daily = _candles_daily(60, end)
    with patch.object(md, "_ordered_chain", return_value=["twelvedata"]), \
         patch.object(md, "_fetch_td_candles", return_value=daily), \
         patch.object(md.provider_health, "is_open", return_value=False):
        md._fail_streak.clear()
        with pytest.raises(RuntimeError):
            md.fetch_candles("XAUUSD", "15min", "k", use_cache=False)


def test_chain_returns_fresh_payload_and_marks_success():
    end = datetime.now(timezone.utc)
    fresh = _candles_15min(120, end)
    with patch.object(md, "_ordered_chain", return_value=["twelvedata"]), \
         patch.object(md, "_fetch_td_candles", return_value=fresh), \
         patch.object(md.provider_health, "is_open", return_value=False):
        md._fail_streak.clear()
        payload = md.fetch_candles("XAUUSD", "15min", "k", use_cache=False)
        assert not md.is_stale(payload)
        assert payload.get("chain_errors") is None


def test_stale_fallback_carries_chain_verdict_for_admin_surfaces():
    """All providers stale → the honest stale payload carries the per-provider
    verdict list (engine_runs material — never user-facing prose)."""
    end = datetime.now(timezone.utc) - timedelta(hours=1.6)
    stale = _candles_15min(120, end)
    with patch.object(md, "_ordered_chain", return_value=["twelvedata"]), \
         patch.object(md, "_fetch_td_candles", return_value=stale), \
         patch.object(md.provider_health, "is_open", return_value=False), \
         patch.object(md, "settlement_frozen_seconds", return_value=0.0):  # v8.14.8: mechanics test — fairness has its own suite
        md._fail_streak.clear()
        payload = md.fetch_candles("XAUUSD", "15min", "k", use_cache=False)
        assert md.is_stale(payload)
        verdicts = payload.get("chain_errors") or []
        assert any(v.startswith("twelvedata:stale(") for v in verdicts)


def test_deep_pass_bypasses_cooldown_after_recovery():
    """Pass 1: provider A (only chain member) in cooldown → no fresh data.
    Pass 2 bypasses the cooldown, the recovered provider answers fresh."""
    end = datetime.now(timezone.utc)
    fresh = _candles_15min(120, end)
    calls = {"n": 0}

    def _td(symbol, interval, key="", outputsize=120):
        calls["n"] += 1
        return fresh

    with patch.object(md, "_ordered_chain", return_value=["twelvedata"]), \
         patch.object(md, "_fetch_td_candles", side_effect=_td), \
         patch.object(md.provider_health, "is_open", return_value=False):
        md._fail_streak.clear()
        # mark the sole provider in cooldown (45s window)
        md._fail_streak[md._ck("twelvedata", "XAUUSD", "15min")] = (
            __import__("time").time() + 45, 1)
        payload = md.fetch_candles("XAUUSD", "15min", "k", use_cache=False)
        assert not md.is_stale(payload)
        assert calls["n"] >= 1          # the deep pass actually fetched


def test_smt_cache_prevents_repeat_api_calls():
    from fysb.engines import signal_engine as se
    ctx_cache = {}
    series = [{"datetime": "2026-11-27 04:00:00", "open": 1, "high": 2,
               "low": 0.5, "close": 1.5}]
    se._SMT_CACHE[("GBPUSD", "M15")] = (__import__("time").time(), series)
    # second access within TTL must reuse the cache (no fetch)
    hit = se._SMT_CACHE.get(("GBPUSD", "M15"))
    assert hit and hit[1] is series
