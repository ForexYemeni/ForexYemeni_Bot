"""v7.4.0 End-to-End Verification Suite:
  1. Strategy Leaderboard fixes (public_only filter, real list API shape, Admin-only guard)
  2. Currency Strength Meter fixes (missing currency exclusion, force_refresh, Admin-only)
  3. News Guard fixes (global symbols, honest feed error, Admin Luxury Auto-Broadcast to
     Users + Partners + Channels, Admin toggles & 1-tap immediate bulletin)
  4. Backtest fixes (ALL enabled markets incl. >12 & unverified, pagination, M15/H1/H4,
     real engine_min_score, real per-market spread_pips from market_catalog)
  5. Ultra-Luxury Royal Partner Hub in Mini App (Channel Partner Studio, Affiliate Studio,
     1-tap channel toggle, direct payout request, /partner Mini App button, ?tab=partner)
"""
from __future__ import annotations

import asyncio
import json
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from unittest.mock import patch

import pytest

from fysb import config, db
from fysb.engines import backtest, intelligence, news_guard
from fysb.handlers import admin_partners, admin_trading, dispatcher
from fysb.services import affiliates, leaderboard, partners, users
from fysb.webapp import auth as wa_auth
from fysb.webapp import server as wa_server

ADMIN_ID = 900001
PARTNER_UID = 900002
USER_UID = 900003
BOT_TOKEN = "774400:TEST_V7400_SECRET_TOKEN"


class _FakeBot:
    def __init__(self):
        self.sent: list[tuple] = []

    async def send_message(self, chat_id, text, **kwargs):
        self.sent.append((chat_id, text, kwargs))
        return SimpleNamespace(message_id=len(self.sent))


def _fire_cb(data: str, uid: int, bot: _FakeBot | None = None):
    captured = {"text": "", "kb": None}

    async def _edit(text, reply_markup=None, **kw):
        captured["text"] = text
        captured["kb"] = reply_markup

    async def _answer(*a, **kw):
        return None

    msg = SimpleNamespace(
        chat_id=uid, message_id=42, edit_text=_edit, reply_text=_edit,
        chat=SimpleNamespace(id=uid, type="private"),
    )
    user = SimpleNamespace(id=uid, username=f"u{uid}", first_name="Tester", full_name="Tester")
    q = SimpleNamespace(data=data, from_user=user, message=msg, answer=_answer)
    upd = SimpleNamespace(callback_query=q, effective_user=user, effective_chat=msg.chat)
    ctx = SimpleNamespace(bot=bot or _FakeBot(), user_data={}, chat_data={}, bot_data={}, application=None)
    asyncio.run(dispatcher.buttons(upd, ctx))
    btns = []
    if captured["kb"] and getattr(captured["kb"], "inline_keyboard", None):
        for row in captured["kb"].inline_keyboard:
            for b in row:
                btns.append(getattr(b, "callback_data", None) or getattr(b, "url", None) or "")
    return captured["text"], btns


@pytest.fixture
def env(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "DB_FILE", tmp_path / "v7400.db")
    monkeypatch.setattr(config, "DATABASE_URL", "")
    monkeypatch.setattr(config, "ADMIN_ID", ADMIN_ID)
    monkeypatch.setattr(config, "BOT_TOKEN", BOT_TOKEN)
    monkeypatch.setattr(config, "WEBAPP_URL", "https://royal-test.trycloudflare.com")
    db.init_db()
    news_guard.reset_cache()
    for uid in (ADMIN_ID, PARTNER_UID, USER_UID):
        users.ensure_user(SimpleNamespace(id=uid, username=f"user_{uid}", full_name=f"User {uid}"))
    return tmp_path


