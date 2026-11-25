"""Walk-Forward Non-Repainting Backtesting Engine (v6.20.0).

Institutional-grade historical evaluator for all 13 ForexYemeni strategies:
  * Strictly walk-forward: at bar i, strategies see ONLY candles[:i+1] (zero lookahead).
  * Runs the exact live consensus_filter + RR gate used in production.
  * Conservative SL-first execution model: if a candle spans both SL and TP,
    SL is assumed hit first (never optimistically credits targets).
  * Realistic partial-profit + breakeven trailing model matching live alerts:
      - 50% closed at TP1, stop moved to entry (breakeven)
      - 25% closed at TP2
      - 25% closed at TP3
  * Computes Win Rate, Profit Factor, Net R, Expectancy (R/trade), Max Drawdown
    (R), and per-strategy breakdown.
"""
from __future__ import annotations

from dataclasses import dataclass, field

from fysb import db, instruments
from fysb.engines import market_data
from fysb.engines.analysis import confirmed_candles
from fysb.engines.strategies import REGISTRY, consensus_filter, evaluate_all
from fysb.engines.strategies.base import Ctx


@dataclass
class BacktestTrade:
    strategy: str
    direction: str
    entry_idx: int
    exit_idx: int
    entry: float
    stop: float
    tp1: float
    tp2: float
    tp3: float
    rr: float
    outcome: str       # SL_HIT, TP1_BE, TP2_BE, TP3_HIT, TIME_EXIT, UNFILLED
    r_result: float
    pips_result: float


@dataclass
class BacktestReport:
    symbol: str
    timeframe: str
    bars_tested: int
    strategies_used: list[str]
    total_signals: int = 0
    filled_trades: int = 0
    unfilled: int = 0
    wins: int = 0
    losses: int = 0
    win_rate_pct: float = 0.0
    profit_factor: float = 0.0
    net_r: float = 0.0
    expectancy_r: float = 0.0
    max_drawdown_r: float = 0.0
    by_strategy: dict[str, dict] = field(default_factory=dict)
    trades: list[BacktestTrade] = field(default_factory=list)
    error: str = ""


