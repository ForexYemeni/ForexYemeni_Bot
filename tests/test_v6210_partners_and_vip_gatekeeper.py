"""v6.21.0 regression tests — Royal Channel Partners (Revenue-Share) + VIP Gatekeeper Engine."""
from __future__ import annotations

import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock

from fysb import config, db
from fysb.handlers import admin_channels, admin_partners
from fysb.services import partners as partners_svc
from fysb.services import referrals, subscriptions


def _mk_q(uid: int):
    replies = []

    async def fake_reply(text=None, reply_markup=None, **kw):
        replies.append((text, reply_markup))
        return SimpleNamespace(chat=SimpleNamespace(id=uid), message_id=77)

    q = SimpleNamespace(
        message=SimpleNamespace(
            chat_id=uid,
            message_id=76,
            reply_text=AsyncMock(side_effect=fake_reply),
            edit_text=AsyncMock(side_effect=fake_reply),
        ),
        from_user=SimpleNamespace(id=uid, full_name="Tester"),
        answer=AsyncMock(),
    )
    return q, replies


def test_partner_revenue_share_commission_and_settlement():
    # Create a partner with 30% commission
    p = partners_svc.create_partner(71001, title="قناة الذهب الملكية", commission_pct=30.0, post_mode="teaser")
    assert p["user_id"] == 71001
    assert p["commission_pct"] == 30.0
    assert p["campaign_code"].startswith("P")

    # Buyer 71002 enters via partner's referral code
    cap = referrals.capture(p["campaign_code"], 71002)
    assert cap.get("captured") is True

    # Self-referral attempt is blocked
    assert partners_svc.credit_commission_on_subscription(71001, "PRO", payment_id=901, amount=100.0) is None

    # Buyer subscribes to PRO ($100) -> partner earns $30.00 automatically
    res = subscriptions.grant_subscription(71002, "PRO", source="PAYMENT", payment_id=902, amount=100.0, currency="USD")
    assert res["ok"] is True
    comm = res["commission"]
    assert comm is not None and comm["credited"] is True
    assert comm["commission_amount"] == 30.0
    assert comm["partner_user_id"] == 71001

    # Duplicate payment_id is idempotently ignored
    assert partners_svc.credit_commission_on_subscription(71002, "PRO", payment_id=902, amount=100.0) is None

    st = partners_svc.partner_stats(p["id"])
    assert st["invited"] == 1
    assert st["converted"] == 1
    assert st["earned_pending"] == 30.0
    assert st["paid_out"] == 0.0

    # Admin settles partner payout -> pending flips to paid_out
    settled = partners_svc.settle_partner(p["id"])
    assert settled["count"] == 1 and settled["total"] == 30.0
    st2 = partners_svc.partner_stats(p["id"])
    assert st2["earned_pending"] == 0.0
    assert st2["paid_out"] == 30.0


def test_partner_channel_teaser_sample_full_and_cta_button():
    p = partners_svc.create_partner(72001, title="شريك القمة", commission_pct=25.0, post_mode="teaser")
    partners_svc.update_partner(p["id"], brand_label="قناة القمة للفوركس", sample_daily_limit=2)
    link_res = partners_svc.link_partner_channel(p["id"], "-1002223334445", "قناة القمة", "channel")
    assert link_res["ok"] is True
    cid = link_res["channel_id"]
    ch = admin_channels.get_channel(cid)

    # Insert a real signal
    with db.conn() as c:
        cur = c.execute(
            """INSERT INTO signals(symbol,timeframe,direction,entry,stop,tp1,tp2,tp3,
               rr,strategy,score,reason,status,source,created_at)
               VALUES('XAUUSD','M15','BUY',2660.0,2650.0,2670.0,2685.0,2700.0,
               2.5,'ict',89,'partner_test','ACTIVE','auto',?)""",
            (config.now(),),
        )
        sid = cur.lastrowid
        sig = dict(c.execute("SELECT * FROM signals WHERE id=?", (sid,)).fetchone())

    # 1. TEASER mode hides exact prices & attaches green CTA button with partner's ref link
    sent = []

    async def fake_send(chat_id, text, **kw):
        sent.append((chat_id, text, kw.get("reply_markup")))
        return SimpleNamespace(chat=SimpleNamespace(id=chat_id), message_id=5501)

    bot = SimpleNamespace(send_message=AsyncMock(side_effect=fake_send))
    asyncio.run(admin_channels.emit_to_channels(sig, bot))

    ch_posts = [(t, mk) for c_id, t, mk in sent if c_id == -1002223334445]
    assert len(ch_posts) == 1
    post_text, post_mk = ch_posts[0]
    assert "مخفية للمشتركين فقط" in post_text
    assert "قناة القمة للفوركس" in post_text
    assert post_mk is not None
    cta_btn = post_mk.inline_keyboard[0][0]
    assert p["campaign_code"] in cta_btn.url
    assert cta_btn.to_dict()["style"] == "success"

    # 2. SAMPLE mode allows 2 full signals/day, then switches 3rd to teaser
    partners_svc.update_partner(p["id"], post_mode="sample")
    admin_channels.update_channel(cid, post_mode="sample")
    ch = admin_channels.get_channel(cid)
    p_fresh = partners_svc.get_partner(p["id"])
    assert partners_svc.should_send_full_signal(ch, p_fresh) is True
    assert partners_svc.should_send_full_signal(ch, p_fresh) is True
    assert partners_svc.should_send_full_signal(ch, p_fresh) is False