def test_v7400_leaderboard_public_only_and_admin_only(env):
    now = config.now()
    with db.conn() as c:
        c.execute(
            """INSERT INTO signals(symbol,timeframe,direction,entry,stop,tp1,tp2,tp3,
               targets,rr,score,strategy,reason,status,source,pips_result,created_at,closed_at)
               VALUES('XAUUSD','M15','BUY',3500,3485,3530,3545,3567,'3530,3545,3567',2.0,92,'ICT','auto','TP2_HIT','AUTO',450.0,?,?)""",
            (now, now),
        )
        c.execute(
            """INSERT INTO signals(symbol,timeframe,direction,entry,stop,tp1,tp2,tp3,
               targets,rr,score,strategy,reason,status,source,pips_result,created_at,closed_at)
               VALUES('EURUSD','M15','SELL',1.10,1.11,1.08,1.07,1.06,'1.08,1.07,1.06',2.0,85,'SCALP','priv','SL_HIT','SIGNAL_NOW',-90.0,?,?)""",
            (now, now),
        )

    lb_all = leaderboard.compute_leaderboard("30d", public_only=False)
    lb_pub = leaderboard.compute_leaderboard("30d", public_only=True)
    assert lb_all["total_trades"] == 2
    assert lb_pub["total_trades"] == 1
    assert lb_pub["strategies"][0]["code"] == "ICT"
    assert lb_pub["strategies"][0]["strategy"] == lb_pub["strategies"][0]["name"]

    # Web API: 403 for user, 200 with real Array rows for Admin
    u_hdr = {"X-Telegram-Init-Data": wa_auth.build_init_data({"id": USER_UID}, BOT_TOKEN)}
    a_hdr = {"X-Telegram-Init-Data": wa_auth.build_init_data({"id": ADMIN_ID}, BOT_TOKEN)}
    assert wa_server.dispatch_request("GET", "/api/leaderboard?period=30d", u_hdr)[0] == 403
    st, _, raw = wa_server.dispatch_request("GET", "/api/leaderboard?period=30d", a_hdr)
    assert st == 200
    body = json.loads(raw)
    assert isinstance(body["rows"], list) and len(body["rows"]) == 2
    assert body["rows"][0]["strategy"]


def test_v7400_currency_strength_missing_exclusion_force_refresh_and_admin_only(env):
    # Only EURUSD returned -> ONLY EUR and USD appear (no fake 0.0% currencies!)
    rows = intelligence.compute_strength_from_pairs({"EURUSD": 0.85})
    codes = {r["code"] for r in rows}
    assert codes == {"EUR", "USD"}
    assert rows[0]["code"] == "EUR" and rows[1]["code"] == "USD"

    # force_refresh bypasses 5-minute cache
    calls = {"n": 0}

    def _fake_pair(ticker, base, quote):
        calls["n"] += 1
        pair = f"{base}{quote}"
        return pair, 0.42 if pair == "EURUSD" else -0.15

    with patch.object(intelligence, "_fetch_pair_day_pct_resilient", side_effect=_fake_pair):
        r1 = intelligence.currency_strength_meter(force_refresh=True)
        n_after_1 = calls["n"]
        r2 = intelligence.currency_strength_meter(force_refresh=False)
        assert calls["n"] == n_after_1  # served from cache
        r3 = intelligence.currency_strength_meter(force_refresh=True)
        assert calls["n"] > n_after_1   # bypassed cache!
        assert len(r1) == 8 and len(r3) == 8

    # Regular users cannot access u:strength and do NOT see admin intelligence buttons in u:signals
    sig_txt, sig_btns = _fire_cb("u:signals", USER_UID)
    assert "u:leaderboard" not in sig_btns
    assert "u:strength" not in sig_btns
    assert "u:news" not in sig_btns
    u_txt, _ = _fire_cb("u:strength", USER_UID)
    assert "لإدارة المنصة فقط" in u_txt


