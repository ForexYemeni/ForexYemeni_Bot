"""v6.22.0 tests — 4-Tier Royal Plans (FREE/PRO/VIP/ROYAL), ordered strategies,
Admin-configurable FREE trial duration, and Ultra-Luxury Plan & Upgrade Cards."""
import asyncio
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock

from fysb import config, db
from fysb.engines import signal_engine
from fysb.handlers import admin_core, dispatcher, signal_now, user_pages
from fysb.services import subscriptions as subs
from fysb.services import users as users_svc


def _mk_q(uid: int, data: str = ""):
    replies = []

    async def _reply(text, **kw):
        replies.append((text, kw.get("reply_markup")))

    async def _edit(text, **kw):
        replies.append((text, kw.get("reply_markup")))

    q = SimpleNamespace(
        from_user=SimpleNamespace(id=uid, username=f"u{uid}", full_name=f"User {uid}"),
        data=data,
        message=SimpleNamespace(reply_text=_reply, edit_text=_edit, chat_id=uid, message_id=1),
        answer=AsyncMock(),
    )
    return q, replies


def _reset_plans_v6220():
    with db.conn() as c:
        c.execute("DELETE FROM settings WHERE key IN ('plans_4tier_v6220_applied', 'plans_luxury_v6100_applied')")
    db._settings_cache.clear()
    db.init_db()
    subs.invalidate_plans_cache()


def test_four_tier_plans_ordered_strategies_and_active_catalogs():
    _reset_plans_v6220()
    with db.conn() as c:
        plans = {r["code"]: dict(r) for r in c.execute("SELECT * FROM plans").fetchall()}
        live_mkts = {r["symbol"] for r in c.execute("SELECT symbol FROM market_catalog WHERE enabled=1 AND verified=1").fetchall()}
        live_strats = {r["code"] for r in c.execute("SELECT code FROM strategy_catalog WHERE enabled=1 AND verified=1").fetchall()}

    assert set(plans.keys()) >= {"FREE", "PRO", "VIP", "ROYAL"}
    # Prices & quotas strictly ascending
    assert (plans["FREE"]["price"], plans["FREE"]["max_signals_per_day"], plans["FREE"]["max_analyses_per_day"]) == (0.0, 1, 1)
    assert (plans["PRO"]["price"], plans["PRO"]["max_signals_per_day"], plans["PRO"]["max_analyses_per_day"]) == (19.0, 4, 4)
    assert (plans["VIP"]["price"], plans["VIP"]["max_signals_per_day"], plans["VIP"]["max_analyses_per_day"]) == (39.0, 10, 10)
    assert (plans["ROYAL"]["price"], plans["ROYAL"]["max_signals_per_day"], plans["ROYAL"]["max_analyses_per_day"]) == (79.0, 25, 25)
    assert plans["FREE"]["duration_days"] == 7

    # Ordered strategies: 2 -> 5 -> 9 -> 13, with elite liquidity hunters exclusive to ROYAL
    s_free = set(plans["FREE"]["strategies"].split(","))
    s_pro = set(plans["PRO"]["strategies"].split(","))
    s_vip = set(plans["VIP"]["strategies"].split(","))
    s_royal = set(plans["ROYAL"]["strategies"].split(","))
    assert len(s_free) == 2 and s_free == {"CLASSIC", "TREND"}
    assert len(s_pro) == 5 and s_free < s_pro
    assert len(s_vip) == 9 and s_pro < s_vip
    assert len(s_royal) == 13 and s_vip < s_royal
    elite_only = {"CRT", "AMD", "SILVER_BULLET", "TURTLE_SOUP"}
    assert elite_only.isdisjoint(s_vip)
    assert elite_only.issubset(s_royal)

    # All plan markets & all 13 strategies are active in catalogs
    assert {"XAUUSD", "EURUSD", "GBPUSD", "USDJPY", "US30", "NAS100", "BTCUSD"}.issubset(live_mkts)
    assert len(live_strats) == 13


