"""Tests for v7.4.3 Royal GitHub Pages Front-Door (zero extra accounts, zero ISP DNS blocks, zero Ngrok interstitial)."""
from __future__ import annotations

import asyncio
import pathlib
from types import SimpleNamespace
from urllib.parse import parse_qs, urlparse

from fysb import config, db
from fysb.handlers import dispatcher
from fysb.services import users
from fysb.webapp import server as wa_server
from fysb.webapp import templates as wa_templates
from fysb.webapp import tunnel as wa_tunnel


def _prep(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "DB_FILE", tmp_path / "v7430.db")
    monkeypatch.setattr(config, "DATABASE_URL", "")
    monkeypatch.setattr(config, "BOT_TOKEN", "999:v7430-test-token")
    monkeypatch.setattr(config, "ADMIN_ID", 777001)
    db.init_db()
    users.ensure_user(SimpleNamespace(id=777001, username="admin", full_name="Admin"))


def test_webapp_page_url_wraps_ngrok_and_pinggy_via_github_pages(monkeypatch):
    monkeypatch.setattr(config, "WEBAPP_GH_PAGES_ENABLED", True)
    monkeypatch.setattr(config, "WEBAPP_GH_PAGES_URL", "https://forexyemeni.github.io/ForexYemeni_Bot")
    monkeypatch.setattr(config, "WEBAPP_URL", "https://trombone-blitz-chain.ngrok-free.dev")

    u_url = config.webapp_page_url("/")
    p_u = urlparse(u_url)
    assert p_u.netloc == "forexyemeni.github.io"
    assert p_u.path == "/ForexYemeni_Bot/"
    assert parse_qs(p_u.query)["api"] == ["https://trombone-blitz-chain.ngrok-free.dev"]

    a_url = config.webapp_page_url("/admin")
    p_a = urlparse(a_url)
    assert p_a.netloc == "forexyemeni.github.io"
    assert p_a.path == "/ForexYemeni_Bot/admin/"
    assert parse_qs(p_a.query)["api"] == ["https://trombone-blitz-chain.ngrok-free.dev"]

    partner_url = config.webapp_page_url("/", {"tab": "partner"})
    p_p = urlparse(partner_url)
    assert parse_qs(p_p.query)["tab"] == ["partner"]
    assert parse_qs(p_p.query)["api"] == ["https://trombone-blitz-chain.ngrok-free.dev"]

    # Pinggy free tunnel also wrapped cleanly
    pg_url = config.webapp_page_url("/admin", base_url="https://abc123.a.free.pinggy.link")
    assert pg_url.startswith("https://forexyemeni.github.io/ForexYemeni_Bot/admin/?api=https%3A%2F%2Fabc123.a.free.pinggy.link")


def test_webapp_page_url_preserves_custom_domain_direct(monkeypatch):
    monkeypatch.setattr(config, "WEBAPP_GH_PAGES_ENABLED", True)
    monkeypatch.setattr(config, "WEBAPP_URL", "https://app.forexyemeni.com")

    assert config.webapp_page_url("/") == "https://app.forexyemeni.com/"
    assert config.webapp_page_url("/admin") == "https://app.forexyemeni.com/admin"
    assert config.webapp_page_url("/", {"tab": "partner"}) == "https://app.forexyemeni.com/?tab=partner"


def test_github_pages_static_files_exist_and_match_templates():
    root = pathlib.Path(__file__).resolve().parent.parent
    idx = root / "docs" / "index.html"
    adm = root / "docs" / "admin" / "index.html"
    nojek = root / "docs" / ".nojekyll"

    assert idx.is_file()
    assert adm.is_file()
    assert nojek.is_file()

    u_html = idx.read_text(encoding="utf-8")
    a_html = adm.read_text(encoding="utf-8")
    assert u_html == wa_templates.render_user_miniapp_html()
    assert a_html == wa_templates.render_admin_panel_html()

    for html in (u_html, a_html):
        assert "API_BASE" in html
        assert "ngrok-skip-browser-warning" in html
        assert "X-Pinggy-No-Screen" in html


