"""Channel publish reliability — the multi-market «فشل النشر» fix, proven.

The live bug (2026-11-22): a channel linked to 2+ markets failed with
«⛔ فشل النشر — تأكد أن البوت مشرف بصلاحية النشر» even though the bot WAS a
posting admin. Root cause: one merged message hard-cut at 3900 chars sliced
HTML mid-tag → Telegram «Can't parse entities» → catch-all blamed
permissions. These tests pin the rebuilt behavior: per-market royal posts,
guarded truncation, precise diagnostics, 3-level send fallback.
"""
import asyncio
import unittest
from unittest.mock import patch

import tests.helpers  # noqa: F401  (isolates DB env before fysb imports)
from tests.helpers import MockBot

from fysb.db import init_db
from fysb.ui.htmlguard import plain_fallback, truncate_safe, validate_html


class TestHtmlGuard(unittest.TestCase):
    def test_truncate_never_splits_a_tag(self):
        text = "👑 <b>FOREXYEMENI</b>\n" + "<blockquote>▎تحليل ملكي كامل للسوق</blockquote>\n" * 120
        cut = truncate_safe(text, 3900)
        ok, why = validate_html(cut)
        self.assertTrue(ok, f"truncation produced invalid HTML: {why}")
        self.assertFalse(cut.rstrip().endswith("<"))
        self.assertTrue("&" not in cut or cut.rstrip().endswith(";"))

    def test_truncate_closes_open_tags(self):
        text = "x" * 3880 + "<blockquote><b>النص الملكي</b>"
        cut = truncate_safe(text, 3900)
        ok, why = validate_html(cut)
        self.assertTrue(ok, f"open tags were not closed: {why}")

    def test_truncate_noop_under_limit(self):
        short = "<b>سلامة كاملة</b>"
        self.assertEqual(truncate_safe(short, 3900), short)

    def test_validate_rejects_corruption_shapes(self):
        self.assertFalse(validate_html("<b>open")[0])                      # unclosed tag
        self.assertFalse(validate_html("<blockquote></i></blockquote>")[0])  # wrong close
        self.assertFalse(validate_html("bad & entity here")[0])            # dangling entity
        self.assertTrue(validate_html("<blockquote expandable>x</blockquote>")[0])

    def test_plain_fallback_is_parseable(self):
        dirty = "<b>الذهب</b> <blockquote>تحليل &amp; إشارات</blockquote>"
        out = plain_fallback(dirty)
        self.assertNotIn("<", out)
        self.assertNotIn("&amp;", out)
        self.assertIn("الذهب", out)


class TestGuardedSend(unittest.TestCase):
    def test_diagnose_error_maps_each_failure_shape(self):
        from fysb.handlers.admin_channels import _diagnose_send_error as dx
        self.assertIn("طُرد", dx(RuntimeError("Forbidden: bot was kicked from the supergroup")))
        self.assertIn("غير موجودة", dx(RuntimeError("BadRequest: chat not found")))
        self.assertIn("تنسيق", dx(RuntimeError("BadRequest: Can't parse entities: Unsupported start tag")))
        self.assertIn("بدون صلاحية", dx(RuntimeError("BadRequest: not enough rights to send messages to the chat")))
        self.assertIn("أطول من حد", dx(RuntimeError("BadRequest: message is too long")))
        self.assertIn("تأكد أن البوت مشرفاً", dx(RuntimeError("SomethingWeird")))

    def test_send_html_falls_back_to_plain_on_parse_error(self):
        from fysb.handlers.admin_channels import _send_html

        class ParseErrorBot(MockBot):
            """Fails HTML + repaired like Telegram («Can't parse entities») → plain must win."""

            def __init__(self):
                super().__init__()
                self.parse_failures = 0

            async def send_message(self, chat_id, text, **kwargs):
                if kwargs.get("parse_mode") == "HTML" and self.parse_failures < 2:
                    self.parse_failures += 1
                    raise RuntimeError("BadRequest: Can't parse entities: Unclosed tag 'b' at byte offset 3888")
                self.sent.append({"chat_id": chat_id, "text": text, **kwargs})

        bot = ParseErrorBot()
        res = asyncio.run(_send_html(bot, -100123, "<b>تحليل ملكي</b> 🔥" * 10))
        self.assertTrue(res["ok"])
        self.assertEqual(res["mode"], "plain")
        self.assertEqual(len(bot.sent), 1)

    def test_send_html_first_try_is_untouched_html(self):
        from fysb.handlers.admin_channels import _send_html
        bot = MockBot()
        res = asyncio.run(_send_html(bot, -100123, "<b>تحليل ملكي</b>"))
        self.assertTrue(res["ok"])
        self.assertEqual(res["mode"], "html")
        self.assertEqual(bot.sent[0]["text"], "<b>تحليل ملكي</b>")