def _simulate_trade(symbol: str, cand, future: list[dict], start_idx: int,
                    spread_pips: float = 0.5) -> BacktestTrade:
    """Chronological SL-first walk on strictly future candles."""
    pip = max(instruments.spec(symbol).pip_size, 1e-9)
    spread = max(0.0, spread_pips) * pip
    entry = float(cand.entry)
    stop = float(cand.stop)
    tp1, tp2, tp3 = float(cand.tp1), float(cand.tp2), float(cand.tp3)
    risk = abs(entry - stop)
    if risk <= 1e-9 or not future:
        return BacktestTrade(cand.code, cand.direction, start_idx, start_idx,
                             entry, stop, tp1, tp2, tp3, cand.rr, "UNFILLED", 0.0, 0.0)

    # 1. Entry fill check on future bars (within first 12 bars)
    fill_offset = -1
    for k, bar in enumerate(future[:12]):
        hi, lo, op = float(bar["high"]), float(bar["low"]), float(bar["open"])
        if cand.direction == "BUY":
            if lo <= entry or op <= entry + 0.15 * risk:
                fill_offset = k
                break
        else:
            if hi >= entry or op >= entry - 0.15 * risk:
                fill_offset = k
                break
    if fill_offset < 0:
        return BacktestTrade(cand.code, cand.direction, start_idx, start_idx + min(12, len(future)),
                             entry, stop, tp1, tp2, tp3, cand.rr, "UNFILLED", 0.0, 0.0)

    tp1_r = abs(tp1 - entry) / risk
    tp2_r = abs(tp2 - entry) / risk
    tp3_r = abs(tp3 - entry) / risk
    realized_r = 0.0
    rem_weight = 1.0
    hit_tp = 0
    tp_hit_bar = -1
    outcome = "TIME_EXIT"
    exit_offset = len(future) - 1

    for k in range(fill_offset, len(future)):
        bar = future[k]
        hi, lo = float(bar["high"]), float(bar["low"])
        if cand.direction == "BUY":
            sl_hit = lo <= stop + spread
            be_hit = lo <= entry + spread
            t1_hit = hi >= tp1 + spread
            t2_hit = hi >= tp2 + spread
            t3_hit = hi >= tp3 + spread
        else:
            sl_hit = hi >= stop - spread
            be_hit = hi >= entry - spread
            t1_hit = lo <= tp1 - spread
            t2_hit = lo <= tp2 - spread
            t3_hit = lo <= tp3 - spread

        # Conservative SL-first BEFORE any TP is banked
        if hit_tp == 0 and sl_hit:
            realized_r = -1.0
            outcome = "SL_HIT"
            exit_offset = k
            break

        # Breakeven stop-out on subsequent bar after TP1/TP2
        if hit_tp in (1, 2) and be_hit and k > tp_hit_bar:
            outcome = f"TP{hit_tp}_BE"
            exit_offset = k
            break

        if hit_tp == 0 and t1_hit:
            realized_r += 0.50 * tp1_r
            rem_weight = 0.50
            hit_tp = 1
            tp_hit_bar = k
        if hit_tp == 1 and t2_hit:
            realized_r += 0.25 * tp2_r
            rem_weight = 0.25
            hit_tp = 2
            tp_hit_bar = k
        if hit_tp == 2 and t3_hit:
            realized_r += 0.25 * tp3_r
            rem_weight = 0.0
            hit_tp = 3
            outcome = "TP3_HIT"
            exit_offset = k
            break

    if outcome == "TIME_EXIT":
        last_close = float(future[exit_offset]["close"])
        signed_move = (last_close - entry) if cand.direction == "BUY" else (entry - last_close)
        realized_r += rem_weight * (signed_move / risk)

    pips_res = realized_r * instruments.pips(symbol, entry, stop)
    return BacktestTrade(
        strategy=cand.code,
        direction=cand.direction,
        entry_idx=start_idx + fill_offset,
        exit_idx=start_idx + exit_offset,
        entry=entry,
        stop=stop,
        tp1=tp1,
        tp2=tp2,
        tp3=tp3,
        rr=round(cand.rr, 2),
        outcome=outcome,
        r_result=round(realized_r, 3),
        pips_result=round(pips_res, 1),
    )


