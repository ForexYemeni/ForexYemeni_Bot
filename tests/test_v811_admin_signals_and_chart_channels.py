"""v8.1.1 Regression Suite — Verified Chart Channels & Luxury Admin Signals Filters."""
from __future__ import annotations

import json
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import tests.helpers  # noqa: E402,F401


@pytest.fixture
def fresh_db(tmp_path, monkeypatch):
    from fysb import config, db
    monkeypatch.setattr(config, "DB_FILE", tmp_path / "v811.db")
    monkeypatch.setattr(config, "DATABASE_URL", "")
    monkeypatch.setattr(config, "USE_PG", False)
    db.init_db()
    return tmp_path


def _admin_headers():
    from fysb import config
    from fysb.webapp import auth as wa_auth
    init_data = wa_auth.build_init_data(
        {"id": config.ADMIN_ID, "username": "boss", "first_name": "Admin"},
        config.BOT_TOKEN,
    )
    return {"x-telegram-init-data": init_data, "content-type": "application/json"}


class _FakeBot:
    def __init__(self, *, status="administrator", can_post=True, chat_id=-1009988776655, title="Gold Luxury Charts"):
        self.status = status
        self.can_post = can_post
        self.chat_id = chat_id
        self.title = title
        self.sent_messages = []
        self.sent_photos = []

    async def get_chat(self, target):
        if str(target) == "@missing_chan":
            raise RuntimeError("Bad Request: chat not found")
        return SimpleNamespace(id=self.chat_id, title=self.title, username="goldlux", type="channel")

    async def get_me(self):
        return SimpleNamespace(id=999000)

    async def get_chat_member(self, chat_id, user_id):
        return SimpleNamespace(status=self.status, can_post_messages=self.can_post)

    async def send_message(self, chat_id, text, **kwargs):
        self.sent_messages.append((chat_id, text))
        return SimpleNamespace(message_id=101)

    async def send_photo(self, chat_id, photo, caption="", **kwargs):
        self.sent_photos.append((chat_id, caption))
        return SimpleNamespace(message_id=102)


def test_normalize_chart_channel_inputs():
    from fysb.services.chart_channels import normalize_chat_input

    assert normalize_chat_input("@MyGold") == "@MyGold"
    assert normalize_chat_input("https://t.me/MyGold/") == "@MyGold"
    assert normalize_chat_input("t.me/MyGold?start=1") == "@MyGold"
    assert normalize_chat_input("https://t.me/c/1234567890/42") == "-1001234567890"
    assert normalize_chat_input("MyGoldCharts") == "@MyGoldCharts"
    assert normalize_chat_input("1001234567890") == "-1001234567890"
    assert normalize_chat_input("-1001234567890") == "-1001234567890"
    with pytest.raises(ValueError):
        normalize_chat_input("https://t.me/+privateInviteCode")


