"""Regression tests for v7.0.2:
1. admin_signal_proof_publish NameError fix (_active_channel_chat_ids)
2. Real live floating pips (+100.0 pips instead of +0.0 pips) on open RUNNING trades
   in both captions and rendered chart headers
3. Admin configurable chart scheduler (5/10/15/30/60 min + Admin/Channels toggle + send now)
"""
from __future__ import annotations

import asyncio
from types import SimpleNamespace

import pytest

from fysb import config, db
from fysb.engines import market_data
from fysb.handlers import admin_trading
from fysb.ui import charts


@pytest.fixture
def isolated_db(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "DB_FILE", tmp_path / "v7002.db")
    monkeypatch.setattr(config, "DATABASE_URL", "")
    db.init_db()
    return tmp_path / "v7002.db"


def _sample_xau_sell_candles(entry: float = 4555.0, current: float = 4545.0, n: int = 40):
    """40 candles moving smoothly from entry down to current (e.g. +100 pips on XAUUSD SELL)."""
    out = []
    step = (current - entry) / max(1, n - 1)
    for i in range(n):
        c = round(entry + step * i, 2)
        o = round(c - step * 0.5, 2)
        out.append({
            "datetime": f"2026-11-24 {10 + i // 60:02d}:{i % 60:02d}:00",
            "open": o,
            "high": round(max(o, c) + 0.8, 2),
            "low": round(min(o, c) - 0.8, 2),
            "close": c,
        })
    return out


class _FakeBot:
    def __init__(self):
        self.photos = []
        self.messages = []

    async def send_photo(self, chat_id, photo, caption="", reply_markup=None, parse_mode=None):
        data = photo.read() if hasattr(photo, "read") else bytes(photo)
        self.photos.append({
            "chat_id": chat_id,
            "png_len": len(data),
            "png_head": data[:8],
            "caption": caption,
            "reply_markup": reply_markup,
            "parse_mode": parse_mode,
        })

    async def send_message(self, chat_id, text, reply_markup=None, parse_mode=None):
        self.messages.append({"chat_id": chat_id, "text": text})


class _FakeMessage:
    def __init__(self, uid=111):
        self.chat_id = uid
        self.message_id = 99
        self.chat = SimpleNamespace(id=uid)
        self.replied = []

    async def reply_text(self, text, reply_markup=None, parse_mode=None, disable_web_page_preview=True):
        self.replied.append({"text": text, "reply_markup": reply_markup})
        return self

    async def edit_text(self, text, reply_markup=None, parse_mode=None, disable_web_page_preview=True):
        self.replied.append({"text": text, "reply_markup": reply_markup})
        return self


class _FakeQuery:
    def __init__(self, uid=111):
        self.from_user = SimpleNamespace(id=uid)
        self.message = _FakeMessage(uid=uid)
        self.edited = self.message.replied

    async def answer(self, *a, **kw):
        return None


def test_live_signal_metrics_calculates_real_floating_pips_on_running_trade(isolated_db):
    sig = {
        "symbol": "XAU/USD",
        "direction": "SELL",
        "entry": 4555.0,
        "stop": 4570.0,
        "tp1": 4540.0,
        "tp2": 4525.0,
        "tp3": 4505.0,
        "status": "RUNNING",
        "pips_result": 0.0,
    }
    candles = _sample_xau_sell_candles(entry=4555.0, current=4545.0)
    m = charts.live_signal_metrics(sig, candles)
    assert m["is_floating"] is True
    assert m["is_closed"] is False
    assert m["live_px"] == pytest.approx(4545.0, abs=0.05)
    assert m["display_pips"] == pytest.approx(100.0, abs=0.5)

    # Closed trade keeps banked pips_result
    closed_sig = dict(sig, status="TP3_HIT", pips_result=500.0, closed_at="2026-11-24T12:00:00+00:00")
    m2 = charts.live_signal_metrics(closed_sig, candles)
    assert m2["is_floating"] is False
    assert m2["is_closed"] is True
    assert m2["display_pips"] == pytest.approx(500.0)


