"""V7 suite — the new real-data & gating features.

Covers:
  * hidden provider chain: symbol mapping, failover order, honest forex error
    without a key, provider names never leaking into user-facing text
  * broker connectivity: fake keys rejected, missing secret rejected,
    custom adapters validated strictly (no more fake HTTP 200)
  * payment-method flow: presets skip the country step entirely
  * plan feature gating: markets/timeframes/strategies decide delivery,
    personal preferences "ALL" follow the plan, quota stays atomic
  * personal trade block + auto-execution card on the user's demo account
  * demo engine: user-set balance, risk/lot settings, risk-based sizing,
    half-TP1 secure-profit partial close with breakeven stop
  * tracker: halfway detection logic (data-verified, SL-first rule)
  * channels: Mecca-time scheduler, quiet hours, due logic, add/verify flow
  * admin FSM fixes: demo balance / free quota / expiry hours text handlers
  * Arabic display names: no raw machine codes in cards
"""
import unittest
import asyncio
from unittest.mock import patch

from tests.helpers import MockBot, MockContext, MockQuery, FakeUser
from fysb import db, config, instruments
from fysb.engines import demo as demo_engine
from fysb.engines import market_data as md
from fysb.engines import tracker
from fysb.engines import signal_engine
from fysb.handlers import inputs as inputs_mod
from fysb.handlers import admin_channels
from fysb.services import users as users_service
from fysb.services import subscriptions as subs
from fysb.services import user_trade
from fysb.theme import strategy_name, status_ar, tf_name


USER = 555777
ADMIN = 9001


def dispatcher_fire(data: str, uid: int):
    from fysb.handlers import dispatcher as dmod
    q = MockQuery(data, uid)
    ctx = MockContext()
    upd = type("Update", (), {})()
    upd.callback_query = q
    asyncio.run(dmod.buttons(upd, ctx))
    return q, ctx


def _sig_row(**over):
    base = {
        "id": 1, "symbol": "XAUUSD", "direction": "SELL", "timeframe": "M15",
        "entry": 3536.0, "stop": 3557.0, "tp1": 3494.0, "tp2": 3473.0, "tp3": 3452.0,
        "rr": 2.0, "score": 88, "strategy": "PRICE_ACTION", "reason": "test setup",
        "status": "ACTIVE", "source": "AUTO", "created_at": config.now(),
        "data_source": "LIVE", "data_time": "2026-11-22 01:00:00",
        "pips_result": 0, "hit_tp": 0, "expires_at": "", "half_notified": 0,
    }
    base.update(over)
    return base


# ---------------------------------------------------------------------------
# 1) Hidden provider chain
# ---------------------------------------------------------------------------
class TestProviderChain(unittest.TestCase):
    def test_symbol_mapping(self):
        self.assertEqual(md._norm("XAU/USD"), "XAUUSD")
        self.assertEqual(md._provider_key("XAUUSD"), "PAXGUSDT")
        self.assertEqual(md._provider_key("BTCUSD"), "BTCUSDT")
        self.assertIsNone(md._provider_key("USDJPY"))          # forex -> keyed only
        self.assertEqual(md._provider_key("SOLUSDT"), "SOLUSDT")  # pass-through pair

    def test_intervals(self):
        self.assertEqual(md.parse_interval("M15"), "15min")
        self.assertEqual(md._binance_interval("15min"), "15m")
        self.assertEqual(md._bybit_interval("4h"), "240")

    def test_forex_without_key_is_honest(self):
        # Forex is served by the keyless FX reference; when both it and the
        # keyed provider fail, the bot must refuse with an honest Arabic
        # message — NEVER serve a wrong price. (Simulate total offline.)
        md._fail_streak.clear()
        with patch.object(config, "_MARKET_KEY_RAW", ""), \
             patch.object(md, "_fetch_yahoo_candles", side_effect=RuntimeError("offline")):
            with self.assertRaises(RuntimeError) as ctx:
                md.fetch_candles("GBP/USD", "15min", "", 40, use_cache=False)
            msg = str(ctx.exception)
            self.assertIn("أعد المحاولة", msg)
            for banned in ("TWELVEDATA", "BINANCE", "BYBIT", "YAHOO"):
                self.assertNotIn(banned, msg)

    def test_gold_chain_real(self):
        """Network test (skipped gracefully offline): gold price must be real-ish."""
        try:
            p = md.fetch_price("XAU/USD", "")
        except RuntimeError:
            self.skipTest("network unavailable")
            return
        self.assertTrue(1000 < p["price"] < 10000, "gold price sanity")
        self.assertEqual(p["source"], "LIVE")

    def test_provider_names_never_in_public_maps(self):
        from fysb.ui import cards
        sig = _sig_row()
        text = cards.render_signal_card(sig, user_view=True)
        for banned in ("TWELVEDATA", "BINANCE", "BYBIT", "PAXG"):
            self.assertNotIn(banned, text)


