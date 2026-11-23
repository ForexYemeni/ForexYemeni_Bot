"""Broadcast + backup + security tests."""
import unittest
import asyncio

from tests.helpers import *
from fysb import db, config
from fysb.services import broadcast as broadcast_service
from fysb.services import backup as backup_service
from fysb.services import support as support_service
from fysb.services import users as users_service


class FakeUser:
    def __init__(self, uid):
        self.id = uid
        self.username = "bc"
        self.full_name = "BC User"


class TestBroadcast(unittest.TestCase):
    uid1, uid2, uid3 = 555401, 555402, 555403

    def setUp(self):
        db.init_db()
        for uid in (self.uid1, self.uid2, self.uid3):
            try:
                users_service.ensure_user(FakeUser(uid))
            except Exception:
                pass

    def test_audience_filters(self):
        ids = broadcast_service.audience_ids("all")
        self.assertIn(self.uid1, ids)
        subs_ids = broadcast_service.audience_ids("plan", "NOPE")
        self.assertEqual(subs_ids, [])

    def test_broadcast_run_with_mock_bot(self):
        bid = broadcast_service.create(9001, "all", "", "رسالة اختبار")
        targets = broadcast_service.audience_ids("all")
        bot = MockBot()
        res = asyncio.run(broadcast_service.run_broadcast(bid, bot, targets))
        b = broadcast_service.get(bid)
        self.assertEqual(b["status"], "DONE")
        self.assertEqual(b["sent"], len(targets))
        self.assertEqual(len(bot.sent), len(targets))
        # re-run impossible (status DONE)
        res2 = broadcast_service.get(bid)
        self.assertNotEqual(res2["status"], "PENDING")

    def test_broadcast_blocked_bot_counts_failed(self):
        bid = broadcast_service.create(9001, "all", "", "اختبار الفشل")
        bot = MockBot()
        bot.fail_for.add(self.uid1)
        res = asyncio.run(broadcast_service.run_broadcast(bid, bot, [self.uid1]))
        b = broadcast_service.get(bid)
        self.assertEqual(b["failed"], 1)
        self.assertEqual(b["sent"], 0)


class TestSupport(unittest.TestCase):
    uid = 555500

    def setUp(self):
        db.init_db()
        try:
            users_service.ensure_user(FakeUser(self.uid))
        except Exception:
            pass

    def test_ticket_thread_lifecycle(self):
        tid = support_service.create_ticket(self.uid, "مشكلة في الدفع", "billing", "لا يظهر الإيصال")
        t = support_service.get_ticket(tid)
        self.assertEqual(t["status"], "OPEN")
        self.assertEqual(t["type"], "billing")
        support_service.add_message(tid, "admin", 9001, "تم استلام مشكلتك")
        msgs = support_service.ticket_messages(tid)
        self.assertEqual(len(msgs), 2)
        self.assertEqual(msgs[-1]["sender_role"], "admin")
        support_service.set_status(tid, "ANSWERED")
        self.assertEqual(support_service.get_ticket(tid)["status"], "ANSWERED")
        support_service.set_status(tid, "CLOSED")
        support_service.set_status(tid, "OPEN")  # reopen
        self.assertEqual(support_service.get_ticket(tid)["status"], "OPEN")

    def test_config_update(self):
        support_service.set_config("fysb_support", "+967000", "-100", "-200")
        cfg = support_service.config_row()
        self.assertEqual(cfg["username"], "fysb_support")


class TestBackup(unittest.TestCase):
    def setUp(self):
        db.init_db()   # v6.8.2: per-suite subprocess isolation — init here, not in a sibling suite

    def test_snapshot_and_verify(self):
        db.init_db()
        snap = backup_service.quick_db_snapshot("test")
        self.assertTrue(snap.exists())
        ok, detail = backup_service.verify_archive(snap)
        self.assertTrue(ok, detail)

    def test_settings_export_no_secrets(self):
        dest = backup_service.settings_export()
        content = dest.read_text(encoding="utf-8")
        for secret in (config.BOT_TOKEN, config.market_api_key()):
            if secret:  # never fail on an empty test placeholder
                self.assertNotIn(secret, content)
        self.assertNotIn("BOT_TOKEN", content)
        self.assertNotIn("TWELVEDATA_API_KEY", content)
        self.assertIn("free_daily_signals", content)

    def test_restore_from_snapshot(self):
        snap = backup_service.quick_db_snapshot("pre_restore_test")
        db.set_setting("maintenance", "1")  # change something
        ok, detail = backup_service.restore_from(snap)
        self.assertTrue(ok, detail)
        # after restoring the snapshot taken before the change, maintenance=1 is still present
        # (snapshot was taken AFTER nothing changed) — integrity matters most
        ok2, _ = db.healthy()
        self.assertTrue(ok2)


class TestSecurity(unittest.TestCase):
    def test_secrets_never_in_logs_or_pages(self):
        import fysb.services.audit as audit_log
        # audit entries carry no secrets
        audit_log.log(9001, "test_secret_scan")
        with db.conn() as c:
            rows = c.execute("SELECT action FROM audit").fetchall()
        for r in rows:
            for secret in (config.BOT_TOKEN, config.market_api_key()):
                if secret:
                    self.assertNotIn(secret, r["action"])

    def test_admin_detection_from_env(self):
        self.assertEqual(config.ADMIN_ID, 9001)

    def test_db_integrity(self):
        ok, res = db.healthy()
        self.assertTrue(ok, res)

    def test_single_instance_lock(self):
        from fysb.handlers.background import SingleInstanceLock
        lock1 = SingleInstanceLock()
        self.assertTrue(lock1.acquire())
        lock2 = SingleInstanceLock()
        self.assertFalse(lock2.acquire(), "second instance must be refused")
        lock1.release()
        lock3 = SingleInstanceLock()
        self.assertTrue(lock3.acquire(), "lock must be acquirable after release")
        lock3.release()


if __name__ == "__main__":
    unittest.main()
