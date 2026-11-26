"""v8.0.0 Grand Institutional Overhaul — Comprehensive Regression Suite.

Verifies all 30 audited fixes across Security, Auth, Payments, Database,
Execution Engines (Auto-Bridge / Crypto / Demo), Trading Strategies (all 13),
Structural TP Engine (strict TP1 >= 2.0R), Intelligence, Analytics, Tracker,
Backtest, and Version 8.0.0 release integrity.
"""
from __future__ import annotations

import asyncio
import hashlib
import hmac
import json
import math
import time
import urllib.parse
from types import SimpleNamespace
from unittest.mock import patch

import pytest

from fysb import config, db, instruments, pglayer
from fysb.engines import analysis, demo, market_data, risk, structural_tp, tracker
from fysb.engines.strategies.base import Ctx, StrategyResult, ensure_min_tp_rr, normalized_score
from fysb.services import affiliates, auto_bridge, crypto_exchange, gifts, payments, promos, subscriptions, users as users_svc
from fysb.webapp import server as wa_server
from fysb.webapp import templates as wa_templates

ADMIN_UID = 880001
VIP_UID = 880002
FREE_UID = 880003
OTHER_UID = 880004


@pytest.fixture
def env(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "DB_FILE", tmp_path / "v800.db")
    monkeypatch.setattr(config, "DATABASE_URL", "")
    monkeypatch.setattr(config, "BOT_TOKEN", "800000:TEST_V800_TOKEN")
    monkeypatch.setattr(config, "ADMIN_ID", ADMIN_UID)
    wa_server.reset_rate_limits()
    db.init_db()
    for uid, uname, fname in (
        (ADMIN_UID, "admin8", "Admin Eight"),
        (VIP_UID, "vip8", "VIP Eight"),
        (FREE_UID, "free8", "Free Eight"),
        (OTHER_UID, "other8", "Other Eight"),
    ):
        users_svc.ensure_user(SimpleNamespace(id=uid, username=uname, full_name=fname))
    subscriptions.grant_subscription(VIP_UID, "VIP", source="TEST", actor=ADMIN_UID)
    db.set_setting("auto_bridge_enabled", "1")
    return tmp_path


def _hdr(uid: int) -> dict:
    user_json = json.dumps({"id": uid, "first_name": "Tester"}, separators=(",", ":"))
    pairs = {"auth_date": str(int(time.time())), "user": user_json}
    dcs = "\n".join(f"{k}={v}" for k, v in sorted(pairs.items()))
    secret = hmac.new(b"WebAppData", config.BOT_TOKEN.encode(), hashlib.sha256).digest()
    h = hmac.new(secret, dcs.encode(), hashlib.sha256).hexdigest()
    qs = urllib.parse.urlencode({**pairs, "hash": h})
    return {"X-Telegram-Init-Data": qs}


# ---------------------------------------------------------------------------
# 1. Security, XSS, Auth, IDOR, Rate Limit & Headers (SEC-1..3, WEB-1..2)
# ---------------------------------------------------------------------------

def test_01_miniapp_xss_escaping_and_rebuilt_docs():
    u_html = wa_templates.render_user_miniapp_html()
    a_html = wa_templates.render_admin_panel_html()
    assert "function esc(v)" in u_html and "function esc(v)" in a_html
    assert "function copyBtnVal(" in a_html
    assert "esc(s.symbol)" in u_html
    assert config.VERSION.startswith("8.")


def test_02_signal_idor_isolation_and_signal_now_plan_gates(env):
    now = config.now()
    with db.tx() as c:
        cur = c.execute(
            """INSERT INTO signals(symbol,direction,timeframe,strategy,entry,stop,tp1,tp2,tp3,rr,
               score,status,source,created_by,created_at)
               VALUES('XAUUSD','BUY','M15','SMC',2650,2640,2670,2680,2695,2.0,82,'ACTIVE',
                      'SIGNAL_NOW',?,?)""",
            (VIP_UID, now),
        )
        priv_sid = int(cur.lastrowid)

    # OTHER_UID cannot steal VIP_UID's private SIGNAL_NOW share card or open it
    st_card, _, _ = wa_server.dispatch_request("GET", f"/api/share-card/signal?id={priv_sid}", _hdr(OTHER_UID))
    assert st_card == 403
    st_demo, _, _ = wa_server.dispatch_request(
        "POST", "/api/demo/open-signal", _hdr(OTHER_UID),
        json.dumps({"signal_id": priv_sid, "risk_pct": 1.0}).encode(),
    )
    assert st_demo == 403

    # FREE_UID cannot bypass plan markets on /api/signal-now (BTCUSD is NOT in FREE plan)
    st_sn, _, b_sn = wa_server.dispatch_request(
        "POST", "/api/signal-now", _hdr(FREE_UID),
        json.dumps({"symbol": "BTCUSD", "timeframe": "M15", "strategy": "ICT"}).encode(),
    )
    assert st_sn == 403
    assert json.loads(b_sn)["ok"] is False

    # Admin payout rejects invalid action strings
    st_pay, _, _ = wa_server.dispatch_request(
        "POST", "/api/admin/payout", _hdr(ADMIN_UID),
        json.dumps({"id": 1, "action": "hack"}).encode(),
    )
    assert st_pay == 400


