"""Auto Weekly & Monthly Luxury Performance Digest Service (v7.6.0).

Computes real SQL performance metrics over the trailing 7 or 30 days, renders
both an executive Arabic Telegram card and a high-DPI dark-gold PNG summary
card, and delivers to the Admin and enabled VIP/Broadcast channels.
"""
from __future__ import annotations

import io
from datetime import datetime, timedelta, timezone

from fysb import config, db
from fysb.config import log
from fysb.theme import bar, card, esc
from fysb.ui import share_cards

WIN_STATUSES = ("TP1_HIT", "TP2_HIT", "TP3_HIT", "CLOSED_WIN", "WIN")
LOSS_STATUSES = ("SL_HIT", "LOSS")
OPEN_STATUSES = ("ACTIVE", "RUNNING", "PENDING")


def _cutoff_iso(days: int) -> str:
    return (datetime.now(timezone.utc) - timedelta(days=max(1, int(days)))).isoformat()


def build_digest_data(days: int = 7) -> dict:
    """Aggregate real signal performance metrics from the live database."""
    days = max(1, int(days))
    since = _cutoff_iso(days)
    with db.conn() as c:
        rows = [dict(r) for r in c.execute(
            "SELECT id, symbol, direction, strategy, timeframe, status, pips_result, score, created_at "
            "FROM signals WHERE created_at>=? ORDER BY id DESC",
            (since,),
        ).fetchall()]

    total = len(rows)
    wins = 0
    losses = 0
    open_c = 0
    net_pips = 0.0
    by_strat: dict[str, dict] = {}
    by_sym: dict[str, dict] = {}

    for r in rows:
        st = str(r.get("status") or "").upper()
        pips = float(r.get("pips_result") or 0.0)
        strat = str(r.get("strategy") or "MANUAL").upper()
        sym = str(r.get("symbol") or "XAUUSD").upper()

        is_win = st in WIN_STATUSES or (pips > 0 and st not in LOSS_STATUSES and st not in OPEN_STATUSES)
        is_loss = st in LOSS_STATUSES or (pips < 0 and st not in WIN_STATUSES and st not in OPEN_STATUSES)
        if is_win:
            wins += 1
        elif is_loss:
            losses += 1
        elif st in OPEN_STATUSES:
            open_c += 1
        net_pips += pips

        s_entry = by_strat.setdefault(strat, {"strategy": strat, "total": 0, "wins": 0, "losses": 0, "pips": 0.0})
        s_entry["total"] += 1
        if is_win:
            s_entry["wins"] += 1
        elif is_loss:
            s_entry["losses"] += 1
        s_entry["pips"] += pips

        m_entry = by_sym.setdefault(sym, {"symbol": sym, "total": 0, "wins": 0, "pips": 0.0})
        m_entry["total"] += 1
        if is_win:
            m_entry["wins"] += 1
        m_entry["pips"] += pips

    decided = wins + losses
    win_rate = (wins / decided * 100.0) if decided > 0 else 0.0

    top_strategies = []
    for v in by_strat.values():
        d_s = v["wins"] + v["losses"]
        v["win_rate"] = (v["wins"] / d_s * 100.0) if d_s > 0 else 0.0
        v["pips"] = round(v["pips"], 1)
        top_strategies.append(v)
    top_strategies.sort(key=lambda x: (x["pips"], x["win_rate"], x["total"]), reverse=True)

    top_symbols = []
    for v in by_sym.values():
        v["pips"] = round(v["pips"], 1)
        top_symbols.append(v)
    top_symbols.sort(key=lambda x: (x["pips"], x["total"]), reverse=True)

    best_strategy = top_strategies[0]["strategy"] if top_strategies else "—"
    period_ar = f"التقرير الأسبوعي الفاخر (آخر {days} أيام)" if days <= 10 else f"التقرير الشهري الفاخر (آخر {days} يوماً)"

    return {
        "days": days,
        "period_ar": period_ar,
        "generated_at": config.now(),
        "total": total,
        "wins": wins,
        "losses": losses,
        "open": open_c,
        "win_rate": round(win_rate, 1),
        "net_pips": round(net_pips, 1),
        "best_strategy": best_strategy,
        "top_strategies": top_strategies[:5],
        "top_symbols": top_symbols[:5],
    }


