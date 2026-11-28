"""v8.14.11 — «سجل الغياب الصادق» (the Honest Absence Ledger).

The bot never pretends it was always there. Every tracker cycle stamps a
heartbeat; on boot the gap since the last stamp is read honestly and the
owner receives ONE luxury report: «I was absent from X to Y — and this is
exactly what closed while I was away». Every trade closed inside the gap is
listed with its real recorded result — nothing is re-derived, nothing is
fabricated, and a short gap (< the threshold) is reported as nothing at all.
"""
from __future__ import annotations

from fysb import config, db
from fysb.theme import esc

ABSENCE_MIN_GAP_SECONDS = 600          # below this, a restart is transparent
HEARTBEAT_KEY = "tracker_last_cycle_seen"


def stamp_cycle() -> None:
    """One honest heartbeat per tracker cycle (cheap single-row upsert)."""
    db.set_setting(HEARTBEAT_KEY, config.now())


def _parse_iso(v: str):
    from datetime import datetime, timezone
    try:
        dt = datetime.fromisoformat(str(v))
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt
    except (TypeError, ValueError):
        return None


def build_absence_report() -> tuple[str | None, float]:
    """(luxury report | None, gap_seconds). None = no meaningful absence or
    nothing closed inside the gap — the silence is honest either way."""
    import time as _time
    last_iso = db.get_setting(HEARTBEAT_KEY, "") or ""
    last = _parse_iso(last_iso)
    if not last:
        return None, 0.0                       # first boot ever — no story yet
    now_dt = config.mecca_now()
    gap = (now_dt - last).total_seconds()
    if gap < ABSENCE_MIN_GAP_SECONDS:
        return None, gap
    # window in the same format family as closed_at (config.now() stamps)
    gap_lo = str(last_iso)
    with db.conn() as c:
        closed = c.execute(
            """SELECT id, symbol, direction, status, pips_result, closed_at
               FROM signals WHERE closed_at>=? AND closed_at!=''
                 AND status IN ('TP1_HIT','TP2_HIT','TP3_HIT','SL_HIT','LOCKED_50')
               ORDER BY closed_at ASC LIMIT 30""", (gap_lo,)).fetchall()
    mins = int(gap // 60)
    head = (
        f"🗓 <b><u>سجل الغياب الصادق</u></b>\n"
        "<blockquote>"
        f"▎⏱ غِبتُ من <b>{esc(str(last_iso)[:16])}</b> إلى "
        f"<b>{esc(now_dt.strftime('%Y-%m-%d %H:%M'))}</b> — نحو <b>{mins}</b> دقيقة.\n"
        f"▎📌 كل ما أُغلق أثناء غيابي موثق هنا بأوقاته ونتائجه المسجلة الحقيقية — "
        "لا شيء مُخترع ولا شيء مُخفي."
    )
    if not closed:
        head += "\n▎✨ لم تُغلق أي صفقة أثناء الغياب — السوق لم يقرر شيئاً في النافذة.</blockquote>"
        return head, gap
    icons = {"TP3_HIT": "👑", "TP2_HIT": "🥈", "TP1_HIT": "🥇", "LOCKED_50": "🏅", "SL_HIT": "🛑"}
    body = "</blockquote>\n<blockquote>"
    for r in closed:
        body += (
            "▎{} <code>#{}</code> <b>{}</b> {} <b>{:+.1f} pips</b> — {}\n".format(
                icons.get(r["status"], "•"), r["id"], esc(str(r["symbol"])),
                "🟢" if r["direction"] == "BUY" else "🔴",
                float(r["pips_result"] or 0.0), esc(str(r["closed_at"] or "").replace("T", " ")[:16])))
    body += "</blockquote>"
    return head + body, gap
