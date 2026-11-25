"""Comprehensive v7.7.0 Royal Auto-Bridge Regression Suite (Broker Demo & Live + Admin Governance)."""
from __future__ import annotations

import json
import time
from types import SimpleNamespace

import pytest

from fysb import config, db
from fysb.services import auto_bridge as ab, subscriptions, users as users_svc
from fysb.webapp import auth as wa_auth, server as wa_server, templates as wa_templates

ADMIN_UID = 999001
FREE_UID = 700101
VIP_UID = 700202
BOT_TOKEN = "123456:TEST-TOKEN-V7700"


def _hdr(uid: int) -> dict:
    init_data = wa_auth.build_init_data(
        {"id": uid, "username": f"u{uid}", "first_name": f"Trader {uid}"},
        BOT_TOKEN,
        auth_date=int(time.time()),
    )
    return {"X-Telegram-Init-Data": init_data}


@pytest.fixture()
def env(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "DB_FILE", tmp_path / "v7700.db")
    monkeypatch.setattr(config, "DATABASE_URL", "")
    monkeypatch.setattr(config, "ADMIN_ID", ADMIN_UID)
    monkeypatch.setattr(config, "BOT_TOKEN", BOT_TOKEN)
    db.init_db()
    users_svc.ensure_user(SimpleNamespace(id=ADMIN_UID, username="boss", full_name="Royal Boss"))
    users_svc.ensure_user(SimpleNamespace(id=FREE_UID, username="free_u", full_name="Free Member"))
    users_svc.ensure_user(SimpleNamespace(id=VIP_UID, username="vip_u", full_name="VIP Member"))
    subscriptions.grant_subscription(VIP_UID, "VIP", source="ADMIN", actor=ADMIN_UID)
    return tmp_path


def test_granular_admin_bridge_policy_off_all_plans_selected_and_per_user_overrides(env):
    # Default policy: PLANS (VIP,ROYAL)
    ok_f, _ = ab.user_can_copy(FREE_UID, "DEMO")
    ok_v, _ = ab.user_can_copy(VIP_UID, "DEMO")
    assert ok_f is False and ok_v is True

    # Mode: OFF -> stops EVERYONE (even VIP & Admin)
    st, _, _ = wa_server.dispatch_request(
        "POST", "/api/admin/bridge/policy", _hdr(ADMIN_UID),
        json.dumps({"access_mode": "OFF", "enabled": False}).encode(),
    )
    assert st == 200
    assert ab.user_can_copy(VIP_UID, "DEMO")[0] is False
    assert ab.user_can_copy(ADMIN_UID, "DEMO")[0] is False

    # Mode: ALL -> opens to FREE & VIP alike
    wa_server.dispatch_request(
        "POST", "/api/admin/bridge/policy", _hdr(ADMIN_UID),
        json.dumps({"access_mode": "ALL", "enabled": True}).encode(),
    )
    assert ab.user_can_copy(FREE_UID, "DEMO")[0] is True
    assert ab.user_can_copy(VIP_UID, "DEMO")[0] is True

    # Mode: SELECTED -> blocks everyone EXCEPT explicitly ALLOWed users
    wa_server.dispatch_request(
        "POST", "/api/admin/bridge/policy", _hdr(ADMIN_UID),
        json.dumps({"access_mode": "SELECTED", "enabled": True}).encode(),
    )
    assert ab.user_can_copy(FREE_UID, "DEMO")[0] is False
    assert ab.user_can_copy(VIP_UID, "DEMO")[0] is False

    # Admin explicitly ALLOWs FREE_UID via API
    st_r, _, b_r = wa_server.dispatch_request(
        "POST", "/api/admin/bridge/user-rule", _hdr(ADMIN_UID),
        json.dumps({"user_id": FREE_UID, "override_mode": "ALLOW", "note": "Beta Demo Tester"}).encode(),
    )
    assert st_r == 200 and json.loads(b_r)["override_mode"] == "ALLOW"
    assert ab.user_can_copy(FREE_UID, "DEMO")[0] is True
    assert ab.user_can_copy(VIP_UID, "DEMO")[0] is False

    # Mode: PLANS + explicit DENY on VIP_UID
    wa_server.dispatch_request(
        "POST", "/api/admin/bridge/policy", _hdr(ADMIN_UID),
        json.dumps({"access_mode": "PLANS", "allowed_plans": "VIP,ROYAL"}).encode(),
    )
    wa_server.dispatch_request(
        "POST", "/api/admin/bridge/user-rule", _hdr(ADMIN_UID),
        json.dumps({"user_id": VIP_UID, "override_mode": "DENY"}).encode(),
    )
    assert ab.user_can_copy(VIP_UID, "DEMO")[0] is False
    assert ab.user_can_copy(FREE_UID, "DEMO")[0] is True  # still explicitly ALLOWed

    # Live lock: allow_live=False blocks LIVE while preserving Broker DEMO
    wa_server.dispatch_request(
        "POST", "/api/admin/bridge/policy", _hdr(ADMIN_UID),
        json.dumps({"allow_live": False}).encode(),
    )
    assert ab.user_can_copy(FREE_UID, "DEMO")[0] is True
    assert ab.user_can_copy(FREE_UID, "LIVE")[0] is False

    # Kill switch halts everything immediately
    wa_server.dispatch_request(
        "POST", "/api/admin/bridge/policy", _hdr(ADMIN_UID),
        json.dumps({"kill_switch": True}).encode(),
    )
    assert ab.user_can_copy(FREE_UID, "DEMO")[0] is False


