"""Live Strategy Leaderboard (v6.25.0 — 100% Real Verified Track Record).

Ranks all strategies from real closed signals in `signals` across three periods:
  * '7d'  : Last 7 days
  * '30d' : Last 30 days (default)
  * 'all' : All-time verified history

100% honest — only closed signals with real market-recorded `pips_result` are
counted. Supports one-tap publishing to linked Telegram broadcast channels.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

from fysb import db
from fysb.theme import bar, esc, strategy_name

_PERIOD_LABELS = {
    "7d": "آخر 7 أيام",
    "30d": "آخر 30 يوماً",
    "all": "السجل الكامل الموثّق",
}


def compute_leaderboard(period: str = "30d") -> dict:
    """Compute verified strategy performance rankings from closed signals."""
    p = (period or "30d").lower()
    if p not in _PERIOD_LABELS:
        p = "30d"
    cutoff = ""
    if p == "7d":
        cutoff = (datetime.now(timezone.utc) - timedelta(days=7)).isoformat()
    elif p == "30d":
        cutoff = (datetime.now(timezone.utc) - timedelta(days=30)).isoformat()

    with db.conn() as c:
        if cutoff:
            rows = c.execute(
                """SELECT strategy, symbol, status, pips_result
                   FROM signals
                   WHERE status IN ('TP1_HIT','TP2_HIT','TP3_HIT','SL_HIT')
                     AND closed_at IS NOT NULL AND closed_at!=''
                     AND closed_at>=?""",
                (cutoff,),
            ).fetchall()
        else:
            rows = c.execute(
                """SELECT strategy, symbol, status, pips_result
                   FROM signals
                   WHERE status IN ('TP1_HIT','TP2_HIT','TP3_HIT','SL_HIT')
                     AND closed_at IS NOT NULL AND closed_at!=''""",
            ).fetchall()

    buckets: dict[str, dict] = {}
    total_trades = 0
    total_wins = 0
    total_net_pips = 0.0

    for r in rows:
        code = str(r["strategy"] or "UNKNOWN").upper().strip()
        sym = str(r["symbol"] or "").upper().replace("/", "")
        pips = float(r["pips_result"] or 0.0)
        is_win = r["status"] in ("TP1_HIT", "TP2_HIT", "TP3_HIT") and pips >= 0

        b = buckets.setdefault(code, {
            "code": code,
            "trades": 0,
            "wins": 0,
            "losses": 0,
            "net_pips": 0.0,
            "gross_win": 0.0,
            "gross_loss": 0.0,
            "sym_pips": {},
        })
        b["trades"] += 1
        total_trades += 1
        b["net_pips"] += pips
        total_net_pips += pips
        if is_win:
            b["wins"] += 1
            total_wins += 1
            b["gross_win"] += max(pips, 0.0)
        else:
            b["losses"] += 1
            b["gross_loss"] += abs(min(pips, 0.0))
        if sym:
            b["sym_pips"][sym] = b["sym_pips"].get(sym, 0.0) + pips

    ranked: list[dict] = []
    for b in buckets.values():
        n = b["trades"]
        wr = (b["wins"] / n * 100.0) if n > 0 else 0.0
        if b["gross_loss"] > 0:
            pf = b["gross_win"] / b["gross_loss"]
        elif b["gross_win"] > 0:
            pf = 9.99
        else:
            pf = 0.0
        best_sym, best_sym_pips = "", 0.0
        if b["sym_pips"]:
            best_sym, best_sym_pips = max(b["sym_pips"].items(), key=lambda kv: kv[1])
        ranked.append({
            "code": b["code"],
            "name": strategy_name(b["code"]),
            "trades": n,
            "wins": b["wins"],
            "losses": b["losses"],
            "win_rate": round(wr, 1),
            "net_pips": round(b["net_pips"], 1),
            "profit_factor": round(pf, 2),
            "best_symbol": best_sym,
            "best_symbol_pips": round(best_sym_pips, 1),
        })

    ranked.sort(key=lambda x: (x["net_pips"], x["profit_factor"], x["win_rate"]), reverse=True)
    overall_wr = round((total_wins / total_trades * 100.0), 1) if total_trades else 0.0
    return {
        "period": p,
        "period_label": _PERIOD_LABELS[p],
        "total_trades": total_trades,
        "total_wins": total_wins,
        "overall_win_rate": overall_wr,
        "total_net_pips": round(total_net_pips, 1),
        "strategies": ranked,
    }


def render_leaderboard_card(period: str = "30d", channel_mode: bool = False) -> str:
    """Ultra-luxury Arabic Live Strategy Leaderboard card."""
    data = compute_leaderboard(period)
    medals = ("🥇", "🥈", "🥉")
    lines = [
        f"🏆 <b><u>مصفوفة ترتيب الاستراتيجيات الحية — {esc(data['period_label'])}</u></b>",
        "<blockquote>"
        f"▎📊 إجمالي الصفقات المغلقة الموثّقة: <b>{data['total_trades']}</b>\n"
        f"▎🎯 نسبة النجاح الإجمالية: <b>{data['overall_win_rate']:.1f}%</b>  {bar(data['overall_win_rate'], 100, 8)}\n"
        f"▎📈 صافي النقاط المحققة: <b>{data['total_net_pips']:+.1f} pips</b>"
        "</blockquote>",
        "",
    ]
    if not data["strategies"]:
        lines.append("<i>✨ لا توجد صفقات مغلقة خلال هذه الفترة بعد — ستظهر النتائج الموثّقة تلقائياً فور إغلاق أول صفقة.</i>")
    else:
        for i, s in enumerate(data["strategies"][:10]):
            m = medals[i] if i < 3 else f"<b>#{i + 1}</b>"
            pf_txt = f"{s['profit_factor']:.2f}" if s["profit_factor"] < 9.9 else "∞"
            best_txt = f" · أقوى سوق: <code>{esc(s['best_symbol'])}</code> ({s['best_symbol_pips']:+.0f}p)" if s["best_symbol"] else ""
            lines.append(
                f"{m} <b>{esc(s['name'])}</b> (<code>{esc(s['code'])}</code>)\n"
                f"   ▎🎯 فوز: <b>{s['win_rate']:.1f}%</b> ({s['wins']}✓ / {s['losses']}✗) · "
                f"⚖️ PF: <b>{pf_txt}</b> · 📈 صافي: <b>{s['net_pips']:+.1f} pips</b>{best_txt}"
            )
    if channel_mode:
        lines.append("\n👑 <i>نتائج حقيقية موثقة 100% بأسعار السوق — بدون أي تلاعب أو تعديل يدوي.</i>")
    else:
        lines.append("\n<i>💡 يتم تحديث ترتيب الاستراتيجيات تلقائياً مع كل إغلاق وتدخل النتائج في نظام الأوزان الذاتية للمحرك.</i>")
    return "\n".join(lines)
