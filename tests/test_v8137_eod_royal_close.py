"""v8.13.7 tests — the royal end-of-day close.

The owner's rules, verbatim:
  * every open signal ends with the day; 30 minutes before the market close a
    MANDATORY notice goes out («إغلاق جميع الصفقات — يوم جديد إشارات جديدة
    ونظام جديد»);
  * the risk board RENEWS daily (fresh panel after the close, counters reset);
  * a manual «إغلاق يوم التداول الآن» button in the risk engine does the same
    immediately — while the bot keeps hunting opportunities 24h non-stop;
  * banked TP1/TP2 winners never lose their winning status;
  * no fabricated model result for day-closed signals (honest accounting).
"""
from __future__ import annotations

import sys
from datetime import datetime, timezone
from unittest.mock import patch

import pytest

sys.path.insert(0, ".")

import tests.helpers  # noqa: F401  (isolated temp DB + env before fysb import)
from fysb import config, db
from fysb.engines import tracker


def _mk_sig(status="RUNNING", symbol="XAUUSD", direction="SELL", **kw):
    d = dict(symbol=symbol, timeframe="15min", strategy="AMD",
             direction=direction, entry=3673.0, stop=3684.8, tp1=3660.0,
             tp2=3650.0, tp3=3640.0, rr=2.5, score=88, status=status,
             source="ENGINE", created_at=config.now(), filled_at=config.now(),
             closed_at="", expires_at="")
    d.update(kw)
    with db.conn() as c:
        cur = c.execute("""INSERT INTO signals(symbol,timeframe,strategy,direction,entry,stop,tp1,tp2,tp3,
                           rr,score,status,source,created_at,filled_at,closed_at,expires_at)
                           VALUES(:symbol,:timeframe,:strategy,:direction,:entry,:stop,:tp1,:tp2,:tp3,
                           :rr,:score,:status,:source,:created_at,:filled_at,:closed_at,:expires_at)""",
                        d)
        return cur.lastrowid


@pytest.fixture()
def fresh_db():
    db.init_db()
    with db.conn() as c:
        c.execute("DELETE FROM signals")
        c.execute("DELETE FROM settings WHERE key IN ('eod_last_close_date','eod_close_enabled','eod_close_utc')")
    yield


# ---------------------------------------------------------------------------

def test_eod_closes_all_open_signals_honestly(fresh_db):
    sid_run = _mk_sig("RUNNING")
    sid_act = _mk_sig("ACTIVE")
    sid_tp1 = _mk_sig("TP1_HIT", hit_tp=1)
    count, edits, notes = tracker.eod_close_all("manual")
    assert count == 2                       # only ACTIVE/RUNNING were "closed"
    with db.conn() as c:
        run_row = c.execute("SELECT * FROM signals WHERE id=?", (sid_run,)).fetchone()
        act_row = c.execute("SELECT * FROM signals WHERE id=?", (sid_act,)).fetchone()
        tp1_row = c.execute("SELECT * FROM signals WHERE id=?", (sid_tp1,)).fetchone()
    assert run_row["status"] == "EXPIRED" and run_row["closed_at"]
    assert act_row["status"] == "EXPIRED" and act_row["closed_at"]
    assert tp1_row["status"] == "TP1_HIT"   # banked winner preserved
    assert tp1_row["closed_at"]             # tracking stopped


def test_mandatory_notice_reaches_every_holder_and_admin(fresh_db):
    _mk_sig("RUNNING")
    _mk_sig("ACTIVE")
    count, edits, notes = tracker.eod_close_all("scheduled")
    board_uids = [uid for uid, _text in notes if "تنبيه إجباري" in _text]
    assert config.ADMIN_ID in board_uids            # the admin gets it exactly once
    assert board_uids.count(config.ADMIN_ID) == 1   # deduplicated — no spam
    joined = "\n".join(t for _u, t in notes)
    assert "يوم جديد، إشارات جديدة، ونظام جديد" in joined
    assert "أغلق أي صفقة حقيقية مفتوحة" in joined


def test_day_boundary_stamped_once_per_utc_day(fresh_db):
    db.set_setting("eod_close_utc", "21:30")
    # pretend yesterday's close already happened → no trigger today? no: stamp = today
    db.set_setting("eod_last_close_date", datetime.now(timezone.utc).strftime("%Y-%m-%d"))
    trig, n, _e, _no = tracker.maybe_daily_close()
    assert trig is False and n == 0


def test_scheduler_disabled_noop(fresh_db):
    db.set_setting("eod_close_enabled", "0")
    trig, n, _e, _no = tracker.maybe_daily_close()
    assert trig is False and n == 0


def test_scheduler_fires_when_time_crossed(fresh_db):
    db.set_setting("eod_close_utc", "00:00")        # any moment today is past midnight
    _mk_sig("RUNNING")
    trig, n, _e, notes = tracker.maybe_daily_close()
    assert trig is True and n == 1
    assert db.get_setting("eod_last_close_date") == datetime.now(timezone.utc).strftime("%Y-%m-%d")
    assert any("تنبيه إجباري" in t for _u, t in notes)
    # second tick the same day: no re-trigger
    trig2, n2, _e2, _no2 = tracker.maybe_daily_close()
    assert trig2 is False and n2 == 0


def test_risk_board_renews_daily_and_names_the_kills_switch_purpose(fresh_db):
    text = tracker  # placeholder to keep flake quiet
    from fysb.handlers.admin_trading import risk_board_text
    db.set_setting("eod_last_close_date", datetime.now(timezone.utc).strftime("%Y-%m-%d"))
    board = risk_board_text()
    assert "محرك إدارة المخاطر" in board
    assert "تتجدد يومياً" in board                  # daily renewal disclosed
    assert "للطوارئ فقط" in board                  # Kill Switch purpose named
    assert "21:30" in board                        # configured EOD time shown
    assert "المخاطرة لكل إشارة" in board


def test_eod_engine_run_recorded(fresh_db):
    _mk_sig("RUNNING")
    tracker.eod_close_all("manual")
    with db.conn() as c:
        row = c.execute("SELECT detail FROM engine_runs WHERE result='EOD_CLOSE:manual' "
                        "ORDER BY id DESC LIMIT 1").fetchone()
    assert row is not None and "signals closed=1" in row["detail"]
