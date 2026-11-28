"""v8.14.11 «الجمالية الملكية» tests — the owner's five decrees in one suite:

  ① the royal share card NEVER lies again: closed trades speak their
     documented result (LOCKED_50 → locked_sl · SL → stop · TP → the banked
     level); open trades speak their true floating value — zero «0 pips» on
     winners, zero live drift on recorded results.
  ② demo auto-execution is fill-gated (covered in phase6's rewritten test):
     creation never opens a position; the tracker's RUNNING transition does.
  ③ the royal opposite-trade alert: 100% real holdings (demo OPEN +
     journal TAKEN OPEN), per-user luxury toggle, honest Arabic.
  ④ the near-lock whisper: one documented +$4 touch → one luxury alert in
     the signal's lifetime; ≥$5 arms the full lock instead.
  ⑤ the Honest Absence Ledger + the royal locks board + the personal daily
     digest — all built only from recorded rows, never fabricated.
"""
from __future__ import annotations

import sys
from datetime import datetime, timedelta, timezone
from unittest.mock import patch

import pytest

sys.path.insert(0, ".")

import tests.helpers  # noqa: F401  (isolated temp DB + env)
from tests.helpers import FrozenFridayDT
from fysb import config, db
from fysb.engines import tracker
from fysb.ui.share_cards import signal_share_payload

T0 = "2026-11-27 08:00:00"


@pytest.fixture(autouse=True)
def _clean_shared_session():
    db.init_db()
    with db.conn() as c:
        c.execute("DELETE FROM signals")
        c.execute("DELETE FROM signal_deliveries")
        c.execute("DELETE FROM engine_runs")
        c.execute("DELETE FROM demo_positions")
        c.execute("DELETE FROM trade_journal")
    yield


def _sig_row(sid: int, **over) -> dict:
    sig = {"id": sid, "symbol": "XAUUSD", "timeframe": "M15", "direction": "SELL",
           "entry": 3581.60, "stop": 3592.91, "tp1": 3558.97, "tp2": 3547.66,
           "tp3": 3530.69, "rr": 2.0, "score": 95, "strategy": "SMC", "hit_tp": 0,
           "reason": "ctx", "status": "RUNNING", "source": "AUTO",
           "created_at": T0, "closed_at": "", "pips_result": 0.0,
           "profit_50_hit": 0, "profit_50_at": "", "locked_sl": 0.0,
           "locked_pips": 0.0, "near_50_at": "", "live_px": 0.0,
           "accounting_model": "LEGACY_LEVEL"}
    sig.update(over)
    return sig


# ---------------------------------------------------------------------------
# ① the honest share card
# ---------------------------------------------------------------------------

def test_share_payload_lock_uses_documented_result():
    sig = _sig_row(1, status="LOCKED_50", closed_at="2026-11-27T09:00:00+00:00",
                   pips_result=50.0, locked_sl=3576.60, locked_pips=50.0,
                   profit_50_hit=1)
    p = signal_share_payload(sig, "RoyalBot", 555)
    assert p["pips"] == pytest.approx(50.0)
    assert p["exit_price"] == pytest.approx(3576.60)
    assert p["tp_label"] == "LOCKED +5$"


def test_share_payload_running_uses_true_floating():
    sig = _sig_row(2, live_px=3570.0)          # SELL 3581.60 → +116 pips floating
    p = signal_share_payload(sig, "RoyalBot", 555)
    assert p["pips"] == pytest.approx(116.0)
    assert p["exit_price"] == pytest.approx(3570.0)


def test_share_payload_sl_uses_the_stop():
    sig = _sig_row(3, status="SL_HIT", closed_at="2026-11-27T09:00:00+00:00",
                   pips_result=-113.1)
    p = signal_share_payload(sig, "RoyalBot", 555)
    assert p["pips"] == pytest.approx(-113.1)
    assert p["exit_price"] == pytest.approx(3592.91)


def test_share_payload_tp_uses_the_banked_level():
    sig = _sig_row(4, status="TP2_HIT", closed_at="2026-11-27T09:00:00+00:00",
                   pips_result=339.4, hit_tp=2)
    p = signal_share_payload(sig, "RoyalBot", 555)
    assert p["pips"] == pytest.approx(339.4)
    assert p["exit_price"] == pytest.approx(3547.66)


# ---------------------------------------------------------------------------
# ③ the royal opposite-trade alert
# ---------------------------------------------------------------------------

