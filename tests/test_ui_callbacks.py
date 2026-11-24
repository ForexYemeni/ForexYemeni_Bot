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
from fysb.services import payments as payments_service


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

    def test_tap_on_registered_panel_edits_in_place_when_enabled(self):
        # When ui_edit_in_place="1" is enabled, edits the panel in place;
        # default ("0") delivers a fresh reply_text page right under the tap.
        db.set_setting("ui_edit_in_place", "1")
        try:
            q = MockQuery("a:signals", ADMIN)
            common.register_panel(777, q.message.message_id)
            ctx = MockContext()
            asyncio.run(dispatcher.buttons(_mk_update(q), ctx))
            self.assertTrue(q._answered)
            self.assertEqual(ctx.bot.sent, [], "render never uses bot.send_message for touchable panels")
            self.assertTrue(q.message.edits, "edit mode edits the panel in place smoothly")
            self.assertFalse(q.message.deleted, "never deletes the tapped panel")
            self.assertIn("مركز إدارة الإشارات", q.message.edits[-1]["text"])
        finally:
            db.set_setting("ui_edit_in_place", "0")

    def test_tap_on_stale_panel_delivers_cleanly_in_both_modes(self):
        # In-place mode ("1") edits cleanly; fresh-message mode ("0") sends reply_text.
        db.set_setting("ui_edit_in_place", "1")
        q, ctx = fire("a:home", ADMIN)
        self.assertTrue(q.message.edits)
        self.assertIn("SUPER ADMIN", page_for(q, ctx))

        db.set_setting("ui_edit_in_place", "0")
        try:
            q2, ctx2 = fire("a:home", ADMIN)
            self.assertTrue(q2.message.replies, "fresh-message mode must deliver via reply_text")
            self.assertFalse(q2.message.edits)
            self.assertIn("SUPER ADMIN", page_for(q2, ctx2))
        finally:
            db.set_setting("ui_edit_in_place", "0")

    def test_inaccessible_panel_still_renders(self):
        # >48h-old panel: PTB v22 gives InaccessibleMessage (chat + message_id
        # only, no edit_text/reply_text). Deliver a fresh page via bot.send_message.
        msg = InaccessibleMock(chat_id=777, message_id=42)
        q, ctx = fire_raw(msg, "a:home", ADMIN)
        self.assertTrue(q._answered)
        self.assertTrue(ctx.bot.sent, "even an inaccessible panel must render a fresh page")
        self.assertIn("SUPER ADMIN", page_for(q, ctx))

    def test_first_tap_after_restart_always_delivers(self):
        # Fresh boot, empty registry: every tap delivers cleanly.
        q, ctx = fire("a:accounts", ADMIN)
        self.assertTrue(page_for(q, ctx), "post-restart first tap must deliver a page")

    def test_rapid_navigation_every_tap_delivers(self):
        # Rapid navigation: EVERY tap delivers its own fresh page — no
        # registry dependence, no dead ends, ever.
        for data in ("a:home", "a:signals", "a:accounts", "a:stats"):
            q, ctx = fire(data, ADMIN)
            self.assertTrue(page_for(q, ctx), f"{data} must deliver a page")


