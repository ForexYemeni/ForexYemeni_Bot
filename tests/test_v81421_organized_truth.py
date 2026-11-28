"""v8.14.21 — «التقرير المنظّم الصادق» tests — the owner's decree:

① An empty filtered slice is NEVER a dead end: the report shows WHY it is
   empty with REAL numbers (period total, all-history total, the active
   markets & strategies inside the period) + a one-tap honest escape
   («🎲 كل ما لديك في الفترة») that clears the slice, keeps the period,
   and shows everything the period actually holds.
② The market picker opens ORGANIZED: real catalog categories (المعادن ·
   الفوركس · العملات الرقمية · المؤشرات · الأسهم · السلع) with real market
   and signal counts; inside a category only its symbols appear — active
   first, counts on every button; the full alphabetical view remains one
   tap away; live-only symbols are honestly bucketed.
"""
from __future__ import annotations

import asyncio
import sys
from datetime import datetime, timedelta, timezone

import pytest

sys.path.insert(0, ".")

import tests.helpers  # noqa: F401
from tests.helpers import MockBot, MockContext, MockQuery, page_for, page_markup_for
from fysb import db
from fysb.handlers import admin_reports_ui, dispatcher
from fysb.services import reports

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


def _ins(sym, strat, status, pips, tf="M15", age_days=0.0):
    closed_at = "" if status in ("ACTIVE", "RUNNING", "PENDING") else _iso(age_days)
    with db.conn() as c:
        c.execute("INSERT INTO signals(symbol,timeframe,status,direction,entry,stop,tp1,tp2,tp3,"
                  "rr,score,strategy,pips_result,profit_50_hit,closed_at,created_at)"
                  " VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                  (sym, tf, status, "BUY", 3500.0, 3450.0, 3540.0, 3580.0, 3620.0,
                   2.0, 85, strat, pips, 0, closed_at, _iso(age_days)))


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


def _period_ctx(days: int | None) -> MockContext:
    ctx = MockContext()
    ctx.user_data["rep_filter"] = {"market": None, "strategy": None, "timeframe": None, "days": days}
    return ctx


# ---------------------------------------------------------------------------
# ① the honest empty state + the one-tap escape
# ---------------------------------------------------------------------------
def test_empty_slice_shows_real_period_and_history_totals():
    _ins("XAUUSD", "TREND", "TP1_HIT", 40.0, age_days=2.0)
    txt = reports.filtered_signals_report("ETHUSD", "SMC", "M15", 30)
    assert "لا إشارات في هذا المقطع المحدد" in txt
    assert "داخل الفترة المختارة" in txt and "<b>1</b> إشارة" in txt
    assert "إجمالي سجل البوت" in txt and "<b>1</b> إشارة" in txt
    assert "XAUUSD</b> (1)" in txt and "TREND</b> (1)" in txt  # what you DO have


def test_empty_everything_is_still_honest_zero_not_fabricated():
    txt = reports.filtered_signals_report("ETHUSD", "SMC", "M15", 30)
    assert "لا إشارات في هذا المقطع المحدد" in txt
    assert "<b>0</b> إشارة" in txt and "لا شيء بعد" in txt


def test_escape_button_clears_slice_keeps_period_and_shows_everything():
    _ins("XAUUSD", "TREND", "TP1_HIT", 40.0, age_days=2.0)
    _ins("BTCUSD", "ICT", "SL_HIT", -500.0, age_days=3.0)
    ctx = _period_ctx(30)
    ctx.user_data["rep_filter"].update({"market": "ETHUSD", "strategy": "SMC", "timeframe": "M15"})
    _fire("a:repf:run", ctx)
    q, ctx = _fire("a:repf:escape", ctx)
    page = page_for(q, ctx)
    assert "السوق: <b>الكل</b>" in page and "الفترة: <b>آخر 30 يوماً</b>" in page
    assert "رابحة" in page  # the period's REAL content, all of it


def test_escape_button_is_on_the_report_page():
    _ins("XAUUSD", "TREND", "TP1_HIT", 40.0, age_days=2.0)
    q, _ = _fire("a:repf:run", _period_ctx(7))
    labels = _labels(page_markup_for(q, q))
    assert any("كل ما لديك في الفترة" in lbl for lbl in labels)


