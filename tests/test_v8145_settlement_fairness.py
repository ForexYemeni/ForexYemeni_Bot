"""v8.14.5 tests — «عدالة الاستراحة الملكية» (the daily settlement lie, fixed).

The owner's recurring card: «بيانات أقدم من الحد المسموح (78 دقيقة)» DURING
gold-generation. The arithmetic root: the last M15 gold candle closes 22:00
UTC (17:00 NY winter); the old calendar said «open» again at 23:00:00 while
the feed stayed paused → at 23:18 the age is 78 min > the 45-min M15 limit →
a DAILY honest-looking rejection. The silence was the MARKET's, not the
feed's. Fix: (a) DST-aware NY-anchored calendar (close/reopen/pause),
(b) fair age = raw age − settlement-pause overlap (metals/commodity only —
spot FX ticks through the pause so a frozen FX feed stays REALLY frozen),
(c) the closed-gate names the pause instead of a vague «market closed».
"""
from __future__ import annotations

import sys
from datetime import datetime, timezone

import pytest

sys.path.insert(0, ".")

import tests.helpers  # noqa: F401  (isolated temp DB + env)
from fysb import db
from fysb.engines import market_data as md
from fysb import theme


@pytest.fixture(autouse=True)
def _init_db():
    db.init_db()
    yield


def _payload(age_min: float, iso: str, symbol: str = "XAUUSD", interval: str = "15min") -> dict:
    return {"age_seconds": age_min * 60.0, "last_candle_iso": iso,
            "symbol": symbol, "interval": interval}


W = timezone.utc


# ---------------------------------------------------------------------------
# ① THE owner scenario, to the minute: 22:00 last candle · 23:18 now · 78 min
# ---------------------------------------------------------------------------

def test_the_78_minute_lie_is_dead():
    p = _payload(78.0, "2026-11-27 21:45:00")          # M15 candle closing 22:00
    now = datetime(2026, 11, 27, 23, 18, tzinfo=W)     # calendar says «open» again
    # fair age = 78 − 60 (the full pause) = 18 MINUTES ≤ 45 → NOT stale anymore
    assert md.fair_age_seconds(p, now.timestamp()) == pytest.approx(18.0 * 60)
    with pytest.MonkeyPatch.context() as mp:
        mp.setattr(md, "datetime", _FrozenDT(now))
        assert md.is_stale(p, now.timestamp()) is False


def test_during_the_pause_the_calendar_says_closed():
    with pytest.MonkeyPatch.context() as mp:
        mp.setattr(md, "datetime", _FrozenDT(datetime(2026, 11, 27, 22, 30, tzinfo=W)))
        assert md.market_state("metal") is False
        assert md.in_settlement_pause() is True


# ---------------------------------------------------------------------------
# ② summer DST: the pause shifts to 21:00–22:00 UTC automatically
# ---------------------------------------------------------------------------

def test_summer_pause_window_and_fairness():
    assert md.settlement_pause_utc(datetime(2026, 7, 15, 12, 0, tzinfo=W))[0].hour == 21
    assert md.settlement_pause_utc(datetime(2026, 11, 27, 12, 0, tzinfo=W))[0].hour == 22
    # July 15: last candle closes 21:00 UTC; at 22:18 the raw age is 78 → fair 18
    p = _payload(78.0, "2026-07-15 20:45:00")
    now = datetime(2026, 7, 15, 22, 18, tzinfo=W)
    assert md.fair_age_seconds(p, now.timestamp()) == pytest.approx(18.0 * 60)


# ---------------------------------------------------------------------------
# ③ FX has NO grace — a frozen FX feed during the pause is REALLY frozen
# ---------------------------------------------------------------------------

def test_fx_freeze_stays_really_frozen():
    p = _payload(78.0, "2026-11-27 21:45:00", symbol="EURUSD")
    now = datetime(2026, 11, 27, 23, 18, tzinfo=W)
    assert md.fair_age_seconds(p, now.timestamp()) == pytest.approx(78.0 * 60)
    with pytest.MonkeyPatch.context() as mp:
        mp.setattr(md, "datetime", _FrozenDT(now))
        assert md.is_stale(p, now.timestamp()) is True


# ---------------------------------------------------------------------------
# ④ DST calendar anchors: Friday close / Sunday reopen / DST boundary days
# ---------------------------------------------------------------------------