def test_encrypted_multiplatform_connect_mt5_ea_poll_ack_tp1_partial_be_and_dd_shield(env):
    # 1) VIP connects TradeLocker DEMO (encrypted at rest) + MT5 Bridge DEMO
    st_tl, _, b_tl = wa_server.dispatch_request(
        "POST", "/api/bridge/connect", _hdr(VIP_UID),
        json.dumps({
            "platform": "tradelocker", "account_mode": "DEMO",
            "creds": {"email": "vip@broker.com", "password": "SuperSecretPassword!", "server": "FundedNext-Demo"},
            "risk_pct": 1.0, "max_daily_dd_pct": 3.0, "partial_tp_be": True,
        }).encode(),
    )
    j_tl = json.loads(b_tl)
    assert st_tl == 200 and j_tl["ok"] is True
    assert "SuperSecretPassword!" not in b_tl.decode("utf-8")
    with db.conn() as c:
        raw_enc = c.execute("SELECT creds_enc FROM bridge_connections WHERE id=?", (j_tl["connection"]["id"],)).fetchone()["creds_enc"]
        assert raw_enc.startswith("ENC1:") and "SuperSecretPassword!" not in raw_enc

    st_mt5, _, b_mt5 = wa_server.dispatch_request(
        "POST", "/api/bridge/connect", _hdr(VIP_UID),
        json.dumps({
            "platform": "mt5_bridge", "account_mode": "DEMO",
            "risk_pct": 1.0, "max_daily_dd_pct": 3.0, "partial_tp_be": True,
            "symbol_suffix": "m",
        }).encode(),
    )
    j_mt5 = json.loads(b_mt5)
    assert st_mt5 == 200 and j_mt5["ok"] is True
    mt5_conn = j_mt5["connection"]
    bkey = mt5_conn["bridge_key"]
    assert bkey.startswith("fyb_")

    # 2) Download ready-to-compile ForexYemeni_Bridge.mq5
    st_ea, ct_ea, body_ea = wa_server.dispatch_request("GET", f"/api/bridge/ea-download?id={mt5_conn['id']}", _hdr(VIP_UID))
    ea_txt = body_ea.decode("utf-8")
    assert st_ea == 200 and "text/plain" in ct_ea
    assert bkey in ea_txt and "HandlePartialBE" in ea_txt and 'InpSymbolSuffix = "m"' in ea_txt

    # 3) Admin publishes a manual signal -> auto-queues to both DEMO bridge connections!
    st_pub, _, b_pub = wa_server.dispatch_request(
        "POST", "/api/admin/publish-signal", _hdr(ADMIN_UID),
        json.dumps({
            "symbol": "XAUUSD", "direction": "BUY", "timeframe": "M15", "strategy": "SMC",
            "entry": 2650.0, "sl": 2640.0, "tp1": 2662.0, "tp2": 2675.0, "tp3": 2690.0,
            "broadcast": False,
        }).encode(),
    )
    j_pub = json.loads(b_pub)
    assert st_pub == 200 and j_pub["ok"] is True and j_pub["bridge_queued_count"] == 2
    sig = j_pub["signal"]

    # 4) MT5 EA polls /api/bridge/poll -> gets order, updates live balance & heartbeat
    st_poll, _, b_poll = wa_server.dispatch_request("GET", f"/api/bridge/poll?key={bkey}&balance=20000&equity=20125", {})
    j_poll = json.loads(b_poll)
    assert st_poll == 200 and j_poll["ok"] is True and j_poll["paused"] is False
    assert len(j_poll["orders"]) == 1
    ord_id = j_poll["orders"][0]["id"]

    # 5) MT5 EA acknowledges execution via /api/bridge/ack
    st_ack, _, b_ack = wa_server.dispatch_request(
        "POST", "/api/bridge/ack", {},
        json.dumps({"key": bkey, "order_id": ord_id, "status": "EXECUTED", "ticket": "8844221", "detail": "Filled"}).encode(),
    )
    assert st_ack == 200 and json.loads(b_ack)["status"] == "EXECUTED"

    # 6) TP1 hits -> dispatch_tp1_partial_be enqueues PARTIAL_BE for MT5 EA!
    n_be = ab.dispatch_tp1_partial_be(sig)
    assert n_be >= 1
    st_p2, _, b_p2 = wa_server.dispatch_request("GET", f"/api/bridge/poll?key={bkey}", {})
    j_p2 = json.loads(b_p2)
    assert any(o["action_type"] == "PARTIAL_BE" for o in j_p2["orders"])

    # 7) Admin freezes MT5 connection -> EA poll reports paused=True
    wa_server.dispatch_request(
        "POST", "/api/admin/bridge/connection", _hdr(ADMIN_UID),
        json.dumps({"connection_id": mt5_conn["id"], "op": "freeze"}).encode(),
    )
    st_p3, _, b_p3 = wa_server.dispatch_request("GET", f"/api/bridge/poll?key={bkey}", {})
    assert json.loads(b_p3)["paused"] is True


