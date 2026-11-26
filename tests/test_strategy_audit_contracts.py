"""Strategy software-contract audit, not an investment-performance test.

Positive fixtures must REALLY produce a result (no conditional assertions).
All transports are absent; test configuration is isolated before fysb imports.
"""
from copy import deepcopy
from dataclasses import replace
from datetime import datetime, timedelta
import random
from unittest.mock import patch

import pytest
from tests.helpers import (make_candles, uptrend_candles, downtrend_candles,
                           ict_bullish_sweep_setup, smc_bos_setup, trend_pullback_setup)
from fysb.engines.strategies import REGISTRY, evaluate_all
from fysb.engines.strategies.base import (Ctx, StrategyResult, candle_input_error,
    result_error, cap_stop, normalized_score, checked_strategy)
from fysb.engines.strategies import silver_bullet, trend, fibonacci

CODES = sorted(REGISTRY)


def _wyckoff():
    vals = []
    for i in range(44):
        o, c = (100.3, 101.6) if i % 2 == 0 else (101.7, 100.4)
        vals.append((o, max(o, c)+.35, min(o, c)-.35, c))
    cs = make_candles(vals)
    cs[-1].update(open=100.5, high=100.9, low=98.8, close=100.9, volume=4000.)
    return cs


def _silver_bullet(day="2026-11-26", utc_hour=15):
    end = datetime.fromisoformat(f"{day}T{utc_hour}:30:00+00:00")
    cs = make_candles([(100., 101., 99., 100.)]*77 + [
        (100., 101., 99., 100.2), (100.3, 104., 100., 103.5), (103.5, 104.5, 103., 104.)])
    for i, c in enumerate(cs):
        c["datetime"] = (end-timedelta(minutes=15*(len(cs)-i-1))).isoformat()
    return cs


def _fibonacci():
    vals = [(100., 100.2, 99.8, 100.)]*50
    px = 100.
    for _ in range(20):
        vals.append((px, px+1.2, px-.2, px+1))
        px += 1
    for _ in range(10):
        vals.append((px, px+.2, px-1.6, px-1.4))
        px -= 1.4
    return make_candles(vals)


def _classic():
    vals = []
    px = 2000.
    for _ in range(52):
        vals.append((px, px+1.35, px-.2, px+1))
        px += 1
    for _ in range(3):
        vals.append((px, px+.15, px-1.1, px-.9))
        px -= .9
    vals.append((px-.15, px+1.8, px-.3, px+1.55))
    return make_candles(vals)


def _fixture(code):
    bias = uptrend_candles(90)
    if code == "ICT":
        cs = ict_bullish_sweep_setup()
        bias = cs
    elif code == "SMC":
        cs = smc_bos_setup()
        bias = cs
    elif code == "PRICE_ACTION":
        cs = _classic()
        bias = cs
    elif code == "CLASSIC":
        cs = _classic()
        bias = cs
    elif code == "TREND":
        cs = trend_pullback_setup()
        bias = cs
    elif code == "ICHIMOKU":
        cs = uptrend_candles(90, base=3000, step=2)
        bias = cs
    elif code == "FIBONACCI":
        cs = _fibonacci()
    elif code == "WYCKOFF":
        cs = _wyckoff()
    elif code == "SILVER_BULLET":
        cs = _silver_bullet()
    elif code == "SK":
        cs = uptrend_candles(43, step=.2)
        key = max(c["high"] for c in cs[-13:-1])
        cs[-1].update(high=key+.75, close=key+.4)
    elif code == "CRT":
        cs = uptrend_candles(60, base=100, step=.05)
        cs[-2].update(open=102, high=103, low=101, close=102.5)
        cs[-1].update(open=102.8, high=104.5, low=101.5, close=102.2)
        bias = downtrend_candles(80)
    elif code == "TURTLE_SOUP":
        cs = uptrend_candles(60, base=100, step=.05)
        hi = max(c["high"] for c in cs[-21:-1])
        cs[-1].update(open=hi-.1, high=hi+1, low=hi-.4, close=hi-.3)
        bias = downtrend_candles(80)
    elif code == "AMD":
        cs = make_candles([(100., 101.3, 99.7, 100.5)]*59 + [(101., 102., 100.3, 100.8)])
        bias = downtrend_candles(80)
    else:
        raise AssertionError(code)
    return Ctx("XAUUSD", "M15", cs, bias)