def test_v7400_news_guard_symbols_error_honesty_and_luxury_auto_broadcast(env):
    # Expanded global symbol coverage
    assert news_guard.currencies_for_symbol("GER40") == {"EUR"}
    assert news_guard.currencies_for_symbol("DAX") == {"EUR"}
    assert news_guard.currencies_for_symbol("UK100") == {"GBP"}
    assert news_guard.currencies_for_symbol("JP225") == {"JPY"}
    assert news_guard.currencies_for_symbol("UKOIL") == {"USD"}
    assert news_guard.currencies_for_symbol("XAUUSD") == {"USD"}

    # Honest feed error reporting when feed fails and events list is empty
    news_guard.set_cached_events([])
    with news_guard._LOCK:
        news_guard._CACHE["error"] = "HTTP 429 rate-limit"
    err_html = news_guard.render_news_calendar_html(events=None)
    assert "HTTP 429 rate-limit" in err_html
    with news_guard._LOCK:
        news_guard._CACHE["error"] = ""

    # Prepare upcoming High-Impact event + Channel Partner + Broadcast Channel
    ev_dt = (datetime.now(timezone.utc) + timedelta(minutes=20)).isoformat()
    ev = {
        "title": "US Non-Farm Payrolls (NFP)",
        "currency": "USD",
        "impact": "High",
        "dt_utc": ev_dt,
        "forecast": "185K",
        "previous": "172K",
    }
    news_guard.set_cached_events([ev])
    partners.create_partner(PARTNER_UID, "Royal Channel Partner", commission_pct=30.0)
    with db.conn() as c:
        c.execute(
            "INSERT INTO broadcast_channels(chat_id,title,enabled,created_at,updated_at) VALUES('-100777111','Public News VIP',1,?,?)",
            (config.now(), config.now()),
        )

    # Luxury cards render rich Arabic alert & digest
    alert_html = news_guard.render_luxury_news_alert_card(ev)
    assert "خبر عالي التأثير" in alert_html and "US Non-Farm Payrolls" in alert_html
    digest_html = news_guard.render_luxury_news_digest_card([ev])
    assert "النشرة الاقتصادية الملكية" in digest_html and "185K" in digest_html

    # Broadcast delivers to Admin + Users + Partners + Channels (deduplicated)
    bot = _FakeBot()
    stats = asyncio.run(news_guard.broadcast_news_to_targets(bot, alert_html, include_admin=True))
    chat_ids = {c[0] for c in bot.sent}
    assert ADMIN_ID in chat_ids
    assert PARTNER_UID in chat_ids
    assert USER_UID in chat_ids
    assert -100777111 in chat_ids
    assert stats["partners"] == 1 and stats["users"] == 1 and stats["channels"] == 1

    # Admin callback controls: toggle auto-broadcast, toggle users destination, 1-tap send now
    assert not news_guard.auto_broadcast_enabled()
    txt, btns = _fire_cb("a:newsauto", ADMIN_ID)
    assert news_guard.auto_broadcast_enabled() is True
    assert "a:newssendnow" in btns and "a:newsrefresh" in btns

    _fire_cb("a:newsdest:users", ADMIN_ID)
    assert news_guard.broadcast_destinations()["users"] is False

    bot2 = _FakeBot()
    with patch.object(news_guard, "fetch_events", return_value=[ev]):
        txt_now, _ = _fire_cb("a:newssendnow", ADMIN_ID, bot=bot2)
    sent_ids2 = {c[0] for c in bot2.sent}
    assert USER_UID not in sent_ids2  # users toggle was turned OFF!
    assert PARTNER_UID in sent_ids2 and -100777111 in sent_ids2
    assert "تم إرسال النشرة الاقتصادية الفاخرة" in txt_now


def test_v7400_backtest_all_enabled_markets_pagination_real_spread_and_score(env):
    # Enable 15 markets (some with verified=0 and custom spread_pips)
    now = config.now()
    with db.conn() as c:
        for i in range(1, 16):
            sym = f"MKT{i:02d}USD"
            c.execute(
                """INSERT OR REPLACE INTO market_catalog
                   (symbol,title,category,provider_symbol,spread_pips,enabled,verified,created_at,updated_at)
                   VALUES(?,?,'forex',?,?,1,?,?,?)""",
                (sym, f"Market {i}", sym, round(1.2 + i * 0.2, 2), 0 if i % 2 == 0 else 1, now, now),
            )

    all_mkts = admin_trading._enabled_backtest_markets()
    assert len(all_mkts) >= 15
    assert "MKT02USD" in all_mkts  # verified=0 MUST still appear when enabled=1!
    assert "MKT15USD" in all_mkts  # no LIMIT 12 truncation!

    # Admin Backtest UI paginates cleanly and includes M15, H1, H4 buttons
    txt_p0, btns_p0 = _fire_cb("a:backtest:0", ADMIN_ID)
    assert "صفحة 1/" in txt_p0
    assert any(":H4" in b for b in btns_p0)
    assert "a:backtest:1" in btns_p0
    txt_p1, _ = _fire_cb("a:backtest:1", ADMIN_ID)
    assert "صفحة 2/" in txt_p1

    # Effective min score reads engine_min_score
    db.set_setting("engine_min_score", "84")
    assert admin_trading._effective_backtest_min_score() == 84

    # Real per-market spread_pips lookup from market_catalog
    resolved_spread, _ = backtest._market_catalog_defaults("MKT05USD")
    assert resolved_spread == pytest.approx(2.2, abs=0.01)