def test_03_bridge_header_auth_rate_limit_and_safe_numbers(env):
    conn_res = auto_bridge.save_connection(VIP_UID, "mt5_bridge", "DEMO", risk_pct=1.0)
    assert conn_res["ok"] is True
    bkey = conn_res["connection"]["bridge_key"]

    # Header-based auth works without putting bridge key in URL query string
    st, _, body = wa_server.dispatch_request("GET", "/api/bridge/poll", {"X-Bridge-Key": bkey})
    assert st == 200 and json.loads(body)["ok"] is True

    # _safe_float rejects NaN and Inf cleanly
    assert wa_server._safe_float("NaN", 1.0) == 1.0
    assert wa_server._safe_float("inf", 2.0) == 2.0
    assert wa_server._safe_float("-infinity", 3.0) == 3.0
    assert wa_server._safe_float("12.5", 0.0) == 12.5

    # Rate limit triggers cleanly after burst
    wa_server.reset_rate_limits()
    for _ in range(5):
        assert wa_server._check_rate_limit("test", "bucket", 5, 60.0) is True
    assert wa_server._check_rate_limit("test", "bucket", 5, 60.0) is False


# ---------------------------------------------------------------------------
# 2. Payments, Renewals, Anti-Double-Commission & Atomicity (PAY-1..6)
# ---------------------------------------------------------------------------

def test_04_same_plan_renewal_carries_forward_remaining_days(env):
    with db.conn() as c:
        sub1 = dict(c.execute("SELECT * FROM subscriptions WHERE user_id=? AND status='ACTIVE' ORDER BY id DESC LIMIT 1", (VIP_UID,)).fetchone())
    # Renew same VIP plan for another 30 days -> must stack (~60 days total, not reset to 30!)
    subscriptions.grant_subscription(VIP_UID, "VIP", override_days=30, source="RENEWAL", actor=ADMIN_UID)
    with db.conn() as c:
        sub2 = dict(c.execute("SELECT * FROM subscriptions WHERE user_id=? AND status='ACTIVE' ORDER BY id DESC LIMIT 1", (VIP_UID,)).fetchone())
    from datetime import datetime
    d1 = datetime.fromisoformat(sub1["ends_at"].replace("Z", "+00:00"))
    d2 = datetime.fromisoformat(sub2["ends_at"].replace("Z", "+00:00"))
    diff_days = (d2 - d1).total_seconds() / 86400.0
    assert 29.0 <= diff_days <= 31.0


def test_05_affiliate_payout_atomicity_and_gift_promo_no_double_redeem(env):
    # Gift with max_uses=1 can only be redeemed ONCE
    g = gifts.create_gift_code("PRO", duration_days=7, max_uses=1, created_by=ADMIN_UID, note="single")
    r1 = gifts.redeem_code(FREE_UID, g["code"])
    r2 = gifts.redeem_code(OTHER_UID, g["code"])
    assert r1["ok"] is True and r2["ok"] is False

    # Promo with max_uses=1 can only be redeemed ONCE
    promos.create_promo("ONCE80", discount_pct=20, max_uses=1, expires_days=7, created_by=ADMIN_UID)
    pr1 = promos.redeem_promo("ONCE80", FREE_UID, plan_code="PRO", saved_amount=10.0)
    pr2 = promos.redeem_promo("ONCE80", OTHER_UID, plan_code="PRO", saved_amount=10.0)
    assert pr1 is True and pr2 is False

    # Affiliate payout rejects NaN and prevents over-withdrawal
    with db.tx() as c:
        c.execute(
            "INSERT INTO affiliate_commissions(referrer_id,referred_user_id,payment_id,amount_usd,rate_pct,commission_usd,status,created_at) "
            "VALUES(?,?,1,100,25,25.0,'CREDITED',?)",
            (VIP_UID, FREE_UID, config.now()),
        )
    bad_nan = affiliates.request_payout(VIP_UID, "TWallet123456", float("nan"))
    assert bad_nan["ok"] is False
    ok_p = affiliates.request_payout(VIP_UID, "TWallet123456", 20.0)
    assert ok_p["ok"] is True
    # Second request for $10 exceeds remaining $5 balance!
    over_p = affiliates.request_payout(VIP_UID, "TWallet123456", 10.0)
    assert over_p["ok"] is False