def test_chart_channel_real_verification_and_test_send(fresh_db):
    from fysb.services import chart_channels
    from fysb.webapp import server

    server.reset_rate_limits()
    headers = _admin_headers()

    # 1. Reject channel when bot is NOT an admin (must NOT insert fake row)
    bad_bot = _FakeBot(status="member", can_post=False)
    server.set_bot_runtime(bad_bot, None)
    try:
        st, _, raw = server.dispatch_request(
            "POST",
            "/api/admin/chart-channel",
            headers,
            json.dumps({"op": "add", "chat_id": "https://t.me/goldlux", "markets": "XAUUSD"}).encode(),
        )
        data = json.loads(raw.decode())
        assert st == 400
        assert data["ok"] is False
        assert "مشرف" in data["error"]
        assert chart_channels.list_channels(False) == []

        # 2. Reject channel when bot is admin but lacks can_post_messages
        no_post_bot = _FakeBot(status="administrator", can_post=False)
        server.set_bot_runtime(no_post_bot, None)
        st2, _, raw2 = server.dispatch_request(
            "POST",
            "/api/admin/chart-channel",
            headers,
            json.dumps({"op": "add", "chat_id": "@goldlux", "markets": "XAUUSD"}).encode(),
        )
        data2 = json.loads(raw2.decode())
        assert st2 == 400
        assert data2["ok"] is False
        assert "نشر" in data2["error"]
        assert chart_channels.list_channels(False) == []

        # 3. Accept when bot IS admin with posting rights -> resolves real numeric ID & title + sends test!
        good_bot = _FakeBot(status="administrator", can_post=True, chat_id=-1005544332211, title="قناة الذهب الفاخرة")
        server.set_bot_runtime(good_bot, None)
        st3, _, raw3 = server.dispatch_request(
            "POST",
            "/api/admin/chart-channel",
            headers,
            json.dumps({"op": "add", "chat_id": "https://t.me/goldlux", "markets": "XAUUSD"}).encode(),
        )
        data3 = json.loads(raw3.decode())
        assert st3 == 200
        assert data3["ok"] is True
        assert data3["verified"] is True
        assert data3["chat_id"] == "-1005544332211"
        assert data3["title"] == "قناة الذهب الفاخرة"
        assert data3["test_sent"] is True
        assert (len(good_bot.sent_messages) + len(good_bot.sent_photos)) >= 1

        # Re-adding the same channel deduplicates rather than creating a second row
        st4, _, raw4 = server.dispatch_request(
            "POST",
            "/api/admin/chart-channel",
            headers,
            json.dumps({"op": "add", "chat_id": "-1005544332211", "markets": "ALL", "send_test": False}).encode(),
        )
        data4 = json.loads(raw4.decode())
        assert st4 == 200
        assert len(chart_channels.list_channels(False)) == 1
        assert chart_channels.list_channels(False)[0]["markets"] == "ALL"

        # On-demand test endpoint works
        cid = data4["id"]
        st5, _, raw5 = server.dispatch_request(
            "POST",
            "/api/admin/chart-channel",
            headers,
            json.dumps({"op": "test", "id": cid}).encode(),
        )
        data5 = json.loads(raw5.decode())
        assert st5 == 200
        assert data5["ok"] is True

        # Schedule endpoint updates setting cleanly
        st6, _, raw6 = server.dispatch_request(
            "POST",
            "/api/admin/chart-channel",
            headers,
            json.dumps({"op": "schedule", "minutes": 15}).encode(),
        )
        assert st6 == 200
        assert json.loads(raw6.decode())["minutes"] == 15
    finally:
        server.set_bot_runtime(None, None)


