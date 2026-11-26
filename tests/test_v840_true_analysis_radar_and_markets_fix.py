"""v8.4.0 — Comprehensive regression suite for:
  1. Fix for v8.3.0 Markets activation bug (Page 2/3 stable in-place flip + VIP sync).
  2. Royal «تحليل ForexYemeni الحقيقي» (100% real closed-candle 2-stage radar,
     instant real execution, wick-fakeout rejection, auto-invalidation protection,
     continuity guard, strict private isolation, Admin-editable price/days/trial,
     and per-user 1-tap Admin grant/revoke + payment approval).
"""
import asyncio
import types
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

from fysb import config, db
from fysb.engines import market_data as md
from fysb.handlers import admin_trading, true_analysis_ui, user_pages
from fysb.services import payments as payments_svc
from fysb.services import signal_archive
from fysb.services import subscriptions as subs
from fysb.services import true_analysis as ta_svc
from fysb.services import users as users_svc


def _run(coro):
    return asyncio.run(coro)


def _fresh_db(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "DB_FILE", str(tmp_path / "v840.db"))
    monkeypatch.setattr(config, "ADMIN_ID", 999999)
    db.init_db()


def _mk_q(uid: int, data: str = ""):
    captured = {"text": "", "markup": None, "messages": [], "photos": []}

    class _Msg:
        chat_id = uid
        message_id = 1

        async def edit_text(self, text, parse_mode=None, reply_markup=None, **kw):
            captured["text"] = text
            captured["markup"] = reply_markup

        async def reply_text(self, text, parse_mode=None, reply_markup=None, **kw):
            captured["text"] = text
            captured["markup"] = reply_markup

    class _Q:
        def __init__(self):
            self.data = data
            self.from_user = SimpleNamespace(id=uid, username=f"u{uid}", first_name="Tester", full_name="Tester")
            self.message = _Msg()

        async def answer(self, text="", show_alert=False):
            pass

        async def edit_message_text(self, text, parse_mode=None, reply_markup=None, **kw):
            captured["text"] = text
            captured["markup"] = reply_markup

    class _Bot:
        async def send_message(self, chat_id, text, parse_mode=None, reply_markup=None, **kw):
            captured["messages"].append((chat_id, text))
            captured["text"] = text
            captured["markup"] = reply_markup
            return SimpleNamespace(message_id=77)

        async def send_photo(self, chat_id, photo, caption="", parse_mode=None, reply_markup=None, **kw):
            captured["photos"].append((chat_id, caption))
            return SimpleNamespace(message_id=88)

    ctx = types.SimpleNamespace(bot=_Bot(), user_data={}, bot_data={})
    return _Q(), ctx, captured


def _btns(markup) -> dict[str, str]:
    if not markup:
        return {}
    return {(b.callback_data or ""): b.text for row in markup.inline_keyboard for b in row}


def _make_candles(bars: list[tuple[str, float, float, float, float]]) -> list[dict]:
    return [
        {"datetime": ts, "open": o, "high": h, "low": l, "close": c, "volume": 1000.0}
        for ts, o, h, l, c in bars
    ]