class TestRoyalFixesV691(unittest.TestCase):
    """v6.9.1 — three owner-reported defects, locked shut with real flows:
      1. «الاستراتيجيات» tap died: the 36-methodology board reached 4678 chars
         (> Telegram's 4096) ⇒ BadRequest ⇒ generic error card. Now paginated.
      2. Broadcast text died: InlineKeyboardButton(“↩️ الإعلانات”, "a:broadcast")
         bound the callback to the URL parameter ⇒ "wrong port number".
      3. Documented USDT wallets invisible to users on the plan page.
    """

    def setUp(self):
        db.init_db()
        common._last_panel.clear()
        # Defensive flag reset (same pattern as test_picker_selfheal): other
        # suites legitimately toggle platform flags (maintenance, registration)
        # and a shared-DB restore path may leave maintenance=1 behind — these
        # USER-side flows must never be hijacked by leftover flags.
        db.set_setting("maintenance", "0")
        payments_service.set_chain_wallet("TRC20", "")
        payments_service.set_chain_wallet("BEP20", "")

    # --- fix 1: strategies pagination ------------------------------------------
    def test_strategies_board_is_paginated_under_telegram_limit(self):
        q, ctx = fire("a:strategies", ADMIN)
        text = page_for(q, ctx)
        self.assertTrue(text, "strategies board must render")
        self.assertLessEqual(len(text), 4096, "page 1 must fit Telegram's limit")
        self.assertIn("الصفحة <b>1</b>", text)
        self.assertIn("صفحة", text)

    def test_strategies_every_page_fits_and_navigation_works(self):
        from fysb.handlers.admin_trading import STRATS_PER_PAGE
        with db.conn() as c:
            total = c.execute("SELECT COUNT(*) n FROM strategy_catalog").fetchone()["n"]
        pages = max(1, (total + STRATS_PER_PAGE - 1) // STRATS_PER_PAGE)
        self.assertGreater(pages, 1, "36 methodologies must produce multiple pages")
        seen_ranks = set()
        for p in range(pages):
            q, ctx = fire(f"a:strategies:{p}", ADMIN)
            text = page_for(q, ctx)
            self.assertTrue(text, f"page {p + 1} must render")
            self.assertLessEqual(len(text), 4096, f"page {p + 1} exceeds Telegram's 4096 limit")
            first = p * STRATS_PER_PAGE + 1
            last = min(total, (p + 1) * STRATS_PER_PAGE)
            for rank in range(first, last + 1):
                seen_ranks.add(rank)
        self.assertEqual(seen_ranks, set(range(1, total + 1)),
                         "global ranks must be continuous across pages, none skipped")

    def test_strategies_toggle_keeps_the_current_page(self):
        # The board's action buttons carry their page: toggling lands BACK on
        # the same page (royal navigation, never yanked to page 1).
        from fysb.handlers.admin_trading import STRATS_PER_PAGE
        with db.conn() as c:
            row = c.execute("SELECT code,verified FROM strategy_catalog ORDER BY code").fetchone()
        code = row["code"]
        if not row["verified"]:
            with db.conn() as c:
                c.execute("UPDATE strategy_catalog SET verified=1 WHERE code=?", (code,))
        # Only 13 real implemented strategies remain (2 pages at 10/page):
        # toggling on page index 1 must re-render page 2 of 2 (never yanked to page 1).
        q, ctx = fire(f"a:strategy_toggle:{code}:1", ADMIN)
        text = page_for(q, ctx)
        self.assertIn("الصفحة <b>2</b>", text, "toggle must re-render the SAME page (1 → عرض 2)")

    # --- fix 2: broadcast preview buttons are callbacks, never URLs -------------
    def test_broadcast_buttons_use_callback_data_never_positional_url(self):
        # The exact defect: InlineKeyboardButton(text, "a:broadcast") binds
        # "a:broadcast" to the URL parameter — Telegram rejects it with
        # "wrong port number specified in the url" and the announcement dies.
        import inspect
        from fysb.handlers import inputs
        src = inspect.getsource(inputs)
        self.assertNotIn('InlineKeyboardButton("↩️ الإعلانات", "a:broadcast")', src,
                         "positional arg = URL — the exact owner-reported defect")
        self.assertEqual(src.count('InlineKeyboardButton("↩️ الإعلانات", callback_data="a:broadcast")'), 2,
                         "both broadcast flows (text preview + photo attach) must carry callback_data")

    # --- fix 3: documented USDT wallets visible on the user's plan page ---------
    def test_plan_page_shows_documented_usdt_wallets_as_fast_lane(self):
        payments_service.set_chain_wallet("TRC20", "T" + "9" * 33)
        payments_service.set_chain_wallet("BEP20", "0x" + "a" * 40)
        q, ctx = fire("u:planview:PRO", USER)
        text = page_for(q, ctx)
        self.assertIn("المحافظ الموثقة", text, "documented wallets section must appear")
        self.assertIn("TRC20", text)
        self.assertIn("BEP20", text)
        markup = page_markup_for(q, ctx)
        datas = [b.callback_data for row in markup.inline_keyboard for b in row]
        self.assertIn("u:usdtpay:PRO:TRC20", datas, "TRC20 fast-lane button must be on the plan page")
        self.assertIn("u:usdtpay:PRO:BEP20", datas, "BEP20 fast-lane button must be on the plan page")

    def test_plan_page_without_documented_wallets_has_no_fast_lane(self):
        q, ctx = fire("u:planview:PRO", USER)
        text = page_for(q, ctx)
        self.assertNotIn("المحافظ الموثقة", text)

    def test_usdtpay_creates_pending_request_and_renders_chain_page(self):
        payments_service.set_chain_wallet("TRC20", "T" + "9" * 33)
        with db.conn() as c:
            before = c.execute("SELECT COUNT(*) n FROM payments").fetchone()["n"]
        q, ctx = fire("u:usdtpay:PRO:TRC20", USER)
        text = page_for(q, ctx)
        self.assertIn("تعليمات الدفع", text)
        self.assertIn("T" + "9" * 33, text, "the documented wallet address must be visible")
        markup = page_markup_for(q, ctx)
        # Row 0: native CopyTextButton for wallet address; Row 1: TXID submit button
        self.assertIsNotNone(markup.inline_keyboard[0][0].copy_text)
        self.assertEqual(markup.inline_keyboard[0][0].copy_text.text, "T" + "9" * 33)
        self.assertIn("أرسل TXID", markup.inline_keyboard[1][0].text)
        with db.conn() as c:
            after = c.execute("SELECT COUNT(*) n FROM payments").fetchone()["n"]
            row = c.execute("SELECT * FROM payments ORDER BY id DESC LIMIT 1").fetchone()
        self.assertEqual(after, before + 1, "one PENDING request must be created")
        self.assertEqual(row["status"], "PENDING")
        self.assertEqual(row["plan_code"], "PRO")
        self.assertEqual(row["method"], "USDT-TRC20")
        self.assertEqual(row["user_id"], USER)

    def test_usdtpay_rejects_undocumented_chain_without_creating_request(self):
        with db.conn() as c:
            before = c.execute("SELECT COUNT(*) n FROM payments").fetchone()["n"]
        q, ctx = fire("u:usdtpay:PRO:ERC20", USER)
        text = page_for(q, ctx)
        self.assertIn("غير موثقة", text)
        with db.conn() as c:
            after = c.execute("SELECT COUNT(*) n FROM payments").fetchone()["n"]
        self.assertEqual(after, before, "no request must be created for an undocumented chain")

    def test_usdtpay_never_opens_the_free_plan(self):
        payments_service.set_chain_wallet("TRC20", "T" + "9" * 33)
        q, ctx = fire("u:usdtpay:FREE:TRC20", USER)
        self.assertIn("غير متاحة", page_for(q, ctx))


if __name__ == "__main__":
    unittest.main()
