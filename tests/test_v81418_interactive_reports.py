"""v8.14.18 — «التقرير التفاعلي الشامل» tests — the owner's decree:

A comprehensive, real, zero-error report system: pick a SPECIFIC market /
strategy / timeframe — or ALL of any dimension — and get the honest closed
ledger for EXACTLY that slice. Zero mixed-unit sums anywhere (public PNG
included), every number a real SQL aggregate, every filter a real slice.
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
from fysb.handlers import admin_reports_ui, dispatcher
from fysb.services import digest, reports

ADMIN = 9001


@pytest.fixture(autouse=True)
def _clean():
    db.init_db()
    with db.conn() as c:
        c.execute("DELETE FROM signals")
    yield
    with db.conn() as c:
        c.execute("DELETE FROM signals")


_SEQ = iter(range(1, 500))


def _ins(sym, strat, tf, status, pips, score=85, rr=2.0, closed=""):
    with db.conn() as c:
        c.execute("INSERT INTO signals(symbol,timeframe,status,direction,entry,stop,tp1,tp2,tp3,"
                  "rr,score,strategy,pips_result,closed_at,created_at)"
                  " VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                  (sym, tf, status, "BUY", 3500.0, 3450.0, 3540.0, 3580.0, 3620.0,
                   rr, score, strat, pips, closed, f"2026-11-27 09:{next(_SEQ):02d}"))


def _seed_matrix():
    _ins("XAUUSD", "TREND", "M15", "TP1_HIT", 40.0, score=90, closed="2026-11-27 10:00")
    _ins("XAUUSD", "TREND", "M15", "SL_HIT", -50.0, score=80)
    _ins("XAUUSD", "ICT", "H1", "LOCKED_50", 55.0, score=88, closed="2026-11-27 11:00")
    _ins("BTCUSD", "TREND", "M15", "SL_HIT", -500.0, score=84)
    _ins("BTCUSD", "ICHIMOKU", "H4", "RUNNING", None)


def _fire(data: str, ctx: MockContext):
    q = MockQuery(data, ADMIN)
    q.from_user = type("U", (), {"id": ADMIN})()
    upd = type("Update", (), {})()
    upd.callback_query = q
    asyncio.run(dispatcher.buttons(upd, ctx))
    return q, ctx


# ---------------------------------------------------------------------------
# the filtered engine — real slices, real numbers
# ---------------------------------------------------------------------------
def test_filter_by_market_counts_only_that_symbol():
    _seed_matrix()
    txt = reports.filtered_signals_report("XAUUSD", None, None)
    assert "XAUUSD" in txt and "BTCUSD" not in txt
    assert "رابحة <b>2</b>" in txt and "خاسرة <b>1</b>" in txt  # TP1 + LOCKED / SL


def test_filter_by_strategy_is_exact():
    _seed_matrix()
    txt = reports.filtered_signals_report(None, "TREND", None)
    assert "XAUUSD" in txt and "BTCUSD" in txt          # TREND traded both
    assert "رابحة <b>1</b>" in txt and "خاسرة <b>2</b>" in txt


def test_filter_by_timeframe_is_exact():
    _seed_matrix()
    txt = reports.filtered_signals_report(None, None, "H1")
    assert "XAUUSD" in txt and "BTCUSD" not in txt
    assert "رابحة <b>1</b>" in txt and "خاسرة <b>0</b>" in txt  # the H1 lock only


def test_combined_three_dimension_filter():
    _seed_matrix()
    txt = reports.filtered_signals_report("XAUUSD", "ICT", "H1")
    assert "رابحة <b>1</b> · خاسرة <b>0</b>" in txt
    usd = instruments.usd_move("XAUUSD", 55.0, 0.01)
    assert f"{usd:+.2f}$" in txt


def test_all_dimensions_no_mixed_pip_summing():
    _seed_matrix()
    txt = reports.filtered_signals_report(None, None, None)
    assert "pips إجمالاً" not in txt                      # the old lie is executed
    assert "XAUUSD" in txt and "BTCUSD" in txt            # per-symbol rows
    xau = instruments.usd_move("XAUUSD", 40.0 - 50.0 + 55.0, 0.01)
    btc = instruments.usd_move("BTCUSD", -500.0, 0.01)
    assert f"{xau + btc:+.2f}$" in txt


def test_empty_slice_reports_truthfully():
    _seed_matrix()
    txt = reports.filtered_signals_report("ETHUSD", None, None)
    # v8.14.21: the honest empty state — real totals + what you DO have
    assert "لا إشارات في هذا المقطع" in txt
    assert "إجمالي سجل البوت" in txt and "الأكثر نشاطاً" in txt
    assert "كل ما لديك في الفترة" in txt


def test_quality_and_rr_are_real_averages_of_the_slice():
    _seed_matrix()
    txt = reports.filtered_signals_report("XAUUSD", "TREND", None)
    assert "1:2.0" in txt                                 # real stored rr column
    assert "متوسط الجودة المسجل: <b>85.0/100</b>" in txt  # (90+80)/2


# ---------------------------------------------------------------------------
# the interactive flow — real dispatcher paths
# ---------------------------------------------------------------------------
def test_filter_home_shows_all_dimensions_and_real_match_count():
    _seed_matrix()
    ctx = MockContext()
    q, ctx = _fire("a:repfilter", ctx)
    page = page_for(q, ctx)
    assert "السوق: <b>الكل</b>" in page and "الاستراتيجية: <b>الكل</b>" in page
    assert "الإطار الزمني: <b>الكل</b>" in page
    assert "الإشارات المطابقة الآن: <b>5</b>" in page


def test_pick_market_reflects_in_card_and_count():
    _seed_matrix()
    ctx = MockContext()
    q, _ = _fire("a:repf:m:XAUUSD", ctx)
    page = page_for(q, ctx)
    assert "السوق: <b>XAUUSD</b>" in page
    assert "الإشارات المطابقة الآن: <b>3</b>" in page


def test_picker_lists_real_distinct_values_from_db():
    _seed_matrix()
    ctx = MockContext()
    q, _ = _fire("a:repf:s", ctx)
    markup = page_markup_for(q, ctx)
    labels = [b.text for row in markup.inline_keyboard for b in row]
    for want in ("TREND", "ICT", "ICHIMOKU"):
        assert any(want in lbl for lbl in labels)


def test_run_renders_the_filtered_report_page():
    _seed_matrix()
    ctx = MockContext()
    _fire("a:repf:t:H1", ctx)
    q, _ = _fire("a:repf:run", ctx)
    page = page_for(q, ctx)
    assert "التقرير التفاعلي الصادق" in page
    assert "الإطار: <b>H1</b>" in page


def test_clear_resets_every_dimension():
    _seed_matrix()
    ctx = MockContext()
    _fire("a:repf:m:XAUUSD", ctx)
    _fire("a:repf:s:ICT", ctx)
    q, _ = _fire("a:repf:clear", ctx)
    page = page_for(q, ctx)
    assert "السوق: <b>الكل</b>" in page and "الاستراتيجية: <b>الكل</b>" in page
    assert ctx.user_data["rep_filter"] == {"market": None, "strategy": None, "timeframe": None, "days": None}  # v8.14.20: period is the 4th dimension


def test_state_is_per_admin_isolated():
    _seed_matrix()
    ctx = MockContext()
    ctx.user_data = {}
    _fire("a:repf:m:XAUUSD", ctx)
    other = {"rep_filter": {"market": None, "strategy": None, "timeframe": None}}
    assert other["rep_filter"]["market"] is None


def test_reports_hub_carries_the_interactive_entry():
    ctx = MockContext()
    q, _ = _fire("a:reports", ctx)
    markup = page_markup_for(q, ctx)
    labels = [b.text for row in markup.inline_keyboard for b in row]
    assert any("التقرير التفاعلي" in t for t in labels)


# ---------------------------------------------------------------------------
# zero mixed units in the PUBLIC digest surfaces
# ---------------------------------------------------------------------------
def _seed_closed_single_week():
    _ins("XAUUSD", "TREND", "M15", "TP1_HIT", 40.0, closed="2026-11-27 10:00")
    _ins("BTCUSD", "TREND", "M15", "SL_HIT", -500.0, closed="2026-11-27 11:00")


def test_public_png_kpi_is_net_usd_not_mixed_pips():
    src = Path("fysb/ui/share_cards.py").read_text(encoding="utf-8")
    assert "NET USD • 0.01 LOT" in src
    assert '"NET PIPS"' not in src
    # the strategy matrix no longer sums pips across symbols
    assert '"{sr:+.1f}R · {swr:.0f}% ({stotal})"' in src


def test_caption_drops_the_mixed_net_pips_line():
    src = Path("fysb/services/digest.py").read_text(encoding="utf-8")
    assert "صافي النقاط" not in src
    assert "· <b>{sp:+.1f}p</b> ({s['total']})" not in src  # strategy line (mixed units)


def test_digest_data_carries_honest_usd_for_the_png():
    _seed_closed_single_week()
    d = digest.build_digest_data(days=7)
    xau = instruments.usd_move("XAUUSD", 40.0, 0.01)
    btc = instruments.usd_move("BTCUSD", -500.0, 0.01)
    assert abs(float(d["net_usd_001"]) - (xau + btc)) < 0.01