def _mirror(ctx):
    # Reflection preserves all OHLC bounds, timestamps and volumes.
    anchor = 2*max(c["high"] for c in ctx.candles+ctx.bias_candles)
    def reflect(bars):
        return [{**c, "open": anchor-c["open"], "close": anchor-c["close"],
                 "high": anchor-c["low"], "low": anchor-c["high"]} for c in bars]
    return replace(ctx, candles=reflect(ctx.candles), bias_candles=reflect(ctx.bias_candles))


@pytest.mark.parametrize("code", CODES)
def test_every_engine_has_a_nonvacuous_positive_fixture(code):
    ctx = _fixture(code)
    original = deepcopy(ctx)
    assert not candle_input_error(ctx.candles)
    assert not candle_input_error(ctx.bias_candles)
    result = REGISTRY[code].evaluate(ctx)
    assert result is not None, code
    assert not result_error(result, code)
    assert REGISTRY[code].evaluate(ctx) == result
    assert ctx == original, "engines must not mutate provider history"
    assert evaluate_all(ctx, [code]) == [result], "registry and direct calls agree"


@pytest.mark.parametrize("code", CODES)
def test_each_engine_rejects_insufficient_history(code):
    assert REGISTRY[code].evaluate(Ctx("XAUUSD", "M15", uptrend_candles(10), [])) is None


@pytest.mark.parametrize("code", CODES)
@pytest.mark.parametrize("bad", ["nan", "infinity", "bounds", "negative", "duplicate", "reverse", "timestamp", "volume", "missing"])
def test_all_engines_fail_closed_on_malformed_market_history(code, bad):
    ctx = _fixture(code)
    cs = deepcopy(ctx.candles)
    if bad == "nan": cs[-1]["close"] = float("nan")
    elif bad == "infinity": cs[-1]["high"] = float("inf")
    elif bad == "bounds": cs[-1]["high"] = cs[-1]["low"]-.1
    elif bad == "negative": cs[-1]["low"] = -1
    elif bad == "duplicate": cs[-1]["datetime"] = cs[-2]["datetime"]
    elif bad == "reverse": cs.reverse()
    elif bad == "timestamp": cs[-1]["datetime"] = "invalid"
    elif bad == "volume": cs[-1]["volume"] = -1
    elif bad == "missing": del cs[-1]["open"]
    assert REGISTRY[code].evaluate(replace(ctx, candles=cs)) is None
    assert REGISTRY[code].evaluate(replace(ctx, bias_candles=cs)) is None


@pytest.mark.parametrize("code", CODES)
def test_seeded_varied_history_never_produces_an_invalid_result(code):
    rng = random.Random(431)
    for _ in range(12):
        px = 100.
        vals = []
        for _ in range(110):
            cl = px+rng.uniform(-1, 1)
            vals.append((px, max(px, cl)+rng.uniform(.01, .8), min(px, cl)-rng.uniform(.01, .8), cl))
            px = cl
        cs = make_candles(vals)
        ctx = Ctx("XAUUSD", "M15", cs, cs)
        result = REGISTRY[code].evaluate(ctx)
        if result is not None:
            assert not result_error(result, code)


@pytest.mark.parametrize("code", CODES)
def test_directional_rules_have_a_positive_opposite_side(code):
    ctx = _fixture(code)
    buy_or_sell = REGISTRY[code].evaluate(ctx)
    reflected = REGISTRY[code].evaluate(_mirror(ctx))
    assert buy_or_sell is not None and reflected is not None
    assert buy_or_sell.direction != reflected.direction
    assert not result_error(reflected, code)