def test_tunnel_recognizes_github_pages_as_frontdoor(tmp_path, monkeypatch):
    _prep(tmp_path, monkeypatch)
    assert wa_tunnel.is_proxy_frontdoor_url("https://forexyemeni.github.io/ForexYemeni_Bot") is True


def test_live_http_server_cors_preflight_allows_ngrok_and_pinggy_headers():
    async def _run():
        srv = await wa_server.start_webapp_server(host="127.0.0.1", port=0)
        assert srv is not None
        port = srv.sockets[0].getsockname()[1]
        try:
            reader, writer = await asyncio.open_connection("127.0.0.1", port)
            req = (
                "OPTIONS /api/admin/overview HTTP/1.1\r\n"
                "Host: 127.0.0.1\r\n"
                "Origin: https://forexyemeni.github.io\r\n"
                "Access-Control-Request-Method: GET\r\n"
                "Access-Control-Request-Headers: content-type,ngrok-skip-browser-warning,x-pinggy-no-screen,x-telegram-init-data\r\n"
                "\r\n"
            ).encode("ascii")
            writer.write(req)
            await writer.drain()
            raw = await asyncio.wait_for(reader.read(4096), timeout=5.0)
            writer.close()
            text = raw.decode("iso-8859-1", errors="replace")
            assert "HTTP/1.1 204" in text
            assert "Access-Control-Allow-Origin: *" in text
            assert "ngrok-skip-browser-warning" in text
            assert "X-Pinggy-No-Screen" in text
            assert "Access-Control-Max-Age: 86400" in text
        finally:
            srv.close()
            await srv.wait_closed()

    asyncio.run(_run())


def test_admin_1tap_ghpages_button_clears_blocked_workers_dev_and_activates(tmp_path, monkeypatch):
    _prep(tmp_path, monkeypatch)
    db.set_setting("webapp_custom_url", "https://forexyemeni.mshay2024m.workers.dev")
    monkeypatch.setattr(config, "WEBAPP_URL_STATIC", "")
    monkeypatch.setattr(config, "WEBAPP_URL", "https://forexyemeni.mshay2024m.workers.dev")
    monkeypatch.setattr(config, "WEBAPP_GH_PAGES_URL", "https://forexyemeni.github.io/ForexYemeni_Bot")
    monkeypatch.setattr(wa_tunnel, "is_tunnel_alive", lambda: True)
    monkeypatch.setattr(
        wa_tunnel,
        "tunnel_status",
        lambda: {"url": "https://trombone-blitz-chain.ngrok-free.dev", "provider": "Ngrok-443", "alive": True, "logs": []},
    )

    captured = {}

    async def _edit_text(text, reply_markup=None, **kwargs):
        captured["text"] = text
        captured["markup"] = reply_markup

    async def _answer(*a, **kw):
        pass

    msg = SimpleNamespace(edit_text=_edit_text, reply_text=_edit_text)
    q = SimpleNamespace(
        data="a:webapp_ghpages",
        from_user=SimpleNamespace(id=777001, username="admin", full_name="Admin"),
        message=msg,
        answer=_answer,
    )
    upd = SimpleNamespace(callback_query=q, effective_user=q.from_user)
    ctx = SimpleNamespace(user_data={}, bot=SimpleNamespace())

    asyncio.run(dispatcher.buttons(upd, ctx))

    assert db.get_setting("webapp_custom_url", "") == ""
    assert config.WEBAPP_URL == "https://trombone-blitz-chain.ngrok-free.dev"
    assert "GitHub Pages" in captured["text"]
    # First WebApp button must open GitHub Pages Admin with ?api=<ngrok>
    first_btn = captured["markup"].inline_keyboard[0][0]
    assert first_btn.web_app is not None
    assert first_btn.web_app.url.startswith(
        "https://forexyemeni.github.io/ForexYemeni_Bot/admin/?api=https%3A%2F%2Ftrombone-blitz-chain.ngrok-free.dev"
    )
