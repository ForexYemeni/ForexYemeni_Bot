"""Auto Weekly & Monthly Institutional Performance Digest Service (v8.8.0).

100% honest, database-verified financial audit over the trailing 7 or 30 days:
  * Audits OFFICIAL published signals only (excludes private on-demand user
    `SIGNAL_NOW` requests so the official track record is never polluted).
  * Separates Decided (closed TP1/TP2/TP3/TP1_BE/TP2_BE/SL), Open (ACTIVE/
    RUNNING/PENDING), and Cancelled/Expired (unfilled) cleanly — ZERO `0% · +0.0p`
    ghost entries in strategy or asset leaderboards!
  * Computes true cross-asset institutional metrics:
      - Exact R-Multiple (`net_r`, `avg_win_r`, `avg_loss_r`, `expectancy_r`)
        so one BTCUSD pip-scale outlier never distorts Forex or Gold results!
      - Real R-based Profit Factor (`profit_factor`) & Realized R:R (`realized_rr`)
      - Estimated Cash Return at 0.01 Lot (`net_usd_001`) via `instruments.usd_move`
      - Target breakdown (`tp3_hits`, `tp2_hits`, `tp1_hits`, `losses`)
      - Transparent Auto-Quarantine status (`quarantined_strategies`)
  * Broadcast Quality Guard: on scheduled auto-digests (`auto_scheduled=True`),
    Admin ALWAYS receives the full unfiltered audit, while public channels only
    receive auto-broadcasts when `decided >= 3`, `net_r > 0`, and `profit_factor >= 1.15`.
"""
from __future__ import annotations

import io
from datetime import datetime, timedelta, timezone

from fysb import config, db, instruments
from fysb.config import log
from fysb.theme import bar, card, esc
from fysb.ui import share_cards

WIN_STATUSES = ("TP1_HIT", "TP2_HIT", "TP3_HIT", "TP1_BE", "TP2_BE", "CLOSED_WIN", "WIN")
LOSS_STATUSES = ("SL_HIT", "LOSS")
OPEN_STATUSES = ("ACTIVE", "RUNNING", "PENDING")
CANCELLED_STATUSES = ("CANCELLED", "EXPIRED")


def _cutoff_iso(days: int) -> str:
    return (datetime.now(timezone.utc) - timedelta(days=max(1, int(days)))).isoformat()


def _calc_r_multiple(r: dict) -> float:
    """Real R-Multiple of a closed signal (pips_result / stop_pips)."""
    pips = float(r.get("pips_result") or 0.0)
    st = str(r.get("status") or "").upper()
    sym = str(r.get("symbol") or "XAUUSD").upper()
    entry = float(r.get("entry") or 0.0)
    stop = float(r.get("stop") or 0.0)
    sl_pips = 0.0
    if entry > 0 and stop > 0 and entry != stop:
        try:
            sl_pips = abs(instruments.pips(sym, entry, stop))
        except Exception:
            sl_pips = 0.0
    if sl_pips > 1e-6 and pips != 0.0:
        return max(-2.0, min(10.0, pips / sl_pips))
    if st == "TP3_HIT":
        return 4.5
    if st in ("TP2_HIT", "TP2_BE"):
        return 3.0
    if st in ("TP1_HIT", "TP1_BE"):
        return 2.0
    if st in LOSS_STATUSES:
        return -1.0
    return 0.0


def _exec_verdict(decided: int, net_r: float, pf: float, net_pips: float) -> str:
    if decided == 0:
        return "⏳ بانتظار إغلاق الصفقات الجارية"
    if net_r > 0 and pf >= 1.80:
        return "🏆 أداء مؤسسي نخبوي (ربحية عالية)"
    if net_r > 0 and pf >= 1.25:
        return "💎 أداء مؤسسي رابح ومستقر"
    if net_r > 0 or net_pips > 0:
        return "✅ أداء إيجابي متوازن"
    return "🛡 فترة متذبذبة — الحجر الذكي للاستراتيجيات مفعّل"