@pytest.mark.parametrize("mirror", [False, True])
@pytest.mark.parametrize("bad", ["outside", "no_surge", "both_sides"])
def test_wyckoff_enforces_range_reclaim_and_usable_volume(mirror, bad):
    ctx = _fixture("WYCKOFF")
    if bad == "outside": ctx.candles[-1].update(high=104, close=103.8)
    elif bad == "no_surge": ctx.candles[-1]["volume"] = 100
    elif bad == "both_sides": ctx.candles[-1]["high"] = 104
    if mirror: ctx = _mirror(ctx)
    assert REGISTRY["WYCKOFF"].evaluate(ctx) is None


def test_wyckoff_no_volume_is_explicitly_skipped_not_fabricated():
    ctx = _fixture("WYCKOFF")
    for c in ctx.candles: c.pop("volume", None)
    res = REGISTRY["WYCKOFF"].evaluate(ctx)
    assert res is not None and res.checks["volume_surge"] is None


@pytest.mark.parametrize("day,hour", [("2026-11-26",15), ("2026-07-15",14)])
def test_silver_bullet_uses_actual_gap_in_this_ny_window_in_both_dst_seasons(day, hour):
    cs = _silver_bullet(day, hour)
    res = silver_bullet.evaluate(Ctx("XAUUSD", "M15", cs, cs))
    assert res is not None and res.checks["sb_window"] == "NY_AM_SB"
    assert res.checks["fvg_in_window"] is True


@pytest.mark.parametrize("index", [None, -1, 0, 1, 2, 6, 100])
def test_silver_bullet_rejects_unknown_or_pre_window_gap(index):
    ctx = _fixture("SILVER_BULLET")
    gap = dict(kind="bullish", lo=101., hi=103., index=index)
    with patch.object(silver_bullet, "fair_value_gaps", return_value=[gap]):
        assert silver_bullet.evaluate(ctx) is None


def test_silver_bullet_does_not_reuse_yesterdays_opening_range():
    ctx = _fixture("SILVER_BULLET")
    # Keep the last 3 current-session bars, shift ALL earlier data back a day.
    for c in ctx.candles[:-3]:
        c["datetime"] = (datetime.fromisoformat(c["datetime"])-timedelta(days=1)).isoformat()
    assert silver_bullet.evaluate(ctx) is None


def test_silver_bullet_rejects_previously_broken_then_reclaimed_gap():
    ctx = _fixture("SILVER_BULLET")
    # Start current window at index 77, break an alleged gap at 78, recover at 79.
    gap = dict(kind="bullish", lo=104., hi=105., index=7)
    with patch.object(silver_bullet, "fair_value_gaps", return_value=[gap]):
        assert silver_bullet.evaluate(ctx) is None


@pytest.mark.parametrize("direction,stop", [("BUY",90.), ("SELL",110.)])
def test_structural_stop_is_never_moved_inside_invalidation(direction, stop):
    assert cap_stop(100, stop, direction, 1, max_atr=2.6, cap_atr=2.4) == stop


def test_amd_stop_is_beyond_actual_manipulation_wick():
    ctx = _fixture("AMD")
    res = REGISTRY["AMD"].evaluate(ctx)
    assert res is not None and res.stop > ctx.candles[-1]["high"]
    mirrored = _mirror(ctx)
    res = REGISTRY["AMD"].evaluate(mirrored)
    assert res is not None and res.stop < mirrored.candles[-1]["low"]


def test_trend_uses_historical_ema_and_atr_before_resumption():
    ctx = _fixture("TREND")
    with patch.object(trend, "ema", wraps=trend.ema) as ema_mock, patch.object(trend, "atr", wraps=trend.atr) as atr_mock:
        assert trend.evaluate(ctx) is not None
    n = len(ctx.candles)
    assert any(len(call.args[0]) < n for call in ema_mock.call_args_list)
    assert any(len(call.args[0]) < n for call in atr_mock.call_args_list)


