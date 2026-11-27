"""v8.14.3 tests — «الاسترجاع الملكي للذهب» (the royal gold recovery).

The owner approved the full plan after v8.14.2's honest clamps turned out to
choke the production pipeline: the real spot↔futures gap (~$53) exceeded the
0.25% pairing corridor, so live calibration NEVER succeeded on production →
no fresh offsets → the futures path died honestly after 6h → and if the
yahoo-spot leg also failed the whole chain collapsed into «تعذر جلب».

The four owner-approved fixes, each proven here:
  ① QUOTE↔QUOTE calibration — offset = spot-now − futures-NOW (two live
    quotes), inside a physical premium corridor ≤1.5% (legit contango)
  ② volatility-aware crosscheck — tol = max($8, 0.25% spot, 2×ATR15)
  ③ XAUUSD=X degradation guard — repeated failures skip the leg 15 min
  ④ royal honest diagnosis — the death error names the dead LAYER (no brands)
"""
from __future__ import annotations

import json
import sys
import time
from datetime import datetime, timedelta
from unittest.mock import patch

import pytest

sys.path.insert(0, ".")

import tests.helpers  # noqa: F401  (isolated temp DB + env)
from fysb import config
from fysb.engines import market_data as md


def _candles(n=60, base=3689.0, spread=1.0):
    end = datetime.utcnow()
    return [{"datetime": (end - timedelta(minutes=15 * (n - i))).strftime("%Y-%m-%d %H:%M:%S"),
             "open": base, "high": base + spread, "low": base - spread, "close": base,
             "volume": 100} for i in range(n)]


@pytest.fixture()
def clean_gold_chain():
    md._cache.clear()
    md._fail_streak.clear()
    md._spot_crosscheck_cache.clear()
    md._fxref_gold_fail.clear()
    calib = config.DB_FILE.parent / "spot_calibration.json"
    if calib.exists():
        calib.unlink()
    yield calib
    if calib.exists():
        calib.unlink()


# ---------------------------------------------------------------------------
# ① quote↔quote calibration — the EXACT production incident, now CORRECT:
#    spot 3636 vs futures-now 3689 → −53 (legit 1.44% premium) learned & applied
# ---------------------------------------------------------------------------

def test_quote_quote_calibration_applies_legit_premium(clean_gold_chain):
    with patch.object(md, "_ordered_chain", return_value=["venue-gold"]), \
         patch.object(md, "_fetch_yahoo_candles", return_value=_candles(base=3689.0)), \
         patch.object(md, "_fetch_yahoo_quote", return_value={"price": 3689.0}), \
         patch.object(md, "_fetch_goldapi_price", return_value={"price": 3636.0}), \
         patch.object(md.provider_health, "is_open", return_value=False):
        p = md.fetch_candles("XAUUSD", "15min", "", 60, use_cache=False)
    # futures level −53 = spot level: the chart is AT the market now
    assert float(p["values"][-1]["close"]) == pytest.approx(3636.0)
    assert p["source"] == "LIVE"
    assert json.loads(clean_gold_chain.read_text())["XAUUSD"]["offset"] == pytest.approx(-53.0)


def test_premium_corridor_violation_never_learned(clean_gold_chain):
    # spot 3636 vs futures 3740 → −104 (>1.5%×3740≈56) = physically impossible
    # premium → NOT learned, NOT applied; empty persisted → chain falls through
    with patch.object(md, "_ordered_chain", return_value=["venue-gold", "fx-ref"]), \
         patch.object(md, "_fetch_yahoo_candles",
                      side_effect=[_candles(base=3740.0), _candles(base=3636.0)]), \
         patch.object(md, "_fetch_yahoo_quote", return_value={"price": 3740.0}), \
         patch.object(md, "_fetch_goldapi_price", return_value={"price": 3636.0}), \
         patch.object(md.provider_health, "is_open", return_value=False):
        p = md.fetch_candles("XAUUSD", "15min", "", 60, use_cache=False)
    assert float(p["values"][-1]["close"]) == pytest.approx(3636.0)
    assert not clean_gold_chain.exists() or "XAUUSD" not in json.loads(clean_gold_chain.read_text())


# ---------------------------------------------------------------------------
# ①-b persisted (quote↔quote-learned) rescue — fresh & inside the corridor
# ---------------------------------------------------------------------------

def test_persisted_quote_quote_offset_rescues_within_corridor(clean_gold_chain):
    # learned −40 (≈1.08% premium) 2h ago; gold-api down now → the corridor
    # (≤1.5%) keeps the rescue alive WITHOUT re-opening the poison door
    clean_gold_chain.write_text(json.dumps(
        {"XAUUSD": {"offset": -40.0, "ts": time.time() - 2 * 3600}}))
    with patch.object(md, "_ordered_chain", return_value=["venue-gold"]), \
         patch.object(md, "_fetch_yahoo_candles", return_value=_candles(base=3680.0)), \
         patch.object(md, "_fetch_goldapi_price", side_effect=RuntimeError("net")), \
         patch.object(md.provider_health, "is_open", return_value=False):
        p = md.fetch_candles("XAUUSD", "15min", "", 60, use_cache=False)
    assert float(p["values"][-1]["close"]) == pytest.approx(3640.0)
    assert p["source"] == "LIVE"


# ---------------------------------------------------------------------------
# ② volatility-aware crosscheck — fair to real motion, strict to poison
# ---------------------------------------------------------------------------

