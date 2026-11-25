"""Payments + subscription lifecycle tests — no double-approval, grant/extend."""
import unittest
from unittest.mock import patch

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


class TestChainVerify(unittest.TestCase):
    """On-chain USDT TRC20 verification — the v6.8.0 instant-activation path."""

    uid = 555101
    WALLET = "TQ5NrRxcNjMhRmYAqKcnBpbsixT9pW8sRW"   # admin-configured destination (test shape)
    CANONICAL_USDT = "TR7NHqjeKQxGTCi8q8ZY4pL8otSzgjLj6t"

    def setUp(self):
        db.init_db()
        try:
            users_service.ensure_user(FakeUser(self.uid))
        except Exception:
            pass

    def test_contract_address_is_the_canonical_usdt(self):
        """REGRESSION (live bug): a mistyped contract address that does not even
        exist on Tron would reject EVERY genuine USDT payment. The address is
        pinned to the exact canonical Tether contract, live-verified on
        TronGrid /v1/contracts/<addr>/transactions (2026-11-23)."""
        from fysb.services import chain_verify
        self.assertEqual(chain_verify.USDT_TRC20_CONTRACT, self.CANONICAL_USDT)
        self.assertTrue(chain_verify.USDT_TRC20_CONTRACT.startswith("TR7NH"))
        self.assertEqual(len(chain_verify.USDT_TRC20_CONTRACT), 34)

    def test_txid_shape_gate(self):
        from fysb.services import chain_verify
        good = "f0c6237e0cb94d59b122ff843495f1a0a045c46dd2d0d611e6da3ae8b8a3f4ef"
        self.assertTrue(chain_verify.valid_txid(good))
        self.assertFalse(chain_verify.valid_txid("short"))
        self.assertFalse(chain_verify.valid_txid(good.replace("f", "g", 1)))
        self.assertFalse(chain_verify.valid_txid(""))

    def test_human_amount_decimal_scaling(self):
        from fysb.services import chain_verify
        self.assertAlmostEqual(chain_verify._human_amount(19_500_000), 19.5)
        # Strict scaling: raw units < 1_000_000 MUST STILL divide by 10**6
        # (prevents $0.01 = 10,000 raw units from being read as $10,000 USDT!).
        self.assertAlmostEqual(chain_verify._human_amount(10_000), 0.01)
        self.assertAlmostEqual(chain_verify._human_amount(19 * 10**18, decimals=18), 19.0)

    def _mock_events(self, monkey_amount=19_500_000, dest=WALLET, contract=CANONICAL_USDT,
                     confirmed=True):
        """Shape mirrors the REAL TronGrid /v1/transactions/<txid>/events reply:
        an event row with contract_address + result{from,to,value}."""
        return {"data": [{"event_name": "Transfer", "block_timestamp": 1795434288000,
                          "contract_address": contract,
                          "result": {"from": "TFROM000000000000000000000000000", "to": dest,
                                     "value": str(monkey_amount)}}],
                "success": True}

    def test_verify_ok_on_real_shape(self):
        import httpx as _httpx
        from fysb.services import chain_verify
        js = self._mock_events()
        with patch.object(_httpx, "get", return_value=type("R", (), {
                "status_code": 200, "raise_for_status": lambda s: None, "json": lambda s: js})()):
            res = chain_verify.verify_trc20_usdt(
                "f0c6237e0cb94d59b122ff843495f1a0a045c46dd2d0d611e6da3ae8b8a3f4ef",
                self.WALLET, 19.0)
        self.assertTrue(res["ok"], res)
        self.assertAlmostEqual(res["amount"], 19.5)

    def test_verify_rejects_wrong_destination(self):
        import httpx as _httpx
        from fysb.services import chain_verify
        js = self._mock_events(dest="TPAYMENTTOAWRONGADDRESS000000000000")
        with patch.object(_httpx, "get", return_value=type("R", (), {
                "status_code": 200, "raise_for_status": lambda s: None, "json": lambda s: js})()):
            res = chain_verify.verify_trc20_usdt(
                "f0c6237e0cb94d59b122ff843495f1a0a045c46dd2d0d611e6da3ae8b8a3f4ef",
                self.WALLET, 19.0)
        self.assertFalse(res["ok"])
        self.assertIn("تعذر التحقق", res["reason"])

    def test_verify_rejects_low_amount(self):
        import httpx as _httpx
        from fysb.services import chain_verify
        js = self._mock_events(monkey_amount=9_000_000)
        with patch.object(_httpx, "get", return_value=type("R", (), {
                "status_code": 200, "raise_for_status": lambda s: None, "json": lambda s: js})()):
            res = chain_verify.verify_trc20_usdt(
                "f0c6237e0cb94d59b122ff843495f1a0a045c46dd2d0d611e6da3ae8b8a3f4ef",
                self.WALLET, 19.0)
        self.assertFalse(res["ok"])
        self.assertIn("أقل من المطلوب", res["reason"])

    def test_verify_rejects_unconfirmed(self):
        import httpx as _httpx
        from fysb.services import chain_verify
        js = {"confirmed": False, "data": []}
        with patch.object(_httpx, "get", return_value=type("R", (), {
                "status_code": 200, "raise_for_status": lambda s: None, "json": lambda s: js})()):
            res = chain_verify.verify_trc20_usdt(
                "f0c6237e0cb94d59b122ff843495f1a0a045c46dd2d0d611e6da3ae8b8a3f4ef",
                self.WALLET, 19.0)
        self.assertFalse(res["ok"])
        self.assertIn("لم تُؤكد", res["reason"])

    def test_txid_bound_once_and_blocked_forever(self):
        pid = payments_service.create_request(self.uid, "PRO", 19.0, "USDT")
        tx = "f0c6237e0cb94d59b122ff843495f1a0a045c46dd2d0d611e6da3ae8b8a3f4ef"
        bound = payments_service.attach_txid(pid, self.uid, tx, "TRC20")
        self.assertTrue(bound["ok"])
        pid2 = payments_service.create_request(self.uid, "VIP", 49.0, "USDT")
        bound2 = payments_service.attach_txid(pid2, self.uid, tx, "TRC20")
        self.assertFalse(bound2["ok"], "the SAME txid can never unlock a second plan")
        self.assertEqual(payments_service.txid_used(tx), pid)


if __name__ == "__main__":
    unittest.main()