# ---------------------------------------------------------------------------
# 1. Markets activation bug fix (Crypto/Forex Page 2/3 in-place flip + VIP sync)
# ---------------------------------------------------------------------------
def test_markets_page2_crypto_and_forex_activate_in_place_and_sync_vip(tmp_path, monkeypatch):
    _fresh_db(tmp_path, monkeypatch)
    admin_uid = config.ADMIN_ID

    # Open Crypto Page 2 and pick a closed symbol (e.g. TONUSD or SHIBUSD)
    q, ctx, cap = _mk_q(admin_uid, "a:mktcat:crypto:2")
    _run(admin_trading.admin_markets_category(q, admin_uid, ctx, ctx.bot, "crypto", 2))
    page2_before = _btns(cap["markup"])
    closed_cbs = [cb for cb, lbl in page2_before.items() if cb.startswith("a:market_toggle:") and "🔒" in lbl]
    assert closed_cbs, "Crypto page 2 should have closed symbols"
    target_cb = closed_cbs[0]
    sym = target_cb.split(":")[2]

    # Tap that closed symbol on Page 2 -> MUST stay on Page 2 and flip 🔒 -> 🟢!
    _run(admin_trading.admin_market_toggle(q, admin_uid, ctx, ctx.bot, sym, "crypto_2"))
    page2_after = _btns(cap["markup"])
    assert target_cb in page2_after, f"{sym} jumped off Page 2 after toggling!"
    assert "🟢" in page2_after[target_cb], f"{sym} did not flip to 🟢!"

    with db.conn() as c:
        row = c.execute("SELECT enabled, verified FROM market_catalog WHERE symbol=?", (sym,)).fetchone()
    assert row["enabled"] == 1 and row["verified"] == 1

    # VIP user can IMMEDIATELY see & toggle that newly activated Crypto inside u:markets!
    vip_uid = 505050
    users_svc.ensure_user(SimpleNamespace(id=vip_uid, username="vip50", full_name="VIP 50", first_name="VIP"))
    subs.grant_subscription(vip_uid, "VIP")
    u = users_svc.get_user(vip_uid)
    qu, ctxu, capu = _mk_q(vip_uid, f"u:togmarket:{sym}:crypto_2")
    _run(user_pages.user_page(qu, f"togmarket:{sym}:crypto_2", vip_uid, ctxu, preloaded_u=u))
    assert "غير متاح في باقتك" not in capu["text"]


# ---------------------------------------------------------------------------
# 2. Instant Real Execution when chart already has a confirmed closed breakout
# ---------------------------------------------------------------------------
def test_true_analysis_instant_real_signal_when_chart_already_confirmed(tmp_path, monkeypatch):
    _fresh_db(tmp_path, monkeypatch)
    uid = 7001
    users_svc.ensure_user(SimpleNamespace(id=uid, username="pro7001", full_name="Pro", first_name="Pro"))
    # Regular user starts WITHOUT access!
    assert ta_svc.has_access(uid)["active"] is False

    # Admin grants access to uid
    ta_svc.grant_access(uid, 30, actor=config.ADMIN_ID)
    assert ta_svc.has_access(uid)["active"] is True

    # Real closed candles from 2 hours ago -> strong bullish body (85%) well above activation 2650 + ATR buffer
    t_old = (datetime.now(timezone.utc) - timedelta(hours=2)).strftime("%Y-%m-%d %H:%M:%S")
    candles = _make_candles([
        (t_old, 2642.0, 2662.0, 2640.0, 2660.0),  # body = 18/22 = 81.8% >= 55%, close 2660 > 2650 + 0.15*10
    ])
    fake_analysis = {
        "symbol": "XAUUSD",
        "timeframe": "H1",
        "strategy": "ICT",
        "direction": "BUY",
        "price": 2660.0,
        "price_time": t_old,
        "quality": 88,
        "confirmation": {
            "stability_tf": "H1",
            "confirm_tf": "M15",
            "activated": True,
            "body_pct": 82,
        },
        "chart_data": {
            "tf": "H1",
            "candles": candles,
            "atr": 10.0,
            "activation_px": 2650.0,
            "invalidation_px": 2635.0,
            "setup": None,
        },
    }
    res = ta_svc.evaluate_on_analysis_created(uid, fake_analysis)
    assert res["mode"] == "INSTANT_SIGNAL"
    sig = res["signal"]
    assert sig["private_user_id"] == uid
    assert sig["direction"] == "BUY"
    assert sig["stop"] < sig["entry"] < sig["tp1"] < sig["tp2"] < sig["tp3"]

    # Strict private isolation: another user CANNOT see this private signal!
    other_uid = 7002
    users_svc.ensure_user(SimpleNamespace(id=other_uid, username="other", full_name="Other", first_name="Other"))
    other_counts = signal_archive.user_scope_counts(other_uid, ["XAUUSD"], ["H1"], ["ICT"], 0)
    owner_counts = signal_archive.user_scope_counts(uid, ["XAUUSD"], ["H1"], ["ICT"], 0)
    assert other_counts["total"] == 0
    assert owner_counts["total"] == 1


