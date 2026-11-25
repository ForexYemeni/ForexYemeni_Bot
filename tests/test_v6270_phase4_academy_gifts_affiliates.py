"""v6.27.0 Phase 4 tests:
  1. Independent Trading Academy (Admin toggle, Free/Paid tracks, 13-strategy curriculum,
     quizzes, progress %, local Pillow Certificate PNG, strict isolation from signal plans).
  2. Gift Subscriptions & Redeem Codes (PRO/VIP/ROYAL/ACADEMY, any custom duration,
     single/multi-use codes, direct UID gift, abuse guards).
  3. Tiered Affiliate / Commission System (15%->20%->25%->35%, automatic payment hook,
     payout requests, Admin 1-tap approve/reject).
"""
import io
import pytest

from fysb import config, db
from fysb.services import academy, affiliates, gifts, payments, subscriptions
from fysb.ui import keyboards as kb


@pytest.fixture
def isolated_db(tmp_path, monkeypatch):
    p = tmp_path / "phase4.db"
    monkeypatch.setattr(config, "DB_FILE", p)
    monkeypatch.setattr(config, "DATABASE_URL", "")
    db.init_db()
    subscriptions._user_plan_cache.clear()
    return tmp_path


def _add_user(uid: int, name: str = "Trader"):
    with db.conn() as c:
        c.execute(
            "INSERT OR IGNORE INTO users(user_id,username,full_name,joined_at,notifications,is_blocked) VALUES(?,?,?,?,1,0)",
            (uid, f"u{uid}", name, config.now()),
        )


def test_version_menus_and_phase4_schema(isolated_db):
    assert tuple(int(x) for x in config.VERSION.split(".")) >= (6, 27, 0)
    assert len(kb.user_menu().inline_keyboard) <= 6
    assert len(kb.admin_menu().inline_keyboard) == 4
    with db.conn() as c:
        tables = {r["name"] for r in c.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()}
    for tbl in (
        "academy_lessons", "academy_enrollments", "academy_progress",
        "gift_codes", "gift_redemptions",
        "affiliate_commissions", "affiliate_payouts",
    ):
        assert tbl in tables


def test_independent_academy_curriculum_isolation_quiz_and_certificate(isolated_db):
    from PIL import Image
    uid_royal = 810001
    uid_acad = 810002
    _add_user(uid_royal, "Royal Trader")
    _add_user(uid_acad, "Academy Student")

    # 1) Curriculum seeds 15 lessons (5 FREE + 10 PAID)
    free_les = academy.list_lessons("FREE")
    paid_les = academy.list_lessons("PAID")
    assert len(free_les) == 5
    assert len(paid_les) == 10

    # 2) STRICT SEPARATION from signals:
    # ROYAL signal plan must NOT unlock Paid Academy
    subscriptions.grant_subscription(uid_royal, "ROYAL", source="TEST", override_days=30)
    assert subscriptions.plan_for(uid_royal)["code"] == "ROYAL"
    assert academy.has_paid_access(uid_royal) is False

    # Paid Academy enrollment must NOT upgrade FREE signal plan
    academy.grant_paid_access(uid_acad, duration_days=365, source="TEST")
    assert academy.has_paid_access(uid_acad) is True
    assert subscriptions.plan_for(uid_acad)["code"] == "FREE"

    # 3) Admin master ON/OFF toggle & pricing
    assert academy.is_enabled() is True
    assert academy.set_enabled(False) is False
    assert academy.is_enabled() is False
    assert academy.set_enabled(True) is True
    p, d = academy.set_pricing(79, 0)
    assert p == 79.0 and d == 0

    # Lesson track & enabled toggle
    nxt_track = academy.toggle_lesson_track("F01_BASICS")
    assert nxt_track == "PAID"
    assert academy.toggle_lesson_track("F01_BASICS") == "FREE"

    # 4) Quiz & progress tracking -> certificate eligibility
    f01 = academy.get_lesson("F01_BASICS")
    wrong_idx = (int(f01["quiz"][0]["ans"]) + 1) % len(f01["quiz"][0]["opts"])
    wrong = academy.submit_quiz_answer(uid_acad, "F01_BASICS", wrong_idx, q_idx=0)
    assert wrong["ok"] is True and wrong["correct"] is False
    assert academy.user_progress(uid_acad)["free_done"] == 0

    for les in free_les:
        for qi, qitem in enumerate(les["quiz"]):
            res = academy.submit_quiz_answer(uid_acad, les["code"], int(qitem["ans"]), q_idx=qi)
            assert res["correct"] is True

    prog = academy.user_progress(uid_acad)
    assert prog["free_done"] == 5 and prog["free_pct"] == 100.0
    assert prog["cert_ready_free"] is True

    # 5) Local Pillow Royal Certificate PNG (0 cost)
    png = academy.render_certificate_png(uid_acad, "Ahmed Al-Yemeni", "FREE")
    assert png is not None and png[:8] == b"\x89PNG\r\n\x1a\n"
    im = Image.open(io.BytesIO(png))
    assert im.size == (1400, 900)
    assert academy.certificate_serial(uid_acad, "FREE").startswith("FY-ACAD-")


