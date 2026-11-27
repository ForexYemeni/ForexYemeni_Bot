"""v8.13.5 tests — tracker truth: no fabricated stop-outs, honest witnesses.

The owner's live incident (#46): an admin SELL on XAUUSD was birthed RUNNING
at «-21.4 pips» and then announced SL_HIT at «-118.2 pips» while the card's
own witness line printed «-14.3 pips» — because (1) the venue-gold chain
served raw GC=F CME futures (+13..18$ premium over spot) whenever the spot
calibration call failed, and (2) the SL witness line picked the trade's
FAVOUR side instead of the adverse side. Root cause + witness + spike guard
all fixed here.
"""
from __future__ import annotations

import sys
from datetime import datetime, timezone
from unittest.mock import patch

import pytest

sys.path.insert(0, ".")

from fysb import config
from fysb.engines import market_data as md
from fysb.engines.tracker import _documented_extreme, _evaluate_signal, _sl_touch_plausible


@pytest.fixture(autouse=True)
def _clean_gold_calibration():
    """v8.13.9 hygiene: other tests may legitimately persist a fresh spot
    calibration (the live path now persists it) — the unoffsettable-futures
    tests here need a calibration-free start to assert the honest refusal.
    v8.14.2: the live-spot crosscheck cache must be cleared too."""
    md._spot_crosscheck_cache.clear()
    md._fxref_gold_fail.clear()
    md._cache.clear()
    md._fail_streak.clear()
    calib = config.DB_FILE.parent / "spot_calibration.json"
    if calib.exists():
        calib.unlink()
    yield
    if calib.exists():
        calib.unlink()


# ---------------------------------------------------------------------------
# Fix 1 — raw futures are NEVER served as spot gold
# ---------------------------------------------------------------------------

def _fut_candles(n=60, base=3680.0):
    # v8.14.5 — timestamps RELATIVE to utcnow: fixed wall-clock stamps made this
    # suite time-fragile (green only when run before 15:00 UTC — the ancient
    # guard rightly refused them later). Fresh-at-run-time = deterministic verdict.
    from datetime import datetime as _dt, timedelta, timezone
    end = _dt.now(timezone.utc)
    return [{"datetime": (end - timedelta(minutes=15 * (n - i))).strftime("%Y-%m-%d %H:%M:%S"),
             "open": base, "high": base + 1.0, "low": base - 1.0, "close": base,
             "volume": 100} for i in range(n)]


def test_futures_without_spot_calibration_fail_honestly():
    # gold-api raises -> calibration impossible -> provider must fail, not serve raw GC=F
    with patch.object(md, "_fetch_yahoo_candles", return_value=_fut_candles()), \
         patch.object(md, "_fetch_goldapi_price", side_effect=RuntimeError("net")), \
         patch.object(md, "_ordered_chain", return_value=["venue-gold"]), \
         patch.object(md.provider_health, "is_open", return_value=False):
        with pytest.raises(RuntimeError):
            md.fetch_candles("XAUUSD", "15min", "", 60, use_cache=False)


def test_chain_falls_through_when_futures_unoffsettable():
    # the NEXT provider (twelvedata) answers instead — no contamination
    # (v8.14.5: stamps RELATIVE to utcnow — fixed wall-clock stamps aged 3h+
    # and the ancient guard rightly refused them after 15:00 UTC)
    from datetime import timedelta, timezone
    _end = datetime.now(timezone.utc)
    td_vals = [{"datetime": (_end - timedelta(minutes=15 * (59 - i))).strftime("%Y-%m-%d %H:%M:%S"),
                "open": 3650.0, "high": 3651.0, "low": 3649.0, "close": 3650.0,
                "volume": 100} for i in range(60)]
    with patch.object(md, "_fetch_yahoo_candles", return_value=_fut_candles()), \
         patch.object(md, "_fetch_goldapi_price", side_effect=RuntimeError("net")), \
         patch.object(md, "_ordered_chain", return_value=["venue-gold", "twelvedata"]), \
         patch.object(md, "_fetch_td_candles", return_value=td_vals), \
         patch.object(md.provider_health, "is_open", return_value=False):
        p = md.fetch_candles("XAUUSD", "15min", "", 60, use_cache=False)
        assert float(p["values"][-1]["close"]) == pytest.approx(3650.0)  # spot-level, NOT 3680+


def test_futures_with_valid_spot_calibration_still_offset():
    with patch.object(md, "_fetch_yahoo_candles", return_value=_fut_candles()), \
         patch.object(md, "_fetch_goldapi_price", return_value={"price": 3676.0}), \
         patch.object(md, "_fetch_yahoo_quote", return_value={"price": 3680.0}), \
         patch.object(md, "_ordered_chain", return_value=["venue-gold"]), \
         patch.object(md.provider_health, "is_open", return_value=False):
        p = md.fetch_candles("XAUUSD", "15min", "", 60, use_cache=False)
        # v8.14.3 quote↔quote: spot 3676 vs futures-now 3680 → −4.0 applied
        assert float(p["values"][-1]["close"]) == pytest.approx(3676.0)


