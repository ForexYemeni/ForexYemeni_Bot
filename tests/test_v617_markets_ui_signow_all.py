"""Tests for v6.17.0:
1. 3-Layer Multi-Select Market Subscriptions (Admin personal filter + User multi-toggle).
2. Luxury User Home Redesign (Row 1 Signal Now + Analysis Now, executive home card).
3. Signal Now Stage 4/4 'ALL' ('جميع المنهجيات') option + watcher expansion.
"""
import asyncio
import os
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from fysb import config, db
from fysb.engines import signal_engine
from fysb.handlers import dispatcher, signal_now
from fysb.services import subscriptions as subs, users as users_service
from fysb.ui import keyboards as kb


class _FakeMsg:
    def __init__(self, chat_id: int):
        self.chat_id = chat_id
        self.message_id = 100
        self.sent = []

    async def reply_text(self, text, **kwargs):
        self.sent.append((text, kwargs))
        return SimpleNamespace(chat_id=self.chat_id, message_id=self.message_id + len(self.sent))

    async def edit_text(self, text, **kwargs):
        self.sent.append((text, kwargs))
        return self


class _FakeQuery:
    def __init__(self, uid: int, data: str):
        self.from_user = SimpleNamespace(id=uid, username=f"u{uid}", full_name=f"User {uid}")
        self.data = data
        self.message = _FakeMsg(uid)
        self.answered = False

    async def answer(self, *a, **k):
        self.answered = True


class _FakeBot:
    def __init__(self):
        self.messages = []

    async def send_message(self, chat_id, text, **kwargs):
        self.messages.append((chat_id, text, kwargs))
        return SimpleNamespace(chat_id=chat_id, message_id=len(self.messages))


def _fire(uid: int, data: str, ctx=None):
    q = _FakeQuery(uid, data)
    bot = _FakeBot()
    if ctx is None:
        ctx = SimpleNamespace(bot=bot, user_data={}, args=[])
    else:
        ctx.bot = bot
    upd = SimpleNamespace(callback_query=q, effective_user=q.from_user)
    asyncio.run(dispatcher.buttons(upd, ctx))
    text = q.message.sent[-1][0] if q.message.sent else ""
    markup = q.message.sent[-1][1].get("reply_markup") if q.message.sent else None
    return text, markup, ctx, bot


