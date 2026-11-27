"""Real-spot truth layer tests — offline (mocked HTTP) + sanitizer checks.

Covers:
  * theme.rich(): whitelisted tags render, hostile/stray tags stay escaped
  * gold calibration: offset learning, absurd-offset rejection, OHLC shift
  * gold candles chain: venue candles get the live spot offset applied
  * Yahoo aggregation: 60m candles fold into UTC-aligned 4h buckets
  * ELITE -> VIP plan rename migration (idempotent)
  * analysis card: engine markup renders bold/code, no raw <script> ever
"""
import unittest
from unittest.mock import patch

import tests.helpers  # noqa: F401  (isolated test env FIRST — subprocess-safe import)
from fysb import db
from fysb.engines import market_data as md
from fysb.theme import rich


def _candle(i, base=3500.0):
    # v8.13.6: recent 15m stamps (last candle ~20min ago) — fresh vs the
    # staleness limit so the calibration contract is tested, not the age gate
    from datetime import datetime, timedelta
    t = datetime.utcnow() - timedelta(minutes=20 + 15 * (239 - i))
    return {"datetime": t.strftime("%Y-%m-%d %H:%M:00"),
            "open": base + i, "high": base + i + 2, "low": base + i - 2,
            "close": base + i + 1}


class TestRichSanitizer(unittest.TestCase):
    def test_whitelisted_tags_reopen(self):
        out = rich("الاتجاه: <b>صاعد</b> · <code>3,539.00</code>")
        self.assertIn("<b>صاعد</b>", out)
        self.assertIn("<code>3,539.00</code>", out)

    def test_hostile_tags_stay_escaped(self):
        out = rich("حقل <script>alert(1)</script> و <img src=x>")
        self.assertNotIn("<script>", out)
        self.assertNotIn("<img", out)
        self.assertIn("&lt;script&gt;", out)

    def test_plain_text_untouched(self):
        self.assertEqual(rich("نص عادي 100%"), "نص عادي 100%")

    def test_ampersand_escaped(self):
        self.assertIn("&amp;", rich("A & B"))


class TestGoldCalibration(unittest.TestCase):
    def setUp(self):
        from fysb.engines import provider_health
        provider_health.reset()

    def test_learn_offset_reasonable(self):
        off = md._learn_gold_offset("XAUUSD", 3539.0, 3552.49)
        self.assertIsNotNone(off)
        self.assertAlmostEqual(off, 3539.0 - 3552.49, delta=0.01)

    def test_learn_offset_rejects_absurd(self):
        # 3539 vs 3000 = +18% — way beyond the 2% guard; must be rejected
        self.assertIsNone(md._learn_gold_offset("XAUUSD", 3539.0, 3000.0))

    def test_persisted_offset_roundtrip(self):
        md._learn_gold_offset("XAUUSD", 3539.0, 3552.49)
        self.assertAlmostEqual(md._persisted_offset("XAUUSD"), 3539.0 - 3552.49, delta=0.01)

    def test_apply_offset_shifts_ohlc(self):
        values = [_candle(0), _candle(1)]
        shifted = md._apply_offset(values, -13.49)
        self.assertAlmostEqual(shifted[0]["close"] - values[0]["close"], -13.49)
        self.assertEqual(shifted[0]["datetime"], values[0]["datetime"])

    def test_gold_candles_get_spot_offset(self):
        """Venue candles must come out calibrated to the real spot level."""
        md._cache.clear()
        md._fail_streak.clear()
        raw = [_candle(i) for i in range(0, 240)]     # 240 fake 15m candles
        with patch.object(md, "_fetch_yahoo_candles", side_effect=RuntimeError("offline")), \
             patch.object(md, "_fetch_binance_candles", return_value=raw), \
             patch.object(md, "_gold_offset_now", return_value=-13.49):
            payload = md.fetch_candles("XAUUSD", "15min", "", 120, use_cache=False)
        self.assertAlmostEqual(payload["values"][-1]["close"] - raw[-1]["close"], -13.49)
        self.assertEqual(payload["source"], "LIVE")

    def test_gold_price_prefers_real_spot_reference(self):
        md._cache.clear()
        md._fail_streak.clear()
        with patch.object(md, "_fetch_goldapi_price", return_value={"price": 3539.0, "updated": "now"}):
            ref = md.fetch_price("XAUUSD", "")
        self.assertAlmostEqual(ref["price"], 3539.0)
        self.assertTrue(ref.get("spot_ref"))

    def test_gold_price_fallback_calibrated_venue(self):
        md._cache.clear()
        md._fail_streak.clear()
        with patch.object(md, "_fetch_goldapi_price", side_effect=RuntimeError("down")), \
             patch.object(md, "_fetch_binance_price", return_value=3552.49), \
             patch.object(md, "_gold_offset_now", return_value=-13.49):
            ref = md.fetch_price("XAUUSD", "")
        self.assertAlmostEqual(ref["price"], 3539.0, delta=0.01)