def test_configurable_free_trial_blocks_when_expired_and_admin_can_extend_or_unlimit():
    _reset_plans_v6220()
    uid = 81201
    users_svc.ensure_user(SimpleNamespace(id=uid, username="trial_u", full_name="مستخدم تجريبي"))
    # Set joined_at = 8 days ago -> exceeds default 7-day FREE trial
    eight_days_ago = (datetime.now(timezone.utc) - timedelta(days=8)).isoformat()
    with db.conn() as c:
        c.execute("UPDATE users SET joined_at=?, notifications=1 WHERE user_id=?", (eight_days_ago, uid))
        c.execute("UPDATE subscriptions SET status='REPLACED' WHERE user_id=? AND status='ACTIVE'", (uid,))
    subs.invalidate_plans_cache(uid)

    st = subs.free_trial_status(uid)
    assert st["is_free"] is True
    assert st["expired"] is True
    assert st["trial_days"] == 7

    # 1. Expired FREE user tapping u:signals, u:analytics, u:signow gets Luxury Upgrade Card
    bot = SimpleNamespace(send_message=AsyncMock())
    ctx = SimpleNamespace(user_data={}, bot=bot)
    for cb in ("u:signals", "u:analytics", "u:signow"):
        q, rep = _mk_q(uid, data=cb)
        asyncio.run(dispatcher.buttons(SimpleNamespace(callback_query=q), ctx))
        assert rep, f"No response for {cb}"
        txt, mk = rep[-1]
        assert "انتهت فترتك المجانية" in txt
        assert "ROYAL" in txt and "VIP" in txt and "PRO" in txt
        btns = [b for row in mk.inline_keyboard for b in row]
        assert all(b.to_dict().get("style") in ("primary", "success", "danger") for b in btns)

    # 2. Private signal broadcast skips expired FREE trial user
    sent_uids = []
    async def _send(chat_id, text, **kw):
        sent_uids.append(chat_id)
    sig_obj = {
        "id": 99992, "symbol": "XAUUSD", "timeframe": "M15", "direction": "BUY",
        "entry": 2650.0, "stop": 2640.0, "tp1": 2665.0, "tp2": 2680.0, "tp3": 2695.0,
        "score": 88, "confidence": 88, "strategy": "CLASSIC", "regime": "TREND",
        "rr": 1.5, "status": "ACTIVE", "tp1_hit": 0, "tp2_hit": 0, "sl_hit": 0,
        "reason": "Trial expiry exclusion test", "created_at": config.now(),
        "data_time": config.now(), "data_source": "twelvedata",
        "last_price": 2650.0, "closed_at": "", "pips_result": 0.0,
    }
    asyncio.run(signal_engine.emit_to_users(sig_obj, _send, include_channels=False))
    assert uid not in sent_uids

    # 3. Admin raises FREE trial to 14 days via 1-tap preset -> user immediately unblocked!
    q_adm, rep_adm = _mk_q(config.ADMIN_ID, data="a:plantrial:FREE:14")
    asyncio.run(dispatcher.buttons(SimpleNamespace(callback_query=q_adm), ctx))
    st14 = subs.free_trial_status(uid)
    assert st14["expired"] is False
    assert st14["trial_days"] == 14
    assert st14["days_left"] >= 5

    # 4. Admin sets FREE trial to 0 (unlimited forever) -> unlimited=True
    q_adm0, _ = _mk_q(config.ADMIN_ID, data="a:plantrial:FREE:0")
    asyncio.run(dispatcher.buttons(SimpleNamespace(callback_query=q_adm0), ctx))
    st0 = subs.free_trial_status(uid)
    assert st0["expired"] is False
    assert st0["unlimited"] is True

    # Restore default 7d for downstream tests
    q_adm7, _ = _mk_q(config.ADMIN_ID, data="a:plantrial:FREE:7")
    asyncio.run(dispatcher.buttons(SimpleNamespace(callback_query=q_adm7), ctx))


