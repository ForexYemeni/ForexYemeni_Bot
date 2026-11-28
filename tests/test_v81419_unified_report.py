"""v8.14.19 — «التقرير الموحد الفاخر + قانون الخمسين نقطة» tests:

① ONE report: «بث تقرير الأداء» + «التقارير الشاملة» are replaced by the
   single interactive comprehensive report (filter → generate → trades detail
   → preview-first broadcast). The old static digest button flow is EXECUTED.
② The 50-pip decree: ANY trade whose documented favorable excursion reached
   the lock threshold (profit_50_hit=1) is NEVER counted as a loss anywhere —
   its real profit IS the threshold pips, 100% — in the filtered report, the
   classic signals report, AND the executive digest data (caption + PNG).
③ Broadcast is preview-first: the deliverable lands on the owner's chat
   alone, then he confirms — nothing leaves before his decision.
"""
from __future__ import annotations

import asyncio
import sys
from pathlib import Path
from unittest.mock import patch

import pytest

sys.path.insert(0, ".")

import tests.helpers  # noqa: F401
from tests.helpers import MockContext, MockQuery, page_for, page_markup_for
from fysb import db, instruments
from fysb.engines import honesty
from fysb.handlers import admin_reports_ui, admin_system, dispatcher
from fysb.services import digest, ledger, reports

ADMIN = 9001

_SEQ = iter(range(1, 900))


@pytest.fixture(autouse=True)
def _clean():
    db.init_db()
    with db.conn() as c:
        c.execute("DELETE FROM signals")
        c.execute("DELETE FROM ui_events")
        c.execute("DELETE FROM settings WHERE key='lock_pips'")
    yield
    with db.conn() as c:
        c.execute("DELETE FROM signals")
        c.execute("DELETE FROM ui_events")
        c.execute("DELETE FROM settings WHERE key='lock_pips'")


def _ins(sym, strat, tf, status, pips, score=85, rr=2.0, closed="", touched50=0):
    with db.conn() as c:
        c.execute("INSERT INTO signals(symbol,timeframe,status,direction,entry,stop,tp1,tp2,tp3,"
                  "rr,score,strategy,pips_result,profit_50_hit,closed_at,created_at)"
                  " VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                  (sym, tf, status, "BUY", 3500.0, 3450.0, 3540.0, 3580.0, 3620.0,
                   rr, score, strat, pips, touched50, closed, f"2026-11-27 09:{next(_SEQ):02d}"))


def _fire(data: str, ctx: MockContext | None = None):
    ctx = ctx or MockContext()
    q = MockQuery(data, ADMIN)
    q.from_user = type("U", (), {"id": ADMIN})()
    upd = type("Update", (), {})()
    upd.callback_query = q
    asyncio.run(dispatcher.buttons(upd, ctx))
    return q, ctx


def _cmd_ctx(bot=None):
    upd = type("Update", (), {})()
    upd.effective_user = type("U", (), {"id": ADMIN})()
    msg = MockMessage = type("M", (), {})()
    upd.effective_message = msg
    upd.effective_chat = type("C", (), {"type": "private"})()
    return upd


# ---------------------------------------------------------------------------
# ② the 50-pip decree — the Truth Ledger
# ---------------------------------------------------------------------------
def test_rescued_stop_is_a_win_at_the_threshold():
    row = {"status": "SL_HIT", "pips_result": -50.0, "profit_50_hit": 1}
    verdict, pips = ledger.closed_verdict(row)
    assert verdict == "win" and pips == honesty.lock_pips() == 50.0


def test_rescued_stop_follows_the_editable_threshold():
    db.set_setting("lock_pips", 70)
    verdict, pips = ledger.closed_verdict({"status": "SL_HIT", "pips_result": -50.0,
                                           "profit_50_hit": 1})
    assert verdict == "win" and pips == 70.0


def test_plain_stop_without_touch_stays_an_honest_loss():
    verdict, pips = ledger.closed_verdict({"status": "SL_HIT", "pips_result": -50.0,
                                           "profit_50_hit": 0})
    assert verdict == "loss" and pips == -50.0


def test_filtered_report_applies_the_decree():
    _ins("XAUUSD", "TREND", "M15", "TP1_HIT", 40.0, closed="2026-11-27 10:00")
    _ins("XAUUSD", "TREND", "M15", "SL_HIT", -50.0, touched50=1)  # rescued
    _ins("XAUUSD", "TREND", "M15", "SL_HIT", -50.0)               # honest loss
    txt = reports.filtered_signals_report("XAUUSD", None, None)
    assert "رابحة <b>2</b> · خاسرة <b>1</b>" in txt
    xau = instruments.usd_move("XAUUSD", 40.0 + 50.0 - 50.0, 0.01)
    assert f"{xau:+.2f}$" in txt
    assert "قانون الخمسين نقطة" in txt and "<b>1</b> صفقة لمست العتبة" in txt


def test_classic_signals_report_applies_the_decree():
    _ins("XAUUSD", "TREND", "M15", "SL_HIT", -50.0, touched50=1)
    txt = reports.signals_report()
    assert "رابحة <b>1</b>" in txt and "خاسرة <b>0</b>" in txt


def test_trades_detail_marks_the_rescue():
    _ins("XAUUSD", "TREND", "M15", "SL_HIT", -50.0, touched50=1)
    txt = reports.filtered_trades_detail("XAUUSD", None, None)
    assert "قانون الخمسين" in txt and "+50.0" in txt
    assert "✅" in txt  # the rescued stop settles as a win


