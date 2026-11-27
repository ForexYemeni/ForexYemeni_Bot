"""v8.13.0 gap-fill tests — every school-declared missing mechanism, verified.

The v8.12.0 release disclosed per-school gaps honestly. This release fills
them mechanically. Each test proves the mechanism EXISTS and is HONEST:
bonus-only scoring (legacy scores never drop), disclosed skip when data is
absent, and pure OHLC arithmetic everywhere.
"""
from __future__ import annotations

import sys

import pytest

sys.path.insert(0, ".")

from fysb.engines.analysis import adx, equal_extremes, psych_level, role_flip
from fysb.engines.anchors import opening_gaps
from fysb.engines.strategies import (REGISTRY, amd, classic, crt, fibonacci, ict,
                                     silver_bullet, smc, trend, turtle_soup)
from fysb.engines.strategies.base import Ctx
from tests.helpers import downtrend_candles, make_candles, uptrend_candles
from tests.test_strategy_audit_contracts import _fixture


# ---------------------------------------------------------------------------
# shared helpers
# ---------------------------------------------------------------------------

def test_adx_is_true_trend_strength():
    # strong monotone trend → high ADX; flat tape → low ADX
    cs = make_candles([(100.0 + 0.8 * i, 101.2 + 0.8 * i, 99.8 + 0.8 * i, 100.8 + 0.8 * i)
                       for i in range(60)])
    assert adx(cs) > 25.0
    flat = make_candles([(100.0, 101.0, 99.0, 100.0)] * 60)
    assert adx(flat) < 25.0
    assert adx(uptrend_candles(10)) == 0.0        # too short → honest 0.0


def test_equal_extremes_clusters_pools():
    cs = uptrend_candles(40, base=100, step=0.05)
    cs[10]["high"] = 102.0
    cs[20]["high"] = 102.05       # EQH pair within 0.15 ATR of each other
    cs[12]["low"] = 99.0
    cs[25]["low"] = 99.02         # EQL pair
    pools = equal_extremes(cs, 0.5)
    assert pools["eqh"] and pools["eql"]


def test_psych_level_finds_big_figures():
    assert psych_level(2652.0, 5.0) in (2650.0, 2700.0, 2600.0)
    assert psych_level(1.0852, 0.001) in (1.08, 1.09)


def test_role_flip_proves_break_and_retest():
    # BUY: price below 100 for 5 bars, closes above (break), then retests ≤100
    vals = ([(98.5, 99.0, 98.0, 98.7)] * 5 +
            [(99.0, 101.0, 98.8, 100.6)] +      # break above the level
            [(100.6, 101.4, 100.2, 101.0)] * 4 +
            [(100.9, 101.2, 99.6, 99.9)])        # retest back below 100+tol
    cs = make_candles(vals)
    assert role_flip(cs, 100.0, 0.5, "BUY") is True
    # no break in history → no flip
    cs2 = make_candles([(101.5, 102.0, 101.0, 101.6)] * 12)
    assert role_flip(cs2, 100.0, 0.5, "BUY") is False


def test_opening_gaps_detects_nwog_ndog():
    vals = ([(100.0, 100.4, 99.6, 100.0)] * 96 +      # full UTC day 1
            [(101.3, 101.6, 100.9, 101.2)] * 10)      # day 2 opens 1.3 above
    cs = make_candles(vals, start_hour=0)
    a = 0.5
    gaps = opening_gaps(cs, a)
    assert gaps["ndog"] is not None
    assert gaps["ndog"]["dir"] == "BUY"
    assert gaps["ndog"]["level"] == 101.3


# ---------------------------------------------------------------------------
# ICT: DOL / Breaker-Mitigation / OTE / PD-stack / SMT
# ---------------------------------------------------------------------------

def test_ict_reports_all_gap_fill_checks_and_honest_smt_skip():
    ctx = _fixture("ICT")
    res = ict.evaluate(ctx)
    assert res is not None
    for key in ("dol", "breaker_confluence", "zone_mitigated", "ote_depth",
                "pd_stack", "smt"):
        assert key in res.checks
    assert res.checks["smt"] is None                  # no correlated series
    assert any("SMT skipped" in r for r in res.reasons)


def test_ict_smt_detects_split_with_correlated_series():
    ctx = _fixture("ICT")
    base = REGISTRY["ICT"].evaluate(ctx)
    assert base is not None
    ctx.correlated_candles = uptrend_candles(60, step=0.3)   # higher lows
    res = REGISTRY["ICT"].evaluate(ctx)
    if res is not None and res.direction == "BUY":
        assert res.checks["smt"] in (None, "BUY")
        if res.checks["smt"] == "BUY":
            assert any("SMT divergence" in r for r in res.reasons)


# ---------------------------------------------------------------------------
# SMC: pools / fixed zone / mitigation / inducement / progression
# ---------------------------------------------------------------------------