def test_luxury_plans_showcase_and_smart_tier_recommender():
    _reset_plans_v6220()
    uid = 81202
    users_svc.ensure_user(SimpleNamespace(id=uid, username="lux_u", full_name="مستخدم فاخر"))

    # Smart cheapest-tier recommender matches exact plan unlocking each feature
    assert subs.cheapest_plan_unlocking("strategies", "PRICE_ACTION")["code"] == "PRO"
    assert subs.cheapest_plan_unlocking("strategies", "ICT")["code"] == "VIP"
    assert subs.cheapest_plan_unlocking("strategies", "CRT")["code"] == "ROYAL"
    assert subs.cheapest_plan_unlocking("strategies", "SILVER_BULLET")["code"] == "ROYAL"
    assert subs.cheapest_plan_unlocking("markets", "EURUSD")["code"] == "PRO"
    assert subs.cheapest_plan_unlocking("markets", "NAS100")["code"] == "VIP"

    # u:plans renders luxury showcase with all 4 tiers & 100% colored buttons
    bot = SimpleNamespace(send_message=AsyncMock())
    ctx = SimpleNamespace(user_data={}, bot=bot)
    q, rep = _mk_q(uid, data="u:plans")
    asyncio.run(dispatcher.buttons(SimpleNamespace(callback_query=q), ctx))
    txt, mk = rep[-1]
    assert "باقات الاشتراك الملكية" in txt
    for code in ("FREE", "PRO", "VIP", "ROYAL"):
        assert code in txt
    btns = [b for row in mk.inline_keyboard for b in row]
    assert all(b.to_dict().get("style") in ("primary", "success", "danger") for b in btns)

    # Tapping locked strategy (CRT) from FREE recommends ROYAL ELITE specifically
    q2, rep2 = _mk_q(uid, data="u:setstrategy:CRT")
    asyncio.run(dispatcher.buttons(SimpleNamespace(callback_query=q2), ctx))
    txt2, mk2 = rep2[-1]
    assert "ROYAL" in txt2
    assert "خطتك التالية المقترحة" in txt2


def test_receipt_photo_sends_admin_approve_reject_buttons_and_approves():
    """v6.22.1: When a user sends a payment receipt photo, Super Admin receives
    a luxury HTML card attached to the photo with 1-tap Approve & Reject buttons."""
    from fysb.handlers import inputs as inputs_handler
    from fysb.services import payments as payments_svc

    _reset_plans_v6220()
    uid = 81301
    users_svc.ensure_user(SimpleNamespace(id=uid, username="payer1", full_name="عميل VIP"))
    pid = payments_svc.create_request(uid, "VIP", 39.0, "USDT")

    sent_photos = []
    async def _send_photo(chat_id, photo_id, **kw):
        sent_photos.append((chat_id, photo_id, kw))

    user_replies = []
    async def _user_reply(text, **kw):
        user_replies.append(text)

    bot = SimpleNamespace(send_photo=_send_photo, send_message=AsyncMock())
    ctx = SimpleNamespace(user_data={"awaiting_receipt": pid}, bot=bot)
    upd = SimpleNamespace(
        effective_user=SimpleNamespace(id=uid, username="payer1", full_name="عميل VIP"),
        effective_message=SimpleNamespace(
            photo=[SimpleNamespace(file_id="FILE_RECEIPT_123")],
            caption="",
            reply_text=_user_reply,
        ),
    )
    orig_admin = config.ADMIN_ID
    config.ADMIN_ID = 999999
    try:
        asyncio.run(inputs_handler.on_photo(upd, ctx))
    finally:
        config.ADMIN_ID = orig_admin

    assert user_replies and f"#{pid}" in user_replies[-1]
    assert sent_photos, "Admin must receive receipt photo notification"
    chat_id, fid, kw = sent_photos[-1]
    assert chat_id == 999999 and fid == "FILE_RECEIPT_123"
    assert kw.get("parse_mode") == "HTML"
    assert f"#{pid}" in kw.get("caption", "")
    assert "VIP" in kw.get("caption", "")
    mk = kw.get("reply_markup")
    assert mk is not None
    btns = [b for row in mk.inline_keyboard for b in row]
    cbs = [b.callback_data for b in btns]
    assert f"a:approvepay:{pid}" in cbs
    assert f"a:rejectpay:{pid}" in cbs
    assert f"a:paymentdetail:{pid}" in cbs
    assert f"a:userdetail:{uid}" in cbs
    assert all(b.to_dict().get("style") in ("primary", "success", "danger") for b in btns)


