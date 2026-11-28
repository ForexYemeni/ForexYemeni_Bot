"""v8.14.20 — «الفخامة الملكية الكاملة للتقرير الموحد» tests — the owner's decree:

① EVERY market/strategy/timeframe is truly complete: pickers merge the FULL
   bot catalogs with every value that actually traded — zero LIMIT, zero
   hidden entries, real per-value counts (period-bounded).
② The FOURTH dimension: the honest period bounds the counter, the report,
   the trades detail and the broadcast; the period is NAMED on the report.
③ بث لي + بث للقنوات: a dedicated send-to-owner button delivers photo + full
   text; send failures are SHOWN with their real reason (never swallowed);
   every channel broadcast leaves an automatic owner's copy.
④ The REAL market picture: the executive PNG of the exact slice & period —
   real aggregates only, the 50-pip decree included, honest when empty.
"""
from __future__ import annotations

import asyncio
import sys
from datetime import datetime, timedelta, timezone

import pytest

sys.path.insert(0, ".")

import tests.helpers  # noqa: F401
from tests.helpers import MockBot, MockContext, MockQuery, page_for, page_markup_for
from fysb import config, db
from fysb.engines.strategies import REGISTRY
from fysb.handlers import admin_reports_ui, dispatcher
from fysb.services import digest, reports

ADMIN = 9001

_SEQ = iter(range(1, 900))


@pytest.fixture(autouse=True)
def _clean():
    db.init_db()
    with db.conn() as c:
        c.execute("DELETE FROM signals")
        c.execute("DELETE FROM broadcast_channels")
    yield
    with db.conn() as c:
        c.execute("DELETE FROM signals")
        c.execute("DELETE FROM broadcast_channels")


def _iso(age_days: float = 0.0) -> str:
    return (datetime.now(timezone.utc) - timedelta(days=age_days)).isoformat()


def _ins(sym, strat, status, pips, tf="M15", age_days=0.0, touched50=0, score=85):
    closed_at = "" if status in ("ACTIVE", "RUNNING", "PENDING") else _iso(age_days)
    with db.conn() as c:
        c.execute("INSERT INTO signals(symbol,timeframe,status,direction,entry,stop,tp1,tp2,tp3,"
                  "rr,score,strategy,pips_result,profit_50_hit,closed_at,created_at)"
                  " VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                  (sym, tf, status, "BUY", 3500.0, 3450.0, 3540.0, 3580.0, 3620.0,
                   2.0, score, strat, pips, touched50, closed_at, _iso(age_days)))


def _fire(data: str, ctx: MockContext | None = None):
    ctx = ctx or MockContext()
    q = MockQuery(data, ADMIN)
    q.from_user = type("U", (), {"id": ADMIN})()
    upd = type("Update", (), {})()
    upd.callback_query = q
    asyncio.run(dispatcher.buttons(upd, ctx))
    return q, ctx


def _labels(markup) -> list[str]:
    return [b.text for row in markup.inline_keyboard for b in row]


# ---------------------------------------------------------------------------
# ① the COMPLETE universe — no LIMIT, catalog ∪ live signals
# ---------------------------------------------------------------------------
def _walk_picker(kind: str, ctx: MockContext) -> list[str]:
    """Collect every value button across ALL picker pages (the complete universe)."""
    labels: list[str] = []
    page = 0
    while page < 12:  # safety bound — the universe is finite
        q, ctx = _fire(f"a:repf:pg:{kind}:{page}", ctx)
        markup = page_markup_for(q, ctx)
        if markup is None:
            break
        page_labels = _labels(markup)
        labels.extend(page_labels)
        if not any("التالي" in lbl for lbl in page_labels):
            break
        page += 1
    return labels


def test_market_picker_is_the_complete_universe_over_eighteen_values():
    # 25 distinct symbols actually traded — the old LIMIT 18 would hide 7 of them
    for i in range(25):
        _ins(f"SYM{i:02d}", "TREND", "TP1_HIT", 40.0, age_days=float(i) / 24.0)
    q, ctx = _fire("a:repf:m")
    labels = _walk_picker("m", ctx)
    for i in range(25):
        assert any(f"SYM{i:02d}" in lbl for lbl in labels), f"SYM{i:02d} is HIDDEN — the LIMIT disease"
    # catalog entries that never traded still appear (the bot's full universe)
    assert any("XAUUSD · 0" in lbl for lbl in labels)


