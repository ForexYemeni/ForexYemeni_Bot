"""v8.5.0 — Institutional True Analysis Engine 10/10 Shield & Dual-Entry Tests.

Verifies ALL 5 real institutional upgrades:
  1. Market-Hours & Weekend Shield (PAXG weekend drift never triggers/invalidates Gold; Crypto 24/7 open).
  2. Unified Closed-Candle Verification in analytics.py (never marks a forming candle as activated).
  3. Dual Institutional Entry Mode: PULLBACK_ZONE (OB/FVG rejection) + BREAKOUT.
  4. Liquidity & Structural S/R-Aware Take Profits (snaps TP ahead of real opposing liquidity walls).
  5. Anti-Chase Spike Filter (> 1.8x ATR blow-off blocked) + High-Impact News Guard (NFP/CPI/FOMC).
"""
from __future__ import annotations

import asyncio
from datetime import datetime, timedelta, timezone

from fysb import config, db, instruments
from fysb.engines import market_data, news_guard
from fysb.services import analytics, true_analysis as ta


class _DummyBot:
    def __init__(self):
        self.messages: list[dict] = []
        self.photos: list[dict] = []

    async def send_message(self, chat_id, text, **kwargs):
        self.messages.append({"chat_id": chat_id, "text": text, **kwargs})
        return type("M", (), {"message_id": 100 + len(self.messages)})()

    async def send_photo(self, chat_id, photo, caption="", **kwargs):
        self.photos.append({"chat_id": chat_id, "caption": caption, **kwargs})
        return type("M", (), {"message_id": 200 + len(self.photos)})()


def _init_db(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "DB_FILE", str(tmp_path / "v850.db"))
    db.init_db()
    news_guard.reset_cache()
    news_guard.set_cached_events([], ts=9e12)  # zero network calls in tests


def _ensure_subscriber(uid: int = 85001):
    from fysb.services import users as users_srv
    users_srv.ensure_user(type("U", (), {"id": uid, "username": f"u{uid}", "full_name": "Institutional"})())
    ta.grant_access(uid, days=30, actor=999)


def test_v850_weekend_shield_pauses_gold_and_keeps_crypto_open(tmp_path, monkeypatch):
    _init_db(tmp_path, monkeypatch)
    uid = 85001
    _ensure_subscriber(uid)

    # Saturday 14:00 UTC -> Gold/Forex CLOSED, Crypto OPEN
    sat_utc = datetime(2026, 4, 25, 14, 0, 0, tzinfo=timezone.utc)
    assert ta.is_market_open_at("metal", sat_utc) is False
    assert ta.is_market_open_at("forex", sat_utc) is False
    assert ta.is_market_open_at("crypto", sat_utc) is True

    # Even if candles look confirmed on Saturday, evaluate_on_analysis_created NEVER fires instant signal
    sat_candles = [
        {"datetime": "2026-04-25 12:00:00", "open": 3600.0, "high": 3622.0, "low": 3599.0, "close": 3620.0},
    ]
    analysis = {
        "symbol": "XAUUSD",
        "timeframe": "H1",
        "direction": "BUY",
        "price": 3620.0,
        "quality": 90,
        "confirmation": {"activated": True, "body_pct": 85},
        "chart_data": {
            "tf": "H1",
            "atr": 10.0,
            "activation_px": 3610.0,
            "invalidation_px": 3585.0,
            "candles": sat_candles,
        },
    }
    res = ta.evaluate_on_analysis_created(uid, analysis, now_dt=sat_utc)
    assert res["mode"] == "MONITORING"
    assert res["market_closed"] is True
    banner = ta.render_monitoring_started_banner(res)
    assert "درع عطلة السوق" in banner

    # Even if PAXG mock candles print a fake crash below invalidation on Saturday,
    # poll_active_monitors MUST pause cleanly (zero invalidation, zero signals!)
    def _fake_fetch(*args, **kwargs):
        return {
            "values": [
                {"datetime": "2026-04-25 13:00:00", "open": 3610.0, "high": 3612.0, "low": 3550.0, "close": 3555.0},
            ]
        }

    monkeypatch.setattr(market_data, "fetch_candles", _fake_fetch)
    bot = _DummyBot()
    out = asyncio.run(ta.poll_active_monitors(bot, now_dt=sat_utc + timedelta(hours=1)))
    assert out["paused_market_closed"] == 1
    assert out["invalidated"] == 0
    assert out["signals_fired"] == 0
    assert len(bot.messages) == 0


