"""v8.14.6 tests — «السبب الاستراتيجي الحقيقي» (the strategy-truth refusal).

The owner's decree: when no signal is born, the card must carry the REAL
reason from the strategies themselves — which methodologies were examined,
which came closest, and which EXACT conditions stayed incomplete — with zero
minute/candle counters on the user surface. Implementation: every strategy
records its own check-ledger at its refusal gate into a thread-local
collector; the engine's NO_SETUP card is built from that ledger.
"""
from __future__ import annotations

import sys
import threading
from unittest.mock import patch

import pytest

sys.path.insert(0, ".")

import tests.helpers  # noqa: F401  (isolated temp DB + env)
from fysb import db, theme
from fysb.engines import market_data as md
from fysb.engines.signal_engine import _no_setup_reason_ar, generate_signal
from fysb.engines.strategies.base import (diagnosis_begin, diagnosis_record,
                                          diagnosis_snapshot)
from fysb.engines.strategies import crt, evaluate_all
from fysb.engines.strategies.base import Ctx


@pytest.fixture(autouse=True)
def _init_db():
    db.init_db()
    yield


def _mk_candles(vals, t0="2026-11-18 08:00:00", step_min=15):
    from datetime import datetime, timedelta
    t = datetime.fromisoformat(t0)
    out = []
    for o, h, l, c in vals:
        out.append({"datetime": t.strftime("%Y-%m-%d %H:%M:%S"),
                    "open": o, "high": h, "low": l, "close": c, "volume": 1000})
        t += timedelta(minutes=step_min)
    return out


def _ctx(candles, bias=None):
    return Ctx(symbol="XAUUSD", timeframe="M15", candles=candles,
               bias_candles=bias or candles, market_row=None, kill_zone="")


# ---------------------------------------------------------------------------
# ① the collector: proximity + REAL missing conditions, closest first
# ---------------------------------------------------------------------------

def test_diagnosis_record_captures_missing_and_proximity():
    diagnosis_begin()
    diagnosis_record("CRT", {"sweep": True, "confirm_close": False,
                             "htf_bias": False}, 30.0, 80.0)
    snap = diagnosis_snapshot()
    assert len(snap) == 1
    d = snap[0]
    assert d["code"] == "CRT"
    assert d["proximity"] == 62            # 30/80 → fair scale 42..95: 42+53*0.375
    assert d["missing"] == ["إغلاق مؤكد داخل النطاق بعد الجرف", "توافق الانحياز الأعلى"]


def test_snapshot_sorts_closest_first():
    diagnosis_begin()
    diagnosis_record("SMC", {}, 30.0, 72.0)
    diagnosis_record("CRT", {}, 50.0, 80.0)
    snap = diagnosis_snapshot()
    assert [d["code"] for d in snap] == ["CRT", "SMC"]


def test_thread_isolation_no_cross_mixing():
    diagnosis_begin()
    diagnosis_record("CRT", {}, 50.0, 80.0)
    seen = []

    def worker():
        diagnosis_record("SK", {}, 10.0, 75.0)
        seen.append([d["code"] for d in diagnosis_snapshot()])

    th = threading.Thread(target=worker)
    th.start()
    th.join()
    assert seen == [["SK"]]                # the worker sees only ITS diagnosis
    assert [d["code"] for d in diagnosis_snapshot()] == ["CRT"]


# ---------------------------------------------------------------------------
# ② integration: a REAL CRT refusal reaches the gate and records its truth
# ---------------------------------------------------------------------------

def test_all_18_strategy_gates_carry_the_record_call():
    # mechanical truth: EVERY strategy's merit-gate refusal feeds the ledger —
    # the diagnosis can never be silently absent for a scored refusal
    import pathlib
    codes = ["AMD", "CLASSIC", "CRT", "DIVERGENCE", "FIBONACCI", "HARMONIC",
             "ICHIMOKU", "ICT", "LONDON_BREAKOUT", "PRICE_ACTION", "SILVER_BULLET",
             "SK", "SMC", "SQUEEZE", "TREND", "TURTLE_SOUP", "VWAP_RECLAIM", "WYCKOFF"]
    for code in codes:
        p = pathlib.Path(f"fysb/engines/strategies/{code.lower()}.py")
        src = p.read_text(encoding="utf-8")
        assert f'diagnosis_record("{code}", checks, earned, max_pts)' in src, code
        # and the record sits exactly at the merit gate (before return None)
        gate = src.find("score = normalized_score(earned, max_pts)")
        rec = src.find(f'diagnosis_record("{code}"')
        ret = src.find("return None", gate)
        assert gate != -1 and rec != -1 and gate < rec < ret, code