def test_fibonacci_preserves_origin_stop_and_discloses_adjusted_targets():
    ctx = _fixture("FIBONACCI")
    res = fibonacci.evaluate(ctx)
    assert res is not None and res.stop < min(c["low"] for c in ctx.candles[-40:])
    assert res.checks["targets_adjusted"] is True
    assert any("raw Fibonacci levels" in r for r in res.reasons)
    assert any("after RR floors" in r for r in res.reasons)


@pytest.mark.parametrize("field,value", [
    ("entry", float("nan")), ("entry", 10**1000), ("stop", 100),
    ("tp2", 101), ("tp2", 125), ("tp3", 140), ("score", 96),
    ("score", float("inf")), ("score", True), ("rr", 3),
    ("direction", "FLAT"), ("code", "SMC"), ("reasons", []), ("checks", None),
])
def test_common_result_guard_rejects_bad_engine_output(field, value):
    result = StrategyResult("ICT", "BUY", 100, 90, 120, 130, 145, 2, 80, ["fixture"], {})
    def evaluate(ctx): return replace(result, **{field:value})
    evaluate.__module__ = "fysb.engines.strategies.ict"
    assert checked_strategy(evaluate)(_fixture("ICT")) is None


def test_registry_reports_exception_and_does_not_block_another_engine(caplog):
    with patch.object(REGISTRY["ICT"], "evaluate", side_effect=RuntimeError("audit failure")):
        results = evaluate_all(_fixture("SMC"), ["ICT", "SMC", "SMC"])
    assert [r.code for r in results] == ["SMC"]
    assert "strategy evaluation failed code=ICT" in caplog.text
    assert "audit failure" in caplog.text


def test_score_is_a_bounded_checklist_transform_not_a_probability():
    assert normalized_score(0, 100) == 42
    assert normalized_score(100, 100) == 95
    assert normalized_score(50, 100) == 68


@pytest.mark.parametrize("code", CODES)
def test_every_engine_rejects_a_valid_but_directionless_flat_range(code):
    cs = make_candles([(100., 101., 99., 100.)]*90)
    assert REGISTRY[code].evaluate(Ctx("XAUUSD", "M15", cs, cs)) is None


def test_trend_rejects_a_visit_created_only_by_later_ema_drift():
    ctx = _fixture("TREND")
    # These lows are inside TODAY's EMA band but were above each historical
    # band. The previous implementation incorrectly called this a pullback.
    for c in ctx.candles[-6:-1]:
        c["low"] = c["close"]-.6
    assert trend.evaluate(ctx) is None


def test_equivalent_utc_timestamps_are_duplicates_even_with_different_offsets():
    cs = uptrend_candles(2)
    cs[0]["datetime"] = "2026-11-26T08:00:00Z"
    cs[1]["datetime"] = "2026-11-26T09:00:00+01:00"
    assert candle_input_error(cs) == "duplicate/out-of-order timestamps"


@pytest.mark.parametrize("code", ["AMD", "CRT", "TURTLE_SOUP"])
def test_single_sweep_engines_reject_an_ambiguous_outside_bar(code):
    ctx = _fixture(code)
    cs = ctx.candles
    cs[-1].update(high=max(c["high"] for c in cs[:-1])+10,
                  low=min(c["low"] for c in cs[:-1])-10)
    assert REGISTRY[code].evaluate(ctx) is None


def test_strategy_identity_pages_disclose_merit_and_actual_rule_limits(monkeypatch):
    from fysb import db
    from fysb.services import strategy_info
    db.init_db()
    monkeypatch.setattr(strategy_info, "real_stats", lambda code: {})
    for code in CODES:
        assert strategy_info.HOW_IT_WORKS[code]
        text = strategy_info.info_page(code)
        assert "وليست احتمال فوز" in text
        assert "ولا ضمان ربح" in text
    assert "4.5R" in " ".join(strategy_info.HOW_IT_WORKS["SK"])
    assert "Kijun" in " ".join(strategy_info.HOW_IT_WORKS["ICHIMOKU"])
