"""v8.14.9 tests — «مفتاحا الصدق الملكيان» (the owner-controlled honesty laws).

⚖️ بوابة حد الصدق (القِدم) and 🧊 قانون عدم إعادة الرسم — both default ON
(the institutional contract is unchanged); the OWNER may disarm either from
System & Security. Every disarm is audited, every bypass is stamped honestly
on the user surface and logged in engine_runs — nothing silent, nothing fake.
Refusals keep their 100% royal reasons in every mode.
"""
from __future__ import annotations

import asyncio
import sys
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from unittest.mock import patch

import pytest

sys.path.insert(0, ".")

import tests.helpers  # noqa: F401  (isolated temp DB + env)
from fysb import db
from fysb.engines import honesty, market_data as md
from fysb.engines.signal_engine import generate_signal
from fysb.engines.strategies.base import StrategyResult

STALE_REFUSAL = "لم تُفحص"
DEFERRED = "وضع المؤجل الصادق"


@pytest.fixture(autouse=True)
def _init_db_and_restore_laws():
    db.init_db()
    yield
    # the laws are restored to the institutional default after every test —
    # the whole existing suite must never observe a disarmed law
    db.set_setting("honesty_stale_gate", "1")
    db.set_setting("no_repaint_closed_only", "1")


def _stale_payload(symbol="BTCUSD", age_s=7200.0):
    from tests.helpers import uptrend_candles
    candles = uptrend_candles(120)
    last = candles[-1]["datetime"]
    return {"symbol": symbol, "interval": "15min", "values": candles,
            "source": "LIVE", "age_seconds": age_s, "last_candle_iso": last,
            "provider": "binance", "is_proxy": False,
            "chain_errors": ["binance:stale"]}


def _fresh_payload_with_forming(symbol="BTCUSD"):
    """120 closed candles + the STILL-FORMING bar as the last row (age ≈ 0)."""
    now = datetime.now(timezone.utc).replace(microsecond=0)
    bucket = now.replace(second=0, microsecond=0)
    bucket -= timedelta(minutes=bucket.minute % 15)
    out = []
    px = 100.0
    for i in range(120):
        t = bucket - timedelta(minutes=15 * (121 - i))
        out.append({"datetime": t.strftime("%Y-%m-%d %H:%M:%S"), "open": px,
                    "high": px + 0.3, "low": px - 0.25, "close": px + 0.2,
                    "volume": 1000})
        px += 0.2
    forming = {"datetime": bucket.strftime("%Y-%m-%d %H:%M:%S"), "open": px,
               "high": px + 0.4, "low": px - 0.3, "close": px + 0.1,
               "volume": 1000}
    out.append(forming)
    return {"symbol": symbol, "interval": "15min", "values": out,
            "source": "LIVE", "age_seconds": 30.0,
            "last_candle_iso": forming["datetime"], "provider": "binance",
            "is_proxy": False}, forming


def _run(payload, only=None, news_ok=True):
    with patch.object(md, "fetch_candles", return_value=payload), \
         patch.object(md, "market_state", return_value=True), \
         patch.object(db, "kill_switch_on", return_value=False), \
         patch("fysb.engines.news_guard.check_news_block",
               return_value=(False, None) if news_ok else (True, "news")):
        return generate_signal("BTCUSD", "M15", force=False,
                               only_strategies=only)


# ---------------------------------------------------------------------------
# ① the institutional default: both laws ON — the contract unchanged
# ---------------------------------------------------------------------------

def test_defaults_both_laws_armed():
    assert honesty.stale_gate_enabled() is True
    assert honesty.closed_candles_only() is True
    assert honesty.states() == {"honesty_stale_gate": True,
                                "no_repaint_closed_only": True}


def test_gate_armed_stale_still_refused_with_royal_reason():
    res = _run(_stale_payload(), only=["SMC"])
    assert res["status"] == "REJECTED"
    assert STALE_REFUSAL in res["reason"] and "(SMC)" in res["reason"]