def run_backtest(
    symbol: str,
    timeframe: str = "M15",
    candles: list[dict] | None = None,
    strategies: list[str] | None = None,
    warmup: int = 60,
    max_hold_bars: int = 48,
    min_rr: float = 2.0,
    min_score: int = 72,
    spread_pips: float = 0.5,
    api_key: str = "",
    outputsize: int = 800,
    strict_htf: bool = True,
    strict_chop: bool = True,
    strict_session: bool = True,
) -> BacktestReport:
    """Run a non-repainting walk-forward backtest matching the LIVE engine:
      * Strictly causal HTF resampling (`resample_htf`) — zero lookahead.
      * Exact same `consensus_filter` + `evaluate_quality_gate` (min_score,
        HTF alignment, Kaufman ER chop filter, thin-session guard) + RR gate.
      * Conservative SL-first execution with realistic spread & BE trailing.
    """
    from fysb.engines.quality_gate import evaluate_quality_gate, htf_factor_for, resample_htf

    sym = (symbol or "XAUUSD").strip().upper()
    tf = (timeframe or "M15").strip().upper()
    allowed = [s.strip().upper() for s in (strategies or list(REGISTRY.keys())) if s.strip().upper() in REGISTRY]
    if not allowed:
        allowed = list(REGISTRY.keys())

    if candles is None:
        try:
            with db.conn() as c:
                r = c.execute("SELECT provider_symbol FROM market_catalog WHERE symbol=?", (sym,)).fetchone()
            prov_sym = r["provider_symbol"] if r and r["provider_symbol"] else sym
            payload = market_data.fetch_candles(prov_sym, market_data.parse_interval(tf), api_key, outputsize=outputsize)
            candles = confirmed_candles(payload["values"], tf)
        except Exception as exc:
            return BacktestReport(symbol=sym, timeframe=tf, bars_tested=0,
                                  strategies_used=allowed, error=str(exc)[:180])

    n = len(candles)
    if n < warmup + 10:
        return BacktestReport(symbol=sym, timeframe=tf, bars_tested=n,
                              strategies_used=allowed,
                              error=f"بيانات تاريخية غير كافية ({n} شمعة، المطلوب {warmup + 10}+)")

    htf_factor = htf_factor_for(tf)
    trades: list[BacktestTrade] = []
    i = warmup
    while i < n - 3:
        window = candles[:i + 1]
        htf_window = resample_htf(window, htf_factor)
        ctx = Ctx(symbol=sym, timeframe=tf, candles=window, bias_candles=htf_window)
        raw = evaluate_all(ctx, allowed)
        if not raw:
            i += 1
            continue
        filtered, _note = consensus_filter(raw)
        if not filtered:
            i += 1
            continue
        cand = None
        for r in filtered:
            if r.rr < min_rr:
                continue
            ok_q, _q_reason = evaluate_quality_gate(
                sym, tf, window, htf_window, r,
                min_score=min_score,
                strict_htf=strict_htf,
                strict_chop=strict_chop,
                strict_session=strict_session,
            )
            if ok_q:
                cand = r
                break
        if cand is None:
            i += 1
            continue

        future = candles[i + 1:min(n, i + 1 + max_hold_bars)]
        tr = _simulate_trade(sym, cand, future, i + 1, spread_pips=spread_pips)
        trades.append(tr)
        # Advance past exit (or past entry attempt if unfilled) to avoid overlapping trades
        i = max(i + 2, tr.exit_idx + 1)

    report = BacktestReport(
        symbol=sym,
        timeframe=tf,
        bars_tested=n - warmup,
        strategies_used=allowed,
        total_signals=len(trades),
        trades=trades,
    )
    filled = [t for t in trades if t.outcome != "UNFILLED"]
    report.filled_trades = len(filled)
    report.unfilled = len(trades) - len(filled)
    if not filled:
        return report

    gross_win = 0.0
    gross_loss = 0.0
    equity = 0.0
    peak = 0.0
    max_dd = 0.0
    by_strat: dict[str, dict] = {}

    for t in filled:
        if t.r_result > 0:
            report.wins += 1
            gross_win += t.r_result
        elif t.r_result < 0:
            report.losses += 1
            gross_loss += abs(t.r_result)
        equity += t.r_result
        if equity > peak:
            peak = equity
        dd = peak - equity
        if dd > max_dd:
            max_dd = dd

        st = by_strat.setdefault(t.strategy, {"trades": 0, "wins": 0, "losses": 0, "net_r": 0.0})
        st["trades"] += 1
        if t.r_result > 0:
            st["wins"] += 1
        elif t.r_result < 0:
            st["losses"] += 1
        st["net_r"] = round(st["net_r"] + t.r_result, 2)

    for st in by_strat.values():
        st["win_rate_pct"] = round((st["wins"] / st["trades"] * 100.0) if st["trades"] else 0.0, 1)

    report.win_rate_pct = round(report.wins / len(filled) * 100.0, 1)
    report.net_r = round(equity, 2)
    report.expectancy_r = round(equity / len(filled), 3)
    report.max_drawdown_r = round(max_dd, 2)
    report.profit_factor = round(gross_win / gross_loss, 2) if gross_loss > 1e-9 else (99.0 if gross_win > 0 else 0.0)
    report.by_strategy = dict(sorted(by_strat.items(), key=lambda kv: kv[1]["net_r"], reverse=True))
    return report


