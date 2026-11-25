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

    # 1) v7.4.2: ALWAYS saves & activates Admin's chosen HTTPS URL immediately while showing honest live diagnostic!
    stop_mock = AsyncMock()
    with patch.object(wa_tunnel, "verify_https_health", new=AsyncMock(return_value=(False, "الرابط يعيد صفحة Hello World"))), \
         patch.object(wa_tunnel, "stop_tunnel", new=stop_mock):
        handled = asyncio.run(inputs._admin_text(upd, ctx, ADMIN_ID, msg.text))
    assert handled is True
    assert stop_mock.await_count == 0  # backend tunnel MUST stay alive behind Worker!
    assert config.WEBAPP_URL == "https://forexyemeni.mshay2024m.workers.dev"
    assert config.WEBAPP_TUNNEL_SOURCE == "worker_proxy"
    assert "Hello World" in replies[-1]

    # 2) When /health returns OK -> shows HTTP 200 ready badge too
    ctx.user_data["awaiting_webapp_url"] = True
    with patch.object(wa_tunnel, "verify_https_health", new=AsyncMock(return_value=(True, "OK"))), \
         patch.object(wa_tunnel, "stop_tunnel", new=stop_mock):
        handled2 = asyncio.run(inputs._admin_text(upd, ctx, ADMIN_ID, msg.text))
    assert handled2 is True
    assert "HTTP 200" in replies[-1]


def test_v7410_webapp_worker_guide_button_and_ngrok_token_domain_parse(env):
    from fysb.ui import keyboards as kb_ui
    db.set_setting("ngrok_domain", "my-royal.ngrok-free.app")
    captured = {"text": "", "kb": None}

    async def _edit(text, reply_markup=None, **kw):
        captured["text"] = text
        captured["kb"] = reply_markup

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
    # Verify NO CopyTextButton exceeds Telegram's 256-char hard limit (prevents BadRequest!)
    for row in captured["kb"].inline_keyboard:
        for b in row:
            ct = getattr(b, "copy_text", None)
            if ct is not None:
                assert 1 <= len(ct.text) <= 256

    # copy_btn itself safely refuses >256 chars instead of crashing with BadRequest
    long_btn = kb_ui.copy_btn("Long", "x" * 500)
    assert getattr(long_btn, "copy_text", None) is None


def test_v7420_universal_token_and_url_autodetect(env):
    # Cloudflare Zero Trust JWT token (raw & wrapped in CLI command)
    cf_jwt = "eyJhIjoiMTIzNDU2Nzg5MDEyMzQ1Njc4OTAiLCJ0IjoiYWJjZGVmZ2giLCJzIjoieHl6In0"
    p_cf = wa_tunnel.detect_and_parse_tunnel_input(f"cloudflared service install {cf_jwt}")
    assert p_cf["kind"] == "cloudflare_token" and p_cf["token"] == cf_jwt

    # Ngrok token + optional free static domain (even with full CLI command)
    p_ng = wa_tunnel.detect_and_parse_tunnel_input("ngrok config add-authtoken 2AbCdEfGhIjKlMnOpQrStUvWxYz123456 my-bot.ngrok-free.app")
    assert p_ng["kind"] == "ngrok_token" and p_ng["domain"] == "my-bot.ngrok-free.app"

    # Pinggy token
    p_pg1 = wa_tunnel.detect_and_parse_tunnel_input("pinggy:myToken9988")
    assert p_pg1["kind"] == "pinggy_token" and p_pg1["token"] == "myToken9988"
    p_pg2 = wa_tunnel.detect_and_parse_tunnel_input("ssh -p 443 -R0:localhost:8080 abcXYZ12@a.pinggy.io")
    assert p_pg2["kind"] == "pinggy_token" and p_pg2["token"] == "abcXYZ12"

    # Markdown-wrapped or plain HTTPS URL
    p_u = wa_tunnel.detect_and_parse_tunnel_input("[https://forexyemeni.mshay2024m.workers.dev](https://forexyemeni.mshay2024m.workers.dev)")
    assert p_u["kind"] == "custom_url" and p_u["url"] == "https://forexyemeni.mshay2024m.workers.dev"

    # Entering a Cloudflare Tunnel Token via awaiting_webapp_url saves cf_tunnel_token & launches start_tunnel
    replies = []

    async def _reply(text, **kw):
        replies.append(text)

    user = SimpleNamespace(id=ADMIN_ID, username="admin", first_name="Admin", full_name="Admin")
    msg = SimpleNamespace(text=cf_jwt, reply_text=_reply, chat_id=ADMIN_ID)
    upd = SimpleNamespace(effective_user=user, effective_message=msg, message=msg, effective_chat=SimpleNamespace(id=ADMIN_ID))
    ctx = SimpleNamespace(user_data={"awaiting_webapp_url": True}, bot=SimpleNamespace())
    with patch.object(wa_tunnel, "stop_tunnel", new=AsyncMock()), \
         patch.object(wa_tunnel, "start_tunnel", new=AsyncMock(return_value="cloudflare-named-tunnel")), \
         patch("fysb.webapp.server.start_webapp_server", new=AsyncMock(return_value=object())):
        handled = asyncio.run(inputs._admin_text(upd, ctx, ADMIN_ID, cf_jwt))
    assert handled is True
    assert wa_tunnel.get_cf_tunnel_token() == cf_jwt
    assert any("Cloudflare Zero Trust" in r for r in replies)
