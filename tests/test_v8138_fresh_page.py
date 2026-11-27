"""v8.13.8 tests — the royal fresh page (total signals-book wipe).

The owner's explicit decisions, verbatim:
  * wipe EVERYTHING from «الآن» — NO archive, 100% total deletion;
  * the mandatory «يوم جديد إشارات جديدة ونظام جديد» notice goes to every
    holder (+ admin) BEFORE the wipe;
  * scope = signals + deliveries + journal + exchange orders + bridge orders;
    demo positions/trades, engine_runs, analysis_monitors untouched;
  * the school race restarts from a REAL zero (leaderboard reads `signals`);
  * numbering is ROW_NUMBER-based → the next signal is literally #1.
"""
from __future__ import annotations

import sys

import pytest

sys.path.insert(0, ".")

import tests.helpers  # noqa: F401  (isolated temp DB + env)
from fysb import config, db
from fysb.engines import tracker
from fysb.services import strategy_stats


def _mk_sig(status="RUNNING", **kw):
    d = dict(symbol="XAUUSD", timeframe="15min", strategy="AMD", direction="SELL",
             entry=3673.0, stop=3684.8, tp1=3660.0, tp2=3650.0, tp3=3640.0,
             rr=2.5, score=88, status=status, source="ENGINE",
             created_at=config.now(), filled_at=config.now(), closed_at="",
             expires_at="")
    d.update(kw)
    with db.conn() as c:
        return c.execute("""INSERT INTO signals(symbol,timeframe,strategy,direction,entry,stop,tp1,tp2,tp3,
                           rr,score,status,source,created_at,filled_at,closed_at,expires_at)
                           VALUES(:symbol,:timeframe,:strategy,:direction,:entry,:stop,:tp1,:tp2,:tp3,
                           :rr,:score,:status,:source,:created_at,:filled_at,:closed_at,:expires_at)""",
                         d).lastrowid


@pytest.fixture()
def fresh_db():
    db.init_db()
    with db.conn() as c:
        c.execute("DELETE FROM signals")
        c.execute("DELETE FROM signal_deliveries")
        c.execute("DELETE FROM trade_journal")
        c.execute("DELETE FROM exchange_orders")
        c.execute("DELETE FROM bridge_orders")
        c.execute("DELETE FROM demo_positions")
        c.execute("DELETE FROM demo_trades")
        c.execute("DELETE FROM engine_runs")
    strategy_stats.invalidate_leaderboard_cache()
    yield


def test_wipe_removes_signals_and_all_in_scope_dependents(fresh_db):
    s1 = _mk_sig("RUNNING")
    s2 = _mk_sig("ACTIVE")
    with db.conn() as c:
        c.execute("INSERT INTO signal_deliveries(user_id,signal_id,delivered_at) VALUES(111,?,?)", (s1, config.now()))
        c.execute("INSERT INTO signal_deliveries(user_id,signal_id,delivered_at) VALUES(222,?,?)", (s2, config.now()))
        c.execute("INSERT INTO trade_journal(user_id,signal_id,symbol,direction,action,lots,created_at) "
                  "VALUES(111,?, 'XAUUSD','SELL','OPEN',0.01,?)", (s1, config.now()))
        c.execute("INSERT INTO exchange_orders(user_id,exchange,symbol,side,qty,signal_id,created_at) "
                  "VALUES(111,'binance','XAUUSD','SELL',0.01,?,?)", (s1, config.now()))
        c.execute("INSERT INTO bridge_orders(connection_id,user_id,platform,signal_id,symbol,direction,created_at,updated_at) "
                  "VALUES(1,111,'mt5',?, 'XAUUSD','SELL',?, ?)", (s1, config.now(), config.now()))
    counts = tracker.fresh_page_wipe()
    assert counts["signals"] == 2
    assert counts["signal_deliveries"] == 2
    assert counts["trade_journal"] == 1
    assert counts["exchange_orders"] >= 1
    assert counts["bridge_orders"] == 1
    with db.conn() as c:
        for t in ("signals", "signal_deliveries", "trade_journal", "exchange_orders", "bridge_orders"):
            assert c.execute(f"SELECT COUNT(*) n FROM {t}").fetchone()["n"] == 0, t


