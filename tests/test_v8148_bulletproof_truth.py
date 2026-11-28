"""v8.14.8 tests — «الحقيقة المحكمة» (the bulletproof full-truth contract).

The owner's decree, institutionalized: EVERY refusal carries its REAL reason
— pre-gate withdrawals record their exact structural gate (Arabic), a dead
feed says the chosen strategy was NOT examined (no-repaint law), and the
crypto chain survives venue blocking via a Yahoo rescue layer. The mechanical
zero-silent-gates scan is a permanent CI contract: any future silent gate
turns the suite red.
"""
from __future__ import annotations

import glob
import re
import sys
from unittest.mock import patch

import pytest

sys.path.insert(0, ".")

import tests.helpers  # noqa: F401  (isolated temp DB + env)
from fysb import db, theme
from fysb.engines import market_data as md
from fysb.engines.signal_engine import _no_setup_reason_ar, generate_signal
from fysb.engines.strategies import crt, smc
from fysb.engines.strategies.base import (Ctx, diagnosis_begin,
                                          diagnosis_record, diagnosis_refuse,
                                          diagnosis_snapshot)

OLD_TEMPLATE = "السوق مغلق أو يمرّ بساعة سيولة"


@pytest.fixture(autouse=True)
def _init_db():
    db.init_db()
    yield


def _mk(vals, t0="2026-11-18 08:00:00", step=15):
    from datetime import datetime, timedelta
    t = datetime.fromisoformat(t0)
    out = []
    for o, h, l, c in vals:
        out.append({"datetime": t.strftime("%Y-%m-%d %H:%M:%S"), "open": o,
                    "high": h, "low": l, "close": c, "volume": 1000})
        t += timedelta(minutes=step)
    return out


TINY = _mk([(100, 100.4, 99.7, 100.1)] * 10)
BIAS = _mk([(120, 120.4, 119.6, 119.8)] * 10)
DEAD_FEED_REASON = ("📡 تغذية بيانات السوق لا تبلغ حد الصدق الآن — لا فحص مؤسسي ولا "
                    "إشارة من شموع جامدة (قانون عدم إعادة الرسم) — المنهجية المختارة (SMC) "
                    "لم تُفحص بعد — فور عودة تغذية صالحة يبدأ الفحص تلقائياً ويعود بالسبب "
                    "الحقيقي 100%")


# ---------------------------------------------------------------------------
# ① THE permanent contract: zero silent gates — mechanically scanned
# ---------------------------------------------------------------------------

def _evaluate_span(lines):
    start = None
    for i, ln in enumerate(lines):
        if re.match(r"^def evaluate\(", ln):
            start = i
        elif start is not None and re.match(r"^def |^class ", ln):
            return start, i
    return start, len(lines)


def test_zero_silent_gates_across_all_18_strategies():
    """Any return None inside evaluate() MUST carry its diagnosis within the
    3 lines above it — a future silent gate turns the suite red forever."""
    silent = []
    for f in sorted(glob.glob("fysb/engines/strategies/*.py")):
        lines = open(f, encoding="utf-8").read().split("\n")
        s, e = _evaluate_span(lines)
        if s is None:
            continue
        for i in range(s, e):
            if not re.match(r"^\s*return None(\s*#.*)?$", lines[i]):
                continue
            window = "\n".join(lines[max(0, i - 3):i])
            if "diagnosis_record(" not in window and "diagnosis_refuse(" not in window:
                silent.append(f"{f}:{i + 1}")
    assert silent == []


def test_diagnosis_refuse_records_arabic_gate_and_snapshot_sorts_merit_first():
    diagnosis_begin()
    diagnosis_refuse("SMC", "تاريخ الشموع أقصر من الحد الأدنى للنموذج")
    diagnosis_record("CRT", {"displacement": False, "kill_zone": True}, 30, 80)
    snap = diagnosis_snapshot()
    assert snap[0]["code"] == "CRT" and snap[0]["proximity"] > 0   # merit leads
    assert snap[1]["code"] == "SMC" and snap[1].get("pre") is True


def test_pre_gate_refusal_is_recorded_by_real_strategy_code():
    ctx = Ctx(symbol="XAUUSD", timeframe="M15", candles=TINY, bias_candles=BIAS,
              market_row=None, kill_zone="")
    diagnosis_begin()
    assert crt.evaluate(ctx) is None
    snap = diagnosis_snapshot()
    assert snap and snap[0]["code"] == "CRT" and snap[0].get("pre") is True
    assert "تاريخ الشموع أقصر" in snap[0]["missing"][0]
    diagnosis_begin()
    assert smc.evaluate(ctx) is None
    snap2 = diagnosis_snapshot()
    assert snap2 and snap2[0]["code"] == "SMC"


