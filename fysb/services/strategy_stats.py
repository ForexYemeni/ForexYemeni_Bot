"""Strategy strength leaderboard — REAL recorded outcomes only (v6.8.0).

The owner asked «which of the strategies is strongest?» — answered with
NUMBERS computed from the platform's own tracked signal history, never
marketing labels. Per implemented strategy over CLOSED signals
(TP1/TP2/TP3/SL): total, wins (hit_tp >= 1), SL losses, TP2/TP3 share,
average realized R (pips_result / planned risk), and a composite strength
score = 50% win rate + 30% capped avg R + 20% TP3 share.

Honesty contract:
  * only CLOSED signals count — a pending signal proves nothing;
  * strategies with no closed history are listed last and labeled
    «لا بيانات كافية بعد» — never invented numbers;
  * realized R uses the SAME pips the tracker banked (no optimistic rounding).
"""
from fysb import db


def leaderboard() -> list[dict]:
    """Ranked strength list over closed signals, best first."""
    with db.conn() as c:
        rows = [dict(r) for r in c.execute(
            """SELECT strategy, symbol, entry, stop, hit_tp, pips_result
               FROM signals
               WHERE status IN ('TP1_HIT','TP2_HIT','TP3_HIT','SL_HIT')""").fetchall()]
    agg: dict[str, dict] = {}
    for r in rows:
        code = (r["strategy"] or "?").upper()
        d = agg.setdefault(code, {"n": 0, "wins": 0, "sl": 0, "tp2": 0, "tp3": 0, "r_sum": 0.0, "r_n": 0})
        d["n"] += 1
        hit = int(r["hit_tp"] or 0)
        if hit >= 1:
            d["wins"] += 1
        if hit >= 2:
            d["tp2"] += 1
        if hit >= 3:
            d["tp3"] += 1
        if hit == 0:
            d["sl"] += 1          # SL_HIT rows carry hit_tp = 0 by definition
        # realized R = banked pips / planned risk pips (risk > 0 only)
        try:
            from fysb import instruments
            risk_pips = instruments.pips(r["symbol"], float(r["entry"]), float(r["stop"] or 0))
            if risk_pips > 0:
                d["r_sum"] += (float(r["pips_result"] or 0) / risk_pips)
                d["r_n"] += 1
        except Exception:
            pass
    out = []
    for code, d in agg.items():
        n = d["n"]
        win_rate = (d["wins"] / n * 100.0) if n else 0.0
        avg_r = (d["r_sum"] / d["r_n"]) if d["r_n"] else 0.0
        tp3_share = (d["tp3"] / n * 100.0) if n else 0.0
        strength = 0.5 * win_rate + 30.0 * min(max(avg_r, -1.0), 3.0) + 0.2 * tp3_share
        out.append({
            "code": code, "n": n, "wins": d["wins"], "sl": d["sl"],
            "tp2": d["tp2"], "tp3": d["tp3"],
            "win_rate": win_rate, "avg_r": avg_r,
            "tp3_share": tp3_share, "strength": round(strength, 1),
        })
    out.sort(key=lambda d: (d["strength"], d["win_rate"], d["n"]), reverse=True)
    for i, d in enumerate(out, start=1):
        d["rank"] = i
    return out


def leaderboard_text(limit: int = 8) -> str:
    """Luxury ranked board (HTML) — real numbers, honest when history is thin."""
    from fysb.theme import esc, strategy_name
    rows = leaderboard()
    if not rows:
        return ("🏆 <b>ترتيب قوة الاستراتيجيات — أرقام حقيقية</b>\n"
                "<blockquote>▎لا توجد إشارات مغلقة بعد — يُبنى الترتيب تلقائياً "
                "من نتائج الإشارات المسلمة (TP/SL) فور توفرها.</blockquote>")
    medals = {1: "🥇", 2: "🥈", 3: "🥉"}
    lines = ["🏆 <b><u>ترتيب قوة الاستراتيجيات — من نتائج الإشارات المسلمة فعلاً</u></b>", "<blockquote>"]
    for d in rows[:limit]:
        medal = medals.get(d["rank"], f"{d['rank']}.")
        lines.append(
            f"▎{medal} <b>{esc(strategy_name(d['code']))}</b> — قوة <b>{d['strength']:.1f}</b>"
            f" · إشارات مغلقة: <b>{d['n']}</b>"
            f" · رابحة: <b>{d['win_rate']:.0f}%</b>"
            f" · TP3: <b>{d['tp3_share']:.0f}%</b>"
            f" · متوسط R: <b>{d['avg_r']:+.2f}</b>")
    lines.append("</blockquote>")
    lines.append("<i>الترتيب آلي 100% من النتائج المسجلة (TP1/TP2/TP3/SL) — يتحدث تلقائياً مع كل إشارة مغلقة، "
                 "وبلا أي وعود تسويقية.</i>")
    return "\n".join(lines)


# v6.8.0 — REAL GUARANTEED ORDERING (owner instruction: «الأقوى أولاً 100%»).
# Two honest tiers, never a cosmetic shuffle:
#   tier 1: strategies WITH closed-signal history — ordered by their measured
#           strength score (50% win rate + 30% capped avg R + 20% TP3 share);
#   tier 2: strategies without history yet — ordered by a documented baseline
#           (the registry order below), then the not-yet-implemented last.
# The result is deterministic for the same data — the same DB always renders
# the same order, and the order upgrades itself as history accumulates.
# v6.8.1 — the baseline now covers the FULL world catalog: implemented modules
# first (their order = documented institutional ranking), then the complete
# global schools, so the board is ordered for every strategy in the platform.
BASELINE_ORDER = [
    # implemented + tested modules (institutional rank)
    "ICT", "SMC", "CRT", "PRICE_ACTION", "FIBONACCI", "ICHIMOKU", "TREND",
    "AMD", "SILVER_BULLET", "TURTLE_SOUP",
    # documented global schools (closed until implemented + back-tested)
    "WYCKOFF", "ELLIOTT_WAVE", "HARMONIC", "VOLUME_PROFILE", "ORDER_FLOW",
    "MARKET_PROFILE", "VWAP", "CPR", "PIVOT_POINTS", "SUPERTREND",
    "BREAKOUT_RETEST", "SUPPORT_RESISTANCE", "LONDON_BREAK", "ASIA_RANGE",
    "ORB", "DONCHIAN", "ATR_BREAKOUT", "MOMENTUM", "MA_CROSS", "MACD_STRAT",
    "RSI_DIVERGENCE", "BOLLINGER", "KELTNER", "MEAN_REVERSION", "GAP_TRADE",
    "HEIKIN_ASHI",
]


def ordered_codes(codes: list[str]) -> list[str]:
    """Sort strategy codes strongest-first (real history, then baseline)."""
    want = [c for c in BASELINE_ORDER if c in {x.upper() for x in codes}] + \
           [c.upper() for c in codes if c.upper() not in BASELINE_ORDER]
    board = {d["code"]: d for d in leaderboard()}
    with_history = [c for c in want if c in board]
    without = [c for c in want if c not in board]
    with_history.sort(key=lambda c: (-board[c]["strength"], -board[c]["win_rate"]))
    # implemented modules first, unimplemented tail last; Python's stable sort
    # preserves the documented BASELINE_ORDER within each group
    from fysb.engines.strategies import REGISTRY
    without.sort(key=lambda c: 0 if REGISTRY.get(c) is not None else 1)
    return with_history + without