def test_friday_close_and_sunday_reopen_dst_aware():
    with pytest.MonkeyPatch.context() as mp:
        # winter (EST): Friday close 22:00 UTC
        mp.setattr(md, "datetime", _FrozenDT(datetime(2026, 11, 27, 22, 30, tzinfo=W)))
        assert md.market_state("metal") is False          # Friday after NY 17:00
        mp.setattr(md, "datetime", _FrozenDT(datetime(2026, 11, 27, 21, 50, tzinfo=W)))
        assert md.market_state("metal") is True           # Friday before close
        # summer (EDT): Friday close 21:00 UTC
        mp.setattr(md, "datetime", _FrozenDT(datetime(2026, 7, 17, 21, 30, tzinfo=W)))
        assert md.market_state("metal") is False
        mp.setattr(md, "datetime", _FrozenDT(datetime(2026, 7, 17, 20, 30, tzinfo=W)))
        assert md.market_state("metal") is True
        # Sunday reopen: winter 23:00 UTC / summer 22:00 UTC
        mp.setattr(md, "datetime", _FrozenDT(datetime(2026, 11, 29, 22, 30, tzinfo=W)))
        assert md.market_state("metal") is False
        mp.setattr(md, "datetime", _FrozenDT(datetime(2026, 11, 29, 23, 5, tzinfo=W)))
        assert md.market_state("metal") is True
        mp.setattr(md, "datetime", _FrozenDT(datetime(2026, 7, 19, 21, 30, tzinfo=W)))
        assert md.market_state("metal") is False
        mp.setattr(md, "datetime", _FrozenDT(datetime(2026, 7, 19, 22, 5, tzinfo=W)))
        assert md.market_state("metal") is True


def test_dst_boundary_days_shift_the_pause():
    # 2026: DST starts Sun Mar 8, ends Sun Nov 1 — the NEXT day's pause shifts
    assert md.settlement_pause_utc(datetime(2026, 3, 7, 12, 0, tzinfo=W))[0].hour == 22
    assert md.settlement_pause_utc(datetime(2026, 3, 9, 12, 0, tzinfo=W))[0].hour == 21
    assert md.settlement_pause_utc(datetime(2026, 10, 31, 12, 0, tzinfo=W))[0].hour == 21
    assert md.settlement_pause_utc(datetime(2026, 11, 2, 12, 0, tzinfo=W))[0].hour == 22


def test_crypto_never_closes_and_unknown_is_none():
    assert md.market_state("crypto") is True
    assert md.market_state("weird") is None


# ---------------------------------------------------------------------------
# ⑤ the closed gate names the pause (royal message, not a vague «closed»)
# ---------------------------------------------------------------------------

def test_engine_gate_names_the_settlement_pause():
    from fysb.engines import signal_engine
    with pytest.MonkeyPatch.context() as mp:
        mp.setattr(md, "market_state", lambda cat: False)
        mp.setattr(md, "in_settlement_pause", lambda: True)
        mp.setattr(md, "settlement_pause_utc",
                   lambda now=None: (datetime(2026, 11, 27, 22, 0, tzinfo=W),
                                     datetime(2026, 11, 27, 23, 0, tzinfo=W)))
        res = signal_engine.generate_signal("XAUUSD", "M15", force=False)
    assert res["status"] == "REJECTED"
    assert "settlement pause 22:00-23:00 UTC" in res["reason"]


def test_theme_translates_the_pause_with_times():
    ar = theme.ar_reason("settlement pause 22:00-23:00 UTC; no signal manufactured")
    assert "استراحة التسوية" in ar and "22:00" in ar and "23:00" in ar


class _FrozenDT:
    """Patches market_data.datetime so now() returns a FIXED instant while the
    class itself stays real: constructor calls (datetime(...)) and classmethods
    (fromisoformat/fromtimestamp) keep working through delegation."""

    def __init__(self, fixed: datetime):
        self._fixed = fixed
        self._real = datetime

    def __call__(self, *args, **kwargs):
        return self._real(*args, **kwargs)

    def now(self, tz=None):
        return self._fixed if tz is None else self._fixed.astimezone(tz)

    def __getattr__(self, item):
        return getattr(self._real, item)