def test_trades_detail_empty_shows_the_period_truth():
    _ins("XAUUSD", "TREND", "TP1_HIT", 40.0, age_days=2.0)
    det = reports.filtered_trades_detail("ETHUSD", None, None, days=30)
    assert "لا صفقات مغلقة ضمن هذه التصفية" in det
    assert "<b>1</b> إشارة" in det and "كل ما لديك في الفترة" in det


def test_empty_via_real_dispatcher_shows_diagnostics_and_escape():
    ctx = _period_ctx(30)
    ctx.user_data["rep_filter"].update({"market": "BTCUSD", "strategy": "SMC", "timeframe": "M15"})
    q, _ = _fire("a:repf:run", ctx)
    page = page_for(q, ctx)
    assert "لا إشارات في هذا المقطع المحدد" in page
    assert "داخل الفترة المختارة" in page
    labels = _labels(page_markup_for(q, ctx))
    assert any("كل ما لديك في الفترة" in lbl for lbl in labels)


# ---------------------------------------------------------------------------
# ② the ORGANIZED market picker — real categories, honest counts
# ---------------------------------------------------------------------------
def test_market_hub_groups_by_real_catalog_categories():
    q, _ = _fire("a:repf:m")
    page = page_for(q, q)
    assert "اختر السوق — مصنّفاً ومرتباً" in page
    labels = _labels(page_markup_for(q, q))
    joined = " | ".join(labels)
    for ar in ("المعادن", "الفوركس", "العملات الرقمية", "المؤشرات", "الأسهم", "السلع"):
        assert ar in joined, f"{ar} category missing from the hub"
    assert any("كل الأسواق أبجدياً" in lbl for lbl in labels)
    assert any(lbl.startswith("🌐") for lbl in labels)


def test_market_hub_counts_are_real_and_period_bounded():
    _ins("XAUUSD", "TREND", "TP1_HIT", 40.0, age_days=1.0)
    _ins("XAUUSD", "TREND", "TP1_HIT", 40.0, age_days=40.0)  # outside the period
    q, _ = _fire("a:repf:m", _period_ctx(30))
    labels = _labels(page_markup_for(q, q))
    metal = next(lbl for lbl in labels if "المعادن" in lbl)
    assert "· 1 إشارة" in metal  # only the last-30-days signal counts


def test_category_page_lists_only_its_symbols_active_first():
    _ins("EURUSD", "TREND", "TP1_HIT", 40.0)
    _ins("EURUSD", "TREND", "TP1_HIT", 40.0)
    q, ctx = _fire("a:repf:cat:m:forex:0")
    page = page_for(q, ctx)
    assert "الفوركس" in page
    labels = _labels(page_markup_for(q, ctx))
    assert any("EURUSD · 2" in lbl for lbl in labels)          # real count on the button
    assert not any(lbl.startswith("XAUUSD ·") for lbl in labels)  # gold is NOT in forex
    assert any("فئات الأسواق" in lbl for lbl in labels)         # the hub is one tap back


def test_live_only_symbols_get_their_own_honest_bucket():
    _ins("WEIRDPAIR", "TREND", "TP1_HIT", 40.0)  # traded but not in the catalog
    q, _ = _fire("a:repf:m")
    labels = _labels(page_markup_for(q, q))
    assert any("من السجل الحي فقط · 1" in lbl for lbl in labels)
    q2, _ = _fire("a:repf:cat:m:__live__:0")
    assert any("WEIRDPAIR · 1" in lbl for lbl in _labels(page_markup_for(q2, q2)))


def test_strategy_picker_leads_with_the_active_strategies():
    _ins("XAUUSD", "ICT", "TP1_HIT", 40.0)
    _ins("XAUUSD", "ICT", "TP1_HIT", 40.0)
    _ins("XAUUSD", "SMC", "TP1_HIT", 40.0)
    q, _ = _fire("a:repf:s")
    labels = [lbl for lbl in _labels(page_markup_for(q, q)) if " · " in lbl]
    codes = [lbl.split(" · ")[0] for lbl in labels]
    assert codes.index("ICT") < codes.index("SMC") < codes.index("TREND")  # active-first


def test_full_routes_still_render_through_the_dispatcher():
    ctx = _period_ctx(90)
    for data in ("a:repfilter", "a:repf:m", "a:repf:cat:m:metal:0", "a:repf:pg:m:0",
                 "a:repf:s", "a:repf:t", "a:repf:d", "a:repf:run", "a:repfd"):
        q, ctx = _fire(data, ctx)
        assert page_for(q, ctx), f"route {data} rendered nothing"