# ---------------------------------------------------------------------------
# 3. 2-Stage Closed-Candle Monitoring (Rejects Wick -> Confirms H1 -> Confirms M15 -> Fires!)
# ---------------------------------------------------------------------------
def test_true_analysis_two_stage_radar_rejects_wick_then_confirms_h1_and_m15(tmp_path, monkeypatch):
    _fresh_db(tmp_path, monkeypatch)
    uid = 7010
    users_svc.ensure_user(SimpleNamespace(id=uid, username="rad10", full_name="Radar", first_name="Radar"))
    ta_svc.grant_access(uid, 30, actor=config.ADMIN_ID)

    t0 = datetime(2026, 11, 26, 10, 0, 0, tzinfo=timezone.utc)
    # Initial analysis created at 09:45:00 UTC -> next H1 close is 10:00:00 (for 09:00 bar) / 11:00:00 (for 10:00 bar)
    monkeypatch.setattr(ta_svc, "_now_utc", lambda: datetime(2026, 11, 26, 9, 45, 0, tzinfo=timezone.utc))
    monkeypatch.setattr(config, "now", lambda: "2026-11-26T09:45:00+00:00")
    init_candles = _make_candles([
        ("2026-11-26 08:00:00", 2640.0, 2648.0, 2638.0, 2645.0),
    ])
    fake_analysis = {
        "symbol": "XAUUSD", "timeframe": "H1", "strategy": "SMC", "direction": "BUY",
        "price": 2645.0, "price_time": "2026-11-26 08:00:00", "quality": 85,
        "confirmation": {"stability_tf": "H1", "confirm_tf": "M15", "activated": False, "body_pct": 50},
        "chart_data": {"tf": "H1", "candles": init_candles, "atr": 10.0, "activation_px": 2650.0, "invalidation_px": 2630.0},
    }
    res = ta_svc.evaluate_on_analysis_created(uid, fake_analysis)
    assert res["mode"] == "MONITORING"
    mid = res["monitor"]["id"]

    _, ctx, cap = _mk_q(uid)

    # Tick 1: Wick-only fakeout (high 2658, close 2652, open 2650 -> body only 15% < 55%) -> MUST REJECT!
    wick_h1 = _make_candles([
        ("2026-11-26 09:00:00", 2642.0, 2648.0, 2640.0, 2645.0),
        ("2026-11-26 10:00:00", 2650.0, 2662.0, 2649.0, 2652.0),  # tiny body 15.4%
    ])
    monkeypatch.setattr(md, "fetch_candles", lambda *a, **kw: {"values": wick_h1, "last_candle_iso": "2026-11-26 10:00:00"})
    s1 = _run(ta_svc.poll_active_monitors(ctx.bot, now_dt=t0 + timedelta(hours=2)))
    assert s1["stage1_confirmed"] == 0 and s1["signals_fired"] == 0

    # Tick 2: Strong real closed H1 candle (11:00 -> 12:00 UTC, open 2645, close 2660 -> body 83% >= 55% > 2651.5)!
    strong_h1 = _make_candles([
        ("2026-11-26 09:00:00", 2642.0, 2648.0, 2640.0, 2645.0),
        ("2026-11-26 11:00:00", 2645.0, 2662.0, 2644.0, 2660.0),
    ])
    monkeypatch.setattr(md, "fetch_candles", lambda *a, **kw: {"values": strong_h1, "last_candle_iso": "2026-11-26 11:00:00"})
    s2 = _run(ta_svc.poll_active_monitors(ctx.bot, now_dt=t0 + timedelta(hours=3)))
    assert s2["stage1_confirmed"] == 1
    assert s2["signals_fired"] == 0  # MUST wait for M15 confirmation!
    assert any("تم تأكيد إغلاق شمعة H1" in msg for _, msg in cap["messages"])

    # Tick 3: Subsequent closed M15 confirmation candle opening AT/AFTER H1 close (12:00 UTC) and closed by 12:30!
    confirm_m15 = _make_candles([
        ("2026-11-26 11:45:00", 2656.0, 2661.0, 2655.0, 2660.0),  # inside H1 bar
        ("2026-11-26 12:00:00", 2659.0, 2666.0, 2658.5, 2665.0),  # opens at 12:00, closes at 12:15 (body 80%)
    ])
    monkeypatch.setattr(md, "fetch_candles", lambda *a, **kw: {"values": confirm_m15, "last_candle_iso": "2026-11-26 12:00:00"})
    s3 = _run(ta_svc.poll_active_monitors(ctx.bot, now_dt=t0 + timedelta(hours=3, minutes=30)))
    assert s3["signals_fired"] == 1
    assert len(cap["photos"]) >= 1 or any("إشارة حقيقية مؤكّدة" in msg for _, msg in cap["messages"])

    # Continuity Guard: user asks for a new XAUUSD analysis while their signal is ACTIVE -> gets continuity card!
    cont = ta_svc.check_active_continuity(uid, "XAUUSD")
    assert cont is not None and cont["kind"] == "ACTIVE_SIGNAL"
    body, _ = ta_svc.render_continuity_card(cont, live_px=2675.0)
    assert "ما زالا مستمرّين" in body

    # Once the signal closes (TP3_HIT), Continuity Guard automatically releases!
    with db.tx() as c:
        c.execute("UPDATE signals SET status='TP3_HIT' WHERE id=?", (cont["signal"]["id"],))
    assert ta_svc.check_active_continuity(uid, "XAUUSD") is None