def test_v850_analytics_card_never_marks_forming_candle_as_activated(tmp_path, monkeypatch):
    _init_db(tmp_path, monkeypatch)
    now = datetime.now(timezone.utc)
    # Closed previous H1 candle is BELOW activation (3610.0), while the still-forming
    # current H1 candle has temporarily pushed to 3620.0!
    closed_open = (now - timedelta(hours=2)).strftime("%Y-%m-%d %H:00:00")
    forming_open = now.strftime("%Y-%m-%d %H:00:00")
    candles = [
        {"datetime": closed_open, "open": 3602.0, "high": 3608.0, "low": 3598.0, "close": 3605.0},
        {"datetime": forming_open, "open": 3605.0, "high": 3622.0, "low": 3604.0, "close": 3620.0},
    ]
    main = {
        "close": 3620.0,
        "atr": 10.0,
        "sr": {"resistance": 3610.0, "support": 3590.0},
        "struct": {"bos": True, "choch": False},
        "breakout": {},
        "sweep": {},
        "obs": [],
        "fvgs": [],
    }
    spec = analytics._build_real_confirmation_spec(
        symbol="XAUUSD",
        row=None,
        tf="H1",
        direction="BUY",
        candles=candles,
        main=main,
        activation_px=3610.0,
        invalidation_px=3588.0,
        fmt=lambda v: f"{v:,.2f}",
    )
    assert spec["activated"] is False
    assert "الشمعة الجارية" in spec["live_state"]


def test_v850_dual_entry_pullback_ob_fvg_detection_and_confirmation(tmp_path, monkeypatch):
    _init_db(tmp_path, monkeypatch)
    uid = 85002
    _ensure_subscriber(uid)

    # 1. analytics._detect_pullback_zone picks clean bullish OB below price & above invalidation
    pz = analytics._detect_pullback_zone(
        direction="BUY",
        cur_close=3612.0,
        activation_px=3625.0,
        invalidation_px=3595.0,
        atr=10.0,
        obs=[{"kind": "bullish", "lo": 3602.0, "hi": 3608.0}],
        fvgs=[],
    )
    assert pz["entry_mode"] == "PULLBACK_ZONE"
    assert pz["zone_lo"] == 3602.0
    assert pz["zone_hi"] == 3608.0

    # 2. evaluate_closed_candle confirms a real bullish OB rejection candle WITHOUT needing distant breakout!
    tue_open = datetime(2026, 4, 21, 10, 0, 0, tzinfo=timezone.utc)
    tue_after = datetime(2026, 4, 21, 11, 5, 0, tzinfo=timezone.utc)
    candles = [
        {
            "datetime": "2026-04-21 10:00:00",
            "open": 3609.0,
            "high": 3616.5,
            "low": 3604.0,   # dipped cleanly into OB [3602, 3608]
            "close": 3616.0,  # closed strongly bullish above zone_hi (body > 55%)
        }
    ]
    res = ta.evaluate_closed_candle(
        candles,
        tf="H1",
        direction="BUY",
        level_px=3625.0,  # breakout resistance still far above!
        buffer_px=1.5,
        invalidation_px=3595.0,
        min_body_pct=55.0,
        min_close_dt=tue_open + timedelta(minutes=10),
        now_dt=tue_after,
        entry_mode="PULLBACK_ZONE",
        zone_lo=3602.0,
        zone_hi=3608.0,
        atr=10.0,
    )
    assert res["state"] == "CONFIRMED"
    assert res["trigger_kind"] == "PULLBACK_ZONE"
    assert res["reason"] == "pullback_zone_rejection_confirmed"


def test_v850_liquidity_and_sr_aware_take_profits(tmp_path, monkeypatch):
    _init_db(tmp_path, monkeypatch)
    # Entry = 3600, Invalidation = 3590 (risk ~ 12.5 with pad -> 1.5R ~ 3618.75)
    # Real opposing bearish OB sits at 3617.0 (1.36R) -> TP1 must snap cleanly BEFORE 3617.0!
    plan = ta._derive_real_trade_plan(
        symbol="XAUUSD",
        row=None,
        direction="BUY",
        entry_px=3600.0,
        invalidation_px=3590.0,
        atr=10.0,
        setup=None,
        obs=[{"kind": "bearish", "lo": 3617.0, "hi": 3621.0}],
        sr={"resistance": 3632.0},
    )
    assert plan is not None
    assert plan["tp_grounded"] is True
    assert 3600.0 < plan["tp1"] < 3617.0
    assert plan["tp1"] < plan["tp2"] < plan["tp3"]
    assert plan["rr"] >= 1.5


