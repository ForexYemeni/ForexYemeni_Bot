"""v6.25.0 — Phase 2 Institutional Intelligence & Strategy Leaderboard Suite:
1. Live Economic Calendar & News Guard (`news_guard.py`, `a:newsguard*`, `u:news`, engine block).
2. Adaptive Strategy Weighting (#3), Confluence Matrix (#4 + card badge), Live DXY Correlation (#5),
   Killzone Intelligence (#6), Liquidity Sweep Detector (#7), Currency Strength Meter (#12).
3. Pending CHoCH Auto-Invalidation (#8), Dynamic Break-Even & Lock-50% Advisory (#9),
   One-Time Early Invalidation Warning (#10).
4. Live Strategy Leaderboard (`leaderboard.py`, `u:leaderboard`, `a:leaderboard`, `a:lbpublish:*`).
"""
import asyncio
import json
import os
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from unittest.mock import patch

from fysb import config, db
from fysb.engines import intelligence, market_data, news_guard, signal_engine, tracker
from fysb.engines.strategies.base import StrategyResult
from fysb.handlers import dispatcher
from fysb.services import leaderboard, users
from fysb.ui.cards import render_signal_card

ADMIN = 970001
USER1 = 970010


def _fire(cb_data: str, uid: int, bot_sent: list | None = None):
    captured = {"text": "", "kb": None}

    async def _edit(text, reply_markup=None, parse_mode=None, **kw):
        captured["text"] = text
        captured["kb"] = reply_markup

    async def _reply(text, reply_markup=None, parse_mode=None, **kw):
        captured["text"] = text
        captured["kb"] = reply_markup

    async def _answer(*a, **kw):
        pass

    async def _send_msg(chat_id, text, **kw):
        if bot_sent is not None:
            bot_sent.append((chat_id, text))
        return SimpleNamespace(chat_id=chat_id, message_id=501)

    msg = SimpleNamespace(chat_id=uid, message_id=1, edit_text=_edit, reply_text=_reply, chat=SimpleNamespace(id=uid))
    q = SimpleNamespace(
        data=cb_data,
        from_user=SimpleNamespace(id=uid, username=f"u{uid}", first_name="U", full_name="U"),
        message=msg,
        answer=_answer,
    )
    upd = SimpleNamespace(callback_query=q, effective_user=q.from_user, effective_chat=SimpleNamespace(id=uid))
    ctx = SimpleNamespace(user_data={}, bot_data={}, bot=SimpleNamespace(send_message=_send_msg))
    asyncio.run(dispatcher.buttons(upd, ctx))
    datas = []
    if captured["kb"] and getattr(captured["kb"], "inline_keyboard", None):
        for row in captured["kb"].inline_keyboard:
            for b in row:
                if getattr(b, "callback_data", None):
                    datas.append(b.callback_data)
    return captured["text"], datas