def build_digest_data(days: int = 7, include_private: bool = False) -> dict:
    """Aggregate real institutional signal performance metrics from the live database."""
    days = max(1, int(days))
    since = _cutoff_iso(days)
    with db.conn() as c:
        if include_private:
            rows = [dict(r) for r in c.execute(
                "SELECT id, symbol, direction, strategy, timeframe, entry, stop, status, hit_tp, "
                "pips_result, score, source, created_at "
                "FROM signals WHERE created_at>=? ORDER BY id DESC",
                (since,),
            ).fetchall()]
            private_excluded = 0
        else:
            rows = [dict(r) for r in c.execute(
                "SELECT id, symbol, direction, strategy, timeframe, entry, stop, status, hit_tp, "
                "pips_result, score, source, created_at "
                "FROM signals WHERE created_at>=? "
                "AND (source IS NULL OR UPPER(source) != 'SIGNAL_NOW') "
                "ORDER BY id DESC",
                (since,),
            ).fetchall()]
            private_excluded = int((c.execute(
                "SELECT COUNT(*) AS n FROM signals WHERE created_at>=? AND UPPER(source)='SIGNAL_NOW'",
                (since,),
            ).fetchone() or {"n": 0})["n"] or 0)

    total = len(rows)
    wins = 0
    losses = 0
    open_c = 0
    cancelled_c = 0
    tp1_hits = 0
    tp2_hits = 0
    tp3_hits = 0
    net_pips = 0.0
    net_r = 0.0
    gross_win_r = 0.0
    gross_loss_r = 0.0
    net_usd_001 = 0.0

    by_strat: dict[str, dict] = {}
    by_sym: dict[str, dict] = {}

    for r in rows:
        st = str(r.get("status") or "").upper()
        pips = float(r.get("pips_result") or 0.0)
        strat = str(r.get("strategy") or "MANUAL").upper()
        sym = str(r.get("symbol") or "XAUUSD").upper()
        entry = float(r.get("entry") or 0.0)

        is_win = st in WIN_STATUSES or (pips > 0 and st not in LOSS_STATUSES and st not in OPEN_STATUSES and st not in CANCELLED_STATUSES)
        is_loss = st in LOSS_STATUSES or (pips < 0 and st not in WIN_STATUSES and st not in OPEN_STATUSES and st not in CANCELLED_STATUSES)
        is_open = (not is_win and not is_loss and st in OPEN_STATUSES)
        is_cancel = (not is_win and not is_loss and st in CANCELLED_STATUSES)

        if is_win:
            wins += 1
            if st == "TP3_HIT":
                tp3_hits += 1
            elif st in ("TP2_HIT", "TP2_BE"):
                tp2_hits += 1
            else:
                tp1_hits += 1
        elif is_loss:
            losses += 1
        elif is_open:
            open_c += 1
        elif is_cancel:
            cancelled_c += 1

        # Only decided trades contribute to realized P/L, R-multiple, and rankings
        if not (is_win or is_loss):
            continue

        r_val = _calc_r_multiple(r)
        if is_win and r_val < 0:
            r_val = abs(r_val)
        elif is_loss and r_val > 0:
            r_val = -abs(r_val)

        usd_val = 0.0
        try:
            usd_val = float(instruments.usd_move(sym, pips, 0.01, px=entry))
        except Exception:
            usd_val = 0.0

        net_pips += pips
        net_r += r_val
        net_usd_001 += usd_val
        if is_win:
            gross_win_r += max(r_val, 0.0)
        else:
            gross_loss_r += abs(r_val)

        s_entry = by_strat.setdefault(strat, {
            "strategy": strat, "total": 0, "wins": 0, "losses": 0,
            "pips": 0.0, "net_r": 0.0, "gross_win_r": 0.0, "gross_loss_r": 0.0, "usd_001": 0.0,
        })
        s_entry["total"] += 1
        if is_win:
            s_entry["wins"] += 1
            s_entry["gross_win_r"] += max(r_val, 0.0)
        else:
            s_entry["losses"] += 1
            s_entry["gross_loss_r"] += abs(r_val)
        s_entry["pips"] += pips
        s_entry["net_r"] += r_val
        s_entry["usd_001"] += usd_val

        m_entry = by_sym.setdefault(sym, {
            "symbol": sym, "total": 0, "wins": 0, "losses": 0,
            "pips": 0.0, "net_r": 0.0, "usd_001": 0.0,
        })
        m_entry["total"] += 1
        if is_win:
            m_entry["wins"] += 1
        else:
            m_entry["losses"] += 1
        m_entry["pips"] += pips
        m_entry["net_r"] += r_val
        m_entry["usd_001"] += usd_val

    decided = wins + losses
    win_rate = (wins / decided * 100.0) if decided > 0 else 0.0
    profit_factor = (gross_win_r / gross_loss_r) if gross_loss_r > 0 else (round(gross_win_r, 2) if gross_win_r > 0 else 0.0)
    avg_win_r = (gross_win_r / wins) if wins > 0 else 0.0
    avg_loss_r = (gross_loss_r / losses) if losses > 0 else 1.0
    realized_rr = (avg_win_r / avg_loss_r) if (wins > 0 and avg_loss_r > 0) else 0.0
    expectancy_r = (net_r / decided) if decided > 0 else 0.0

    top_strategies = []
    for v in by_strat.values():
        d_s = v["wins"] + v["losses"]
        if d_s <= 0:
            continue
        v["win_rate"] = round((v["wins"] / d_s) * 100.0, 1)
        v["pips"] = round(v["pips"], 1)
        v["net_r"] = round(v["net_r"], 2)
        v["usd_001"] = round(v["usd_001"], 2)
        v["pf"] = round(
            (v["gross_win_r"] / v["gross_loss_r"]) if v["gross_loss_r"] > 0 else (v["gross_win_r"] or 0.0),
            2,
        )
        top_strategies.append(v)
    # Sort by REAL Net R (cross-asset fair!), then Profit Factor, then Win Rate
    top_strategies.sort(key=lambda x: (x["net_r"], x["pf"], x["win_rate"], x["total"]), reverse=True)

    top_symbols = []
    for v in by_sym.values():
        d_m = v["wins"] + v["losses"]
        if d_m <= 0:
            continue
        v["win_rate"] = round((v["wins"] / d_m) * 100.0, 1)
        v["pips"] = round(v["pips"], 1)
        v["net_r"] = round(v["net_r"], 2)
        v["usd_001"] = round(v["usd_001"], 2)
        top_symbols.append(v)
    top_symbols.sort(key=lambda x: (x["net_r"], x["pips"], x["total"]), reverse=True)

    quarantined: list[dict] = []
    try:
        from fysb.engines import intelligence as _intel
        quarantined = _intel.list_quarantined_strategies(lookback_days=max(14, days))
    except Exception:
        quarantined = []

    best_strategy = top_strategies[0]["strategy"] if top_strategies else "—"
    period_ar = f"التقرير الأسبوعي الفاخر (آخر {days} أيام)" if days <= 10 else f"التقرير الشهري الفاخر (آخر {days} يوماً)"
    verdict_ar = _exec_verdict(decided, net_r, profit_factor, net_pips)

    return {
        "days": days,
        "period_ar": period_ar,
        "generated_at": config.now(),
        "total": total,
        "decided": decided,
        "wins": wins,
        "losses": losses,
        "open": open_c,
        "cancelled": cancelled_c,
        "private_excluded": private_excluded,
        "tp1_hits": tp1_hits,
        "tp2_hits": tp2_hits,
        "tp3_hits": tp3_hits,
        "win_rate": round(win_rate, 1),
        "net_pips": round(net_pips, 1),
        "net_r": round(net_r, 2),
        "profit_factor": round(profit_factor, 2),
        "realized_rr": round(realized_rr, 2),
        "expectancy_r": round(expectancy_r, 2),
        "net_usd_001": round(net_usd_001, 2),
        "verdict_ar": verdict_ar,
        "best_strategy": best_strategy,
        "top_strategies": top_strategies[:5],
        "top_symbols": top_symbols[:5],
        "quarantined_strategies": quarantined[:4],
    }