def test_no_setup_reason_for_pre_gate_has_no_percent_and_no_counters():
    diagnosis_begin()
    diagnosis_refuse("SMC", "خارج نافذة الرصاصة (نيويورك)")
    reason = _no_setup_reason_ar(["SMC"], diagnosis_snapshot())
    assert "انسحبت SMC عند شرطها التمهيدي" in reason
    assert "خارج نافذة الرصاصة (نيويورك)" in reason
    assert "اكتمال شروط" not in reason          # no fake completion percent
    assert not any(w in reason for w in ("دقيقة", "شمعة"))


def test_engine_no_setup_prefers_real_pre_gate_over_generic():
    candles = _mk([(100 + i * 0.05, 100 + i * 0.05 + 0.4,
                    100 + i * 0.05 - 0.3, 100 + i * 0.05 + 0.1) for i in range(60)])
    payload = {"symbol": "BTCUSD", "interval": "15min", "values": candles,
               "source": "LIVE", "age_seconds": 60.0,
               "last_candle_iso": candles[-1]["datetime"], "provider": "binance",
               "is_proxy": False}
    # fresh data, all strategies refuse pre-gate on the flat synthetic tape →
    # the verdict names a REAL gate, not the generic «لم يكتمل أي نموذج»
    with patch.object(md, "fetch_candles", return_value=payload), \
         patch.object(md, "market_state", return_value=True), \
         patch.object(md, "fetch_price", return_value={"price": 100.2, "source": "LIVE"}), \
         patch.object(db, "kill_switch_on", return_value=False), \
         patch("fysb.engines.news_guard.check_news_block", return_value=(False, None)):
        res = generate_signal("BTCUSD", "M15", force=False,
                              only_strategies=["SMC"])
    assert res["status"] == "NO_SETUP"
    assert "لم يكتمل أي نموذج" not in res["reason"]
    assert "شرطها التمهيدي" in res["reason"]


# ---------------------------------------------------------------------------
# ② the crypto-chain rescue: Yahoo layer survives venue blocking
# ---------------------------------------------------------------------------

def test_crypto_chain_carries_yahoo_rescue_layer():
    assert md._CANDLE_CHAINS["crypto"][:3] == ["binance", "bybit", "yahoo-crypto"]
    assert md._yahoo_symbol_for("BTCUSD") == "BTC-USD"
    assert md._yahoo_symbol_for("ETHUSD") == "ETH-USD"


def test_yahoo_crypto_leg_serves_real_spot_payload():
    from datetime import datetime, timedelta, timezone
    t0 = datetime.now(timezone.utc).replace(microsecond=0) - timedelta(minutes=16 * 60)
    vals = _mk([(100 + i * 0.05, 100 + i * 0.05 + 0.4,
                 100 + i * 0.05 - 0.3, 100 + i * 0.05 + 0.1) for i in range(60)],
               t0=t0.strftime("%Y-%m-%d %H:%M:%S"))
    with patch.object(md, "_fetch_yahoo_candles", return_value=vals) as y:
        out = md.fetch_candles("BTCUSD", "15min", "", 60, use_cache=False)
    assert any("BTC-USD" in str(c) for c in y.call_args_list)
    assert len(out["values"]) == 60
    assert out["provider"] == "yahoo-crypto"
    assert out["is_proxy"] is False          # real spot candles, not proxy


# ---------------------------------------------------------------------------
# ③ the dead-feed verdict: the chosen strategy was NOT examined (100% truth)
# ---------------------------------------------------------------------------

def test_stale_engine_reason_names_no_examination_and_chosen_strategy():
    candles = _mk([(100, 100.4, 99.7, 100.1)] * 60,
                  t0="2026-11-18 08:00:00")
    payload = {"symbol": "BTCUSD", "interval": "15min", "values": candles,
               "source": "LIVE", "age_seconds": 7200.0,
               "last_candle_iso": candles[-1]["datetime"], "provider": "binance",
               "is_proxy": False, "chain_errors": ["binance:stale", "bybit:stale"]}
    with patch.object(md, "fetch_candles", return_value=payload), \
         patch.object(md, "market_state", return_value=True), \
         patch.object(db, "kill_switch_on", return_value=False), \
         patch("fysb.engines.news_guard.check_news_block", return_value=(False, None)):
        res = generate_signal("BTCUSD", "M15", force=False,
                              only_strategies=["SMC"])
    assert res["status"] == "REJECTED"
    assert "لم تُفحص" in res["reason"]
    assert "(SMC)" in res["reason"]            # the chosen methodology, by name
    assert "عدم إعادة الرسم" in res["reason"]  # the law that forbids judging
    assert "الحقيقي 100%" in res["reason"]
    assert not any(w in res["reason"] for w in ("دقيقة", "شمعة"))


