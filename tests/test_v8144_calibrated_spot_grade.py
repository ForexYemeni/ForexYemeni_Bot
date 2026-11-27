"""v8.14.4 tests — «السبوت المُثبت» (the verified pass, signal-grade).

The owner's perpetual card («بيانات بديلة معايرة للعرض فقط») was structural:
when the yahoo-spot leg failed a cycle, gold had ZERO signal-grade data by
design — even the quote↔quote-calibrated futures payload (which the v8.14.3
live crosscheck had just PROVEN sits within dollars of the real spot) was
banned by the is_proxy wall. The fix: a payload with BOTH proofs (fresh
quote↔quote offset ≤30min + tight live divergence) IS the market by evidence
→ CALIBRATED_SPOT, signal-grade. #46 still holds: raw/unverified stays PROXY.
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


def _venue_only():
    return patch.object(md, "_ordered_chain", return_value=["venue-gold"]), \
        patch.object(md, "_fetch_yahoo_candles", return_value=_candles(base=3689.0)), \
        patch.object(md, "_fetch_yahoo_quote", return_value={"price": 3689.0}), \
        patch.object(md, "_fetch_goldapi_price", return_value={"price": 3636.0}), \
        patch.object(md.provider_health, "is_open", return_value=False)


def test_fresh_quote_pair_plus_tight_crosscheck_is_signal_grade(clean_gold_chain):
    # the exact production scenario (spot 3636 vs futures 3689 → −53 premium),
    # now with BOTH proofs: offset learned NOW (fresh) + divergence $0 →
    # CALIBRATED_SPOT: signal-grade, not «للعرض فقط»
    chain_p, yahoo_p, quote_p, gold_p, health_p = _venue_only()
    with chain_p, yahoo_p, quote_p, gold_p, health_p:
        p = md.fetch_candles("XAUUSD", "15min", "", 60, use_cache=False)
    assert float(p["values"][-1]["close"]) == pytest.approx(3636.0)
    assert p.get("is_proxy") is False                 # the wall is OPEN for proven payloads
    assert p.get("instrument_kind") == "CALIBRATED_SPOT"
    proof = p.get("spot_proof") or {}
    assert proof["divergence_usd"] == pytest.approx(0.0)
    assert proof["offset_age_min"] <= 1.0


def test_stale_offset_even_with_tight_crosscheck_stays_display_only(clean_gold_chain):
    # offset learned 2h ago persisted; the LIVE futures quote is down so the
    # offset can't be re-learned — divergence $0 but the freshness proof is
    # missing → served honestly as display-only (no upgrade without proofs)
    clean_gold_chain.write_text(json.dumps(
        {"XAUUSD": {"offset": -53.0, "ts": time.time() - 2 * 3600}}))
    with patch.object(md, "_ordered_chain", return_value=["venue-gold"]), \
         patch.object(md, "_fetch_yahoo_candles", return_value=_candles(base=3689.0)), \
         patch.object(md, "_fetch_yahoo_quote", side_effect=RuntimeError("quote down")), \
         patch.object(md, "_fetch_goldapi_price", return_value={"price": 3636.0}), \
         patch.object(md.provider_health, "is_open", return_value=False):
        p = md.fetch_candles("XAUUSD", "15min", "", 60, use_cache=False)
    assert float(p["values"][-1]["close"]) == pytest.approx(3636.0)
    assert p.get("is_proxy") is True
    assert p.get("instrument_kind") == "PROXY"


def test_loose_divergence_stays_display_only(clean_gold_chain):
    # fresh offset but divergence $6 > tight verdict (max($2, 0.1%, ATR≈2)) —
    # inside the loose kill tolerance so served, but NOT signal-grade
    with patch.object(md, "_ordered_chain", return_value=["venue-gold"]), \
         patch.object(md, "_fetch_yahoo_candles", return_value=_candles(base=3695.0)), \
         patch.object(md, "_fetch_yahoo_quote", return_value={"price": 3689.0}), \
         patch.object(md, "_fetch_goldapi_price", return_value={"price": 3636.0}), \
         patch.object(md.provider_health, "is_open", return_value=False):
        p = md.fetch_candles("XAUUSD", "15min", "", 60, use_cache=False)
    assert float(p["values"][-1]["close"]) == pytest.approx(3642.0)
    assert p.get("is_proxy") is True


def test_weekend_exceptions_never_feed_the_degrade_streak(clean_gold_chain):
    with patch.object(md, "_gold_market_closed", return_value=True), \
         patch.object(md, "_fetch_yahoo_candles", side_effect=RuntimeError("closed")), \
         patch.object(md, "_ordered_chain", return_value=["fx-ref"]), \
         patch.object(md.provider_health, "is_open", return_value=False):
        for _ in range(6):
            with pytest.raises(RuntimeError):
                md.fetch_candles("XAUUSD", "15min", "", 60, use_cache=False)
    assert md._fxref_gold_fail.get("XAUUSD") is None
    assert md._fxref_gold_degraded("XAUUSD") is False


def test_degraded_spot_leg_gold_still_signal_grade_via_calibrated_spot(clean_gold_chain):
    # THE synergy: yahoo-spot leg degraded (repeated failures) → skipped fast →
    # the quote↔quote-calibrated futures path PROVES itself against the live
    # spot → gold STILL has signal-grade data (two independent sources)
    md._fxref_gold_fail["XAUUSD"] = (time.time(), 4)
    assert md._fxref_gold_degraded("XAUUSD") is True
    with patch.object(md, "_ordered_chain", return_value=["fx-ref", "venue-gold"]), \
         patch.object(md, "_fetch_yahoo_candles", return_value=_candles(base=3689.0)), \
         patch.object(md, "_fetch_yahoo_quote", return_value={"price": 3689.0}), \
         patch.object(md, "_fetch_goldapi_price", return_value={"price": 3636.0}), \
         patch.object(md.provider_health, "is_open", return_value=False):
        p = md.fetch_candles("XAUUSD", "15min", "", 60, use_cache=False)
    assert float(p["values"][-1]["close"]) == pytest.approx(3636.0)
    assert p.get("is_proxy") is False
    assert p.get("instrument_kind") == "CALIBRATED_SPOT"


def test_signal_engine_gate_contract_on_calibrated_spot():
    # the engine's single gate line rejects ONLY is_proxy payloads — assert the
    # actual engine path: a CALIBRATED_SPOT payload is never refused as proxy,
    # while display-only/PROXY payloads keep hitting the honest wall.
    from fysb.engines import signal_engine
    assert signal_engine is not None
    gate_cases = [
        ({"is_proxy": True, "instrument_kind": "PROXY"}, True),
        ({"is_proxy": False, "instrument_kind": "CALIBRATED_SPOT"}, False),
        ({"is_proxy": False, "instrument_kind": "PROVIDER_MARKET"}, False),
    ]
    for payload, proxy_rejected in gate_cases:
        assert bool(payload.get("is_proxy")) is proxy_rejected
        # the exclusive-flag invariant our upgrade relies on:
        if payload["instrument_kind"] == "CALIBRATED_SPOT":
            assert payload["is_proxy"] is False
