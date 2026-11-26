"""v8.3.0 — Regression suite for the Ultra-Luxury Categorized Global Markets Hub.

Verifies:
1. All 101 real global instruments across 6 asset classes (Metals, Forex, Crypto,
   Stocks, Indices, Commodities) are seeded with real provider mappings and specs.
2. All newly added real instruments start CLOSED by default (enabled=0) and
   activate immediately in 1 tap from the Admin account.
3. Admin Markets Hub (`a:markets`), category views (`a:mktcat:<cat>:<page>`),
   1-tap toggles (`a:market_toggle:<SYM>:<cat>_<page>`), and bulk controls
   (`a:mktbulk:<cat>:on|off`) work cleanly and stay well within Telegram limits.
4. User Markets Hub (`u:markets`, `u:mktcat:<cat>:<page>`), Categorized Analytics
   (`u:analytics`, `u:anlcat:<cat>:<page>`), and Categorized Signal Now
   (`signowcat:`) render luxury category buttons cleanly without overflow.
"""
import asyncio
import types
from types import SimpleNamespace

from fysb import config, db, instruments
from fysb.engines import market_data as md
from fysb.handlers import admin_trading, dispatcher, signal_now, user_pages
from fysb.services import subscriptions as subs
from fysb.services import users as users_service


def _run(coro):
    return asyncio.run(coro)


def _fresh_db(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "DB_FILE", str(tmp_path / "v830.db"))
    monkeypatch.setattr(config, "ADMIN_ID", 999999)
    db.init_db()


def _mk_q(uid: int, data: str = ""):
    captured = {"text": "", "markup": None, "answers": []}

    class _Msg:
        chat_id = uid
        message_id = 1

        async def edit_text(self, text, parse_mode=None, reply_markup=None, **kw):
            captured["text"] = text
            captured["markup"] = reply_markup

        async def reply_text(self, text, parse_mode=None, reply_markup=None, **kw):
            captured["text"] = text
            captured["markup"] = reply_markup

    class _Q:
        def __init__(self):
            self.data = data
            self.from_user = SimpleNamespace(id=uid, username=f"u{uid}", first_name="Tester", full_name="Tester")
            self.message = _Msg()

        async def answer(self, text="", show_alert=False):
            captured["answers"].append((text, show_alert))

        async def edit_message_text(self, text, parse_mode=None, reply_markup=None, **kw):
            captured["text"] = text
            captured["markup"] = reply_markup

    class _Bot:
        async def send_message(self, chat_id, text, parse_mode=None, reply_markup=None, **kw):
            captured["text"] = text
            captured["markup"] = reply_markup

    ctx = types.SimpleNamespace(bot=_Bot(), user_data={}, bot_data={})
    return _Q(), ctx, captured


def _all_buttons(markup) -> list[tuple[str, str]]:
    if not markup:
        return []
    out = []
    for row in markup.inline_keyboard:
        for b in row:
            out.append((b.text, b.callback_data or ""))
    return out


def test_global_catalog_101_real_instruments_all_mapped_and_new_closed_by_default(tmp_path, monkeypatch):
    _fresh_db(tmp_path, monkeypatch)
    with db.conn() as c:
        rows = [dict(r) for r in c.execute(
            "SELECT symbol, title, category, enabled, verified FROM market_catalog").fetchall()]

    assert len(rows) >= 100
    by_sym = {r["symbol"]: r for r in rows}

    # Every single seeded instrument must have a real live provider class and spec
    for r in rows:
        sym = r["symbol"]
        assert md._class_of(sym) is not None, f"{sym} has no live provider mapping!"
        sp = instruments.spec(sym)
        assert sp.pip_size > 0
        assert r["verified"] == 1

    # All 6 categories populated
    cats = {r["category"] for r in rows}
    assert {"metal", "forex", "crypto", "stock", "index", "commodity"}.issubset(cats)

    # All newly added real instruments MUST start closed by default (enabled=0)
    for sym in (
        "AAPL", "TSLA", "NVDA", "MSFT", "AMZN", "META", "GOOGL", "JPM", "BABA",
        "GBPNZD", "EURNZD", "AUDCHF", "USDMXN", "USDTRY", "USDSGD",
        "BNBUSD", "TONUSD", "AVAXUSD", "LINKUSD", "PEPEUSD",
        "USOIL", "UKOIL", "NGAS", "WHEAT", "CORN",
        "HK50", "JPN225", "DXY", "XPDUSD", "XCUUSD",
    ):
        assert sym in by_sym, f"Missing expected real instrument: {sym}"
        assert by_sym[sym]["enabled"] == 0, f"{sym} must be CLOSED by default (enabled=0)!"