def test_admin_signals_dropdown_filters_and_outcome_classification(fresh_db):
    from fysb import db
    from fysb.webapp import server

    server.reset_rate_limits()
    headers = _admin_headers()

    now_u = datetime.now(timezone.utc)
    iso_now = now_u.isoformat()
    iso_10d = (now_u - timedelta(days=10)).isoformat()

    with db.tx() as c:
        c.execute(
            "INSERT INTO users(user_id, username, full_name, joined_at) VALUES(?,?,?,?)",
            (701, "ali_gold", "Ali Gold", iso_now),
        )
        c.execute(
            "INSERT INTO users(user_id, username, full_name, joined_at) VALUES(?,?,?,?)",
            (702, "sara_fx", "Sara FX", iso_now),
        )
        # 1) XAUUSD today, user 701, TP1_BE -> MUST be classified as WIN (NOT loss!)
        c.execute(
            """INSERT INTO signals(symbol,direction,timeframe,entry,stop,tp1,tp2,tp3,rr,score,strategy,status,source,created_by,created_at,pips_result,closed_at)
               VALUES('XAUUSD','BUY','M15',2650,2640,2665,2680,2695,2.0,85,'TREND','TP1_BE','SIGNAL_NOW',701,?,45.0,?)""",
            (iso_now, iso_now),
        )
        # 2) XAUUSD today, user 701, EXPIRED -> MUST be classified as NEUTRAL (NOT loss!)
        c.execute(
            """INSERT INTO signals(symbol,direction,timeframe,entry,stop,tp1,tp2,tp3,rr,score,strategy,status,source,created_by,created_at,pips_result,closed_at)
               VALUES('XAUUSD','SELL','M15',2655,2665,2640,2625,2610,2.0,82,'CLASSIC','EXPIRED','SIGNAL_NOW',701,?,0.0,?)""",
            (iso_now, iso_now),
        )
        # 3) XAUUSD 10 days ago, user 702, SL_HIT -> true LOSS
        c.execute(
            """INSERT INTO signals(symbol,direction,timeframe,entry,stop,tp1,tp2,tp3,rr,score,strategy,status,source,created_by,created_at,pips_result,closed_at)
               VALUES('XAUUSD','BUY','H1',2620,2610,2640,2660,2680,2.0,80,'TREND','SL_HIT','SIGNAL_NOW',702,?,-30.0,?)""",
            (iso_10d, iso_10d),
        )
        # 4) EURUSD today, AUTO engine (created_by=0), RUNNING -> OPEN
        c.execute(
            """INSERT INTO signals(symbol,direction,timeframe,entry,stop,tp1,tp2,tp3,rr,score,strategy,status,source,created_by,created_at,pips_result,closed_at)
               VALUES('EURUSD','BUY','H1',1.0850,1.0820,1.0910,1.0940,1.0980,2.0,88,'SMC','RUNNING','AUTO',0,?,0.0,'')""",
            (iso_now,),
        )

    # A) All signals — check exact outcome counts & metadata
    st, _, raw = server.dispatch_request("GET", "/api/admin/signals", headers, b"")
    assert st == 200
    d = json.loads(raw.decode())
    assert d["stats"]["total"] == 4
    assert d["stats"]["wins"] == 1        # TP1_BE counted as WIN!
    assert d["stats"]["losses"] == 1      # ONLY SL_HIT counted as LOSS!
    assert d["stats"]["neutral"] == 1     # EXPIRED counted as NEUTRAL!
    assert d["stats"]["open"] == 1        # RUNNING counted as OPEN!
    assert d["stats"]["win_rate"] == 50.0 # 1 win / (1 win + 1 loss)
    assert d["filters"]["symbols"][0]["symbol"] == "XAUUSD"
    assert d["filters"]["auto_count"] == 1
    assert len(d["filters"]["users"]) == 2

    # B) Gold dropdown filter (XAUUSD)
    st_g, _, raw_g = server.dispatch_request("GET", "/api/admin/signals?symbol=XAUUSD", headers, b"")
    dg = json.loads(raw_g.decode())
    assert st_g == 200
    assert dg["stats"]["total"] == 3
    assert all(s["symbol"] == "XAUUSD" for s in dg["signals"])

    # C) Period filter (7d -> excludes the 10-day-old SL_HIT!)
    st_p, _, raw_p = server.dispatch_request("GET", "/api/admin/signals?symbol=XAUUSD&period=7d", headers, b"")
    dp = json.loads(raw_p.decode())
    assert st_p == 200
    assert dp["stats"]["total"] == 2
    assert dp["stats"]["wins"] == 1
    assert dp["stats"]["losses"] == 0
    assert dp["stats"]["neutral"] == 1

    # D) User dropdown filter (user_id=701 vs auto)
    st_u, _, raw_u = server.dispatch_request("GET", "/api/admin/signals?user_id=701", headers, b"")
    du = json.loads(raw_u.decode())
    assert st_u == 200
    assert du["stats"]["total"] == 2
    assert all(int(s["created_by"]) == 701 for s in du["signals"])

    st_a, _, raw_a = server.dispatch_request("GET", "/api/admin/signals?user_id=auto", headers, b"")
    da = json.loads(raw_a.decode())
    assert st_a == 200
    assert da["stats"]["total"] == 1
    assert da["signals"][0]["symbol"] == "EURUSD"

    # E) Neutral status filter returns ONLY EXPIRED
    st_n, _, raw_n = server.dispatch_request("GET", "/api/admin/signals?status=neutral", headers, b"")
    dn = json.loads(raw_n.decode())
    assert st_n == 200
    assert len(dn["signals"]) == 1
    assert dn["signals"][0]["status"] == "EXPIRED"


def test_admin_html_contains_luxury_dropdowns_and_chart_test_controls():
    from fysb.webapp import templates

    html = templates.render_admin_panel_html()
    for token in (
        "sigSymDrop",
        "sigPeriodDrop",
        "sigUserDrop",
        "XAUUSD",
        "loadAdminSignalsFiltered",
        "testChartCh",
        "setChartSched",
        "TP1_BE",
        "EXPIRED",
    ):
        assert token in html, f"missing {token} in admin HTML"
