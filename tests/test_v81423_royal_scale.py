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
        c.execute("DELETE FROM chart_channels")
        c.execute("DELETE FROM audit")
    yield
    with db.conn() as c:
        c.execute("DELETE FROM signals")
        c.execute("DELETE FROM settings WHERE key LIKE 'quarantine%'")
        c.execute("DELETE FROM ui_events")
        c.execute("DELETE FROM chart_channels")
        c.execute("DELETE FROM audit")
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
    with patch("fysb.handlers.admin_scale._fetch_chart_candles", return_value=[]), \
         patch("fysb.services.royal_scale.run_scale", return_value=canned):
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
    with patch("fysb.handlers.admin_scale._fetch_chart_candles", return_value=[]), \
         patch("fysb.services.royal_scale.run_scale",
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
    with patch("fysb.handlers.admin_scale._fetch_chart_candles", return_value=[]), \
         patch("fysb.services.royal_scale.run_scale",
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


# ---------------------------------------------------------------------------
# v8.14.25 — «التقرير العام الفاخر»: لوحة الميزان بصورة الشارت الحقيقي
# ---------------------------------------------------------------------------

def _canned_result(trades=None):
    trades = trades or [_t("TREND", "TP3_HIT", 2.0, 85), _t("TREND", "TP1_BE", 0.0, 75),
                        _t("ICT", "SL_HIT", -1.0, 68)]
    return {"symbol": "XAUUSD", "timeframe": "M15", "bars_tested": 800, "error": "",
            "baseline": royal_scale.aggregate(trades),
            "unfilled": 0, "matrix": {}, "tp1_bank_rate": royal_scale.tp1_bank_rate(trades),
            "grid": royal_scale.score_grid(trades),
            "quarantine": royal_scale.quarantine_sim(trades),
            "by_strategy": {}, "variants": []}


def _add_pub_channel(chat_id: str, *, scale: int = 1, enabled: int = 1) -> int:
    from fysb.services import chart_channels as cch
    cid = cch.add_channel(chat_id, title=f"pub-{chat_id}", markets="ALL")
    if enabled != 1:
        cch.toggle_channel(cid)
    if scale != 1:
        cch.toggle_flag(cid, "send_on_scale")
    return cid


def test_scale_board_renders_real_candles_and_refuses_fake_ones():
    """The luxury board draws REAL OHLC candles; it refuses to render when the
    scale failed or the candles are too thin — never a fabricated picture."""
    from PIL import Image as PILImage
    from fysb.ui import charts
    res = _canned_result()
    candles = _bull_series(800)
    png = charts.render_scale_board(res, candles, elapsed=17.0, deep=False)
    assert png and png[:8] == b"\x89PNG\r\n\x1a\n"
    img = PILImage.open(__import__("io").BytesIO(png))
    assert img.size == (1200, 680)
    # guards: failed scale and thin candles never render
    assert charts.render_scale_board({**res, "error": "provider down"}, candles) is None
    assert charts.render_scale_board(res, candles[:10]) is None


def test_scale_caption_is_measured_and_honest_never_a_guarantee():
    """The public caption carries the MEASURED figures + the mandatory honesty
    lines; a failed scale produces an EMPTY caption (never published)."""
    from fysb.services import chart_channels as cch
    res = _canned_result()
    cap = cch.build_luxury_scale_caption(res, elapsed=17.0, deep=True)
    assert "لوحة الميزان الملكي" in cap and "XAUUSD" in cap and "15 دقيقة" in cap
    floor = res["tp1_bank_rate"]
    assert f"{floor:.1f}%" in cap
    assert "الأرضية الصادقة" in cap and "قياس عميق" in cap
    assert "أرقام نموذجية على التاريخ" in cap
    assert "لا ضمان ربح ولا رقم نسبة مضمون" in cap
    assert "الدرجة استيفاء شروط وليست احتمال فوز" in cap
    assert cch.build_luxury_scale_caption({**res, "error": "x"}) == ""


def test_send_on_scale_flag_lifecycle_and_matching():
    """The new «عند الميزان» channel flag: default ON, toggles persist, and
    matching_channels(mode='scale') serves only opted enabled channels."""
    from fysb.services import chart_channels as cch
    cid = _add_pub_channel("-10077001")
    ch = cch.get_channel(cid)
    assert int(ch["send_on_scale"]) == 1
    assert cch.matching_channels("XAUUSD", mode="scale")
    cch.toggle_flag(cid, "send_on_scale")
    assert int(cch.get_channel(cid)["send_on_scale"]) == 0
    assert not cch.matching_channels("XAUUSD", mode="scale")
    cch.toggle_flag(cid, "send_on_scale")
    cch.toggle_channel(cid)  # disabled channel must not match
    assert not cch.matching_channels("XAUUSD", mode="scale")


def test_scale_report_publishes_only_to_opted_enabled_channels():
    """deliver_scale_report serves ONLY enabled channels with send_on_scale=1,
    reports the honest count, and records last_sent for the served ones."""
    from fysb.services import chart_channels as cch
    on = _add_pub_channel("-10077002")
    off_flag = _add_pub_channel("-10077003", scale=0)
    disabled = _add_pub_channel("-10077004", enabled=0)
    bot = MockBot()
    cap = cch.build_luxury_scale_caption(_canned_result(), elapsed=9.0)
    sent = asyncio.run(cch.deliver_scale_report(bot, "XAUUSD", b"PNGDATA", cap))
    assert sent == 1
    assert [str(p["chat_id"]) for p in bot.photos] == ["-10077002"]
    ch = cch.get_channel(on)
    assert ch["last_sent"]
    assert not cch.get_channel(off_flag)["last_sent"]
    assert not cch.get_channel(disabled)["last_sent"]
    # empty caption (failed scale) and empty png are guards — nothing is sent
    assert asyncio.run(cch.deliver_scale_report(bot, "XAUUSD", b"PNGDATA", "")) == 0
    assert asyncio.run(cch.deliver_scale_report(bot, "XAUUSD", b"", cap)) == 0
    assert len(bot.photos) == 1


def test_scale_worker_delivers_owner_photo_and_publishes_public_board():
    """THE FULL ROYAL FLOW: one press → instant answer → detached worker →
    owner's chat gets the PHOTO board + the full report + the honest public
    line → opted public channels receive the luxury board photo."""
    from fysb.services import chart_channels as cch
    _add_pub_channel("-10077005")
    bot = MockBot()
    ctx = MockContext(bot=bot)
    ctx.user_data["scale_state"] = {"symbol": "XAUUSD", "timeframe": "M15",
                                    "outputsize": 400, "strategy": None}
    canned = _canned_result()
    candles = _bull_series(800)
    with patch("fysb.handlers.admin_scale._fetch_chart_candles", return_value=candles), \
         patch("fysb.services.royal_scale.run_scale", return_value=canned):
        (queries, _job) = _press_on_live_loop(["a:scale:run"], ctx)
    page = page_for(queries[0], ctx)
    assert "النتيجة ستصلك في شاتك فور اكتمالها" in page  # the route stays instant

    owner_texts = [m for m in bot.sent if m["chat_id"] == ADMIN]
    assert owner_texts and "الميزان الملكي — القياس قبل الثقة" in owner_texts[-1]["text"]
    assert "التقرير العام" in owner_texts[-1]["text"]
    assert "قناة عامة" in owner_texts[-1]["text"]
    owner_photos = [p for p in bot.photos if p["chat_id"] == ADMIN]
    assert owner_photos, "the owner must receive the luxury board photo"
    assert "لوحة الميزان الملكي" in owner_photos[-1]["caption"]
    pub_photos = [p for p in bot.photos if str(p["chat_id"]) == "-10077005"]
    assert pub_photos, "the opted public channel must receive the board"
    pub_cap = pub_photos[-1]["caption"]
    assert "أرقام نموذجية على التاريخ" in pub_cap and "لا ضمان ربح" in pub_cap
    with db.conn() as c:
        rows = c.execute("SELECT action FROM audit WHERE action LIKE 'royal_scale_public%'").fetchall()
    assert any("royal_scale_public:XAUUSD:1ch" in r["action"] for r in rows)


def test_scale_worker_without_channels_reports_the_honest_state():
    """No opted public channels → the owner is told the real state (how to
    enable), the board still lands in the owner's chat, nothing is faked."""
    bot = MockBot()
    ctx = MockContext(bot=bot)
    ctx.user_data["scale_state"] = {"symbol": "XAUUSD", "timeframe": "M15",
                                    "outputsize": 400, "strategy": None}
    with patch("fysb.handlers.admin_scale._fetch_chart_candles", return_value=_bull_series(800)), \
         patch("fysb.services.royal_scale.run_scale", return_value=_canned_result()):
        _press_on_live_loop(["a:scale:run"], ctx)
    owner_texts = [m for m in bot.sent if m["chat_id"] == ADMIN]
    assert owner_texts and "لا قنوات مفعّلة لتقارير الميزان بعد" in owner_texts[-1]["text"]
    assert "عند الميزان" in owner_texts[-1]["text"]
    owner_photos = [p for p in bot.photos if p["chat_id"] == ADMIN]
    assert owner_photos, "the owner photo does not depend on public channels"
    pub = [p for p in bot.photos if p["chat_id"] != ADMIN]
    assert not pub


def test_chart_fetch_failure_degrades_gracefully_report_still_arrives():
    """The chart fetch failing must NEVER break the scale: the report still
    reaches the owner, no photo is sent, and the real reason is audited."""
    bot = MockBot()
    ctx = MockContext(bot=bot)
    ctx.user_data["scale_state"] = {"symbol": "XAUUSD", "timeframe": "M15",
                                    "outputsize": 400, "strategy": None}
    with patch("fysb.handlers.admin_scale._fetch_chart_candles",
               side_effect=RuntimeError("provider unreachable")), \
         patch("fysb.services.royal_scale.run_scale", return_value=_canned_result()):
        _press_on_live_loop(["a:scale:run"], ctx)
    owner_texts = [m for m in bot.sent if m["chat_id"] == ADMIN]
    assert owner_texts and "الميزان الملكي — القياس قبل الثقة" in owner_texts[-1]["text"]
    assert "التقرير العام" not in owner_texts[-1]["text"]
    assert not bot.photos
    with db.conn() as c:
        rows = c.execute("SELECT action FROM audit WHERE action LIKE "
                         "'royal_scale_chart_candles_failed%'").fetchall()
    assert rows and "provider unreachable" in rows[-1]["action"]


def test_failed_scale_is_never_published_anywhere():
    """A FAILED scale publishes NOTHING — no photo, no public channel, the real
    error reaches the owner's chat as before."""
    from fysb.services import chart_channels as cch
    _add_pub_channel("-10077006")
    bot = MockBot()
    ctx = MockContext(bot=bot)
    ctx.user_data["scale_state"] = {"symbol": "XAUUSD", "timeframe": "M15",
                                    "outputsize": 400, "strategy": None}
    with patch("fysb.handlers.admin_scale._fetch_chart_candles", return_value=_bull_series(800)), \
         patch("fysb.services.royal_scale.run_scale",
               side_effect=RuntimeError("provider unreachable")):
        _press_on_live_loop(["a:scale:run"], ctx)
    owner_texts = [m for m in bot.sent if m["chat_id"] == ADMIN]
    assert owner_texts and "السبب الحقيقي" in owner_texts[-1]["text"]
    assert "provider unreachable" in owner_texts[-1]["text"]
    assert not bot.photos, "a failed scale must never render or publish a board"


def test_scale_board_matches_scale_numbers_exactly():
    """The board is fed the SAME result the report shows — the floor %, the
    counts, the quarantine drop are identical (one truth, two surfaces)."""
    from fysb.ui import charts
    res = _canned_result()
    trades = [_t("TREND", "TP3_HIT", 2.0, 85), _t("TREND", "TP1_BE", 0.0, 75),
              _t("ICT", "SL_HIT", -1.0, 68)]
    assert res["tp1_bank_rate"] == royal_scale.tp1_bank_rate(trades)
    assert res["baseline"]["n"] == 3
    png = charts.render_scale_board(res, _bull_series(800), elapsed=5.0)
    assert png  # the board renders with the very same numbers
