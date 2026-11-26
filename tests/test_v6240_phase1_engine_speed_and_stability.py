"""v6.24.0 — Phase 1 Regression & Guarantee Suite:
1. Universal `cap_stop` across all 13 strategies + symmetric `ict.py` SELL stop fix.
2. Provider Circuit Breaker (`provider_health.py`) + Free Crypto WebSocket feed (`crypto_ws.py`).
3. Multi-timeframe Auto Scanner (`engine_scan_timeframes`, `a:scantfs`, `a:togglescantf:*`).
4. Live-editable Admin Auto Signal Card (`_pin_admin_delivery` -> `signal_deliveries`).
5. Tier-Aware Best Pick for FREE (`AUTO_FREE` companion signal sent strictly to FREE users only).
6. FREE Trial Luxury Drip Reminders (`48h` / `24h` / `expired`, once per stage).
"""
import asyncio
import json
import os
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from unittest.mock import patch

from fysb import config, db
from fysb.engines import crypto_ws, market_data, provider_health, signal_engine, tracker
from fysb.engines.strategies.base import StrategyResult, cap_stop
from fysb.handlers import background, dispatcher
from fysb.services import subscriptions, users

ADMIN = 980001
FREE_UID = 980010
PRO_UID = 980011
TRIAL_48H_UID = 980012
TRIAL_24H_UID = 980013
TRIAL_EXP_UID = 980014


def _fire(cb_data: str, uid: int):
    captured = {"text": "", "kb": None}

    async def _edit(text, reply_markup=None, parse_mode=None, **kw):
        captured["text"] = text
        captured["kb"] = reply_markup

    async def _reply(text, reply_markup=None, parse_mode=None, **kw):
        captured["text"] = text
        captured["kb"] = reply_markup

    async def _answer(*a, **kw):
        pass

    msg = SimpleNamespace(chat_id=uid, message_id=1, edit_text=_edit, reply_text=_reply, chat=SimpleNamespace(id=uid))
    q = SimpleNamespace(
        data=cb_data,
        from_user=SimpleNamespace(id=uid, username=f"u{uid}", first_name="U", full_name="U"),
        message=msg,
        answer=_answer,
    )
    upd = SimpleNamespace(callback_query=q, effective_user=q.from_user, effective_chat=SimpleNamespace(id=uid))
    ctx = SimpleNamespace(user_data={}, bot_data={}, bot=SimpleNamespace())
    asyncio.run(dispatcher.buttons(upd, ctx))
    datas = []
    if captured["kb"] and getattr(captured["kb"], "inline_keyboard", None):
        for row in captured["kb"].inline_keyboard:
            for b in row:
                if getattr(b, "callback_data", None):
                    datas.append(b.callback_data)
    return captured["text"], datas


