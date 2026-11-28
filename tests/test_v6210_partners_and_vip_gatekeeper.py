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


def test_strict_partner_only_mode_locks_user_account_until_disabled_or_deleted():
    """v6.21.2: Active partner's regular user account is completely closed
    (no u:* menus, /start opens Partner Panel only, stale u:* callbacks redirect
    to Partner Panel, private signal broadcasts skip them) until Admin disables
    or deletes the partner, at which point they revert back to a regular User."""
    from fysb.handlers import common as common_handler, dispatcher
    from fysb.engines import signal_engine
    from fysb.services import subscriptions as subs_svc, users as users_svc

    uid = 78001
    tg_user = SimpleNamespace(id=uid, username="strict_partner", full_name="شريك حصري")
    users_svc.ensure_user(tg_user)
    # Even if they had an active subscription earlier, turning them into an active partner locks user mode
    subs_svc.grant_subscription(uid, "vip")
    p = partners_svc.create_partner(uid, title="قناة الحصري", commission_pct=30.0)
    assert uid in partners_svc.active_partner_user_ids()

    # 1. /start opens ONLY Partner Mode with ZERO u:* buttons
    replies = []
    async def _reply(text, **kw):
        replies.append((text, kw.get("reply_markup")))

    upd = SimpleNamespace(
        effective_user=SimpleNamespace(id=uid, username="strict_partner", full_name="شريك حصري"),
        effective_message=SimpleNamespace(reply_text=_reply),
    )
    ctx = SimpleNamespace(args=[], user_data={}, bot=SimpleNamespace(send_message=AsyncMock()))
    asyncio.run(common_handler.start(upd, ctx))
    assert replies
    txt, mk = replies[-1]
    assert "لوحة الشريك" in txt
    assert "حساب شريك قناة حصري" in txt
    all_cb = [b.callback_data for row in mk.inline_keyboard for b in row if b.callback_data]
    assert all(not c.startswith("u:") for c in all_cb), f"Leaked user button in partner home: {all_cb}"
    assert "p:contact" in all_cb

    # 2. Tapping ANY stale u:* callback (e.g. u:signals / u:home / u:markets) redirects straight to p:home
    q, rep = _mk_q(uid)
    q.data = "u:signals"
    q_upd = SimpleNamespace(callback_query=q)
    asyncio.run(dispatcher.buttons(q_upd, ctx))
    assert rep
    r_txt, r_mk = rep[-1]
    assert "لوحة الشريك" in r_txt
    r_cb = [b.callback_data for row in r_mk.inline_keyboard for b in row if b.callback_data]
    assert all(not c.startswith("u:") for c in r_cb)

    # 3. Private user signal broadcast skips active partners
    sent_uids = []
    async def _bot_send(chat_id, text, **kw):
        sent_uids.append(chat_id)
    sig_obj = {
        "id": 99991, "symbol": "XAUUSD", "timeframe": "M15", "direction": "BUY",
        "entry": 2650.0, "stop": 2640.0, "tp1": 2665.0, "tp2": 2680.0, "tp3": 2695.0,
        "score": 85, "confidence": 85, "strategy": "ict", "regime": "TREND",
        "rr": 1.5, "status": "ACTIVE", "tp1_hit": 0, "tp2_hit": 0, "sl_hit": 0,
        "reason": "Strict partner exclusion test", "created_at": config.now(),
        "data_time": config.now(), "data_source": "twelvedata",
        "last_price": 2650.0, "closed_at": "", "pips_result": 0.0,
    }
    asyncio.run(signal_engine.emit_to_users(sig_obj, _bot_send, include_channels=False))
    assert uid not in sent_uids

    # 4. Admin disables partner -> immediately reverts to regular User account!
    partners_svc.update_partner(p["id"], enabled=0)
    assert uid not in partners_svc.active_partner_user_ids()
    replies.clear()
    asyncio.run(common_handler.start(upd, ctx))
    txt_u, mk_u = replies[-1]
    assert "لوحة الشريك" not in txt_u
    u_cb = [b.callback_data for row in mk_u.inline_keyboard for b in row if b.callback_data]
    assert any(c.startswith("u:") for c in u_cb), "Disabled partner must revert to regular User menu!"

    # 5. Re-enable then delete -> also reverts cleanly to regular User account
    partners_svc.update_partner(p["id"], enabled=1)
    assert uid in partners_svc.active_partner_user_ids()
    partners_svc.delete_partner(p["id"])
    assert uid not in partners_svc.active_partner_user_ids()
    replies.clear()
    asyncio.run(common_handler.start(upd, ctx))
    txt_u2, mk_u2 = replies[-1]
    assert "لوحة الشريك" not in txt_u2
    u_cb2 = [b.callback_data for row in mk_u2.inline_keyboard for b in row if b.callback_data]
    assert any(c.startswith("u:") for c in u_cb2)