def test_digest_data_applies_the_decree():
    _ins("XAUUSD", "TREND", "M15", "SL_HIT", -50.0, touched50=1, closed="2026-11-27 10:00")
    _ins("BTCUSD", "TREND", "M15", "SL_HIT", -500.0, closed="2026-11-27 11:00")
    d = digest.build_digest_data(days=7)
    # the rescued gold stop = +50 pips win; the honest BTC stop stays a loss
    assert d["wins"] == 1 and d["losses"] == 1
    assert abs(float(d["net_pips"]) - (50.0 - 500.0)) < 0.1
    usd = instruments.usd_move("XAUUSD", 50.0, 0.01) + instruments.usd_move("BTCUSD", -500.0, 0.01)
    assert abs(float(d["net_usd_001"]) - usd) < 0.02


# ---------------------------------------------------------------------------
# ① ONE report — the unified interactive system
# ---------------------------------------------------------------------------
def test_digest_now_opens_the_interactive_report_no_broadcast():
    _ins("XAUUSD", "TREND", "M15", "TP1_HIT", 40.0, closed="2026-11-27 10:00")
    ctx = MockContext()
    q, ctx = _fire("a:repfilter", ctx)
    page = page_for(q, ctx)
    assert "التقرير التفاعلي الشامل" in page
    # no weekly digest publish — the old flow is executed
    src = Path("fysb/handlers/auto_bridge_bot.py").read_text(encoding="utf-8")
    assert 'act == "digest_now"' not in src


def test_finance_hub_has_one_comprehensive_report_button():
    src = Path("fysb/ui/keyboards.py").read_text(encoding="utf-8")
    assert "تقرير الأداء الشامل التفاعلي" in src
    assert 'a:digest_now"' not in src
    assert "بث تقرير الأداء الآن" not in src


def test_reports_hub_is_the_unified_entry():
    ctx = MockContext()
    q, _ = _fire("a:reports", ctx)
    page = page_for(q, ctx)
    assert "مركز التقارير الموحد" in page
    assert "قانون الخمسين نقطة" in page
    markup = page_markup_for(q, ctx)
    labels = [b.text for row in markup.inline_keyboard for b in row]
    assert any("التقرير الشامل التفاعلي" in t for t in labels)


def test_generated_report_page_carries_detail_and_broadcast():
    _ins("XAUUSD", "TREND", "M15", "TP1_HIT", 40.0, closed="2026-11-27 10:00")
    ctx = MockContext()
    _fire("a:repf:m:XAUUSD", ctx)
    q, _ = _fire("a:repf:run", ctx)
    markup = page_markup_for(q, ctx)
    labels = [b.text for row in markup.inline_keyboard for b in row]
    assert any("تفاصيل الصفقات المغلقة" in t for t in labels)
    assert any("بث هذا التقرير للقنوات" in t for t in labels)


def test_trades_detail_route_renders_rows():
    _ins("XAUUSD", "TREND", "M15", "LOCKED_50", 55.0, closed="2026-11-27 10:00")
    ctx = MockContext()
    _fire("a:repf:m:XAUUSD", ctx)
    q, _ = _fire("a:repfd", ctx)
    page = page_for(q, ctx)
    assert "تفاصيل الصفقات المغلقة" in page and "LOCKED_50" in page


# ---------------------------------------------------------------------------
# ③ preview-first broadcast of the EXACT filtered report
# ---------------------------------------------------------------------------
def test_broadcast_preview_lands_on_owner_alone():
    _ins("XAUUSD", "TREND", "M15", "TP1_HIT", 40.0, closed="2026-11-27 10:00")
    ctx = MockContext()
    sent = []
    _fire("a:repf:m:XAUUSD", ctx)
    q, _ = _fire("a:repfb:preview", ctx)
    page = page_for(q, ctx)
    assert "معاينة البث الملكية" in page and "ستُبث للقنوات حرفياً" in page
    assert ctx.user_data.get("repf_broadcast_text")
    assert "التقرير التفاعلي الصادق" in ctx.user_data["repf_broadcast_text"]


def test_broadcast_confirm_sends_only_after_decision():
    _ins("XAUUSD", "TREND", "M15", "TP1_HIT", 40.0, closed="2026-11-27 10:00")
    ctx = MockContext()
    ctx.user_data["repf_broadcast_text"] = "📊 تقرير اختبار البث"
    sent_to = []

    class _Bot:
        async def send_message(self, chat_id, text=None, **kw):
            sent_to.append(int(chat_id))
            return None

        async def send_photo(self, chat_id, photo=None, caption="", **kw):
            sent_to.append(int(chat_id))
            return None

    upd = type("Update", (), {})()
    q = MockQuery("a:repfb:confirm", ADMIN)
    q.from_user = type("U", (), {"id": ADMIN})()
    upd.callback_query = q
    upd2 = upd
    upd2.callback_query = q

    with db.conn() as c:
        c.execute("DELETE FROM broadcast_channels")  # hermetic: other suites seed channels in the shared test DB
        c.execute("INSERT INTO broadcast_channels(chat_id,enabled,title,created_at,updated_at)"
                  " VALUES(?,?,?,?,?)", (-1001234, 1, "قناة الاختبار", "2026-11-27 09:00", "2026-11-27 09:00"))
    asyncio.run(admin_reports_ui.rep_filter_broadcast(q, ADMIN, ctx, _Bot(), "confirm"))
    assert sent_to == [-1001234, ADMIN]   # channel text + the owner's copy (v8.14.20) — nothing else
    assert ctx.user_data.get("repf_broadcast_text") is None  # state consumed


def test_broadcast_cancel_sends_nothing():
    ctx = MockContext()
    ctx.user_data["repf_broadcast_text"] = "x"
    sent = []

    class _Bot:
        async def send_message(self, chat_id, text=None, **kw):
            sent.append(chat_id)

    q, _ = _fire("a:repfb:cancel", ctx)
    assert sent == []
    assert ctx.user_data.get("repf_broadcast_text") is None