def test_v7400_ultra_luxury_partner_hub_in_miniapp_and_admin_panel(env):
    # Register Channel Partner + link a channel + credit affiliate balance
    p_info = partners.create_partner(PARTNER_UID, "Yemen Royal Signals", commission_pct=25.0)
    with db.conn() as c:
        ch_id = c.execute(
            """INSERT INTO broadcast_channels(chat_id,title,enabled,owner_partner_id,markets,post_mode,created_at,updated_at)
               VALUES('-100888222','Yemen Gold VIP',1,?,'XAUUSD','teaser',?,?)""",
            (p_info["id"], config.now(), config.now()),
        ).lastrowid
    affiliates.bind_referral(PARTNER_UID, USER_UID)
    affiliates.record_commission_on_payment(USER_UID, 200.0, payment_id=1)

    # 1) Mini App HTML has Ultra-Luxury Partner Hub & NO user-exposed Intel pane
    st, _, html_u = wa_server.dispatch_request("GET", "/", {})
    html_s = html_u.decode("utf-8")
    assert st == 200
    assert 'id="pane-partner"' in html_s
    assert 'id="tab-partner"' in html_s
    assert 'id="chPartnerWrap"' in html_s
    assert 'id="pane-intel"' not in html_s

    # 2) /api/me returns full Channel Partner Studio + Affiliate referral link
    p_hdr = {"X-Telegram-Init-Data": wa_auth.build_init_data({"id": PARTNER_UID}, BOT_TOKEN)}
    u_hdr = {"X-Telegram-Init-Data": wa_auth.build_init_data({"id": USER_UID}, BOT_TOKEN)}
    a_hdr = {"X-Telegram-Init-Data": wa_auth.build_init_data({"id": ADMIN_ID}, BOT_TOKEN)}

    st_me, _, raw_me = wa_server.dispatch_request("GET", "/api/me", p_hdr)
    me = json.loads(raw_me)
    assert st_me == 200 and me["partner"]["is_channel_partner"] is True
    assert me["partner"]["title"] == "Yemen Royal Signals"
    assert me["partner"]["commission_pct"] == 25.0
    assert len(me["partner"]["channels"]) == 1
    assert me["partner"]["channels"][0]["id"] == ch_id
    assert "start=aff" in me["affiliate"]["referral_link"]

    # Non-partner user sees is_channel_partner=False but still gets full Affiliate Studio
    st_ume, _, raw_ume = wa_server.dispatch_request("GET", "/api/me", u_hdr)
    ume = json.loads(raw_ume)
    assert st_ume == 200 and ume["partner"]["is_channel_partner"] is False
    assert "start=aff" in ume["affiliate"]["referral_link"]

    # 3) Partner toggles their own channel ON -> OFF via Mini App API; non-partner gets 403
    assert wa_server.dispatch_request(
        "POST", "/api/partner/channel-toggle", u_hdr, json.dumps({"channel_id": ch_id}).encode()
    )[0] == 403
    st_tg, _, raw_tg = wa_server.dispatch_request(
        "POST", "/api/partner/channel-toggle", p_hdr, json.dumps({"channel_id": ch_id}).encode()
    )
    assert st_tg == 200 and json.loads(raw_tg)["enabled"] is False

    # 4) Partner submits USDT payout request directly from Mini App
    st_po, _, raw_po = wa_server.dispatch_request(
        "POST", "/api/partner/payout", p_hdr, b'{"wallet_address":"TQn9Y2khEsLJW1ChVWFMSMeRDow5KcbLSE"}'
    )
    po = json.loads(raw_po)
    assert st_po == 200 and po["ok"] is True and po["amount_usd"] > 0

    # 5) /partner Telegram home prepends Mini App Royal Partner Hub button (?tab=partner)
    p_row = partners.get_partner_by_user(PARTNER_UID)
    _, markup = asyncio.run(admin_partners._build_partner_home(p_row))
    first_btn = markup.inline_keyboard[0][0]
    assert first_btn.web_app is not None
    assert first_btn.web_app.url.endswith("/?tab=partner")

    # 6) Admin Web Panel exposes news_auto_broadcast toggle, instant broadcast & Admin leaderboard
    st_adm_html, _, html_a = wa_server.dispatch_request("GET", "/admin", {})
    assert st_adm_html == 200
    assert "sendLuxuryNewsNow" in html_a.decode("utf-8")
    assert "adminLbBox" in html_a.decode("utf-8")

    st_ov, _, raw_ov = wa_server.dispatch_request("GET", "/api/admin/overview", a_hdr)
    ov = json.loads(raw_ov)
    assert st_ov == 200 and "news_auto_broadcast" in ov["toggles"]
    assert "leaderboard" in ov and "news_events" in ov

    st_nb, _, raw_nb = wa_server.dispatch_request("POST", "/api/admin/news-broadcast", a_hdr, b"{}")
    assert st_nb == 200 and json.loads(raw_nb)["ok"] is True