class TestPermissionProbe(unittest.TestCase):
    def test_rejects_non_admin(self):
        from fysb.handlers.admin_channels import _verify_posting_rights

        class MemberBot(MockBot):
            async def get_me(self):
                return type("Me", (), {"id": 42})()

            async def get_chat_member(self, chat, uid):
                return type("M", (), {"status": "member"})()  # NOT an admin

        ch = {"chat_id": "-100123", "kind": "channel"}
        err = asyncio.run(_verify_posting_rights(MemberBot(), ch))
        self.assertTrue(err)
        self.assertIn("ليس مشرفاً", err)

    def test_rejects_admin_without_post_right(self):
        from fysb.handlers.admin_channels import _verify_posting_rights

        class AdminNoPostBot(MockBot):
            async def get_me(self):
                return type("Me", (), {"id": 42})()

            async def get_chat_member(self, chat, uid):
                return type("M", (), {"status": "administrator", "can_post_messages": False})()

        ch = {"chat_id": "-100123", "kind": "channel"}
        err = asyncio.run(_verify_posting_rights(AdminNoPostBot(), ch))
        self.assertTrue(err)
        self.assertIn("نشر الرسائل", err)

    def test_passes_real_admin(self):
        from fysb.handlers.admin_channels import _verify_posting_rights

        class AdminOkBot(MockBot):
            async def get_me(self):
                return type("Me", (), {"id": 42})()

            async def get_chat_member(self, chat, uid):
                return type("M", (), {"status": "administrator", "can_post_messages": True})()

        ch = {"chat_id": "-100123", "kind": "channel"}
        self.assertIsNone(asyncio.run(_verify_posting_rights(AdminOkBot(), ch)))


class TestMultiMarketPublish(unittest.TestCase):
    """The exact live-reported scenario: a channel linked to MULTIPLE markets."""

    @classmethod
    def setUpClass(cls):
        init_db()

    def _fake_analysis(self, sym, tf):
        return {"ok": True, "analysis": {
            "symbol": sym, "timeframe": tf, "price": 3537.5, "direction": "BUY",
            "quality": 88, "summary": "تحليل ملكي كامل", "detail": "تفاصيل المؤسسات",
            "tools": [], "created_at": "2026-11-22T00:00:00"}}

    class AllGoodBot(MockBot):
        async def get_me(self):
            return type("Me", (), {"id": 42})()

        async def get_chat_member(self, chat, uid):
            return type("M", (), {"status": "administrator", "can_post_messages": True})()

    def test_posts_one_royal_message_per_market(self):
        from fysb.handlers import admin_channels as ac

        cid = ac.add_channel("-100555", "قناة الملوك", "channel")
        ac.update_channel(cid, markets="XAUUSD,EURUSD,GBPUSD", auto_analysis=1)
        with patch.object(ac, "run_analysis", side_effect=lambda _u, sym, tf, *a, **k: self._fake_analysis(sym, tf)):
            bot = self.AllGoodBot()
            res = asyncio.run(ac.post_analysis(cid, bot))
        self.assertTrue(res["ok"], res)
        self.assertEqual(sorted(res["posted"]), ["EURUSD", "GBPUSD", "XAUUSD"])
        # THREE separate royal messages — never a merged truncated one
        self.assertEqual(len(bot.sent), 3)
        for s in bot.sent:
            self.assertEqual(s["chat_id"], -100555)
            ok, why = validate_html(s["text"])
            self.assertTrue(ok, f"channel post is not valid HTML: {why}")
            self.assertLessEqual(len(s["text"]), 4096)
            self.assertIn("FOREXYEMENI", s["text"])
            self.assertIn("@ForexYemeni1_BOT", s["text"])
        texts = " ".join(s["text"] for s in bot.sent)
        for sym in ("XAUUSD", "EURUSD", "GBPUSD"):
            self.assertIn(sym, texts)
        self.assertTrue(ac.get_channel(cid)["last_sent"])

    def test_reports_partial_failures_precisely(self):
        from fysb.handlers import admin_channels as ac

        cid = ac.add_channel("-100556", "قناة جزئية", "channel")
        ac.update_channel(cid, markets="XAUUSD,BADUSD")
        calls = {"n": 0}

        class HalfBrokenBot(MockBot):
            async def get_me(self):
                return type("Me", (), {"id": 42})()

            async def get_chat_member(self, chat, uid):
                return type("M", (), {"status": "administrator", "can_post_messages": True})()

            async def send_message(self, chat_id, text, **kwargs):
                calls["n"] += 1
                if calls["n"] == 2:  # the SECOND market's send fails at Telegram
                    raise RuntimeError("BadRequest: chat not found")
                self.sent.append({"chat_id": chat_id, "text": text, **kwargs})

        with patch.object(ac, "run_analysis", side_effect=lambda _u, sym, tf, *a, **k: self._fake_analysis(sym, tf)):
            res = asyncio.run(ac.post_analysis(cid, HalfBrokenBot()))
        self.assertTrue(res["ok"])
        self.assertEqual(res["posted"], ["XAUUSD"])
        self.assertTrue(res["partial"])
        self.assertIn("BADUSD", res["partial"][0])
        self.assertIn("غير موجودة", res["partial"][0],
                      "the failure reason must be PRECISE, not a permissions blame")

    def test_publish_denied_reports_exact_rights_problem(self):
        from fysb.handlers import admin_channels as ac

        cid = ac.add_channel("-100557", "قناة محظورة", "channel")

        class KickedBot(MockBot):
            async def get_me(self):
                return type("Me", (), {"id": 42})()

            async def get_chat_member(self, chat, uid):
                raise RuntimeError("Forbidden: bot was kicked from the channel")

        res = asyncio.run(ac.post_analysis(cid, KickedBot()))
        self.assertFalse(res["ok"])
        self.assertIn("طُرد", res["error"], f"expected the kicked-bot diagnosis, got: {res['error']}")


if __name__ == "__main__":
    unittest.main()