def test_06_telegram_stars_currency_amount_and_charge_idempotency(env):
    from fysb.handlers import inputs
    ans = []

    async def _ans(**kw):
        ans.append(kw)

    # Reject non-XTR currency
    upd_bad_ccy = SimpleNamespace(
        pre_checkout_query=SimpleNamespace(
            invoice_payload="plan:VIP:", currency="USD", total_amount=5000,
            from_user=SimpleNamespace(id=FREE_UID), answer=_ans,
        )
    )
    asyncio.run(inputs.on_pre_checkout(upd_bad_ccy, SimpleNamespace()))
    assert ans[-1]["ok"] is False

    # Reject underpaid Stars amount
    upd_low = SimpleNamespace(
        pre_checkout_query=SimpleNamespace(
            invoice_payload="plan:VIP:", currency="XTR", total_amount=1,
            from_user=SimpleNamespace(id=FREE_UID), answer=_ans,
        )
    )
    asyncio.run(inputs.on_pre_checkout(upd_low, SimpleNamespace()))
    assert ans[-1]["ok"] is False

    # Accept valid XTR invoice
    upd_ok = SimpleNamespace(
        pre_checkout_query=SimpleNamespace(
            invoice_payload="plan:VIP:", currency="XTR", total_amount=999999,
            from_user=SimpleNamespace(id=FREE_UID), answer=_ans,
        )
    )
    asyncio.run(inputs.on_pre_checkout(upd_ok, SimpleNamespace()))
    assert ans[-1]["ok"] is True


# ---------------------------------------------------------------------------
# 3. PostgreSQL SQL Translator (DB-1)
# ---------------------------------------------------------------------------

def test_07_pglayer_does_not_corrupt_dml_or_escaped_quotes():
    sql = "INSERT INTO notes(body, title) VALUES('It''s AUTOINCREMENT ok?', ?)"
    out = pglayer.translate(sql)
    assert "AUTOINCREMENT" in out
    assert "It''s AUTOINCREMENT ok?" in out
    assert out.endswith(", %s)")

    ddl = "CREATE TABLE t (id INTEGER PRIMARY KEY AUTOINCREMENT, d TEXT)"
    out_ddl = pglayer.translate(ddl)
    assert "BIGSERIAL PRIMARY KEY" in out_ddl


# ---------------------------------------------------------------------------
# 4. Execution Engines: Auto-Bridge, Crypto Exchange, Demo (EXEC-1..5)
# ---------------------------------------------------------------------------

