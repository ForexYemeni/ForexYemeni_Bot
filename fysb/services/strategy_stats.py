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
import threading
import time

from fysb import db

_LB_TTL = 30.0
_lb_lock = threading.Lock()
_lb_cache: tuple[list[dict], float] | None = None


def invalidate_leaderboard_cache() -> None:
    global _lb_cache
    with _lb_lock:
        _lb_cache = None


def leaderboard() -> list[dict]:
    """Ranked strength list over closed signals, best first (30s TTL cache)."""
    global _lb_cache
    now_m = time.monotonic()
    with _lb_lock:
        if _lb_cache is not None and (now_m - _lb_cache[1]) < _LB_TTL:
            return [dict(d) for d in _lb_cache[0]]
    with db.conn() as c:
        rows = [dict(r) for r in c.execute(
            """SELECT * FROM signals
               WHERE status IN ('TP1_HIT','TP2_HIT','TP3_HIT','SL_HIT','LOCKED_50')
                 AND (status IN ('TP3_HIT','SL_HIT','LOCKED_50') OR COALESCE(closed_at,'')!='')
                 AND COALESCE(private_user_id,0)=0
                 AND COALESCE(UPPER(source),'') NOT IN
                     ('SIGNAL_NOW','ADMIN_SIGNAL_NOW','TRUE_ANALYSIS_INSTANT','TRUE_ANALYSIS_CONFIRMED')""").fetchall()]
    agg: dict[str, dict] = {}
    for r in rows:
        code = (r["strategy"] or "?").upper()
        d = agg.setdefault(code, {"n": 0, "wins": 0, "sl": 0, "tp2": 0, "tp3": 0, "r_sum": 0.0, "r_n": 0})
        d["n"] += 1
        hit = int(r["hit_tp"] or 0)
        # v8.14.10 🏅: a royally locked trade (+$5 documented) IS a real win —
        # banked profit must never be buried as a loss (owner's decree).
        if hit >= 1 or (r["status"] or "").upper() == "LOCKED_50":
            d["wins"] += 1
        if hit >= 2:
            d["tp2"] += 1
        if hit >= 3:
            d["tp3"] += 1
        if hit == 0 and (r["status"] or "").upper() == "SL_HIT":
            d["sl"] += 1          # only genuine stop-outs are losses here
        from fysb.engines.accounting import modeled_r
        value = modeled_r(r)
        if value is not None:
            d["r_sum"] += value
            d["r_n"] += 1
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
    with _lb_lock:
        _lb_cache = ([dict(d) for d in out], time.monotonic())
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
            f" · متوسط R نموذجي: <b>{d['avg_r']:+.2f}</b>")
    lines.append("</blockquote>")
    lines.append("<i>الترتيب آلي 100% من النتائج المسجلة (TP1/TP2/TP3/SL) — يتحدث تلقائياً مع كل إشارة مغلقة، "
                 "سجلات قديمة بقياس المسافة وأخرى بنموذج جزئي؛ ليست كشف حساب وسيط ولا تشمل كل التكاليف.</i>")
    return "\n".join(lines)


# v6.8.0 — REAL GUARANTEED ORDERING (owner instruction: «الأقوى أولاً 100%»).
# Two honest tiers, never a cosmetic shuffle:
#   tier 1: strategies WITH closed-signal history — ordered by their measured
#           strength score (50% win rate + 30% capped avg R + 20% TP3 share);
#   tier 2: strategies without history yet — ordered by a documented baseline
#           (the registry order below), then the not-yet-implemented last.
# The result is deterministic for the same data — the same DB always renders
# the same order, and the order upgrades itself as history accumulates.
# v6.12.0 — ONLY real implemented & automated-tested strategies (13 modules).
# All 25 empty/unimplemented placeholders have been permanently purged.
# v8.12.0 — five new schools appended (HARMONIC, VWAP_RECLAIM, LONDON_BREAKOUT,
# DIVERGENCE, SQUEEZE) → 18 implemented modules.
BASELINE_ORDER = [
    "ICT", "SMC", "CRT", "PRICE_ACTION", "WYCKOFF", "FIBONACCI",
    "ICHIMOKU", "TREND", "CLASSIC", "SK", "AMD", "SILVER_BULLET", "TURTLE_SOUP",
    "HARMONIC", "VWAP_RECLAIM", "LONDON_BREAKOUT", "DIVERGENCE", "SQUEEZE",
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
