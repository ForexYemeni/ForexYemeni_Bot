"""Comprehensive v7.6.0 Royal Power Suite Regression Tests (#2, #3, #4, #10, #11, #14)."""
from __future__ import annotations

import asyncio
import json
import time
from types import SimpleNamespace

import pytest

from fysb import config, db
from fysb.engines import demo as demo_engine
from fysb.services import digest as digest_svc, promos as promos_svc, users as users_svc
from fysb.ui import share_cards
from fysb.webapp import auth as wa_auth, server as wa_server, templates as wa_templates

ADMIN_UID = 999001
USER_UID = 777001
BOT_TOKEN = "123456:TEST-TOKEN-V7600"


def _hdr(uid: int) -> dict:
    init_data = wa_auth.build_init_data(
        {"id": uid, "username": f"u{uid}", "first_name": f"User {uid}"},
        BOT_TOKEN,
        auth_date=int(time.time()),
    )
    return {"X-Telegram-Init-Data": init_data}


@pytest.fixture()
def env(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "DB_FILE", tmp_path / "v7600.db")
    monkeypatch.setattr(config, "DATABASE_URL", "")
    monkeypatch.setattr(config, "ADMIN_ID", ADMIN_UID)
    monkeypatch.setattr(config, "BOT_TOKEN", BOT_TOKEN)
    db.init_db()
    users_svc.ensure_user(SimpleNamespace(id=ADMIN_UID, username="boss", full_name="Royal Boss"))
    users_svc.ensure_user(SimpleNamespace(id=USER_UID, username="trader1", full_name="Ali Trader"))
    return tmp_path


def _seed_signal(symbol="XAUUSD", direction="BUY", entry=2650.0, sl=2640.0, tp1=2662.0, tp2=2675.0, tp3=2690.0,
                 status="TP2_HIT", pips_result=25.0) -> int:
    with db.tx() as c:
        cur = c.execute(
            """INSERT INTO signals(
                symbol,direction,entry,stop,tp1,tp2,tp3,targets,rr,score,strategy,timeframe,
                reason,source,status,pips_result,created_by,created_at
            ) VALUES(?,?,?,?,?,?,?,?,3.0,92,'SMC','M15','Test','MANUAL',?,?,?,?)""",
            (symbol, direction, entry, sl, tp1, tp2, tp3, f"{tp1},{tp2},{tp3}",
             status, pips_result, ADMIN_UID, config.now()),
        )
        return int(cur.lastrowid)


def test_02_branded_png_pnl_share_cards_signal_and_demo(env):
    sid = _seed_signal(status="TP3_HIT", pips_result=142.5)
    # Direct Pillow renderer check
    png = share_cards.render_pnl_share_card({
        "kind": "signal", "symbol": "XAUUSD", "direction": "BUY",
        "entry": 2650.0, "exit_price": 2690.0, "tp3": 2690.0,
        "tp_label": "TP3 HIT", "pips": 142.5, "rr": 4.0, "score": 95,
        "ref_link": "t.me/ForexYemeniBot?start=ref_777001",
    })
    assert png is not None and png[:8] == b"\x89PNG\r\n\x1a\n" and len(png) > 5000

    # Mini App API: Signal Share Card PNG
    st, ct, body = wa_server.dispatch_request("GET", f"/api/share-card/signal?id={sid}", _hdr(USER_UID))
    assert st == 200 and ct == "image/png" and body[:8] == b"\x89PNG\r\n\x1a\n"

    # Demo trade share card PNG (mock live price inside signal geometry so CI never depends on live XAU spot)
    from unittest.mock import patch
    from fysb.engines import market_data as _md
    with patch.object(_md, "fetch_price", return_value={"price": 2652.0, "fetched_at": config.now()}):
        pos_res = demo_engine.open_signal_interactive(USER_UID, sid)
    assert pos_res["ok"]
    pid = pos_res["position_id"]
    st2, ct2, body2 = wa_server.dispatch_request("GET", f"/api/share-card/demo?id={pid}", _hdr(USER_UID))
    assert st2 == 200 and ct2 == "image/png" and body2[:8] == b"\x89PNG\r\n\x1a\n"