# ---------------------------------------------------------------------------
# 2) Broker connectivity — real authenticated checks, no fake pass
# ---------------------------------------------------------------------------
class TestBrokerChecks(unittest.TestCase):
    def test_missing_key_rejected(self):
        ok, msg = inputs_mod.broker_test_connectivity({"base_url": "https://api.binance.com"}, "BINANCE")
        self.assertFalse(ok)

    def test_missing_secret_rejected(self):
        ok, msg = inputs_mod.broker_test_connectivity({"api_key": "ABCDEF"}, "BINANCE")
        self.assertFalse(ok)
        self.assertIn("Secret", msg)

    def test_fake_binance_keys_rejected_by_server(self):
        # Real signed request with garbage credentials -> rejection from Binance itself.
        # The HTTP status is geography-dependent (401 auth-fail, 451 geo-block, 418 IP-ban,
        # or a plain network failure), so we assert the invariant that matters: fake keys
        # are NEVER accepted, and the message is an honest Arabic rejection/failure wording.
        ok, msg = inputs_mod.broker_test_connectivity(
            {"base_url": "https://api.binance.com", "api_key": "FAKEKEY123456",
             "api_secret": "fakesecret", "account_id": "123"}, "BINANCE")
        self.assertFalse(ok)
        self.assertTrue(bool(msg and msg.strip()))
        for marker in ("مرفوضة", "رفض الخادم", "فشل الاتصال"):
            if marker in msg:
                break
        else:
            self.fail(f"unexpected broker message: {msg}")

    def test_custom_without_url_rejected(self):
        ok, msg = inputs_mod.broker_test_connectivity({"api_key": "x", "account_id": "1"}, "CUSTOM")
        self.assertFalse(ok)
        self.assertIn("عنوان", msg)

    def test_custom_http_rejected(self):
        ok, msg = inputs_mod.broker_test_connectivity(
            {"base_url": "http://broker.example", "api_key": "x", "account_id": "1"}, "CUSTOM")
        self.assertFalse(ok)
        self.assertIn("https", msg)

    def test_custom_garbage_host_rejected(self):
        ok, msg = inputs_mod.broker_test_connectivity(
            {"base_url": "هؤدواي", "api_key": "x", "account_id": "1"}, "CUSTOM")
        self.assertFalse(ok)


# ---------------------------------------------------------------------------
# 3) Payment-method flow — no redundant country question
# ---------------------------------------------------------------------------
class TestPaymentMethodFlow(unittest.TestCase):
    def test_preset_starts_at_account_step(self):
        q, ctx = dispatcher_fire("a:pmitem:WESTERN_UNION", ADMIN)
        d = ctx.user_data.get("pm_draft") or {}
        self.assertEqual(d.get("step"), "account")           # country step GONE
        self.assertEqual(d.get("country"), "عالمي")          # prefilled silently
        self.assertEqual(d.get("title"), "ويسترن يونيون")

    def test_custom_starts_at_name_step(self):
        q, ctx = dispatcher_fire("a:pmitem:CUSTOM", ADMIN)
        d = ctx.user_data.get("pm_draft") or {}
        self.assertEqual(d.get("step"), "name")