def test_stale_engine_reason_without_choice_names_all_methodologies():
    candles = _mk([(100, 100.4, 99.7, 100.1)] * 60)
    payload = {"symbol": "BTCUSD", "interval": "15min", "values": candles,
               "source": "LIVE", "age_seconds": 7200.0,
               "last_candle_iso": candles[-1]["datetime"], "provider": "binance",
               "is_proxy": False}
    with patch.object(md, "fetch_candles", return_value=payload), \
         patch.object(md, "market_state", return_value=True), \
         patch.object(db, "kill_switch_on", return_value=False), \
         patch("fysb.engines.news_guard.check_news_block", return_value=(False, None)):
        res = generate_signal("BTCUSD", "M15", force=False)
    assert "المنهجيات المفعّلة لم تُفحص بعد" in res["reason"]


def test_dead_feed_card_is_specific_never_the_old_template():
    html = __import__("fysb.ui.cards", fromlist=["render_no_signal_card"]).render_no_signal_card(
        symbol="BTCUSD", timeframe="M15", strategy_label="SMC", style_label="يومي",
        status="REJECTED", reason=DEAD_FEED_REASON)
    assert "الفحص الاستراتيجي متوقف" in html
    assert "لم تُفحص" in html and "عدم إعادة الرسم" in html
    assert "البحث التلقائي" in html
    assert OLD_TEMPLATE not in html
    assert "حالة الجلسة والسيولة" not in html


def test_engine_cycle_starts_a_fresh_refusal_ledger():
    # cross-cycle contamination is impossible: begin() runs per generate_signal
    diagnosis_begin()
    diagnosis_refuse("CRT", "بوابة قديمة من دورة سابقة")
    candles = _mk([(100 + i * 0.05, 100 + i * 0.05 + 0.4,
                    100 + i * 0.05 - 0.3, 100 + i * 0.05 + 0.1) for i in range(60)])
    payload = {"symbol": "BTCUSD", "interval": "15min", "values": candles,
               "source": "LIVE", "age_seconds": 60.0,
               "last_candle_iso": candles[-1]["datetime"], "provider": "binance",
               "is_proxy": False}
    with patch.object(md, "fetch_candles", return_value=payload), \
         patch.object(md, "market_state", return_value=True), \
         patch.object(md, "fetch_price", return_value={"price": 100.2, "source": "LIVE"}), \
         patch.object(db, "kill_switch_on", return_value=False), \
         patch("fysb.engines.news_guard.check_news_block", return_value=(False, None)), \
         patch("fysb.engines.signal_engine.evaluate_all", return_value=[]):
        res = generate_signal("BTCUSD", "M15", force=False)
    assert res["status"] == "NO_SETUP"
    assert "بوابة قديمة" not in res["reason"]   # the stale record never leaks


# ---------------------------------------------------------------------------
# ④ regression guards — v8.14.6/v8.14.7 contracts stay intact
# ---------------------------------------------------------------------------

def test_no_counter_words_in_any_pre_gate_label():
    # sweep the actual source: no refusal label may read like a counter
    bad = []
    for f in sorted(glob.glob("fysb/engines/strategies/*.py")):
        for m in re.finditer(r'diagnosis_refuse\("[A-Z_]+", "([^"]+)"\)',
                             open(f, encoding="utf-8").read()):
            if "دقيقة" in m.group(1) or "شمعة" in m.group(1):
                bad.append(f"{f}: {m.group(1)}")
    assert bad == []


def test_theme_translations_and_run_lines_stay_royal():
    assert "مغلق فعلاً 100%" in theme.ar_result("MARKET_CLOSED")
    assert "أسعار غير حية" in theme.ar_reason("data stale (age 942min)")
    line = theme.ar_run_line("STALE_DATA", "last candle x age=942s")
    assert "بيانات أقدم من الحد المسموح" in line


@pytest.fixture(autouse=True)
def _frozen_market_friday():
    """v8.14.10 — CI crossed the Fri→Sat boundary and the weekend widening
    reclassified every test's «stale» payload as fresh (mechanics unchanged).
    Pin the market clock to Friday noon UTC — the suite tests the MECHANICS,
    never the wall calendar."""
    from tests.helpers import frozen_market_clock
    with frozen_market_clock():
        yield