def test_03_auto_weekly_monthly_performance_digest(env):
    _seed_signal("XAUUSD", "BUY", status="TP3_HIT", pips_result=85.0)
    _seed_signal("EURUSD", "SELL", status="TP1_HIT", pips_result=32.0)
    _seed_signal("GBPUSD", "BUY", status="SL_HIT", pips_result=-18.0)

    with db.tx() as c:
        c.execute("UPDATE signals SET closed_at=? WHERE status='TP1_HIT'", (config.now(),))
    d, cap, png = digest_svc.build_digest_bundle(days=7)
    assert d["total"] == 3 and d["wins"] == 2 and d["losses"] == 1
    assert d["net_pips"] == 99.0 and d["best_strategy"] == "SMC"
    assert "التقرير الأسبوعي" in cap
    assert png is not None and png[:8] == b"\x89PNG\r\n\x1a\n"

    # Admin API endpoints
    st, ct, body = wa_server.dispatch_request("GET", "/api/admin/digest-card?days=7", _hdr(ADMIN_UID))
    assert st == 200 and ct == "image/png" and body[:8] == b"\x89PNG\r\n\x1a\n"

    st2, _, body2 = wa_server.dispatch_request("POST", "/api/admin/digest", _hdr(ADMIN_UID), json.dumps({"days": 30}).encode())
    j2 = json.loads(body2)
    assert st2 == 200 and j2["ok"] is True and j2["stats"]["total"] == 3


def test_04_telegram_stars_and_percentage_promo_codes(env):
    # Create % promo via Admin Web App API
    st, _, body = wa_server.dispatch_request(
        "POST", "/api/admin/promo", _hdr(ADMIN_UID),
        json.dumps({"op": "create", "code": "ROYAL30", "discount_pct": 30, "max_uses": 5, "expires_days": 15}).encode(),
    )
    j = json.loads(body)
    assert st == 200 and j["ok"] is True and j["code"] == "ROYAL30" and j["discount_pct"] == 30

    v = promos_svc.validate_promo("royal30", user_id=USER_UID)
    assert v["ok"] is True and v["discount_pct"] == 30
    final_usd, saved_usd = promos_svc.apply_discount(100.0, 30)
    assert final_usd == 70.0 and saved_usd == 30.0
    assert promos_svc.plan_stars_price(70.0) == 3500

    # Simulate Telegram Stars PreCheckout + SuccessfulPayment
    from fysb.handlers import inputs
    answered = []

    async def _pcq_ans(**kw):
        answered.append(kw)

    upd_pcq = SimpleNamespace(
        pre_checkout_query=SimpleNamespace(invoice_payload="plan:VIP:ROYAL30", answer=_pcq_ans)
    )
    asyncio.run(inputs.on_pre_checkout(upd_pcq, SimpleNamespace()))
    assert answered and answered[0]["ok"] is True

    replies = []

    async def _reply(text, **kw):
        replies.append(text)

    async def _send_msg(**kw):
        pass

    upd_pay = SimpleNamespace(
        effective_user=SimpleNamespace(id=USER_UID, full_name="Ali Trader"),
        effective_message=SimpleNamespace(
            successful_payment=SimpleNamespace(
                invoice_payload="plan:VIP:ROYAL30",
                total_amount=3500,
                telegram_payment_charge_id="xtr_chg_999",
            ),
            reply_text=_reply,
        ),
    )
    ctx = SimpleNamespace(user_data={"active_promo": {"code": "ROYAL30", "discount_pct": 30}},
                          bot=SimpleNamespace(send_message=_send_msg))
    asyncio.run(inputs.on_successful_payment(upd_pay, ctx))
    assert any("نجوم تيليجرام" in r for r in replies)

    # Promo code should now be redeemed once and blocked from second use by same user
    v2 = promos_svc.validate_promo("ROYAL30", user_id=USER_UID)
    assert v2["ok"] is False
    with db.conn() as c:
        sub = c.execute("SELECT plan_code, source FROM subscriptions WHERE user_id=? AND status='ACTIVE'", (USER_UID,)).fetchone()
        assert sub and sub["plan_code"] == "VIP" and sub["source"] == "TELEGRAM_STARS"