# ---------------------------------------------------------------------------
# 4) Plan feature gating in signal delivery
# ---------------------------------------------------------------------------
class TestPlanGating(unittest.TestCase):
    uid = 555800

    def setUp(self):
        db.init_db()
        users_service.ensure_user(FakeUser(self.uid))
        # isolate delivery to ONLY this test user (other suites' users stay silent)
        with db.conn() as c:
            c.execute("UPDATE users SET notifications=1 WHERE user_id=?", (self.uid,))
            c.execute("UPDATE users SET notifications=0 WHERE user_id != ?", (self.uid,))
        db.set_setting("free_daily_signals", "50")
        subs.grant_subscription(self.uid, "FREE", source="TEST")

    def _deliver(self, sig):
        sent = []

        async def send(uid, text):
            sent.append((uid, text))

        stats = asyncio.run(signal_engine.emit_to_users(sig, send))
        return stats, sent

    def test_plan_blocks_other_strategy(self):
        sig = _sig_row(strategy="SILVER_BULLET")  # not in FREE plan
        stats, sent = self._deliver(sig)
        self.assertEqual(stats["skipped_filters"], 1)
        self.assertEqual(stats["sent"], 0)

    def test_plan_blocks_other_timeframe(self):
        sig = _sig_row(timeframe="H4")  # FREE has only M15
        stats, sent = self._deliver(sig)
        self.assertEqual(stats["sent"], 0)

    def test_plan_blocks_other_market(self):
        sig = _sig_row(symbol="ETHUSD")
        stats, sent = self._deliver(sig)
        self.assertEqual(stats["sent"], 0)

    def test_allowed_signal_delivered_with_personal_block(self):
        sig = _sig_row(strategy="PRICE_ACTION")
        stats, sent = self._deliver(sig)
        self.assertEqual(stats["sent"], 1)
        uid, text = sent[0]
        self.assertEqual(uid, self.uid)
        self.assertIn("خطتك الشخصية", text)   # personal risk/profit block
        self.assertIn("حركة السعر", text)      # Arabic strategy name
        self.assertNotIn("PRICE_ACTION", text) # no machine codes

    def test_preference_all_follows_plan(self):
        users_service.set_preference(self.uid, "market", "", {"market"})
        users_service.set_preference(self.uid, "timeframe", "", {"timeframe"})
        sig = _sig_row(strategy="PRICE_ACTION")
        stats, sent = self._deliver(sig)
        self.assertEqual(stats["sent"], 1)

    def test_personal_pref_outside_plan_ignored(self):
        # user preference may only narrow the plan, never widen it
        users_service.set_preference(self.uid, "timeframe", "H4", {"timeframe"})
        sig = _sig_row(strategy="PRICE_ACTION", timeframe="M15")
        stats, sent = self._deliver(sig)
        self.assertEqual(stats["sent"], 0)  # user's TF preference filters it out