def test_webapp_templates_contain_royal_auto_bridge_ui(env):
    u_html = wa_templates.render_user_miniapp_html()
    a_html = wa_templates.render_admin_panel_html()
    for token in ("demoBridgeSub", "saveBridgeConnection", "downloadMt5Ea", "selectBridgePlatform", "runBridgeTestTrade"):
        assert token in u_html
    for token in ("setBridgePolicyMode", "setAdminUserBridgeRule", "loadAdminBridge", "admBridgeConns", "saveAdminCtraderApp"):
        assert token in a_html


def test_ctrader_openapi_v2_full_lifecycle_test_trade_signal_and_tp1_partial_be(env, monkeypatch):
    # 1) Admin configures global cTrader App credentials once (encrypted at rest)
    st_app, _, b_app = wa_server.dispatch_request(
        "POST", "/api/admin/bridge/ctrader-app", _hdr(ADMIN_UID),
        json.dumps({"client_id": "ct_app_12345", "client_secret": "ct_secret_xyz"}).encode(),
    )
    assert st_app == 200 and json.loads(b_app)["configured"] is True

    # 2) Mock _CTraderClient JSON-WS protocol transport
    sent_calls = []

    class FakeCTrader:
        def __init__(self, account_mode="DEMO", timeout=10.0):
            self.account_mode = account_mode

        def __enter__(self):
            return self

        def __exit__(self, *a):
            return None

        def auth_session(self, cid, csec, tok, hint_account_id=""):
            sent_calls.append(("auth", self.account_mode, cid, tok))
            return 44556677, {"balance": 1000000, "moneyDigits": 2, "traderLogin": 998877, "brokerTitleShort": "ICMarkets"}

        def execute_market_order(self, ctid, symbol, direction, lots, entry=0.0, sl=0.0, tp=0.0, suffix="", comment=""):
            sent_calls.append(("order", ctid, symbol, direction, lots, sl, tp))
            return {"position_id": "555001", "symbol_name": f"{symbol}{suffix}", "volume": int(lots * 10_000_000), "fill_price": entry or 1.0850}

        def partial_close_and_be(self, ctid, position_id, be_price):
            sent_calls.append(("partial_be", ctid, position_id, be_price))
            return {"ok": True, "detail": "cTrader TP1: 50% إغلاق ✅ · BE ✅"}

    monkeypatch.setattr(ab, "_CTraderClient", FakeCTrader)

    # 3) VIP connects cTrader DEMO using ONLY access_token (inherits Admin App creds + auto-discovers ctid!)
    st_c, _, b_c = wa_server.dispatch_request(
        "POST", "/api/bridge/connect", _hdr(VIP_UID),
        json.dumps({"platform": "ctrader", "account_mode": "DEMO", "creds": {"access_token": "tok_demo_abc"}}).encode(),
    )
    j_c = json.loads(b_c)
    assert st_c == 200 and j_c["ok"] is True
    cid = j_c["connection"]["id"]

    # 4) Live connection test reads $10,000.00 balance and persists discovered ctid 44556677
    st_t, _, b_t = wa_server.dispatch_request(
        "POST", "/api/bridge/test", _hdr(VIP_UID),
        json.dumps({"connection_id": cid}).encode(),
    )
    j_t = json.loads(b_t)
    assert st_t == 200 and j_t["ok"] is True and j_t["balance"] == 10000.0 and j_t["ctid"] == 44556677

    # 5) 1-Tap 0.01 lot Test Trade executes directly on cTrader DEMO
    st_tt, _, b_tt = wa_server.dispatch_request(
        "POST", "/api/bridge/test-trade", _hdr(VIP_UID),
        json.dumps({"connection_id": cid, "symbol": "EURUSD", "direction": "BUY", "lots": 0.01}).encode(),
    )
    j_tt = json.loads(b_tt)
    assert st_tt == 200 and j_tt["ok"] is True and j_tt["status"] == "EXECUTED" and j_tt["position_id"] == "555001"

    # 6) Signal publish -> auto-executes on cTrader DEMO -> TP1 triggers real 50% partial close + BE!
    st_p, _, b_p = wa_server.dispatch_request(
        "POST", "/api/admin/publish-signal", _hdr(ADMIN_UID),
        json.dumps({
            "symbol": "XAUUSD", "direction": "BUY", "timeframe": "M15", "strategy": "SMC",
            "entry": 2650.0, "sl": 2640.0, "tp1": 2662.0, "tp2": 2675.0, "tp3": 2690.0, "broadcast": False,
        }).encode(),
    )
    sig = json.loads(b_p)["signal"]
    n_be = ab.dispatch_tp1_partial_be(sig)
    assert n_be == 1
    assert any(c[0] == "partial_be" and c[2] == 555001 and c[3] == 2650.0 for c in sent_calls)


