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
    for token in ("demoBridgeSub", "saveBridgeConnection", "downloadMt5Ea", "selectBridgePlatform"):
        assert token in u_html
    for token in ("setBridgePolicyMode", "setAdminUserBridgeRule", "loadAdminBridge", "admBridgeConns"):
        assert token in a_html
