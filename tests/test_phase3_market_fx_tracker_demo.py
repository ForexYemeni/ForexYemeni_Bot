"""Phase 3 (v6.15.0) regression & contract suite:
  1. Market data: Bybit v5 ms timestamp parsing + retCode validation,
     Binance -> Bybit PAXG gold failover, provider cooldown clear on recovery,
     expanded FX crosses (EURJPY/GBPJPY/...), quote TTL cache, custom symbol
     TwelveData fallback.
  2. FX quote-currency math: USDJPY / USDCAD / USDCHF position_size, pnl,
     notional_usd, usd_move, and JPY cross specs (pip=0.01, digits=3).
  3. Tracker: conservative SL-first rule on gap candles spanning SL+TP,
     adaptive tracking window (>14h -> 15min, >28h -> 1h), USDJPY alert USD
     conversion, expiry preserving banked TP1_HIT/TP2_HIT wins.
  4. Demo & user_trade: USDJPY notional cap & real USD P&L/personal block,
     real risk_pct enforcement in open_position, duplicate signal_id guard,
     HALF_TP1 anchored to signal's actual TP1 (not TP3), full-history win_rate.
"""
import unittest
from datetime import datetime, timedelta, timezone
from unittest.mock import patch

import tests.helpers  # noqa: F401
from fysb import config, db, instruments
from fysb.engines import demo as demo_engine
from fysb.engines import market_data as md
from fysb.engines import tracker
from fysb.services import user_trade, users as users_service


def _fake_user(uid: int):
    return type("U", (), {"id": uid, "username": f"u{uid}", "full_name": f"User {uid}"})()



def _stale_dt(i):
    """Near-stale candle stamps (last candle ~60min ago) — stale vs the M15
    limit (45min) yet under the v8.13.6 ancient threshold (3x limit), so the
    recovery-credit contract still applies verbatim."""
    from datetime import datetime, timedelta
    end = datetime.utcnow() - timedelta(minutes=60)
    m = 60 - (15 * i) % 60
    t = end - timedelta(minutes=15 * i)
    return t.strftime("%Y-%m-%d %H:%M:00")


class TestPhase3MarketData(unittest.TestCase):
    def setUp(self):
        from fysb.engines import provider_health
        provider_health.reset()
        md._cache.clear()
        md._fail_streak.clear()

    def test_bybit_v5_millisecond_timestamps_parse_cleanly(self):
        # Bybit v5 kline returns startTime in ms, newest-first.
        base_ms = 1700000000000
        rows = [
            [str(base_ms + i * 900_000), "50000", "50100", "49900", "50050", "10", "500000"]
            for i in reversed(range(45))
        ]
        payload = {"retCode": 0, "retMsg": "OK", "result": {"list": rows}}
        with patch.object(md, "_http_json", return_value=payload):
            candles = md._fetch_bybit_candles("BTCUSDT", "15min", 45)
        self.assertEqual(len(candles), 45)
        # Oldest-first, valid UTC timestamp (year 2023, NOT year 55839 overflow!)
        self.assertTrue(candles[0]["datetime"].startswith("2023-11-14"))
        self.assertLess(candles[0]["datetime"], candles[-1]["datetime"])

    def test_bybit_nonzero_retcode_raises(self):
        with patch.object(md, "_http_json", return_value={"retCode": 10001, "retMsg": "Invalid symbol"}):
            with self.assertRaises(RuntimeError):
                md._fetch_bybit_candles("BADUSDT", "15min", 50)

    def test_binance_error_dict_raises(self):
        with patch.object(md, "_http_json", return_value={"code": -1121, "msg": "Invalid symbol."}):
            with self.assertRaises(RuntimeError):
                md._fetch_binance_candles("BADUSDT", "15min", 50)

    def test_venue_gold_fails_over_from_binance_to_bybit_paxg(self):
        bybit_candles = [
            {"datetime": _stale_dt(i),
             "open": 3550.0, "high": 3555.0, "low": 3545.0, "close": 3552.0}
            for i in range(45)
        ]
        # v8.14.2: mock the live-spot crosscheck consistently with the fixture
        # (3552 − 12 = 3540) — in CI the REAL gold-api answers and would
        # (rightly) refuse an inconsistent venue payload as diverged.
        md._spot_crosscheck_cache.clear()
        with patch.object(md, "_fetch_yahoo_candles", side_effect=RuntimeError("offline")), \
             patch.object(md, "_fetch_binance_candles", side_effect=RuntimeError("geo-blocked")), \
             patch.object(md, "_fetch_bybit_candles", return_value=bybit_candles), \
             patch.object(md, "_gold_offset_now", return_value=-12.0), \
             patch.object(md, "_fetch_goldapi_price", return_value={"price": 3540.0}):
            res = md.fetch_candles("XAUUSD", "15min", "", 45, use_cache=False)
        self.assertEqual(res["source"], "LIVE")
        self.assertAlmostEqual(res["values"][-1]["close"], 3540.0, places=4)

    def test_provider_cooldown_clears_on_recovery(self):
        md._mark_failure("binance", "BTCUSD", "15min")
        self.assertIn("binance|BTCUSD|15min", md._fail_streak)
        # Expire cooldown window so binance is tried again
        md._fail_streak["binance|BTCUSD|15min"] = (0.0, 2)
        candles = [
            {"datetime": _stale_dt(i),
             "open": 90000.0, "high": 90100.0, "low": 89900.0, "close": 90050.0}
            for i in range(45)
        ]
        with patch.object(md, "_fetch_binance_candles", return_value=candles):
            md.fetch_candles("BTCUSD", "15min", "", 45, use_cache=False)
        self.assertNotIn("binance|BTCUSD|15min", md._fail_streak)

    def test_fx_crosses_routed_to_yahoo(self):
        for pair in ("EURJPY", "GBPJPY", "EURGBP", "AUDJPY", "GBPAUD"):
            self.assertEqual(md._class_of(pair), "fx")
            self.assertIn(pair, md.YAHOO_FX_MAP)

    def test_quote_ttl_cache_prevents_burst_hits(self):
        calls = {"n": 0}

        def fake_spot(_ticker):
            calls["n"] += 1
            return {"price": 3540.0, "updated": "now"}

        with patch.object(md, "_fetch_goldapi_price", side_effect=fake_spot):
            r1 = md.fetch_price("XAUUSD", "")
            r2 = md.fetch_price("XAU/USD", "")
        self.assertEqual(calls["n"], 1)
        self.assertEqual(r1["price"], 3540.0)
        self.assertEqual(r2["symbol"], "XAU/USD")