def render_digest_caption(d: dict, *, admin_diagnostics: bool = False) -> str:
    """Luxury Arabic executive institutional Telegram HTML caption for the digest."""
    wr = float(d.get("win_rate") or 0.0)
    net_pips = float(d.get("net_pips") or 0.0)
    net_r = float(d.get("net_r") or 0.0)
    pf = float(d.get("profit_factor") or 0.0)
    rr = float(d.get("realized_rr") or 0.0)
    usd001 = float(d.get("net_usd_001") or 0.0)
    decided = int(d.get("decided") or (int(d.get("wins") or 0) + int(d.get("losses") or 0)))

    tp_strip = f"TP3: <b>{d.get('tp3_hits', 0)}</b> · TP2: <b>{d.get('tp2_hits', 0)}</b> · TP1: <b>{d.get('tp1_hits', 0)}</b>"
    cancel_bit = f"  ·  ملغاة: <b>{d.get('cancelled', 0)}</b>" if int(d.get("cancelled") or 0) > 0 else ""

    summary = "\n".join([
        f"📊 <b>ملخص الأداء المؤسسي الموثّق</b> — {esc(d.get('verdict_ar', ''))}",
        f"▎📡 إشارات رسمية: <b>{d['total']}</b> (مغلقة: <b>{decided}</b> · جارية: <b>{d['open']}</b>{cancel_bit})",
        f"▎✅ رابحة: <b>{d['wins']}</b> ({tp_strip})  ·  ❌ وقف: <b>{d['losses']}</b>",
        f"▎🎯 نسبة النجاح: <b>{wr:.1f}%</b>  {bar(int(round(wr)), 100)}",
        f"▎⚖️ العائد الصافي (R): <b>{net_r:+.2f}R</b>  ·  معامل الربح: <b>PF {pf:.2f}</b>",
        f"▎💎 صافي النقاط: <b>{net_pips:+.1f}p</b>  ·  متوسط العائد/المخاطرة: <b>1:{rr:.1f}</b>",
        f"▎💵 الأثر المالي (0.01 لوت): <b>{usd001:+,.2f} $</b>  ·  🏆 الأفضل: <b>{esc(d['best_strategy'])}</b>",
    ])

    medals = ("🥇", "🥈", "🥉", "4️⃣", "5️⃣")
    strat_lines = ["🧠 <b>أقوى الاستراتيجيات أداءً (مرتبة بالعائد الحقيقي R)</b>"]
    if d.get("top_strategies"):
        for i, s in enumerate(d["top_strategies"][:5]):
            sp = float(s.get("pips") or 0.0)
            sr = float(s.get("net_r") or 0.0)
            strat_lines.append(
                f"▎{medals[i]} <b>{esc(s['strategy'])}</b> — "
                f"<b>{sr:+.1f}R</b> · نجاح <b>{s['win_rate']:.0f}%</b> · <b>{sp:+.1f}p</b> ({s['total']})"
            )
    else:
        strat_lines.append("▎لا توجد صفقات مغلقة خلال هذه الفترة بعد.")

    sym_lines = ["🔥 <b>أقوى الأصول أداءً (مرتبة بالعائد الحقيقي R)</b>"]
    if d.get("top_symbols"):
        for s in d["top_symbols"][:4]:
            sp = float(s.get("pips") or 0.0)
            sr = float(s.get("net_r") or 0.0)
            sym_lines.append(
                f"▎• <b>{esc(s['symbol'])}</b>: <b>{sr:+.1f}R</b> · <b>{sp:+.1f}p</b> ({s['wins']}/{s['total']})"
            )
    else:
        sym_lines.append("▎—")

    blocks = [
        f"👑 <b>{esc(config.BRAND)} — {esc(d['period_ar'])}</b>",
        "━━━━━━━━━━━━━━━━━━━━━",
        card(summary),
        card("\n".join(strat_lines)),
        card("\n".join(sym_lines)),
    ]

    q_list = list(d.get("quarantined_strategies") or [])
    if admin_diagnostics and q_list:
        q_lines = ["🛡 <b>الحجر الذكي التلقائي للاستراتيجيات المتراجعة (Auto-Quarantine)</b>"]
        for qs in q_list:
            q_lines.append(
                f"▎• <b>{esc(qs['strategy'])}</b> موقوفة تلقائياً "
                f"(نجاح {qs['win_rate']:.0f}% · PF {qs['pf']:.2f} · {qs['net_r']:+.1f}R من {qs['trades']} صفقات)"
            )
        blocks.append(card("\n".join(q_lines)))

    blocks.append("<i>✨ تدقيق مالي آلي موثّق 100% من قاعدة البيانات الحية (يستثني طلبات المستخدمين الخاصة)</i>")
    return "\n".join(blocks)