def test_admin_signal_proof_publish_no_nameerror_and_shows_live_pips(isolated_db, monkeypatch):
    candles = _sample_xau_sell_candles(entry=4555.0, current=4545.0)
    monkeypatch.setattr(
        market_data,
        "fetch_candles",
        lambda symbol, interval, api_key="", outputsize=120, use_cache=True: {"values": candles, "source": "mock"},
    )
    with db.conn() as c:
        c.execute(
            """INSERT INTO signals
               (symbol, direction, timeframe, strategy, entry, stop,
                tp1, tp2, tp3, rr, score, reason, status, pips_result, created_at)
               VALUES ('XAU/USD', 'SELL', '15m', 'institutional_v3', 4555.0, 4570.0,
                       4540.0, 4525.0, 4505.0, 2.5, 88, 'SMC sweep', 'RUNNING', 0.0, '2026-11-24T10:00:00+00:00')"""
        )
        sid = c.execute("SELECT MAX(id) id FROM signals").fetchone()["id"]
        c.execute(
            """INSERT INTO broadcast_channels(chat_id, title, kind, enabled, created_at, updated_at)
               VALUES ('-100999888777', 'VIP Channel', 'channel', 1, '2026-11-24T00:00:00+00:00', '2026-11-24T00:00:00+00:00')"""
        )

    bot = _FakeBot()
    q = _FakeQuery(uid=777)
    asyncio.run(admin_trading.admin_signal_proof_publish(q, 777, SimpleNamespace(), bot, sid))

    # Sent to both Admin (777) and the active channel (-100999888777), zero NameError!
    assert len(bot.photos) == 2
    assert {p["chat_id"] for p in bot.photos} == {777, -100999888777}
    for p in bot.photos:
        assert p["png_head"] == b"\x89PNG\r\n\x1a\n"
        assert "+100.0 pips" in p["caption"]
        assert "+0.0 pips" not in p["caption"]
        assert "4,545.00" in p["caption"]


def test_chart_scheduler_settings_and_send_now(isolated_db, monkeypatch):
    candles = _sample_xau_sell_candles(entry=4555.0, current=4545.0)
    monkeypatch.setattr(
        market_data,
        "fetch_candles",
        lambda symbol, interval, api_key="", outputsize=120, use_cache=True: {"values": candles, "source": "mock"},
    )
    with db.conn() as c:
        c.execute(
            """INSERT INTO signals
               (symbol, direction, timeframe, strategy, entry, stop,
                tp1, tp2, tp3, rr, score, reason, status, pips_result, created_at)
               VALUES ('XAU/USD', 'SELL', '15m', 'institutional_v3', 4555.0, 4570.0,
                       4540.0, 4525.0, 4505.0, 2.5, 88, 'SMC sweep', 'RUNNING', 0.0, '2026-11-24T10:00:00+00:00')"""
        )
        c.execute(
            """INSERT INTO broadcast_channels(chat_id, title, kind, enabled, created_at, updated_at)
               VALUES ('-100123456', 'Public VIP', 'channel', 1, '2026-11-24T00:00:00+00:00', '2026-11-24T00:00:00+00:00')"""
        )

    bot = _FakeBot()
    q = _FakeQuery(uid=777)

    # Configure schedule to every 15 minutes + enable channels
    asyncio.run(admin_trading.admin_chart_sched_set(q, 777, SimpleNamespace(), bot, 15))
    assert db.get_setting_int("chart_schedule_minutes", 0) == 15

    asyncio.run(admin_trading.admin_chart_sched_ch_toggle(q, 777, SimpleNamespace(), bot))
    assert db.get_setting("chart_schedule_channels", "0") == "1"

    # Trigger immediate send
    asyncio.run(admin_trading.admin_chart_send_now(q, 777, SimpleNamespace(), bot))
    assert len(bot.photos) == 2
    assert {p["chat_id"] for p in bot.photos} == {777, -100123456}
    assert "+100.0 pips" in bot.photos[0]["caption"]
    # Admin copy has 1-tap publish button
    assert bot.photos[0]["reply_markup"] is not None


def test_stdlib_chart_fallback_with_live_pips(isolated_db, monkeypatch):
    sig = {
        "symbol": "XAU/USD",
        "direction": "SELL",
        "timeframe": "15m",
        "strategy": "institutional_v3",
        "entry": 4555.0,
        "stop": 4570.0,
        "tp1": 4540.0,
        "tp2": 4525.0,
        "tp3": 4505.0,
        "status": "RUNNING",
        "pips_result": 0.0,
    }
    candles = _sample_xau_sell_candles(entry=4555.0, current=4545.0)
    monkeypatch.setattr(charts, "PIL_AVAILABLE", False)
    png = charts.render_proof_chart(sig, candles)
    assert png is not None and png[:8] == b"\x89PNG\r\n\x1a\n"