class TestPhase3FXQuoteCurrencyMath(unittest.TestCase):
    def test_usdjpy_position_size_and_pnl_in_usd(self):
        # Balance $10,000, risk 1% = $100, entry 150.00, SL 149.50 (50 pips = 0.50 JPY)
        # Risk per 1 lot = (0.50 * 100,000) / 150.00 = $333.33 -> 0.30 lots
        lots = instruments.position_size(10000.0, 1.0, 150.00, 149.50, "USDJPY")
        self.assertAlmostEqual(lots, 0.30, places=2)
        # Buy 0.10 lot USDJPY 150.00 -> 151.00 = +10,000 JPY / 151.00 = +$66.23 USD (NOT +$10,000!)
        profit_usd = instruments.pnl("USDJPY", "BUY", 0.10, 150.00, 151.00)
        self.assertAlmostEqual(profit_usd, 10000.0 / 151.00, places=2)
        # Notional of 0.10 lot USDJPY = $10,000 USD (NOT $1,500,000!)
        self.assertAlmostEqual(instruments.notional_usd("USDJPY", 0.10, 150.00), 10000.0, places=2)

    def test_usdcad_and_usdchf_pnl_in_usd(self):
        # Buy 1.00 lot USDCAD 1.3800 -> 1.3900 (+100 pips = +1000 CAD / 1.39 = +$719.42 USD)
        p = instruments.pnl("USDCAD", "BUY", 1.0, 1.3800, 1.3900)
        self.assertAlmostEqual(p, 1000.0 / 1.3900, places=2)
        self.assertTrue(instruments.is_usd_base("USDCHF"))
        self.assertFalse(instruments.is_usd_base("EURUSD"))

    def test_jpy_crosses_use_jpy_pip_and_digits(self):
        for pair in ("EURJPY", "GBPJPY", "CADJPY", "SGDJPY"):
            sp = instruments.spec(pair)
            self.assertEqual(sp.pip_size, 0.01)
            self.assertEqual(sp.digits, 3)


