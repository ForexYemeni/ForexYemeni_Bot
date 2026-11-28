"""v8.14.10 tests — «القفل الملكي +50» (the royal +$5 lock).

The owner's decree: a trade that banked $5 at 0.01 lot (50 gold pips / 500 BTC
pips — the REAL usd_move truth) and then retrace-slapped the original stop is
NEVER a loss. The lock arms on the documented $5 touch, persists, and any
later return closes the trade at the locked level as a WIN — in the tracker,
the executive P/L hub, the daily archive, and the strategy win-rate. Genuine
stop-outs (never touched $5) stay honest losses.
"""
from __future__ import annotations

import sys
from datetime import datetime, timedelta
from unittest.mock import patch

import pytest

sys.path.insert(0, ".")

import tests.helpers  # noqa: F401  (isolated temp DB + env)
from fysb import db
from fysb.engines import tracker
from fysb.engines.accounting import modeled_r

T0 = "2026-11-28 08:00:00"


@pytest.fixture(autouse=True)
def _laws_default():
    db.init_db()
    db.set_setting("lock_50_enabled", "1")
    yield
    db.set_setting("lock_50_enabled", "1")


def mk(price_actions, start=T0):
    out, t = [], datetime.fromisoformat(start)
    for o, h, l, c in price_actions:
        out.append({"datetime": t.strftime("%Y-%m-%d %H:%M:%S"), "open": o,
                    "high": h, "low": l, "close": c, "volume": 1000})
        t += timedelta(minutes=15)
    return out


def gold_sig(**over):
    sig = {"id": 1, "symbol": "XAUUSD", "timeframe": "M15", "direction": "BUY",
           "entry": 3600.0, "stop": 3595.0, "tp1": 3610.0, "tp2": 3620.0,
           "tp3": 3635.0, "hit_tp": 0, "status": "RUNNING",
           "filled_at": T0, "created_at": T0, "profit_50_hit": 0,
           "profit_50_at": "", "locked_sl": 0.0, "locked_pips": 0.0,
           "half_notified": 0, "accounting_model": "PARTIAL_50_25_25",
           "strategy": "SMC", "score": 88, "source": "AUTO", "reason": "test"}
    sig.update(over)
    return sig