def test_v850_anti_chase_filter_and_high_impact_news_guard(tmp_path, monkeypatch):
    _init_db(tmp_path, monkeypatch)
    uid = 85003
    _ensure_subscriber(uid)

    tue_09 = datetime(2026, 4, 21, 9, 15, 0, tzinfo=timezone.utc)
    # Create active H1 monitor (watching breakout above 3610, ATR = 10.0, max_chase = 1.8*ATR = 18.0)
    analysis = {
        "symbol": "XAUUSD",
        "timeframe": "H1",
        "direction": "BUY",
        "price": 3606.0,
        "quality": 86,
        "confirmation": {"activated": False, "body_pct": 30},
        "chart_data": {
            "tf": "H1",
            "atr": 10.0,
            "activation_px": 3610.0,
            "invalidation_px": 3592.0,
            "candles": [
                {"datetime": "2026-04-21 08:00:00", "open": 3603.0, "high": 3608.0, "low": 3601.0, "close": 3606.0},
            ],
        },
    }
    mon_res = ta.evaluate_on_analysis_created(uid, analysis, now_dt=tue_09)
    assert mon_res["mode"] == "MONITORING"

    # A) Anti-Chase: Blow-off spike candle closes at 3640.0 (+30 beyond 3610 = 3.0x ATR > 1.8x ATR!)
    bot = _DummyBot()
    monkeypatch.setattr(
        market_data,
        "fetch_candles",
        lambda *a, **kw: {
            "values": [
                {"datetime": "2026-04-21 09:00:00", "open": 3606.0, "high": 3642.0, "low": 3605.0, "close": 3640.0},
            ]
        },
    )
    r_chase = asyncio.run(ta.poll_active_monitors(bot, now_dt=datetime(2026, 4, 21, 10, 2, 0, tzinfo=timezone.utc)))
    assert r_chase["anti_chase_blocked"] == 1
    assert r_chase["stage1_confirmed"] == 0
    assert r_chase["signals_fired"] == 0
    assert len(bot.messages) == 1 and "منع مطاردة السعر" in bot.messages[0]["text"]

    # Second poll on same spike candle MUST NOT spam duplicate alert!
    r_chase2 = asyncio.run(ta.poll_active_monitors(bot, now_dt=datetime(2026, 4, 21, 10, 4, 0, tzinfo=timezone.utc)))
    assert r_chase2["anti_chase_blocked"] == 1
    assert len(bot.messages) == 1

    # B) Clean H1 close (3618.0 -> 0.8x ATR beyond 3610) confirms Stage 1 -> advances to Stage 2
    monkeypatch.setattr(
        market_data,
        "fetch_candles",
        lambda *a, **kw: {
            "values": [
                {"datetime": "2026-04-21 10:00:00", "open": 3609.0, "high": 3619.0, "low": 3608.0, "close": 3618.0},
            ]
        },
    )
    r_s1 = asyncio.run(ta.poll_active_monitors(bot, now_dt=datetime(2026, 4, 21, 11, 2, 0, tzinfo=timezone.utc)))
    assert r_s1["stage1_confirmed"] == 1

    # C) High-Impact News Guard: USD CPI scheduled at 11:30 UTC blocks M15 final trigger at 11:16 UTC!
    news_guard.set_cached_events(
        [
            {
                "title": "US CPI m/m",
                "currency": "USD",
                "impact": "High",
                "dt_utc": "2026-04-21T11:30:00+00:00",
            }
        ],
        ts=9e12,
    )
    monkeypatch.setattr(
        market_data,
        "fetch_candles",
        lambda *a, **kw: {
            "values": [
                {"datetime": "2026-04-21 11:00:00", "open": 3616.0, "high": 3622.0, "low": 3615.0, "close": 3621.0},
            ]
        },
    )
    r_news = asyncio.run(ta.poll_active_monitors(bot, now_dt=datetime(2026, 4, 21, 11, 16, 0, tzinfo=timezone.utc)))
    assert r_news["paused_news"] == 1
    assert r_news["signals_fired"] == 0
    assert any("درع الأخبار القوية" in m["text"] for m in bot.messages)

    # D) After news window passes (12:16 UTC -> 46 min after CPI > 30 min guard), clean M15 fires private signal!
    monkeypatch.setattr(
        market_data,
        "fetch_candles",
        lambda *a, **kw: {
            "values": [
                {"datetime": "2026-04-21 12:00:00", "open": 3616.0, "high": 3622.0, "low": 3615.0, "close": 3621.0},
            ]
        },
    )
    r_fire = asyncio.run(ta.poll_active_monitors(bot, now_dt=datetime(2026, 4, 21, 12, 16, 0, tzinfo=timezone.utc)))
    assert r_fire["signals_fired"] == 1
    assert r_fire["paused_news"] == 0