class TestPhase3TrackerFixes(unittest.TestCase):
    def setUp(self):
        db.init_db()
        with db.conn() as c:
            c.execute("DELETE FROM signals")
            c.execute("DELETE FROM signal_deliveries")

    def test_gap_candle_spanning_sl_and_tp_is_conservative_sl_hit(self):
        sig = {"id": 1, "symbol": "XAUUSD", "direction": "BUY", "timeframe": "M15",
               "entry": 3500.0, "stop": 3490.0, "tp1": 3520.0, "tp2": 3530.0, "tp3": 3540.0,
               "status": "RUNNING", "hit_tp": 0, "filled_at": "2026-11-24 10:00:00",
               "created_at": "2026-11-24 10:00:00"}
        # Single wild candle touches BOTH SL (3485 <= 3490) AND TP1 (3525 >= 3520.2)
        gap_bar = {"datetime": "2026-11-24 10:05:00",
                   "open": 3500.0, "high": 3525.0, "low": 3485.0, "close": 3510.0}
        self.assertEqual(tracker._evaluate_signal(sig, [gap_bar]), "SL_HIT")

    def test_adaptive_tracking_intervals_cover_old_signals(self):
        now = datetime.now(timezone.utc)
        young = (now - timedelta(hours=2)).isoformat()
        medium = (now - timedelta(hours=20)).isoformat()
        old = (now - timedelta(hours=40)).isoformat()
        self.assertEqual(tracker._tracking_intervals_for(young)[0][0], "5min")
        self.assertEqual(tracker._tracking_intervals_for(medium)[0][0], "15min")
        self.assertEqual(tracker._tracking_intervals_for(old)[0][0], "1h")

    def test_expiry_preserves_banked_tp1_win_and_stops_tracking(self):
        old_iso = (datetime.now(timezone.utc) - timedelta(hours=80)).isoformat()
        with db.conn() as c:
            c.execute("""INSERT INTO signals(symbol,direction,timeframe,entry,stop,tp1,tp2,tp3,
                         targets,rr,score,strategy,reason,status,hit_tp,pips_result,source,created_by,
                         created_at,data_source,data_time,strategy_scores,expires_at,closed_at)
                         VALUES('XAUUSD','BUY','M15',3500,3490,3520,3530,3540,'t',2.0,90,'SMC','x',
                         'TP1_HIT',1,200.0,'AUTO',0,?,'LIVE','t','{}','','')""", (old_iso,))
            c.execute("""INSERT INTO signals(symbol,direction,timeframe,entry,stop,tp1,tp2,tp3,
                         targets,rr,score,strategy,reason,status,hit_tp,pips_result,source,created_by,
                         created_at,data_source,data_time,strategy_scores,expires_at,closed_at)
                         VALUES('XAUUSD','BUY','M15',3505,3490,3520,3530,3540,'t',2.0,90,'SMC','x',
                         'ACTIVE',0,0.0,'AUTO',0,?,'LIVE','t','{}','','')""", (old_iso,))
        expired_n = tracker.expire_stale_signals()
        self.assertEqual(expired_n, 1)  # only the ACTIVE unbanked signal became EXPIRED
        with db.conn() as c:
            rows = [dict(r) for r in c.execute("SELECT status, closed_at FROM signals ORDER BY id").fetchall()]
        self.assertEqual(rows[0]["status"], "TP1_HIT")
        self.assertTrue(rows[0]["closed_at"])  # closed so tracker won't keep polling it
        self.assertEqual(rows[1]["status"], "EXPIRED")