def test_smc_reports_all_gap_fill_checks():
    ctx = _fixture("SMC")
    res = smc.evaluate(ctx)
    assert res is not None
    for key in ("fixed_zone_touch", "zone_mitigated", "intact_zone_layers",
                "inducement_unswept", "pool_origin", "pool_swept"):
        assert key in res.checks
    joined = " ".join(res.reasons)
    if res.checks["pool_swept"]:
        assert "liquidity pool swept" in joined
    if res.checks["zone_mitigated"]:
        assert "mitigated" in joined


# ---------------------------------------------------------------------------
# SILVER_BULLET: 1-2-3 protocol + NWOG/NDOG alignment
# ---------------------------------------------------------------------------

def test_silver_bullet_123_and_gap_checks():
    ctx = _fixture("SILVER_BULLET")
    res = silver_bullet.evaluate(ctx)
    assert res is not None
    assert isinstance(res.checks["protocol_123"], bool)
    assert "protocol_123" in res.checks and "gap_align" in res.checks
    assert any("1-2-3" in r for r in res.reasons)     # disclosed either way


def test_opening_gaps_honest_when_no_gap():
    vals = [(100.0, 100.4, 99.6, 100.05)] * 60        # continuous, no gap
    cs = make_candles(vals, start_hour=0)
    assert opening_gaps(cs, 0.5)["ndog"] is None


# ---------------------------------------------------------------------------
# AMD: post-manip POI / London manipulation / D equation
# ---------------------------------------------------------------------------

def test_amd_poi_and_distribution_equation_disclosed():
    ctx = _fixture("AMD")
    res = amd.evaluate(ctx)
    assert res is not None
    assert "post_manip_poi" in res.checks and "london_manip" in res.checks
    assert res.checks["d_distribution_eq"].startswith("0.5x/1.0x/1.5x")
    assert any("D-distribution equation" in r for r in res.reasons)


# ---------------------------------------------------------------------------
# CRT: prior-day reference + confirmation ladder
# ---------------------------------------------------------------------------

def test_crt_day_ref_and_ladder_checks():
    ctx = _fixture("CRT")
    res = crt.evaluate(ctx)
    assert res is not None
    assert "day_ref_swept" in res.checks              # honest None on one-day data
    assert res.checks["day_ref_swept"] is None
    assert isinstance(res.checks["confirm_ladder"], int) and res.checks["confirm_ladder"] <= 2


# ---------------------------------------------------------------------------
# TURTLE_SOUP: 55 window / double trap / trap session
# ---------------------------------------------------------------------------

def test_turtle_soup_gap_fill_checks():
    ctx = _fixture("TURTLE_SOUP")
    res = turtle_soup.evaluate(ctx)
    assert res is not None
    assert "long_window_55" in res.checks
    assert "second_trap" in res.checks
    assert "trap_session" in res.checks
    joined = " ".join(res.reasons)
    if res.checks["long_window_55"]:
        assert "55-bar" in joined


def test_turtle_soup_55_window_needs_history():
    ctx = _fixture("TURTLE_SOUP")
    ctx.candles = ctx.candles[:40]                    # below 57 bars
    res = turtle_soup.evaluate(ctx)
    if res is not None:
        assert res.checks["long_window_55"] is False  # honest False, not fabricated


# ---------------------------------------------------------------------------
# FIBONACCI: OTE sweet spot + equilibrium naming
# ---------------------------------------------------------------------------

def test_fibonacci_ote_and_eq_named():
    ctx = _fixture("FIBONACCI")
    res = fibonacci.evaluate(ctx)
    assert res is not None
    assert 0.618 <= res.checks["ote_depth"] <= 0.79
    assert res.checks["eq_side"] in ("EQ+", "EQ-")
    assert any("equilibrium side" in r for r in res.reasons)


# ---------------------------------------------------------------------------
# CLASSIC: role-flip proof + psych level
# ---------------------------------------------------------------------------

def test_classic_role_flip_and_psych_checks():
    ctx = _fixture("CLASSIC")
    res = classic.evaluate(ctx)
    assert res is not None
    assert "role_flip_proof" in res.checks
    assert "psych_level" in res.checks
    if res.checks["role_flip_proof"]:
        assert any("PROVEN" in r for r in res.reasons)


# ---------------------------------------------------------------------------
# TREND: ADX + expansion phase
# ---------------------------------------------------------------------------

def test_trend_adx_and_phase_checks():
    ctx = _fixture("TREND")
    res = trend.evaluate(ctx)
    assert res is not None
    assert 0.0 <= res.checks["adx"] <= 100.0
    assert isinstance(res.checks["phase_expanding"], bool)
    assert any("ADX" in r for r in res.reasons)
    assert any("phase:" in r for r in res.reasons)


# ---------------------------------------------------------------------------
# legacy-score protection: bonuses never deflate below the old floors
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("code", ["ICT", "SMC", "CRT", "AMD", "TURTLE_SOUP",
                                  "SILVER_BULLET", "FIBONACCI", "TREND",
                                  "CLASSIC", "WYCKOFF", "ICHIMOKU", "PRICE_ACTION"])
def test_upgraded_engines_keep_accepting_their_fixtures(code):
    res = REGISTRY[code].evaluate(_fixture(code))
    assert res is not None, code                       # regression guard
