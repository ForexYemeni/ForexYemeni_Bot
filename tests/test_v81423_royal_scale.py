"""v8.14.23/24 — «الميزان الملكي — القياس قبل الثقة» tests — the owner's decree:

Raise the win rate TRUTHFULLY and PROVE it with numbers before touching the
live system. The Royal Scale:
① runs the REAL walk-forward engine (its exact gates; conservative SL-first),
② computes the HONEST FLOOR (TP1-banked share can never be a loss),
③ the quality grid (what each higher threshold keeps),
④ the quarantine simulation with the owner's SHARP floors — and the floors
   are ROYAL SETTINGS (quarantine_wr_floor/pf_floor/n), adjustable on the
   risk board, disclosed in the reason,
⑤ deep mode answers "what if the gates were relaxed" honestly,
⑥ the UI routes render with real params and honest errors; the reports hub
   carries the entry; nothing is promised — the card says the scale measures.
⑦ v8.14.24 — THE ROYAL PATTERN: the button answers instantly (the 20s royal
   route budget is never spent on a multi-minute backtest), the worker runs
   DETACHED, and the finished report lands in the OWNER'S CHAT; one scale at
   a time (the second press says so honestly); errors reach the owner too.
"""
from __future__ import annotations

import asyncio
import sys
import time
from datetime import datetime, timedelta, timezone
from unittest.mock import patch

import pytest

sys.path.insert(0, ".")

import tests.helpers  # noqa: F401
from tests.helpers import MockBot, MockContext, MockQuery, page_for, page_markup_for
from fysb import db
from fysb.engines.backtest import BacktestTrade, run_backtest
from fysb.engines.intelligence import strategy_quarantine_status
from fysb.handlers import admin_scale, dispatcher
from fysb.services import royal_scale

ADMIN = 9001


@pytest.fixture(autouse=True)
def _clean():
    db.init_db()
    with db.conn() as c:
        c.execute("DELETE FROM signals")
        c.execute("DELETE FROM settings WHERE key LIKE 'quarantine%'")
        c.execute("DELETE FROM ui_events")
    yield
    with db.conn() as c:
        c.execute("DELETE FROM signals")
        c.execute("DELETE FROM settings WHERE key LIKE 'quarantine%'")
        c.execute("DELETE FROM ui_events")
    # leave no worker behind between tests
    async def _drain():
        job = admin_scale._JOBS.get("royal_scale")
        if job:
            job.cancel()
            try:
                await job
            except (asyncio.CancelledError, Exception):
                pass
    try:
        asyncio.run(_drain())
    except Exception:
        pass
    admin_scale._JOBS.clear()
    admin_scale._JOB_INFO.clear()


def _fire(data: str, ctx=None):
    ctx = ctx or MockContext()
    q = MockQuery(data, ADMIN)
    q.from_user = type("U", (), {"id": ADMIN})()
    upd = type("Update", (), {})()
    upd.callback_query = q
    asyncio.run(dispatcher.buttons(upd, ctx))
    return q, ctx


def _labels(markup) -> list[str]:
    return [b.text for row in markup.inline_keyboard for b in row]


def _t(strategy, outcome, r, score=80.0):
    return BacktestTrade(strategy=strategy, direction="BUY", entry_idx=0, exit_idx=1,
                         entry=3500.0, stop=3490.0, tp1=3520.0, tp2=3540.0, tp3=3560.0,
                         rr=2.0, outcome=outcome, r_result=r, pips_result=r * 100.0,
                         score=score)


