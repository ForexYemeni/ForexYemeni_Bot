"""UI callback routing tests — every button family answers, renders, guards.

These smoke tests replay real callback payloads through the dispatcher with
mocked Telegram objects, asserting the button-fix behaviors:
  q.answer() first, fresh-message render, admin guard, no dead callbacks.
"""
import unittest
import asyncio

from tests.helpers import MockBot, MockContext, MockQuery, FakeUser
from fysb import db, config
from fysb.handlers import dispatcher
from fysb.services import users as users_service


def run(coro):
    return asyncio.get_event_loop().run_until_complete(coro) if not asyncio.get_event_loop().is_running() \
        else asyncio.new_event_loop().run_until_complete(coro)


def fire(data: str, uid: int, bot: MockBot | None = None) -> tuple[MockQuery, MockContext]:
    q = MockQuery(data, uid)
    ctx = MockContext(bot)
    asyncio.run(dispatcher.buttons(_mk_update(q), ctx))
    return q, ctx


def _mk_update(q):
    upd = type("Update", (), {})()
    upd.callback_query = q
    return upd


USER = 555300
ADMIN = 9001


class TestUserCallbacks(unittest.TestCase):
    def setUp(self):
        db.init_db()
        try:
            users_service.ensure_user(FakeUser(USER))
        except Exception:
            pass

    def test_answered_and_rendered(self):
        q, ctx = fire("u:home", USER)
        self.assertTrue(q._answered, "callback must be answered immediately")
        self.assertEqual(len(q.message.replies), 1)

    def test_all_user_buttons_render_something(self):
        actions = ["home", "profile", "dashboard", "signals", "preferences",
                   "markets", "timeframes", "strategies", "quality", "notifications",
                   "plans", "payments", "demo", "demopick", "demoopen_list", "demohistory",
                   "my_stats", "support", "help", "analytics"]
        for a in actions:
            q, ctx = fire(f"u:{a}", USER)
            self.assertTrue(q._answered, f"u:{a} not answered")
            self.assertTrue(q.message.replies, f"u:{a} rendered nothing")

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
            text = q.message.replies[0]["text"] if q.message.replies else ""
            for bad in bad_fallbacks:
                self.assertNotIn(bad, text, f"menu button {data} hit the fallback — routing broken")

    def test_admin_menu_buttons_route_correctly(self):
        """REGRESSION: admin menu buttons (single prefix) reach real sections."""
        from fysb.ui import keyboards as kbmod
        markup = kbmod.admin_menu()
        datas = [b.callback_data for row in markup.inline_keyboard for b in row]
        for data in datas:
            q, ctx = fire(data, ADMIN)
            text = q.message.replies[0]["text"] if q.message.replies else ""
            self.assertNotIn("غير معروف", text, f"admin menu button {data} is dead")
            self.assertNotIn("غير متاح", text, f"admin menu button {data} unreachable")

    def test_blocked_user_cannot_browse(self):
        users_service.set_blocked(USER, True, 9001)
        q, ctx = fire("u:home", USER)
        self.assertIn("موقوف", q.message.replies[0]["text"])
        users_service.set_blocked(USER, False, 9001)

    def test_user_cannot_reach_admin_pages(self):
        q, ctx = fire("a:stats", USER)
        self.assertIn("غير مصرح", q.message.replies[0]["text"])
        with db.conn() as c:
            r = c.execute("SELECT action FROM audit WHERE actor=? ORDER BY id DESC LIMIT 1", (USER,)).fetchone()
        self.assertTrue(r["action"].startswith("admin_probe"), "admin probes must be audited")


class TestAdminCallbacks(unittest.TestCase):
    def setUp(self):
        db.init_db()

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
            self.assertTrue(q.message.replies, f"a:{a} rendered nothing (dead section)")

    def test_admin_guard_rejects_stranger(self):
        q, ctx = fire("a:stats", USER)
        self.assertIn("غير مصرح", q.message.replies[0]["text"])

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
            self.assertTrue(q.message.replies, f"{data} dead button")
            if expect:
                self.assertIn(expect, q.message.replies[0]["text"], f"{data} unexpected text")

    def test_settings_toggles_persist(self):
        before = db.get_setting("maintenance")
        q, ctx = fire("a:togglemaintenance", ADMIN)
        after = db.get_setting("maintenance")
        self.assertNotEqual(before, after)
        q, ctx = fire("a:togglemaintenance", ADMIN)  # restore
        self.assertEqual(db.get_setting("maintenance"), before)

    def test_live_stays_locked(self):
        q, ctx = fire("a:live", ADMIN)
        self.assertIn("مغلق", q.message.replies[0]["text"])
        self.assertEqual(db.get_setting("live_enabled", "0"), "0")

    def test_mode_c_not_switchable(self):
        q, ctx = fire("a:mode_set:C", ADMIN)
        text = q.message.replies[0]["text"]
        self.assertTrue("غير قابل للتنفيذ" in text or "LIVE" in text)
        self.assertNotEqual(db.get_setting("trading_mode"), "C")

    def test_unknown_callback_handled(self):
        q, ctx = fire("x:weird", USER)
        self.assertTrue(q._answered)
        self.assertTrue(q.message.replies, "unknown callbacks must reply, never die silently")


if __name__ == "__main__":
    unittest.main()