def test_crosscheck_volatility_aware_toleration(clean_gold_chain):
    # ATR15 ≈ 15 (wild gold session): close 3660 vs spot 3636 = $24 — INSIDE
    # max($8, 0.25%, 2×ATR=30) → served (real motion, not a poison)
    clean_gold_chain.write_text(json.dumps(
        {"XAUUSD": {"offset": -20.0, "ts": time.time() - 1800}}))
    with patch.object(md, "_ordered_chain", return_value=["venue-gold"]), \
         patch.object(md, "_fetch_yahoo_candles", return_value=_candles(base=3680.0, spread=15.0)), \
         patch.object(md, "_fetch_goldapi_price",
                      side_effect=[RuntimeError("net"), {"price": 3636.0}]), \
         patch.object(md.provider_health, "is_open", return_value=False):
        p = md.fetch_candles("XAUUSD", "15min", "", 60, use_cache=False)
    assert float(p["values"][-1]["close"]) == pytest.approx(3660.0)


def test_crosscheck_still_refuses_true_poison(clean_gold_chain):
    # same divergence on a CALM tape (ATR≈2): tol = max(8, 9.09, 4) = 9.09 —
    # $24 divergence is refused → fx-ref true spot serves instead
    clean_gold_chain.write_text(json.dumps(
        {"XAUUSD": {"offset": -20.0, "ts": time.time() - 1800}}))
    with patch.object(md, "_ordered_chain", return_value=["venue-gold", "fx-ref"]), \
         patch.object(md, "_fetch_yahoo_candles",
                      side_effect=[_candles(base=3680.0), _candles(base=3636.0)]), \
         patch.object(md, "_fetch_goldapi_price",
                      side_effect=[RuntimeError("net"), {"price": 3636.0}]), \
         patch.object(md.provider_health, "is_open", return_value=False):
        p = md.fetch_candles("XAUUSD", "15min", "", 60, use_cache=False)
    assert float(p["values"][-1]["close"]) == pytest.approx(3636.0)


# ---------------------------------------------------------------------------
# ③ XAUUSD=X degradation guard — skip a dead leg, auto-retry after 15 min
# ---------------------------------------------------------------------------

def test_fxref_gold_degrades_after_repeated_failures_and_recovers(clean_gold_chain):
    boom = patch.object(md, "_fetch_yahoo_candles",
                        side_effect=[RuntimeError("net")] * 4, autospec=True)
    # the degrade streak is deliberately frozen while gold is CLOSED (v8.14.4
    # fairness) — this test exercises the guard, so pin the market open
    with boom as ymock, \
         patch.object(md, "_ordered_chain", return_value=["fx-ref"]), \
         patch.object(md, "_gold_market_closed", return_value=False), \
         patch.object(md.provider_health, "is_open", return_value=False):
        for _ in range(4):
            with pytest.raises(RuntimeError):
                md.fetch_candles("XAUUSD", "15min", "", 60, use_cache=False)
        assert ymock.call_count == 4
        # degraded → the 5th attempt fails FAST without touching yahoo again
        # (the leg-raise is swallowed by the per-provider failover; the proof
        # is the unchanged call_count)
        with pytest.raises(RuntimeError):
            md.fetch_candles("XAUUSD", "15min", "", 60, use_cache=False)
        assert ymock.call_count == 4
    # after the degrade window the leg is retried (and a success clears it)
    md._fxref_gold_fail["XAUUSD"] = (time.time() - md.FXREF_GOLD_DEGRADE_SECS - 1, 4)
    with patch.object(md, "_ordered_chain", return_value=["fx-ref"]), \
         patch.object(md, "_fetch_yahoo_candles", return_value=_candles(base=3636.0)), \
         patch.object(md.provider_health, "is_open", return_value=False):
        p = md.fetch_candles("XAUUSD", "15min", "", 60, use_cache=False)
    assert float(p["values"][-1]["close"]) == pytest.approx(3636.0)
    assert "XAUUSD" not in md._fxref_gold_fail


# ---------------------------------------------------------------------------
# ④ royal honest diagnosis — the death error names the dead LAYER (no brands)
# ---------------------------------------------------------------------------

def test_royal_diagnosis_names_layers_without_brands(clean_gold_chain):
    md._fxref_gold_fail["XAUUSD"] = (time.time(), 5)   # spot leg failing hard
    # v8.14.7: this contract is about the LAYER diagnosis — the Friday
    # pre-close window has its own honest variant, tested separately
    with patch.object(md, "_ordered_chain", return_value=["fx-ref", "venue-gold"]), \
         patch.object(md, "_pre_close_window", return_value=False), \
         patch.object(md, "_fetch_yahoo_candles", side_effect=RuntimeError("net")), \
         patch.object(md, "_fetch_goldapi_price", side_effect=RuntimeError("net")), \
         patch.object(md, "_fetch_venue_gold_candles", side_effect=RuntimeError("net")), \
         patch.object(md, "_fetch_yahoo_quote", side_effect=RuntimeError("net")), \
         patch.object(md.provider_health, "is_open", return_value=False), \
         patch.object(config, "has_market_api", return_value=False):
        with pytest.raises(RuntimeError) as ctx:
            md.fetch_candles("XAUUSD", "15min", "", 60, use_cache=False)
    msg = str(ctx.value)
    for layer in ("السبوت اللحظي", "العقود", "المصدر المفتاحي", "تعذر جلب بيانات الذهب"):
        assert layer in msg
    for brand in ("YAHOO", "BINANCE", "GOLDAPI", "TWELVEDATA", "PAXG", "BYBIT"):
        assert brand not in msg
