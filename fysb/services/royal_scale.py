"""⚖️ Royal Scale (v8.14.23) — the owner's honest measure-before-trust engine.

The owner's decree: raise the win rate TRUTHFULLY — no illusion, no games, no
lies — and prove it with numbers BEFORE touching the live system. The Royal
Scale answers with the only honest instrument that exists:

  * It runs the REAL walk-forward non-repainting backtest engine
    (fysb/engines/backtest.py — the exact live consensus + quality gates,
    conservative SL-first execution, realistic partial/BE model).
  * Then it answers the owner's questions with REAL aggregates on REAL
    historical candles:
      ① Baseline: WR · PF · NetR · expectancy · max drawdown · outcome matrix.
      ② The HONEST FLOOR: the share of filled trades that BANKED TP1 before
         any stop (TP1_BE/TP2_BE/TP3_HIT) — with the royal exit (bank TP1 +
         breakeven stop, already the engine's model) those trades can NEVER be
         recorded as losses, so the published win rate can never fall below
         that share. A promise by construction, not by hope.
      ③ The QUALITY GRID: WR/PF/NetR recomputed at each score threshold
         (the engine's default 72 + 80/85/90) — what the owner gains by
         demanding higher-quality signals only (post-trade filter, zero cost).
      ④ The QUARANTINE SIMULATION: drop every strategy failing the owner's
         sharp floors (settings quarantine_wr_floor/pf_floor/n) and show the
         remaining portfolio's honest stats + exactly who was dropped.
      ⑤ (deep, owner-triggered) the strict-gate reruns: HTF/chop/session
         gates relaxed — the honest "what if" of loosening the gates.
  * Every number is labeled modeled (backtest): no live slippage, simplified
    spread — disclosed on the card. This instrument MEASURES; it never promises
    a win rate. The decision stays the owner's.
"""
from __future__ import annotations

from fysb import db
from fysb.engines.backtest import BacktestReport, BacktestTrade, run_backtest

SCORE_GRID_DEFAULT = (72, 80, 85, 90)

BANKED = ("TP1_BE", "TP2_BE", "TP3_HIT")     # banked TP1 first — can never lose
WIN_OUTCOMES = ("TP1_BE", "TP2_BE", "TP3_HIT")
LOSS_OUTCOMES = ("SL_HIT",)


def aggregate(trades: list[BacktestTrade]) -> dict:
    """Mirror the engine's own counting EXACTLY (r_result sign semantics)."""
    filled = [t for t in trades if t.outcome != "UNFILLED"]
    wins = sum(1 for t in filled if t.r_result > 0)
    losses = sum(1 for t in filled if t.r_result < 0)
    net_r = round(sum(t.r_result for t in filled), 2)
    gross_win = sum(t.r_result for t in filled if t.r_result > 0)
    gross_loss = -sum(t.r_result for t in filled if t.r_result < 0)
    pf = round(gross_win / gross_loss, 2) if gross_loss > 1e-9 else (99.0 if gross_win > 0 else 0.0)
    wr = round(wins / len(filled) * 100.0, 1) if filled else 0.0
    exp = round(net_r / len(filled), 3) if filled else 0.0
    eq = 0.0
    peak = 0.0
    max_dd = 0.0
    for t in filled:
        eq += t.r_result
        peak = max(peak, eq)
        max_dd = min(max_dd, eq - peak)
    return {
        "n": len(filled), "wins": wins, "losses": losses,
        "wr": wr, "pf": pf, "net_r": net_r, "exp": exp, "max_dd": round(max_dd, 2),
    }


def tp1_bank_rate(trades: list[BacktestTrade]) -> float:
    """① the HONEST FLOOR — % of filled trades that banked TP1 before any stop."""
    filled = [t for t in trades if t.outcome != "UNFILLED"]
    if not filled:
        return 0.0
    return round(sum(1 for t in filled if t.outcome in BANKED) / len(filled) * 100.0, 1)


def score_grid(trades: list[BacktestTrade], grid=SCORE_GRID_DEFAULT) -> list[dict]:
    """③ what each higher quality threshold honestly keeps (post-filter)."""
    out = []
    for th in grid:
        kept = [t for t in trades if t.outcome != "UNFILLED" and t.score >= th]
        agg = aggregate(kept)
        agg["min_score"] = th
        out.append(agg)
    return out