class TestPhase3DemoAndUserTrade(unittest.TestCase):
    uid = 777301

    def setUp(self):
        db.init_db()
        users_service.ensure_user(_fake_user(self.uid))
        demo_engine.reset_account(self.uid)
        with db.conn() as c:
            c.execute("UPDATE market_catalog SET enabled=1, verified=1 WHERE symbol IN ('XAUUSD','USDJPY')")

    def test_usdjpy_demo_open_succeeds_and_personal_block_uses_usd(self):
        demo_engine.set_balance(self.uid, 10000.0)
        demo_engine.set_risk_pct(self.uid, 1.0)
        # 0.30 lots USDJPY has $30,000 USD notional (well below $500,000 cap!)
        with patch.object(demo_engine.market_data, "fetch_price",
                          return_value={"price": 150.00, "fetched_at": config.now(), "source": "LIVE"}):
            res = demo_engine.open_position(self.uid, "USDJPY", "BUY", 0.30, sl=149.50, tp=151.00)
        self.assertTrue(res["ok"], res)
        sig = {"id": 99, "symbol": "USDJPY", "direction": "BUY", "timeframe": "M15",
               "entry": 150.00, "stop": 149.50, "tp1": 151.00, "tp2": 151.50, "tp3": 152.00}
        block = user_trade.personal_block(sig, self.uid)
        # True USD risk at 0.30 lots = ~$100 (NOT $15,000!)
        self.assertNotIn("15,000", block)
        self.assertIn("100.", block)

    def test_open_position_blocks_excessive_risk_pct(self):
        demo_engine.set_balance(self.uid, 1000.0)
        # 1.0 lot XAUUSD with $100 SL distance = $10,000 risk (1000% of balance!)
        with patch.object(demo_engine.market_data, "fetch_price",
                          return_value={"price": 3500.0, "fetched_at": config.now(), "source": "LIVE"}):
            res = demo_engine.open_position(self.uid, "XAUUSD", "BUY", 1.0, sl=3400.0, tp=3600.0)
        self.assertFalse(res["ok"])
        self.assertIn("risk per trade", res["error"])

    def test_open_from_signal_blocks_duplicate_signal_id(self):
        demo_engine.set_balance(self.uid, 10000.0)
        sig = {"id": 5001, "symbol": "XAUUSD", "direction": "BUY", "timeframe": "M15",
               "entry": 3500.0, "stop": 3480.0, "tp1": 3540.0, "tp2": 3560.0, "tp3": 3580.0}
        with patch.object(demo_engine.market_data, "fetch_price",
                          return_value={"price": 3500.0, "fetched_at": config.now(), "source": "LIVE"}):
            r1 = demo_engine.open_from_signal(self.uid, sig)
            r2 = demo_engine.open_from_signal(self.uid, sig)
        self.assertTrue(r1["ok"], r1)
        self.assertFalse(r2["ok"])
        self.assertIn("already opened", r2["error"])

    def test_half_tp1_triggers_against_signal_tp1_not_tp3(self):
        demo_engine.set_balance(self.uid, 10000.0)
        with db.conn() as c:
            cur = c.execute("""INSERT INTO signals(symbol,direction,timeframe,entry,stop,tp1,tp2,tp3,
                         targets,rr,score,strategy,reason,status,source,created_by,created_at,
                         data_source,data_time,strategy_scores,expires_at)
                         VALUES('XAUUSD','BUY','M15',3500,3480,3540,3580,3620,'t',2.0,90,'SMC','x',
                         'RUNNING','AUTO',0,?,'LIVE','t','{}','')""", (config.now(),))
            sid = cur.lastrowid
        sig = {"id": sid, "symbol": "XAUUSD", "direction": "BUY", "timeframe": "M15",
               "entry": 3500.0, "stop": 3480.0, "tp1": 3540.0, "tp2": 3580.0, "tp3": 3620.0}
        with patch.object(demo_engine.market_data, "fetch_price",
                          return_value={"price": 3500.0, "fetched_at": config.now(), "source": "LIVE"}):
            res = demo_engine.open_from_signal(self.uid, sig)
        self.assertTrue(res["ok"], res)
        # Halfway to TP1 (3500 -> 3540) is ~3521, far below halfway to TP3 (3560)!
        with patch.object(demo_engine.market_data, "fetch_price",
                          return_value={"price": 3522.0, "fetched_at": config.now(), "source": "LIVE"}):
            events = demo_engine.sync_positions_with_real_prices(self.uid)
        self.assertTrue(any(e["kind"] == "HALF_TP1" for e in events), events)

    def test_account_overview_win_rate_uses_all_closed_trades(self):
        acc = demo_engine.get_or_create_account(self.uid)
        with db.conn() as c:
            # 10 old losses + 10 recent wins -> true win rate is 50%, NOT 100%!
            for i in range(10):
                c.execute("""INSERT INTO demo_positions(account_id,user_id,symbol,direction,lots,
                             initial_lots,entry,sl,tp,status,pnl,opened_at,closed_at)
                             VALUES(?,?,'XAUUSD','BUY',0.01,0.01,3500,3490,3520,'LOSS',-10.0,?,?)""",
                          (acc["id"], self.uid, "2026-11-01T00:00:00+00:00", f"2026-11-01T01:{i:02d}:00+00:00"))
            for i in range(10):
                c.execute("""INSERT INTO demo_positions(account_id,user_id,symbol,direction,lots,
                             initial_lots,entry,sl,tp,status,pnl,opened_at,closed_at)
                             VALUES(?,?,'XAUUSD','BUY',0.01,0.01,3500,3490,3520,'WIN',20.0,?,?)""",
                          (acc["id"], self.uid, "2026-11-10T00:00:00+00:00", f"2026-11-10T01:{i:02d}:00+00:00"))
        ov = demo_engine.account_overview(self.uid)
        self.assertEqual(len(ov["closed"]), 10)
        self.assertAlmostEqual(ov["win_rate"], 50.0, places=2)


if __name__ == "__main__":
    unittest.main()