# ---------------------------------------------------------------------------
# 4. Auto-Invalidation Protection + Admin Editable Settings & Trial Flow
# ---------------------------------------------------------------------------
def test_invalidation_protection_and_admin_editable_trial_and_payment(tmp_path, monkeypatch):
    _fresh_db(tmp_path, monkeypatch)
    uid = 7020
    users_svc.ensure_user(SimpleNamespace(id=uid, username="tr20", full_name="TrialUser", first_name="Trial"))

    # Admin configures price=149, days=45, trial=5
    db.set_setting("true_analysis_price_usd", "149")
    db.set_setting("true_analysis_days", "45")
    db.set_setting("true_analysis_trial_days", "5")
    cfg = ta_svc.settings_snapshot()
    assert cfg["price_usd"] == 149.0 and cfg["duration_days"] == 45 and cfg["trial_days"] == 5

    # User activates Admin-configured 5-day free trial once; second attempt blocked!
    t_res = ta_svc.activate_user_trial(uid)
    assert t_res["ok"] is True and t_res["days"] == 5
    assert ta_svc.has_access(uid)["active"] is True
    ta_svc.revoke_access(uid, actor=config.ADMIN_ID)
    t_res2 = ta_svc.activate_user_trial(uid)
    assert t_res2["ok"] is False

    # Payment approval for TRUE_ANALYSIS auto-grants 45-day paid access!
    pid = payments_svc.create_request(uid, "TRUE_ANALYSIS", 149.0, "USDT-TRC20")
    ap = payments_svc.approve_request(pid, config.ADMIN_ID)
    assert ap["ok"] is True
    st = ta_svc.has_access(uid)
    assert st["active"] is True and st["days_left"] >= 44

    # Register monitor at 09:45 UTC and verify auto-invalidation ONLY AFTER a new H1 candle closes!
    t0 = datetime(2026, 11, 26, 10, 0, 0, tzinfo=timezone.utc)
    monkeypatch.setattr(ta_svc, "_now_utc", lambda: datetime(2026, 11, 26, 9, 45, 0, tzinfo=timezone.utc))
    monkeypatch.setattr(config, "now", lambda: "2026-11-26T09:45:00+00:00")
    fake_an = {
        "symbol": "XAUUSD", "timeframe": "H1", "strategy": "ICT", "direction": "BUY",
        "price": 2645.0, "price_time": "2026-11-26 08:00:00", "quality": 82,
        "confirmation": {"stability_tf": "H1", "confirm_tf": "M15", "activated": False, "body_pct": 40},
        "chart_data": {"tf": "H1", "candles": _make_candles([("2026-11-26 08:00:00", 2642.0, 2648.0, 2640.0, 2645.0)]),
                       "atr": 8.0, "activation_px": 2655.0, "invalidation_px": 2635.0},
    }
    ta_svc.evaluate_on_analysis_created(uid, fake_an)
    _, ctx, cap = _mk_q(uid)
    crash_h1 = _make_candles([("2026-11-26 10:00:00", 2644.0, 2645.0, 2628.0, 2630.0)])
    monkeypatch.setattr(md, "fetch_candles", lambda *a, **kw: {"values": crash_h1, "last_candle_iso": "2026-11-26 10:00:00"})
    s_inv = _run(ta_svc.poll_active_monitors(ctx.bot, now_dt=t0 + timedelta(hours=2)))
    assert s_inv["invalidated"] == 1 and s_inv["signals_fired"] == 0
    assert any("تم إلغاء تحليل" in msg for _, msg in cap["messages"])

    # Admin Studio UI renders cleanly
    qa, ctxa, capa = _mk_q(config.ADMIN_ID, "a:true_analysis")
    _run(true_analysis_ui.handle_admin_true_analysis(qa, config.ADMIN_ID, "true_analysis", "", "", ctxa, ctxa.bot))
    assert "149" in capa["text"] and "45" in capa["text"] and "5" in capa["text"]