def test_v851_admin_webapp_channel_edit_instruments_and_stop_periodic_charts(tmp_path, monkeypatch):
    import json
    from fysb.services import chart_channels
    from fysb.webapp import auth, server, templates

    _init_db(tmp_path, monkeypatch)
    monkeypatch.setattr(config, "BOT_TOKEN", "851000:TESTTOKEN_V851")
    monkeypatch.setattr(config, "ADMIN_ID", 999851)
    init_hdr = auth.build_init_data({"id": 999851, "username": "boss", "first_name": "Boss"}, config.BOT_TOKEN)
    hdrs = {"x-telegram-init-data": init_hdr}

    # 1. Add channel with single instrument XAUUSD
    cid = chart_channels.add_channel("@GoldCharts851", "Gold Only", "XAUUSD")
    assert chart_channels.get_channel(cid)["markets"] == "XAUUSD"

    # 2. Edit channel via Admin WebApp API (`op="update"`): multi-instruments + new title + disable periodic schedule
    body = json.dumps({
        "op": "update",
        "id": cid,
        "title": "VIP Gold & Crypto Charts",
        "markets": "XAUUSD,BTCUSD,EURUSD",
        "send_on_signal": True,
        "send_on_tracking": True,
        "send_on_schedule": False,
    }).encode("utf-8")
    code, _, raw = server.dispatch_request("POST", "/api/admin/chart-channel", hdrs, body)
    assert code == 200
    resp = json.loads(raw.decode("utf-8"))
    assert resp["ok"] is True
    ch = resp["channel"]
    assert ch["title"] == "VIP Gold & Crypto Charts"
    assert ch["markets"] == "XAUUSD,BTCUSD,EURUSD"
    assert int(ch["send_on_schedule"]) == 0
    assert int(ch["send_on_signal"]) == 1

    # 3. Stop periodic royal chart updates (15m -> 0m) & toggle chart_schedule_channels
    code_s, _, raw_s = server.dispatch_request(
        "POST", "/api/admin/chart-channel", hdrs,
        json.dumps({"op": "schedule", "minutes": 0}).encode("utf-8"),
    )
    assert code_s == 200
    assert db.get_setting_int("chart_schedule_minutes", 15) == 0

    code_t, _, raw_t = server.dispatch_request(
        "POST", "/api/admin/toggle", hdrs,
        json.dumps({"key": "chart_schedule_channels"}).encode("utf-8"),
    )
    assert code_t == 200
    assert json.loads(raw_t.decode("utf-8"))["enabled"] is True

    # 4. Verify Admin HTML contains channel editor + 1-tap stop periodic charts button + required strings
    html = templates.render_admin_panel_html()
    assert "openEditChartCh" in html
    assert "saveEditChartCh" in html
    assert "schedStopBtn" in html
    assert "إيقاف التحديث الدوري للشارتات الآن" in html
    assert "تعديل الأدوات" in html
    assert "قنوات الشارت فقط" in html