class TestV617Upgrade(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self._orig_db = config.DB_FILE
        self._orig_admin = config.ADMIN_ID
        config.DB_FILE = Path(self._tmp.name) / "v617.db"
        config.ADMIN_ID = 9001
        db.init_db()
        users_service.reset_touch_cache()
        # Enable & verify XAUUSD, BTCUSD, ETHUSD, EURUSD + 3 real strategies
        with db.conn() as c:
            for sym in ("XAUUSD", "BTCUSD", "ETHUSD", "EURUSD"):
                c.execute(
                    "UPDATE market_catalog SET enabled=1, verified=1 WHERE symbol=?",
                    (sym,),
                )
            for st in ("SMC", "ICT", "CRT"):
                c.execute(
                    "UPDATE strategy_catalog SET enabled=1, verified=1 WHERE code=?",
                    (st,),
                )
        # Ensure admin + FREE user + VIP user
        for uid, uname in ((9001, "admin"), (2001, "free_u"), (2002, "vip_u")):
            users_service.ensure_user(SimpleNamespace(id=uid, username=uname, full_name=uname))
        subs.grant_subscription(2002, "VIP", "ADMIN", 9001)

    def tearDown(self):
        config.DB_FILE = self._orig_db
        config.ADMIN_ID = self._orig_admin
        self._tmp.cleanup()

    # --- 1. Admin 3-Layer Personal Market Filter ---
    def test_admin_signal_markets_filter_and_isolation(self):
        # Default: accepts all enabled markets
        self.assertTrue(signal_engine.admin_accepts_market("XAUUSD"))
        self.assertTrue(signal_engine.admin_accepts_market("BTCUSD"))

        # Admin picks Gold-only via a:adminmktsonly:XAUUSD
        text, _, _, _ = _fire(9001, "a:adminmktsonly:XAUUSD")
        self.assertIn("XAUUSD", text)
        self.assertTrue(signal_engine.admin_accepts_market("XAUUSD"))
        self.assertFalse(signal_engine.admin_accepts_market("BTCUSD"))

        # BTCUSD MUST remain enabled in market_catalog for VIP users!
        with db.conn() as c:
            row = c.execute("SELECT enabled FROM market_catalog WHERE symbol='BTCUSD'").fetchone()
        self.assertEqual(row["enabled"], 1)

        # Admin resets to ALL
        _fire(9001, "a:adminmktsall")
        self.assertEqual(db.get_setting("admin_signal_markets", ""), "")
        self.assertTrue(signal_engine.admin_accepts_market("BTCUSD"))

        # 1-tap turn off BTCUSD from ALL mode -> keeps XAUUSD/ETHUSD/EURUSD active
        _fire(9001, "a:adminmktstog:BTCUSD")
        self.assertFalse(signal_engine.admin_accepts_market("BTCUSD"))
        self.assertTrue(signal_engine.admin_accepts_market("XAUUSD"))

        # Re-enabling BTCUSD collapses setting back to "" (ALL)
        _fire(9001, "a:adminmktstog:BTCUSD")
        self.assertEqual(db.get_setting("admin_signal_markets", ""), "")

    # --- 2. User Multi-Select Market Subscriptions ---
    def test_user_multi_select_markets_and_plan_gate(self):
        # VIP user starts with "" (all VIP markets active)
        u = users_service.get_user(2002)
        self.assertEqual((u["market"] or ""), "")

        # Tap u:markets -> renders multi-toggle buttons (u:togmarket:<SYM>)
        text, markup, _, _ = _fire(2002, "u:markets")
        self.assertIn("أسواق إشاراتي", text)
        cbs = [b.callback_data for row in markup.inline_keyboard for b in row]
        self.assertIn("u:togmarket:XAUUSD", cbs)
        self.assertIn("u:togmarket:BTCUSD", cbs)

        # VIP user taps XAUUSD to turn OFF Gold while keeping BTCUSD & others
        _fire(2002, "u:togmarket:XAUUSD")
        u_after = users_service.get_user(2002)
        self.assertNotIn("XAUUSD", u_after["market"])
        self.assertIn("BTCUSD", u_after["market"])
        self.assertFalse(signal_engine.passes_personal_filters(u_after, "XAUUSD", "M15", "SMC", 85))
        self.assertTrue(signal_engine.passes_personal_filters(u_after, "BTCUSD", "M15", "SMC", 85))

        # VIP user taps XAUUSD again -> collapses back to "" (all VIP markets)
        _fire(2002, "u:togmarket:XAUUSD")
        self.assertEqual(users_service.get_user(2002)["market"], "")

        # FREE user (XAUUSD only) cannot toggle BTCUSD -> upsell gate blocks it
        text_free, _, _, _ = _fire(2001, "u:togmarket:BTCUSD")
        self.assertIn("غير متاح في باقتك", text_free)
        self.assertEqual(users_service.get_user(2001)["market"], "")

    # --- 3. Luxury User Home Redesign ---
    def test_user_home_luxury_menu_row1_and_banner(self):
        markup = kb.user_menu()
        self.assertLessEqual(len(markup.inline_keyboard), 6)
        row1_cbs = [b.callback_data for b in markup.inline_keyboard[0]]
        self.assertEqual(row1_cbs, ["u:signow", "u:analytics"])
        row1_labels = [b.text for b in markup.inline_keyboard[0]]
        self.assertIn("إشارة الآن", row1_labels[0])
        self.assertIn("تحليل الآن", row1_labels[1])

        # u:home renders executive summary with plan + quota + markets
        text, _, _, _ = _fire(2002, "u:home")
        self.assertIn("باقتك الحالية", text)
        self.assertIn("رصيد الإشارات اليومي", text)
        self.assertIn("أسواق إشاراتك", text)

        # /start for a regular user also renders the luxury executive banner + Row-1 buttons
        from fysb.handlers import common as common_h
        fake_msg = _FakeMsg(2002)
        upd = SimpleNamespace(
            effective_user=SimpleNamespace(id=2002, username="vip_u", full_name="VIP Trader"),
            effective_message=fake_msg,
        )
        ctx = SimpleNamespace(args=[])
        asyncio.run(common_h.start(upd, ctx))
        self.assertTrue(fake_msg.sent)
        start_text, start_kw = fake_msg.sent[-1]
        self.assertIn("باقتك الحالية", start_text)
        self.assertIn("إشارة الآن", start_text)
        start_row1 = [b.callback_data for b in start_kw["reply_markup"].inline_keyboard[0]]
        self.assertEqual(start_row1, ["u:signow", "u:analytics"])

    # --- 4. Signal Now Stage 4/4 "ALL" ('جميع المنهجيات') + Watcher ---
    def test_signal_now_stage4_all_strategies_and_watcher(self):
        ctx = SimpleNamespace(bot=_FakeBot(), user_data={}, args=[])
        _fire(2002, "u:signow", ctx)
        _fire(2002, "u:signowstyle:INTRADAY", ctx)
        _fire(2002, "u:signowsym:XAUUSD", ctx)
        text4, markup4, ctx, _ = _fire(2002, "u:signowtf:M15", ctx)

        self.assertIn("جميع المنهجيات", text4)
        first_btn = markup4.inline_keyboard[0][0]
        self.assertEqual(first_btn.callback_data, "u:signowstrat:ALL")
        self.assertIn("جميع المنهجيات", first_btn.text)

        # Pick ALL -> generate_signal receives full allowed strategies list (not ["ALL"])
        captured = {}

        def _fake_gen(symbol, tf, force, only_strategies, source, req_uid):
            captured["only_strategies"] = list(only_strategies or [])
            captured["symbol"] = symbol
            captured["tf"] = tf
            return {"status": "NO_SETUP"}

        with patch.object(signal_now, "generate_signal", side_effect=_fake_gen):
            text_no, markup_no, ctx, _ = _fire(2002, "u:signowstrat:ALL", ctx)

        self.assertGreater(len(captured["only_strategies"]), 1)
        self.assertNotIn("ALL", captured["only_strategies"])
        self.assertIn("جميع المنهجيات", text_no)
        # Watcher button works immediately with strategy="ALL"
        text_w, _, ctx, _ = _fire(2002, "u:signowwatch:30", ctx)
        self.assertIn("جميع المنهجيات", text_w)
        w = signal_now.active_watcher(2002)
        self.assertIsNotNone(w)
        self.assertEqual(w["strategy"], "ALL")

        # Make watcher due and verify run_due_watchers expands "ALL" -> allowed list
        with db.conn() as c:
            c.execute("UPDATE signal_watchers SET next_run='2000-01-01T00:00:00+00:00' WHERE id=?", (w["id"],))
        watcher_calls = []

        def _fake_watcher_gen(symbol, tf, force, only_strategies, source, req_uid):
            watcher_calls.append(list(only_strategies or []))
            return {"status": "NO_SETUP"}

        fake_app = SimpleNamespace(bot=_FakeBot())
        with patch.object(signal_now, "generate_signal", side_effect=_fake_watcher_gen):
            asyncio.run(signal_now.run_due_watchers(fake_app))

        self.assertEqual(len(watcher_calls), 1)
        self.assertGreater(len(watcher_calls[0]), 1)
        self.assertNotIn("ALL", watcher_calls[0])


if __name__ == "__main__":
    unittest.main()
