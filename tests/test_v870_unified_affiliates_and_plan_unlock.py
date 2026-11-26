"""v8.7.0 — Unified Royal Referral & Commission Ecosystem + Dual Plan-Unlock Tests."""
from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, MagicMock

import pytest

from fysb import config, db
from fysb.handlers import dispatcher
from fysb.services import (
    affiliates,
    partners as partners_svc,
    payments as payments_svc,
    referrals,
    subscriptions as subs,
    true_analysis as ta_svc,
)
from fysb.ui import keyboards as kb


def _seed_user(uid: int, name: str = "Trader"):
    with db.conn() as c:
        c.execute(
            "INSERT OR IGNORE INTO users(user_id, full_name, username, joined_at) VALUES(?,?,?,?)",
            (uid, name, f"user{uid}", config.now()),
        )


def _fresh_db(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "DB_FILE", str(tmp_path / "v870.db"))
    monkeypatch.setattr(config, "ADMIN_ID", 999999)
    db.init_db()


def test_cross_table_first_link_wins_and_unified_counts(tmp_path, monkeypatch):
    _fresh_db(tmp_path, monkeypatch)
    _seed_user(101, "RefA")
    _seed_user(102, "RefB")
    _seed_user(201, "Invitee1")
    _seed_user(202, "Invitee2")
    _seed_user(203, "Invitee3")

    camp101 = referrals.personal_campaign(101)
    camp102 = referrals.personal_campaign(102)

    # 1. Invitee1 enters via legacy personal campaign of 101 -> affiliate bind by 102 MUST fail (first-link-wins!)
    assert referrals.capture(camp101["code"], 201)["captured"] is True
    assert affiliates.bind_referral(102, 201) is False

    # 2. Invitee2 enters via unified ref_102 -> referrals.capture by 101 MUST fail!
    assert affiliates.bind_referral(102, 202) is True
    cap_fail = referrals.capture(camp101["code"], 202)
    assert cap_fail["captured"] is False
    assert cap_fail["reason"] == "already_linked"

    # 3. Invitee3 enters via unified ref_101 too -> total unified count for 101 is 2 (across both tables, no dupes!)
    assert affiliates.bind_referral(101, 203) is True
    st101 = affiliates.affiliate_stats(101)
    assert st101["total_refs"] == 2
    assert st101["referred"] == 2
    assert st101["paid_refs"] == 0

    st102 = affiliates.affiliate_stats(102)
    assert st102["total_refs"] == 1
    assert camp102["code"]


def test_channel_partner_credited_on_academy_and_true_analysis_without_double_credit(tmp_path, monkeypatch):
    _fresh_db(tmp_path, monkeypatch)
    _seed_user(500, "PartnerOwner")
    _seed_user(601, "BuyerAcad")
    _seed_user(602, "BuyerTA")

    p = partners_svc.create_partner(
        user_id=500,
        title="Partner Royal",
        commission_pct=25.0,
    )
    assert p["id"] > 0

    # Bind both buyers to Channel Partner via partner referral code
    assert referrals.capture(p["campaign_code"], 601)["captured"] is True
    assert referrals.capture(p["campaign_code"], 602)["captured"] is True

    # Buyer 601 purchases ACADEMY ($99)
    pid1 = payments_svc.create_request(601, "ACADEMY", 99.0, method="MANUAL")
    res1 = payments_svc.approve_request(pid1, actor=999999)
    assert res1["ok"] is True
    assert res1["commission"] is not None
    assert res1["commission"]["commission_amount"] == pytest.approx(24.75, abs=0.01)
    assert res1["affiliate_commission"] is None  # NO double-credit to affiliate_commissions!

    # Buyer 602 purchases TRUE_ANALYSIS ($49)
    pid2 = payments_svc.create_request(602, "TRUE_ANALYSIS", 49.0, method="MANUAL")
    res2 = payments_svc.approve_request(pid2, actor=999999)
    assert res2["ok"] is True
    assert res2["commission"] is not None
    assert res2["commission"]["commission_amount"] == pytest.approx(12.25, abs=0.01)
    assert res2["affiliate_commission"] is None

    with db.conn() as c:
        aff_cnt = c.execute("SELECT COUNT(*) AS n FROM affiliate_commissions").fetchone()["n"]
        p_cnt = c.execute("SELECT COUNT(*) AS n FROM partner_commissions").fetchone()["n"]
    assert aff_cnt == 0
    assert p_cnt == 2