class TestV6240Phase1EngineSpeedAndStability(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self._old_db = config.DB_FILE
        self._old_admin = config.ADMIN_ID
        config.DB_FILE = os.path.join(self._tmp.name, "v6240.db")
        config.ADMIN_ID = ADMIN
        db.init_db()
        db.set_setting("news_guard_enabled", "0")
        provider_health.reset()
        crypto_ws.reset()
        market_data._cache.clear()
        market_data._fail_streak.clear()

    def tearDown(self):
        provider_health.reset()
        crypto_ws.reset()
        config.DB_FILE = self._old_db
        config.ADMIN_ID = self._old_admin
        self._tmp.cleanup()

    # 1) Universal cap_stop across BUY & SELL
    def test_cap_stop_enforces_side_min_and_preserves_structure(self):
        # Wide BUY stop stays beyond structural invalidation; issuance may reject it.
        self.assertAlmostEqual(cap_stop(3500.0, 3410.0, "BUY", 10.0), 3410.0, places=2)
        # Symmetric SELL rule: never cosmetically tighten a structural stop.
        self.assertAlmostEqual(cap_stop(3500.0, 3590.0, "SELL", 10.0), 3590.0, places=2)
        # Reversed BUY stop -> corrected to fallback_atr below entry
        self.assertAlmostEqual(cap_stop(3500.0, 3510.0, "BUY", 10.0, fallback_atr=1.6), 3484.0, places=2)
        # Normal stop inside [min_atr, max_atr] -> preserved untouched
        self.assertAlmostEqual(cap_stop(3500.0, 3482.0, "BUY", 10.0), 3482.0, places=2)

    # 2) Circuit Breaker + Crypto WebSocket live feed
    def test_circuit_breaker_trips_recovers_and_shows_in_admin_health(self):
        t0 = 1_000_000.0
        self.assertEqual(provider_health.state_of("binance", now=t0), "CLOSED")
        self.assertFalse(provider_health.record_failure("binance", "timeout 1", now=t0))
        self.assertFalse(provider_health.record_failure("binance", "timeout 2", now=t0 + 1))
        self.assertTrue(provider_health.record_failure("binance", "timeout 3", now=t0 + 2))
        self.assertEqual(provider_health.state_of("binance", now=t0 + 10), "OPEN")
        self.assertTrue(provider_health.is_open("binance", now=t0 + 10))

        # After cooldown (120s), transitions to HALF_OPEN allowing 1 probe
        self.assertEqual(provider_health.state_of("binance", now=t0 + 130), "HALF_OPEN")
        self.assertFalse(provider_health.is_open("binance", now=t0 + 130))  # probe allowed
        self.assertTrue(provider_health.is_open("binance", now=t0 + 131))   # second concurrent blocked
        provider_health.record_success("binance", latency_ms=42.5, now=t0 + 132)
        self.assertEqual(provider_health.state_of("binance", now=t0 + 133), "CLOSED")

        # Admin Health panel displays Circuit Breaker & WebSocket status cleanly
        txt, _ = _fire("a:health", ADMIN)
        self.assertIn("Circuit Breaker", txt)
        self.assertIn("binance", txt)
        self.assertIn("WebSocket", txt)

    def test_crypto_ws_parsers_and_market_data_fast_path(self):
        # Parse Binance combined miniTicker
        ok_b = crypto_ws.parse_binance_message(json.dumps({
            "stream": "btcusdt@miniTicker",
            "data": {"s": "BTCUSDT", "c": "95432.50"},
        }))
        self.assertTrue(ok_b)
        self.assertAlmostEqual(crypto_ws.get_live_price("BTCUSD"), 95432.50, places=2)

        # Parse Bybit v5 ticker
        ok_by = crypto_ws.parse_bybit_message(json.dumps({
            "topic": "tickers.ETHUSDT",
            "data": {"symbol": "ETHUSDT", "lastPrice": "3412.80"},
        }))
        self.assertTrue(ok_by)
        self.assertAlmostEqual(crypto_ws.get_live_price("ETHUSD"), 3412.80, places=2)

        # fetch_price("BTCUSD") hits WebSocket fast-path with ZERO HTTP calls!
        with patch.object(market_data, "_http_json", side_effect=AssertionError("HTTP must not be called when WS tick is fresh")):
            q = market_data.fetch_price("BTCUSD")
        self.assertAlmostEqual(q["price"], 95432.50, places=2)
        self.assertTrue(q.get("ws"))

    # 3) Multi-Timeframe Auto Scanner + Admin UI toggle
    def test_multi_timeframe_scanner_defaults_and_admin_toggle(self):
        tfs = background._armed_scan_timeframes()
        self.assertEqual(tfs, ["M15", "H1"])

        # Admin toggles H4 ON -> engine_scan_timeframes becomes M15,H1,H4
        txt, datas = _fire("a:togglescantf:H4", ADMIN)
        self.assertIn("M15,H1,H4", txt)
        self.assertIn("a:togglescantf:M15", datas)
        self.assertEqual(db.get_setting("engine_scan_timeframes"), "M15,H1,H4")

        # Toggling off M15 and H1 leaves H4; trying to toggle off the last TF keeps at least 1
        _fire("a:togglescantf:M15", ADMIN)
        _fire("a:togglescantf:H1", ADMIN)
        self.assertEqual(db.get_setting("engine_scan_timeframes"), "H4")
        _fire("a:togglescantf:H4", ADMIN)
        self.assertEqual(db.get_setting("engine_scan_timeframes"), "H4")

    # 4) Live-editable Admin Auto Signal Card
    def test_pin_admin_delivery_enables_live_tracker_edits(self):
        with db.conn() as c:
            sid = c.execute(
                """INSERT INTO signals(symbol,timeframe,direction,entry,stop,tp1,tp2,tp3,
                   targets,rr,score,strategy,reason,status,source,created_at)
                   VALUES('XAUUSD','M15','BUY',3550.0,3535.0,3580.0,3595.0,3617.5,
                   '3580,3595,3617.5',2.0,90,'SMC','test','ACTIVE','AUTO',?)""",
                (config.now(),),
            ).lastrowid
        fake_msg = SimpleNamespace(chat_id=ADMIN, message_id=4242)
        background._pin_admin_delivery(sid, fake_msg)

        refs = tracker._delivery_refs(sid)
        self.assertTrue(any(int(r["user_id"]) == ADMIN and int(r["message_id"]) == 4242 for r in refs))

    # 5) Tier-Aware Best Pick for FREE (`AUTO_FREE` strictly FREE-only!)
    def test_tier_aware_free_companion_signal_delivered_only_to_free_users(self):
        past = (datetime.now(timezone.utc) - timedelta(hours=2)).isoformat()
        users.ensure_user(SimpleNamespace(id=FREE_UID, username="f", first_name="F", full_name="F"))
        users.ensure_user(SimpleNamespace(id=PRO_UID, username="p", first_name="P", full_name="P"))
        with db.conn() as c:
            c.execute("UPDATE users SET joined_at=? WHERE user_id IN (?,?)", (past, FREE_UID, PRO_UID))
        subscriptions.grant_subscription(PRO_UID, "PRO")

        # Build clean bullish M15 candles (real volatility ~0.2% ATR)
        candles = []
        px = 3540.0
        now_dt = datetime.now(timezone.utc)
        for i in range(60):
            dt_s = (now_dt - timedelta(minutes=15 * (60 - i))).strftime("%Y-%m-%d %H:%M:%S")
            o = px
            c_px = px + 0.8
            candles.append({"open": o, "high": c_px + 3.0, "low": o - 3.0, "close": c_px, "datetime": dt_s})
            px = c_px
        payload = {
            "symbol": "XAUUSD",
            "interval": "15min",
            "values": candles,
            "last_candle_iso": candles[-1]["datetime"],
            "age_seconds": 30,
            "source": "LIVE",
        }
        top_smc = StrategyResult(
            code="SMC", direction="BUY", entry=px, stop=px - 12.0,
            tp1=px + 24.0, tp2=px + 36.0, tp3=px + 54.0, rr=2.0, score=92,
            reasons=["SMC demand"], checks={},
        )
        free_classic = StrategyResult(
            code="CLASSIC", direction="BUY", entry=px, stop=px - 12.0,
            tp1=px + 24.0, tp2=px + 36.0, tp3=px + 54.0, rr=2.0, score=86,
            reasons=["CLASSIC breakout"], checks={},
        )
        with patch.object(market_data, "market_state", return_value=True), \
             patch.object(market_data, "fetch_candles", return_value=payload), \
             patch.object(market_data, "fetch_price", return_value={"price": px}), \
             patch.object(signal_engine, "evaluate_all", return_value=[top_smc, free_classic]), \
             patch("fysb.engines.quality_gate.evaluate_quality_gate", return_value=(True, "")):
            res = signal_engine.generate_signal("XAUUSD", "M15", source="AUTO")

        self.assertEqual(res["status"], "SIGNAL_CREATED")
        self.assertIsNotNone(res.get("free_fallback_signal_id"))

        # Emit AUTO_FREE companion -> ONLY FREE_UID receives it, NOT PRO_UID!
        sent_to = []

        async def _fake_send(chat_id, text, **kw):
            sent_to.append(int(chat_id))
            return SimpleNamespace(chat_id=chat_id, message_id=909)

        with db.conn() as c:
            free_sig = dict(c.execute("SELECT * FROM signals WHERE id=?", (res["free_fallback_signal_id"],)).fetchone())
        self.assertEqual(free_sig["source"], "AUTO_FREE")
        self.assertEqual(free_sig["strategy"], "CLASSIC")

        asyncio.run(signal_engine.emit_to_users(free_sig, _fake_send, include_channels=True))
        self.assertIn(FREE_UID, sent_to)
        self.assertNotIn(PRO_UID, sent_to)

    # 6) FREE Trial Luxury Drip Reminders (48h / 24h / expired — once per stage!)
    def test_free_trial_drip_reminders_all_three_stages_deduped(self):
        now_dt = datetime.now(timezone.utc)
        # FREE plan default duration_days = 7
        for uid, age_hours in (
            (TRIAL_48H_UID, 7 * 24 - 36),   # 36h left -> stage 48h
            (TRIAL_24H_UID, 7 * 24 - 12),   # 12h left -> stage 24h
            (TRIAL_EXP_UID, 7 * 24 + 3),    # expired 3h ago -> stage expired
        ):
            users.ensure_user(SimpleNamespace(id=uid, username=f"t{uid}", first_name="T", full_name="T"))
            with db.conn() as c:
                c.execute(
                    "UPDATE users SET joined_at=? WHERE user_id=?",
                    ((now_dt - timedelta(hours=age_hours)).isoformat(), uid),
                )

        due = {d["user_id"]: d for d in subscriptions.free_trial_due_reminders()}
        self.assertEqual(due[TRIAL_48H_UID]["stage"], "48h")
        self.assertEqual(due[TRIAL_24H_UID]["stage"], "24h")
        self.assertEqual(due[TRIAL_EXP_UID]["stage"], "expired")

        # Verify luxury payloads render cleanly for both countdown and expired stages
        txt_24, rows_24 = background._build_free_trial_drip_payload(due[TRIAL_24H_UID])
        self.assertIn("تنبيه أخير", txt_24)
        self.assertTrue(any(b.callback_data == "u:plan:PRO" for r in rows_24 for b in r))

        txt_exp, rows_exp = background._build_free_trial_drip_payload(due[TRIAL_EXP_UID])
        self.assertIn("انتهت فترتك المجانية", txt_exp)
        self.assertTrue(any(b.callback_data == "u:plan:VIP" for r in rows_exp for b in r))

        # Mark all 3 reminded -> calling again returns empty (zero duplicate spam!)
        for item in due.values():
            subscriptions.mark_free_trial_reminded(item["user_id"], item["stage"])
        due_again = {d["user_id"]: d for d in subscriptions.free_trial_due_reminders()}
        self.assertNotIn(TRIAL_48H_UID, due_again)
        self.assertNotIn(TRIAL_24H_UID, due_again)
        self.assertNotIn(TRIAL_EXP_UID, due_again)


if __name__ == "__main__":
    unittest.main()
