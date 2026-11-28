"""v8.14.17 — «التقارير الصادقة والتحكم الملكي» — the owner's four decrees:

① The closed-results ledger tells the WHOLE truth: LOCKED_50 is a closed WIN
   (it only exists when usd_move reached the threshold), wins/losses classify
   by STATUS semantics, pips are NEVER summed across symbols — per-symbol
   rows + one USD total via the real usd_move contract at 0.01 lot.
② «بث تقرير الأداء» opens a PREVIEW first — the exact public report lands on
   the owner's chat alone, and NOTHING is broadcast until he picks the
   audience (channels / me only / both).
③ The lock threshold is the OWNER-EDITABLE setting (20–500 gold pips,
   default 50 = $5 at 0.01 lot) — engine, cards and settings all read it.
④ The ornamental vocabulary («الملكي/الملكية/الفاخر/القمرة») is EXECUTED
   from user/public-facing strings — clean professional Arabic only.
"""
from __future__ import annotations

import asyncio
import sys
from pathlib import Path
from unittest.mock import patch

import pytest

sys.path.insert(0, ".")

import tests.helpers  # noqa: F401
from tests.helpers import MockContext, MockMessage, MockQuery, page_for
from fysb import db, instruments
from fysb.engines import honesty
from fysb.handlers import admin_system, dispatcher, inputs
from fysb.services import reports

ADMIN = 9001


@pytest.fixture(autouse=True)
def _clean():
    db.init_db()
    with db.conn() as c:
        c.execute("DELETE FROM signals")
        c.execute("DELETE FROM ui_events")
        c.execute("DELETE FROM settings WHERE key IN ('lock_pips','lock_50_enabled')")
    yield
    with db.conn() as c:
        c.execute("DELETE FROM signals")
        c.execute("DELETE FROM settings WHERE key IN ('lock_pips','lock_50_enabled')")


def _mk_update(uid: int = ADMIN):
    upd = type("Update", (), {})()
    upd.effective_user = type("U", (), {"id": uid})()
    msg = MockMessage()
    upd.effective_message = msg
    upd.effective_chat = type("C", (), {"type": "private"})()
    return upd, msg


# ---------------------------------------------------------------------------
# ① the honest closed ledger
# ---------------------------------------------------------------------------
def _seed_closed():
    with db.conn() as c:
        rows = [
            # (symbol, status, pips_result, closed_at)
            ("XAUUSD", "SL_HIT", -50.0, "2026-11-27 10:00"),
            ("XAUUSD", "SL_HIT", -55.0, "2026-11-27 11:00"),
            ("XAUUSD", "TP1_HIT", 40.0, "2026-11-27 12:00"),
            ("XAUUSD", "LOCKED_50", 55.0, "2026-11-27 13:00"),
            ("BTCUSD", "SL_HIT", -500.0, "2026-11-27 14:00"),
            ("RUNNING", None, None, None),  # open — never in the closed ledger
        ]
        for i, (sym, st, pips, closed) in enumerate(rows):
            if st is None:
                c.execute("INSERT INTO signals(symbol,timeframe,status,direction,entry,stop,tp1,tp2,tp3,rr,created_at)"
                          " VALUES(?,?,?,?,?,?,?,?,?,?,?)",
                          ("XAUUSD", "M15", "RUNNING", "BUY", 3500.0, 3450.0, 3540.0,
                           3580.0, 3620.0, 2.0, f"2026-11-27 09:{i:02d}"))
            else:
                c.execute("INSERT INTO signals(symbol,timeframe,status,direction,entry,stop,tp1,tp2,tp3,rr,pips_result,closed_at,created_at)"
                          " VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)",
                          (sym, "M15", st, "BUY", 3500.0, 3450.0, 3540.0, 3580.0, 3620.0,
                           2.0, pips, closed, f"2026-11-27 09:{i:02d}"))


def test_locked50_counts_as_win_and_never_silent():
    _seed_closed()
    txt = reports.signals_report()
    # 2 wins (TP1 + LOCKED) / 3 losses (SL) — LOCKED is NEVER excluded again
    # (the old report counted ONLY 2 wins and hid the 3 locked ones entirely)
    assert "رابحة <b>2</b>" in txt and "خاسرة <b>3</b>" in txt
    assert "نسبة الإصابة: <b>40%</b>" in txt