def test_admin_configurable_commissions_tiered_flat_and_disable(tmp_path, monkeypatch):
    _fresh_db(tmp_path, monkeypatch)
    _seed_user(100, "Referrer")
    _seed_user(201, "U1")
    _seed_user(202, "U2")
    _seed_user(203, "U3")

    # 1. Custom TIERED Bronze rate = 22%
    affiliates.set_config(mode="TIERED", tier_bronze=22.0, min_payout_usd=15.0)
    assert affiliates.bind_referral(100, 201) is True
    c1 = affiliates.record_commission_on_payment(201, 100.0, payment_id=11)
    assert c1 is not None
    assert c1["rate_pct"] == 22.0
    assert c1["commission_usd"] == 22.0

    # 2. Switch to FLAT 18%
    affiliates.set_config(mode="FLAT", flat_pct=18.0)
    assert affiliates.bind_referral(100, 202) is True
    c2 = affiliates.record_commission_on_payment(202, 100.0, payment_id=12)
    assert c2 is not None
    assert c2["tier"]["code"] == "FLAT"
    assert c2["rate_pct"] == 18.0
    assert c2["commission_usd"] == 18.0

    # 3. Disable commissions globally -> no credit on payment
    affiliates.set_config(enabled=False)
    assert affiliates.bind_referral(100, 203) is True
    c3 = affiliates.record_commission_on_payment(203, 100.0, payment_id=13)
    assert c3 is None

    # Re-enable and check editable min_payout_usd
    affiliates.set_config(enabled=True, min_payout_usd=50.0)
    po_fail = affiliates.request_payout(100, "T" + "1" * 33)
    assert po_fail["ok"] is False  # Available is $40 < $50
    affiliates.set_config(min_payout_usd=35.0)
    po_ok = affiliates.request_payout(100, "T" + "1" * 33)
    assert po_ok["ok"] is True
    assert po_ok["amount_usd"] == 40.0


def test_balance_to_plan_instant_unlock_and_no_double_spend(tmp_path, monkeypatch):
    _fresh_db(tmp_path, monkeypatch)
    _seed_user(300, "Earner")
    _seed_user(301, "Buyer1")

    with db.conn() as c:
        c.execute("UPDATE plans SET price=25.0, duration_days=30, enabled=1 WHERE code='PRO'")
        c.execute("UPDATE plans SET price=60.0, duration_days=30, enabled=1 WHERE code='VIP'")

    # Give Earner $30 commission balance (20% of $150)
    affiliates.set_config(mode="FLAT", flat_pct=20.0, milestone_enabled=False)
    assert affiliates.bind_referral(300, 301) is True
    affiliates.record_commission_on_payment(301, 150.0, payment_id=501)
    st0 = affiliates.affiliate_stats(300)
    assert st0["available_usd"] == 30.0

    # Check unlockable list contains PRO, VIP, TRUE_ANALYSIS, ACADEMY
    prods = {p["code"]: p for p in affiliates.list_unlockable_products()}
    assert "PRO" in prods
    assert "VIP" in prods
    assert "TRUE_ANALYSIS" in prods
    assert "ACADEMY" in prods

    # VIP ($60) must fail cleanly (insufficient balance)
    fail_vip = affiliates.redeem_balance_for_plan(300, "VIP")
    assert fail_vip["ok"] is False
    assert "غير كافٍ" in fail_vip["error"]

    # Disable balance unlock -> blocked cleanly
    affiliates.set_config(balance_unlock_enabled=False)
    fail_off = affiliates.redeem_balance_for_plan(300, "PRO")
    assert fail_off["ok"] is False
    affiliates.set_config(balance_unlock_enabled=True)

    # Unlock PRO ($25) immediately -> deducts $25, leaves $5, activates PRO!
    ok_pro = affiliates.redeem_balance_for_plan(300, "PRO")
    assert ok_pro["ok"] is True
    assert ok_pro["price_usd"] == 25.0
    assert ok_pro["remaining_usd"] == 5.0
    assert subs.plan_for(300)["code"] == "PRO"

    st1 = affiliates.affiliate_stats(300)
    assert st1["redeemed_usd"] == 25.0
    assert st1["available_usd"] == 5.0

    # Cannot double-spend remaining $5 on PRO ($25) or payout ($20 min)
    again = affiliates.redeem_balance_for_plan(300, "PRO")
    assert again["ok"] is False
    po = affiliates.request_payout(300, "T" + "9" * 33)
    assert po["ok"] is False