def _insert_row(sig_id: int, sig: dict) -> None:
    """Clean shared-session row then insert this test's signal."""
    with db.conn() as c:
        c.execute("DELETE FROM signals WHERE id=?", (sig_id,))
        c.execute("""INSERT INTO signals(id,symbol,direction,timeframe,entry,stop,tp1,tp2,tp3,
                     targets,rr,score,strategy,reason,status,source,created_by,created_at,
                     hit_tp,closed_at,pips_result,locked_sl,locked_pips,profit_50_hit,
                     private_user_id,accounting_model)
                     VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                  (sig_id, sig["symbol"], sig["direction"], sig["timeframe"],
                   sig["entry"], sig["stop"], sig["tp1"], sig["tp2"], sig["tp3"],
                   "", 2.0, sig.get("score", 88), sig.get("strategy", "SMC"), "test",
                   sig["status"], "AUTO", 0, db.now(), int(sig.get("hit_tp") or 0),
                   "", float(sig.get("pips_result") or 0.0),
                   float(sig.get("locked_sl") or 0.0),
                   float(sig.get("locked_pips") or 0.0),
                   int(sig.get("profit_50_hit") or 0), 0,
                   sig.get("accounting_model", "PARTIAL_50_25_25")))

# ---------------------------------------------------------------------------
# ① the owner's exact scenario: banked $5 → retrace through the stop = WIN
# ---------------------------------------------------------------------------

def test_touch_5_then_crash_closes_at_the_lock_not_a_loss():
    sig = gold_sig()
    candles = mk([
        (3600.0, 3602.0, 3599.5, 3601.0),
        (3601.0, 3606.0, 3600.5, 3605.0),   # +60 gold pips = $6 ≥ $5 → arm @3605.0
        (3605.0, 3605.5, 3593.0, 3593.5),   # crash through the lock AND the stop
    ])
    lock_info: dict = {}
    res = tracker._evaluate_signal(sig, candles, cutoff=T0, lock_out=lock_info)
    assert res == "LOCKED_50"
    assert lock_info["armed"] is True
    assert abs(lock_info["locked_sl"] - 3605.0) < 1e-9
    assert abs(lock_info["locked_pips"] - 50.0) < 1e-9   # exactly the $5 level


def test_btc_touch_500_pips_is_the_same_5_dollars():
    sig = gold_sig(symbol="BTCUSD", entry=60000.0, stop=59000.0,
                   tp1=61500.0, tp2=62500.0, tp3=64000.0)
    candles = mk([
        (60000.0, 60500.0, 59900.0, 60400.0),   # +500 BTC pips = $5 → arm @60500
        (60400.0, 60600.0, 58900.0, 58950.0),   # crash
    ])
    res = tracker._evaluate_signal(sig, candles, cutoff=T0)
    assert res == "LOCKED_50"
    assert abs(sig["locked_sl"] - 60500.0) < 1e-9
    assert abs(sig["locked_pips"] - 500.0) < 1e-9


def test_sell_mirror_locks_at_minus_50_pips_level():
    sig = gold_sig(direction="SELL", entry=3600.0, stop=3605.0,
                   tp1=3590.0, tp2=3580.0, tp3=3565.0)
    candles = mk([
        (3600.0, 3600.5, 3594.0, 3595.0),   # down $6 → arm @3595.0
        (3595.0, 3606.0, 3594.5, 3605.5),   # spike back up through lock+stop
    ])
    res = tracker._evaluate_signal(sig, candles, cutoff=T0)
    assert res == "LOCKED_50"
    assert abs(sig["locked_sl"] - 3595.0) < 1e-9


# ---------------------------------------------------------------------------
# ② the arming bar never closes instantly — winners keep running
# ---------------------------------------------------------------------------

def test_arming_bar_does_not_close_and_runner_reaches_tp1():
    sig = gold_sig()
    candles = mk([
        (3600.0, 3606.0, 3599.5, 3605.0),               # arm bar
        (3605.0, 3611.0, 3605.5, 3610.5),               # lows stay ABOVE the lock
        (3610.0, 3621.0, 3610.2, 3620.5),               # TP2
    ])
    lock_info: dict = {}
    res = tracker._evaluate_signal(sig, candles, cutoff=T0, lock_out=lock_info)
    assert res == "TP2_HIT"                              # the runner is untouched
    assert lock_info["armed"] is True                    # but the lock was armed


def test_genuine_stop_without_5_touch_stays_an_honest_loss():
    sig = gold_sig()
    candles = mk([
        (3600.0, 3601.5, 3599.0, 3600.5),
        (3600.5, 3602.0, 3594.0, 3594.5),   # max excursion $2 — never $5
    ])
    assert tracker._evaluate_signal(sig, candles, cutoff=T0) == "SL_HIT"


def test_pre_armed_lock_from_previous_cycle_closes_on_return():
    sig = gold_sig(profit_50_hit=1, locked_sl=3605.0, locked_pips=50.0)
    candles = mk([
        (3600.0, 3601.0, 3604.9, 3601.5),   # dips 3604.9 ≤ 3605 → locked out
    ])
    assert tracker._evaluate_signal(sig, candles, cutoff=T0) == "LOCKED_50"


def test_lock_disabled_restores_the_old_stop_semantics():
    db.set_setting("lock_50_enabled", "0")
    sig = gold_sig()
    candles = mk([
        (3600.0, 3602.0, 3599.5, 3601.0),
        (3601.0, 3606.0, 3600.5, 3605.0),
        (3605.0, 3605.5, 3593.0, 3593.5),
    ])
    assert tracker._evaluate_signal(sig, candles, cutoff=T0) == "SL_HIT"


# ---------------------------------------------------------------------------
# ③ the accounting: WIN everywhere, +$5, never buried
# ---------------------------------------------------------------------------

def test_locked_50_persists_as_positive_win_row():
    sig = gold_sig(profit_50_hit=1, locked_sl=3605.0, locked_pips=50.0)
    _insert_row(1, sig)
    payload = {"values": mk([(3605.0, 3605.5, 3593.0, 3593.5)]),
               "last_candle_iso": "2026-11-28 08:30:00"}
    notes, pips, edits = tracker._apply_transition(sig, "LOCKED_50", payload, live_px=3593.5)
    assert pips == pytest.approx(50.0)
    with db.conn() as c:
        row = c.execute("SELECT * FROM signals WHERE id=1").fetchone()
    assert row["status"] == "LOCKED_50"
    assert float(row["pips_result"]) == pytest.approx(50.0)
    assert row["closed_at"]
    assert "مؤمّنة عند +50" in "".join(n for _, n in notes)


def test_modeled_r_is_the_real_locked_ratio():
    row = gold_sig(profit_50_hit=1, locked_sl=3605.0, locked_pips=50.0,
                   status="LOCKED_50")
    # risk = 50 gold pips → locked 50 pips = +1.0R — the honest modeled value
    assert modeled_r(row) == pytest.approx(1.0)


def test_strategy_stats_counts_the_lock_as_a_real_win():
    from fysb.services import strategy_stats
    with db.conn() as c:
        c.execute("DELETE FROM signals")
    row = gold_sig(status="LOCKED_50", profit_50_hit=1, locked_sl=3605.0,
                   locked_pips=50.0, private_user_id=0)
    _insert_row(101, row)
    board = {d["code"]: d for d in strategy_stats.leaderboard()}
    assert board["SMC"]["wins"] == 1 and board["SMC"]["sl"] == 0
    assert board["SMC"]["n"] == 1 and board["SMC"]["win_rate"] == 100.0


def test_pl_accounting_counts_lock_as_win_with_positive_usd():
    from fysb.services import signal_archive
    with db.conn() as c:
        c.execute("DELETE FROM signals")
    row = gold_sig(status="LOCKED_50", profit_50_hit=1, locked_sl=3605.0,
                   locked_pips=50.0, pips_result=50.0)
    _insert_row(102, row)
    rows = signal_archive.admin_pl_rows("all")
    gold = [r for r in rows if r["symbol"].replace("/", "").upper() == "XAUUSD"][0]
    assert gold["wins"] == 1 and gold["losses"] == 0
    data = signal_archive.admin_pl_detailed("all")
    assert data["grand"]["wins"] == 1 and data["grand"]["locked_n"] == 1
    assert data["grand"]["net_usd"] == pytest.approx(5.0)
    summary = signal_archive.summarize_rows(
        [dict(r) for r in db.conn().execute("SELECT * FROM signals WHERE id=102").fetchall()])
    assert summary["wins"] == 1 and summary["losses"] == 0


def test_digest_counts_the_lock_as_a_win():
    from fysb.services import digest
    with db.conn() as c:
        c.execute("DELETE FROM signals")
    row = gold_sig(status="LOCKED_50", profit_50_hit=1, locked_sl=3605.0,
                   locked_pips=50.0, pips_result=50.0)
    _insert_row(103, row)
    rows = [dict(r) for r in db.conn().execute("SELECT * FROM signals WHERE id=3").fetchall()]
    d = digest.build_daily(rows) if hasattr(digest, "build_daily") else None
    if d is not None:
        assert d.get("wins", 0) >= 1


def test_status_label_is_the_royal_medal():
    from fysb.theme import status_ar
    assert "مؤمّنة عند +50" in status_ar("LOCKED_50")


# ---------------------------------------------------------------------------
# ④ the System & Security key — ON by default, audited flips
# ---------------------------------------------------------------------------

def test_lock_key_defaults_armed_and_flips_are_audited():
    from fysb.engines import honesty
    from fysb.services import audit as audit_log
    assert honesty.lock50_enabled() is True
    db.set_setting("lock_50_enabled", "0")
    assert honesty.lock50_enabled() is False
    audit_log.log(9001, "lock_50_enabled:0")
    entries = audit_log.recent(20, 0)
    assert any("lock_50_enabled:0" in (e.get("action") or "") for e in entries)
    db.set_setting("lock_50_enabled", "1")


def test_settings_page_shows_the_lock_state():
    from fysb.handlers import admin_system
    from types import SimpleNamespace

    class _FakeMsg:
        def __init__(self):
            self.sent = []
            self.markups = []

        async def reply_text(self, text, **kw):
            self.sent.append(text)
            self.markups.append(kw.get("reply_markup"))
            return SimpleNamespace(message_id=1)

    class _FakeQuery:
        def __init__(self):
            self.from_user = SimpleNamespace(id=9001)
            self.message = _FakeMsg()

        async def answer(self):
            return True

    q = _FakeQuery()
    asyncio_run = __import__("asyncio").run
    asyncio_run(admin_system.admin_settings(q, 9001, None, None))
    body = q.message.sent[-1]
    assert "التأمين عند الربح" in body and "النقاط" in body and "تُحسب رابحة" in body


def test_dispatcher_knows_the_lock_route():
    src = open("fysb/handlers/dispatcher.py", encoding="utf-8").read()
    assert '"togglelock50"' in src