# ---------------------------------------------------------------------------
# 5) Demo engine — user-owned settings + half-TP1 partial close
# ---------------------------------------------------------------------------
class TestDemoEngine(unittest.TestCase):
    uid = 555801

    def setUp(self):
        db.init_db()
        users_service.ensure_user(FakeUser(self.uid))
        demo_engine.reset_account(self.uid)

    def test_user_sets_balance_and_settings(self):
        demo_engine.set_balance(self.uid, 5000)
        demo_engine.set_risk_pct(self.uid, 2.0)
        demo_engine.set_lot_mode(self.uid, "fixed", 0.05)
        demo_engine.set_auto_trade(self.uid, True)
        s = demo_engine.demo_settings(self.uid)
        self.assertEqual(s["balance"], 5000)
        self.assertEqual(s["risk_pct"], 2.0)
        self.assertEqual(s["lot_mode"], "fixed")
        self.assertEqual(s["fixed_lot"], 0.05)
        self.assertTrue(s["auto_trade"])

    def test_risk_bounded(self):
        demo_engine.set_risk_pct(self.uid, 99)
        self.assertEqual(demo_engine.demo_settings(self.uid)["risk_pct"], 5.0)

    def test_compute_lots_auto_by_risk(self):
        # XAUUSD: pip 0.1, contract 100. risk 1% of 10000 = 100$.
        # SL distance 21.0 -> lots = 100 / (21*100) = 0.0476 -> 0.05
        demo_engine.set_balance(self.uid, 10000)
        demo_engine.set_risk_pct(self.uid, 1.0)
        lots, risk_money, pct = demo_engine.compute_lots(self.uid, "XAUUSD", 3536.0, 3557.0)
        self.assertEqual(lots, 0.05)
        self.assertAlmostEqual(risk_money, 100.0, delta=0.01)

    def test_compute_lots_fixed(self):
        demo_engine.set_lot_mode(self.uid, "fixed", 0.02)
        lots, risk_money, _p = demo_engine.compute_lots(self.uid, "XAUUSD", 3536.0, 3557.0)
        self.assertEqual(lots, 0.02)
        self.assertAlmostEqual(risk_money, 21.0 * 0.02 * 100, delta=0.5)

    def test_partial_close_books_pnl_and_breakeven(self):
        acc = demo_engine.get_or_create_account(self.uid)
        with db.conn() as c:
            bal_before = float(dict(c.execute("SELECT balance FROM demo_accounts WHERE id=?", (acc["id"],)).fetchone())["balance"])
            cur = c.execute("""INSERT INTO demo_positions(account_id,user_id,symbol,direction,lots,initial_lots,
                               entry,sl,tp,status,signal_id,opened_at)
                               VALUES(?,?,?,?,?,?,?,?,?, 'OPEN', ?, ?)""",
                            (acc["id"], self.uid, "XAUUSD", "BUY", 0.10, 0.10, 3500.0, 3480.0, 3540.0, 1, config.now()))
            pid = cur.lastrowid
        res = demo_engine._partial_close(self.uid, pid, 0.5, 3520.0, "HALF_TP1_SECURE")
        self.assertTrue(res["ok"])
        self.assertAlmostEqual(res["closed_lots"], 0.05)
        # pnl = +20.0 * 0.05 * 100 = +100 (delta, independent of starting balance)
        self.assertAlmostEqual(res["pnl"], 100.0, delta=0.01)
        with db.conn() as c:
            pos = dict(c.execute("SELECT * FROM demo_positions WHERE id=?", (pid,)).fetchone())
            bal_after = float(dict(c.execute("SELECT balance FROM demo_accounts WHERE id=?", (acc["id"],)).fetchone())["balance"])
        self.assertEqual(pos["status"], "OPEN")
        self.assertAlmostEqual(pos["lots"], 0.05)
        self.assertEqual(pos["partials_done"], 1)
        self.assertAlmostEqual(pos["sl"], 3500.0)   # moved to breakeven
        self.assertAlmostEqual(bal_after - bal_before, 100.0, delta=0.01)

    def test_half_tp1_event_via_sync(self):
        acc = demo_engine.get_or_create_account(self.uid)
        sig = _sig_row(id=42, direction="BUY", entry=3500.0, stop=3480.0, tp1=3540.0)
        with db.conn() as c:
            c.execute("""INSERT INTO demo_positions(account_id,user_id,symbol,direction,lots,initial_lots,
                       entry,sl,tp,status,signal_id,opened_at)
                       VALUES(?,?,?,?,?,?,?,?,?, 'OPEN', ?, ?)""",
                      (acc["id"], self.uid, "XAUUSD", "BUY", 0.10, 0.10, 3500.0, 3480.0, 3540.0, 42, config.now()))
        # price at 3520 = exactly half-way to TP1 (3500->3540)
        with patch.object(demo_engine.market_data, "fetch_price", return_value={"price": 3520.0, "source": "LIVE"}), \
             patch.object(demo_engine.market_data, "fetch_candles", side_effect=RuntimeError("no")):
            events = demo_engine.sync_positions_with_real_prices(self.uid, "")
        kinds = [e["kind"] for e in events]
        self.assertIn("HALF_TP1", kinds)
        ev = next(e for e in events if e["kind"] == "HALF_TP1")
        self.assertAlmostEqual(ev["closed_lots"], 0.05)
        self.assertAlmostEqual(ev["pnl"], 100.0, delta=0.01)

    def test_auto_execution_card_contains_user_numbers(self):
        sig = _sig_row()
        exec_res = {
            "ok": True, "position_id": 7, "symbol": "XAUUSD", "lots": 0.05,
            "risk_pct": 1.0, "risk_money": 100.0, "entry_ref": 3536.0,
            "plan": {"tp1": 210.0, "tp2": 315.0, "tp3": 420.0},
        }
        text = user_trade.execution_card(exec_res, sig)
        self.assertIn("نُفّذت الصفقة تلقائياً", text)
        self.assertIn("0.05", text)
        self.assertIn("+210.00", text)

    def test_personal_block_math(self):
        demo_engine.set_balance(self.uid, 10000)
        demo_engine.set_risk_pct(self.uid, 1.0)
        demo_engine.set_lot_mode(self.uid, "auto")
        block = user_trade.personal_block(_sig_row(), self.uid)
        self.assertIn("10000.00", block.replace(",", "").replace("10,000.00", "10000.00"))
        self.assertIn("lot", block)
        self.assertIn("TP1", block)