def test_paid_referral_milestone_auto_unlock_strict_and_idempotent(tmp_path, monkeypatch):
    _fresh_db(tmp_path, monkeypatch)
    _seed_user(400, "Ambassador")
    for i in range(1, 8):
        _seed_user(400 + i, f"Friend{i}")
        assert affiliates.bind_referral(400, 400 + i) is True

    # 7 FREE referrals -> ZERO milestones unlocked!
    assert affiliates.evaluate_milestone_unlock(400) is None
    assert subs.plan_for(400)["code"] == "FREE"

    # Configure milestone: every 3 paid referrals -> auto-unlock PRO for 30 days
    affiliates.set_config(
        milestone_enabled=True,
        milestone_paid_refs=3,
        milestone_target="PRO",
        milestone_days=30,
    )

    # 1st & 2nd paid referrals -> still not unlocked
    r1 = affiliates.record_commission_on_payment(401, 50.0, payment_id=901)
    assert r1["milestone_unlocked"] is None
    r2 = affiliates.record_commission_on_payment(402, 50.0, payment_id=902)
    assert r2["milestone_unlocked"] is None
    # Same friend 402 paying a second time does NOT increase unique paid_refs!
    r2b = affiliates.record_commission_on_payment(402, 50.0, payment_id=903)
    assert r2b["milestone_unlocked"] is None
    assert subs.plan_for(400)["code"] == "FREE"

    # 3rd unique paid referral -> AUTO-UNLOCKS PRO 30 DAYS!
    r3 = affiliates.record_commission_on_payment(403, 50.0, payment_id=904)
    ms1 = r3["milestone_unlocked"]
    assert ms1 is not None
    assert ms1["step"] == 1
    assert ms1["paid_refs"] == 3
    assert ms1["target_code"] == "PRO"
    assert subs.plan_for(400)["code"] == "PRO"

    # Strictly idempotent: calling evaluate again at 3 paid_refs returns None!
    assert affiliates.evaluate_milestone_unlock(400) is None

    # Change milestone target to TRUE_ANALYSIS for step 2 (at 6 unique paid referrals)
    affiliates.set_config(milestone_target="TRUE_ANALYSIS", milestone_days=15)
    affiliates.record_commission_on_payment(404, 50.0, payment_id=905)
    affiliates.record_commission_on_payment(405, 50.0, payment_id=906)
    r6 = affiliates.record_commission_on_payment(406, 50.0, payment_id=907)
    ms2 = r6["milestone_unlocked"]
    assert ms2 is not None
    assert ms2["step"] == 2
    assert ms2["target_code"] == "TRUE_ANALYSIS"
    assert ta_svc.has_access(400)["active"] is True


def test_ui_handlers_for_unified_referrals_unlock_and_admin_config(tmp_path, monkeypatch):
    _fresh_db(tmp_path, monkeypatch)
    _seed_user(700, "RoyalUser")
    _seed_user(999999, "SuperAdmin")

    # Verify Admin finance hub button clearly distinguishes User Commissions from Channel Partners
    fin_kb = kb.admin_hub_finance()
    labels = [b.text for row in fin_kb.inline_keyboard for b in row]
    assert any("عمولات المستخدمين" in lbl for lbl in labels)
    assert any("شركاء القنوات" in lbl for lbl in labels)

    def _run_cb(data: str, uid: int):
        q = MagicMock()
        q.data = data
        q.from_user = MagicMock(id=uid, full_name="Tester", username="tester")
        q.answer = AsyncMock()
        q.edit_message_text = AsyncMock()
        q.message = MagicMock(chat_id=uid, message_id=10, text="prev")
        q.message.reply_text = AsyncMock(return_value=MagicMock(message_id=12))
        q.message.delete = AsyncMock()
        ctx = MagicMock()
        ctx.user_data = {}
        ctx.bot = MagicMock()
        ctx.bot.get_me = AsyncMock(return_value=MagicMock(username="ForexYemeniBot"))
        ctx.bot.send_message = AsyncMock(return_value=MagicMock(message_id=11))
        ctx.bot.edit_message_text = AsyncMock()
        upd = MagicMock(callback_query=q, effective_user=q.from_user)
        asyncio.run(dispatcher.buttons(upd, ctx))
        return q, ctx

    def _rendered(q):
        if q.edit_message_text.called:
            call = q.edit_message_text.call_args
        else:
            call = q.message.reply_text.call_args
        txt = call.kwargs.get("text") or (call.args[0] if call.args else "")
        mk = call.kwargs.get("reply_markup")
        return txt, mk

    # 1. User opens u:referrals -> shows unified link + CopyTextButton + u:affunlock
    q_ref, _ = _run_cb("u:referrals", 700)
    ref_text, ref_kb = _rendered(q_ref)
    assert "ref_700" in ref_text
    assert any(b.callback_data == "u:affunlock" for r in ref_kb.inline_keyboard for b in r)
    assert any(b.copy_text is not None for r in ref_kb.inline_keyboard for b in r)

    # 2. User opens u:affunlock -> displays unlockable products
    q_un, _ = _run_cb("u:affunlock", 700)
    un_text, _ = _rendered(q_un)
    assert "فتح باقة فوري" in un_text

    # 3. Admin opens a:affcfg and toggles mode + flat % + milestone refs + target via 1-tap callbacks
    q_cfg, _ = _run_cb("a:affcfg", 999999)
    cfg_text, _ = _rendered(q_cfg)
    assert "ضبط عمولات المستخدمين" in cfg_text

    _run_cb("a:affset:toggle_mode", 999999)
    assert affiliates.get_config()["mode"] == "FLAT"
    _run_cb("a:affset:flat:5", 999999)
    assert affiliates.get_config()["flat_pct"] == 20.0
    _run_cb("a:affset:ms_refs:2", 999999)
    assert affiliates.get_config()["milestone_paid_refs"] == 5
    _run_cb("a:affset:ms_target", 999999)
    assert affiliates.get_config()["milestone_target"] == "VIP"