def _bull_series(n=140):
    t0 = datetime(2026, 10, 1, 10, 0, tzinfo=timezone.utc)
    bars, px = [], 3500.0
    for i in range(n):
        drift = 2.0 if (i // 10) % 3 != 2 else -3.0
        op, cl = px, px + drift
        bars.append({"datetime": (t0 + timedelta(minutes=15 * i)).strftime("%Y-%m-%d %H:%M:%S"),
                     "open": op, "high": max(op, cl) + 1.5, "low": min(op, cl) - 1.5,
                     "close": cl, "volume": 100.0})
        px = cl
    return bars


def _press_on_live_loop(presses: list[str], ctx):
    """Fire presses on ONE live loop (mirrors production: the PTB loop never
    closes between the press and the background delivery). Returns the queries
    and the live single-flight job handle."""
    async def _main():
        queries = []
        for data in presses:
            q = MockQuery(data, ADMIN)
            q.from_user = type("U", (), {"id": ADMIN})()
            upd = type("Update", (), {})()
            upd.callback_query = q
            await dispatcher.buttons(upd, ctx)
            queries.append(q)
        job = admin_scale._JOBS.get("royal_scale")
        if job:  # await the detached worker ON THE LIVE LOOP (production parity)
            try:
                await asyncio.wait_for(job, timeout=10)
            except (asyncio.CancelledError, Exception):
                pass
        return queries, job
    return asyncio.run(_main())


def _drain(job):
    async def _main():
        try:
            await asyncio.wait_for(job, timeout=10)
        except (asyncio.CancelledError, Exception):
            pass
    asyncio.run(_main())


# ---------------------------------------------------------------------------
# ① the REAL engine runs on real-shaped candles
# ---------------------------------------------------------------------------
def test_real_engine_runs_and_scale_aggregates_match_it():
    rep = run_backtest(symbol="XAUUSD", timeframe="M15", candles=_bull_series(),
                       strategies=["TREND"], min_score=60, strict_session=False,
                       outputsize=800)
    res = royal_scale.run_scale("XAUUSD", "M15", ["TREND"], outputsize=800, min_score=60,
                                strict_session=False, candles=_bull_series())
    if rep.error:  # honest error must surface, never fake numbers
        assert res["error"]
        return
    assert res["baseline"]["n"] == rep.filled_trades
    assert abs(res["baseline"]["wr"] - rep.win_rate_pct) < 0.11
    assert abs(res["baseline"]["net_r"] - rep.net_r) < 0.01
    assert res["tp1_bank_rate"] >= 0.0


def test_scale_on_fully_synthetic_trades_is_exact():
    trades = [_t("TREND", "TP3_HIT", 2.0, 90), _t("TREND", "TP1_BE", 0.5, 84),
              _t("SMC", "SL_HIT", -1.0, 75), _t("SMC", "SL_HIT", -1.0, 73)]
    agg = royal_scale.aggregate(trades)
    assert agg["n"] == 4 and agg["wins"] == 2 and agg["losses"] == 2
    assert agg["wr"] == 50.0 and agg["net_r"] == 0.5
    assert royal_scale.tp1_bank_rate(trades) == 50.0  # 2 of 4 banked TP1 — the honest floor


# ---------------------------------------------------------------------------
# ② the honest floor — banked-TP1 share can never be a loss
# ---------------------------------------------------------------------------
def test_honest_floor_promise_is_stated_on_the_card():
    trades = [_t("TREND", "TP1_BE", 0.5, 80), _t("TREND", "SL_HIT", -1.0, 80)]
    res = {"symbol": "XAUUSD", "timeframe": "M15", "bars_tested": 120, "error": "",
           "baseline": royal_scale.aggregate(trades), "unfilled": 0,
           "matrix": royal_scale.outcome_matrix(trades),
           "tp1_bank_rate": royal_scale.tp1_bank_rate(trades),
           "grid": [], "quarantine": {"dropped": []}, "by_strategy": {}, "variants": []}
    card = royal_scale.render_scale_report(res)
    assert "الأرضية الصادقة" in card and "50.0%" in card
    assert "لا يمكن أن تُسجل خاسرة أبداً" in card
    assert "أرقام نموذجية" in card  # the modeled label is disclosed, never hidden


# ---------------------------------------------------------------------------
# ③ the quality grid — post-filter honesty
# ---------------------------------------------------------------------------
def test_quality_grid_keeps_fewer_better_trades():
    trades = [_t("T", "TP3_HIT", 2.0, 88), _t("T", "TP3_HIT", 2.0, 82),
              _t("T", "SL_HIT", -1.0, 62), _t("T", "SL_HIT", -1.0, 55)]
    grid = royal_scale.score_grid(trades, grid=(50, 80, 90))
    assert grid[0]["n"] == 4 and grid[1]["n"] == 2 and grid[2]["n"] == 0
    assert grid[1]["wr"] == 100.0 and grid[1]["pf"] == 99.0  # winners only survive 80+


# ---------------------------------------------------------------------------
# ④ the SHARP quarantine — royal settings, disclosed, either-floor fails
# ---------------------------------------------------------------------------
def test_quarantine_sim_drops_the_bleeder_and_improves_the_rest():
    trades = ([_t("TREND", "TP3_HIT", 2.0, 80)] * 8
              + [_t("TREND", "SL_HIT", -1.0, 80)] * 2          # WR 80%
              + [_t("BLEED", "TP3_HIT", 2.0, 80)] * 2
              + [_t("BLEED", "SL_HIT", -1.0, 80)] * 18)        # WR 10%
    sim = royal_scale.quarantine_sim(trades, wr_floor=40.0, pf_floor=0.9, n_min=20)
    dropped = [d["strategy"] for d in sim["dropped"]]
    assert dropped == ["BLEED"]
    assert sim["remaining"]["wr"] == 80.0 and sim["remaining"]["pf"] == 8.0  # 8×2R over 2×1R
    assert sim["remaining"]["n"] == 10


def test_quarantine_thresholds_are_royal_settings_not_code_magic():
    with db.conn() as c:
        c.execute("INSERT OR REPLACE INTO settings(key,value) VALUES('quarantine_wr_floor','25')")
        c.execute("INSERT OR REPLACE INTO settings(key,value) VALUES('quarantine_pf_floor','0.5')")
        c.execute("INSERT OR REPLACE INTO settings(key,value) VALUES('quarantine_n','5')")
    sim = royal_scale.quarantine_sim(
        [_t("OK", "TP3_HIT", 2.0, 80)] * 4 + [_t("OK", "SL_HIT", -1.0, 80)] * 4)  # WR 50% PF ~1.33
    assert sim["dropped"] == []           # passes the LOOSE floors the owner set


def test_live_quarantine_uses_the_sharp_floors_and_discloses():
    def _ins(code, status, pips, age_days=1.0):
        now = datetime.now(timezone.utc)
        closed = (now - timedelta(days=age_days)).isoformat()
        created = (now - timedelta(days=age_days + 1)).isoformat()
        with db.conn() as c:
            c.execute("INSERT INTO signals(symbol,timeframe,status,direction,entry,stop,tp1,tp2,tp3,"
                      "rr,score,strategy,pips_result,profit_50_hit,closed_at,created_at)"
                      " VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                      ("XAUUSD", "M15", status, "BUY", 3500.0, 3490.0, 3520.0, 3540.0, 3560.0,
                       2.0, 80, code, pips, 0, closed, created))

    with db.conn() as c:
        c.execute("INSERT OR REPLACE INTO settings(key,value) VALUES('quarantine_n','20')")
        c.execute("INSERT OR REPLACE INTO settings(key,value) VALUES('quarantine_wr_floor','40')")
        c.execute("INSERT OR REPLACE INTO settings(key,value) VALUES('quarantine_pf_floor','0.9')")
    _ins("BLEED", "TP1_HIT", 200.0); _ins("BLEED", "TP1_HIT", 200.0)
    for _ in range(18):
        _ins("BLEED", "SL_HIT", -100.0)
    q = strategy_quarantine_status("BLEED", "XAUUSD")
    assert q["quarantined"] is True and q["trades"] == 20
    assert "العتبات" in (q.get("reason") or "")