def test_quota_exhausted_shows_luxury_next_tier_upgrade_card():
    """v6.22.1: Exhausting daily Analysis or Signal quota immediately shows the
    Ultra-Luxury Next-Tier Upgrade Card recommending PRO for FREE, VIP for PRO,
    and ROYAL for VIP with colored 1-tap upgrade buttons."""
    _reset_plans_v6220()
    assert subs.next_higher_plan("FREE")["code"] == "PRO"
    assert subs.next_higher_plan("PRO")["code"] == "VIP"
    assert subs.next_higher_plan("VIP")["code"] == "ROYAL"
    assert subs.next_higher_plan("ROYAL") is None

    uid = 81302
    users_svc.ensure_user(SimpleNamespace(id=uid, username="quota_u", full_name="عميل تحليل"))
    # Exhaust FREE's 1/1 daily analysis quota
    with db.conn() as c:
        c.execute(
            """INSERT INTO analyses(user_id,symbol,timeframe,tool,analysis_type,strategy,
               price,direction,quality,summary,detail,created_at)
               VALUES(?, 'XAUUSD', 'M15', '', 'يومي', 'CLASSIC', 2650, 'BUY', 80, 'ok', '{}', ?)""",
            (uid, config.now()),
        )
        # Also exhaust FREE's 1/1 daily signal quota
        c.execute(
            "INSERT OR IGNORE INTO signal_deliveries(user_id,signal_id,delivered_at) VALUES(?, 77777, ?)",
            (uid, config.now()),
        )

    bot = SimpleNamespace(send_message=AsyncMock())
    ctx = SimpleNamespace(user_data={}, bot=bot)

    # 1. u:analytics -> Luxury Next-Tier Upgrade Card recommending PRO!
    q_an, rep_an = _mk_q(uid, data="u:analytics")
    asyncio.run(dispatcher.buttons(SimpleNamespace(callback_query=q_an), ctx))
    txt_an, mk_an = rep_an[-1]
    assert "وصلت للحد اليومي لباقتك" in txt_an
    assert "انتهت حصة التحليلات اليومية" in txt_an
    assert "خطتك التالية المقترحة" in txt_an
    assert "PRO" in txt_an
    btns_an = [b for row in mk_an.inline_keyboard for b in row]
    assert any(b.callback_data == "u:planview:PRO" for b in btns_an)
    assert all(b.to_dict().get("style") in ("primary", "success", "danger") for b in btns_an)

    # 2. u:signow -> Luxury Next-Tier Upgrade Card recommending PRO!
    q_sn, rep_sn = _mk_q(uid, data="u:signow")
    asyncio.run(dispatcher.buttons(SimpleNamespace(callback_query=q_sn), ctx))
    txt_sn, mk_sn = rep_sn[-1]
    assert "وصلت للحد اليومي لباقتك" in txt_sn
    assert "انتهت حصة الإشارات اليومية" in txt_sn
    assert "PRO" in txt_sn
    assert any(b.callback_data == "u:planview:PRO" for row in mk_sn.inline_keyboard for b in row)