class TestV6250Phase2IntelligenceAndLeaderboard(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self._old_db = config.DB_FILE
        self._old_admin = config.ADMIN_ID
        config.DB_FILE = os.path.join(self._tmp.name, "v6250.db")
        config.ADMIN_ID = ADMIN
        db.init_db()
        users.ensure_user(SimpleNamespace(id=USER1, username="u1", first_name="U1", full_name="U1"))
        news_guard.reset_cache()
        intelligence.reset_caches()
        tracker._EARLY_WARNED_SIDS.clear()

    def tearDown(self):
        news_guard.reset_cache()
        intelligence.reset_caches()
        tracker._EARLY_WARNED_SIDS.clear()
        config.DB_FILE = self._old_db
        config.ADMIN_ID = self._old_admin
        self._tmp.cleanup()

    # 1) Live News Guard + Engine block + Pre-News Alert + Admin UI
    def test_news_guard_blocks_affected_symbols_and_admin_controls(self):
        now = datetime.now(timezone.utc)
        ev_time = (now + timedelta(minutes=12)).isoformat()
        news_guard.set_cached_events([
            {
                "title": "US CPI m/m",
                "currency": "USD",
                "impact": "High",
                "dt_utc": ev_time,
                "forecast": "0.3%",
                "previous": "0.2%",
            },
            {
                "title": "Minor Speech",
                "currency": "USD",
                "impact": "Low",
                "dt_utc": ev_time,
            },
        ])
        self.assertEqual(news_guard.currencies_for_symbol("XAUUSD"), {"USD"})
        self.assertEqual(news_guard.currencies_for_symbol("EURUSD"), {"EUR", "USD"})
        self.assertEqual(news_guard.currencies_for_symbol("GBPJPY"), {"GBP", "JPY"})

        blocked_xau, ev = news_guard.check_news_block("XAUUSD", now_utc=now)
        self.assertTrue(blocked_xau)
        self.assertEqual(ev["title"], "US CPI m/m")

        blocked_gj, _ = news_guard.check_news_block("GBPJPY", now_utc=now)
        self.assertFalse(blocked_gj)

        # Signal engine immediately refuses XAUUSD during High-Impact USD news!
        with patch.object(market_data, "market_state", return_value=True):
            res = signal_engine.generate_signal("XAUUSD", "M15", force=False)
        self.assertEqual(res["status"], "REJECTED")
        self.assertIn("News Guard", res["reason"])

        # Pre-news alert fires once and deduplicates
        due1 = news_guard.due_pre_news_alerts(lead_minutes=30, now_utc=now)
        self.assertEqual(len(due1), 1)
        due2 = news_guard.due_pre_news_alerts(lead_minutes=30, now_utc=now)
        self.assertEqual(len(due2), 0)

        # Admin UI toggle & window control
        txt, datas = _fire("a:newsguard", ADMIN)
        self.assertIn("News Guard", txt)
        self.assertIn("US CPI m/m", txt)
        self.assertIn("a:newsguardtoggle", datas)
        _fire("a:newsguardwin:45", ADMIN)
        self.assertEqual(news_guard.window_minutes(), 45)
        _fire("a:newsguardtoggle", ADMIN)
        self.assertFalse(news_guard.is_enabled())

        # User calendar view
        u_txt, _ = _fire("u:news", USER1)
        self.assertIn("درع الأخبار الاقتصادية", u_txt)

    # 2) Institutional Intelligence Layers (#3, #4, #5, #6, #7, #12)
    def test_adaptive_weighting_confluence_dxy_killzone_sweep_and_strength(self):
        # #3 Adaptive strategy weighting: seed 6 closed SMC wins & 6 closed SK losses
        now_iso = config.now()
        with db.conn() as c:
            for i in range(6):
                c.execute(
                    """INSERT INTO signals(symbol,timeframe,direction,entry,stop,tp1,tp2,tp3,
                       targets,rr,score,strategy,reason,status,source,pips_result,created_at,closed_at)
                       VALUES('XAUUSD','M15','BUY',?,3485,3530,3545,3567,'3530,3545,3567',2.0,88,'SMC','w','TP2_HIT','AUTO',300.0,?,?)""",
                    (3500.0 + i, now_iso, now_iso),
                )
                c.execute(
                    """INSERT INTO signals(symbol,timeframe,direction,entry,stop,tp1,tp2,tp3,
                       targets,rr,score,strategy,reason,status,source,pips_result,created_at,closed_at)
                       VALUES('XAUUSD','M15','BUY',?,3485,3530,3545,3567,'3530,3545,3567',2.0,82,'SK','l','SL_HIT','AUTO',-150.0,?,?)""",
                    (3510.0 + i, now_iso, now_iso),
                )
        aw_smc = intelligence.adaptive_strategy_weight("SMC", "XAUUSD")
        aw_sk = intelligence.adaptive_strategy_weight("SK", "XAUUSD")
        self.assertGreater(aw_smc["delta"], 0.0)
        self.assertLess(aw_sk["delta"], 0.0)

        # #4 Confluence matrix + card rendering badge
        cands = [
            StrategyResult("SMC", "BUY", 3550, 3535, 3580, 3595, 3617, 2.0, 90),
            StrategyResult("ICT", "BUY", 3550, 3535, 3580, 3595, 3617, 2.0, 86),
            StrategyResult("CRT", "BUY", 3550, 3535, 3580, 3595, 3617, 2.0, 84),
        ]
        conf = intelligence.build_confluence_summary(cands, "BUY", "M15", bias_trend="up")
        self.assertEqual(conf["count"], 3)
        self.assertIn("A+ ملكي", conf["grade"])
        card_html = render_signal_card({
            "id": 1, "symbol": "XAUUSD", "timeframe": "M15", "direction": "BUY",
            "entry": 3550, "stop": 3535, "tp1": 3580, "tp2": 3595, "tp3": 3617,
            "rr": 2.0, "score": 90, "strategy": "SMC", "status": "ACTIVE",
            "reason": "test", "created_at": now_iso, "data_time": now_iso,
            "pips_result": 0.0, "closed_at": "",
            "strategy_scores": json.dumps({"SMC": 90, "ICT": 86, "CRT": 84}),
        }, user_view=True)
        self.assertIn("التوافق المؤسسي", card_html)
        self.assertIn("SMC · ICT · CRT", card_html)

        # #5 Live DXY correlation check
        intelligence.set_dxy_snapshot(-0.45)  # DXY falling -> bullish Gold, bearish USDJPY
        self.assertGreater(intelligence.dxy_bias_check("XAUUSD", "BUY")["delta"], 0)
        self.assertLess(intelligence.dxy_bias_check("XAUUSD", "SELL")["delta"], 0)
        self.assertLess(intelligence.dxy_bias_check("USDJPY", "BUY")["delta"], 0)
        self.assertGreater(intelligence.dxy_bias_check("USDJPY", "SELL")["delta"], 0)

        # #6 Killzone classification
        self.assertEqual(intelligence.current_killzone(datetime(2026, 11, 24, 14, 0, tzinfo=timezone.utc))["code"], "LONDON_NY_OVERLAP")
        self.assertEqual(intelligence.current_killzone(datetime(2026, 11, 24, 9, 0, tzinfo=timezone.utc))["code"], "LONDON")
        self.assertEqual(intelligence.current_killzone(datetime(2026, 11, 24, 17, 0, tzinfo=timezone.utc))["code"], "NEW_YORK")
        self.assertEqual(intelligence.current_killzone(datetime(2026, 11, 24, 2, 0, tzinfo=timezone.utc))["code"], "ASIA")

        # #7 Liquidity Sweep detector (bullish stop-hunt below 3500 swing low + reclaim close)
        bars = [{"open": 3510, "high": 3520, "low": 3500, "close": 3512} for _ in range(21)]
        bars.append({"open": 3505, "high": 3515, "low": 3492, "close": 3513})
        sw = intelligence.detect_liquidity_sweep(bars, lookback=20)
        self.assertTrue(sw["detected"])
        self.assertEqual(sw["direction"], "BUY")

        # #12 Currency Strength Meter (8 majors)
        ranked = intelligence.compute_strength_from_pairs({
            "EURUSD": +0.85, "GBPUSD": +0.55, "AUDUSD": +0.30, "NZDUSD": +0.10,
            "USDJPY": +0.90, "USDCAD": +0.20, "USDCHF": -0.10,
        })
        self.assertEqual(len(ranked), 8)
        self.assertEqual(ranked[0]["code"], "EUR")
        self.assertEqual(ranked[-1]["code"], "JPY")
        s_card = intelligence.render_strength_card(ranked)
        self.assertIn("مؤشر قوة العملات اللحظي", s_card)
        self.assertIn("EUR", s_card)
        self.assertIn("JPY", s_card)

    # 3) Tracker #8 Pending CHoCH Invalidation, #9 Dynamic Stop Advisory, #10 Early Invalidation
    def test_tracker_pending_choch_dynamic_stop_and_early_invalidation(self):
        sig_active = {
            "id": 41, "symbol": "XAUUSD", "timeframe": "M15", "direction": "BUY",
            "entry": 3550.0, "stop": 3535.0, "tp1": 3580.0, "tp2": 3595.0, "tp3": 3617.5,
            "status": "ACTIVE", "hit_tp": 0,
        }
        # #8: Candle closes below SL before entry fills -> auto-invalidated
        inv_ok, why = tracker.detect_pending_choch_invalidation(
            sig_active, [{"open": 3542, "high": 3544, "low": 3530, "close": 3532}]
        )
        self.assertTrue(inv_ok)
        self.assertIn("CHoCH", why)

        # #9: Dynamic stop advisory shows exact BE level on TP1 and Lock-50% TP1 level on TP2
        adv1 = tracker.dynamic_stop_advisory(sig_active, 1)
        self.assertIn("Break-Even", adv1)
        self.assertIn("3,550.00", adv1)
        adv2 = tracker.dynamic_stop_advisory(sig_active, 2)
        self.assertIn("تأمين 50%", adv2)
        self.assertIn("3,580.00", adv2)

        # #10: RUNNING trade retraces 70% toward SL + strong bearish body candle -> one-time warning!
        with db.conn() as c:
            sid = c.execute(
                """INSERT INTO signals(symbol,timeframe,direction,entry,stop,tp1,tp2,tp3,
                   targets,rr,score,strategy,reason,status,source,created_at)
                   VALUES('XAUUSD','M15','BUY',3550.0,3535.0,3580.0,3595.0,3617.5,
                   '3580,3595,3617.5',2.0,90,'SMC','run','RUNNING','AUTO',?)""",
                (config.now(),),
            ).lastrowid
        sig_run = dict(sig_active, id=sid, status="RUNNING")
        adverse_bars = [{"open": 3548.0, "high": 3549.0, "low": 3538.5, "close": 3539.5}]
        w1, txt1 = tracker.detect_early_invalidation(sig_run, 3539.5, adverse_bars)
        self.assertTrue(w1)
        self.assertIn("تنبيه ضعف مبكر", txt1)
        # Second call deduplicated!
        w2, _ = tracker.detect_early_invalidation(sig_run, 3539.0, adverse_bars)
        self.assertFalse(w2)

    # 4) Live Strategy Leaderboard + User/Admin UI + Channel Publishing
    def test_strategy_leaderboard_and_channel_publish(self):
        now_iso = config.now()
        with db.conn() as c:
            c.execute(
                """INSERT INTO signals(symbol,timeframe,direction,entry,stop,tp1,tp2,tp3,
                   targets,rr,score,strategy,reason,status,source,pips_result,created_at,closed_at)
                   VALUES('XAUUSD','M15','BUY',3500,3485,3530,3545,3567,'3530,3545,3567',2.0,92,'ICT','w','TP3_HIT','AUTO',675.0,?,?)""",
                (now_iso, now_iso),
            )
            c.execute(
                """INSERT INTO signals(symbol,timeframe,direction,entry,stop,tp1,tp2,tp3,
                   targets,rr,score,strategy,reason,status,source,pips_result,created_at,closed_at)
                   VALUES('EURUSD','H1','SELL',1.10,1.11,1.08,1.07,1.06,'1.08,1.07,1.06',2.0,85,'CLASSIC','l','SL_HIT','AUTO',-100.0,?,?)""",
                (now_iso, now_iso),
            )
            c.execute(
                "INSERT INTO broadcast_channels(chat_id,title,enabled,created_at,updated_at) VALUES('-100999888','VIP Ch',1,?,?)",
                (now_iso, now_iso),
            )

        lb = leaderboard.compute_leaderboard("30d")
        self.assertEqual(lb["total_trades"], 2)
        self.assertEqual(lb["strategies"][0]["code"], "ICT")
        self.assertEqual(lb["strategies"][0]["best_symbol"], "XAUUSD")

        # User leaderboard screen
        u_txt, u_datas = _fire("u:leaderboard", USER1)
        self.assertIn("مصفوفة ترتيب الاستراتيجيات الحية", u_txt)
        self.assertIn("ICT", u_txt)
        self.assertIn("u:lb:7d", u_datas)

        # Admin leaderboard + 1-tap publish to channels
        sent = []
        a_txt, a_datas = _fire("a:lbpublish:30d", ADMIN, bot_sent=sent)
        self.assertIn("تم نشر مصفوفة الاستراتيجيات", a_txt)
        self.assertEqual(len(sent), 1)
        self.assertEqual(sent[0][0], -100999888)
        self.assertIn("مصفوفة ترتيب الاستراتيجيات الحية", sent[0][1])


if __name__ == "__main__":
    unittest.main()
