"""Royal referral engine — invite → open → subscribe, verified end-to-end.

Covers the owner's exact royal metric: «أدعو 50 شخصاً لفتح باقة يكون منهم
5 على الأقل مشتركين فعلاً» — capture, first-link-wins, no self-referral,
conversion through the real grant_subscription path, idempotency, targets.
"""
import unittest

import tests.helpers  # noqa: F401  (isolates DB env before fysb imports)
from fysb.db import init_db
from fysb.services import referrals as ref
from fysb.services import subscriptions as subs


class TestRoyalReferrals(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        init_db()
        # Deterministic slate for this suite (campaign ROYAL is shared).
        with __import__("fysb.db", fromlist=["conn"]).conn() as c:
            c.execute("DELETE FROM referrals WHERE invitee_id BETWEEN 3000 AND 3999")
            c.execute("DELETE FROM referrals WHERE invitee_id IN (3101,3102,3103,3104,3105,4321,777001)")

    def test_default_royal_campaign_seeded(self):
        camp = ref.get_campaign_by_code("ROYAL")
        self.assertIsNotNone(camp, "default ROYAL campaign must exist on every boot")
        self.assertEqual(camp["target_invited"], 50)
        self.assertEqual(camp["target_converted"], 5)
        self.assertEqual(camp["enabled"], 1)
        link = ref.link_for("ROYAL")
        self.assertTrue(link.startswith("https://t.me/"))
        self.assertIn("?start=refROYAL", link)

    def test_capture_first_link_wins_and_unknown_codes_dead(self):
        self.assertTrue(ref.capture("ROYAL", 3001)["captured"])
        other = ref.ensure_campaign("SECONDX", "second", owner_id=0)
        # first link wins — a second campaign can never steal the invitee
        self.assertFalse(ref.capture("SECONDX", 3001)["captured"])
        # unknown codes never capture
        self.assertFalse(ref.capture("GHOST", 3002)["captured"])
        # disabled campaigns are dead links
        ref.set_enabled(other["id"], 0)
        self.assertFalse(ref.capture("SECONDX", 3003)["captured"])
        ref.set_enabled(other["id"], 1)

    def test_no_self_referral(self):
        ref.ensure_campaign("SELFY", "self", owner_id=4321)
        self.assertFalse(ref.capture("SELFY", 4321)["captured"])

    def test_conversion_through_real_grant_subscription(self):
        for u in [3101, 3102, 3103, 3104, 3105]:
            self.assertTrue(ref.capture("ROYAL", u)["captured"])
        # the conversion path is the REAL one used by payment approval
        for u in [3101, 3102, 3103]:
            res = subs.grant_subscription(u, "PRO", source="PAYMENT")
            self.assertTrue(res["ok"])
        camp = ref.get_campaign_by_code("ROYAL")
        st = ref.campaign_stats(camp["id"])
        self.assertEqual(st["converted"], 3, "grant_subscription must mark referrals converted")
        self.assertGreater(st["rate"], 0)
        self.assertLessEqual(st["rate"], 100)
        # idempotent: re-grant never double-counts
        subs.grant_subscription(3101, "VIP", source="PAYMENT")
        st2 = ref.campaign_stats(camp["id"])
        self.assertEqual(st2["converted"], 3)

    def test_royal_target_met_at_five_subscribers(self):
        camp = ref.get_campaign_by_code("ROYAL")
        for u in [3104, 3105]:
            subs.grant_subscription(u, "PRO", source="PAYMENT")
        st = ref.campaign_stats(camp["id"])
        self.assertEqual(st["converted"], 5)
        self.assertTrue(st["converted_met"], "the royal 5-subscriber target must flip")
        self.assertEqual(st["converted_progress"], 100.0)

    def test_invitee_ledger_and_overall_stats(self):
        # Self-contained (unittest runs methods alphabetically — never rely
        # on another method's conversions): a dedicated ledger campaign.
        ref.ensure_campaign("LEDGER1", "ledger", owner_id=0)
        for u in [3201, 3202, 3203]:
            self.assertTrue(ref.capture("LEDGER1", u)["captured"])
        subs.grant_subscription(3202, "PRO", source="PAYMENT")
        camp = ref.get_campaign_by_code("LEDGER1")
        rows = ref.invitees(camp["id"], limit=20)
        self.assertEqual(len(rows), 3)
        conv = [r for r in rows if r["converted"]]
        self.assertEqual(len(conv), 1)
        self.assertTrue(all(r["plan_code"] for r in conv))
        ov = ref.overall_stats()
        self.assertGreaterEqual(ov["converted"], 1)
        self.assertGreaterEqual(ov["invited"], ov["converted"])

    def test_personal_user_campaigns(self):
        st = ref.user_stats(777001)
        self.assertEqual(st["code"], "U777001")
        self.assertIn("start=refU777001", st["link"])
        self.assertEqual(st["target_invited"], 50)
        self.assertEqual(st["target_converted"], 5)


if __name__ == "__main__":
    unittest.main()
