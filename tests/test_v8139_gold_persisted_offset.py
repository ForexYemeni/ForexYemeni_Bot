"""v8.13.9 tests — the fresh persisted-offset fallback for CME gold futures.

The owner's Friday 13:47 UTC incident (market OPEN): a momentary gold-api
calibration hiccup killed the whole venue-gold provider (v8.13.5 raised on
ANY unoffsettable futures) → with TwelveData also failing the chain returned
«تعذر جلب بيانات لحظية» and the chart died with it. Fix: before failing, the
futures path falls back to the FRESH PERSISTED offset (learned ≤48h ago from
live spot-vs-venue calibration, magnitude-capped ≤2%). RAW futures are STILL
never served — the #46 guarantee holds.
"""
from __future__ import annotations

import json
import sys
import time
from unittest.mock import patch

import pytest

sys.path.insert(0, ".")

import tests.helpers  # noqa: F401  (isolated temp DB + env)
from fysb import config
from fysb.engines import market_data as md


def _fut_candles(n=60, base=3680.0):
    from datetime import datetime, timedelta
    end = datetime.utcnow()
    return [{"datetime": (end - timedelta(minutes=15 * (n - i))).strftime("%Y-%m-%d %H:%M:%S"),
             "open": base, "high": base + 1.0, "low": base - 1.0, "close": base,
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


def _fetch():
    with patch.object(md, "_ordered_chain", return_value=["venue-gold"]), \
         patch.object(md, "_fetch_yahoo_candles", return_value=_fut_candles()), \
         patch.object(md.provider_health, "is_open", return_value=False):
        return md.fetch_candles("XAUUSD", "15min", "", 60, use_cache=False)


def test_fresh_persisted_offset_rescues_chain_when_goldapi_down(clean_gold_chain):
    # a live calibration ≤48h ago persisted spot − venue = −15.0
    clean_gold_chain.write_text(json.dumps({"XAUUSD": {"offset": -15.0, "ts": time.time()}}))
    with patch.object(md, "_fetch_goldapi_price", side_effect=RuntimeError("net")):
        p = _fetch()                       # was «تعذر جلب» — now served
    assert float(p["values"][-1]["close"]) == pytest.approx(3680.0 - 15.0)
    assert p["source"] == "LIVE"


def test_stale_persisted_offset_never_rescues(clean_gold_chain):
    # offset older than the 48h trust window → the honest refusal stands
    clean_gold_chain.write_text(json.dumps(
        {"XAUUSD": {"offset": -15.0, "ts": time.time() - 49 * 3600}}))
    with patch.object(md, "_fetch_goldapi_price", side_effect=RuntimeError("net")):
        with pytest.raises(RuntimeError):
            _fetch()


def test_no_persisted_offset_never_serves_raw_futures(clean_gold_chain):
    clean_gold_chain.write_text(json.dumps({}))
    with patch.object(md, "_fetch_goldapi_price", side_effect=RuntimeError("net")):
        with pytest.raises(RuntimeError):
            _fetch()
    assert md._fail_streak.get("venue-gold|XAUUSD|15min") is not None


def test_successful_calibration_persists_for_future_fallbacks(clean_gold_chain):
    # v8.14.3 quote↔quote calibration: spot 3676 vs futures-NOW 3680 → −4.0
    # learned & applied (the legit premium corridor makes this live path work)
    with patch.object(md, "_fetch_goldapi_price", return_value={"price": 3676.0}), \
         patch.object(md, "_fetch_yahoo_quote", return_value={"price": 3680.0}):
        p = _fetch()
    assert float(p["values"][-1]["close"]) == pytest.approx(3676.0)
    data = json.loads(clean_gold_chain.read_text())
    assert data["XAUUSD"]["offset"] == pytest.approx(-4.0)
    assert time.time() - data["XAUUSD"]["ts"] < 60


def test_absurd_persisted_offset_rejected_by_trust_cap(clean_gold_chain):
    # >2% magnitude is rejected by _persisted_offset itself — raw futures never served
    clean_gold_chain.write_text(json.dumps({"XAUUSD": {"offset": -300.0, "ts": time.time()}}))
    with patch.object(md, "_fetch_goldapi_price", side_effect=RuntimeError("net")):
        with pytest.raises(RuntimeError):
            _fetch()
