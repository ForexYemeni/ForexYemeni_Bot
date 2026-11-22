"""Payments + subscription lifecycle tests — no double-approval, grant/extend."""
import unittest

from tests.helpers import *
from fysb import db
from fysb.services import payments as payments_service
from fysb.services import subscriptions as subs
from fysb.services import users as users_service


class FakeUser:
    def __init__(self, uid):
        self.id = uid
        self.username = "payuser"
        self.full_name = "Pay User"


class TestPayments(unittest.TestCase):
    uid = 555100

    def setUp(self):
        db.init_db()
        try:
            users_service.ensure_user(FakeUser(self.uid))
        except Exception:
            pass

    def test_payment_method_crud(self):
        mid = payments_service.add_method("USDT TRC20", "حوّل ثم أرسل الإيصال", "TX123456", "اليمن", "PRO,VIP")
        m = payments_service.get_method(mid)
        self.assertEqual(m["title"], "USDT TRC20")
        self.assertEqual(m["linked_plans"], "PRO,VIP")
        self.assertTrue(payments_service.update_method(mid, enabled=False))
        self.assertFalse(payments_service.get_method(mid)["enabled"])
        self.assertTrue(payments_service.delete_method(mid))
        self.assertIsNone(payments_service.get_method(mid))

    def test_request_approve_flow_activates_subscription(self):
        pid = payments_service.create_request(self.uid, "PRO", 19.0, "USDT")
        p = payments_service.get_request(pid)
        self.assertEqual(p["status"], "PENDING")
        self.assertTrue(payments_service.attach_receipt(pid, self.uid, "file123"))
        res = payments_service.approve_request(pid, actor=9001)
        self.assertTrue(res["ok"])
        plan = subs.active_plan(self.uid)
        self.assertIsNotNone(plan, "approval must activate the subscription")
        self.assertEqual(plan["plan_code"], "PRO")
        # double approval must fail
        res2 = payments_service.approve_request(pid, actor=9001)
        self.assertFalse(res2["ok"])
        # re-attachment after review must fail
        self.assertFalse(payments_service.attach_receipt(pid, self.uid, "file999"))

    def test_reject_with_reason(self):
        pid = payments_service.create_request(self.uid, "VIP", 49.0, "USDT")
        res = payments_service.reject_request(pid, "إيصال غير واضح", actor=9001)
        self.assertTrue(res["ok"])
        p = payments_service.get_request(pid)
        self.assertEqual(p["status"], "REJECTED")
        self.assertEqual(p["rejection_reason"], "إيصال غير واضح")
        res2 = payments_service.reject_request(pid, "سبب آخر", actor=9001)
        self.assertFalse(res2["ok"], "double reject must be refused")

    def test_upgrade_replaces_previous_subscription(self):
        subs.grant_subscription(self.uid, "PRO", source="TEST")
        res = subs.grant_subscription(self.uid, "VIP", source="TEST_UPGRADE")
        self.assertTrue(res["ok"])
        plan = subs.active_plan(self.uid)
        self.assertEqual(plan["plan_code"], "VIP")
        with db.conn() as c:
            active_count = c.execute("SELECT COUNT(*) n FROM subscriptions WHERE user_id=? AND status='ACTIVE'",
                                     (self.uid,)).fetchone()["n"]
        self.assertEqual(active_count, 1, "only one ACTIVE subscription per user")
        subs.cancel_subscription(self.uid)


if __name__ == "__main__":
    unittest.main()