def test_true_analysis_full_checkout_and_all_ui_buttons_zero_crash(tmp_path, monkeypatch):
    """v8.4.1 regression: u:tabuy, u:tausdt, u:tapay, u:tatrial, a:utamenu/utagrant/utarevoke."""
    _fresh_db(tmp_path, monkeypatch)
    uid = 7099
    users_svc.ensure_user(SimpleNamespace(id=uid, username="buyer99", full_name="Buyer 99", first_name="Buyer"))

    # Add a real payment method + USDT TRC20 wallet
    mid = payments_svc.add_method("محفظة جوالي", "أرسل الإيصال بعد التحويل", "777123456", "اليمن")
    payments_svc.set_chain_wallet("TRC20", "TTestWalletAddress123456789012345678")

    # 1. User opens u:true_analysis -> sees offer + trial + buy
    q, ctx, cap = _mk_q(uid, "u:true_analysis")
    assert _run(true_analysis_ui.handle_user_true_analysis(q, uid, "true_analysis", ctx, ctx.bot)) is True
    assert "u:tabuy" in _btns(cap["markup"])

    # 2. User taps u:tabuy (the exact button that crashed in v8.4.0!) -> renders cleanly with BOTH TRC20 & Jawali!
    assert _run(true_analysis_ui.handle_user_true_analysis(q, uid, "tabuy", ctx, ctx.bot)) is True
    btns_buy = _btns(cap["markup"])
    assert "u:tausdt:TRC20" in btns_buy
    assert f"u:tapay:{mid}" in btns_buy

    # 3. User taps manual method u:tapay:<mid> -> renders account_ref + sets awaiting_receipt cleanly!
    assert _run(true_analysis_ui.handle_user_true_analysis(q, uid, f"tapay:{mid}", ctx, ctx.bot)) is True
    assert "777123456" in cap["text"]
    assert int(ctx.user_data.get("awaiting_receipt") or 0) > 0

    # 4. User taps u:tausdt:TRC20 -> renders chain payment page cleanly!
    assert _run(true_analysis_ui.handle_user_true_analysis(q, uid, "tausdt:TRC20", ctx, ctx.bot)) is True
    assert "TTestWalletAddress123456789012345678" in cap["text"]

    # 5. Admin per-user menu, grant trial, grant 30d, list, revoke -> all zero-crash!
    qa, ctxa, capa = _mk_q(config.ADMIN_ID)
    for act, a1, a2 in [
        ("utamenu", str(uid), ""),
        ("utagrant", str(uid), "trial"),
        ("talist", "", ""),
        ("utagrant", str(uid), "30"),
        ("utarevoke", str(uid), ""),
    ]:
        assert _run(true_analysis_ui.handle_admin_true_analysis(qa, config.ADMIN_ID, act, a1, a2, ctxa, ctxa.bot)) is True