def quarantine_sim(trades: list[BacktestTrade], wr_floor: float | None = None,
                   pf_floor: float | None = None, n_min: int | None = None) -> dict:
    """④ simulate the owner's sharp floors on the SAME historical trades."""
    wr_floor = wr_floor if wr_floor is not None else db.get_setting_float("quarantine_wr_floor", 40.0)
    pf_floor = pf_floor if pf_floor is not None else db.get_setting_float("quarantine_pf_floor", 0.9)
    n_min = n_min if n_min is not None else max(3, db.get_setting_int("quarantine_n", 20))
    filled = [t for t in trades if t.outcome != "UNFILLED"]
    per: dict[str, list[BacktestTrade]] = {}
    for t in filled:
        per.setdefault(t.strategy, []).append(t)
    dropped: list[dict] = []
    kept_trades: list[BacktestTrade] = []
    for code, ts in per.items():
        st = aggregate(ts)
        bad = st["n"] >= n_min and (st["wr"] < wr_floor or st["pf"] < pf_floor)
        if bad:
            dropped.append({"strategy": code, **st})
        else:
            kept_trades.extend(ts)
    remaining = aggregate(kept_trades)
    return {
        "wr_floor": wr_floor, "pf_floor": pf_floor, "n_min": n_min,
        "dropped": sorted(dropped, key=lambda d: d["net_r"]),
        "remaining": remaining,
        "baseline": aggregate(filled),
    }


def outcome_matrix(trades: list[BacktestTrade]) -> dict[str, int]:
    out: dict[str, int] = {}
    for t in trades:
        if t.outcome != "UNFILLED":
            out[t.outcome] = out.get(t.outcome, 0) + 1
    return dict(sorted(out.items(), key=lambda kv: -kv[1]))


def run_scale(symbol: str, timeframe: str = "M15", strategies: list[str] | None = None,
              outputsize: int = 800, min_score: int | None = None,
              strict_htf: bool = True, strict_chop: bool = True, strict_session: bool = True,
              deep: bool = False, candles: list[dict] | None = None) -> dict:
    """② run the REAL engine once (or with relaxed gates when deep) and answer
    with every honest aggregate. min_score defaults to the LIVE engine gate.
    `candles` may be injected (tests/offline) — otherwise the engine fetches
    the real history itself."""
    if min_score is None:
        from fysb.handlers.admin_trading import _effective_backtest_min_score
        try:
            min_score = _effective_backtest_min_score()
        except Exception:
            min_score = 72
    rep = run_backtest(symbol=symbol, timeframe=timeframe, strategies=strategies,
                       min_score=min_score, outputsize=outputsize, candles=candles,
                       strict_htf=strict_htf, strict_chop=strict_chop,
                       strict_session=strict_session)
    res: dict = {
        "symbol": rep.symbol, "timeframe": rep.timeframe,
        "bars_tested": rep.bars_tested, "error": rep.error,
        "baseline": aggregate(rep.trades),
        "unfilled": rep.unfilled,
        "matrix": outcome_matrix(rep.trades),
        "tp1_bank_rate": tp1_bank_rate(rep.trades),
        "grid": score_grid(rep.trades),
        "quarantine": quarantine_sim(rep.trades),
        "by_strategy": {code: aggregate([t for t in rep.trades
                                         if t.strategy == code and t.outcome != "UNFILLED"])
                        for code in rep.by_strategy},
        "variants": [],
    }
    if deep and not rep.error:
        for label, kw in (("بدون حارس الاتجاه الأعلى", {"strict_htf": False}),
                          ("بدون حارس التذبذب", {"strict_chop": False}),
                          ("بدون حارس الجلسة الرفيعة", {"strict_session": False})):
            r2 = run_backtest(symbol=symbol, timeframe=timeframe, strategies=strategies,
                              min_score=min_score, outputsize=outputsize, candles=candles, **kw)
            v = aggregate(r2.trades)
            v["label"] = label
            v["error"] = r2.error
            res["variants"].append(v)
    return res


