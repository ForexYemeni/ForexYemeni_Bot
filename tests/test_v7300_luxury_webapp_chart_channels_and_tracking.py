"""v7.3.0 Regression Suite:
  1. Ultra-Luxury Royal Mini App & Super Admin Web Panel templates + chart-channel APIs
  2. Market-Filtered Chart-Only Channels CRUD, filtering & photo-only delivery
  3. Auto-Luxury Signal Chart sent automatically when a user creates a signal («إشارة الآن»)
  4. Ultra-Luxury Tracking Charts (`render_tracking_chart`) + `tracker.check_open_signals` chart_events
"""
from __future__ import annotations

import asyncio
import json
from types import SimpleNamespace

import pytest

from fysb import config, db
from fysb.engines import market_data, tracker
from fysb.handlers import admin_trading, signal_now
from fysb.services import chart_channels
from fysb.ui import charts
from fysb.webapp import auth as wa_auth
from fysb.webapp import server as wa_server


TEST_BOT_TOKEN = "987654321:v7300_test_token_abcdef"
TEST_ADMIN_ID = 777001
TEST_USER_ID = 777002


@pytest.fixture
def isolated_db(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "DB_FILE", tmp_path / "v7300.db")
    monkeypatch.setattr(config, "DATABASE_URL", "")
    monkeypatch.setattr(config, "BOT_TOKEN", TEST_BOT_TOKEN)
    monkeypatch.setattr(config, "ADMIN_ID", TEST_ADMIN_ID)
    db.init_db()
    with db.tx() as c:
        c.execute(
            "INSERT OR IGNORE INTO users(user_id, username, full_name, joined_at) VALUES (?,?,?,?)",
            (TEST_ADMIN_ID, "adm", "Admin", config.now()),
        )
        c.execute(
            "INSERT OR IGNORE INTO users(user_id, username, full_name, joined_at) VALUES (?,?,?,?)",
            (TEST_USER_ID, "usr", "Trader", config.now()),
        )
    return tmp_path / "v7300.db"


def _sample_candles(base: float = 2650.0, n: int = 30, step: float = 0.6) -> list[dict]:
    out = []
    px = base
    for i in range(n):
        o = px
        c = px + (step if i % 3 != 0 else -step * 0.4)
        h = max(o, c) + 0.8
        l = min(o, c) - 0.6
        out.append({
            "datetime": f"2026-11-25 10:{i:02d}:00",
            "open": round(o, 2),
            "high": round(h, 2),
            "low": round(l, 2),
            "close": round(c, 2),
        })
        px = c
    return out


def _sample_signal(sig_id: int = 101, symbol: str = "XAUUSD", source: str = "SIGNAL_NOW") -> dict:
    return {
        "id": sig_id,
        "symbol": symbol,
        "timeframe": "M15",
        "strategy": "ICT",
        "direction": "BUY",
        "entry": 2650.0,
        "stop": 2642.0,
        "tp1": 2662.0,
        "tp2": 2674.0,
        "tp3": 2690.0,
        "rr": 2.5,
        "score": 88,
        "reason": "Order Block + FVG",
        "status": "RUNNING",
        "hit_tp": 0,
        "pips_result": 0.0,
        "source": source,
        "data_time": "2026-11-25 10:00:00",
        "created_at": "2026-11-25T10:00:00+00:00",
        "filled_at": "2026-11-25 10:02:00",
    }


def test_luxury_webapp_templates_and_chart_channel_api(isolated_db):
    st, _, html_u = wa_server.dispatch_request("GET", "/", {})
    u_txt = html_u.decode("utf-8")
    assert st == 200
    assert 'dir="rtl"' in u_txt
    assert "lightweight-charts" in u_txt
    assert "FOREXYEMENI ROYAL" in u_txt
    assert "ngrok-skip-browser-warning" in u_txt
    assert "createPriceLine" in u_txt

    st, _, html_a = wa_server.dispatch_request("GET", "/admin", {})
    a_txt = html_a.decode("utf-8")
    assert st == 200
    assert "لوحة الإدارة" in a_txt
    assert "قنوات الشارت فقط" in a_txt
    assert "ngrok-skip-browser-warning" in a_txt

    u_hdr = {"X-Telegram-Init-Data": wa_auth.build_init_data({"id": TEST_USER_ID}, TEST_BOT_TOKEN)}
    a_hdr = {"X-Telegram-Init-Data": wa_auth.build_init_data({"id": TEST_ADMIN_ID}, TEST_BOT_TOKEN)}

    # Non-admin blocked
    assert wa_server.dispatch_request(
        "POST", "/api/admin/chart-channel", u_hdr, b'{"op":"add","chat_id":"@gold"}'
    )[0] == 403

    # Admin creates, updates markets, toggles, and deletes a chart-only channel
    st, _, raw = wa_server.dispatch_request(
        "POST", "/api/admin/chart-channel", a_hdr,
        json.dumps({"op": "add", "chat_id": "@GoldOnlyCharts", "title": "ذهب شارت", "markets": "XAUUSD"}).encode(),
    )
    assert st == 200
    cid = json.loads(raw)["id"]

    st, _, ov_raw = wa_server.dispatch_request("GET", "/api/admin/overview", a_hdr)
    ov = json.loads(ov_raw)
    assert st == 200 and any(c["id"] == cid and c["markets"] == "XAUUSD" for c in ov["chart_channels"])

    st, _, raw = wa_server.dispatch_request(
        "POST", "/api/admin/chart-channel", a_hdr,
        json.dumps({"op": "toggle", "id": cid}).encode(),
    )
    assert st == 200 and json.loads(raw)["enabled"] is False

    st, _, raw = wa_server.dispatch_request(
        "POST", "/api/admin/chart-channel", a_hdr,
        json.dumps({"op": "delete", "id": cid}).encode(),
    )
    assert st == 200 and json.loads(raw)["ok"] is True