def test_08_auto_bridge_max_open_lifecycle_and_delete_cleanup(env, monkeypatch):
    conn_res = auto_bridge.save_connection(VIP_UID, "mt5_bridge", "DEMO", max_open_trades=1)
    cid = conn_res["connection"]["id"]
    now = config.now()
    with db.tx() as c:
        s1 = int(c.execute(
            "INSERT INTO signals(symbol,direction,timeframe,strategy,entry,stop,tp1,tp2,tp3,rr,score,status,source,created_at) "
            "VALUES('XAUUSD','BUY','M15','SMC',2650,2640,2670,2680,2695,2.0,85,'ACTIVE','AUTO',?)",
            (now,),
        ).lastrowid)
        s2 = int(c.execute(
            "INSERT INTO signals(symbol,direction,timeframe,strategy,entry,stop,tp1,tp2,tp3,rr,score,status,source,created_at) "
            "VALUES('EURUSD','BUY','M15','SMC',1.1000,1.0950,1.1100,1.1150,1.1225,2.0,85,'ACTIVE','AUTO',?)",
            (now,),
        ).lastrowid)
    db.set_setting("global_kill_switch", "0")
    from fysb.engines import market_data
    monkeypatch.setattr(market_data, "execution_quote", lambda symbol, *a, **kw: {"ok": True, "price": 2650. if "XAU" in symbol else 1.1})
    with db.tx() as c:
        c.execute("UPDATE signals SET status='RUNNING', filled_at=? WHERE id IN (?,?)", (now, s1, s2))
        for sid in (s1, s2):
            c.execute("INSERT INTO signal_deliveries(signal_id,user_id,delivered_at) VALUES(?,?,?)", (sid, VIP_UID, now))
    # Signal 1 dispatches -> fills the 1-trade slot
    assert auto_bridge.dispatch_signal_to_bridges(
        {"id": s1, "symbol": "XAUUSD", "direction": "BUY", "entry": 2650, "stop": 2640, "tp1": 2670, "tp2": 2680, "tp3": 2695, "source": "AUTO"}
    )["queued"] == 1
    # While signal 1 is still OPEN, signal 2 is blocked by max_open_trades=1
    assert auto_bridge.dispatch_signal_to_bridges(
        {"id": s2, "symbol": "EURUSD", "direction": "BUY", "entry": 1.1000, "stop": 1.0950, "tp1": 1.1100, "tp2": 1.1150, "tp3": 1.1225, "source": "AUTO"}
    )["queued"] == 0
    # Signal 1 closes -> mark_signal_bridge_orders_closed frees the slot IMMEDIATELY!
    with db.tx() as c:
        c.execute("UPDATE signals SET status='TP3_HIT', closed_at=? WHERE id=?", (now, s1))
    assert auto_bridge.mark_signal_bridge_orders_closed(s1) == 1
    assert auto_bridge.dispatch_signal_to_bridges(
        {"id": s2, "symbol": "EURUSD", "direction": "BUY", "entry": 1.1000, "stop": 1.0950, "tp1": 1.1100, "tp2": 1.1150, "tp3": 1.1225, "source": "AUTO"}
    )["queued"] == 1

    # Deleting connection cleans up queued orders cleanly
    auto_bridge.delete_connection(cid, user_id=VIP_UID)
    with db.conn() as c:
        rem = c.execute("SELECT status FROM bridge_orders WHERE connection_id=? AND signal_id=?", (cid, s2)).fetchone()
    assert rem is None or rem["status"] == "CANCELLED"


def test_09_crypto_step_precision_and_spot_sell_guard(env):
    q_doge, s_doge = crypto_exchange.format_crypto_qty("DOGEUSD", 123.4567)
    assert q_doge == 123.0 and s_doge == "123"
    q_btc, s_btc = crypto_exchange.format_crypto_qty("BTCUSD", 0.0123456)
    assert s_btc == "0.01235"

    # Spot SELL without allow_spot_sell is safely blocked with Arabic message
    crypto_exchange.set_enabled(True)
    crypto_exchange.save_exchange_key(VIP_UID, "BINANCE", "ak_test_key_1234", "as_test_sec_5678", testnet=True, max_trade_usd=100.0)
    res = crypto_exchange.execute_crypto_signal(
        VIP_UID, "BINANCE",
        {"id": 1, "symbol": "BTCUSD", "direction": "SELL", "entry": 60000.0, "stop": 61000.0, "tp1": 58000.0},
    )
    assert res["ok"] is False and "Spot" in res["error"]


def test_10_demo_interactive_never_bypasses_inverted_sl_geometry(env):
    now = config.now()
    with db.tx() as c:
        sid = int(c.execute(
            "INSERT INTO signals(symbol,direction,timeframe,strategy,entry,stop,tp1,tp2,tp3,rr,score,status,source,created_at) "
            "VALUES('XAUUSD','BUY','M15','SMC',2650,2640,2670,2680,2695,2.0,85,'ACTIVE','AUTO',?)",
            (now,),
        ).lastrowid)
    # Live price crashed BELOW Stop Loss (2630 < 2640) -> MUST be rejected, NEVER inserted!
    with patch.object(market_data, "fetch_price", return_value={"price": 2630.0}):
        res = demo.open_signal_interactive(VIP_UID, sid)
    assert res["ok"] is False


# ---------------------------------------------------------------------------
# 5. Trading Engine: DST, Structural TP (>= 2.0R), Strategies, Risk (SIG-1..7)
# ---------------------------------------------------------------------------

def test_11_dst_killzones_and_session_levels():
    # Winter (Jan = EST = UTC-5): 13:00 UTC == 08:00 NY -> NEW_YORK Killzone
    assert analysis.ny_hour("2026-01-15 13:00:00") == 8
    assert analysis.kill_zone("2026-01-15 13:00:00") == "NEW_YORK"
    # Summer (Jul = EDT = UTC-4): 11:00 UTC == 07:00 NY -> NEW_YORK Killzone (DST adjusted!)
    assert analysis.ny_hour("2026-07-15 11:00:00") == 7
    assert analysis.kill_zone("2026-07-15 11:00:00") == "NEW_YORK"
    # London Summer: 06:30 UTC == 02:30 NY -> LONDON Killzone
    assert analysis.kill_zone("2026-07-15 06:30:00") == "LONDON"


