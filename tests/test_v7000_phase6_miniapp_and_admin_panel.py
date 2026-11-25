"""v7.0.0 Phase 6 tests: Ultra-Luxury Telegram Mini App (User) + Super Admin Web Panel
inside Telegram — official HMAC-SHA256 `initData` auth, ADMIN_ID guard, REST APIs,
graceful WEBAPP_URL HTTPS gating, and real live TCP server round-trip."""
import asyncio
import json
import time
import pytest

from fysb import config, db
from fysb.services import affiliates, payments, subscriptions
from fysb.ui import keyboards as kb
from fysb.webapp import auth as wa_auth
from fysb.webapp import server as wa_server

TEST_BOT_TOKEN = "123456789:AABBCCDDEEFFGGHHIIJJKKLLMMNNOOPPQQR"
TEST_ADMIN_ID = 999001
TEST_USER_ID = 999002


@pytest.fixture
def isolated_db(tmp_path, monkeypatch):
    p = tmp_path / "phase6.db"
    monkeypatch.setattr(config, "DB_FILE", p)
    monkeypatch.setattr(config, "DATABASE_URL", "")
    monkeypatch.setattr(config, "BOT_TOKEN", TEST_BOT_TOKEN)
    monkeypatch.setattr(config, "ADMIN_ID", TEST_ADMIN_ID)
    db.init_db()
    subscriptions._user_plan_cache.clear()
    with db.conn() as c:
        for uid, name in ((TEST_ADMIN_ID, "Super Admin"), (TEST_USER_ID, "Royal User")):
            c.execute(
                "INSERT OR IGNORE INTO users(user_id,username,full_name,joined_at,notifications,is_blocked) VALUES(?,?,?,?,1,0)",
                (uid, f"u{uid}", name, config.now()),
            )
    return tmp_path


def test_version_and_webapp_https_gating(isolated_db, monkeypatch):
    assert tuple(int(x) for x in config.VERSION.split(".")) >= (7, 0, 0)
    monkeypatch.setattr(config, "WEBAPP_URL", "")
    assert config.webapp_ready() is False

    # HTTP is NOT valid for Telegram Mini Apps — must require HTTPS
    monkeypatch.setattr(config, "WEBAPP_URL", "http://example.com")
    assert config.webapp_ready() is False

    monkeypatch.setattr(config, "WEBAPP_URL", "https://royal.forexyemeni.com")
    monkeypatch.setattr(config, "WEBAPP_ENABLED", True)
    assert config.webapp_ready() is True

    btn = kb.webapp_btn("📱 Mini App", "https://royal.forexyemeni.com/")
    assert btn.web_app is not None
    assert btn.web_app.url == "https://royal.forexyemeni.com/"


def test_telegram_init_data_hmac_verification(isolated_db):
    now_ts = int(time.time())
    u_init = wa_auth.build_init_data({"id": TEST_USER_ID, "first_name": "Ali"}, TEST_BOT_TOKEN, auth_date=now_ts)
    p = wa_auth.verify_init_data(u_init, bot_token=TEST_BOT_TOKEN, now_ts=now_ts)
    assert p is not None and p["user_id"] == TEST_USER_ID and p["is_admin"] is False

    a_init = wa_auth.build_init_data({"id": TEST_ADMIN_ID, "first_name": "Admin"}, TEST_BOT_TOKEN, auth_date=now_ts)
    pa = wa_auth.verify_init_data(a_init, bot_token=TEST_BOT_TOKEN, now_ts=now_ts)
    assert pa is not None and pa["user_id"] == TEST_ADMIN_ID and pa["is_admin"] is True

    # Tampered initData MUST be rejected
    tampered = u_init.replace("Ali", "Evil")
    assert wa_auth.verify_init_data(tampered, bot_token=TEST_BOT_TOKEN, now_ts=now_ts) is None

    # Expired auth_date (replay attack) MUST be rejected
    old_init = wa_auth.build_init_data({"id": TEST_USER_ID}, TEST_BOT_TOKEN, auth_date=now_ts - 100000)
    assert wa_auth.verify_init_data(old_init, bot_token=TEST_BOT_TOKEN, now_ts=now_ts) is None

    # Wrong bot token MUST be rejected
    assert wa_auth.verify_init_data(u_init, bot_token="wrong:token", now_ts=now_ts) is None