# ---------------------------------------------------------------------------
# Fix 2 — spike guard: an isolated outlier candle cannot fake a stop-out
# ---------------------------------------------------------------------------

def _sell_sig(entry=3673.0, sl=3684.8, tp1=3660.0, tp2=3650.0, tp3=3640.0):
    return {"id": 1, "symbol": "XAUUSD", "timeframe": "15min", "direction": "SELL",
            "entry": entry, "stop": sl, "tp1": tp1, "tp2": tp2, "tp3": tp3,
            "hit_tp": 0, "status": "RUNNING",
            "filled_at": "2026-11-27 08:00:00", "created_at": "2026-11-27 08:00:00"}


def _post_fill_bars():
    # calm bars around entry (ATR ~ 1.5); nothing touches anything
    bars = []
    for i in range(40):
        px = 3673.0 + (i % 4) * 0.5
        m = 15 * i
        bars.append({"datetime": f"2026-11-27 {8 + m // 60:02d}:{m % 60:02d}:00",
                     "open": px, "high": px + 1.0, "low": px - 1.0, "close": px,
                     "volume": 100})
    return bars


def test_isolated_spike_candle_does_not_fake_stop_out():
    cs = _post_fill_bars()
    # calm range ~3672-3674; one garbage spike to 3690 (≈12x ATR) touching the SELL stop
    cs[20] = {**cs[20], "high": 3691.0, "low": 3672.5}
    verdict = _evaluate_signal(_sell_sig(), cs)
    assert verdict != "SL_HIT"


def test_genuine_repeated_stop_touch_still_fires():
    cs = _post_fill_bars()
    cs[20] = {**cs[20], "high": 3685.2, "low": 3672.5}   # touches SL 3684.8
    cs[21] = {**cs[21], "high": 3686.0, "low": 3680.0}   # confirmed next candle
    verdict = _evaluate_signal(_sell_sig(), cs)
    assert verdict == "SL_HIT"


def test_single_wide_stop_bar_that_is_in_range_fires():
    # a violent-but-in-range stop bar (not an isolated outlier): prior bars
    # already stretched upward, the touch is within 3x ATR of that range
    cs = _post_fill_bars()
    for i in range(15, 20):
        cs[i] = {**cs[i], "high": 3681.0 + (i - 15) * 0.5}   # gradually climbing range
    cs[20] = {**cs[20], "high": 3685.4, "low": 3672.0}       # only ~1.4 beyond recent max
    verdict = _evaluate_signal(_sell_sig(), cs)
    assert verdict == "SL_HIT"


def test_spike_guard_helper_bounds():
    bars = _post_fill_bars()
    a = 1.5
    assert _sl_touch_plausible(bars, 0, 3691.0, "SELL", a) is True      # too little history
    assert _sl_touch_plausible(bars, 20, 3678.5, "SELL", a) is True     # within prior max + 3xATR
    assert _sl_touch_plausible(bars, 20, 3691.0, "SELL", a) is False    # ~11x ATR outlier
    assert _sl_touch_plausible(bars, 20, 3691.0, "SELL", 0.0) is True   # no ATR -> no blocking


# ---------------------------------------------------------------------------
# Fix 3 — the witness side is honest to the card being sent
# ---------------------------------------------------------------------------

def test_documented_extreme_adverse_side_for_sl_card():
    sig = _sell_sig()
    payload = {"values": _post_fill_bars() + [
        {"datetime": "2026-11-27 09:45:00", "open": 3683.0, "high": 3685.6,
         "low": 3676.0, "close": 3684.0, "volume": 100}]}
    favour_px, favour_pips = _documented_extreme(sig, payload, adverse=False)
    adverse_px, adverse_pips = _documented_extreme(sig, payload, adverse=True)
    assert favour_px == pytest.approx(3672.0)      # min low (SELL favour side)
    assert adverse_px == pytest.approx(3685.6)     # max high = the stop-touch witness
    # adverse travel is negative (price ABOVE entry for a SELL) and its
    # magnitude dwarfs the favour-side travel — the honest stop witness
    assert adverse_pips < 0 and abs(adverse_pips) > abs(favour_pips)


def test_documented_extreme_favour_side_for_tp_card():
    sig = {"id": 2, "symbol": "XAUUSD", "timeframe": "15min", "direction": "BUY",
           "entry": 100.0, "stop": 98.0, "tp1": 102.0, "tp2": 104.0, "tp3": 106.0,
           "hit_tp": 0, "status": "RUNNING", "filled_at": "2026-11-27 08:00:00",
           "created_at": "2026-11-27 08:00:00"}
    payload = {"values": [
        {"datetime": "2026-11-27 08:05:00", "open": 100.0, "high": 101.0,
         "low": 97.5, "close": 100.5, "volume": 100}]}
    favour_px, _ = _documented_extreme(sig, payload, adverse=False)
    adverse_px, _ = _documented_extreme(sig, payload, adverse=True)
    assert favour_px == pytest.approx(101.0)       # max high (BUY favour)
    assert adverse_px == pytest.approx(97.5)       # min low (BUY adverse)