def test_out_of_scope_tables_survive(fresh_db):
    s = _mk_sig("RUNNING")
    with db.conn() as c:
        acct = c.execute("INSERT INTO demo_accounts(user_id,balance,created_at) "
                         "VALUES(9001,10000.0,?)", (config.now(),)).lastrowid
        c.execute("INSERT INTO demo_positions(account_id,user_id,symbol,direction,lots,entry,sl,tp,"
                  "status,opened_at) VALUES(?,9001,'XAUUSD','SELL',0.01,3673,3684.8,3660,'OPEN',?)",
                  (acct, config.now()))
        c.execute("INSERT INTO demo_trades(user_id,signal_id,status,created_at) "
                  "VALUES(9001,?, 'PAPER_ONLY',?)", (s, config.now()))
    tracker.fresh_page_wipe()
    with db.conn() as c:
        assert c.execute("SELECT COUNT(*) n FROM demo_positions").fetchone()["n"] == 1
        assert c.execute("SELECT COUNT(*) n FROM demo_trades").fetchone()["n"] == 1


def test_leaderboard_restarts_from_real_zero(fresh_db):
    _mk_sig("SL_HIT", pips_result=-25.0, hit_tp=0)
    before = strategy_stats.leaderboard()
    assert isinstance(before, list)          # whatever the old outcomes were
    tracker.fresh_page_wipe()
    assert strategy_stats.leaderboard() == []   # real zero — no ghost stats


def test_holders_include_admin_and_all_delivery_users(fresh_db):
    s1, s2 = _mk_sig("RUNNING"), _mk_sig("ACTIVE")
    with db.conn() as c:
        c.execute("INSERT INTO signal_deliveries(user_id,signal_id,delivered_at) VALUES(7001,?,?)", (s1, config.now()))
        c.execute("INSERT INTO signal_deliveries(user_id,signal_id,delivered_at) VALUES(7001,?,?)", (s2, config.now()))
        c.execute("INSERT INTO signal_deliveries(user_id,signal_id,delivered_at) VALUES(7002,?,?)", (s1, config.now()))
    holders = tracker.fresh_page_holders()
    assert set(holders) == {7001, 7002, config.ADMIN_ID}   # deduplicated + admin always


def test_engine_run_recorded_and_next_signal_is_number_one(fresh_db):
    _mk_sig("RUNNING")
    _mk_sig("EXPIRED")
    tracker.fresh_page_wipe()
    with db.conn() as c:
        row = c.execute("SELECT detail FROM engine_runs WHERE result='FRESH_PAGE' "
                        "ORDER BY id DESC LIMIT 1").fetchone()
    assert row is not None and "signals=2" in row["detail"]
    # numbering is ROW_NUMBER-based: a brand-new signal ranks #1
    new_id = _mk_sig("ACTIVE")
    assert tracker.seq_of(new_id) == 1


def test_counts_report_before_wipe(fresh_db):
    _mk_sig("RUNNING")
    _mk_sig("ACTIVE")
    counts = tracker.fresh_page_counts()
    assert counts["signals_total"] == 2 and counts["signals_open"] == 2


def test_admin_flow_wired():
    src = open("fysb/handlers/dispatcher.py", encoding="utf-8").read()
    assert '"a:freshpage"' in src.replace("elif act == \"freshpage\"", '"a:freshpage"') or "freshpage" in src
    src2 = open("fysb/handlers/admin_trading.py", encoding="utf-8").read()
    assert "a:freshpage" in src2 and "a:freshpagego" in src2
    assert "صفحة جديدة — مسح تاريخ الإشارات" in src2