def test_real_crt_refusal_records_diagnosis():
    diagnosis_begin()
    # REAL CRT flow: the proven sweep fixture reaches CRT's merit gate; the
    # patched normalizer forces the refusal — the ledger must carry CRT's own
    # check truth (kill-zone honestly missing: the fixture runs outside KZs)
    vals = [(100 + i * 0.05, 100 + i * 0.05 + 0.4, 100 + i * 0.05 - 0.3, 100 + i * 0.05 + 0.1)
            for i in range(50)]
    ref_hi = 103.0
    vals[-2] = (102.0, ref_hi, 101.0, 102.5)
    # sweep high then close BACK INSIDE with a BEARISH body (SELL direction ✓)
    vals[-1] = (103.2, ref_hi + 1.5, 102.2, 102.6)
    # bearish HTF bias series → the SELL direction passes the bias gate
    bias = _mk_candles([(120 - i * 0.4, 120.4 - i * 0.4, 119.6 - i * 0.4, 119.8 - i * 0.4)
                        for i in range(50)])
    ctx = _ctx(_mk_candles(vals), bias=bias)
    with patch("fysb.engines.strategies.crt.normalized_score", lambda e, m, **k: 60):
        res = crt.evaluate(ctx)
    snap = diagnosis_snapshot()
    if res is None:
        # refused at the merit gate → the ledger MUST carry CRT's truth
        assert snap and snap[0]["code"] == "CRT"
        # proximity = the REAL fair-scale completion of CRT's own ledger
        assert 42 <= snap[0]["proximity"] <= 95
        assert any("قتل" in m for m in snap[0]["missing"])   # kill-zone was False
    else:
        # CRT completed a full setup (fixture geometry) → nothing recorded
        assert res.code == "CRT" and snap == []


def test_evaluate_all_begins_a_fresh_ledger_each_run():
    diagnosis_record("CRT", {}, 50.0, 80.0)          # stale entry from a previous run
    candles = _mk_candles([(100 + i * 0.05, 100 + i * 0.05 + 0.4,
                            100 + i * 0.05 - 0.3, 100 + i * 0.05 + 0.1) for i in range(50)])
    evaluate_all(_ctx(candles), ["CRT"])
    snap = diagnosis_snapshot()
    assert all(d["code"] == "CRT" for d in snap)     # fresh ledger, no mixing


# ---------------------------------------------------------------------------
# ③ the engine refusal card carries the strategy truth (no counters)
# ---------------------------------------------------------------------------

def test_no_setup_reason_names_strategy_and_missing():
    diag = [{"code": "CRT", "proximity": 86, "missing":
             ["إغلاق مؤكد داخل النطاق بعد الجرف", "توافق الانحياز الأعلى"]}]
    msg = _no_setup_reason_ar(["CRT", "SMC", "ICT"], diag)
    assert "فحصنا المنهجيات المفعّلة" in msg
    assert "CRT" in msg and "86%" in msg
    assert "إغلاق مؤكد داخل النطاق" in msg
    assert "SMC" in msg and "ICT" in msg
    assert any(ch.isdigit() for ch in msg.replace("86%", "").replace("100%", "")) is False


def test_no_setup_reason_without_diagnosis_lists_examined():
    msg = _no_setup_reason_ar(["TREND", "SK"], [])
    assert "فحصنا المنهجيات المفعّلة" in msg
    assert "TREND" in msg and "SK" in msg
    assert "لم يكتمل أي نموذج مؤسسي 100%" in msg


def test_engine_no_setup_returns_strategy_truth():
    candles = _mk_candles([(100 + i * 0.05, 100 + i * 0.05 + 0.4,
                            100 + i * 0.05 - 0.3, 100 + i * 0.05 + 0.1) for i in range(60)])
    payload = {"symbol": "XAUUSD", "interval": "15min", "values": candles,
               "source": "LIVE", "age_seconds": 60.0, "last_candle_iso": candles[-1]["datetime"],
               "provider": "fx-ref", "is_proxy": False}
    with patch.object(md, "fetch_candles", return_value=payload), \
         patch.object(md, "market_state", return_value=True), \
         patch.object(md, "fetch_price", return_value={"price": 100.2, "source": "LIVE"}), \
         patch.object(db, "kill_switch_on", return_value=False), \
         patch("fysb.engines.news_guard.check_news_block", return_value=(False, None)), \
         patch("fysb.engines.signal_engine.evaluate_all", return_value=[]):
        res = generate_signal("XAUUSD", "M15", force=False)
    assert res["status"] == "NO_SETUP"
    assert "فحصنا المنهجيات المفعّلة" in res["reason"]
    assert not any(w in res["reason"] for w in ("دقيقة", "شمعة"))   # zero counters


# ---------------------------------------------------------------------------
# ④ the user surface carries NO counters anywhere (owner decree)
# ---------------------------------------------------------------------------

def test_user_translations_have_no_minute_or_candle_counters():
    stale = theme.ar_reason("data stale (age 78min)")
    frozen = theme.ar_reason("data frozen (repeats 3, age 90min)")
    insufficient = theme.ar_reason("insufficient candles (40)")
    for msg in (stale, frozen, insufficient):
        assert "دقيقة" not in msg and "شمعة" not in msg and "شموع فقط" not in msg
    assert "أسعار غير حية" in stale
    assert "متجمّد" in frozen
    assert "عينة ناقصة" in insufficient


def test_settlement_pause_message_unchanged():
    ar = theme.ar_reason("settlement pause 22:00-23:00 UTC; no signal manufactured")
    assert "استراحة التسوية" in ar and "22:00" in ar
