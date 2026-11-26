"""v8.1.0 Regression Suite — Ultra-Luxury Institutional ForexYemeni Analysis Charts.

Verifies automatic luxury chart generation & delivery across ALL 4 surfaces:
  1. `analytics.run_analysis` attaches structured real `chart_data` (candles, OB, FVG, SR, PD, RSI, EMA, ATR, setup).
  2. `charts.render_analysis_chart` generates a crisp 1600x1000 PNG with ForexYemeni branding & caption <= 1024 chars.
  3. Users & Admin (`u:analytics`) automatically receive the ForexYemeni analysis chart PNG + full Arabic report.
  4. Channels (`admin_channels.post_analysis`) automatically broadcast the ForexYemeni analysis chart PNG + full card.
  5. Partners (`p:anl` in Partner Panel & Partner channel publishing) automatically receive/post the ForexYemeni chart.
"""
from __future__ import annotations

import asyncio
import math
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest

from fysb import config, db
from fysb.engines import market_data
from fysb.handlers import admin_channels, admin_partners, user_pages
from fysb.services import analytics, partners as partners_svc, subscriptions, users as users_svc
from fysb.ui import charts

ADMIN_UID = 810001
USER_UID = 810002
PARTNER_UID = 810003


def _sample_candles(n: int = 90) -> list[dict]:
    bars = []
    px = 2630.0
    for i in range(n):
        wave = math.sin(i * 0.25) * 4.0 + i * 0.35
        o = px
        c = 2630.0 + wave + (1.6 if i % 3 != 0 else -1.2)
        h = max(o, c) + 2.2
        l = min(o, c) - 2.0
        px = c
        bars.append({
            "datetime": f"2026-11-25 {i // 4:02d}:{(i % 4) * 15:02d}:00",
            "open": o, "high": h, "low": l, "close": c,
        })
    return bars


def _fake_payload(sym="XAUUSD", tf="15min"):
    bars = _sample_candles(90)
    return {
        "symbol": sym, "interval": tf, "values": bars,
        "fetched_at": config.now(), "fetched_epoch": 0,
        "last_candle_iso": bars[-1]["datetime"], "age_seconds": 15,
    }


@pytest.fixture
def env(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "DB_FILE", tmp_path / "v810.db")
    monkeypatch.setattr(config, "DATABASE_URL", "")
    monkeypatch.setattr(config, "USE_PG", False)
    monkeypatch.setattr(config, "ADMIN_ID", ADMIN_UID)
    db.init_db()
    db.set_setting("charts_enabled", "1")
    for uid, uname, fname in (
        (ADMIN_UID, "adm81", "Admin 810"),
        (USER_UID, "usr81", "User 810"),
        (PARTNER_UID, "prt81", "Partner 810"),
    ):
        users_svc.ensure_user(SimpleNamespace(id=uid, username=uname, full_name=fname))
    subscriptions.grant_subscription(USER_UID, "VIP", source="TEST", actor=ADMIN_UID)
    return tmp_path


def test_01_run_analysis_attaches_chart_data_and_renders_1600x1000_png(env):
    with patch.object(market_data, "fetch_candles", side_effect=lambda *a, **k: _fake_payload()), \
         patch.object(market_data, "market_state", return_value=True):
        res = analytics.run_analysis(USER_UID, "XAUUSD", "M15", "", "", "SMC", enforce_plan=True)

    assert res["ok"] is True
    an = res["analysis"]
    cd = an.get("chart_data")
    assert isinstance(cd, dict)
    assert len(cd["candles"]) >= 40
    assert "sr" in cd and "obs" in cd and "fvgs" in cd and "rsi" in cd

    png = charts.render_analysis_chart(an)
    assert png is not None and png[:8] == b"\x89PNG\r\n\x1a\n" and len(png) > 20000
    # Verify exact 1600x1000 PNG dimensions from IHDR chunk
    w = int.from_bytes(png[16:20], "big")
    h = int.from_bytes(png[20:24], "big")
    assert (w, h) == (1600, 1000)

    cap = charts.render_analysis_caption(an)
    assert config.BRAND in cap and "XAUUSD" in cap and len(cap) <= 1024
    assert tuple(int(x) for x in config.VERSION.split(".")) >= (8, 1, 0)