def _journal_take(uid: int, sid: int, symbol: str, direction: str):
    with db.conn() as c:
        c.execute("""INSERT OR IGNORE INTO trade_journal(user_id,signal_id,symbol,direction,
                     action,lots,risk_usd,status,created_at)
                     VALUES(?,?,?,?,?,?,?,?,?)""",
                  (uid, sid, symbol, direction, "TAKEN", 0.05, 50.0, "OPEN", config.now()))


def test_opposite_conflict_from_journal_and_demo():
    from fysb.services import user_trade
    _journal_take(771, 21, "XAUUSD", "BUY")            # holds BUY
    assert user_trade.opposite_holding_conflicts(771, "XAUUSD", "SELL")  # new SELL → conflict
    assert user_trade.opposite_holding_conflicts(771, "XAUUSD", "BUY") == []  # same dir → none


def test_opposite_conflict_closed_journal_is_not_a_holding():
    from fysb.services import user_trade
    _journal_take(772, 22, "XAUUSD", "BUY")
    with db.conn() as c:
        c.execute("UPDATE trade_journal SET status='CLOSED' WHERE user_id=772")
    assert user_trade.opposite_holding_conflicts(772, "XAUUSD", "SELL") == []


def test_opposite_conflict_from_open_demo_position():
    from fysb.services import user_trade
    from fysb.engines import demo as demo_engine
    demo_engine.set_balance(773, 10000.0)
    with patch.object(demo_engine.market_data, "fetch_price",
                      return_value={"price": 2600.0, "fetched_at": config.now(), "source": "LIVE"}):
        demo_engine.open_position(773, "XAUUSD", "BUY", 0.02, sl=2580.0, tp=2650.0)
    conflicts = user_trade.opposite_holding_conflicts(773, "XAUUSD", "SELL")
    assert conflicts and conflicts[0]["source"] == "DEMO" and conflicts[0]["direction"] == "BUY"


def test_opposite_alert_toggle_silences_but_stays_luxurious():
    from fysb.services import user_trade
    from fysb.engines import demo as demo_engine
    _journal_take(774, 24, "XAUUSD", "BUY")
    sig = _sig_row(24, direction="SELL")
    assert user_trade.opposite_alerts_for_delivery(sig, [774])       # default ON
    demo_engine.set_opposite_alert(774, False)
    assert user_trade.opposite_alerts_for_delivery(sig, [774]) == []
    demo_engine.set_opposite_alert(774, True)
    notes = user_trade.opposite_alerts_for_delivery(sig, [774])
    txt = notes[0][1]
    assert "تنبيه الصفقة المعاكسة الملكي" in txt
    assert "المعاكس تماماً" in txt and "XAUUSD" in txt
    assert "إعدادات التجريبي" in txt                                  # honest escape hatch


# ---------------------------------------------------------------------------
# ④ the near-lock whisper (+$4, once per lifetime)
# ---------------------------------------------------------------------------

def _walk_bars(t0, acts):
    from datetime import datetime as _dt, timedelta as _td
    out, t = [], _dt.fromisoformat(t0)
    for o, h, l, c in acts:
        out.append({"datetime": t.strftime("%Y-%m-%d %H:%M:%S"),
                    "open": o, "high": h, "low": l, "close": c, "volume": 1000})
        t += _td(minutes=15)
    return out


def test_near_whisper_fires_once_then_silences():
    sig = _sig_row(31, direction="BUY", entry=3600.0, stop=3595.0,
                   tp1=3610.0, tp2=3620.0, tp3=3635.0, status="RUNNING",
                   accounting_model="PARTIAL_50_25_25")
    bars = _walk_bars(T0, [(3600.0, 3604.7, 3599.5, 3604.0)])    # +47 pips = $4.70
    lock_info: dict = {}
    res = tracker._evaluate_signal(sig, bars, cutoff=T0, lock_out=lock_info)
    assert res is None and lock_info.get("near") is True
    notes: list = []
    tracker._handle_lock_arm(sig, lock_info, None, {"values": bars}, 3604.0, notes)
    assert sig["near_50_at"]
    assert any("من التأمين" in txt for _, txt in notes)
    # a second documented touch in the $4 band never repeats
    sig2 = dict(sig)
    lock2: dict = {}
    tracker._evaluate_signal(sig2, _walk_bars(T0, [(3604.0, 3604.8, 3599.5, 3604.4)]),
                             cutoff=T0, lock_out=lock2)
    notes2: list = []
    tracker._handle_lock_arm(sig2, lock2, None, {"values": []}, 3604.0, notes2)
    assert not lock2.get("near") and not notes2