def test_market_filtered_chart_only_channels_delivery(isolated_db):
    c_gold = chart_channels.add_channel("-100111222", "Gold Charts", "XAUUSD")
    c_btc = chart_channels.add_channel("-100333444", "BTC Charts", "BTCUSD")
    c_all = chart_channels.add_channel("@AllRoyalCharts", "All Charts", "ALL")

    # Toggle market helper works cleanly
    mkts_after = chart_channels.toggle_market(c_gold, "EURUSD")
    assert "XAUUSD" in mkts_after and "EURUSD" in mkts_after
    assert chart_channels.market_matches(mkts_after, "XAU/USD") is True
    assert chart_channels.market_matches(mkts_after, "BTCUSD") is False

    matched_gold = chart_channels.matching_channels("XAUUSD", mode="schedule")
    matched_ids = {c["id"] for c in matched_gold}
    assert matched_ids == {c_gold, c_all}
    assert c_btc not in matched_ids

    class FakeBot:
        def __init__(self):
            self.photos = []
            self.messages = []

        async def send_photo(self, chat_id, photo, caption="", parse_mode=None, **kw):
            self.photos.append((chat_id, len(photo.read()), caption))

        async def send_message(self, *a, **kw):
            self.messages.append((a, kw))

    bot = FakeBot()
    sig_auto = _sample_signal(201, symbol="XAUUSD", source="AUTO")
    sent = asyncio.run(
        chart_channels.deliver_chart_to_matching_channels(
            bot, sig_auto, b"\x89PNG\r\n\x1a\nFAKEPNG", "caption", mode="schedule"
        )
    )
    assert sent == 2
    assert len(bot.messages) == 0  # STRICTLY chart-only, zero text-only messages!
    assert {-100111222, "@AllRoyalCharts"} == {p[0] for p in bot.photos}

    # Private user SIGNAL_NOW must NEVER leak to public chart channels
    bot.photos.clear()
    sig_priv = _sample_signal(202, symbol="XAUUSD", source="SIGNAL_NOW")
    sent_priv = asyncio.run(
        chart_channels.deliver_chart_to_matching_channels(
            bot, sig_priv, b"\x89PNG\r\n\x1a\nFAKEPNG", "caption", mode="tracking", skip_private=True
        )
    )
    assert sent_priv == 0
    assert len(bot.photos) == 0


