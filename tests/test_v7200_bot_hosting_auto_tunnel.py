"""Regression tests for v7.2.0: Zero-Config Bot-Hosting Auto-HTTPS Tunnel for Mini App."""
from __future__ import annotations

import asyncio
import socket
from pathlib import Path
from types import SimpleNamespace

import pytest

from fysb import config, db
from fysb.handlers import admin_system
from fysb.ui import keyboards as kb
from fysb.webapp import server as webapp_server
from fysb.webapp import tunnel


@pytest.fixture
def isolated_env(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "DB_FILE", tmp_path / "v7200.db")
    monkeypatch.setattr(config, "DATABASE_URL", "")
    monkeypatch.setattr(config, "WEBAPP_URL_STATIC", "")
    monkeypatch.setattr(config, "WEBAPP_URL", "")
    monkeypatch.setattr(config, "WEBAPP_TUNNEL_SOURCE", "")
    monkeypatch.setattr(config, "WEBAPP_ENABLED", True)
    monkeypatch.setattr(config, "WEBAPP_AUTO_TUNNEL", True)
    db.init_db()
    yield tmp_path
    config.set_runtime_webapp_url("", source="")


def test_runtime_webapp_url_and_user_menu_webapp_button(isolated_env):
    # Initially off -> compact 6-row user menu
    assert config.webapp_ready() is False
    m0 = kb.user_menu()
    assert len(m0.inline_keyboard) == 6

    # Runtime auto-tunnel URL injected -> WebApp button appears automatically on row 0!
    config.set_runtime_webapp_url("https://demo-royal.trycloudflare.com", source="auto_tunnel")
    assert config.webapp_ready() is True
    m1 = kb.user_menu()
    assert len(m1.inline_keyboard) == 7
    top_btn = m1.inline_keyboard[0][0]
    assert getattr(top_btn, "web_app", None) is not None
    assert top_btn.web_app.url == "https://demo-royal.trycloudflare.com/"

    # strip_button_styles preserves WebApp button intact
    stripped = kb.strip_button_styles(m1)
    assert getattr(stripped.inline_keyboard[0][0], "web_app", None) is not None

    # Clearing resets cleanly
    config.set_runtime_webapp_url("", source="")
    assert config.webapp_ready() is False


def test_start_webapp_server_falls_back_to_free_port_when_busy(isolated_env):
    async def _run():
        # Occupy a local port first
        blocker = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        blocker.bind(("127.0.0.1", 0))
        blocker.listen(1)
        busy_port = blocker.getsockname()[1]
        try:
            srv = await webapp_server.start_webapp_server("127.0.0.1", busy_port)
            assert srv is not None
            assert config.WEBAPP_PORT != busy_port
            assert config.WEBAPP_PORT > 0
            srv.close()
            await srv.wait_closed()
        finally:
            blocker.close()

    asyncio.run(_run())


def test_tunnel_parses_trycloudflare_url_and_cleans_up(isolated_env, monkeypatch, tmp_path):
    # Create a tiny fake executable script that emits a trycloudflare URL on stderr and sleeps
    fake_bin = tmp_path / "fake_cloudflared.sh"
    fake_bin.write_text(
        "#!/bin/sh\n"
        "echo 'INF | +---------------------------------------------------------+' >&2\n"
        "echo 'INF |  https://forexyemeni-live-test.trycloudflare.com          |' >&2\n"
        "sleep 30\n",
        encoding="utf-8",
    )
    fake_bin.chmod(0o755)
    monkeypatch.setattr(tunnel, "ensure_cloudflared_binary", lambda: fake_bin)

    async def _run():
        url = await tunnel.start_tunnel(port=18080, timeout=10.0)
        assert url == "https://forexyemeni-live-test.trycloudflare.com"
        assert config.WEBAPP_URL == url
        assert config.WEBAPP_TUNNEL_SOURCE == "auto_tunnel"
        assert config.webapp_ready() is True
        assert tunnel.is_tunnel_alive() is True
        st = tunnel.tunnel_status()
        assert st["active"] is True and st["port"] == 18080

        await tunnel.stop_tunnel()
        assert tunnel.is_tunnel_alive() is False
        assert config.WEBAPP_URL == ""

    asyncio.run(_run())


def test_admin_webapp_info_renders_live_tunnel_controls(isolated_env):
    config.set_runtime_webapp_url("https://live-bot-hosting.trycloudflare.com", source="auto_tunnel")

    class _FakeMsg:
        def __init__(self):
            self.chat = SimpleNamespace(id=1)
            self.replied = []

        async def reply_text(self, text, reply_markup=None, parse_mode=None, disable_web_page_preview=True):
            self.replied.append({"text": text, "reply_markup": reply_markup})
            return self

    q = SimpleNamespace(from_user=SimpleNamespace(id=1), message=_FakeMsg())
    asyncio.run(admin_system.admin_webapp_info(q, 1, SimpleNamespace(user_data={}), SimpleNamespace()))
    assert len(q.message.replied) == 1
    txt = q.message.replied[0]["text"]
    assert "live-bot-hosting.trycloudflare.com" in txt
    assert "Bot-Hosting" in txt
    cbs = [getattr(b, "callback_data", "") for row in q.message.replied[0]["reply_markup"].inline_keyboard for b in row]
    assert "a:webapptunnel_restart" in cbs
    assert "a:webapptunnel_toggle" in cbs
