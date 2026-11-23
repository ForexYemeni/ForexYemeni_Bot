"""UI callback routing tests — every button family answers, renders, guards.

These smoke tests replay real callback payloads through the dispatcher with
REALISTIC mocked Telegram objects (messages carry chat/message_id and support
edit_text/delete — the exact surface production runs on), asserting:
  q.answer() first, the v6.8.4 ROYAL RESTORE contract (a FRESH page under
  every tap — reply_text, exactly like the v6.8.0 build the owner loved),
  admin guard, no dead callbacks, and that no tap can ever end without a
  visibly delivered page.
"""
import unittest
import asyncio

from tests.helpers import (MockBot, MockContext, MockQuery, MockMessage,
                           FakeUser, InaccessibleMock, page_for, page_markup_for)
from fysb import db, config
from fysb.handlers import dispatcher, common
from fysb.services import users as users_service


def run(coro):
    return asyncio.get_event_loop().run_until_complete(coro) if not asyncio.get_event_loop().is_running() \
        else asyncio.new_event_loop().run_until_complete(coro)


def fire_raw(message, data: str, uid: int, bot: MockBot | None = None):
    """Fire a callback attached to an EXPLICIT message object (any kind)."""
    q = MockQuery(data, uid)
    q.message = message
    ctx = MockContext(bot)
    asyncio.run(dispatcher.buttons(_mk_update(q), ctx))
    return q, ctx


def fire(data: str, uid: int, bot: MockBot | None = None) -> tuple[MockQuery, MockContext]:
    return fire_raw(MockMessage(), data, uid, bot)


def _mk_update(q):
    upd = type("Update", (), {})()
    upd.callback_query = q
    return upd


USER = 555300
ADMIN = 9001


class TestUserCallbacks(unittest.TestCase):
    def setUp(self):
        db.init_db()
        common._last_panel.clear()
        try:
            users_service.ensure_user(FakeUser(USER))
        except Exception:
            pass

    def test_answered_and_rendered(self):
        q, ctx = fire("u:home", USER)
        self.assertTrue(q._answered, "callback must be answered immediately")
        self.assertTrue(page_for(q, ctx), "a page must actually appear")

    def test_all_user_buttons_render_something(self):
        actions = ["home", "profile", "dashboard", "signals", "preferences",
                   "markets", "timeframes", "strategies", "quality", "notifications",
                   "plans", "payments", "demo", "demopick", "demoopen_list", "demohistory",
                   "my_stats", "support", "help", "analytics"]
        for a in actions:
            q, ctx = fire(f"u:{a}", USER)
            self.assertTrue(q._answered, f"u:{a} not answered")
            self.assertTrue(page_for(q, ctx), f"u:{a} rendered nothing")

    def test_menu_buttons_route_correctly(self):
        """REGRESSION (double-prefix bug): every callback_data the menus actually
        send must land on its real section — never on the generic fallback."""
        from fysb.ui import keyboards as kbmod
        bad_fallbacks = ("هذا القسم غير متاح", "غير معروف")
        markup = kbmod.user_menu()
        datas = [b.callback_data for row in markup.inline_keyboard for b in row]
        self.assertTrue(datas, "user menu must expose buttons")
        for data in datas:
            q, ctx = fire(data, USER)
            text = page_for(q, ctx)
            for bad in bad_fallbacks:
                self.assertNotIn(bad, text, f"menu button {data} hit the fallback — routing broken")

    def test_admin_menu_buttons_route_correctly(self):
        """REGRESSION: admin menu buttons (single prefix) reach real sections."""
        from fysb.ui import keyboards as kbmod
        markup = kbmod.admin_menu()
        datas = [b.callback_data for row in markup.inline_keyboard for b in row]
        for data in datas:
            q, ctx = fire(data, ADMIN)
            text = page_for(q, ctx)
            self.assertNotIn("غير معروف", text, f"admin menu button {data} is dead")
            self.assertNotIn("غير متاح", text, f"admin menu button {data} unreachable")

    def test_blocked_user_cannot_browse(self):
        users_service.set_blocked(USER, True, 9001)
        q, ctx = fire("u:home", USER)
        self.assertIn("موقوف", page_for(q, ctx))
        users_service.set_blocked(USER, False, 9001)

    def test_user_cannot_reach_admin_pages(self):
        q, ctx = fire("a:stats", USER)
        self.assertIn("غير مصرح", page_for(q, ctx))
        with db.conn() as c:
            r = c.execute("SELECT action FROM audit WHERE actor=? ORDER BY id DESC LIMIT 1", (USER,)).fetchone()
        self.assertTrue(r["action"].startswith("admin_probe"), "admin probes must be audited")


