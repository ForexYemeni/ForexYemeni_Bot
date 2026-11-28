"""Auto Weekly & Monthly Institutional Performance Digest Service (v8.8.0).

Recorded signal-model summary (not broker-account financial audit) over the trailing 7 or 30 days:
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
    receive both profitable and losing periods when `decided >= 3`.
"""
from __future__ import annotations

import io
from datetime import datetime, timedelta, timezone

from fysb import config, db, instruments
from fysb.config import log
from fysb.theme import bar, card, esc
from fysb.ui import share_cards

WIN_STATUSES = ("TP1_HIT", "TP2_HIT", "TP3_HIT", "TP1_BE", "TP2_BE", "CLOSED_WIN", "WIN", "LOCKED_50")
LOSS_STATUSES = ("SL_HIT", "LOSS")
OPEN_STATUSES = ("ACTIVE", "RUNNING", "PENDING")
CANCELLED_STATUSES = ("CANCELLED", "EXPIRED")


def cutoff_iso(days: int | None) -> str | None:
    """v8.14.20 — the one period cutoff generator (public; string-comparable
    with config.now() ISO timestamps). days=None => full history (no cutoff)."""
    if days is None:
        return None
    return (datetime.now(timezone.utc) - timedelta(days=max(1, int(days)))).isoformat()


def _cutoff_iso(days: int) -> str:
    return cutoff_iso(days) or ""


def period_ar(days: int | None) -> str:
    """v8.14.20 — honest Arabic period label shared by every report surface."""
    if days is None:
        return "كل التاريخ"
    if int(days) == 1:
        return "آخر 24 ساعة"
    if int(days) <= 10:
        return f"آخر {int(days)} أيام"
    return f"آخر {int(days)} يوماً"


def slice_ar(market: str | None, strategy: str | None, timeframe: str | None) -> str:
    """v8.14.20 — the exact slice label (only non-ALL dimensions are named)."""
    parts = [str(v) for v in (market, strategy, timeframe) if v]
    return " · ".join(parts) if parts else "كل الأبعاد"



def _calc_r_multiple(r: dict) -> float:
    """Real R-Multiple of a closed signal (pips_result / stop_pips)."""
    from fysb.engines.accounting import modeled_r
    return modeled_r(r) or 0.0


def _exec_verdict(decided: int, net_r: float, pf: float, net_pips: float) -> str:
    if decided == 0:
        return "⏳ بانتظار إغلاق الصفقات الجارية"
    if net_r > 0 and pf >= 1.80:
        return "🏆 أداء مؤسسي نخبوي (ربحية عالية)"
    if net_r > 0 and pf >= 1.25:
        return "💎 أداء مؤسسي رابح ومستقر"
    if net_r > 0 or net_pips > 0:
        return "✅ أداء إيجابي متوازن"
    return "🛡 فترة سلبية/متذبذبة — دون إخفاء الخسائر"