# ---------------------------------------------------------------------------
# ⑤ the UI — real params, honest errors, hub entry
# ---------------------------------------------------------------------------
def test_scale_home_and_pickers_render_through_the_dispatcher():
    q, _ = _fire("a:scale")
    assert "الميزان الملكي" in page_for(q, q)
    q, _ = _fire("a:scale:m")
    assert "اختر سوق الميزان" in page_for(q, q)
    q, _ = _fire("a:scale:set:m:XAUUSD")
    assert "السوق: <b>XAUUSD</b>" in page_for(q, q)
    q, _ = _fire("a:scale:set:s:ICT")
    assert "الاستراتيجية: <b>ICT</b>" in page_for(q, q)


def test_scale_run_answers_instantly_and_delivers_to_owner_chat():
    """v8.14.24 — the button route NEVER spends the 20s royal budget on the
    heavy backtest: it answers instantly (⏳ page), the worker runs detached,
    and the finished board lands in the OWNER'S CHAT as its own message."""
    bot = MockBot()
    ctx = MockContext(bot=bot)
    ctx.user_data["scale_state"] = {"symbol": "XAUUSD", "timeframe": "M15",
                                    "outputsize": 400, "strategy": None}
    canned = {"symbol": "XAUUSD", "timeframe": "M15", "bars_tested": 120, "error": "",
              "baseline": royal_scale.aggregate([_t("TREND", "TP3_HIT", 2.0, 85),
                                                 _t("TREND", "SL_HIT", -1.0, 70)]),
              "unfilled": 0, "matrix": {"TP3_HIT": 1, "SL_HIT": 1},
              "tp1_bank_rate": 50.0, "grid": royal_scale.score_grid(
                  [_t("TREND", "TP3_HIT", 2.0, 85), _t("TREND", "SL_HIT", -1.0, 70)]),
              "quarantine": {"dropped": [], "wr_floor": 40.0, "pf_floor": 0.9, "n_min": 20},
              "by_strategy": {"TREND": {"n": 2, "wr": 50.0, "pf": 2.0, "net_r": 1.0,
                                        "wins": 1, "losses": 1, "exp": 0.5, "max_dd": -1.0}},
              "variants": []}
    with patch("fysb.services.royal_scale.run_scale", return_value=canned):
        (queries, _job) = _press_on_live_loop(["a:scale:run"], ctx)
    q = queries[0]
    page = page_for(q, ctx)  # the ROUTE page — instant, not the report
    assert "النتيجة ستصلك في شاتك فور اكتمالها" in page
    assert "بلا مهلة" in page
    assert "الأرضية الصادقة" not in page  # the heavy work is NOT in the route

    owner_msgs = [m for m in bot.sent if m["chat_id"] == ADMIN]
    assert owner_msgs, "the detached worker never delivered to the owner's chat"
    assert "الميزان الملكي — القياس قبل الثقة" in owner_msgs[-1]["text"]
    assert "الأرضية الصادقة" in owner_msgs[-1]["text"] and "50.0%" in owner_msgs[-1]["text"]
    assert "زمن القياس الفعلي" in owner_msgs[-1]["text"]
    assert admin_scale._JOBS.get("royal_scale") is None  # the instrument is free again