def test_02_user_and_admin_analytics_automatically_send_luxury_chart_photo(env):
    photos_sent = []
    messages_sent = []

    async def fake_send_photo(**kw):
        photos_sent.append(kw)

    async def fake_send_message(*a, **kw):
        messages_sent.append((a, kw))
        return SimpleNamespace(chat_id=USER_UID, message_id=99)

    bot = SimpleNamespace(send_photo=AsyncMock(side_effect=fake_send_photo), send_message=AsyncMock(side_effect=fake_send_message))
    for uid in (USER_UID, ADMIN_UID):
        photos_sent.clear()
        q = SimpleNamespace(
            from_user=SimpleNamespace(id=uid, username="t", full_name="Tester"),
            message=SimpleNamespace(chat_id=uid, message_id=10, reply_text=AsyncMock(), edit_text=AsyncMock()),
            answer=AsyncMock(),
        )
        ctx = SimpleNamespace(user_data={"anl_draft": {"symbol": "XAUUSD", "tf": "M15"}}, bot=bot)
        with patch.object(market_data, "fetch_candles", side_effect=lambda *a, **k: _fake_payload()), \
             patch.object(market_data, "market_state", return_value=True):
            asyncio.run(user_pages.user_page(q, "anlstrat:SMC", uid, ctx))
        assert len(photos_sent) == 1
        assert photos_sent[0]["chat_id"] == uid
        assert config.BRAND in photos_sent[0]["caption"]
        assert photos_sent[0]["photo"].getvalue()[:8] == b"\x89PNG\r\n\x1a\n"


def test_03_channel_post_analysis_automatically_broadcasts_luxury_chart_and_card(env):
    cid = admin_channels.add_channel("-1001777888999", "ForexYemeni Official", "channel")
    admin_channels.update_channel(cid, markets="XAUUSD", timeframes="M15", strategies="SMC", enabled=1)
    ch = admin_channels.get_channel(cid)

    photos = []
    texts = []

    async def fake_photo(**kw):
        photos.append(kw)
        return SimpleNamespace(message_id=501)

    async def fake_msg(*a, **kw):
        texts.append((a, kw))
        return SimpleNamespace(message_id=502)

    bot = SimpleNamespace(
        send_photo=AsyncMock(side_effect=fake_photo),
        send_message=AsyncMock(side_effect=fake_msg),
        get_me=AsyncMock(return_value=SimpleNamespace(id=12345)),
        get_chat_member=AsyncMock(return_value=SimpleNamespace(status="administrator", can_post_messages=True)),
    )
    with patch.object(market_data, "fetch_candles", side_effect=lambda *a, **k: _fake_payload()), \
         patch.object(market_data, "market_state", return_value=True):
        res = asyncio.run(admin_channels.post_analysis(cid, bot, ch))

    assert res["ok"] is True and "XAUUSD" in res["posted"]
    assert len(photos) == 1
    assert photos[0]["chat_id"] == -1001777888999
    assert config.BRAND in photos[0]["caption"]
    assert photos[0]["photo"].getvalue()[:8] == b"\x89PNG\r\n\x1a\n"
    assert len(texts) >= 1


def test_04_partner_panel_has_instant_chart_analysis_and_delivers_photo(env):
    partners_svc.create_partner(PARTNER_UID, "Royal Partner Desk", commission_pct=25.0)
    photos = []

    async def fake_photo(**kw):
        photos.append(kw)

    async def fake_msg(*a, **kw):
        return SimpleNamespace(chat_id=PARTNER_UID, message_id=77)

    bot = SimpleNamespace(send_photo=AsyncMock(side_effect=fake_photo), send_message=AsyncMock(side_effect=fake_msg))
    q = SimpleNamespace(
        from_user=SimpleNamespace(id=PARTNER_UID, username="prt81", full_name="Partner 810"),
        message=SimpleNamespace(chat_id=PARTNER_UID, message_id=20, reply_text=AsyncMock(), edit_text=AsyncMock()),
        answer=AsyncMock(),
    )
    ctx = SimpleNamespace(user_data={}, bot=bot)

    # Partner home exposes the new button
    p = partners_svc.get_partner_by_user(PARTNER_UID)
    _txt, mk = asyncio.run(admin_partners._build_partner_home(p))
    assert "p:anl" in str(mk)

    # Partner runs p:anl:XAUUSD:M15 -> receives luxury ForexYemeni chart PNG automatically
    with patch.object(market_data, "fetch_candles", side_effect=lambda *a, **k: _fake_payload()), \
         patch.object(market_data, "market_state", return_value=True):
        asyncio.run(admin_partners.handle_partner_callback(q, PARTNER_UID, ctx, bot, "anl:XAUUSD:M15"))

    assert len(photos) == 1
    assert photos[0]["chat_id"] == PARTNER_UID
    assert config.BRAND in photos[0]["caption"]
    assert photos[0]["photo"].getvalue()[:8] == b"\x89PNG\r\n\x1a\n"
