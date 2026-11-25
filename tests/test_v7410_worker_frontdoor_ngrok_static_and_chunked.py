"""v7.4.1 Verification Suite:
  - Cloudflare Worker Zero-Warning Front-Door Mode (keeps Ngrok/SSH tunnel alive behind *.workers.dev)
  - Live /health validation on custom URLs (catches empty "Hello World!" Cloudflare Worker immediately)
  - Ngrok free static domain support (`NGROK_DOMAIN` / `ngrok_domain` via `TOKEN DOMAIN`)
  - `Transfer-Encoding: chunked` body decoding + CORS headers + trailing slash normalization
  - 1-tap `a:webapp_worker` copy-ready Worker code generator
"""
from __future__ import annotations

import asyncio
import json
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest

from fysb import config, db
from fysb.handlers import dispatcher, inputs
from fysb.services import users
from fysb.webapp import server as wa_server
from fysb.webapp import tunnel as wa_tunnel

ADMIN_ID = 910001
BOT_TOKEN = "774410:TEST_V7410_TOKEN"


@pytest.fixture
def env(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "DB_FILE", tmp_path / "v7410.db")
    monkeypatch.setattr(config, "DATABASE_URL", "")
    monkeypatch.setattr(config, "ADMIN_ID", ADMIN_ID)
    monkeypatch.setattr(config, "BOT_TOKEN", BOT_TOKEN)
    monkeypatch.setattr(config, "WEBAPP_URL_STATIC", "")
    monkeypatch.setattr(config, "WEBAPP_URL", "")
    monkeypatch.setattr(config, "WEBAPP_TUNNEL_SOURCE", "")
    db.init_db()
    users.ensure_user(SimpleNamespace(id=ADMIN_ID, username="admin", full_name="Admin"))
    return tmp_path


def test_v7410_chunked_body_cors_and_trailing_slash(env):
    # Trailing slash /admin/ works identically to /admin
    st, ct, body = wa_server.dispatch_request("GET", "/admin/", {})
    assert st == 200 and "text/html" in ct and "لوحة الإدارة الملكية" in body.decode("utf-8")

    # Chunked body decoder decodes multi-chunk HTTP/1.1 bodies from Cloudflare Workers
    async def _run_chunked():
        reader = asyncio.StreamReader()
        payload = b'{"hello":"worker","n":42}'
        c1, c2 = payload[:10], payload[10:]
        raw = f"{len(c1):x}\r\n".encode() + c1 + b"\r\n" + f"{len(c2):x}\r\n".encode() + c2 + b"\r\n0\r\n\r\n"
        reader.feed_data(raw)
        reader.feed_eof()
        return await wa_server._read_chunked_body(reader)

    decoded = asyncio.run(_run_chunked())
    assert json.loads(decoded) == {"hello": "worker", "n": 42}


def test_v7410_worker_code_static_domain_and_hello_world_detection(env):
    db.set_setting("ngrok_domain", "https://royal-test.ngrok-free.app/")
    assert wa_tunnel.get_ngrok_domain() == "royal-test.ngrok-free.app"
    assert wa_tunnel.is_proxy_frontdoor_url("https://forexyemeni.mshay2024m.workers.dev") is True

    code = wa_tunnel.build_cloudflare_worker_code("https://royal-test.ngrok-free.app")
    assert 'const ORIGIN = "https://royal-test.ngrok-free.app";' in code
    assert 'ngrok-skip-browser-warning' in code
    assert 'X-Pinggy-No-Screen' in code

    # Hello World response on /health produces clear Arabic diagnosis
    class _FakeResp:
        status = 200
        def read(self, _n=-1):
            return b"Hello World!"
        def __enter__(self):
            return self
        def __exit__(self, *a):
            return False

    with patch("urllib.request.urlopen", return_value=_FakeResp()):
        ok, msg = wa_tunnel._probe_health_once("https://forexyemeni.mshay2024m.workers.dev")
    assert ok is False
    assert "Hello World" in msg


def test_v7410_custom_worker_url_live_probe_and_keeps_tunnel_alive(env):
    replies: list[str] = []

    async def _reply(text, **kw):
        replies.append(text)

    user = SimpleNamespace(id=ADMIN_ID, username="admin", first_name="Admin", full_name="Admin")
    msg = SimpleNamespace(text="https://forexyemeni.mshay2024m.workers.dev", reply_text=_reply, chat_id=ADMIN_ID)
    upd = SimpleNamespace(effective_user=user, effective_message=msg, message=msg, effective_chat=SimpleNamespace(id=ADMIN_ID))
    ctx = SimpleNamespace(user_data={"awaiting_webapp_url": True}, bot=SimpleNamespace())

    # 1) When Worker still returns Hello World -> rejected with clear diagnosis & Worker guide button!
    with patch.object(wa_tunnel, "verify_https_health", new=AsyncMock(return_value=(False, "الرابط يعيد صفحة Hello World"))):
        handled = asyncio.run(inputs._admin_text(upd, ctx, ADMIN_ID, msg.text))
    assert handled is True
    assert ctx.user_data.get("awaiting_webapp_url") is True
    assert "Hello World" in replies[-1]
    assert config.WEBAPP_URL == ""

    # 2) Once Worker is deployed and /health returns OK -> activated as worker_proxy WITHOUT killing backend tunnel!
    stop_mock = AsyncMock()
    with patch.object(wa_tunnel, "verify_https_health", new=AsyncMock(return_value=(True, "OK"))), \
         patch.object(wa_tunnel, "stop_tunnel", new=stop_mock):
        handled2 = asyncio.run(inputs._admin_text(upd, ctx, ADMIN_ID, msg.text))
    assert handled2 is True
    assert stop_mock.await_count == 0  # backend tunnel MUST stay alive behind Worker!
    assert config.WEBAPP_URL == "https://forexyemeni.mshay2024m.workers.dev"
    assert config.WEBAPP_TUNNEL_SOURCE == "worker_proxy"


def test_v7410_webapp_worker_guide_button_and_ngrok_token_domain_parse(env):
    db.set_setting("ngrok_domain", "my-royal.ngrok-free.app")
    captured = {"text": ""}

    async def _edit(text, reply_markup=None, **kw):
        captured["text"] = text

    async def _answer(*a, **kw):
        return None

    user = SimpleNamespace(id=ADMIN_ID, username="admin", first_name="A", full_name="A")
    msg = SimpleNamespace(chat_id=ADMIN_ID, message_id=1, edit_text=_edit, reply_text=_edit, chat=SimpleNamespace(id=ADMIN_ID))
    q = SimpleNamespace(data="a:webapp_worker", from_user=user, message=msg, answer=_answer)
    upd = SimpleNamespace(callback_query=q, effective_user=user, effective_chat=msg.chat)
    ctx = SimpleNamespace(user_data={}, bot_data={}, bot=SimpleNamespace())
    asyncio.run(dispatcher.buttons(upd, ctx))
    assert "my-royal.ngrok-free.app" in captured["text"]
    assert "ngrok-skip-browser-warning" in captured["text"]