def test_strategy_picker_lists_every_registry_strategy_even_with_zero_signals():
    q, ctx = _fire("a:repf:s")
    labels = _walk_picker("s", ctx)
    for code in REGISTRY:  # every strategy the bot owns — even with zero signals
        assert any(code in lbl for lbl in labels), f"{code} missing from the COMPLETE list"


def test_picker_counts_are_real_and_period_bounded():
    _ins("XAUUSD", "TREND", "TP1_HIT", 40.0, age_days=0.0)
    _ins("XAUUSD", "TREND", "TP1_HIT", 40.0, age_days=0.5)
    _ins("XAUUSD", "TREND", "TP1_HIT", 40.0, age_days=1.0)
    _ins("XAUUSD", "TREND", "SL_HIT", -50.0, age_days=10.0)
    ctx = MockContext()
    ctx.user_data["rep_filter"] = {"market": None, "strategy": None, "timeframe": None, "days": 7}
    labels = _walk_picker("m", ctx)
    assert any("XAUUSD · 3" in lbl for lbl in labels)  # only the last-7-days trades


def test_picker_pagination_carries_every_value_across_pages():
    for i in range(45):
        _ins(f"SYM{i:02d}", "TREND", "TP1_HIT", 40.0)
    q, ctx = _fire("a:repf:m")
    page0 = _labels(page_markup_for(q, ctx))
    assert any("التالي" in lbl for lbl in page0) and len(page0) < 60
    labels = _walk_picker("m", ctx)
    for i in range(45):
        assert any(f"SYM{i:02d}" in lbl for lbl in labels)  # beyond the old LIMIT — real, paginated
    q_last, _ = _fire("a:repf:pg:m:1", ctx)
    assert any("السابق" in lbl for lbl in _labels(page_markup_for(q_last, ctx)))


# ---------------------------------------------------------------------------
# ② the FOURTH dimension — the honest period
# ---------------------------------------------------------------------------
def test_period_picker_flow_sets_and_resets_days():
    q, ctx = _fire("a:repf:d")
    assert "اختر الفترة الزمنية" in page_for(q, ctx)
    labels = _labels(page_markup_for(q, ctx))
    for want in ("24 ساعة", "7 يوم", "365 يوم", "كل التاريخ"):
        assert any(want in lbl for lbl in labels)
    q, ctx = _fire("a:repf:d:30", ctx)
    assert "الفترة الزمنية: <b>آخر 30 يوماً</b>" in page_for(q, ctx)
    q, ctx = _fire("a:repf:d:-", ctx)
    assert "الفترة الزمنية: <b>كل التاريخ</b>" in page_for(q, ctx)


def test_matching_counter_respects_the_period():
    _ins("XAUUSD", "TREND", "TP1_HIT", 40.0, age_days=10.0)
    ctx = MockContext()
    ctx.user_data["rep_filter"] = {"market": None, "strategy": None, "timeframe": None, "days": 30}
    q, _ = _fire("a:repfilter", ctx)
    assert "الإشارات المطابقة الآن: <b>1</b>" in page_for(q, ctx)
    ctx.user_data["rep_filter"] = {"market": None, "strategy": None, "timeframe": None, "days": 7}
    q, _ = _fire("a:repfilter", ctx)
    assert "الإشارات المطابقة الآن: <b>0</b>" in page_for(q, ctx)


def test_period_bounds_the_filtered_report_and_is_named_on_it():
    _ins("XAUUSD", "TREND", "TP1_HIT", 40.0, age_days=2.0)
    _ins("XAUUSD", "TREND", "TP1_HIT", 30.0, age_days=3.0)
    _ins("XAUUSD", "TREND", "TP1_HIT", 60.0, age_days=40.0)
    txt7 = reports.filtered_signals_report(None, None, None, 7)
    assert "الفترة: <b>آخر 7 أيام</b>" in txt7 and "رابحة <b>2</b>" in txt7
    txt_full = reports.filtered_signals_report(None, None, None, None)
    assert "الفترة: <b>كل التاريخ</b>" in txt_full and "رابحة <b>3</b>" in txt_full


