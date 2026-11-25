"""v7.5.0 Regression & Feature Tests:
1. User Home luxury Auto-Signals Green (OFF) / Red (ON) toggle + Mini App sync.
2. Luxury Signal Management redesign (semantic colors, non-clipping text, creator attribution, achieved metrics).
3. User-generated signals always share luxury Pillow chart + interactive 'تجريبي الآن' bar.
4. Live in-place card updates (ACTIVE -> RUNNING -> TP1/BE) with preserved inline buttons.
5. One-tap 'تجريبي الآن' -> Mini App (?demo_sig=<id>) live chart activation + unified Demo API + Admin Signals Board.
"""
import asyncio
import hashlib
import hmac
import json
from types import SimpleNamespace
from urllib.parse import urlencode

import pytest

from fysb import config, db
from fysb.engines import demo as demo_engine
from fysb.engines import tracker
from fysb.handlers import background, dispatcher, signal_now
from fysb.services import subscriptions, users as users_svc
from fysb.ui import cards
from fysb.ui import keyboards as kb
from fysb.webapp import server as wa_server
from fysb.webapp import templates as wa_templates


ADMIN_UID = 990001
USER_UID = 990002
BOT_TOK = "999999:TEST_TOKEN_V7500"


def _make_init_data(uid: int, username: str = "trader", first_name: str = "Ali") -> str:
    import time
    user_json = json.dumps({"id": uid, "username": username, "first_name": first_name}, separators=(",", ":"))
    pairs = {"auth_date": str(int(time.time())), "query_id": "AAHdF6IQAAAAAN0XohDhrOrc", "user": user_json}
    data_check = "\n".join(f"{k}={v}" for k, v in sorted(pairs.items()))
    secret = hmac.new(b"WebAppData", BOT_TOK.encode("utf-8"), hashlib.sha256).digest()
    sig = hmac.new(secret, data_check.encode("utf-8"), hashlib.sha256).hexdigest()
    pairs["hash"] = sig
    return urlencode(pairs)


@pytest.fixture()
def env(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "DB_FILE", tmp_path / "v7500.db")
    monkeypatch.setattr(config, "DATABASE_URL", "")
    monkeypatch.setattr(config, "BOT_TOKEN", BOT_TOK)
    monkeypatch.setattr(config, "ADMIN_ID", ADMIN_UID)
    monkeypatch.setattr(config, "WEBAPP_URL_STATIC", "")
    monkeypatch.setattr(config, "WEBAPP_URL", "")
    monkeypatch.setattr(config, "WEBAPP_GH_PAGES_ENABLED", False)
    db.init_db()
    users_svc.ensure_user(SimpleNamespace(id=ADMIN_UID, username="boss", full_name="Admin Boss"))
    users_svc.ensure_user(SimpleNamespace(id=USER_UID, username="yemen_pro", full_name="أحمد اليماني"))
    subscriptions.grant_subscription(USER_UID, "VIP", source="TEST", override_days=30)
    yield tmp_path
    config.set_runtime_webapp_url("", source="")