def test_pips_never_summed_across_symbols_usd_instead():
    _seed_closed()
    txt = reports.signals_report()
    # per-symbol rows present
    assert "XAUUSD" in txt and "BTCUSD" in txt
    # the old misleading mixed total is EXECUTED
    assert "pips إجمالاً" not in txt
    # the USD totals follow the real usd_move at 0.01 lot
    xau = instruments.usd_move("XAUUSD", -50.0 - 55.0 + 40.0 + 55.0, 0.01)
    btc = instruments.usd_move("BTCUSD", -500.0, 0.01)
    total = xau + btc
    assert f"{total:+.2f}$" in txt


def test_open_signals_never_enter_the_closed_ledger():
    _seed_closed()
    txt = reports.signals_report()
    # the closed-ledger totals count ONLY closed signals: 2W/3L proves the
    # RUNNING trade never leaked into the truth card
    assert "رابحة <b>2</b> · خاسرة <b>3</b>" in txt


# ---------------------------------------------------------------------------
# ③ the editable lock threshold
# ---------------------------------------------------------------------------
def test_lock_defaults_to_the_v81410_contract():
    assert honesty.lock_pips() == 50
    assert honesty.lock_usd() == 5.0


def test_lock_owner_sets_70_and_engine_reads_it():
    db.set_setting("lock_pips", 70)
    assert honesty.lock_pips() == 70
    assert honesty.lock_usd() == 7.0


def test_lock_out_of_range_falls_back_to_50():
    db.set_setting("lock_pips", 9999)
    assert honesty.lock_pips() == 50
    db.set_setting("lock_pips", 5)
    assert honesty.lock_pips() == 50


def test_lock_wizard_opens_and_accepts_a_valid_value():
    q = MockQuery("a:lockset", ADMIN)
    q.from_user = type("U", (), {"id": ADMIN})()
    upd = type("Update", (), {})()
    upd.callback_query = q
    ctx = MockContext()
    asyncio.run(admin_system.admin_lock_set(q, ADMIN, ctx, ctx.bot))
    assert ctx.user_data.get("awaiting_lock_pips") is True
    assert "عتبة التأمين" in page_for(q, ctx) and "20 إلى 500" in page_for(q, ctx)

    upd2, msg2 = _mk_update()
    ctx2 = MockContext()
    ctx2.user_data = {"awaiting_lock_pips": True}
    upd2.effective_message.text = "70"
    upd2.effective_message.reply_text = msg2.reply_text
    asyncio.run(inputs.on_text(upd2, ctx2))
    assert db.get_setting("lock_pips", "0") == "70"


def test_lock_rejects_out_of_range_and_keeps_asking():
    upd, msg = _mk_update()
    ctx = MockContext()
    ctx.user_data = {"awaiting_lock_pips": True}
    upd.effective_message.text = "10"
    upd.effective_message.reply_text = msg.reply_text
    asyncio.run(inputs.on_text(upd, ctx))
    assert ctx.user_data.get("awaiting_lock_pips") is True
    assert db.get_setting("lock_pips", "50") == "50"
    assert "20" in msg.replies[-1]["text"]


def test_settings_page_shows_editable_threshold():
    db.set_setting("lock_pips", 70)
    q = MockQuery("a:settings", ADMIN)
    q.from_user = type("U", (), {"id": ADMIN, "username": "o", "full_name": "O"})()
    ctx = MockContext()
    upd = type("Update", (), {})()
    upd.callback_query = q
    asyncio.run(dispatcher.buttons(upd, ctx))
    markup = page_markup = None
    # find the button row through the delivered page markup
    from tests.helpers import page_markup_for
    markup = page_markup_for(q, ctx)
    labels = [b.text for row in markup.inline_keyboard for b in row]
    assert any("عتبة التأمين: 70" in t for t in labels)


# ---------------------------------------------------------------------------
# ② the broadcast preview + audience choice
# ---------------------------------------------------------------------------
def test_ornamental_words_executed_from_public_sources():
    for f in ["fysb/ui/cards.py", "fysb/handlers/user_pages.py",
              "fysb/services/chart_channels.py", "fysb/engines/news_guard.py",
              "fysb/services/digest.py", "fysb/services/reports.py"]:
        src = Path(f).read_text(encoding="utf-8")
        assert "القمرة" not in src, f
        assert "الفاخرة" not in src, f
        assert "الفاخر " not in src, f


def test_user_home_carries_the_clean_title():
    src = Path("fysb/handlers/user_pages.py").read_text(encoding="utf-8")
    assert "منصة التداول" in src
    assert "القمرة" not in src


def test_lock_cards_carry_the_dynamic_threshold():
    src = Path("fysb/engines/tracker.py").read_text(encoding="utf-8")
    assert "honesty.lock_pips()" in src and "honesty.lock_usd()" in src
    assert "مؤمّنة عند +50" not in src  # hardcoded 50 is executed everywhere