class TestAdminCallbacks(unittest.TestCase):
    def setUp(self):
        db.init_db()
        common._last_panel.clear()

    def test_all_admin_sections_render(self):
        # Every one of the 27 admin sections must render a page (no dead sections)
        actions = ["stats", "statistics", "users", "subscriptions", "plans", "payments",
                   "paymethods", "signals", "engine", "strategies", "modes", "risk",
                   "markets", "timeframes", "brokers", "accounts", "demo", "live",
                   "reports", "notifications", "broadcast", "support", "security",
                   "backup", "settings", "audit", "health", "analytics", "home"]
        for a in actions:
            q, ctx = fire(f"a:{a}", ADMIN)
            self.assertTrue(q._answered, f"a:{a} not answered")
            self.assertTrue(page_for(q, ctx), f"a:{a} rendered nothing (dead section)")

    def test_admin_guard_rejects_stranger(self):
        q, ctx = fire("a:stats", USER)
        self.assertIn("غير مصرح", page_for(q, ctx))

    def test_admin_detail_pages(self):
        pairs = [
            ("a:planedit:FREE", "محرر" if False else "Free"),
            ("a:paymentsstatus:APPROVED", "المدفوعات"),
            ("a:paymentdetail:999999", "غير موجود"),
            ("a:signalview:999999", "غير موجود"),
            ("a:userdetail:999999", "غير موجود"),
            ("a:auditpage:0", "سجل"),
            ("a:repusers", "تقرير"),
            ("a:repsignals", "تقرير"),
            ("a:reppayments", "تقرير"),
            ("a:repsubs", "تقرير"),
            ("a:repengine", "المحرك"),
            ("a:ticketview:999999", "غير موجود"),
            ("a:bcplan", "الباقة"),
            ("a:usersearch", "المعرّف"),
            ("a:pmadd", "وسيلة"),
            ("a:pmcat:yemen", "محافظ يمنية"),
            ("a:pmitem:JAWALI", "جوالي"),
            ("a:brokeradd", "النوع"),
            ("a:market_add", "SYMBOL"),
            ("a:plannew", "CODE"),
            ("a:ticketreply:1", "رد"),
            ("a:freeset", "المجاني"),
            ("a:demobalset", "الرصيد"),
            ("a:expiryset", "صلاحية"),
            ("a:bkrestore", "الاستعادة"),
            ("a:supportconfig", "وسائل"),
            ("a:enginenow", None),
            ("a:tracknow", None),
            ("a:bknow", None),
        ]
        for data, expect in pairs:
            q, ctx = fire(data, ADMIN)
            self.assertTrue(q._answered, f"{data} not answered")
            self.assertTrue(page_for(q, ctx), f"{data} dead button")
            if expect:
                self.assertIn(expect, page_for(q, ctx), f"{data} unexpected text")

    def test_settings_toggles_persist(self):
        before = db.get_setting("maintenance")
        q, ctx = fire("a:togglemaintenance", ADMIN)
        after = db.get_setting("maintenance")
        self.assertNotEqual(before, after)
        q, ctx = fire("a:togglemaintenance", ADMIN)  # restore
        self.assertEqual(db.get_setting("maintenance"), before)

    def test_live_stays_locked(self):
        q, ctx = fire("a:live", ADMIN)
        self.assertIn("مغلق", page_for(q, ctx))
        self.assertEqual(db.get_setting("live_enabled", "0"), "0")

    def test_mode_c_not_switchable(self):
        q, ctx = fire("a:mode_set:C", ADMIN)
        text = page_for(q, ctx)
        self.assertTrue("غير قابل للتنفيذ" in text or "LIVE" in text)
        self.assertNotEqual(db.get_setting("trading_mode"), "C")

    def test_unknown_callback_handled(self):
        q, ctx = fire("x:weird", USER)
        self.assertTrue(q._answered)
        self.assertTrue(page_for(q, ctx), "unknown callbacks must reply, never die silently")