# ---------------------------------------------------------------------------
# 6) Tracker halfway logic (SL-first rule)
# ---------------------------------------------------------------------------
class TestTrackerHalfway(unittest.TestCase):
    def _sig(self, direction="BUY"):
        return {"id": 9, "symbol": "XAUUSD", "direction": direction, "timeframe": "M15",
                "entry": 100.0, "stop": 98.0 if direction == "BUY" else 102.0,
                "tp1": 104.0 if direction == "BUY" else 96.0,
                "tp2": 106.0, "tp3": 108.0}

    def test_buy_halfway_reached(self):
        candles = [{"high": 102.1, "low": 100.5, "close": 101.9}]
        self.assertTrue(tracker.halfway_reached(self._sig(), candles))

    def test_buy_not_reached(self):
        candles = [{"high": 101.9, "low": 100.2, "close": 101.0}]
        self.assertFalse(tracker.halfway_reached(self._sig(), candles))

    def test_sl_first_no_celebration(self):
        candles = [{"high": 102.2, "low": 97.5, "close": 99.0}]  # SL hit same bar
        self.assertFalse(tracker.halfway_reached(self._sig(), candles))

    def test_sell_halfway_reached(self):
        sig = self._sig("SELL")
        sig["stop"] = 102.0   # SL above entry for a SELL
        sig["tp1"] = 96.0     # dist = 4 -> half = 98
        candles = [{"high": 99.5, "low": 97.9, "close": 98.1}]
        self.assertTrue(tracker.halfway_reached(sig, candles))


# ---------------------------------------------------------------------------
# 7) Channels — Mecca scheduler + quiet hours
# ---------------------------------------------------------------------------
class TestChannels(unittest.TestCase):
    def setUp(self):
        db.init_db()

    def _mk_channel(self, **over):
        cid = admin_channels.add_channel("@testchan", "قناة الاختبار", "channel")
        admin_channels.update_channel(cid, **over)
        return cid

    def test_mecca_offset_fixed(self):
        from datetime import timedelta, timezone
        now = admin_channels.mecca_now()
        self.assertEqual(now.utcoffset(), timedelta(hours=3))

    def test_quiet_hours(self):
        from datetime import datetime, timedelta, timezone
        self.assertFalse(admin_channels.in_quiet_hours(datetime(2026, 11, 22, 9, 0, tzinfo=timezone(timedelta(hours=3)))))
        self.assertTrue(admin_channels.in_quiet_hours(datetime(2026, 11, 22, 3, 0, tzinfo=timezone(timedelta(hours=3)))))

    def test_due_requires_interval_elapsed(self):
        cid = self._mk_channel(enabled=1, auto_analysis=1, interval_minutes=60)
        with patch.object(admin_channels, "in_quiet_hours", return_value=False):
            admin_channels.update_channel(cid, last_sent=admin_channels.mecca_now().isoformat())
            self.assertEqual([c["id"] for c in admin_channels.due_channels() if c["id"] == cid], [])
            old = (admin_channels.mecca_now() - timedelta_wrap(minutes=90)).isoformat()
            admin_channels.update_channel(cid, last_sent=old)
            self.assertIn(cid, [c["id"] for c in admin_channels.due_channels()])

    def test_disabled_never_due(self):
        cid = self._mk_channel(enabled=0)
        admin_channels.update_channel(cid, last_sent="2020-01-01T00:00:00+03:00")
        self.assertNotIn(cid, [c["id"] for c in admin_channels.due_channels()])

    def test_no_signal_is_honest(self):
        """When the engine finds no setup, the channel post says so — no fabrication."""
        cid = self._mk_channel(enabled=1, markets="XAUUSD", timeframes="M1", auto_signals=1)
        with patch("fysb.engines.signal_engine.generate_signal",
                   return_value={"status": "NO_SETUP"}):
            res = asyncio.run(admin_channels.post_signal(cid, MockBot()))
        self.assertFalse(res["ok"])
        self.assertIn("لا توجد إشارة", res["error"])


def timedelta_wrap(**kw):
    from datetime import timedelta
    return timedelta(**kw)