# ---------------------------------------------------------------------------
# ② ⚖️ gate disarmed — the royal deferred-honesty mode
# ---------------------------------------------------------------------------

def test_gate_disarmed_stale_reaches_the_strategies():
    called = []
    def _spy(ctx, allowed):
        called.append(True)
        return []
    db.set_setting("honesty_stale_gate", "0")
    with patch("fysb.engines.signal_engine.evaluate_all", side_effect=_spy):
        res = _run(_stale_payload(), only=["SMC"])
    assert called, "the strategies MUST be examined when the gate is disarmed"
    assert res["status"] == "NO_SETUP"
    with db.conn() as c:
        runs = [r["result"] for r in
                c.execute("SELECT result FROM engine_runs WHERE result LIKE '%BYPASS'").fetchall()]
    assert any("STALE_BYPASS" in r for r in runs)


def test_gate_disarmed_created_signal_carries_the_deferred_stamp():
    px = 120.0
    cand = StrategyResult("SMC", "BUY", px, px - 2.0, px + 4.0, px + 6.0,
                          px + 9.0, 2.0, 95, ["fresh setup"])
    db.set_setting("honesty_stale_gate", "0")
    # v8.14.10 — shared-session hygiene: the full-cycle creation must start
    # from a clean ledger (cross-file rows poison the gates in suite order).
    with db.conn() as c:
        c.execute("DELETE FROM signals")
        c.execute("DELETE FROM engine_runs")
    with patch.object(md, "fetch_candles", return_value=_stale_payload()), \
         patch.object(md, "market_state", return_value=True), \
         patch.object(db, "kill_switch_on", return_value=False), \
         patch("fysb.engines.news_guard.check_news_block", return_value=(False, None)), \
         patch("fysb.engines.signal_engine.evaluate_all", return_value=[cand]), \
         patch.object(md, "fetch_price", return_value={"price": px, "source": "LIVE"}), \
         patch("fysb.engines.intelligence.dxy_bias_check", return_value={"delta": 0, "note": ""}), \
         patch("fysb.engines.signal_engine.is_stale_entry", return_value=(False, "")), \
         patch("fysb.engines.signal_engine.validate_signal_geometry", return_value=(True, "")):
        res = generate_signal("BTCUSD", "M15", force=False, only_strategies=["SMC"])
    assert res["status"] == "SIGNAL_CREATED" and res["signal_id"]
    with db.conn() as c:
        reason = c.execute("SELECT reason FROM signals WHERE id=?",
                           (res["signal_id"],)).fetchone()["reason"]
    assert DEFERRED in reason and "تأكيد كوت حي" in reason
    assert not any(w in reason for w in ("دقيقة", "7200", "2h"))   # zero counters


def test_gate_disarmed_frozen_feed_stamps_the_frozen_variant():
    db.set_setting("honesty_stale_gate", "0")
    called = []
    def _spy(ctx, allowed):
        called.append(True)
        return []
    payload = _stale_payload()
    payload["feed_frozen"] = True
    payload["frozen_repeats"] = 2
    with patch("fysb.engines.signal_engine.evaluate_all", side_effect=_spy):
        res = _run(payload)
    assert called and res["status"] == "NO_SETUP"
    with db.conn() as c:
        runs = [r["result"] for r in
                c.execute("SELECT result FROM engine_runs WHERE result='FROZEN_BYPASS'").fetchall()]
    assert runs


