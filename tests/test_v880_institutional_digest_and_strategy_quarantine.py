"""v8.8.0 — Institutional Verified Performance Digest & Strategy Auto-Quarantine Tests."""
from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, MagicMock

import pytest

from fysb import config, db
from fysb.engines import intelligence, quality_gate
from fysb.engines.strategies.base import StrategyResult
from fysb.services import digest as digest_svc


def _fresh_db(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "DB_FILE", str(tmp_path / "v880.db"))
    monkeypatch.setattr(config, "ADMIN_ID", 999999)
    db.init_db()


def _ins_sig(
    *,
    symbol: str = "XAUUSD",
    strategy: str = "ICHIMOKU",
    direction: str = "BUY",
    entry: float = 3500.0,
    stop: float = 3490.0,
    tp1: float = 3520.0,
    tp2: float = 3530.0,
    tp3: float = 3545.0,
    status: str = "TP2_HIT",
    pips_result: float = 300.0,
    source: str = "AUTO",
):
    now = config.now()
    closed = now if status in ("TP1_HIT", "TP2_HIT", "TP3_HIT", "TP1_BE", "TP2_BE", "SL_HIT", "CANCELLED") else ""
    with db.conn() as c:
        c.execute(
            """INSERT INTO signals(symbol,timeframe,direction,entry,stop,tp1,tp2,tp3,
               targets,rr,score,strategy,reason,status,source,pips_result,created_at,closed_at)
               VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            (
                symbol, "M15", direction, entry, stop, tp1, tp2, tp3,
                f"{tp1},{tp2},{tp3}", 2.0, 84, strategy, "test", status, source,
                pips_result, now, closed,
            ),
        )


def test_digest_excludes_private_signal_now_and_zero_ghost_rows_and_computes_true_r(tmp_path, monkeypatch):
    _fresh_db(tmp_path, monkeypatch)

    # Official winning trades across Gold & EURUSD (including TP1_BE!)
    _ins_sig(symbol="XAUUSD", strategy="ICHIMOKU", entry=3500, stop=3490, status="TP3_HIT", pips_result=450.0, source="AUTO")  # +4.5R
    _ins_sig(symbol="XAUUSD", strategy="CRT", entry=3500, stop=3490, status="TP1_BE", pips_result=200.0, source="AUTO")        # +2.0R
    _ins_sig(symbol="EURUSD", strategy="PRICE_ACTION", entry=1.1000, stop=1.0980, tp1=1.1040, status="TP2_HIT", pips_result=60.0, source="AUTO")  # +3.0R

    # One BTCUSD SL (-1000 pips, but ONLY -1.0R!)
    _ins_sig(symbol="BTCUSD", strategy="SMC", entry=90000, stop=89000, tp1=92000, status="SL_HIT", pips_result=-1000.0, source="AUTO")  # -1.0R

    # Unfilled / Open ghost signals that MUST NOT appear as "0% · +0.0p" in leaderboards!
    _ins_sig(symbol="GBPJPY", strategy="FIBONACCI", status="ACTIVE", pips_result=0.0, source="AUTO")
    _ins_sig(symbol="USDJPY", strategy="WYCKOFF", status="CANCELLED", pips_result=0.0, source="AUTO")

    # Private user on-demand SIGNAL_NOW losses MUST NOT contaminate official track record!
    _ins_sig(symbol="XAUUSD", strategy="ICT", status="SL_HIT", pips_result=-500.0, source="SIGNAL_NOW")
    _ins_sig(symbol="ETHUSD", strategy="ICT", status="SL_HIT", pips_result=-300.0, source="SIGNAL_NOW")

    d, cap, png = digest_svc.build_digest_bundle(days=7)
    assert d["private_excluded"] == 2
    assert d["total"] == 6       # 4 decided + 1 open + 1 cancelled (excludes 2 SIGNAL_NOW!)
    assert d["decided"] == 4
    assert d["wins"] == 3        # TP3_HIT + TP1_BE + TP2_HIT
    assert d["losses"] == 1
    assert d["open"] == 1
    assert d["cancelled"] == 1
    assert d["win_rate"] == 75.0
    # Even though raw net pips is negative (-290p because of 1 BTCUSD SL of -1000p),
    # true institutional Net R is +8.5R and Profit Factor is 9.50!
    assert d["net_r"] == pytest.approx(8.5, abs=0.05)
    assert d["profit_factor"] == pytest.approx(9.5, abs=0.05)
    assert d["best_strategy"] == "ICHIMOKU"

    # ZERO ghost 0% strategies or symbols in rankings!
    strat_names = [s["strategy"] for s in d["top_strategies"]]
    sym_names = [s["symbol"] for s in d["top_symbols"]]
    assert "FIBONACCI" not in strat_names
    assert "WYCKOFF" not in strat_names
    assert "GBPJPY" not in sym_names
    assert "USDJPY" not in sym_names
    assert "PF 9.50" in cap and "+8.50R" in cap
    assert png is not None and png[:8] == b"\x89PNG\r\n\x1a\n"


def test_auto_scheduled_channel_broadcast_quality_guard(tmp_path, monkeypatch):
    _fresh_db(tmp_path, monkeypatch)
    with db.conn() as c:
        now = config.now()
        c.execute(
            "INSERT INTO broadcast_channels(chat_id, title, kind, enabled, created_at, updated_at) VALUES(?,?,?,?,?,?)",
            ("-1001234567890", "Royal VIP", "channel", 1, now, now),
        )

    # Seed a drawdown week (1 win, 4 losses -> Net R = -2.0R)
    _ins_sig(strategy="BAD_STRAT", status="TP1_HIT", pips_result=200.0)
    for _ in range(4):
        _ins_sig(strategy="BAD_STRAT", status="SL_HIT", pips_result=-100.0)

    bot = MagicMock()
    bot.send_photo = AsyncMock()
    bot.send_message = AsyncMock()

    # Scheduled auto-digest MUST deliver to Admin ONLY and hold public channel broadcast!
    res = asyncio.run(
        digest_svc.publish_digest(bot, days=7, include_channels=True, notify_admin=True, auto_scheduled=True)
    )
    assert res["ok"] is True
    assert res["channels_held"] is False
    assert res["channels_sent"] == 1
    assert res["sent"] == 2  # Admin AND channel receive the losing period honestly!

    # Now add 4 big winners -> Net R becomes strongly positive -> scheduled broadcast succeeds!
    for _ in range(4):
        _ins_sig(strategy="ICHIMOKU", status="TP2_HIT", pips_result=300.0)
    res2 = asyncio.run(
        digest_svc.publish_digest(bot, days=7, include_channels=True, notify_admin=True, auto_scheduled=True)
    )
    assert res2["channels_held"] is False
    assert res2["channels_sent"] == 1
    assert res2["sent"] == 2  # Admin + Channel!


def test_institutional_strategy_auto_quarantine_blocks_bleeding_and_keeps_winners(tmp_path, monkeypatch):
    _fresh_db(tmp_path, monkeypatch)

    # Bleeding strategy: 1 win (+2R) and 6 losses (-6R) -> WR 14.3%, PF 0.33 -> MUST QUARANTINE!
    _ins_sig(strategy="BLEEDER", status="TP1_HIT", pips_result=200.0)
    for _ in range(6):
        _ins_sig(strategy="BLEEDER", status="SL_HIT", pips_result=-100.0)

    # Winning strategy: 3 wins (+9R) and 3 losses (-3R) -> WR 50%, PF 3.0 -> MUST STAY ACTIVE!
    for _ in range(3):
        _ins_sig(strategy="ICHIMOKU", status="TP2_HIT", pips_result=300.0)
        _ins_sig(strategy="ICHIMOKU", status="SL_HIT", pips_result=-100.0)

    q_bad = intelligence.strategy_quarantine_status("BLEEDER", "XAUUSD")
    q_good = intelligence.strategy_quarantine_status("ICHIMOKU", "XAUUSD")
    assert q_bad["quarantined"] is True
    assert q_bad["win_rate"] < 35.0 and q_bad["pf"] < 0.85
    assert q_good["quarantined"] is False

    q_list = intelligence.list_quarantined_strategies(14)
    assert [x["strategy"] for x in q_list] == ["BLEEDER"]

    # Admin diagnostic caption transparently includes Auto-Quarantine section!
    d = digest_svc.build_digest_data(days=7)
    admin_cap = digest_svc.render_digest_caption(d, admin_diagnostics=True)
    assert "Auto-Quarantine" in admin_cap and "BLEEDER" in admin_cap


def test_quality_gate_blocks_htf_liquidity_wall_before_tp1():
    from datetime import datetime, timedelta, timezone
    t0 = datetime(2026, 11, 20, 10, 0, tzinfo=timezone.utc)
    bars = []
    px = 3500.0
    for i in range(160):
        op, cl = px, px + 1.0
        dt = (t0 + timedelta(minutes=i * 15)).strftime("%Y-%m-%d %H:%M:%S")
        bars.append({"datetime": dt, "open": op, "high": cl + 0.5, "low": op - 0.5, "close": cl, "volume": 100.0})
        px = cl
    htf = quality_gate.resample_htf(bars, 4)

    # Setup with clear_path_to_2r=False and mid score 78 -> MUST BE BLOCKED!
    trapped = StrategyResult("ICT", "BUY", 3650, 3635, 3680, 3695, 3718, 2.0, 78, [], {"clear_path_to_2r": False})
    ok, reason = quality_gate.evaluate_quality_gate("XAUUSD", "M15", bars, htf, trapped, min_score=72)
    assert ok is False
    assert "عائق سيولة" in reason

    # Extraordinary A++ score (86 >= 72+12) still allowed
    elite = StrategyResult("ICT", "BUY", 3650, 3635, 3680, 3695, 3718, 2.0, 86, [], {"clear_path_to_2r": False})
    ok2, _ = quality_gate.evaluate_quality_gate("XAUUSD", "M15", bars, htf, elite, min_score=72)
    assert ok2 is True