def test_gift_codes_and_direct_gifts_any_plan_any_duration(isolated_db):
    u1, u2, u3 = 820001, 820002, 820003
    for u in (u1, u2, u3):
        _add_user(u)

    # Multi-use code for VIP 14 days (custom duration, max 2 uses)
    g_vip = gifts.create_gift_code("VIP", 14, max_uses=2, note="Eid Gift", created_by=1)
    assert g_vip["code"].startswith("FY-")

    r1 = gifts.redeem_code(u1, g_vip["code"])
    assert r1["ok"] is True and r1["target_plan"] == "VIP" and r1["duration_days"] == 14
    assert subscriptions.plan_for(u1)["code"] == "VIP"

    # Same user cannot redeem same code twice
    dup = gifts.redeem_code(u1, g_vip["code"])
    assert dup["ok"] is False

    # Second user succeeds, third user hits max_uses limit
    r2 = gifts.redeem_code(u2, g_vip["code"])
    assert r2["ok"] is True
    r3 = gifts.redeem_code(u3, g_vip["code"])
    assert r3["ok"] is False

    # ACADEMY lifetime gift code (0 days = lifetime)
    g_ac = gifts.create_gift_code("ACADEMY", 0, max_uses=1, created_by=1)
    ra = gifts.redeem_code(u3, g_ac["code"])
    assert ra["ok"] is True and ra["target_plan"] == "ACADEMY"
    assert academy.has_paid_access(u3) is True
    assert subscriptions.plan_for(u3)["code"] == "FREE"  # still independent!

    # Direct UID gift for ROYAL 45 days
    gd = gifts.gift_direct(u3, "ROYAL", 45, granted_by=1, note="VIP Partner")
    assert gd["ok"] is True
    assert subscriptions.plan_for(u3)["code"] == "ROYAL"


def test_tiered_affiliate_commissions_and_admin_payouts(isolated_db):
    # Verify exact 4-tier progression: 15% -> 20% -> 25% -> 35%
    assert affiliates.tier_for_count(0)["rate_pct"] == 15.0
    assert affiliates.tier_for_count(4)["rate_pct"] == 15.0
    assert affiliates.tier_for_count(5)["rate_pct"] == 20.0
    assert affiliates.tier_for_count(15)["rate_pct"] == 25.0
    assert affiliates.tier_for_count(30)["rate_pct"] == 35.0
    assert affiliates.tier_for_count(30)["code"] == "ROYAL"

    ref_uid = 830001
    _add_user(ref_uid, "Top Partner")
    # Self-referral must be rejected
    assert affiliates.bind_referral(ref_uid, ref_uid) is False

    # 5 referred users pay via payments.approve_request -> triggers auto commission!
    for i in range(5):
        buyer = 830100 + i
        _add_user(buyer, f"Buyer {i}")
        assert affiliates.bind_referral(ref_uid, buyer) is True
        pid = payments.create_request(buyer, "ROYAL", 100.0, "USDT")
        res = payments.approve_request(pid, actor=1)
        assert res["ok"] is True
        assert res["affiliate_commission"] is not None
        assert res["affiliate_commission"]["commission_usd"] == 15.0  # 15% on first 5

    # 6th paid referral is now SILVER tier (20%)!
    buyer6 = 830199
    _add_user(buyer6, "Buyer 6")
    affiliates.bind_referral(ref_uid, buyer6)
    pid6 = payments.create_request(buyer6, "ACADEMY", 50.0, "USDT")
    res6 = payments.approve_request(pid6, actor=1)
    assert res6["ok"] is True
    assert academy.has_paid_access(buyer6) is True  # ACADEMY payment unlocked Academy!
    assert res6["affiliate_commission"]["rate_pct"] == 20.0
    assert res6["affiliate_commission"]["commission_usd"] == 10.0

    # Total earned = 5 * $15 + $10 = $85.00
    st = affiliates.affiliate_stats(ref_uid)
    assert st["paid_refs"] == 6
    assert st["tier"]["code"] == "SILVER"
    assert st["total_earned"] == 85.0
    assert st["available_usd"] == 85.0

    # Payout below minimum ($20) rejected
    bad_p = affiliates.request_payout(ref_uid, "TR7NHqjeKQxGTCi8q8ZY4pL8otSzgjLj6t", amount_usd=10.0)
    assert bad_p["ok"] is False

    # Valid payout ($40) -> reduces available to $45 while PENDING
    ok_p = affiliates.request_payout(ref_uid, "TR7NHqjeKQxGTCi8q8ZY4pL8otSzgjLj6t", amount_usd=40.0)
    assert ok_p["ok"] is True
    assert affiliates.affiliate_stats(ref_uid)["available_usd"] == 45.0

    # Admin rejects -> funds restored to $85
    rej = affiliates.reject_payout(ok_p["payout_id"], "Retry wallet")
    assert rej is not None
    assert affiliates.affiliate_stats(ref_uid)["available_usd"] == 85.0

    # Request $50 and Admin approves -> paid_out=$50, available=$35
    ok_p2 = affiliates.request_payout(ref_uid, "TR7NHqjeKQxGTCi8q8ZY4pL8otSzgjLj6t", amount_usd=50.0)
    app = affiliates.approve_payout(ok_p2["payout_id"], "Paid txid123")
    assert app is not None
    st2 = affiliates.affiliate_stats(ref_uid)
    assert st2["paid_out"] == 50.0
    assert st2["available_usd"] == 35.0
    assert "برنامج الشركاء" in affiliates.render_affiliate_dashboard(ref_uid, "ForexYemeniBot")
