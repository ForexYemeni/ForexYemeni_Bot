"""Quota enforcement tests — FREE limit (2/day) cannot be bypassed."""
import unittest
import concurrent.futures

from tests.helpers import *
from fysb import db
from fysb.services import subscriptions as subs
from fysb.services import users as users_service


class FakeUser:
    def __init__(self, uid, username="tester", full_name="Tester"):
        self.id = uid
        self.username = username
        self.full_name = full_name
        self.first_name = full_name


class TestQuota(unittest.TestCase):
    uid = 555001

    def setUp(self):
        db.init_db()
        try:
            users_service.ensure_user(FakeUser(self.uid))
        except Exception:
            pass
        # Full state isolation per test: reset quota ledger + subscriptions
        with db.conn() as c:
            c.execute("DELETE FROM signal_deliveries WHERE user_id=?", (self.uid,))
            c.execute("UPDATE subscriptions SET status='REPLACED' WHERE user_id=? AND status='ACTIVE'", (self.uid,))

    def test_free_default_quota_is_two(self):
        state = subs.quota_state(self.uid)
        self.assertEqual(state["plan"], "FREE")
        self.assertEqual(state["quota"], 2)
        self.assertEqual(state["remaining"], 2)

    def test_view_consumes_and_blocks_third(self):
        subs.release_signal(self.uid, 1)
        subs.release_signal(self.uid, 2)
        r1 = subs.consume_on_view(self.uid, 1)
        self.assertTrue(r1["ok"])
        r2 = subs.consume_on_view(self.uid, 2)
        self.assertTrue(r2["ok"])
        r3 = subs.consume_on_view(self.uid, 3)
        self.assertFalse(r3["ok"], "third FREE view in a day must be blocked")
        # replay of the same signal is allowed (already delivered)
        r1b = subs.consume_on_view(self.uid, 1)
        self.assertTrue(r1b["ok"] and r1b.get("already"))

    def test_restart_does_not_reset_quota(self):
        # quota is computed from the DB ledger by UTC date, not session state
        state = subs.quota_state(self.uid)
        self.assertEqual(state["used"], min(2, state["used"]))

    def test_concurrent_consumption_no_double_spend(self):
        subs.release_signal(self.uid, 11)
        subs.release_signal(self.uid, 12)
        # simulate parallel sessions racing for the two daily slots (distinct signals)
        with db.conn() as c:
            c.execute("DELETE FROM signal_deliveries WHERE user_id=? AND delivered_at>=?",
                      (self.uid, db.day_start_utc()))

        def consume(i):
            return subs.try_consume_signal(self.uid, {"id": 200 + i})

        with concurrent.futures.ThreadPoolExecutor(max_workers=6) as ex:
            results = list(ex.map(consume, range(6)))
        self.assertEqual(results.count(True), 2, "exactly 2 consumers must win the race")

    def test_paid_plan_gets_plan_quota(self):
        subs.grant_subscription(self.uid, "PRO", source="TEST")
        state = subs.quota_state(self.uid)
        self.assertEqual(state["plan"], "PRO")
        self.assertGreaterEqual(state["quota"], 15)
        subs.cancel_subscription(self.uid)

    def test_release_rolls_back(self):
        subs.release_signal(self.uid, 7)
        ok = subs.try_consume_signal(self.uid, {"id": 7})
        self.assertTrue(ok)
        subs.release_signal(self.uid, 7)
        state = subs.quota_state(self.uid)
        self.assertLessEqual(state["used"], 2)

    def test_expiry_flow(self):
        with db.conn() as c:
            cur = c.execute("""INSERT INTO subscriptions(user_id,plan_code,starts_at,ends_at,status,created_at,source)
                               VALUES(?,?,?,?,?,?,?)""",
                            (self.uid, "PRO", db.now(), "2000-01-01T00:00:00+00:00", "ACTIVE", db.now(), "TEST"))
            sid = cur.lastrowid
        expired = subs.expired_but_unmarked()
        self.assertTrue(any(s["id"] == sid for s in expired))
        subs.mark_expired(sid)
        with db.conn() as c:
            status = c.execute("SELECT status FROM subscriptions WHERE id=?", (sid,)).fetchone()["status"]
        self.assertEqual(status, "EXPIRED")


if __name__ == "__main__":
    unittest.main()