# ---------------------------------------------------------------------------
# 8) Admin FSM fixes (the "استخدم أزرار القائمة" dead-ends)
# ---------------------------------------------------------------------------
class TestAdminTextFSM(unittest.TestCase):
    class FakeMsg:
        def __init__(self, text=""):
            self.text = text
            self.replies = []

        async def reply_text(self, text, **kw):
            self.replies.append(text)
            return True

    def _update(self, text):
        upd = type("Update", (), {})()
        upd.effective_user = FakeUser(ADMIN)
        upd.effective_message = self.FakeMsg(text)
        return upd

    def _run(self, text, state):
        ctx = MockContext()
        ctx.user_data.update(state)
        upd = self._update(text)
        asyncio.run(inputs_mod.on_text(upd, ctx))
        return ctx, upd.effective_message.replies

    def setUp(self):
        db.init_db()   # v6.8.2: per-suite subprocess isolation — the FSM tests need the schema

    def test_demo_balance_setting_works(self):
        ctx, replies = self._run("25000", {"awaiting_demo_balance": True})
        self.assertNotIn("awaiting_demo_balance", ctx.user_data)
        self.assertEqual(db.get_setting_float("demo_start_balance", 0), 25000.0)
        self.assertIn("25,000", replies[-1])

    def test_demo_balance_invalid_reprompts(self):
        ctx, replies = self._run("abc", {"awaiting_demo_balance": True})
        self.assertTrue(ctx.user_data.get("awaiting_demo_balance"))
        self.assertIn("رقم", replies[-1])

    def test_free_quota_setting_works(self):
        ctx, replies = self._run("4", {"awaiting_free_quota": True})
        self.assertEqual(db.get_setting_int("free_daily_signals", 0), 4)

    def test_expiry_hours_setting_works(self):
        ctx, replies = self._run("48", {"awaiting_expiry_hours": True})
        self.assertEqual(db.get_setting_int("signal_expiry_hours", 0), 48)

    def test_user_demo_balance_flow(self):
        uid = 555802
        users_service.ensure_user(FakeUser(uid))
        ctx2 = MockContext()
        ctx2.user_data["awaiting_demo_bal_user"] = True
        upd = type("Update", (), {})()
        upd.effective_user = FakeUser(uid)
        upd.effective_message = self.FakeMsg("7777")
        asyncio.run(inputs_mod.on_text(upd, ctx2))
        self.assertEqual(demo_engine.demo_settings(uid)["balance"], 7777.0)

    def test_broadcast_preview_escapes_html(self):
        ctx, replies = self._run("عرض <b>خاص</b> & <script>", {"awaiting_broadcast_text": True})
        joined = " ".join(replies)
        self.assertIn("معاينة الإعلان", joined)
        self.assertNotIn("<script>", joined)   # escaped, never raw


# ---------------------------------------------------------------------------
# 9) Arabic display names everywhere
# ---------------------------------------------------------------------------
class TestArabicNames(unittest.TestCase):
    def test_strategy_names(self):
        self.assertEqual(strategy_name("PRICE_ACTION"), "حركة السعر")
        self.assertEqual(strategy_name("ICT"), "آي سي تي المؤسسية")
        self.assertEqual(strategy_name("SILVER_BULLET"), "الرصاصة الفضية")

    def test_status_names(self):
        self.assertIn("نشطة", status_ar("ACTIVE"))
        self.assertIn("وقف الخسارة", status_ar("SL_HIT"))
        self.assertEqual(status_ar("CANCELLED"), "ملغاة")

    def test_tf_names(self):
        self.assertEqual(tf_name("M15"), "15 دقيقة")
        self.assertEqual(tf_name("H4"), "4 ساعات")

    def test_signal_card_fully_arabic(self):
        from fysb.ui import cards
        text = cards.render_signal_card(_sig_row(), user_view=True)
        for banned in ("PRICE_ACTION", "TWELVEDATA", " SELL ", "CANCELLED", "EXPIRED"):
            self.assertNotIn(banned, text)
        self.assertIn("حركة السعر", text)
        self.assertIn("15 دقيقة", text)
        self.assertIn("بيع", text)


# ---------------------------------------------------------------------------
# 10) Composite analytics (no tool chooser)
# ---------------------------------------------------------------------------
class TestCompositeAnalytics(unittest.TestCase):
    uid = 555803

    def setUp(self):
        db.init_db()
        users_service.ensure_user(FakeUser(self.uid))

    def test_analytics_module_has_no_tools_constant_use_in_ui(self):
        import inspect
        from fysb.handlers import user_pages
        src = inspect.getsource(user_pages.user_page)
        self.assertNotIn("anltool", src)   # the chooser step is gone

    def test_run_analysis_gates_plan_market(self):
        from fysb.services import analytics as anl
        subs.grant_subscription(self.uid, "FREE", source="TEST")
        res = anl.run_analysis(self.uid, "ETHUSD", "M15", "", "", "ICT")
        self.assertFalse(res["ok"])
        self.assertIn("غير مشمول", res["error"])

    def test_run_analysis_gates_plan_timeframe(self):
        from fysb.services import analytics as anl
        subs.grant_subscription(self.uid, "FREE", source="TEST")
        res = anl.run_analysis(self.uid, "XAUUSD", "H4", "", "", "ICT")
        self.assertFalse(res["ok"])
        self.assertIn("الإطار", res["error"])