def test_miniapp_and_admin_panel_routes_end_to_end(isolated_db):
    u_hdr = {"X-Telegram-Init-Data": wa_auth.build_init_data({"id": TEST_USER_ID}, TEST_BOT_TOKEN)}
    a_hdr = {"X-Telegram-Init-Data": wa_auth.build_init_data({"id": TEST_ADMIN_ID}, TEST_BOT_TOKEN)}

    # 1) Public routes: health + User Mini App HTML + Admin Web Panel HTML
    st, ct, body = wa_server.dispatch_request("GET", "/health", {})
    assert st == 200 and json.loads(body)["version"] == config.VERSION

    st, ct, html_u = wa_server.dispatch_request("GET", "/", {})
    assert st == 200 and "lightweight-charts" in html_u.decode("utf-8") and 'dir="rtl"' in html_u.decode("utf-8")

    st, ct, html_a = wa_server.dispatch_request("GET", "/admin", {})
    assert st == 200 and "لوحة الإدارة الملكية" in html_a.decode("utf-8")

    # 2) Unauthenticated /api/me -> 401; non-admin /api/admin/* -> 403
    assert wa_server.dispatch_request("GET", "/api/me", {})[0] == 401
    assert wa_server.dispatch_request("GET", "/api/admin/overview", u_hdr)[0] == 403
    assert wa_server.dispatch_request("POST", "/api/admin/toggle", u_hdr, b'{"key":"engine_enabled"}')[0] == 403

    # 3) Admin overview, 1-tap toggle, payment approve, payout approve & gift creation
    st, _, raw = wa_server.dispatch_request("GET", "/api/admin/overview", a_hdr)
    assert st == 200 and json.loads(raw)["ok"] is True

    prev_acad = db.get_setting("academy_enabled", "1") == "1"
    st, _, raw = wa_server.dispatch_request("POST", "/api/admin/toggle", a_hdr, b'{"key":"academy_enabled"}')
    assert st == 200 and json.loads(raw)["enabled"] is (not prev_acad)
    wa_server.dispatch_request("POST", "/api/admin/toggle", a_hdr, b'{"key":"academy_enabled"}')

    # Admin generates a VIP 21d gift code via Web Panel -> User redeems it via Mini App!
    st, _, raw = wa_server.dispatch_request(
        "POST", "/api/admin/gift", a_hdr, b'{"target_plan":"VIP","duration_days":21,"max_uses":1}'
    )
    assert st == 200
    code = json.loads(raw)["code"]
    assert code.startswith("FY-")

    st, _, raw = wa_server.dispatch_request(
        "POST", "/api/redeem", u_hdr, json.dumps({"code": code}).encode("utf-8")
    )
    assert st == 200 and json.loads(raw)["ok"] is True
    assert subscriptions.plan_for(TEST_USER_ID)["code"] == "VIP"

    # Admin approves pending payment & affiliate payout via Web Panel
    pid = payments.create_request(TEST_USER_ID, "ROYAL", 79.0, "USDT")
    st, _, raw = wa_server.dispatch_request(
        "POST", "/api/admin/payment", a_hdr, json.dumps({"payment_id": pid, "decision": "approve"}).encode("utf-8")
    )
    assert st == 200 and json.loads(raw)["ok"] is True
    assert subscriptions.plan_for(TEST_USER_ID)["code"] == "ROYAL"

    # User updates risk profile & fetches /api/me + /api/leaderboard + /api/news
    st, _, raw = wa_server.dispatch_request(
        "POST", "/api/risk", u_hdr, b'{"balance":2500,"risk_pct":1.5,"leverage":200,"max_daily_dd_pct":4}'
    )
    assert st == 200 and json.loads(raw)["profile"]["balance"] == 2500.0

    st, _, raw = wa_server.dispatch_request("GET", "/api/me", u_hdr)
    me = json.loads(raw)
    assert st == 200 and me["plan"]["code"] == "ROYAL" and me["risk"]["balance"] == 2500.0
    assert wa_server.dispatch_request("GET", "/api/leaderboard?period=30d", u_hdr)[0] == 200
    assert wa_server.dispatch_request("GET", "/api/news", u_hdr)[0] == 200


def test_real_asyncio_http_server_loopback(isolated_db):
    async def _run():
        srv = await wa_server.start_webapp_server("127.0.0.1", 0)
        assert srv is not None
        try:
            port = srv.sockets[0].getsockname()[1]
            reader, writer = await asyncio.open_connection("127.0.0.1", port)
            writer.write(b"GET /health HTTP/1.1\r\nHost: 127.0.0.1\r\nConnection: close\r\n\r\n")
            await writer.drain()
            raw = await asyncio.wait_for(reader.read(), timeout=5.0)
            writer.close()
            await writer.wait_closed()
            assert b"HTTP/1.1 200 OK" in raw
            assert f'"version": "{config.VERSION}"'.encode("ascii") in raw
        finally:
            srv.close()
            await srv.wait_closed()

    asyncio.run(_run())
