"""v8.13.3 tests — the FINAL declared gap: the weekly CRT reference.

Scenario: the school's highest CRT is a sweep of a WEEKLY extreme (prior
completed week PWH/PWL or the current running week H/L). Coverage-honest:
levels count only when the series truly spans the window; and the updater
now names the exact reason a release lookup found nothing installable
(the manual-asset naming incident).
"""
from __future__ import annotations

import sys
from datetime import datetime, timedelta
from unittest.mock import patch

import pytest

sys.path.insert(0, ".")

from fysb.engines.analysis import weekly_reference
from fysb.engines.strategies import crt
from tests.test_strategy_audit_contracts import _fixture


def _stamp(dt: datetime) -> str:
    return dt.strftime("%Y-%m-%d %H:%M:%S")


def _series(bars: list[dict], t0: datetime, step_min: int = 15) -> list[dict]:
    out = []
    for i, c in enumerate(bars):
        out.append({**c, "datetime": _stamp(t0 + timedelta(minutes=step_min * i))})
    return out


# ---------------------------------------------------------------------------
# weekly_reference: coverage honesty
# ---------------------------------------------------------------------------

def test_weekly_reference_current_week_only():
    # Monday 2026-11-23 00:00 UTC onward, 100 bars (~25h) — no prior week data
    bars = [{"open": 100.0, "high": 101.0 + i * 0.05, "low": 99.0, "close": 100.5,
             "volume": 1000} for i in range(100)]
    cs = _series(bars, datetime(2026, 11, 23, 0, 0))
    wr = weekly_reference(cs)
    assert wr is not None
    assert wr["cur_bars"] == 100
    assert wr["cur_high"] == pytest.approx(max(b["high"] for b in bars))
    assert wr["cur_low"] == pytest.approx(99.0)
    assert wr["prev_high"] is None and wr["prev_low"] is None
    assert wr["prev_bars"] == 0
    assert wr["cur_span_hours"] == pytest.approx((99 * 15) / 60.0, abs=0.1)


def test_weekly_reference_prior_completed_week():
    # hourly bars: prev week Mon 09..Thu 12 Nov, cur week from Mon 16 Nov
    bars_prev = [{"open": 100.0, "high": 104.0, "low": 98.0, "close": 101.0, "volume": 1}] * 80
    bars_cur = [{"open": 101.0, "high": 103.5, "low": 97.5, "close": 102.0, "volume": 1}] * 80
    cs = _series(bars_prev, datetime(2026, 11, 9, 0, 0), step_min=60) + \
         _series(bars_cur, datetime(2026, 11, 16, 0, 0), step_min=60)
    wr = weekly_reference(cs)
    assert wr is not None
    assert wr["prev_bars"] == 80
    assert wr["prev_high"] == pytest.approx(104.0)
    assert wr["prev_low"] == pytest.approx(98.0)
    assert wr["cur_bars"] == 80 and wr["cur_high"] == pytest.approx(103.5)


def test_weekly_reference_honest_on_short_or_undated():
    assert weekly_reference([]) is None
    assert weekly_reference([{"open": 1, "high": 1, "low": 1, "close": 1}] * 4) is None
    nodt = [{"open": 1.0, "high": 1.0, "low": 1.0, "close": 1.0, "datetime": ""}] * 10
    assert weekly_reference(nodt) is None


# ---------------------------------------------------------------------------
# CRT weekly bonus: earned-only, honest disclosure, denominator untouched
# ---------------------------------------------------------------------------

def _retimed_crt_fixture(week_span: bool) -> list[dict]:
    """Take the audit CRT fixture (a real passing sweep setup) and re-time it:
    earlier bars inside the prior completed week Mon 16..22 Nov (highs capped
    just under the sweep extreme so the sweep truly reaches the PWH), and the
    last `tail` bars on Monday 23 Nov (current week). week_span=False squeezes
    everything into one day (honest None path)."""
    ctx = _fixture("CRT")
    cs = [dict(c) for c in ctx.candles]
    sweep_high = float(cs[-1]["high"])
    cap = sweep_high - 1.0
    n = len(cs)
    tail = min(45, n - 10)
    for i in range(n - tail):
        o, c_ = float(cs[i]["open"]), float(cs[i]["close"])
        cs[i]["high"] = max(min(float(cs[i]["high"]), cap), o, c_)
    if week_span:
        cs = _series(cs[:n - tail], datetime(2026, 11, 16, 0, 0)) + \
             _series(cs[n - tail:], datetime(2026, 11, 23, 0, 0))
    else:
        cs = _series(cs, datetime(2026, 11, 23, 8, 0))
    return cs


def _crt_eval(cs):
    base_ctx = _fixture("CRT")
    ctx = type(base_ctx)(**{**base_ctx.__dict__, "candles": cs})
    return crt.evaluate(ctx)


def test_crt_week_ref_bonus_earned_and_scored():
    res = _crt_eval(_retimed_crt_fixture(week_span=True))
    assert res is not None
    assert res.checks["week_ref_swept"] is not None
    assert res.checks["week_ref_swept"][0] in ("PWH", "PWL", "week-high", "week-low")
    assert any("weekly reference" in r for r in res.reasons)
    cur_bars, span_h, prev_bars = res.checks["week_ref_span"]
    assert prev_bars >= 12 and cur_bars >= 1


def test_crt_week_ref_honest_none_on_one_day_data():
    res = _crt_eval(_retimed_crt_fixture(week_span=False))
    if res is None:                      # re-timing may void the setup — acceptable
        return
    assert res.checks["week_ref_swept"] is None
    assert not any("weekly reference" in r for r in res.reasons)


def test_crt_bonus_only_denominator_untouched():
    src = open("fysb/engines/strategies/crt.py").read()
    assert "max_pts = 80.0" in src                       # denominator unchanged
    res_week = _crt_eval(_retimed_crt_fixture(week_span=True))
    res_day = _crt_eval(_retimed_crt_fixture(week_span=False))
    if res_week is None or res_day is None:
        return
    assert res_week.score >= res_day.score               # bonus only ever lifts


# ---------------------------------------------------------------------------
# updater: precise Arabic blocker instead of the generic «لا يوجد إصدار»
# ---------------------------------------------------------------------------

def test_updater_names_misnamed_asset_release():
    import updater
    payload = {"tag_name": "v8.13.3",
               "assets": [{"name": "ForexYemeni1.5.zip"}]}
    with patch.object(updater, "_get_json", return_value=payload):
        msg = updater._latest_release_blocker()
    assert "v8.13.3" in msg and "ForexYemeni1.5.zip" in msg and "باسم غير مقبول" in msg


def test_updater_names_missing_assets_release():
    import updater
    with patch.object(updater, "_get_json", return_value={"tag_name": "v9.0.0", "assets": []}):
        msg = updater._latest_release_blocker()
    assert "بلا حزم مرفقة" in msg and "v9.0.0" in msg


def test_updater_falls_back_to_generic_when_api_fails():
    import updater
    with patch.object(updater, "_get_json", side_effect=RuntimeError("net")):
        assert updater._latest_release_blocker() == ""