def test_trades_detail_respects_the_period():
    _ins("XAUUSD", "TREND", "TP1_HIT", 40.0, age_days=2.0)
    _ins("XAUUSD", "TREND", "TP1_HIT", 60.0, age_days=40.0)
    det7 = reports.filtered_trades_detail(None, None, None, days=7)
    assert "<b>1</b> مطابقة" in det7 and det7.count("#") == 1  # exactly 1 row
    det_full = reports.filtered_trades_detail(None, None, None, days=None)
    assert "<b>2</b> مطابقة" in det_full and det_full.count("#") == 2


def test_broadcast_text_carries_the_period_label():
    _ins("XAUUSD", "TREND", "TP1_HIT", 40.0, age_days=2.0)
    ctx = MockContext()
    ctx.user_data["rep_filter"] = {"market": None, "strategy": None, "timeframe": None, "days": 7}
    _fire("a:repf:run", ctx)
    _fire("a:repfb:preview", ctx)
    assert "الفترة: <b>آخر 7 أيام</b>" in ctx.user_data["repf_broadcast_text"]


def test_digest_engine_serves_the_slice_and_full_history():
    _ins("XAUUSD", "TREND", "TP1_HIT", 40.0, age_days=2.0)
    _ins("BTCUSD", "TREND", "SL_HIT", -500.0, age_days=3.0)
    _ins("XAUUSD", "TREND", "SL_HIT", -50.0, age_days=40.0, touched50=1)  # rescued, old
    d = digest.build_digest_data(days=None, market="XAUUSD")
    assert d["days"] == 0 and "كل التاريخ" in d["period_ar"] and d["wins"] == 2 and d["losses"] == 0
    d_all = digest.build_digest_data(days=None)
    assert d_all["wins"] == 2 and d_all["losses"] == 1  # decree applied across the slice too


# ---------------------------------------------------------------------------
# ③ بث لي — instant owner delivery, failures SHOWN, owner copy always
# ---------------------------------------------------------------------------
def test_send_me_button_delivers_photo_and_full_text_to_the_owner():
    _ins("XAUUSD", "TREND", "TP1_HIT", 40.0, age_days=2.0)
    bot = MockBot()
    ctx = MockContext(bot=bot)
    _fire("a:repf:run", ctx)
    q, _ = _fire("a:repfme", ctx)
    page = page_for(q, ctx)
    assert "وصلك التقرير كاملاً في شاتك" in page
    owner_msgs = [m for m in bot.sent if m["chat_id"] == ADMIN]
    assert owner_msgs and "التقرير التفاعلي الصادق" in owner_msgs[-1]["text"]
    assert bot.photos and bot.photos[-1]["chat_id"] == ADMIN  # the real market picture rides too


def test_send_me_failure_shows_the_real_reason_never_swallowed():
    _ins("XAUUSD", "TREND", "TP1_HIT", 40.0, age_days=2.0)
    bot = MockBot()
    bot.fail_for = {ADMIN}
    ctx = MockContext(bot=bot)
    _fire("a:repf:run", ctx)
    q, _ = _fire("a:repfme", ctx)
    page = page_for(q, ctx)
    assert "السبب الحقيقي" in page and "blocked by user" in page
    assert "وصلك التقرير كاملاً" not in page


def test_broadcast_preview_reports_owner_delivery_failure_honestly():
    _ins("XAUUSD", "TREND", "TP1_HIT", 40.0, age_days=2.0)
    bot = MockBot()
    bot.fail_for = {ADMIN}
    ctx = MockContext(bot=bot)
    _fire("a:repf:run", ctx)
    q, _ = _fire("a:repfb:preview", ctx)
    page = page_for(q, ctx)
    assert "معاينة البث الملكية" in page and "ستُبث للقنوات حرفياً" in page
    assert "تعذر تسليم المعاينة" in page and "blocked by user" in page
    assert ctx.user_data.get("repf_broadcast_text")  # staged state survives