def test_10_and_11_manual_signal_studio_signal_now_and_smart_demo_auto_copy(env):
    # Enable Smart Demo Auto-Copy + 50% Partial TP1 & BE for USER_UID via API
    st, _, body = wa_server.dispatch_request(
        "POST", "/api/demo/auto-copy", _hdr(USER_UID),
        json.dumps({"auto_trade": True, "partial_tp_be": True}).encode(),
    )
    j = json.loads(body)
    assert st == 200 and j["ok"] is True
    assert j["settings"]["auto_trade"] is True and j["settings"]["partial_tp_be"] is True

    # Publish a manual signal from Admin Web App Studio -> auto-copies to USER_UID's Demo account!
    # Mock live XAUUSD quote inside signal geometry so CI never depends on live Gold spot.
    from unittest.mock import patch
    from fysb.engines import market_data as _md
    with patch.object(_md, "fetch_price", return_value={"price": 2652.0, "fetched_at": config.now()}):
        st2, _, body2 = wa_server.dispatch_request(
            "POST", "/api/admin/publish-signal", _hdr(ADMIN_UID),
            json.dumps({
                "symbol": "XAUUSD", "direction": "BUY", "timeframe": "M15", "strategy": "SMC",
                "entry": 2650.0, "sl": 2640.0, "tp1": 2662.0, "tp2": 2675.0, "tp3": 2690.0,
                "broadcast": False,
            }).encode(),
        )
    j2 = json.loads(body2)
    assert st2 == 200 and j2["ok"] is True
    assert j2["signal"]["symbol"] == "XAUUSD" and j2["auto_copied_count"] == 0
    # A manual pending plan not delivered to the user must NOT auto-copy.
    # Activate + deliver explicitly before testing the paper-copy adapter.
    db.set_setting("global_kill_switch", "0")
    sig = dict(j2["signal"], status="RUNNING", filled_at=config.now())
    with db.tx() as c:
        c.execute("UPDATE signals SET status='RUNNING', filled_at=? WHERE id=?", (config.now(), sig["id"]))
        c.execute("INSERT INTO signal_deliveries(signal_id,user_id,delivered_at) VALUES(?,?,?)", (sig["id"], USER_UID, config.now()))
    with patch.object(_md, "fetch_price", return_value={"price": 2652., "fetched_at": config.now()}):
        assert len(demo_engine.auto_copy_signal_for_subscribers(sig)) == 1

    # Verify USER_UID now has an OPEN demo position for that exact signal
    with db.conn() as c:
        pos = c.execute("SELECT * FROM demo_positions WHERE user_id=? AND signal_id=? AND status='OPEN'",
                        (USER_UID, j2["signal"]["id"])).fetchone()
        assert pos is not None and pos["symbol"] == "XAUUSD"

    # Calling auto_copy again must NOT duplicate
    with patch.object(_md, "fetch_price", return_value={"price": 2652.0, "fetched_at": config.now()}):
        dup_res = demo_engine.auto_copy_signal_for_subscribers(j2["signal"])
    assert dup_res == []

    # The explicit delivery consumed FREE daily quota; upgrade this endpoint
    # fixture rather than expecting a second free delivery.
    from fysb.services import subscriptions
    subscriptions.grant_subscription(USER_UID, "VIP", source="ADMIN", actor=ADMIN_UID)
    # User Mini App: /api/signal-now returns active signal cleanly
    st3, _, body3 = wa_server.dispatch_request(
        "POST", "/api/signal-now", _hdr(USER_UID),
        json.dumps({"symbol": "XAUUSD", "timeframe": "M15"}).encode(),
    )
    j3 = json.loads(body3)
    assert st3 == 200 and j3["ok"] is True and j3["signal"]["symbol"] == "XAUUSD", j3


def test_14_admin_users_page_management_and_6page_template_integrity(env):
    # Search user via Admin Web App API
    st, _, body = wa_server.dispatch_request("GET", "/api/admin/users?q=trader1", _hdr(ADMIN_UID))
    j = json.loads(body)
    assert st == 200 and j["ok"] is True and j["count"] == 1
    assert j["users"][0]["user_id"] == USER_UID

    # Upgrade user to ROYAL for 60 days
    st2, _, body2 = wa_server.dispatch_request(
        "POST", "/api/admin/user-action", _hdr(ADMIN_UID),
        json.dumps({"user_id": USER_UID, "op": "set_plan", "plan_code": "ROYAL", "days": 60}).encode(),
    )
    assert st2 == 200 and json.loads(body2)["ok"] is True

    # Ban -> Unban -> Reset Quota
    for op in ("ban", "unban", "reset_quota"):
        st_x, _, b_x = wa_server.dispatch_request(
            "POST", "/api/admin/user-action", _hdr(ADMIN_UID),
            json.dumps({"user_id": USER_UID, "op": op}).encode(),
        )
        assert st_x == 200 and json.loads(b_x)["ok"] is True

    # Verify 6-page Admin Web App & upgraded User Mini App HTML integrity
    a_html = wa_templates.render_admin_panel_html()
    for pid in ("apane-home", "apane-signals", "apane-users", "apane-finance", "apane-channels", "apane-analytics"):
        assert f'id="{pid}"' in a_html
    for tid in ("atab-home", "atab-signals", "atab-users", "atab-finance", "atab-channels", "atab-analytics"):
        assert f'id="{tid}"' in a_html
    for fn in ("publishAdminSignal", "loadAdminUsers", "createAdminPromo", "sendDigestNow", "previewAdminPng"):
        assert fn in a_html

    u_html = wa_templates.render_user_miniapp_html()
    for fn in ("runSignalNow", "toggleDemoAutoCopy", "openShareCard"):
        assert fn in u_html
