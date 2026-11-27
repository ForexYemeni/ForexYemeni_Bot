"""v8.13.4 tests — volatility-anchored structural stop cap.

The owner's live incident: XAUUSD M15 at record prices, a genuinely
structural 1.27% sweep stop rejected by the fixed 1.00% M15 ceiling.
The cap now stretches with REAL ATR% (4x ATR in % space) between the base
cap (calm regime == exact legacy behaviour) and 2x base (extreme regime).
Money risk is unchanged: position_sizing derives the lot from the actual
stop distance, so account risk stays at risk_per_trade_pct.
"""
from __future__ import annotations

import sys

import pytest

sys.path.insert(0, ".")

from fysb.engines.signal_engine import validate_signal_geometry


def _geo(symbol="XAUUSD", direction="BUY", tf="M15", entry=3633.0, stop=3586.53,
         tp1=3650.0, tp2=3670.0, tp3=3695.0, atr=None):
    return validate_signal_geometry(symbol, tf, direction, entry, stop, tp1, tp2, tp3, atr_val=atr)


# ---------------------------------------------------------------------------
# the exact live incident: now passes in a high-vol regime
# ---------------------------------------------------------------------------

def test_gold_record_price_structural_stop_passes_when_atr_is_real():
    # entry 3633, stop 46.47 away (1.2794%), ATR 11.7 (0.322%):
    # freak check: 4xATR = 46.8 >= 46.47 OK; adapt = 4*0.322/1.0 = 1.289
    # cap = 1.289% >= 1.2794% -> accepted
    ok, why = _geo(atr=11.7)
    assert ok, why


def test_rejection_reason_is_arabic_with_full_disclosure():
    # same setup but stop slightly wider: 47.5 (1.308%) — cap 1.289% rejects
    ok, why = _geo(stop=3633.0 - 47.5, atr=11.7)
    assert not ok
    assert "سقف M15 البنيوي" in why and "تكيف التقلب" in why and "1.29%" in why


# ---------------------------------------------------------------------------
# calm regime == exact legacy behaviour (no silent risk inflation)
# ---------------------------------------------------------------------------

def test_calm_regime_keeps_legacy_cap():
    # ATR 0.2% -> adapt = 4*0.2/1.0 = 0.8 -> clamped to 1.0 -> cap 1.00%
    ok, _ = _geo(entry=100.0, stop=99.4, tp1=101.0, tp2=102.0, tp3=103.5, atr=0.2)
    assert ok, "0.6% stop inside the calm base cap must pass"
    # ATR 0.3% -> adapt 1.2 -> cap 1.20%; stop 1.22% exceeds the cap (4xATR=1.2
    # also applies but the cap check runs first with the Arabic disclosure)
    ok2, why2 = _geo(entry=100.0, stop=98.78, tp1=101.0, tp2=102.0, tp3=103.5, atr=0.3)
    assert not ok2 and "1.20%" in why2 and "سقف M15 البنيوي" in why2


def test_no_atr_falls_back_to_base_cap():
    # no ATR -> adapt 1.0 -> M15 base cap 1.00% exactly as before
    ok, _ = _geo(entry=100.0, stop=99.05, tp1=101.0, tp2=102.0, tp3=103.5, atr=None)
    assert ok
    ok2, _ = _geo(entry=100.0, stop=98.9, tp1=101.0, tp2=102.0, tp3=103.5, atr=None)
    assert not ok2


# ---------------------------------------------------------------------------
# extreme regime: hard 2x ceiling bites, freak check still guards
# ---------------------------------------------------------------------------

def test_extreme_regime_hard_ceiling_2x_base():
    # ATR 0.8% -> adapt clamped at 2.0 -> cap 2.00%; stop 2.1% (2.6xATR, not freak) rejected
    ok, why = _geo(entry=100.0, stop=97.9, tp1=102.0, tp2=103.5, tp3=105.0, atr=0.8)
    assert not ok
    assert "2.00%" in why and "تكيف التقلب 2.00x" in why


def test_freak_stop_still_rejected_independent_of_cap():
    # calm-ish ATR (0.2%): cap floors at base 1.00%, so a 0.9% stop passes the
    # cap but is 4.5x ATR -> the freak-stop guard must reject it on its own
    ok, why = _geo(entry=1000.0, stop=991.0, tp1=1015.0, tp2=1030.0, tp3=1050.0, atr=2.0)
    assert not ok and "oversized stop" in why


# ---------------------------------------------------------------------------
# scalping TFs stay tight; crypto multiplier preserved
# ---------------------------------------------------------------------------

def test_scalping_m5_adapts_gently():
    # M5 base 0.65%; ATR 6.0 (0.165%) -> adapt 1.02 -> cap 0.66%; stop 21 (0.578%, 3.5xATR) passes
    ok, _ = _geo(tf="M5", entry=3633.0, stop=3612.0, tp1=3645.0, tp2=3658.0, tp3=3670.0, atr=6.0)
    assert ok
    # stop 25 (0.688%) exceeds cap 0.66% -> Arabic cap rejection (cap check first)
    ok2, why2 = _geo(tf="M5", entry=3633.0, stop=3608.0, tp1=3645.0, tp2=3658.0, tp3=3670.0, atr=6.0)
    assert not ok2 and "سقف M5" in why2
    # volatile M5: ATR 9.0 (0.248%) -> adapt 1.53 -> cap 0.99%; stop 33 (0.909%, 3.7xATR) passes
    ok3, _ = _geo(tf="M5", entry=3633.0, stop=3600.0, tp1=3645.0, tp2=3658.0, tp3=3672.0, atr=9.0)
    assert ok3


def test_crypto_multiplier_preserved_with_adaptation():
    # BTC M15: base 1.0% x2.5 = 2.5%; ATR 1.5% -> adapt clamped 2.0 -> cap 5.0%
    ok, _ = _geo(symbol="BTCUSD", entry=97000.0, stop=93100.0, tp1=99000.0, tp2=101000.0, tp3=103500.0, atr=1455.0)
    assert ok
    ok2, why2 = _geo(symbol="BTCUSD", entry=97000.0, stop=92000.0, tp1=99000.0, tp2=101000.0, tp3=103500.0, atr=1455.0)
    assert not ok2 and "5.00%" in why2


# ---------------------------------------------------------------------------
# geometry sanity untouched
# ---------------------------------------------------------------------------

def test_inverted_geometry_still_rejected():
    ok, why = _geo(direction="BUY", stop=3650.0, atr=11.7)
    assert not ok and "geometry" in why or "معكوسة" in why
