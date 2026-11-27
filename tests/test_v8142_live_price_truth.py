"""v8.14.2 tests — «عقد السعر الحي الصادق» (the honest live-price contract).

The owner caught the chart showing «السعر الحي: 3,583.80» while the real
market was 3,636 — a $53 lie. Root cause chain:
  ① the spot↔venue pairing tolerance was ±$120 — ANY offset was learned/applied
  ② a persisted offset could be applied up to 48h old and 2% (~$70) — drift
     of tens of dollars poisoned every venue payload
  ③ the analysis headline price was the last CANDLE CLOSE, not a live quote
The three clamps (pairing 0.25%, application 6h/0.5%, live crosscheck 0.15%)
plus the quote-first headline make a $53-class lie structurally impossible.
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


def _candles(n=60, base=3680.0):
    end = datetime.utcnow()
    return [{"datetime": (end - timedelta(minutes=15 * (n - i))).strftime("%Y-%m-%d %H:%M:%S"),
             "open": base, "high": base + 1.0, "low": base - 1.0, "close": base,
             "volume": 100} for i in range(n)]


def _spot_candles(n=60, base=3636.0):
    return _candles(n=n, base=base)


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
# ① broken pairing (the ±$120 hole) — never learned, never applied
# ---------------------------------------------------------------------------

def test_premium_corridor_violation_refused_true_spot_serves(clean_gold_chain):
    # v8.14.3 contract: quote↔quote calibration — spot 3636 vs futures quote
    # 3740 → offset −104 > 1.5% corridor (~56) = impossible premium → refused,
    # never learned, chain falls through to fx-ref true spot.
    with patch.object(md, "_ordered_chain", return_value=["venue-gold", "fx-ref"]), \
         patch.object(md, "_fetch_yahoo_candles",
                      side_effect=[_candles(base=3740.0), _spot_candles()]), \
         patch.object(md, "_fetch_yahoo_quote", return_value={"price": 3740.0}), \
         patch.object(md, "_fetch_goldapi_price", return_value={"price": 3636.0}), \
         patch.object(md.provider_health, "is_open", return_value=False):
        p = md.fetch_candles("XAUUSD", "15min", "", 60, use_cache=False)
    assert float(p["values"][-1]["close"]) == pytest.approx(3636.0)
    assert not p.get("is_proxy")
    # the impossible premium must NOT have been persisted
    assert not clean_gold_chain.exists() or "XAUUSD" not in json.loads(clean_gold_chain.read_text())


# ---------------------------------------------------------------------------
# ② drifted persisted offset — refused at application (6h / 0.5% clamps)
# ---------------------------------------------------------------------------

def test_drifted_persisted_offset_refused_true_spot_serves(clean_gold_chain):
    # v8.14.3: a persisted offset OUTSIDE the physical premium corridor
    # (−70 ≈ 1.9% > 1.5%) is refused at application — the chain falls to
    # fx-ref true spot. (A −53 offset is now LEGIT quote↔quote premium —
    # see test_v8143 — so the corridor, not a tight clamp, is the guard.)
    clean_gold_chain.write_text(json.dumps(
        {"XAUUSD": {"offset": -70.0, "ts": time.time() - 2 * 3600}}))
    with patch.object(md, "_ordered_chain", return_value=["venue-gold", "fx-ref"]), \
         patch.object(md, "_fetch_yahoo_candles",
                      side_effect=[_candles(), _spot_candles()]), \
         patch.object(md, "_fetch_goldapi_price", side_effect=RuntimeError("net")), \
         patch.object(md.provider_health, "is_open", return_value=False):
        p = md.fetch_candles("XAUUSD", "15min", "", 60, use_cache=False)
    # venue refused (70 > 1.5% corridor) → fx-ref true spot, close 3636 — no lie
    assert float(p["values"][-1]["close"]) == pytest.approx(3636.0)


def test_fresh_small_persisted_offset_still_rescues(clean_gold_chain):
    # the v8.13.9 rescue SURVIVES the tightening: fresh (1h) + small (−4.0 ≈
    # 0.11%) persisted offset with gold-api down → venue serves honestly
    clean_gold_chain.write_text(json.dumps(
        {"XAUUSD": {"offset": -4.0, "ts": time.time() - 3600}}))
    with patch.object(md, "_ordered_chain", return_value=["venue-gold"]), \
         patch.object(md, "_fetch_yahoo_candles", return_value=_candles()), \
         patch.object(md, "_fetch_goldapi_price", side_effect=RuntimeError("net")), \
         patch.object(md.provider_health, "is_open", return_value=False):
        p = md.fetch_candles("XAUUSD", "15min", "", 60, use_cache=False)
    assert float(p["values"][-1]["close"]) == pytest.approx(3676.0)
    assert p["source"] == "LIVE"


# ---------------------------------------------------------------------------
# ③ live crosscheck kill switch — diverged payload never leaves the chain
# ---------------------------------------------------------------------------

def test_crosscheck_refuses_diverged_payload(clean_gold_chain):
    # persisted offset passes the strict clamp (−10 ≈ 0.27%) but the assembled
    # close (3670) diverges from the LIVE spot quote (3636) by $34 — the
    # crosscheck refuses the payload → fx-ref true spot serves instead.
    clean_gold_chain.write_text(json.dumps(
        {"XAUUSD": {"offset": -10.0, "ts": time.time() - 1800}}))
    with patch.object(md, "_ordered_chain", return_value=["venue-gold", "fx-ref"]), \
         patch.object(md, "_fetch_yahoo_candles",
                      side_effect=[_candles(), _spot_candles()]), \
         patch.object(md, "_fetch_goldapi_price",
                      side_effect=[RuntimeError("net"), {"price": 3636.0}]), \
         patch.object(md.provider_health, "is_open", return_value=False):
        p = md.fetch_candles("XAUUSD", "15min", "", 60, use_cache=False)
    assert float(p["values"][-1]["close"]) == pytest.approx(3636.0)


# ---------------------------------------------------------------------------
# ④ the analysis headline price = live quote, candle close only a fallback
# ---------------------------------------------------------------------------

def test_headline_price_prefers_ok_live_quote():
    from fysb.services import analytics
    with patch.object(md, "execution_quote",
                      return_value={"ok": True, "price": 3636.0, "provider": "spot-ref"}):
        px, live = analytics._headline_price("XAUUSD", 3583.8)
    assert px == pytest.approx(3636.0)
    assert live is True


def test_headline_price_falls_back_to_candle_close_without_ok_quote():
    from fysb.services import analytics
    with patch.object(md, "execution_quote",
                      return_value={"ok": False, "price": 0.0, "provider": "venue-gold"}):
        px, live = analytics._headline_price("XAUUSD", 3583.8)
    assert px == pytest.approx(3583.8)
    assert live is False


def test_headline_price_survives_quote_crash():
    from fysb.services import analytics
    with patch.object(md, "execution_quote", side_effect=RuntimeError("net")):
        px, live = analytics._headline_price("XAUUSD", 3583.8)
    assert px == pytest.approx(3583.8)
    assert live is False