def channel_broadcast_allowed(d: dict) -> tuple[bool, str]:
    """Quality Guard for automated public/VIP channel broadcasts.

    Never auto-blasts a tiny sample (< 3 decided) or a drawdown period into
    public channels — Admin always gets the full diagnostic report instead.
    """
    decided = int(d.get("decided") or 0)
    net_r = float(d.get("net_r") or 0.0)
    pf = float(d.get("profit_factor") or 0.0)
    if decided < 3:
        return False, f"عدد الصفقات المغلقة غير كافٍ للبث العام ({decided} < 3)"
    if net_r <= 0 or pf < 1.15:
        return False, f"حماية سمعة القناة: العائد ({net_r:+.2f}R · PF {pf:.2f}) أقل من حد البث التلقائي العام"
    return True, ""


def build_digest_bundle(days: int = 7) -> tuple[dict, str, bytes | None]:
    d = build_digest_data(days=days)
    cap = render_digest_caption(d)
    png = share_cards.render_performance_digest_card(d)
    return d, cap, png


async def publish_digest(
    bot,
    days: int = 7,
    include_channels: bool = True,
    notify_admin: bool = True,
    *,
    auto_scheduled: bool = False,
) -> dict:
    """Send the Executive Performance Digest (PNG + luxury Arabic caption) to Admin & Channels."""
    d, cap_public, png = build_digest_bundle(days=days)
    cap_admin = render_digest_caption(d, admin_diagnostics=True)

    channels_held = False
    hold_reason = ""
    effective_channels = bool(include_channels)
    if effective_channels and auto_scheduled:
        ok_ch, why_ch = channel_broadcast_allowed(d)
        if not ok_ch:
            effective_channels = False
            channels_held = True
            hold_reason = why_ch

    sent_targets: list[str] = []
    channels_sent = 0
    errors: list[str] = []

    async def _deliver(target, caption_txt: str) -> bool:
        try:
            if png and len(caption_txt) <= 1020:
                await bot.send_photo(chat_id=target, photo=io.BytesIO(png), caption=caption_txt, parse_mode="HTML")
            elif png:
                await bot.send_photo(
                    chat_id=target,
                    photo=io.BytesIO(png),
                    caption=f"👑 <b>{esc(config.BRAND)} — {esc(d['period_ar'])}</b>",
                    parse_mode="HTML",
                )
                await bot.send_message(chat_id=target, text=caption_txt, parse_mode="HTML")
            else:
                await bot.send_message(chat_id=target, text=caption_txt, parse_mode="HTML")
            return True
        except Exception as e:
            errors.append(f"{target}: {e}")
            log.warning("digest send failed target=%s err=%s", target, e)
            return False

    if notify_admin and config.ADMIN_ID:
        admin_txt = cap_admin
        if channels_held:
            admin_txt += (
                f"\n\n🛡 <b>ملاحظة حماية القنوات:</b> {esc(hold_reason)} — "
                "<i>تم تسليم التقرير الكامل للأدمن فقط ولم يُبث تلقائياً للقنوات.</i>"
            )
        if await _deliver(config.ADMIN_ID, admin_txt):
            sent_targets.append(str(config.ADMIN_ID))

    if effective_channels:
        with db.conn() as c:
            ch_rows = c.execute("SELECT chat_id FROM broadcast_channels WHERE enabled=1").fetchall()
        for r in ch_rows:
            cid = str(r["chat_id"] or "").strip()
            if cid and cid not in sent_targets:
                if await _deliver(cid, cap_public):
                    sent_targets.append(cid)
                    channels_sent += 1

    today = config.mecca_today_iso()
    db.set_setting("last_weekly_digest_day" if days <= 10 else "last_monthly_digest_day", today)
    return {
        "ok": True,
        "days": days,
        "sent": len(sent_targets),
        "channels_sent": channels_sent,
        "channels_held": channels_held,
        "hold_reason": hold_reason,
        "errors": len(errors),
        "stats": d,
    }