def build_digest_data(days: int | None = 7, include_private: bool = False,
                      market: str | None = None, strategy: str | None = None,
                      timeframe: str | None = None) -> dict:
    """Aggregate real institutional signal performance metrics from the live
    database. v8.14.20 — the SAME honest engine also serves the interactive
    unified report: optional market/strategy/timeframe slice + optional period
    (days=None => full history). Every number stays a real aggregate of the
    exact requested slice — zero fabrication, zero mixed-unit sums."""
    since = cutoff_iso(days)
    slice_where, slice_params = [], []
    if market:
        slice_where.append("symbol=?")
        slice_params.append(market)
    if strategy:
        slice_where.append("strategy=?")
        slice_params.append(strategy)
    if timeframe:
        slice_where.append("timeframe=?")
        slice_params.append(timeframe)
    _slice_w = (" AND " + " AND ".join(slice_where)) if slice_where else ""
    _slice_p = list(slice_params)
    _period_w = (" AND created_at>=?" if since else "")
    _period_p = [since] if since else []
    with db.conn() as c:
        if include_private:
            rows = [dict(r) for r in c.execute(
                "SELECT id, symbol, direction, strategy, timeframe, entry, stop, status, hit_tp, "
                "pips_result, profit_50_hit, score, source, created_at, closed_at, private_user_id, accounting_model, tp1, tp2, tp3 "
                f"FROM signals WHERE 1=1{_period_w}{_slice_w} ORDER BY id DESC",
                (*_period_p, *_slice_p),
            ).fetchall()]
            private_excluded = 0
        else:
            rows = [dict(r) for r in c.execute(
                "SELECT id, symbol, direction, strategy, timeframe, entry, stop, status, hit_tp, "
                "pips_result, profit_50_hit, score, source, created_at, closed_at, private_user_id, accounting_model, tp1, tp2, tp3 "
                f"FROM signals WHERE 1=1{_period_w}{_slice_w} "
                "AND COALESCE(private_user_id,0)=0 "
                "AND COALESCE(UPPER(source),'') NOT IN ('SIGNAL_NOW','ADMIN_SIGNAL_NOW','TRUE_ANALYSIS_INSTANT','TRUE_ANALYSIS_CONFIRMED') "
                "ORDER BY id DESC",
                (*_period_p, *_slice_p),
            ).fetchall()]
            private_excluded = int((c.execute(
                "SELECT COUNT(*) AS n FROM signals WHERE 1=1"
                f"{_period_w} AND (COALESCE(private_user_id,0)>0 "
                "OR UPPER(source) IN ('SIGNAL_NOW','ADMIN_SIGNAL_NOW','TRUE_ANALYSIS_INSTANT','TRUE_ANALYSIS_CONFIRMED'))",
                (*_period_p,),
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

        from fysb.engines.accounting import is_closed
        closed = is_closed(r)
        # v8.14.19 — the Truth Ledger governs CLOSED rows: the 50-pip decree
        # (a documented threshold touch is a WIN at the threshold, never a
        # loss) + status semantics for everything else. Zero sign-guessing.
        from fysb.services import ledger as _ledger
        is_win = is_loss = False
        if closed:
            verdict, eff_pips = _ledger.closed_verdict(r)
            if verdict == "win":
                pips = eff_pips
                is_win = True
            elif verdict == "loss":
                pips = eff_pips
                is_loss = True
        is_open = (not is_win and not is_loss and st not in CANCELLED_STATUSES
                   and (not closed or st in OPEN_STATUSES + ("TP1_HIT", "TP2_HIT")))
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

        r_val = _calc_r_multiple(_ledger.effective_row(r))

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
        quarantined = _intel.list_quarantined_strategies(lookback_days=max(14, int(days or 30)))
    except Exception:
        quarantined = []

    best_strategy = top_strategies[0]["strategy"] if top_strategies else "—"
    _period_label = period_ar(days)
    _slice_label = slice_ar(market, strategy, timeframe)
    if _slice_label != "كل الأبعاد":
        # v8.14.20 — the filtered slice: short honest period + the exact slice
        _period_label = f"{_period_label} · {_slice_label}"
    else:
        # the plain digest keeps its traditional weekly/monthly wording
        _period_label = ("كل التاريخ" if days is None else
                         f"التقرير الأسبوعي (آخر {int(days)} أيام)" if int(days) <= 10
                         else f"التقرير الشهري (آخر {int(days)} يوماً)")
    verdict_ar = _exec_verdict(decided, net_r, profit_factor, net_pips)

    return {
        "days": int(days) if days else 0,
        "slice_ar": _slice_label,
        "period_ar": _period_label,
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
        f"▎⚖️ العائد النموذجي الصافي (R): <b>{net_r:+.2f}R</b>  ·  معامل الربح: <b>PF {pf:.2f}</b>",
        f"▎💎 متوسط RR المخطط: <b>1:{rr:.1f}</b>",
        f"▎💵 تقدير نظري (0.01 لوت): <b>{usd001:+,.2f} $</b>  ·  🏆 الأفضل: <b>{esc(d['best_strategy'])}</b>",
    ])

    medals = ("🥇", "🥈", "🥉", "4️⃣", "5️⃣")
    strat_lines = ["🧠 <b>أقوى الاستراتيجيات أداءً (مرتبة بالعائد النموذجي R)</b>"]
    if d.get("top_strategies"):
        for i, s in enumerate(d["top_strategies"][:5]):
            sr = float(s.get("net_r") or 0.0)
            strat_lines.append(
                f"▎{medals[i]} <b>{esc(s['strategy'])}</b> — "
                f"<b>{sr:+.1f}R</b> · نجاح <b>{s['win_rate']:.0f}%</b> ({s['total']})"
            )
    else:
        strat_lines.append("▎لا توجد صفقات مغلقة خلال هذه الفترة بعد.")

    sym_lines = ["🔥 <b>أقوى الأصول أداءً (مرتبة بالعائد النموذجي R)</b>"]
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

    blocks.append("<i>✨ ملخص نموذجي من سجلات التتبع المغلقة العامة؛ قد يجمع قياس المسافة القديم والنموذج الجزئي الجديد، دون تكاليف وسيط</i>")
    return "\n".join(blocks)


def personal_royal_digest(user_id: int, days: int = 1) -> str | None:
    """v8.14.11 — the royal PERSONAL daily summary: one paid subscriber's own
    delivered, closed trades — wins, royal locks, honest stops, net pips.
    Built ONLY from real recorded rows; returns None when the subscriber had
    zero closed activity (no vanity empty cards, no fabricated numbers)."""
    lo = _cutoff_iso(days)
    with db.conn() as c:
        rows = c.execute(
            """SELECT s.status, s.pips_result, s.symbol, s.direction, s.strategy,
               s.locked_pips, s.closed_at
               FROM signal_deliveries d JOIN signals s ON s.id=d.signal_id
               WHERE d.user_id=? AND s.closed_at>=? AND s.closed_at!=''
               ORDER BY s.closed_at DESC""",
            (int(user_id), lo)).fetchall()
    if not rows:
        return None
    wins = [r for r in rows if r["status"] in ("TP1_HIT", "TP2_HIT", "TP3_HIT", "LOCKED_50")]
    locks = [r for r in rows if r["status"] == "LOCKED_50"]
    stops = [r for r in rows if r["status"] == "SL_HIT"]
    net_pips = sum(float(r["pips_result"] or 0.0) for r in rows)
    day = config.mecca_now().strftime("%Y-%m-%d")
    lock_note = " (كل قفلة حفظت +5.00 $ بـ 0.01 لوت — لا خسارة أبداً)" if locks else ""
    lines = [
        "👑 <b><u>ملخصك الشخصي</u></b> — آخر {} يوم".format(days),
        "🗓 {} — صفقاتك المغلقة الموثقة فقط".format(day),
        "<blockquote>"
        "▎📊 صفقاتك: <b>{}</b> — 🏆 رابحة <b>{}</b> · 🛑 وقف <b>{}</b>\n"
        "▎🏅 قفلات ملكية: <b>{}</b>{}\n"
        "▎📈 صافي النتيجة المسجلة: <b>{:+.1f} pips</b>"
        "</blockquote>".format(len(rows), len(wins), len(stops), len(locks), lock_note, net_pips),
    ]
    icons = {"TP3_HIT": "👑", "TP2_HIT": "🥈", "TP1_HIT": "🥇", "LOCKED_50": "🏅",
             "SL_HIT": "🛑", "TP1_BE": "🛡", "TP2_BE": "🛡"}
    for r in rows[:8]:
        lines.append(
            "▎{} <b>{}</b> {} <b>{:+.1f} pips</b> — {}".format(
                icons.get(r["status"], "•"), esc(str(r["symbol"])),
                "🟢" if r["direction"] == "BUY" else "🔴",
                float(r["pips_result"] or 0.0), esc(str(r["closed_at"] or "").replace("T", " ")[:16])))
    if net_pips >= 0:
        lines.append("✨ <i>استمر بنفس الالتزام — القرار المؤسسي يُبنى على وثيقة موثقة.</i>")
    else:
        lines.append("🛡 <i>الخسارة المحدودة جزء من القانون — الوقف الحقيقي حماية لمسارك.</i>")
    return "\n".join(lines)


def channel_broadcast_allowed(d: dict) -> tuple[bool, str]:
    """Minimum disclosed sample only; NEVER suppress losing periods."""
    decided = int(d.get("decided") or 0)
    if decided < 3:
        return False, f"عدد الصفقات المغلقة غير كافٍ للبث العام ({decided} < 3)"
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