class TestYahooAggregation(unittest.TestCase):
    def test_60m_folds_into_4h_buckets(self):
        # 8 hourly candles spanning 2 UTC 4h buckets
        values = []
        for h in (0, 1, 2, 3, 4, 5, 6, 7):
            values.append({"datetime": f"2026-11-20 {h:02d}:00:00",
                           "open": 100.0 + h, "high": 101.0 + h, "low": 99.0 + h,
                           "close": 100.5 + h})
        out = md._aggregate_utc(values, 4 * 3600)
        self.assertEqual(len(out), 2)
        self.assertEqual(out[0]["datetime"], "2026-11-20 00:00:00")
        self.assertEqual(out[1]["datetime"], "2026-11-20 04:00:00")
        self.assertAlmostEqual(out[0]["high"], max(101.0 + h for h in (0, 1, 2, 3)))
        self.assertAlmostEqual(out[0]["low"], min(99.0 + h for h in (0, 1, 2, 3)))
        self.assertAlmostEqual(out[0]["close"], values[3]["close"])   # last close wins

    def test_yahoo_interval_map(self):
        self.assertEqual(md._yahoo_interval("15min"), ("15m", "1mo"))
        self.assertEqual(md._yahoo_interval("4h"), ("60m", "1y"))
        self.assertEqual(md._yahoo_interval("1week"), ("1wk", "5y"))


class TestElitePlanRenamed(unittest.TestCase):
    def test_fresh_db_has_vip_not_elite(self):
        db.init_db()
        with db.conn() as c:
            codes = {r["code"] for r in c.execute("SELECT code FROM plans").fetchall()}
        self.assertIn("VIP", codes)
        self.assertNotIn("ELITE", codes)

    def test_legacy_elite_rows_migrate(self):
        # Reproduce a REAL legacy DB: old seed created ELITE, no VIP existed yet.
        db.init_db()
        c = db.conn()
        c.execute("DELETE FROM plans WHERE code='VIP'")
        c.execute("INSERT INTO plans(code,title,price,currency,duration_days,min_score,"
                  "max_accounts,enabled,markets,timeframes,strategies,max_signals_per_day)"
                  " VALUES('ELITE','Elite',49,'USD',30,60,3,0,'XAUUSD','M15','ICT',50)")
        c.execute("INSERT OR REPLACE INTO subscriptions(id,user_id,plan_code,starts_at,status,created_at)"
                  " VALUES(99123, 555999, 'ELITE', '2026-01-01', 'ACTIVE', '2026-01-01')")
        c.commit()
        db._rename_legacy_elite_plan(c)
        c.commit()
        with db.conn() as q:
            self.assertIsNone(q.execute("SELECT 1 FROM plans WHERE code='ELITE'").fetchone())
            self.assertIsNotNone(q.execute("SELECT 1 FROM plans WHERE code='VIP'").fetchone())
            row = q.execute("SELECT plan_code FROM subscriptions WHERE id=99123").fetchone()
            self.assertEqual(row["plan_code"], "VIP")
            q.execute("DELETE FROM subscriptions WHERE id=99123")
        c.close()

    def test_rename_is_idempotent(self):
        db.init_db()
        c = db.conn()
        db._rename_legacy_elite_plan(c)
        db._rename_legacy_elite_plan(c)
        c.commit()
        c.close()
        with db.conn() as q:
            titles = {r["code"]: r["title"] for r in q.execute("SELECT code,title FROM plans").fetchall()}
        self.assertEqual(titles.get("VIP"), "VIP")


class TestAnalysisCardMarkup(unittest.TestCase):
    def _analysis(self):
        return {
            "symbol": "XAUUSD", "timeframe": "15 دقيقة", "tool": "", "tool_name": "منظومة مؤسسية مركبة",
            "strategy_name": "منظومة مؤسسية مركبة",
            "price": 3539.0, "price_time": "2026-11-22 02:00:00",
            "direction": "BUY", "quality": 95,
            "summary": ["الاتجاه الهيكلي: <b>صاعد</b> — آخر العلامات: قاع أعلى",
                        "مدى التقلب: <code>2.18</code> نقطة · التقلب النسبي: <b>0.06%</b>"],
            "zones": ["🟥 مقاومة هيكلية: <code>3,556.64</code>"],
            "recommendation": "منظومة مؤسسية مركبة تمنح توصية <b>شراء</b> على إطار M15",
            "activation": "ثبات الشموع فوق <code>3,556.64</code>",
            "invalidation": "3,550.85", "risk_note": "مخاطرة عقلية 1-2%",
            "market_open": False, "stale": False,
        }

    def test_engine_markup_renders_bold_and_code(self):
        from fysb.ui import cards
        text = cards.render_analysis_card(self._analysis())
        self.assertIn("<b>صاعد</b>", text)
        self.assertIn("<code>2.18</code>", text)
        self.assertIn("<b>شراء</b>", text)

    def test_no_hostile_html_passes_through(self):
        from fysb.ui import cards
        an = self._analysis()
        an["summary"] = ["حقن <script>alert(1)</script> خبيث"]
        text = cards.render_analysis_card(an)
        self.assertNotIn("<script>", text)
        self.assertIn("&lt;script&gt;", text)

    def test_no_elite_word_anywhere(self):
        from fysb.ui import cards
        text = cards.render_analysis_card(self._analysis())
        self.assertNotIn("ELITE", text.upper())


if __name__ == "__main__":
    unittest.main()