def test_partner_manual_limits_admin_controls_unlimited_and_reset(monkeypatch):
    """v6.21.3: Super Admin controls per-partner daily manual limits for
    Analysis Now and Signal Now (presets, unlimited=0, custom number, daily reset),
    and failed/NO_SETUP posts never consume quota."""
    p = partners_svc.create_partner(79001, title="شريك الحدود", commission_pct=25.0)
    cid = partners_svc.link_partner_channel(p["id"], "-1007778889990", "قناة الحدود")["channel_id"]
    bot = SimpleNamespace(send_message=AsyncMock())
    ctx = SimpleNamespace(user_data={}, bot=bot)

    # 1. Admin partner profile shows limits + button to a:partlim:<pid>
    q_adm, rep_adm = _mk_q(config.ADMIN_ID)
    asyncio.run(admin_partners.admin_partner_view(q_adm, config.ADMIN_ID, ctx, bot, p["id"]))
    txt_v, mk_v = rep_adm[-1]
    assert "تحليل الآن" in txt_v
    assert "إشارة الآن" in txt_v
    cbs_v = [b.callback_data for row in mk_v.inline_keyboard for b in row if b.callback_data]
    assert f"a:partlim:{p['id']}" in cbs_v

    # 2. Admin limits panel renders with 100% colored buttons
    asyncio.run(admin_partners.admin_partner_limits(q_adm, config.ADMIN_ID, ctx, bot, p["id"]))
    txt_l, mk_l = rep_adm[-1]
    assert "حدود النشر اليدوي اليومي" in txt_l
    btns_l = [b for row in mk_l.inline_keyboard for b in row]
    assert all(b.to_dict().get("style") in ("primary", "success", "danger") for b in btns_l)

    # 3. Admin sets analysis limit = 2 and signal limit = 0 (unlimited)
    asyncio.run(admin_partners.admin_partner_limit_set(q_adm, config.ADMIN_ID, ctx, bot, p["id"], "analysis", 2))
    asyncio.run(admin_partners.admin_partner_limit_set(q_adm, config.ADMIN_ID, ctx, bot, p["id"], "signal", 0))
    mq = partners_svc.manual_quota_status(p["id"])
    assert mq["analysis_limit"] == 2
    assert mq["signal_limit"] == 0

    # 4. Failed / NO_SETUP manual post does NOT burn quota
    monkeypatch.setattr(admin_channels, "post_analysis", AsyncMock(return_value={"ok": False, "error": "no data"}))
    q_p, rep_p = _mk_q(79001)
    asyncio.run(admin_partners.handle_partner_callback(q_p, 79001, ctx, bot, f"chpost:{cid}:analysis"))
    assert partners_svc.manual_quota_status(p["id"])["analysis_used"] == 0

    # 5. Two successful manual analyses consume 2/2; 3rd is blocked
    monkeypatch.setattr(admin_channels, "post_analysis", AsyncMock(return_value={"ok": True}))
    asyncio.run(admin_partners.handle_partner_callback(q_p, 79001, ctx, bot, f"chpost:{cid}:analysis"))
    asyncio.run(admin_partners.handle_partner_callback(q_p, 79001, ctx, bot, f"chpost:{cid}:analysis"))
    assert partners_svc.manual_quota_status(p["id"])["analysis_used"] == 2
    asyncio.run(admin_partners.handle_partner_callback(q_p, 79001, ctx, bot, f"chpost:{cid}:analysis"))
    assert "بلغت الحد اليومي" in rep_p[-1][0]

    # 6. Signal Now is unlimited (0) -> stays allowed after 10 posts
    for _ in range(10):
        ok, _, lim = partners_svc.can_manual_post(p["id"], "signal")
        assert ok is True and lim == 0
        partners_svc.record_manual_post(p["id"], "signal")
    ok_11, used_11, lim_11 = partners_svc.can_manual_post(p["id"], "signal")
    assert ok_11 is True and used_11 == 10 and lim_11 == 0

    # 7. Admin resets today's counter -> partner can immediately post analysis again
    asyncio.run(admin_partners.admin_partner_limit_reset(q_adm, config.ADMIN_ID, ctx, bot, p["id"]))
    mq_after = partners_svc.manual_quota_status(p["id"])
    assert mq_after["analysis_used"] == 0 and mq_after["signal_used"] == 0

    # 8. Admin sets custom numeric limit (18) via text input
    ctx.user_data["awaiting_partner_limit"] = (p["id"], "analysis")
    adm_replies = []
    async def _adm_reply(t, **kw):
        adm_replies.append(t)
    upd_in = SimpleNamespace(effective_message=SimpleNamespace(reply_text=_adm_reply))
    handled = asyncio.run(admin_partners.handle_partner_limit_input(upd_in, ctx, config.ADMIN_ID, "18"))
    assert handled is True
    assert partners_svc.manual_quota_status(p["id"])["analysis_limit"] == 18