def test_vip_gatekeeper_auto_invite_and_auto_kick_on_expiry():
    # Admin creates a VIP Gatekeeper channel
    cid = admin_channels.add_channel("-1003334445556", "قناة VIP الملكية", "channel")
    admin_channels.update_channel(cid, is_vip_gate=1)

    buyer_uid = 73001
    subscriptions.grant_subscription(buyer_uid, "PRO", source="PAYMENT", amount=50.0)

    invites_created = []
    dms = []
    bans = []
    unbans = []

    async def fake_invite(chat_id, member_limit=1, name=""):
        invites_created.append((chat_id, member_limit, name))
        return SimpleNamespace(invite_link="https://t.me/+RoyalSingleUseInvite")

    async def fake_send(chat_id, text, **kw):
        dms.append((chat_id, text, kw.get("reply_markup")))

    async def fake_ban(chat_id, user_id):
        bans.append((chat_id, user_id))

    async def fake_unban(chat_id, user_id, only_if_banned=True):
        unbans.append((chat_id, user_id))

    bot = SimpleNamespace(
        create_chat_invite_link=AsyncMock(side_effect=fake_invite),
        send_message=AsyncMock(side_effect=fake_send),
        ban_chat_member=AsyncMock(side_effect=fake_ban),
        unban_chat_member=AsyncMock(side_effect=fake_unban),
    )

    granted = asyncio.run(partners_svc.grant_vip_access(bot, buyer_uid, "PRO"))
    assert any(g["invite_link"] == "https://t.me/+RoyalSingleUseInvite" for g in granted)
    assert any(c_id == -1003334445556 and lim == 1 for c_id, lim, _ in invites_created)
    assert any(u == buyer_uid and "بطاقة الدخول الحصري لقنوات VIP" in txt for u, txt, _ in dms)

    # While subscription is ACTIVE, sweep MUST NOT kick the subscriber
    sw1 = asyncio.run(partners_svc.sweep_expired_vip_members(bot))
    assert not any(u == buyer_uid for _, u in bans)

    # Now cancel/expire the subscription -> sweep kicks & unbans cleanly + sends renewal DM!
    subscriptions.cancel_subscription(buyer_uid, actor=config.ADMIN_ID)
    sw2 = asyncio.run(partners_svc.sweep_expired_vip_members(bot))
    assert sw2["removed"] >= 1
    assert (-1003334445556, buyer_uid) in bans
    assert (-1003334445556, buyer_uid) in unbans
    assert any(u == buyer_uid and "انتهاء صلاحية اشتراكك" in txt for u, txt, _ in dms)


