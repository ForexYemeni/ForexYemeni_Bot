"""Offline characterization probes for AUDIT_2026-11-26_AR.md.

Run from the repo root: .venv/bin/python docs/audits/reproduce_2026_11_26.py
Updated after the strategy fixes: FIXED probes assert desired behavior;
remaining CONFIRMED probes characterize unresolved defects/limitations.
The original baseline output is preserved in reproduction_output_2026_11_26.txt.
No Telegram, price-provider or broker requests; tests.helpers isolates config/DB.
"""
from datetime import datetime, timedelta, timezone
from pathlib import Path
import sys
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from tests.helpers import uptrend_candles, make_candles  # isolate BEFORE config
from fysb import config, db
from fysb.engines import tracker, signal_engine, quality_gate, backtest
from fysb.engines.strategies.base import Ctx, StrategyResult, cap_stop, normalized_score
from fysb.engines.strategies import wyckoff, silver_bullet
from fysb.services import analytics, strategy_stats, true_analysis, digest


def observed(label, detail, status="CONFIRMED"):
    print(f"{status} | {label} | {detail}")


def bar(ts, high, low, close, open_=101):
    return dict(datetime=f"2026-11-26 {ts}", open=open_, high=high, low=low, close=close)


def main():
    db.init_db()
    sig = dict(symbol="XAUUSD", direction="BUY", entry=100., stop=90.,
               tp1=120., tp2=130., tp3=145., hit_tp=0,
               created_at="2026-11-26 08:00:00", filled_at="2026-11-26 08:00:00")
    walk = [bar("08:00:00", 121, 101, 120),
            bar("08:05:00", 106, 99, 101),
            bar("08:10:00", 146, 101, 145)]
    batch = tracker._evaluate_signal(sig, walk)
    first = tracker._evaluate_signal(sig, walk[:1])
    split = tracker._evaluate_signal({**sig, "hit_tp": 1}, walk[:2])
    assert (batch, first, split) == ("TP3_HIT", "TP1_HIT", "TP1_BE")
    observed("tracker polling dependence", f"batch={batch}; after first TP1, next pass={split}")

    with db.conn() as c:
        c.execute("UPDATE strategy_catalog SET enabled=0")
    allowed = signal_engine._allowed_strategies("XAUUSD", "M15")
    assert allowed == ["PRICE_ACTION"]
    observed("disabled catalog fallback", repr(allowed))

    candles = uptrend_candles(101)
    a = quality_gate.resample_htf(candles[:100], 4)
    b = quality_gate.resample_htf(candles, 4)
    assert a[0]["datetime"] != b[0]["datetime"]
    observed("HTF historical bins shift", f"first group ends {a[0]['datetime']} -> {b[0]['datetime']}")

    with db.conn() as c:
        c.execute("DELETE FROM signals")
        cur = c.execute("""INSERT INTO signals(symbol,direction,entry,stop,tp1,tp2,tp3,rr,
            timeframe,strategy,score,status,hit_tp,pips_result,source,created_at,closed_at,private_user_id)
            VALUES('XAUUSD','BUY',100,90,120,130,145,2,'M15','ICT',80,
                   'TP1_HIT',1,200,'TRUE_ANALYSIS_CONFIRMED',?,'',42)""", (config.now(),))
        sid = cur.lastrowid
        c.execute("INSERT INTO signal_deliveries(signal_id,user_id,delivered_at,chat_id,message_id) VALUES(?,42,?,'42',0)", (sid, config.now()))
    strategy_stats.invalidate_leaderboard_cache()
    board = strategy_stats.leaderboard()
    assert board[0]["n"] == 1 and board[0]["wins"] == 1
    observed("leaderboard counts open private TP1", repr(board[0]))
    with patch("fysb.engines.intelligence.list_quarantined_strategies", return_value=[]):
        data = digest.build_digest_data()
    assert data["wins"] == 1 and data["decided"] == 1
    observed("public digest includes private open runner", f"wins={data['wins']}; decided={data['decided']}")
    with patch.object(signal_engine, "admin_accepts_market", return_value=True):
        targets = tracker._alert_targets({**sig, "id": sid, "source": "TRUE_ANALYSIS_CONFIRMED"}, "probe")
    assert any(str(t[0]) == str(config.ADMIN_ID) for t in targets)
    observed("private radar admin alert", f"targets={[t[0] for t in targets]}")

    stop = cap_stop(100, 90, "BUY", 1)
    assert stop == 90
    observed("structural stop preserved", f"raw=90; preserved={stop}", "FIXED")
    assert normalized_score(0, 100) == 42
    observed("score is checklist transformation", f"zero earned -> {normalized_score(0, 100)}")

    plan = true_analysis._derive_real_trade_plan(symbol="XAUUSD", row=None,
        direction="BUY", entry_px=100, invalidation_px=90, atr=1)
    risk = plan["entry"] - plan["sl"]
    rr1 = (plan["tp1"] - plan["entry"]) / risk
    assert plan["rr"] == 2.6 and abs(rr1 - 1.5) < .01
    observed("radar RR denotes TP2", f"reported RR={plan['rr']}; TP1 RR={rr1:.2f}")

    # Full analytics path, mock only data transport and unrelated presentation spec.
    # Distinct offsets identify the actual candle series passed into Ctx.
    def fake_fetch(symbol, tf, size=150):
        cs = uptrend_candles(80, base={"M5": 100, "M15": 200, "M30": 300, "H1": 400}.get(tf, 500))
        return {"values": cs, "last_candle_iso": cs[-1]["datetime"], "source": "AUDIT"}, None
    captured = []
    def capture(ctx, allowed):
        captured.append((ctx, allowed))
        return []
    plan_row = dict(markets="XAUUSD", timeframes="M5,M15,M30,H1", strategies="ICT")
    with patch.object(analytics, "_fetch", side_effect=fake_fetch), \
         patch.object(analytics, "analytics_enabled", return_value=True), \
         patch.object(analytics, "quota_state", return_value=dict(quota=10, used=0, remaining=10)), \
         patch("fysb.services.subscriptions.plan_for", return_value=plan_row), \
         patch.object(analytics, "evaluate_all", side_effect=capture), \
         patch.object(analytics, "_build_real_confirmation_spec", return_value={}), \
         patch.object(analytics.market_data, "is_stale", return_value=False), \
         patch.object(analytics.market_data, "market_state", return_value=True):
        res = analytics.run_analysis(42, "XAUUSD", "ALL", strategy="ALL")
    assert res["ok"], res
    ctx, used_codes = captured[0]
    assert ctx.timeframe == "H1" and ctx.candles[0]["open"] == 100
    assert "WYCKOFF" in used_codes  # despite plan containing ICT only
    observed("analytics timeframe mix", f"Ctx.timeframe={ctx.timeframe}; candles=M5; chart={res['analysis']['chart_data']['tf']}")
    observed("analytics ALL bypasses plan/catalog", f"plan=ICT; disabled catalog; evaluated={used_codes}")

    # Regression: reclaim is now mandatory; use fully valid OHLC input.
    vals = []
    for i in range(44):
        o, cl = (100.3, 101.6) if i % 2 == 0 else (101.7, 100.4)
        vals.append((o, max(o, cl) + .35, min(o, cl) - .35, cl))
    cs = make_candles(vals)
    cs[-1].update(open=100.5, high=104, low=98.8, close=103.8, volume=100)
    result = wyckoff.evaluate(Ctx("XAUUSD", "M15", cs, uptrend_candles(), kill_zone="NY"))
    assert result is None
    observed("Wyckoff requires range reclaim", "outside close rejected", "FIXED")

    # Regression: a chronologically valid but PRE-window gap must be rejected.
    cs = uptrend_candles(80)
    end = datetime(2026, 11, 26, 15, 0, tzinfo=timezone.utc)  # 10:00 NY
    for i, c in enumerate(cs):
        c["datetime"] = (end-timedelta(minutes=15*(len(cs)-i-1))).isoformat()
    close = cs[-1]["close"]
    gap = dict(kind="bullish", lo=close-1, hi=close-.5, index=2)
    with patch.object(silver_bullet, "fair_value_gaps", return_value=[gap]):
        result = silver_bullet.evaluate(Ctx("XAUUSD", "M15", cs, uptrend_candles()))
    assert result is None
    observed("Silver Bullet gap timestamp checked", "pre-window mocked gap rejected", "FIXED")

    cand = StrategyResult("ICT", "BUY", 100, 90, 120, 130, 145, 2, 80)
    # Entire bar ABOVE entry but opening within +0.15R is treated as a fill.
    trade = backtest._simulate_trade("XAUUSD", cand, [bar("09:00:00", 105, 101, 104)], 0, spread_pips=0)
    assert trade.outcome != "UNFILLED"
    observed("backtest accepts untouched entry", f"entry=100; candle low=101; outcome={trade.outcome}")
    print("DONE: 11 remaining observations + 3 FIXED regressions; no production files or network used.")


if __name__ == "__main__":
    main()