def test_force_bypass_is_stamped_and_traced_even_with_gate_armed():
    cand = StrategyResult("SMC", "BUY", 120.0, 118.0, 124.0, 126.0, 129.0,
                          2.0, 95, ["fresh setup"])
    payload = _stale_payload()
    # v8.14.10 — shared-session hygiene (same doctrine as above).
    with db.conn() as c:
        c.execute("DELETE FROM signals")
        c.execute("DELETE FROM engine_runs")
    with patch.object(md, "fetch_candles", return_value=payload), \
         patch.object(md, "market_state", return_value=True), \
         patch.object(db, "kill_switch_on", return_value=False), \
         patch("fysb.engines.news_guard.check_news_block", return_value=(False, None)), \
         patch("fysb.engines.signal_engine.evaluate_all", return_value=[cand]), \
         patch.object(md, "fetch_price", return_value={"price": 120.0, "source": "LIVE"}), \
         patch("fysb.engines.intelligence.dxy_bias_check", return_value={"delta": 0, "note": ""}), \
         patch("fysb.engines.signal_engine.is_stale_entry", return_value=(False, "")), \
         patch("fysb.engines.signal_engine.validate_signal_geometry", return_value=(True, "")):
        res = generate_signal("BTCUSD", "M15", force=True, only_strategies=["SMC"])
    assert res["status"] == "SIGNAL_CREATED"
    assert payload.get("stale_bypass") is True
    with db.conn() as c:
        runs = [r["result"] for r in
                c.execute("SELECT result FROM engine_runs WHERE result='FORCE_BYPASS'").fetchall()]
    assert runs


def test_stale_bypass_note_is_royal_and_counter_free():
    note = honesty.stale_bypass_note({"feed_frozen": False})
    frozen = honesty.stale_bypass_note({"feed_frozen": True})
    assert DEFERRED in note and "تأكيد كوت حي" in note
    assert "جامدة" in frozen
    assert not any(w in note + frozen for w in ("دقيقة", "شمعة"))


# ---------------------------------------------------------------------------
# ③ 🧊 the no-repaint law — forming bar in/out, traced either way
# ---------------------------------------------------------------------------

def test_repaint_armed_forming_bar_is_stripped():
    payload, forming = _fresh_payload_with_forming()
    seen = []
    def _spy(ctx, allowed):
        seen.append([c["datetime"] for c in ctx.candles])
        return []
    with patch("fysb.engines.signal_engine.evaluate_all", side_effect=_spy):
        _run(payload)
    assert seen and seen[0][-1] != forming["datetime"]


def test_repaint_disarmed_forming_bar_enters_and_run_is_traced():
    db.set_setting("no_repaint_closed_only", "0")
    payload, forming = _fresh_payload_with_forming()
    seen = []
    def _spy(ctx, allowed):
        seen.append([c["datetime"] for c in ctx.candles])
        return []
    with patch("fysb.engines.signal_engine.evaluate_all", side_effect=_spy):
        res = _run(payload)
    assert seen and seen[0][-1] == forming["datetime"]   # the live bar participates
    assert res["status"] == "NO_SETUP"
    with db.conn() as c:
        runs = [r["result"] for r in
                c.execute("SELECT result FROM engine_runs WHERE result='NO_SETUP'").fetchall()]
    assert runs


def test_repaint_disarmed_with_bias_and_correlation_follow_the_law():
    # the law is read once per cycle and applied to entry+bias+correlation
    src = open("fysb/engines/signal_engine.py", encoding="utf-8").read()
    assert src.count("if _strip else list(") == 3   # entry + bias + correlation


# ---------------------------------------------------------------------------
# ④ the System & Security page — luxurious toggles, audited, honest labels
# ---------------------------------------------------------------------------

class _FakeMsg:
    def __init__(self):
        self.sent = []
        self.markups = []

    async def reply_text(self, text, **kw):
        self.sent.append(text)
        self.markups.append(kw.get("reply_markup"))
        return SimpleNamespace(message_id=1)

class _FakeQuery:
    def __init__(self):
        self.from_user = SimpleNamespace(id=9001)
        self.message = _FakeMsg()
    async def answer(self):
        return True


def _render_capture(q, text, markup, bot=None):
    q.message.sent.append(text)