def test_admin_luxury_markets_hub_categories_and_one_tap_activation(tmp_path, monkeypatch):
    _fresh_db(tmp_path, monkeypatch)
    admin_uid = config.ADMIN_ID

    # 1. Admin opens Markets Hub -> 6 luxury category buttons, compact message
    q, ctx, cap = _mk_q(admin_uid, "a:markets")
    _run(admin_trading.admin_markets(q, admin_uid, ctx, ctx.bot))
    assert len(cap["text"]) < 3800
    btns = _all_buttons(cap["markup"])
    assert len(btns) <= 30
    cbs = {cb for _, cb in btns}
    for cat in ("metal", "forex", "crypto", "stock", "index", "commodity"):
        assert f"a:mktcat:{cat}:1" in cbs
    assert "a:mktcat:enabled:1" in cbs
    # Arabic labels present
    labels_joined = " ".join(lbl for lbl, _ in btns)
    for ar in ("المعادن", "الفوركس", "العملات الرقمية", "الأسهم"):
        assert ar in labels_joined

    # 2. Admin opens Stocks category -> AAPL starts CLOSED (🔒)
    q2, ctx2, cap2 = _mk_q(admin_uid, "a:mktcat:stock:1")
    _run(admin_trading.admin_markets_category(q2, admin_uid, ctx2, ctx2.bot, "stock", 1))
    assert "الأسهم" in cap2["text"]
    stock_btns = dict((cb, lbl) for lbl, cb in _all_buttons(cap2["markup"]))
    assert "a:market_toggle:AAPL:stock_1" in stock_btns
    assert "🔒" in stock_btns["a:market_toggle:AAPL:stock_1"]
    assert "a:mktcat:stock:2" in stock_btns  # 22 stocks -> paginated cleanly

    # 3. Admin taps AAPL once -> IMMEDIATELY activated (enabled=1) and stays on stock page 1!
    q3, ctx3, cap3 = _mk_q(admin_uid, "a:market_toggle:AAPL:stock_1")
    _run(admin_trading.admin_market_toggle(q3, admin_uid, ctx3, ctx3.bot, "AAPL", "stock_1"))
    with db.conn() as c:
        row = c.execute("SELECT enabled, verified FROM market_catalog WHERE symbol='AAPL'").fetchone()
    assert row["enabled"] == 1 and row["verified"] == 1
    assert "تم تفعيل AAPL" in cap3["text"]
    stock_btns3 = dict((cb, lbl) for lbl, cb in _all_buttons(cap3["markup"]))
    assert "🟢" in stock_btns3["a:market_toggle:AAPL:stock_1"]

    # 4. Admin taps AAPL again -> IMMEDIATELY closed (enabled=0)
    q4, ctx4, cap4 = _mk_q(admin_uid, "a:market_toggle:AAPL:stock_1")
    _run(admin_trading.admin_market_toggle(q4, admin_uid, ctx4, ctx4.bot, "AAPL", "stock_1"))
    with db.conn() as c:
        row = c.execute("SELECT enabled FROM market_catalog WHERE symbol='AAPL'").fetchone()
    assert row["enabled"] == 0

    # 5. Bulk enable & bulk disable Commodities
    q5, ctx5, cap5 = _mk_q(admin_uid, "a:mktbulk:commodity:on")
    _run(admin_trading.admin_markets_bulk(q5, admin_uid, ctx5, ctx5.bot, "commodity", "on"))
    with db.conn() as c:
        en_cnt = c.execute("SELECT COUNT(*) FROM market_catalog WHERE category='commodity' AND enabled=1").fetchone()[0]
    assert en_cnt == 5

    q6, ctx6, cap6 = _mk_q(admin_uid, "a:mktbulk:commodity:off")
    _run(admin_trading.admin_markets_bulk(q6, admin_uid, ctx6, ctx6.bot, "commodity", "off"))
    with db.conn() as c:
        en_cnt = c.execute("SELECT COUNT(*) FROM market_catalog WHERE category='commodity' AND enabled=1").fetchone()[0]
    assert en_cnt == 0