def test_scale_run_surfaces_the_real_error_to_owner_chat_never_fabricates():
    bot = MockBot()
    ctx = MockContext(bot=bot)
    ctx.user_data["scale_state"] = {"symbol": "XAUUSD", "timeframe": "M15",
                                    "outputsize": 400, "strategy": None}
    with patch("fysb.services.royal_scale.run_scale",
               side_effect=RuntimeError("provider unreachable")):
        (queries, _job) = _press_on_live_loop(["a:scale:run"], ctx)
    q = queries[0]
    assert "النتيجة ستصلك في شاتك" in page_for(q, ctx)

    owner_msgs = [m for m in bot.sent if m["chat_id"] == ADMIN]
    assert owner_msgs and "السبب الحقيقي" in owner_msgs[-1]["text"]
    assert "provider unreachable" in owner_msgs[-1]["text"]
    assert admin_scale._JOBS.get("royal_scale") is None  # the instrument is free again


def test_second_press_while_running_is_honest_not_stacked():
    bot = MockBot()
    ctx = MockContext(bot=bot)
    ctx.user_data["scale_state"] = {"symbol": "XAUUSD", "timeframe": "M15",
                                    "outputsize": 400, "strategy": None}

    def _slow(*a, **kw):
        time.sleep(2.0)  # a heavy multi-minute scale (blocks the executor thread)
        return {"error": "never reached", "baseline": {"n": 0}}

    with patch("fysb.services.royal_scale.run_scale", side_effect=_slow):
        (q1, q2), running = _press_on_live_loop(["a:scale:run", "a:scale:run"], ctx)
    assert "النتيجة ستصلك في شاتك" in page_for(q1, ctx)
    page2 = page_for(q2, ctx)
    assert "آلة الميزان مشغولة الآن بقياس آخر" in page2
    assert "جهاز واحد" in page2
    assert running is not None or admin_scale._JOBS.get("royal_scale") is None  # lock held live
    _drain(admin_scale._JOBS.get("royal_scale"))


def test_scale_run_surfaces_the_real_error_never_fabricates():
    """the old route contract, now honored by the DETACHED worker's delivery."""
    bot = MockBot()
    ctx = MockContext(bot=bot)
    ctx.user_data["scale_state"] = {"symbol": "XAUUSD", "timeframe": "M15",
                                    "outputsize": 400, "strategy": None}
    with patch("fysb.services.royal_scale.run_scale",
               side_effect=RuntimeError("provider unreachable")):
        (queries, _job) = _press_on_live_loop(["a:scale:run"], ctx)
    owner_msgs = [m for m in bot.sent if m["chat_id"] == ADMIN]
    assert owner_msgs and "provider unreachable" in owner_msgs[-1]["text"]


def test_reports_hub_carries_the_scale_entry():
    q, _ = _fire("a:reports")
    page = page_for(q, q)
    assert "الميزان الملكي" in page
    assert any("الميزان" in lbl for lbl in _labels(page_markup_for(q, q)))


def test_risk_board_carries_the_quarantine_floor_dials():
    src = open("fysb/handlers/admin_trading.py", encoding="utf-8").read()
    assert '"quarantine_wr_floor"' in src and '"quarantine_pf_floor"' in src and '"quarantine_n"' in src
    assert '"quarantine_wr_floor": (10.0, 80.0)' in src  # bounded — no absurd values