def test_settings_page_shows_both_states_and_toggles():
    from fysb.handlers import admin_system
    q = _FakeQuery()
    asyncio.run(admin_system.admin_settings(q, 9001, None, None))
    body = q.message.sent[-1]
    assert "⚖️ بوابة حد الصدق (القِدم):" in body and "عقد المؤسسة" in body
    assert "🧊 قانون عدم إعادة الرسم:" in body
    assert "مفعّلة" in body and "مفعّل" in body


def test_toggle_stale_gate_flips_audits_and_rerenders():
    from fysb.handlers import admin_system
    from fysb.services import audit as audit_log
    q = _FakeQuery()
    asyncio.run(admin_system.admin_toggle_stale_gate(q, 9001, None, None))
    assert db.get_setting("honesty_stale_gate", "1") == "0"
    entries = audit_log.recent(20, 0)
    assert any("honesty_stale_gate:0" in (e.get("action") or "") for e in entries)
    body = q.message.sent[-1]
    assert "وضع المؤجل الصادق" in body          # the honest consequence, stated
    asyncio.run(admin_system.admin_toggle_stale_gate(q, 9001, None, None))
    assert db.get_setting("honesty_stale_gate", "1") == "1"


def test_toggle_repaint_flips_audits_and_rerenders():
    from fysb.handlers import admin_system
    from fysb.services import audit as audit_log
    q = _FakeQuery()
    asyncio.run(admin_system.admin_toggle_repaint(q, 9001, None, None))
    assert db.get_setting("no_repaint_closed_only", "1") == "0"
    entries = audit_log.recent(20, 0)
    assert any("no_repaint_closed_only:0" in (e.get("action") or "") for e in entries)
    body = q.message.sent[-1]
    assert "غير موصى به" in body                # the honest warning, stated
    asyncio.run(admin_system.admin_toggle_repaint(q, 9001, None, None))
    assert db.get_setting("no_repaint_closed_only", "1") == "1"


def test_dispatcher_knows_both_routes():
    src = open("fysb/handlers/dispatcher.py", encoding="utf-8").read()
    assert '"togglestalegate"' in src and '"togglerepaint"' in src


# ---------------------------------------------------------------------------
# ⑤ both disarmed — the full luxury cycle still tells the whole truth
# ---------------------------------------------------------------------------

def test_both_disarmed_full_cycle_stays_honest():
    db.set_setting("honesty_stale_gate", "0")
    db.set_setting("no_repaint_closed_only", "0")
    payload, forming = _fresh_payload_with_forming()
    payload["age_seconds"] = 7200.0            # ancient + forming → both laws off
    seen = []
    def _spy(ctx, allowed):
        seen.append([c["datetime"] for c in ctx.candles])
        return []
    with patch("fysb.engines.signal_engine.evaluate_all", side_effect=_spy):
        res = _run(payload)
    assert seen and seen[0][-1] == forming["datetime"]
    assert res["status"] == "NO_SETUP"
    with db.conn() as c:
        runs = [r["result"] for r in
                c.execute("SELECT result FROM engine_runs WHERE result='STALE_BYPASS'").fetchall()]
    assert runs                                # the bypass is always traced


def test_refusal_reasons_stay_royal_in_every_mode():
    # market-closed refusals are untouched by the honesty keys
    with patch.object(md, "market_state", return_value=False), \
         patch.object(md, "in_settlement_pause", return_value=False), \
         patch.object(db, "kill_switch_on", return_value=False):
        res = generate_signal("BTCUSD", "M15", force=False)
    assert res["status"] == "REJECTED"
    assert "مغلق فعلاً 100%" in res["reason"]   # v8.14.7 royal closure truth


@pytest.fixture(autouse=True)
def _frozen_market_friday():
    """v8.14.10 — CI crossed the Fri→Sat boundary and the weekend widening
    reclassified every test's «stale» payload as fresh (mechanics unchanged).
    Pin the market clock to Friday noon UTC — the suite tests the MECHANICS,
    never the wall calendar."""
    from tests.helpers import frozen_market_clock
    with frozen_market_clock():
        yield