def _insert_signal(symbol="XAUUSD", direction="BUY", status="RUNNING", source="SIGNAL_NOW",
                   created_by=USER_UID, pips_result=0.0, hit_tp=0) -> int:
    with db.tx() as c:
        cur = c.execute(
            """INSERT INTO signals(symbol,direction,timeframe,entry,stop,tp1,tp2,tp3,targets,rr,score,
               strategy,reason,status,source,created_by,created_at,pips_result,hit_tp)
               VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            (symbol, direction, "M15", 2650.0, 2640.0, 2665.0, 2680.0, 2700.0,
             "2665,2680,2700", 2.5, 94, "SMC", "BOS + OB", status, source, created_by,
             config.now(), pips_result, hit_tp),
        )
        return int(cur.lastrowid)


def test_user_home_autosig_green_red_toggle_and_miniapp_sync(env):
    # 1. Button color semantics: OFF -> Emerald Green ('success'), ON -> Ruby Red ('danger')
    kb_off = kb.user_menu(auto_signals_on=False)
    kb_on = kb.user_menu(auto_signals_on=True)
    btn_off = [b for row in kb_off.inline_keyboard for b in row if b.callback_data == "u:autosig_toggle"][0]
    btn_on = [b for row in kb_on.inline_keyboard for b in row if b.callback_data == "u:autosig_toggle"][0]
    assert btn_off.to_dict()["style"] == "success"
    assert "تشغيل الإشارات التلقائية" in btn_off.text
    assert btn_on.to_dict()["style"] == "danger"
    assert "إيقاف الإشارات التلقائية" in btn_on.text

    # 2. Clicking u:autosig_toggle flips DB & button color immediately
    captured = {}

    async def _edit(text, reply_markup=None, **kw):
        captured["text"] = text
        captured["markup"] = reply_markup

    async def _ans(*a, **kw):
        captured["toast"] = a[0] if a else ""

    q = SimpleNamespace(
        data="u:autosig_toggle",
        from_user=SimpleNamespace(id=USER_UID, username="yemen_pro", full_name="أحمد اليماني"),
        message=SimpleNamespace(chat_id=USER_UID, message_id=10, edit_text=_edit, reply_text=_edit),
        answer=_ans,
    )
    upd = SimpleNamespace(callback_query=q, effective_user=q.from_user)
    ctx = SimpleNamespace(bot=SimpleNamespace(), user_data={})

    # Initially notifications=1 -> toggle turns OFF -> GREEN button appears
    asyncio.run(dispatcher.buttons(upd, ctx))
    assert users_svc.get_user(USER_UID)["notifications"] == 0
    b_after_off = [b for r in captured["markup"].inline_keyboard for b in r if b.callback_data == "u:autosig_toggle"][0]
    assert b_after_off.to_dict()["style"] == "success"

    # Toggle again -> turns ON -> RED button appears
    asyncio.run(dispatcher.buttons(upd, ctx))
    assert users_svc.get_user(USER_UID)["notifications"] == 1
    b_after_on = [b for r in captured["markup"].inline_keyboard for b in r if b.callback_data == "u:autosig_toggle"][0]
    assert b_after_on.to_dict()["style"] == "danger"

    # 3. Mini App /api/user/auto-signals syncs the exact same DB column
    hdr = {"x-telegram-init-data": _make_init_data(USER_UID, "yemen_pro", "أحمد اليماني")}
    st, _, raw = wa_server.dispatch_request("POST", "/api/user/auto-signals", hdr, b"{}")
    assert st == 200 and json.loads(raw)["auto_signals"] is False
    assert users_svc.get_user(USER_UID)["notifications"] == 0


def test_luxury_signals_management_colors_creator_and_dossier(env):
    sid_run = _insert_signal("XAUUSD", "BUY", "RUNNING", "SIGNAL_NOW", USER_UID, pips_result=0.0)
    sid_win = _insert_signal("EURUSD", "SELL", "TP2_HIT", "AUTO", 0, pips_result=48.5, hit_tp=2)
    sid_sl = _insert_signal("ETHUSD", "SELL", "SL_HIT", "MANUAL", ADMIN_UID, pips_result=-21.0)

    with db.conn() as c:
        rows = {r["id"]: dict(r) for r in c.execute("SELECT * FROM signals").fetchall()}

    assert cards.signal_row_style(rows[sid_win]) == "success"
    assert cards.signal_row_style(rows[sid_run]) == "primary"
    assert cards.signal_row_style(rows[sid_sl]) == "danger"

    # Compact button text is crisp, mobile-safe, no HTML, no 🔴 hijacking color
    for r in rows.values():
        lbl = cards.render_signal_compact(r)
        assert len(lbl) <= 52
        assert "<b>" not in lbl and "🔴" not in lbl

    # Creator attribution resolves exact user, auto engine, and admin
    assert "أحمد اليماني" in cards.signal_creator_label(rows[sid_run])
    assert "@yemen_pro" in cards.signal_creator_label(rows[sid_run])
    assert "المحرك التلقائي" in cards.signal_creator_label(rows[sid_win])
    assert "الإدارة" in cards.signal_creator_label(rows[sid_sl])

    # Admin signals center renders creator + semantic colored rows
    captured = {}

    async def _edit(text, reply_markup=None, **kw):
        captured["text"] = text
        captured["markup"] = reply_markup

    async def _ans(*a, **kw):
        pass

    q = SimpleNamespace(
        data="a:signals",
        from_user=SimpleNamespace(id=ADMIN_UID, username="boss", full_name="Admin Boss"),
        message=SimpleNamespace(chat_id=ADMIN_UID, message_id=20, edit_text=_edit, reply_text=_edit),
        answer=_ans,
    )
    upd = SimpleNamespace(callback_query=q, effective_user=q.from_user)
    ctx = SimpleNamespace(bot=SimpleNamespace(), user_data={})
    asyncio.run(dispatcher.buttons(upd, ctx))
    assert "أحمد اليماني" in captured["text"]
    assert "مركز إدارة الإشارات الملكي" in captured["text"]
    sig_btns = {b.callback_data: b for r in captured["markup"].inline_keyboard for b in r if (b.callback_data or "").startswith("a:signalview:")}
    assert sig_btns[f"a:signalview:{sid_win}"].to_dict()["style"] == "success"
    assert sig_btns[f"a:signalview:{sid_run}"].to_dict()["style"] == "primary"
    assert sig_btns[f"a:signalview:{sid_sl}"].to_dict()["style"] == "danger"


def test_user_signal_generation_always_shares_luxury_chart_and_demo_button(env, monkeypatch):
    # Even if global auto-broadcast charts_enabled=0, user-generated signal MUST still share chart!
    db.set_setting("charts_enabled", "0")
    sid = _insert_signal("XAUUSD", "BUY", "ACTIVE", "SIGNAL_NOW", USER_UID)
    with db.conn() as c:
        sig = dict(c.execute("SELECT * FROM signals WHERE id=?", (sid,)).fetchone())

    monkeypatch.setattr(
        "fysb.handlers.user_pages.build_signal_chart_bundle",
        lambda s, fp=False: {"png": b"\x89PNG\r\n\x1a\nFAKE_CHART", "metrics": {"rr": 2.5}},
    )
    sent = {"msg_kb": None, "photos": []}

    class _FakeBot:
        async def send_message(self, chat_id, text, parse_mode=None, reply_markup=None):
            sent["msg_kb"] = reply_markup
            return SimpleNamespace(chat_id=chat_id, message_id=777)

        async def send_photo(self, chat_id, photo, caption="", parse_mode=None, reply_markup=None):
            sent["photos"].append({"chat_id": chat_id, "bytes": photo.read(), "caption": caption, "kb": reply_markup})
            return SimpleNamespace(chat_id=chat_id, message_id=778)

    ok = asyncio.run(signal_now._deliver_private_signal(_FakeBot(), USER_UID, sig))
    assert ok is True
    assert sent["msg_kb"] is not None
    assert any("تجريبي الآن" in b.text for r in sent["msg_kb"].inline_keyboard for b in r)
    assert len(sent["photos"]) == 1
    assert sent["photos"][0]["bytes"].startswith(b"\x89PNG")


def test_live_in_place_card_update_preserves_buttons_and_status_ribbon(env):
    sid = _insert_signal("XAUUSD", "BUY", "RUNNING", "AUTO", 0)
    with db.conn() as c:
        sig = dict(c.execute("SELECT * FROM signals WHERE id=?", (sid,)).fetchone())

    txt_run = tracker._base_live_card_text(sig, 2660.0)
    assert "قيد التنفيذ في السوق الآن" in txt_run
    assert "التقدم نحو الأهداف" in txt_run

    sig_pending = dict(sig, status="ACTIVE")
    txt_pend = tracker._base_live_card_text(sig_pending, 2647.0)
    assert "بانتظار التنفيذ" in txt_pend

    refs = [{"user_id": USER_UID, "chat_id": str(USER_UID), "message_id": 501}]
    edits = tracker._card_refresh_edits(sig, 2660.0, refs)
    assert len(edits) == 1
    uid, cid, mid, body = edits[0]

    edited_calls = []

    class _EditBot:
        async def edit_message_text(self, text, **kwargs):
            edited_calls.append((text, kwargs))

    ok = asyncio.run(background._safe_edit(_EditBot(), cid, mid, body))
    assert ok is True
    assert len(edited_calls) == 1
    mk = edited_calls[0][1].get("reply_markup")
    assert mk is not None
    assert any("تجريبي الآن" in b.text for r in mk.inline_keyboard for b in r)


def test_demo_now_webapp_deep_link_and_live_demo_api_roundtrip(env, monkeypatch):
    # 1. Deep link URL carries ?demo_sig=<id> when HTTPS Mini App is active
    config.set_runtime_webapp_url("https://trombone-blitz-chain.ngrok-free.dev", source="ngrok")
    sid = _insert_signal("XAUUSD", "BUY", "RUNNING", "AUTO", 0)
    btn_web = kb.signal_demo_launch_btn(sid)
    assert btn_web.web_app is not None
    assert btn_web.web_app.url == f"https://trombone-blitz-chain.ngrok-free.dev/?demo_sig={sid}"

    # 2. Mini App API: open-signal -> idempotent 2nd open -> overview -> breakeven -> close
    monkeypatch.setattr(
        "fysb.engines.market_data.fetch_price",
        lambda sym, key="": {"price": 2655.0, "fetched_at": config.now()},
    )
    u_hdr = {"x-telegram-init-data": _make_init_data(USER_UID, "yemen_pro", "أحمد اليماني")}
    st, _, raw = wa_server.dispatch_request("POST", "/api/demo/open-signal", u_hdr, json.dumps({"signal_id": sid}).encode())
    assert st == 200
    d1 = json.loads(raw)
    assert d1["ok"] is True and d1["already_open"] is False
    pid = d1["position_id"]

    # Idempotent repeat tap focuses existing live trade
    st2, _, raw2 = wa_server.dispatch_request("POST", "/api/demo/open-signal", u_hdr, json.dumps({"signal_id": sid}).encode())
    assert st2 == 200 and json.loads(raw2)["already_open"] is True

    # Overview synced with unified Telegram demo account
    st_ov, _, raw_ov = wa_server.dispatch_request("GET", "/api/demo/overview", u_hdr)
    assert st_ov == 200
    ov = json.loads(raw_ov)
    assert len(ov["open"]) == 1 and ov["open"][0]["id"] == pid

    # Breakeven moves SL to entry
    st_be, _, raw_be = wa_server.dispatch_request("POST", "/api/demo/breakeven", u_hdr, json.dumps({"position_id": pid}).encode())
    assert st_be == 200 and json.loads(raw_be)["ok"] is True

    # Close settles PnL cleanly
    st_cl, _, raw_cl = wa_server.dispatch_request("POST", "/api/demo/close", u_hdr, json.dumps({"position_id": pid}).encode())
    assert st_cl == 200 and json.loads(raw_cl)["ok"] is True

    # 3. Admin overview returns enriched recent_signals with creator & demo_count
    a_hdr = {"x-telegram-init-data": _make_init_data(ADMIN_UID, "boss", "Admin Boss")}
    st_a, _, raw_a = wa_server.dispatch_request("GET", "/api/admin/overview", a_hdr)
    assert st_a == 200
    aov = json.loads(raw_a)
    assert len(aov["recent_signals"]) >= 1
    assert aov["recent_signals"][0]["demo_count"] == 1
    assert "creator" in aov["recent_signals"][0]

    # 4. Templates contain the v7.5.0 Live Demo HUD, Auto-Signals Toggle, & v7.5.1 5-Page Admin Command App
    u_html = wa_templates.render_user_miniapp_html()
    a_html = wa_templates.render_admin_panel_html()
    assert "demo_sig" in u_html and "activateDemoSignal" in u_html and "toggleAutoSignals" in u_html
    assert "adminSignalsBox" in a_html and "openSigDemoChart" in a_html
    for pane_id in ("apane-home", "apane-signals", "apane-finance", "apane-channels", "apane-analytics"):
        assert f'id="{pane_id}"' in a_html
    for tab_id in ("atab-home", "atab-signals", "atab-finance", "atab-channels", "atab-analytics"):
        assert f'id="{tab_id}"' in a_html
    assert "function openAdminTab(" in a_html


def test_clear_and_new_token_always_reset_stuck_gh_pages_flag(env, monkeypatch):
    """v7.5.1 regression: `clear` and pasting a new URL/token MUST reset `webapp_gh_pages_enabled` to 0."""
    from fysb.handlers import inputs
    from fysb.webapp import tunnel as _tun

    db.set_setting("webapp_gh_pages_enabled", "1")
    config.WEBAPP_GH_PAGES_ENABLED = True
    monkeypatch.setattr(_tun, "stop_tunnel", lambda: asyncio.sleep(0))

    replies = []

    async def _reply(text, **kw):
        replies.append((text, kw))

    upd = SimpleNamespace(
        effective_user=SimpleNamespace(id=ADMIN_UID, username="boss", full_name="Admin Boss"),
        effective_message=SimpleNamespace(text="clear", reply_text=_reply),
    )
    ctx = SimpleNamespace(user_data={"awaiting_webapp_url": True}, bot=SimpleNamespace())
    handled = asyncio.run(inputs._admin_text(upd, ctx, ADMIN_UID, "clear"))
    assert handled is True
    assert db.get_setting("webapp_gh_pages_enabled", "1") == "0"
    assert config.WEBAPP_GH_PAGES_ENABLED is False

    # Also verify pasting a custom URL resets a stuck GH Pages flag to 0
    db.set_setting("webapp_gh_pages_enabled", "1")
    config.WEBAPP_GH_PAGES_ENABLED = True

    async def _fake_health(u, **kw):
        return True, "HTTP 200"

    monkeypatch.setattr(_tun, "verify_https_health", _fake_health)
    upd2 = SimpleNamespace(
        effective_user=SimpleNamespace(id=ADMIN_UID, username="boss", full_name="Admin Boss"),
        effective_message=SimpleNamespace(text="https://trombone-blitz-chain.ngrok-free.dev", reply_text=_reply),
    )
    ctx2 = SimpleNamespace(user_data={"awaiting_webapp_url": True}, bot=SimpleNamespace())
    handled2 = asyncio.run(inputs._admin_text(upd2, ctx2, ADMIN_UID, "https://trombone-blitz-chain.ngrok-free.dev"))
    assert handled2 is True
    assert db.get_setting("webapp_gh_pages_enabled", "1") == "0"
    assert config.WEBAPP_GH_PAGES_ENABLED is False
    # Direct Ngrok URL in the reply button (NOT forexyemeni.github.io)
    last_kb = replies[-1][1]["reply_markup"]
    assert last_kb.inline_keyboard[0][0].web_app.url == "https://trombone-blitz-chain.ngrok-free.dev/admin"