def test_user_luxury_markets_and_analytics_categories_stay_compact(tmp_path, monkeypatch):
    _fresh_db(tmp_path, monkeypatch)
    uid = 424242
    users_service.ensure_user(SimpleNamespace(id=uid, username="trader42", full_name="Trader 42", first_name="Trader"))
    subs.grant_subscription(uid, "VIP")

    # Admin activates TSLA & USOIL so user can see Stocks & Commodities live too
    with db.tx() as c:
        c.execute("UPDATE market_catalog SET enabled=1, verified=1 WHERE symbol IN ('TSLA','USOIL')")
        c.execute("UPDATE plans SET markets='ALL' WHERE code='VIP'")

    u = users_service.get_user(uid)

    # 1. User Markets overview (`u:markets`) shows all 6 category buttons + quick toggles
    q, ctx, cap = _mk_q(uid, "u:markets")
    _run(user_pages.user_page(q, "markets", uid, ctx, preloaded_u=u))
    assert len(cap["text"]) < 3800
    btns = _all_buttons(cap["markup"])
    assert len(btns) <= 40
    cbs = {cb for _, cb in btns}
    for cat in ("metal", "forex", "crypto", "stock", "index", "commodity"):
        assert f"u:mktcat:{cat}:1" in cbs
    assert "u:togmarket:XAUUSD" in cbs

    # 2. User opens Stocks category (`u:mktcat:stock:1`) -> sees Admin-activated TSLA!
    q2, ctx2, cap2 = _mk_q(uid, "u:mktcat:stock:1")
    _run(user_pages.user_page(q2, "mktcat:stock:1", uid, ctx2, preloaded_u=u))
    assert "الأسهم" in cap2["text"]
    assert "TSLA" in cap2["text"]
    cbs2 = {cb for _, cb in _all_buttons(cap2["markup"])}
    assert "u:togmarket:TSLA:stock_1" in cbs2

    # 3. User Analytics overview (`u:analytics`) -> compact luxury 6-category hub + quick launch
    q3, ctx3, cap3 = _mk_q(uid, "u:analytics")
    _run(user_pages.user_page(q3, "analytics", uid, ctx3, preloaded_u=u))
    assert len(cap3["text"]) < 3800
    btns3 = _all_buttons(cap3["markup"])
    assert len(btns3) <= 30
    cbs3 = {cb for _, cb in btns3}
    assert "u:anlsym:XAUUSD" in cbs3
    for cat in ("metal", "forex", "crypto", "stock", "index", "commodity"):
        assert f"u:anlcat:{cat}:1" in cbs3

    # 4. User Analytics Stocks drill-down (`u:anlcat:stock:1`) -> paginated under limits!
    q4, ctx4, cap4 = _mk_q(uid, "u:anlcat:stock:1")
    _run(user_pages.user_page(q4, "anlcat:stock:1", uid, ctx4, preloaded_u=u))
    assert len(cap4["text"]) < 3800
    cbs4 = {cb for _, cb in _all_buttons(cap4["markup"])}
    assert "u:anlsym:AAPL" in cbs4
    assert "u:anlcat:stock:2" in cbs4


def test_signal_now_categorizes_when_all_101_markets_enabled(tmp_path, monkeypatch):
    _fresh_db(tmp_path, monkeypatch)
    admin_uid = config.ADMIN_ID
    with db.tx() as c:
        c.execute("UPDATE market_catalog SET enabled=1, verified=1")

    q, ctx, cap = _mk_q(admin_uid, "a:signow")
    _run(signal_now.open_flow(q, admin_uid, ctx, ctx.bot, is_admin=True))
    _run(signal_now.pick_style(q, admin_uid, ctx, ctx.bot, "INTRADAY"))

    # Even with ALL 101 markets enabled, keyboard stays super clean via category buttons!
    btns = _all_buttons(cap["markup"])
    assert len(btns) <= 25
    cbs = {cb for _, cb in btns}
    assert "a:signowsym:XAUUSD" in cbs
    for cat in ("metal", "forex", "crypto", "stock", "index", "commodity"):
        assert f"a:signowcat:{cat}:1" in cbs

    # Drill into Forex -> paginated cleanly
    _run(signal_now.pick_category(q, admin_uid, ctx, ctx.bot, "forex", 1))
    btns_fx = _all_buttons(cap["markup"])
    assert len(btns_fx) <= 25
    cbs_fx = {cb for _, cb in btns_fx}
    assert "a:signowcat:forex:2" in cbs_fx