def test_v842_never_invalidates_before_h1_candle_actually_closes_at_548am(tmp_path, monkeypatch):
    """Exact live bug reproduction (5:48 AM):
    User creates XAUUSD H1 SELL analysis at 05:45 UTC.
      - Historical 04:00->05:00 H1 bar closed BEFORE analysis at 3610.10 (> invalidation 3606.50).
      - Live 05:00->06:00 H1 bar is STILL FORMING at 05:48 UTC (price 3610.10).
    Radar MUST NOT cancel or trigger at 05:48 UTC! It must wait until 06:00 UTC when the
    05:00 H1 candle ACTUALLY closes!
    """
    _fresh_db(tmp_path, monkeypatch)
    uid = 7148
    users_svc.ensure_user(SimpleNamespace(id=uid, username="u7148", full_name="Gold", first_name="Gold"))
    ta_svc.grant_access(uid, 30, actor=config.ADMIN_ID)

    # Create SELL monitor at 05:45:00 UTC with invalidation above price (3606.50)
    monkeypatch.setattr(ta_svc, "_now_utc", lambda: datetime(2026, 11, 26, 5, 45, 0, tzinfo=timezone.utc))
    monkeypatch.setattr(config, "now", lambda: "2026-11-26T05:45:00+00:00")
    an = {
        "symbol": "XAUUSD", "timeframe": "H1", "strategy": "SMC", "direction": "SELL",
        "price": 3600.0, "price_time": "2026-11-26 05:00:00", "quality": 84,
        "confirmation": {"stability_tf": "H1", "confirm_tf": "M15", "activated": False, "body_pct": 40},
        "chart_data": {
            "tf": "H1",
            "candles": _make_candles([
                ("2026-11-26 04:00:00", 3602.0, 3612.0, 3600.0, 3610.10),  # old bar closed at 05:00!
                ("2026-11-26 05:00:00", 3605.0, 3606.0, 3598.0, 3600.00),  # forming bar (closes 06:00)
            ]),
            "atr": 6.0,
            "activation_px": 3595.0,
            "invalidation_px": 3606.50,
        },
    }
    res = ta_svc.evaluate_on_analysis_created(uid, an)
    assert res["mode"] == "MONITORING"

    _, ctx, cap = _mk_q(uid)

    # Live feed at 05:48:00 UTC: old 04:00 bar closed at 3610.10, live 05:00 bar currently at 3610.10 (NOT CLOSED!)
    live_at_548 = _make_candles([
        ("2026-11-26 04:00:00", 3602.0, 3612.0, 3600.0, 3610.10),
        ("2026-11-26 05:00:00", 3605.0, 3611.0, 3598.0, 3610.10),
    ])
    monkeypatch.setattr(md, "fetch_candles", lambda *a, **kw: {"values": live_at_548, "last_candle_iso": "2026-11-26 05:00:00"})

    # Poll at 05:48:00 UTC -> MUST STAY WATCHING (ZERO invalidation, ZERO alerts!)
    s_548 = _run(ta_svc.poll_active_monitors(ctx.bot, now_dt=datetime(2026, 11, 26, 5, 48, 0, tzinfo=timezone.utc)))
    assert s_548["invalidated"] == 0
    assert s_548["signals_fired"] == 0
    assert len(cap["messages"]) == 0
    assert ta_svc.check_active_continuity(uid, "XAUUSD")["kind"] == "WATCHING"

    # Poll at 05:59:55 UTC -> STILL 5 seconds before H1 close -> MUST STILL STAY WATCHING!
    s_559 = _run(ta_svc.poll_active_monitors(ctx.bot, now_dt=datetime(2026, 11, 26, 5, 59, 55, tzinfo=timezone.utc)))
    assert s_559["invalidated"] == 0
    assert len(cap["messages"]) == 0

    # Poll at 06:00:05 UTC -> NOW the 05:00->06:00 H1 bar HAS ACTUALLY CLOSED at 3610.10 > 3606.50 -> NOW invalidates honestly!
    s_600 = _run(ta_svc.poll_active_monitors(ctx.bot, now_dt=datetime(2026, 11, 26, 6, 0, 5, tzinfo=timezone.utc)))
    assert s_600["invalidated"] == 1
    assert any("05:00→06:00 UTC" in msg for _, msg in cap["messages"])