def test_confirm_broadcast_delivers_channels_and_the_owner_copy():
    _ins("XAUUSD", "TREND", "TP1_HIT", 40.0, age_days=2.0)
    bot = MockBot()
    ctx = MockContext(bot=bot)
    with db.conn() as c:
        now = _iso(0.0)
        c.execute("INSERT INTO broadcast_channels(chat_id,enabled,title,created_at,updated_at)"
                  " VALUES(?,?,?,?,?)", ("-1001234", 1, "قناة الاختبار", now, now))
    _fire("a:repf:run", ctx)
    _fire("a:repfb:preview", ctx)
    q, _ = _fire("a:repfb:confirm", ctx)
    page = page_for(q, ctx)
    assert "تم بث التقرير الصادق للقنوات" in page
    assert bot.photos and bot.photos[-1]["chat_id"] == ADMIN  # the owner's copy
    owner_msgs = [m for m in bot.sent if m["chat_id"] == ADMIN]
    assert any("نسخة الملك" in m["text"] for m in owner_msgs)
    ch_msgs = [m for m in bot.sent if m["chat_id"] == -1001234]
    assert ch_msgs and "التقرير التفاعلي الصادق" in ch_msgs[-1]["text"]
    assert ctx.user_data.get("repf_broadcast_text") is None  # state consumed


def test_confirm_without_enabled_channels_is_honest_and_keeps_state():
    _ins("XAUUSD", "TREND", "TP1_HIT", 40.0, age_days=2.0)
    bot = MockBot()
    ctx = MockContext(bot=bot)
    ctx.user_data["repf_broadcast_text"] = "📊 تقرير جاهز"
    q, _ = _fire("a:repfb:confirm", ctx)
    page = page_for(q, ctx)
    assert "لا توجد قنوات مفعّلة للبث" in page and "بث لي التقرير" in page
    assert ctx.user_data.get("repf_broadcast_text") == "📊 تقرير جاهز"  # state preserved
    assert bot.sent == []  # nothing was sent anywhere


# ---------------------------------------------------------------------------
# ④ the REAL market picture — the executive PNG of the exact slice & period
# ---------------------------------------------------------------------------
def test_real_market_picture_png_of_the_slice_with_the_decree():
    _ins("XAUUSD", "TREND", "TP1_HIT", 40.0, age_days=2.0)
    _ins("XAUUSD", "TREND", "SL_HIT", -50.0, age_days=3.0, touched50=1)  # rescued
    _ins("BTCUSD", "TREND", "SL_HIT", -500.0, age_days=3.0)              # honest loss
    bot = MockBot()
    ctx = MockContext(bot=bot)
    ctx.user_data["rep_filter"] = {"market": None, "strategy": None, "timeframe": None, "days": 7}
    _fire("a:repf:run", ctx)
    q, _ = _fire("a:repfp", ctx)
    page = page_for(q, ctx)
    assert "وصلك السوق الحقيقي" in page
    assert bot.photos and bot.photos[-1]["chat_id"] == ADMIN
    png, d = admin_reports_ui._build_slice_png({"market": None, "strategy": None,
                                                "timeframe": None, "days": 7})
    assert png and png[:8] == b"\x89PNG\r\n\x1a\n"
    assert d["wins"] == 2 and d["losses"] == 1  # the decree is IN the picture's data


def test_png_of_an_empty_slice_is_honest_no_fabricated_image():
    bot = MockBot()
    ctx = MockContext(bot=bot)
    _fire("a:repf:run", ctx)
    q, _ = _fire("a:repfp", ctx)
    page = page_for(q, ctx)
    assert "لا توجد بيانات حقيقية" in page
    assert bot.photos == []  # no fake picture of nothing


def test_new_actions_route_through_the_real_dispatcher():
    _ins("XAUUSD", "TREND", "TP1_HIT", 40.0, age_days=2.0)
    bot = MockBot()
    ctx = MockContext(bot=bot)
    for data in ("a:repfilter", "a:repf:d", "a:repf:d:14", "a:repf:run",
                 "a:repfp", "a:repfme", "a:repfd"):
        q, ctx = _fire(data, ctx)
        assert page_for(q, ctx), f"route {data} rendered nothing"
    # the period set through the dispatcher is honored by the report engine
    assert ctx.user_data["rep_filter"]["days"] == 14
    owner_msgs = [m for m in bot.sent if m["chat_id"] == ADMIN]
    assert owner_msgs  # repfme really delivered