def format_report(rep: BacktestReport) -> str:
    """Render BacktestReport as a luxury Arabic HTML card for Telegram."""
    from fysb.theme import esc, tf_name
    if rep.error:
        return (
            f"🧪 <b><u>نتائج الاختبار التاريخي — {esc(rep.symbol)}</u></b> ⏱ {tf_name(rep.timeframe)}\n"
            f"<blockquote>▎⛔ {esc(rep.error)}</blockquote>"
        )
    if rep.filled_trades == 0:
        return (
            f"🧪 <b><u>نتائج الاختبار التاريخي — {esc(rep.symbol)}</u></b> ⏱ {tf_name(rep.timeframe)}\n"
            f"<blockquote>"
            f"▎📊 الشموع المفحوصة: <b>{rep.bars_tested}</b> شمعة مؤكدة\n"
            f"▎📌 الصفقات المنفّذة: <b>0</b> (إشارات غير مفعّلة: {rep.unfilled})\n"
            f"▎💡 لم تتحقق فرص مطابقة لمعايير الجودة والإجماع ضمن هذه النافذة التاريخية."
            f"</blockquote>"
        )
    strat_lines = []
    for code, st in rep.by_strategy.items():
        strat_lines.append(
            f"▎• <b>{esc(code)}</b>: {st['trades']} صفقات · فوز <code>{st['win_rate_pct']:.0f}%</code> · صافي <b>{st['net_r']:+.2f}R</b>"
        )
    silent_strats = [c for c in rep.strategies_used if c not in rep.by_strategy]
    if silent_strats:
        strat_lines.append(
            f"▎▫️ منهجيات بلا صفقة مطابقة في هذه النافذة ({len(silent_strats)}): <i>{esc('، '.join(silent_strats))}</i>"
        )
    pf_txt = f"{rep.profit_factor:.2f}" if rep.profit_factor < 99 else "∞"
    if rep.filled_trades < 15:
        verdict = "🟡 عينة صغيرة — لا تكفي للحكم الإحصائي النهائي (يُفضّل 30+ صفقة أو فريم H1/H4)"
    elif rep.profit_factor >= 1.5 and rep.net_r > 0:
        verdict = "✅ أداء مؤسسي مؤهّل (معامل ربح ≥ 1.5 وعائد إيجابي)"
    elif rep.profit_factor >= 1.05 and rep.net_r > 0:
        verdict = "🟢 أداء إيجابي معتدل — يمكن تحسينه بتعطيل المنهجيات الأضعف أدناه"
    else:
        verdict = "🔴 أداء ضعيف على هذا الفريم — راجع تفعيل المنهجيات الرابحة فقط"
    return (
        f"🧪 <b><u>تقرير الاختبار التاريخي المؤسسي — {esc(rep.symbol)}</u></b> ⏱ {tf_name(rep.timeframe)}\n"
        f"<blockquote>"
        f"▎📊 الشموع المفحوصة: <b>{rep.bars_tested}</b> شمعة مغلقة (Walk-Forward + HTF بلا Repainting)\n"
        f"▎🧠 المنهجيات المفحوصة: <b>{len(rep.strategies_used)}</b> منهجية (نشطت منها: <b>{len(rep.by_strategy)}</b>)\n"
        f"▎🎯 الصفقات المنفّذة: <b>{rep.filled_trades}</b> (فائزة: <b>{rep.wins}</b> · خاسرة: <b>{rep.losses}</b> · غير مفعّلة: {rep.unfilled})\n"
        f"▎🏆 نسبة النجاح: <b>{rep.win_rate_pct:.1f}%</b> · عامل الربح (PF): <b>{pf_txt}</b>\n"
        f"▎📈 صافي العائد: <b>{rep.net_r:+.2f}R</b> · التوقع/صفقة: <b>{rep.expectancy_r:+.3f}R</b>\n"
        f"▎🛡 أقصى تراجع (Max DD): <b>-{rep.max_drawdown_r:.2f}R</b>\n"
        f"▎⚖️ التقييم الإحصائي: <b>{verdict}</b>\n"
        f"</blockquote>\n"
        + (f"<b>أداء المنهجيات ({len(rep.by_strategy)}/{len(rep.strategies_used)}):</b>\n<blockquote>" + "\n".join(strat_lines) + "</blockquote>\n" if strat_lines else "")
        + "<i>محاكاة SL-أولاً مع سبريد واقعي + فلتر اتجاه الفريم الأعلى + فلتر التذبذب + تأمين الدخول بعد TP1.</i>"
    )