def test_v852_guaranteed_analysis_chart_and_clean_separation_from_true_analysis(tmp_path, monkeypatch):
    from types import SimpleNamespace
    from fysb.handlers import admin_channels, true_analysis_ui, user_pages

    _init_db(tmp_path, monkeypatch)
    monkeypatch.setattr(config, "ADMIN_ID", 999852)
    uid = 85201
    _ensure_subscriber(uid)

    # CRITICAL: Even if Admin turned OFF periodic charts (`charts_enabled = "0"`),
    # «تحليل الآن» in Channels & Users + «تحليل ForexYemeni الحقيقي» MUST ALWAYS send the luxury chart PNG!
    db.set_setting("charts_enabled", "0")

    # Deterministic live candles (60 H1 bars)
    t0 = datetime.now(timezone.utc).replace(minute=0, second=0, microsecond=0) - timedelta(hours=60)
    vals = []
    px = 3580.0
    for i in range(60):
        o = px
        c = px + (2.0 if i % 3 else -1.0)
        vals.append({
            "datetime": (t0 + timedelta(hours=i)).strftime("%Y-%m-%d %H:%M:%S"),
            "open": o, "high": max(o, c) + 2.0, "low": min(o, c) - 2.0, "close": c,
        })
        px = c
    monkeypatch.setattr(
        market_data, "fetch_candles",
        lambda *a, **kw: {"values": vals, "last_candle_iso": vals[-1]["datetime"], "provider": "TEST"},
    )

    # 1. Admin Channel «🚀 تحليل الآن» (`post_analysis`) MUST send PNG photo + text card!
    ch_id = admin_channels.add_channel("@TestAnlCh852", "VIP Analysis Channel", "public")
    with db.tx() as c:
        c.execute("UPDATE broadcast_channels SET markets='XAUUSD', timeframes='H1', strategies='ALL' WHERE id=?", (ch_id,))

    class _ChBot(_DummyBot):
        async def get_me(self):
            return SimpleNamespace(id=777)

        async def get_chat_member(self, chat_id, user_id):
            return SimpleNamespace(status="administrator", can_post_messages=True)

    ch_bot = _ChBot()
    res_ch = asyncio.run(admin_channels.post_analysis(ch_id, ch_bot))
    assert res_ch["ok"] is True
    assert len(ch_bot.photos) >= 1, "Channel «تحليل الآن» MUST send the luxury PNG chart even when charts_enabled=0!"
    assert len(ch_bot.messages) >= 1

    # 2. Dedicated «👑 تحليل ForexYemeni الحقيقي» wizard (`ta_anl` -> `ta_sym` -> `ta_tf` -> `ta_run`)
    ta_bot = _DummyBot()

    def _mk_q_ctx(bot_inst):
        msg = SimpleNamespace(
            chat_id=uid,
            message_id=10,
            reply_text=lambda text="", **kw: bot_inst.send_message(uid, text, **kw),
        )
        q = SimpleNamespace(
            from_user=SimpleNamespace(id=uid, username="sub852", full_name="Royal Sub"),
            message=msg,
            get_bot=lambda: bot_inst,
            answer=lambda *a, **k: asyncio.sleep(0),
        )
        ctx = SimpleNamespace(bot=bot_inst, user_data={})
        return q, ctx

    q, ctx = _mk_q_ctx(ta_bot)
    assert asyncio.run(true_analysis_ui.handle_user_true_analysis(q, uid, "ta_anl", ctx, ta_bot)) is True
    assert asyncio.run(true_analysis_ui.handle_user_true_analysis(q, uid, "ta_sym:XAUUSD", ctx, ta_bot)) is True
    assert asyncio.run(true_analysis_ui.handle_user_true_analysis(q, uid, "ta_tf:H1", ctx, ta_bot)) is True
    assert asyncio.run(true_analysis_ui.handle_user_true_analysis(q, uid, "ta_run:ALL", ctx, ta_bot)) is True
    assert len(ta_bot.photos) >= 1, "«تحليل ForexYemeni الحقيقي» MUST send the luxury PNG chart!"

    # Ensure a True Analysis monitor exists now (or active signal)
    mons_after_ta = ta.user_monitors(uid)
    assert len(mons_after_ta) >= 1

    # 3. Continuity Guard MUST trigger inside `u:ta_sym:XAUUSD` (True Analysis)...
    ta_bot2 = _DummyBot()
    q2, ctx2 = _mk_q_ctx(ta_bot2)
    assert asyncio.run(true_analysis_ui.handle_user_true_analysis(q2, uid, "ta_sym:XAUUSD", ctx2, ta_bot2)) is True
    assert any(("يراقب الشارت" in m["text"] or "ما زالا مستمر" in m["text"]) for m in ta_bot2.messages)

    # 4. ...but normal «🔬 تحليل الآن» (`u:anlsym:XAUUSD` -> `u:anlstrat:ALL`) MUST NEVER be blocked by Continuity Guard
    #    AND MUST ALWAYS send the luxury Analysis Chart PNG WITHOUT creating a duplicate True Analysis monitor!
    from fysb.services import subscriptions
    subscriptions.grant_subscription(uid, "VIP", override_days=30)
    norm_bot = _DummyBot()
    qn, ctxn = _mk_q_ctx(norm_bot)
    asyncio.run(user_pages.user_page(qn, "anlsym:XAUUSD", uid, ctxn))
    assert ctxn.user_data.get("anl_draft", {}).get("symbol") == "XAUUSD", "Normal «تحليل الآن» must NOT be blocked by True Analysis continuity guard!"
    ctxn.user_data["anl_draft"] = {"symbol": "XAUUSD", "tf": "H1"}
    asyncio.run(user_pages.user_page(qn, "anlstrat:ALL", uid, ctxn))
    assert len(norm_bot.photos) >= 1, "User «تحليل الآن» MUST send the luxury PNG chart even when charts_enabled=0!"
    assert len(ta.user_monitors(uid)) == len(mons_after_ta), "Normal «تحليل الآن» must NOT mix with True Analysis radar!"