def test_partner_panel_isolation_and_admin_ui_colors():
    p1 = partners_svc.create_partner(74001, title="شريك A", commission_pct=20.0)
    p2 = partners_svc.create_partner(74002, title="شريك B", commission_pct=25.0)
    ch2 = partners_svc.link_partner_channel(p2["id"], "-1004445556667", "قناة B")["channel_id"]

    bot = SimpleNamespace(send_message=AsyncMock())
    ctx = SimpleNamespace(user_data={}, bot=bot)

    # 1. Non-partner blocked cleanly
    q_np, rep_np = _mk_q(74999)
    asyncio.run(admin_partners.handle_partner_callback(q_np, 74999, ctx, bot, "home"))
    assert "للشركاء المفعّلين فقط" in rep_np[-1][0]

    # 2. Partner A cannot toggle Partner B's channel!
    q_a, rep_a = _mk_q(74001)
    asyncio.run(admin_partners.handle_partner_callback(q_a, 74001, ctx, bot, f"chtoggle:{ch2}"))
    assert "لا تتبع حسابك" in rep_a[-1][0]

    # 3. Admin partner view renders with 100% colored luxury buttons
    q_adm, rep_adm = _mk_q(config.ADMIN_ID)
    asyncio.run(admin_partners.admin_partner_view(q_adm, config.ADMIN_ID, ctx, bot, p1["id"]))
    txt, mk = rep_adm[-1]
    assert "ملف الشريك" in txt
    btns = [b for row in mk.inline_keyboard for b in row]
    assert all(b.to_dict().get("style") in ("primary", "success", "danger") for b in btns)


def test_partner_channel_full_auto_controls_strategies_and_schedule():
    p = partners_svc.create_partner(75001, title="شريك النخبة", commission_pct=30.0)
    cid = partners_svc.link_partner_channel(p["id"], "-1005556667778", "قناة النخبة")["channel_id"]
    bot = SimpleNamespace(send_message=AsyncMock())
    ctx = SimpleNamespace(user_data={}, bot=bot)
    q, rep = _mk_q(75001)

    # Partner channel screen displays auto-analysis, auto-signals, strategies, schedule
    asyncio.run(admin_partners.handle_partner_callback(q, 75001, ctx, bot, f"ch:{cid}"))
    txt, mk = rep[-1]
    assert "النشر التلقائي للتحليلات" in txt
    assert "النشر التلقائي للإشارات" in txt
    assert "الاستراتيجيات" in txt
    assert "الجدولة" in txt
    datas = [b.callback_data for row in mk.inline_keyboard for b in row]
    assert f"p:chmode:{cid}:analysis" in datas
    assert f"p:chmode:{cid}:signals" in datas
    assert f"p:chfeat:{cid}:markets" in datas
    assert f"p:chfeat:{cid}:timeframes" in datas
    assert f"p:chfeat:{cid}:strategies" in datas
    assert f"p:chint:{cid}" in datas
    assert f"p:chall:{cid}" in datas

    # Toggle auto_analysis off -> persisted
    asyncio.run(admin_partners.handle_partner_callback(q, 75001, ctx, bot, f"chmode:{cid}:analysis"))
    assert admin_channels.get_channel(cid)["auto_analysis"] == 0

    # Toggle auto_signals off -> persisted
    asyncio.run(admin_partners.handle_partner_callback(q, 75001, ctx, bot, f"chmode:{cid}:signals"))
    assert admin_channels.get_channel(cid)["auto_signals"] == 0

    # Pick strategy ICT
    asyncio.run(admin_partners.handle_partner_callback(q, 75001, ctx, bot, f"chfeatset:{cid}:strategies:ict"))
    assert admin_channels.get_channel(cid)["strategies"] == "ict"

    # Set schedule interval to 120 minutes
    asyncio.run(admin_partners.handle_partner_callback(q, 75001, ctx, bot, f"chsetint:{cid}:120"))
    assert admin_channels.get_channel(cid)["interval_minutes"] == 120

    # 1-tap enable ALL restores auto_analysis=1, auto_signals=1, markets/tfs/strategies='ALL'
    asyncio.run(admin_partners.handle_partner_callback(q, 75001, ctx, bot, f"chall:{cid}"))
    ch_after = admin_channels.get_channel(cid)
    assert ch_after["auto_analysis"] == 1
    assert ch_after["auto_signals"] == 1
    assert ch_after["markets"] == "ALL"
    assert ch_after["timeframes"] == "ALL"
    assert ch_after["strategies"] == "ALL"