class TestRoyalRestore(unittest.TestCase):
    """v6.8.4 ROYAL RESTORE (owner demand 2026-11-23: «v6.8.0 كانت تعمل فيها
    الأزرار بشكل حقيقي وفاخر جداً — استعادتها 100%») — the render contract is
    EXACTLY v6.8.0 again: EVERY tap on ANY message kind delivers a FRESH page
    (reply under the tapped panel, or bot.send_message for >48h panels).
    Nothing is ever edited in place, nothing is ever deleted, nothing can
    ever silently mutate history — the page ALWAYS visibly appears."""

    def setUp(self):
        db.init_db()
        common._last_panel.clear()

    def test_tap_on_registered_panel_still_delivers_fresh(self):
        # v6.8.0 semantics: even a "registered" panel gets a FRESH reply under
        # the tap — never an in-place edit.
        q = MockQuery("a:signals", ADMIN)
        common.register_panel(777, q.message.message_id)
        ctx = MockContext()
        asyncio.run(dispatcher.buttons(_mk_update(q), ctx))
        self.assertTrue(q._answered)
        self.assertEqual(ctx.bot.sent, [], "render never uses bot.send_message for touchable panels")
        self.assertTrue(q.message.replies, "a fresh page must appear under the tapped panel")
        self.assertFalse(q.message.edits, "v6.8.4 NEVER edits in place — owner demand")
        self.assertFalse(q.message.deleted, "v6.8.4 NEVER deletes the tapped panel")
        self.assertIn("مركز إدارة الإشارات", q.message.replies[-1]["text"])

    def test_tap_on_stale_panel_delivers_fresh(self):
        # THE OWNER'S CONTRACT: a button on ANY panel produces a FRESH page
        # right under the tap — visible, always.
        q, ctx = fire("a:home", ADMIN)
        self.assertTrue(q.message.replies, "stale panel tap must deliver a fresh page")
        self.assertFalse(q.message.edits)
        self.assertFalse(q.message.deleted)
        self.assertIn("SUPER ADMIN", page_for(q, ctx))

    def test_inaccessible_panel_still_renders(self):
        # >48h-old panel: PTB v22 gives InaccessibleMessage (chat + message_id
        # only, no reply_text). Deliver a fresh page via bot.send_message.
        msg = InaccessibleMock(chat_id=777, message_id=42)
        q, ctx = fire_raw(msg, "a:home", ADMIN)
        self.assertTrue(q._answered)
        self.assertTrue(ctx.bot.sent, "even an inaccessible panel must render a fresh page")
        self.assertIn("SUPER ADMIN", page_for(q, ctx))

    def test_first_tap_after_restart_always_delivers(self):
        # Fresh boot, empty registry: every tap delivers — always has, in
        # v6.8.0, because every tap is a fresh message.
        q, ctx = fire("a:accounts", ADMIN)
        self.assertTrue(q.message.replies, "post-restart first tap must deliver a page")

    def test_rapid_navigation_every_tap_delivers(self):
        # Rapid navigation: EVERY tap delivers its own fresh page — no
        # registry dependence, no dead ends, ever.
        for data in ("a:home", "a:signals", "a:accounts", "a:stats"):
            q, ctx = fire(data, ADMIN)
            self.assertTrue(page_for(q, ctx), f"{data} must deliver a page")


if __name__ == "__main__":
    unittest.main()