def test_user_signal_now_auto_sends_luxury_chart(isolated_db, monkeypatch):
    sig = _sample_signal(301, symbol="XAUUSD", source="SIGNAL_NOW")
    with db.tx() as c:
        c.execute(
            """INSERT INTO signals(id, symbol, timeframe, strategy, direction,
               entry, stop, tp1, tp2, tp3, rr, score, reason, status, source, created_at)
               VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            (
                sig["id"], sig["symbol"], sig["timeframe"], sig["strategy"], sig["direction"],
                sig["entry"], sig["stop"], sig["tp1"], sig["tp2"], sig["tp3"],
                sig["rr"], sig["score"], sig["reason"], "ACTIVE", "SIGNAL_NOW", sig["created_at"],
            ),
        )
    db.set_setting("charts_enabled", "1")
    candles = _sample_candles(2650.0, 28)
    monkeypatch.setattr(
        market_data,
        "fetch_candles",
        lambda sym, interval, api_key="", outputsize=72, use_cache=True: {"values": candles, "source": "mock"},
    )

    class FakeBot:
        def __init__(self):
            self.messages = []
            self.photos = []

        async def send_message(self, chat_id, text, **kw):
            self.messages.append((chat_id, text))
            return SimpleNamespace(chat=SimpleNamespace(id=chat_id), message_id=9001)

        async def send_photo(self, chat_id, photo, caption="", **kw):
            raw = photo.read()
            self.photos.append((chat_id, raw, caption))
            return SimpleNamespace(chat=SimpleNamespace(id=chat_id), message_id=9002)

    bot = FakeBot()
    ok = asyncio.run(signal_now._deliver_private_signal(bot, TEST_USER_ID, sig))
    assert ok is True
    assert len(bot.messages) >= 1 and bot.messages[0][0] == TEST_USER_ID
    # User MUST automatically receive the luxury PNG chart right with the signal!
    assert len(bot.photos) == 1
    uid_sent, png_bytes, cap = bot.photos[0]
    assert uid_sent == TEST_USER_ID
    assert png_bytes[:8] == b"\x89PNG\r\n\x1a\n"
    assert "الشارت الاحترافي" in cap and "XAUUSD" in cap


def test_luxury_tracking_chart_and_tracker_chart_events(isolated_db, monkeypatch):
    candles = _sample_candles(2650.0, 32, step=0.9)
    sig = _sample_signal(401, symbol="XAUUSD", source="SIGNAL_NOW")

    # 1) Direct luxury tracking chart PNG rendering for all key milestones
    for ev in ("RUNNING", "HALFWAY", "TP1_HIT", "TP2_HIT", "TP3_HIT", "SL_HIT"):
        png = charts.render_tracking_chart(sig, candles, event=ev)
        assert png is not None and png[:8] == b"\x89PNG\r\n\x1a\n"

    # 2) Full tracker pass produces chart_events targeting the signal owner!
    with db.tx() as c:
        c.execute(
            """INSERT INTO signals(id, symbol, timeframe, strategy, direction,
               entry, stop, tp1, tp2, tp3, rr, score, reason, status, hit_tp, source, created_at, filled_at)
               VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            (
                sig["id"], sig["symbol"], sig["timeframe"], sig["strategy"], sig["direction"],
                2650.0, 2642.0, 2662.0, 2674.0, 2690.0,
                2.5, 90, "ICT", "RUNNING", 0, "SIGNAL_NOW",
                "2026-11-25T10:00:00+00:00", "2026-11-25 10:02:00",
            ),
        )
        c.execute(
            "INSERT INTO signal_deliveries(user_id, signal_id, delivered_at, chat_id, message_id) VALUES (?,?,?,?,?)",
            (TEST_USER_ID, sig["id"], "2026-11-25T10:00:05+00:00", str(TEST_USER_ID), 555),
        )

    # Candles push well above TP1 (2662 + 2 pips confirm) without hitting TP2
    tp1_candles = _sample_candles(2645.0, 25, step=0.4)   # never reaches the +$5 lock level (2655)
    tp1_candles.append({
        "datetime": "2026-11-25 10:26:00",
        "open": 2659.0, "high": 2665.5, "low": 2658.5, "close": 2664.2,
    })
    monkeypatch.setattr(
        tracker,
        "_tracking_payload",
        lambda prov_sym, api_key, since_iso="": {
            "values": tp1_candles,
            "last_candle_iso": "2026-11-25 10:26:00",
            "source": "mock",
        },
    )

    res = tracker.check_open_signals("dummy_key")
    assert res["stats"]["updated"] == 1
    assert len(res["chart_events"]) == 1
    ev = res["chart_events"][0]
    assert ev["event"] == "TP1_HIT"
    assert TEST_USER_ID in ev["targets"]
    assert TEST_ADMIN_ID not in ev["targets"]  # private SIGNAL_NOW isolation preserved!
    assert len(ev["candles"]) >= 10
    assert ev["pips"] > 0


def test_admin_charts_menu_shows_chart_channels_button(isolated_db):
    chart_channels.add_channel("@GoldChartsVIP", "ذهب شارت فقط", "XAUUSD")
    captured = {}

    class FakeMsg:
        async def edit_text(self, text, reply_markup=None, **kw):
            captured["text"] = text
            captured["kb"] = reply_markup

        async def reply_text(self, text, reply_markup=None, **kw):
            captured["text"] = text
            captured["kb"] = reply_markup

    q = SimpleNamespace(
        message=FakeMsg(),
        from_user=SimpleNamespace(id=TEST_ADMIN_ID),
        answer=lambda *a, **k: asyncio.sleep(0),
    )
    ctx = SimpleNamespace(user_data={})
    asyncio.run(admin_trading.admin_charts_menu(q, TEST_ADMIN_ID, ctx, None))
    assert "قنوات الشارت فقط" in captured["text"]
    assert "XAUUSD" in captured["text"]
    cb_data = [
        btn.callback_data
        for row in captured["kb"].inline_keyboard
        for btn in row
        if getattr(btn, "callback_data", None)
    ]
    assert "a:chartchs" in cb_data