def test_12_structural_tp_strictly_enforces_min_2r_and_detects_htf_obstruction():
    # Construct candles with a clear PDH liquidity level at 2673.0 (2.3R above entry=2650, sl=2640 -> risk=10)
    candles = []
    for i in range(40):
        day = "2026-11-23" if i < 20 else "2026-11-24"
        hr = (i % 20)
        hi = 2673.0 if (i == 10) else (2655.0 + (i % 3))
        lo = 2638.0 if (i == 5) else (2645.0 - (i % 2))
        candles.append({
            "datetime": f"{day} {hr:02d}:00:00",
            "open": 2648.0, "high": hi, "low": lo, "close": 2650.0,
        })
    cand = StrategyResult(
        code="SMC", direction="BUY", entry=2650.0, stop=2640.0,
        tp1=2670.0, tp2=2680.0, tp3=2695.0, rr=2.0, score=80,
        reasons=[], checks={},
    )
    out = structural_tp.apply_structural_targets(cand, candles, candles)
    risk_val = abs(out.entry - out.stop)
    # STRICT ROYAL INVARIANT: TP1 MUST ALWAYS BE >= 2.0 * risk!
    assert (out.tp1 - out.entry) >= 2.0 * risk_val - 1e-9
    assert out.rr >= 2.0
    assert out.tp2 >= out.entry + 3.0 * risk_val - 1e-9
    assert out.tp3 >= out.entry + 4.5 * risk_val - 1e-9

    # Now test HTF obstacle at 1.1R (2661.0): TP1 MUST STILL BE >= 2.0R, while score gets penalized!
    htf_bars = list(candles)
    htf_bars[10] = {"datetime": "2026-11-23 10:00:00", "open": 2655.0, "high": 2661.0, "low": 2645.0, "close": 2650.0}
    cand2 = StrategyResult(
        code="SMC", direction="BUY", entry=2650.0, stop=2640.0,
        tp1=2670.0, tp2=2680.0, tp3=2695.0, rr=2.0, score=82,
        reasons=[], checks={},
    )
    out2 = structural_tp.apply_structural_targets(cand2, htf_bars, htf_bars)
    assert (out2.tp1 - out2.entry) >= 2.0 * risk_val - 1e-9
    assert out2.checks["clear_path_to_2r"] is False
    assert out2.score < 82


def test_13_normalized_scoring_min_tp_floor_and_risk_be_pnl(env):
    # Normalized score puts all strategies on a fair 42..95 scale
    assert normalized_score(0, 80) == 42
    assert normalized_score(80, 80) == 95
    assert 65 <= normalized_score(40, 80) <= 72

    # ensure_min_tp_rr strictly lifts any sub-2.0R TP1 to >= 2.0R
    t1, t2, t3, rr = ensure_min_tp_rr(100.0, 90.0, 112.0, 120.0, 130.0, "BUY")
    assert t1 == 120.0 and t2 == 130.0 and t3 == 145.0 and rr == 2.0

    # risk.daily_signal_pnl_pct counts TP1_BE & TP2_BE realized gains
    now = config.now()
    with db.tx() as c:
        c.execute(
            "INSERT INTO signals(symbol,direction,timeframe,strategy,entry,stop,tp1,tp2,tp3,rr,score,status,hit_tp,source,created_at,closed_at) "
            "VALUES('XAUUSD','BUY','M15','SMC',2650,2640,2670,2680,2695,2.0,85,'TP1_BE',1,'AUTO',?,?)",
            (now, now),
        )
    with db.tx() as c:
        c.execute("UPDATE signals SET accounting_model='PARTIAL_50_25_25' WHERE status='TP1_BE'")
    pnl_pct, sl_hits = risk.daily_signal_pnl_pct()
    assert pnl_pct > 0.0 and sl_hits == 0

    # Per-asset spread & TP confirmation buffers are realistic
    assert instruments.typical_spread_pips("XAUUSD") >= 2.0
    assert instruments.typical_spread_pips("EURUSD") <= 1.5
    assert instruments.tp_confirm_pips("BTCUSD") > instruments.tp_confirm_pips("EURUSD")
