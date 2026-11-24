"""v6.20.6 Regression Tests — Institutional Quality Gate, Causal HTF Backtesting,
Strategy Hardening (ICT / CRT / PRICE_ACTION / SMC) & Statistical Honesty."""
import unittest
from datetime import datetime, timedelta, timezone

from fysb.engines import backtest, quality_gate
from fysb.engines.strategies import StrategyResult, crt, ict, price_action
from fysb.engines.strategies.base import Ctx


def _trend_bars(n: int = 120, start_px: float = 3600.0, step: float = 1.2,
                start_hour: int = 8, step_min: int = 15) -> list[dict]:
    t0 = datetime(2026, 11, 20, start_hour, 0, tzinfo=timezone.utc)
    out = []
    px = start_px
    for i in range(n):
        op = px
        cl = px + step
        hi = max(op, cl) + abs(step) * 0.6
        lo = min(op, cl) - abs(step) * 0.6
        dt = (t0 + timedelta(minutes=i * step_min)).strftime("%Y-%m-%d %H:%M:%S")
        out.append({"datetime": dt, "open": round(op, 2), "high": round(hi, 2),
                    "low": round(lo, 2), "close": round(cl, 2), "volume": 100.0})
        px = cl
    return out


def _chop_bars(n: int = 80, base: float = 3600.0, amp: float = 3.0) -> list[dict]:
    t0 = datetime(2026, 11, 20, 10, 0, tzinfo=timezone.utc)
    out = []
    for i in range(n):
        op = base + (amp if i % 2 == 0 else -amp)
        cl = base + (-amp if i % 2 == 0 else amp)
        hi = max(op, cl) + 1.5
        lo = min(op, cl) - 1.5
        dt = (t0 + timedelta(minutes=i * 15)).strftime("%Y-%m-%d %H:%M:%S")
        out.append({"datetime": dt, "open": op, "high": hi, "low": lo, "close": cl, "volume": 100.0})
    return out


class TestV6206InstitutionalQualityAndBacktest(unittest.TestCase):
    def test_causal_htf_resample_preserves_ohlc_and_zero_lookahead(self):
        bars = _trend_bars(160, start_px=3500.0, step=1.0)
        htf = quality_gate.resample_htf(bars, factor=4)
        self.assertEqual(len(htf), 40)
        # Last HTF bar must end at the exact last LTF bar (no stale lag, no future leak)
        self.assertEqual(htf[-1]["close"], bars[-1]["close"])
        self.assertEqual(htf[-1]["high"], max(b["high"] for b in bars[-4:]))
        self.assertEqual(htf[-1]["low"], min(b["low"] for b in bars[-4:]))

    def test_efficiency_ratio_separates_trend_from_chop(self):
        er_trend = quality_gate.efficiency_ratio(_trend_bars(60, step=1.0), 20)
        er_chop = quality_gate.efficiency_ratio(_chop_bars(60), 20)
        self.assertGreater(er_trend, 0.85)
        self.assertLess(er_chop, 0.15)

    def test_quality_gate_blocks_low_score_chop_counter_htf_and_falling_knife(self):
        up_ltf = _trend_bars(160, start_px=3500.0, step=1.2)
        up_htf = quality_gate.resample_htf(up_ltf, 4)
        chop = _chop_bars(80)

        # 1) Low score rejected
        weak = StrategyResult("PRICE_ACTION", "BUY", 3640, 3620, 3680, 3700, 3730, 2.0, 65, [])
        ok, reason = quality_gate.evaluate_quality_gate("XAUUSD", "M15", up_ltf, up_htf, weak, min_score=72)
        self.assertFalse(ok)
        self.assertIn("جودة", reason)

        # 2) Chop market rejected
        chop_cand = StrategyResult("PRICE_ACTION", "BUY", 3600, 3590, 3620, 3630, 3645, 2.0, 78, [])
        ok_c, reason_c = quality_gate.evaluate_quality_gate("XAUUSD", "M15", chop, chop, chop_cand, min_score=72)
        self.assertFalse(ok_c)
        self.assertIn("متذبذب", reason_c)

        # 3) Counter-HTF trade blocked (SELL into strong UP HTF!)
        counter = StrategyResult("ICT", "SELL", 3640, 3660, 3600, 3580, 3550, 2.0, 92, [], {"sweep": True})
        ok_htf, reason_htf = quality_gate.evaluate_quality_gate("XAUUSD", "M15", up_ltf, up_htf, counter, min_score=72)
        self.assertFalse(ok_htf)
        self.assertIn("الفريم الأعلى", reason_htf)

        # 4) Aligned high-quality setup passes cleanly
        aligned = StrategyResult("ICT", "BUY", 3640, 3620, 3680, 3700, 3730, 2.0, 82, [])
        ok_a, _ = quality_gate.evaluate_quality_gate("XAUUSD", "M15", up_ltf, up_htf, aligned, min_score=72)
        self.assertTrue(ok_a)

        # 5) Thin rollover session on Gold M15 requires +6 score, but 24/7 Crypto does not
        thin_bars = _trend_bars(60, start_px=3500.0, step=1.2, start_hour=0, step_min=1)
        mid_score = StrategyResult("ICT", "BUY", 3570, 3555, 3600, 3615, 3638, 2.0, 74, [])
        ok_gold_thin, _ = quality_gate.evaluate_quality_gate("XAUUSD", "M15", thin_bars, thin_bars, mid_score, min_score=72)
        ok_btc_thin, _ = quality_gate.evaluate_quality_gate("BTCUSD", "M15", thin_bars, thin_bars, mid_score, min_score=72)
        self.assertFalse(ok_gold_thin)
        self.assertTrue(ok_btc_thin)

    def test_ict_does_not_fire_blindly_without_sweep_or_zone_trigger(self):
        # Smooth uptrend with NO sweep and NO FVG/OB -> ICT must return None (not spam BUY!)
        bars = _trend_bars(60, start_px=3500.0, step=0.5)
        ctx = Ctx(symbol="XAUUSD", timeframe="M15", candles=bars, bias_candles=bars)
        res = ict.evaluate(ctx)
        self.assertIsNone(res)

    def test_price_action_rejects_tiny_doji_trigger(self):
        bars = _trend_bars(60, start_px=3500.0, step=1.0)
        # Replace final bar with a tiny doji
        last = dict(bars[-1])
        last["open"] = last["close"] - 0.05
        last["low"] = last["close"] - 2.0
        bars[-1] = last
        ctx = Ctx(symbol="XAUUSD", timeframe="M15", candles=bars, bias_candles=bars)
        self.assertIsNone(price_action.evaluate(ctx))

    def test_backtest_report_shows_htf_and_small_sample_warning(self):
        rep = backtest.BacktestReport(
            symbol="XAUUSD", timeframe="M15", bars_tested=199, strategies_used=["ICT"],
            total_signals=11, filled_trades=8, unfilled=3, wins=2, losses=6,
            win_rate_pct=25.0, profit_factor=0.67, net_r=-1.95, expectancy_r=-0.244,
            max_drawdown_r=4.0,
        )
        html = backtest.format_report(rep)
        self.assertIn("عينة صغيرة", html)
        self.assertIn("HTF", html)


if __name__ == "__main__":
    unittest.main()