def render_scale_report(res: dict) -> str:
    """The luxury Arabic card — every number real, every limit disclosed."""
    from fysb.theme import esc, tf_name
    sym = esc(res.get("symbol") or "XAUUSD")
    tf = tf_name(res.get("timeframe") or "M15")
    head = (f"⚖️ <b><u>الميزان الملكي — القياس قبل الثقة</u></b>\n"
            f"<blockquote>▎📊 {sym} · ⏱ {tf} · الشموع المؤكدة المفحوصة: <b>{res.get('bars_tested') or 0}</b>\n"
            "▎المحرك: walk-forward بلا إعادة رسم · بوابات الإنتاج نفسها · تسوية متحفظة (الوقف أولاً)\n"
            "▎⚠️ أرقام نموذجية على التاريخ — لا انزلاق حي وسبريد مبسط</blockquote>\n")
    if res.get("error"):
        return head + f"⛔ <b>تعذر القياس — السبب الحقيقي:</b>\n<blockquote>▎{esc(res['error'])}</blockquote>"
    b = res["baseline"]
    if not b["n"]:
        return (head + "<blockquote>▎🫥 لا صفقات منفّذة ضمن هذه النافذة والمعايير — "
                "وهذا هو الصدق: لا يُختلق تاريخ لملء ميزان.</blockquote>")
    matrix = " · ".join(f"<b>{esc(k)}</b>: {v}" for k, v in (res.get("matrix") or {}).items()) or "—"
    lines = [
        f"▎📌 الصفقات المنفّذة: <b>{b['n']}</b> (غير مفعّلة: {res.get('unfilled') or 0}) · "
        f"🟢 فوز <b>{b['wins']}</b> · 🔴 خسارة <b>{b['losses']}</b>",
        f"▎🧮 المصفوفة: {matrix}",
        f"▎📈 النتائج: نسبة النجاح <b>{b['wr']:.1f}%</b> · معامل الربح <b>{b['pf']:.2f}</b> · "
        f"صافي <b>{b['net_r']:+.2f}R</b> · التوقع <b>{b['exp']:+.3f}R</b> · أقصى تراجع <b>{b['max_dd']:+.2f}R</b>",
        "",
        f"🏅 <b>الأرضية الصادقة</b> — الصفقات التي مصرفت هدف 1 قبل أي وقف: "
        f"<b>{res.get('tp1_bank_rate') or 0:.1f}%</b>\n"
        "<blockquote>▎بالخروج الملكي (بنك هدف 1 + وقف تعادل — وهو نموذج المحرك نفسه) هذه الصفقات "
        "<b>لا يمكن أن تُسجل خاسرة أبداً</b> — فالنسبة المعلنة لا تنزل تحت هذه الأرضية بالبناء الرياضي. "
        "هذا وعيد بالقياس لا بحال السوق.</blockquote>",
    ]
    grid = res.get("grid") or []
    if grid:
        lines.append("🎛 <b>شبكة الجودة</b> — ماذا يفعل رفع العتبة (تصفية لاحقة حقيقية):")
        for g in grid:
            lines.append(f"▎• جودة ≥ <b>{g['min_score']}</b>: صفقات <b>{g['n']}</b> · "
                         f"نجاح <b>{g['wr']:.1f}%</b> · PF <b>{g['pf']:.2f}</b> · صافي <b>{g['net_r']:+.2f}R</b>")
    q = res.get("quarantine") or {}
    if q.get("dropped"):
        lines.append(f"🚫 <b>محاكاة الحجر الصحي الملكي</b> (عتباتك: نجاح <{q['wr_floor']:.0f}% أو "
                     f"PF <{q['pf_floor']:.2f} على {q['n_min']}+):")
        for d in q["dropped"]:
            lines.append(f"▎⚠️ يُسقط: <b>{esc(d['strategy'])}</b> — {d['n']} صفقة · "
                         f"نجاح {d['wr']:.1f}% · PF {d['pf']:.2f} · صافي {d['net_r']:+.2f}R")
        r = q["remaining"]
        lines.append(f"▎✅ الباقي بعد الحجر: صفقات <b>{r['n']}</b> · نجاح <b>{r['wr']:.1f}%</b> · "
                     f"PF <b>{r['pf']:.2f}</b> · صافي <b>{r['net_r']:+.2f}R</b>")
    else:
        lines.append("🚫 محاكاة الحجر الصحي: لا استراتيجية تنكسر عتباتك على هذه النافذة — صدق كامل.")
    bys = res.get("by_strategy") or {}
    if bys:
        top = sorted(((c, s) for c, s in bys.items() if s["n"]), key=lambda kv: kv[1]["net_r"], reverse=True)
        lines.append("🧠 <b>لكل استراتيجية</b> (الأربح أولاً):")
        for c, s in top[:8]:
            lines.append(f"▎• <b>{esc(c)}</b>: {s['n']} صفقة · نجاح <b>{s['wr']:.1f}%</b> · "
                         f"PF <b>{s['pf']:.2f}</b> · صافي <b>{s['net_r']:+.2f}R</b>")
    for v in res.get("variants") or []:
        if v.get("error"):
            lines.append(f"🧪 ماذا لو {v['label']}: ⛔ {esc(v['error'])[:80]}")
        else:
            lines.append(f"🧪 ماذا لو {v['label']}: صفقات <b>{v['n']}</b> · نجاح <b>{v['wr']:.1f}%</b> · "
                         f"PF <b>{v['pf']:.2f}</b> · صافي <b>{v['net_r']:+.2f}R</b>")
    lines.append("<i>⚖️ الميزان يقيس ولا يعد — القرار النهائي بأمرك على الأرقام.</i>")
    return head + "\n".join(lines)