def render_digest_caption(d: dict) -> str:
    """Luxury Arabic executive Telegram HTML caption for the digest."""
    wr = float(d.get("win_rate") or 0.0)
    net = float(d.get("net_pips") or 0.0)
    sign = "+" if net >= 0 else ""

    summary = "\n".join([
        f"📊 <b>ملخص الأداء الفعلي الموثّق</b>",
        f"▎📡 إجمالي الإشارات: <b>{d['total']}</b>  ·  مفتوحة: <b>{d['open']}</b>",
        f"▎✅ رابحة: <b>{d['wins']}</b>  ·  ❌ وقف: <b>{d['losses']}</b>",
        f"▎🎯 نسبة النجاح: <b>{wr:.1f}%</b>  {bar(int(round(wr)), 100)}",
        f"▎💎 صافي النقاط: <b>{sign}{net:.1f} نقطة</b>",
        f"▎🏆 أفضل استراتيجية: <b>{esc(d['best_strategy'])}</b>",
    ])

    medals = ("🥇", "🥈", "🥉", "4️⃣", "5️⃣")
    strat_lines = ["🧠 <b>ترتيب الاستراتيجيات</b>"]
    if d.get("top_strategies"):
        for i, s in enumerate(d["top_strategies"][:5]):
            sp = float(s.get("pips") or 0.0)
            strat_lines.append(
                f"▎{medals[i]} <b>{esc(s['strategy'])}</b> — "
                f"نجاح <b>{s['win_rate']:.0f}%</b> · <b>{'+' if sp >= 0 else ''}{sp:.1f}p</b> ({s['total']} إشارة)"
            )
    else:
        strat_lines.append("▎لا توجد إشارات مغلقة خلال هذه الفترة بعد.")

    sym_lines = ["🔥 <b>أقوى الأصول أداءً</b>"]
    if d.get("top_symbols"):
        for s in d["top_symbols"][:4]:
            sp = float(s.get("pips") or 0.0)
            sym_lines.append(f"▎• <b>{esc(s['symbol'])}</b>: <b>{'+' if sp >= 0 else ''}{sp:.1f} نقطة</b> ({s['total']} إشارة)")
    else:
        sym_lines.append("▎—")

    return "\n".join([
        f"👑 <b>{esc(config.BRAND)} — {esc(d['period_ar'])}</b>",
        "━━━━━━━━━━━━━━━━━━━━━",
        card(summary),
        card("\n".join(strat_lines)),
        card("\n".join(sym_lines)),
        "<i>✨ تقرير آلي موثّق بالكامل من قاعدة البيانات الحية</i>",
    ])


def build_digest_bundle(days: int = 7) -> tuple[dict, str, bytes | None]:
    d = build_digest_data(days=days)
    cap = render_digest_caption(d)
    png = share_cards.render_performance_digest_card(d)
    return d, cap, png


async def publish_digest(bot, days: int = 7, include_channels: bool = True, notify_admin: bool = True) -> dict:
    """Send the Executive Performance Digest (PNG + luxury Arabic caption) to Admin & Channels."""
    d, cap, png = build_digest_bundle(days=days)
    sent_targets: list[str] = []
    errors: list[str] = []

    targets: list[int | str] = []
    if notify_admin and config.ADMIN_ID:
        targets.append(config.ADMIN_ID)
    if include_channels:
        with db.conn() as c:
            for r in c.execute("SELECT chat_id FROM broadcast_channels WHERE enabled=1").fetchall():
                cid = str(r["chat_id"] or "").strip()
                if cid and cid not in [str(t) for t in targets]:
                    targets.append(cid)

    for t in targets:
        try:
            if png and len(cap) <= 1020:
                await bot.send_photo(chat_id=t, photo=io.BytesIO(png), caption=cap, parse_mode="HTML")
            elif png:
                await bot.send_photo(
                    chat_id=t,
                    photo=io.BytesIO(png),
                    caption=f"👑 <b>{esc(config.BRAND)} — {esc(d['period_ar'])}</b>",
                    parse_mode="HTML",
                )
                await bot.send_message(chat_id=t, text=cap, parse_mode="HTML")
            else:
                await bot.send_message(chat_id=t, text=cap, parse_mode="HTML")
            sent_targets.append(str(t))
        except Exception as e:
            errors.append(f"{t}: {e}")
            log.warning("digest send failed target=%s err=%s", t, e)

    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    db.set_setting("last_weekly_digest_day" if days <= 10 else "last_monthly_digest_day", today)
    return {"ok": True, "days": days, "sent": len(sent_targets), "errors": len(errors), "stats": d}