def test_full_lock_never_whispers():
    sig = _sig_row(32, direction="BUY", entry=3600.0, stop=3595.0,
                   tp1=3610.0, tp2=3620.0, tp3=3635.0, status="RUNNING",
                   accounting_model="PARTIAL_50_25_25")
    lock_info: dict = {}
    tracker._evaluate_signal(sig, _walk_bars(T0, [(3600.0, 3606.0, 3599.5, 3605.5)]),
                             cutoff=T0, lock_out=lock_info)
    assert lock_info.get("armed") is True and "near" not in lock_info


# ---------------------------------------------------------------------------
# ⑤ the board, the personal digest, the absence ledger
# ---------------------------------------------------------------------------

def _insert_db_sig(sid: int, sig: dict):
    with db.conn() as c:
        c.execute("DELETE FROM signals WHERE id=?", (sid,))
        c.execute("""INSERT INTO signals(id,symbol,direction,timeframe,entry,stop,tp1,tp2,tp3,
                     targets,rr,score,strategy,reason,status,source,created_by,created_at,
                     hit_tp,closed_at,pips_result,locked_sl,locked_pips,profit_50_hit,
                     near_50_at,live_px,private_user_id,accounting_model)
                     VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                  (sid, sig["symbol"], sig["direction"], sig["timeframe"], sig["entry"],
                   sig["stop"], sig["tp1"], sig["tp2"], sig["tp3"], "", sig["rr"],
                   sig["score"], sig["strategy"], "x", sig["status"], "AUTO", 0,
                   config.now(), 0, sig.get("closed_at") or "",
                   float(sig.get("pips_result") or 0.0), float(sig.get("locked_sl") or 0.0),
                   float(sig.get("locked_pips") or 0.0), int(sig.get("profit_50_hit") or 0),
                   str(sig.get("near_50_at") or ""), float(sig.get("live_px") or 0.0),
                   0, sig.get("accounting_model", "LEGACY_LEVEL")))


def test_royal_locks_board_shows_live_floors():
    from fysb.handlers.admin_trading import royal_locks_board
    sig = _sig_row(41, status="RUNNING", profit_50_hit=1, locked_sl=3576.60,
                   locked_pips=50.0, profit_50_at="2026-11-27T08:30:00+00:00",
                   live_px=3570.0)
    _insert_db_sig(41, sig)
    body = royal_locks_board()
    assert "القفلات" in body and "3,576.60" in body
    assert "مؤمّنة الآن: <b>1</b>" in body


def test_personal_royal_digest_is_honest_and_empty_free():
    from fysb.services.digest import personal_royal_digest
    from datetime import datetime as _dt
    lock = _sig_row(51, status="LOCKED_50", closed_at=config.now(), pips_result=50.0,
                    locked_sl=3576.60, locked_pips=50.0, profit_50_hit=1)
    stop = _sig_row(52, status="SL_HIT", closed_at=config.now(), pips_result=-113.1)
    _insert_db_sig(51, lock)
    _insert_db_sig(52, stop)
    for sid in (51, 52):
        with db.conn() as c:
            c.execute("INSERT INTO signal_deliveries(signal_id,user_id,delivered_at) VALUES(?,?,?)",
                      (sid, 880, config.now()))
    txt = personal_royal_digest(880, 1)
    assert txt and "ملخصك الشخصي" in txt
    assert "صفقاتك: <b>2</b>" in txt and "قفلات ملكية: <b>1</b>" in txt
    assert "+50.0 pips" in txt and "-113.1 pips" in txt
    assert personal_royal_digest(881, 1) is None            # zero activity → silence


def test_absence_ledger_reports_the_gap_honestly():
    from fysb.services import absence
    old = (datetime.now(timezone.utc) - timedelta(hours=2)).isoformat()
    db.set_setting(absence.HEARTBEAT_KEY, old)
    lost = _sig_row(61, status="LOCKED_50", closed_at=(datetime.now(timezone.utc)
                                                        - timedelta(minutes=90)).isoformat(),
                    pips_result=50.0, profit_50_hit=1)
    _insert_db_sig(61, lost)
    report, gap = absence.build_absence_report()
    assert report and "سجل الغياب الصادق" in report and "غِبتُ" in report
    assert "XAUUSD" in report and gap >= absence.ABSENCE_MIN_GAP_SECONDS
    # fresh heartbeat → transparent restart, no story
    absence.stamp_cycle()
    assert absence.build_absence_report()[0] is None
    # first boot ever (no stamp at all) → no fabricated story either
    db.set_setting(absence.HEARTBEAT_KEY, "")
    assert absence.build_absence_report()[0] is None