def test_v772_native_bot_buttons_and_admin_home_panel_button(env, monkeypatch):
    from fysb.ui import keyboards as kb
    from fysb.handlers import auto_bridge_bot

    # 1) Admin execution hub & finance hub expose native Telegram buttons for Bridge, Promos, Digest
    exec_cbs = [b.callback_data for r in kb.admin_hub_execution().inline_keyboard for b in r]
    fin_cbs = [b.callback_data for r in kb.admin_hub_finance().inline_keyboard for b in r]
    assert "a:bridge" in exec_cbs
    assert "a:promos" in fin_cbs and "a:digest_now" in fin_cbs

    # 2) Admin menu stays 4 rows offline, and prepends Royal Admin WebApp button when live HTTPS URL is ready
    monkeypatch.setattr(config, "WEBAPP_URL", "")
    assert len(kb.admin_menu().inline_keyboard) == 4
    monkeypatch.setattr(config, "WEBAPP_URL", "https://live.example.com")
    m_live = kb.admin_menu()
    assert len(m_live.inline_keyboard) == 5
    assert "لوحة الإدارة" in m_live.inline_keyboard[0][0].text

    # 3) Luxury cTrader guide renders clean HTML
    guide = auto_bridge_bot.render_ctrader_luxury_guide_html()
    assert "openapi.ctrader.com" in guide and "Playground" in guide and "Access Token" in guide

    # 4) User Mini App template contains Admin Jump Bar
    u_html = wa_templates.render_user_miniapp_html()
    assert "adminJumpBar" in u_html and "openAdminPanelNow" in u_html


def test_v773_admin_clicking_u_home_opens_admin_panel_never_user_home(env):
    import asyncio
    from unittest.mock import AsyncMock
    from fysb.handlers import user_pages

    sent = []
    q = SimpleNamespace(
        from_user=SimpleNamespace(id=ADMIN_UID, username="boss", full_name="Royal Boss"),
        message=SimpleNamespace(
            chat_id=ADMIN_UID,
            message_id=1,
            edit_text=AsyncMock(),
            reply_text=AsyncMock(side_effect=lambda *a, **kw: sent.append((a, kw))),
        ),
        answer=AsyncMock(),
    )
    bot = SimpleNamespace(send_message=AsyncMock())
    ctx = SimpleNamespace(bot=bot, user_data={})

    asyncio.run(user_pages.user_page(q, "home", ADMIN_UID, ctx))
    assert len(sent) == 1
    args, kw = sent[0]
    rendered_text = args[0] if args else kw.get("text", "")
    rm = kw.get("reply_markup")
    assert "SUPER ADMIN" in rendered_text
    assert rm is not None
    rendered_cbs = [b.callback_data for r in rm.inline_keyboard for b in r if b.callback_data]
    assert "a:stats" in rendered_cbs and "a:hub:execution" in rendered_cbs
    assert "u:signow" not in rendered_cbs



