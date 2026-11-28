"""Admin trading sections: Signals, Engine, Strategies, Modes, Risk, Markets,
Timeframes, Brokers, Accounts, Demo, Live — everything wired to real logic."""
import asyncio
import json
from datetime import timedelta

from telegram.ext import ContextTypes

from fysb import afn, config, db
from fysb.engines import market_data
from fysb.engines.strategies import VERIFIABLE
from fysb.handlers.common import render, safe
from fysb.handlers.inputs import BROKER_PRESETS, broker_test_connectivity, clear_flows
from fysb.handlers.user_pages import _safe_int
from fysb.services import audit as audit_log
from fysb.theme import ICON, ar_result, ar_reason, ar_run_line, esc, human_dt
from fysb import theme
from fysb.ui.cards import render_signal_card, render_signal_compact, signal_creator_label, signal_row_style
from fysb.ui import keyboards as kb


# --- Signals management ----------------------------------------------------------
# v7.5.0 — Luxury Executive Signals Command Center:
# semantic color-coded non-clipping rows + full creator attribution + achieved
# targets/pips ledger right inside the header card.
SIG_CATS = {
    "pending": ("⏳ بانتظار التنفيذ", "ACTIVE", "بانتظار"),
    "running": ("🚀 قيد التنفيذ في السوق", "RUNNING", "قيد التنفيذ"),
    "partial": ("🏁 أهداف جزئية", "TP1_HIT,TP2_HIT", "أهداف جزئية"),
    "sl": ("🛑 ضربت الوقف", "SL_HIT", "ضربت الوقف"),
    "wins": ("👑 حققت الأهداف", "TP3_HIT", "حققت الأهداف"),
    "dead": ("🧹 ملغاة/منتهية", "CANCELLED,EXPIRED", "ملغاة/منتهية"),
    "all": ("🗂 الكل", "", "الكل"),
}


def _signals_page_body(statuses: tuple, page: int, size: int = 8, scope: str = "all") -> tuple[int, list, int, dict, list[str]]:
    from fysb.services import signal_archive as _sa
    from fysb.theme import is_signal_closed, signal_status_ar, strategy_name, tf_name
    total, rows, summary = _sa.admin_signals_page(scope=scope, statuses=statuses, page=page, size=size)
    # Batch-load creator usernames/names + demo counts in 1 lightweight checkout
    cids = sorted({int(r.get("created_by") or 0) for r in rows if int(r.get("created_by") or 0) > 0})
    sids = [int(r["id"]) for r in rows]
    users_map: dict[int, dict] = {}
    demo_cnt: dict[int, int] = {}
    if cids or sids:
        with db.conn() as c:
            if cids:
                ph = ",".join("?" * len(cids))
                for u in c.execute(f"SELECT user_id, username, full_name FROM users WHERE user_id IN ({ph})", tuple(cids)).fetchall():
                    users_map[int(u["user_id"])] = dict(u)
            if sids:
                ph = ",".join("?" * len(sids))
                for d in c.execute(f"SELECT signal_id, COUNT(*) n FROM demo_positions WHERE signal_id IN ({ph}) GROUP BY signal_id", tuple(sids)).fetchall():
                    demo_cnt[int(d["signal_id"])] = int(d["n"])

    rows_kb = [[kb.btn(render_signal_compact(r), f"a:signalview:{r['id']}", style=signal_row_style(r))] for r in rows]
    ledger_lines: list[str] = []
    for r in rows:
        seq = r.get("seq") or r["id"]
        d_ic = "🟢" if str(r.get("direction")).upper() == "BUY" else "🔴"
        d_ar = "شراء" if str(r.get("direction")).upper() == "BUY" else "بيع"
        pips = float(r.get("pips_result") or 0.0)
        pips_txt = f" · <b>{pips:+.1f}p</b>" if pips else ""
        creator = signal_creator_label(r, users_map)
        dc = demo_cnt.get(int(r["id"]), 0)
        dc_txt = f" · 🎮 {dc}" if dc else ""
        st_lbl = signal_status_ar(r)
        ledger_lines.append(
            f"▎<b>#{seq}</b> {d_ic} <b>{esc(r['symbol'])}</b> {d_ar} · {esc(tf_name(r['timeframe']))} · "
            f"{esc(strategy_name(r['strategy']))} ({int(r.get('score') or 0)}/100)\n"
            f"   ↳ {st_lbl}{pips_txt}{dc_txt} · {esc(creator)}"
        )
    return total, rows_kb, len(rows), summary, ledger_lines


async def _render_signals_center(q, bot, cat: str = "all", page: int = 0, scope: str = "today"):
    from fysb.services import signal_archive as _sa
    title, st_csv, _tab = SIG_CATS.get(cat, SIG_CATS["all"])
    statuses = tuple(s for s in st_csv.split(",") if s)
    size = 8
    counts = await afn.run(_sa.admin_scope_counts)
    total, rows_kb, shown, summary, ledger_lines = await afn.run(_signals_page_body, statuses, page, size, scope)
    total_pages = max(1, (total + size - 1) // size)

    # Pagination row
    nav_row = []
    if page > 0:
        nav_row.append(kb.btn("◀️ السابق", f"a:sigday:{scope}:{cat}:{page - 1}", style="primary"))
    if (page + 1) * size < total:
        nav_row.append(kb.btn("التالي ▶️", f"a:sigday:{scope}:{cat}:{page + 1}", style="primary"))
    if nav_row:
        rows_kb.append(nav_row)

    # Row 0: Royal Date Scope Tabs (Today / Yesterday / By Date / All)
    day_iso = _sa.resolve_scope_date(scope)
    is_today = scope == "today"
    is_yest = scope == "yesterday"
    is_custom_date = bool(day_iso) and not is_today and not is_yest
    is_all = scope == "all"
    date_row = [
        kb.btn(("◉ " if is_today else "") + f"اليوم ({counts['today']})", f"a:sigday:today:{cat}:0",
               style="success" if is_today else "primary"),
        kb.btn(("◉ " if is_yest else "") + f"أمس ({counts['yesterday']})", f"a:sigday:yesterday:{cat}:0",
               style="success" if is_yest else "primary"),
        kb.btn(("◉ " if is_custom_date else "") + f"تاريخ ({counts['days']})", "a:sigdates:0",
               style="success" if is_custom_date else "primary"),
        kb.btn(("◉ " if is_all else "") + f"الكل ({counts['total']})", f"a:sigday:all:{cat}:0",
               style="success" if is_all else "primary"),
    ]
    # Category pills bound to active date scope
    cat_row = [
        kb.btn(("◉ " if cat == key else "") + SIG_CATS[key][2], f"a:sigday:{scope}:{key}:0",
               style="success" if cat == key else "primary")
        for key in list(SIG_CATS)[:4]
    ]
    cat_row2 = [
        kb.btn(("◉ " if cat == key else "") + SIG_CATS[key][2], f"a:sigday:{scope}:{key}:0",
               style="success" if cat == key else "primary")
        for key in list(SIG_CATS)[4:]
    ]
    rows_kb.insert(0, date_row)
    rows_kb.insert(1, cat_row)
    rows_kb.insert(2, cat_row2)

    pl_cb = f"a:sigpl:{day_iso}" if is_custom_date else ("a:sigpl" if is_all else f"a:sigpl:{scope}")
    rows_kb.append([kb.btn("💰 سجل الربح والخسارة", "a:sigpl", style="success"),
                    kb.btn("📊 محاسبة اليوم", pl_cb, style="primary")] if not is_all
                   else [kb.btn("💰 سجل الربح والخسارة — كل الأدوات", "a:sigpl", style="success")])
    rows_kb.append([kb.btn("⚡ إشارة الآن", "a:signow", style="success"),
                    kb.btn("🎯 أسواق إشاراتي", "a:adminmkts", style="primary")])
    rows_kb.append([kb.btn("➕ إشارة يدوية", "a:newsignal", style="success"),
                    kb.btn("🔄 فحص المفتوحة", "a:tracknow", style="primary")])
    rows_kb.append([kb.btn("🧹 تنظيف المنتهية", "a:signalpurge", style="danger")])

    if is_today:
        scope_label = f"{_sa.ar_date_label(_sa.today_iso())} · {title}"
    elif is_yest:
        scope_label = f"{_sa.ar_date_label(_sa.yesterday_iso())} · {title}"
    elif is_custom_date:
        scope_label = f"{_sa.ar_date_label(day_iso)} · {title}"
    else:
        scope_label = f"🗂 جميع التواريخ · {title}"

    extra = ["▎🎨 <b>الألوان:</b> 🟢 حققت أهداف · 🔵 نشطة/سوق · 🔴 وقف/منتهية — اضغط أي إشارة للتفاصيل والشارت والتجريبي."]
    if shown == 0:
        if is_today:
            extra.append("▎✨ لا توجد إشارات مطابقة اليوم حتى الآن — جرّب «أمس» أو «تاريخ» أو «الكل».")
        else:
            extra.append("▎لا توجد إشارات في هذا النطاق.")

    body = _sa.luxury_day_header("مركز إدارة الإشارات الملكي", scope_label, summary, page, total_pages, extra)
    if ledger_lines:
        body += "\n\n📋 <b><u>تفاصيل الصفحة الحالية (المنشئ · الحالة · الأداء):</u></b>\n<blockquote>" + "\n".join(ledger_lines) + "</blockquote>"
    await render(q, body, kb.admin_nav(rows_kb), bot)


async def admin_signals(q, uid, context, bot, page: int = 0):
    await _render_signals_center(q, bot, "all", page, scope="today")


async def admin_signals_cat(q, uid, context, bot, cat: str, page: int = 0):
    await _render_signals_center(q, bot, cat, page, scope="all")


async def admin_signals_day(q, uid, context, bot, scope: str = "today", cat: str = "all", page: int = 0):
    await _render_signals_center(q, bot, cat, page, scope=scope or "today")


async def admin_signals_dates(q, uid, context, bot, page: int = 0):
    """v6.20.4 — Luxury Trading Journal Calendar for Admin Signals."""
    from fysb.services import signal_archive as _sa
    size = 10
    total_days, rows = await afn.run(_sa.admin_dates_page, page, size)
    total_pages = max(1, (total_days + size - 1) // size)
    rows_kb = [
        [kb.btn(_sa.format_date_journal_button(r), f"a:sigday:{r['day']}:all:0")]
        for r in rows
    ]
    nav_row = []
    if page > 0:
        nav_row.append(kb.btn("◀️ أحدث", f"a:sigdates:{page - 1}"))
    if (page + 1) * size < total_days:
        nav_row.append(kb.btn("أقدم ▶️", f"a:sigdates:{page + 1}"))
    if nav_row:
        rows_kb.append(nav_row)
    rows_kb.append([
        kb.btn("📅 إشارات اليوم", "a:sigday:today:all:0", style="primary"),
        kb.btn("📆 إشارات أمس", "a:sigday:yesterday:all:0"),
    ])
    rows_kb.append([kb.btn("💰 محاسبة الربح والخسارة", "a:sigpl"), kb.btn("↩️ مركز الإشارات", "a:signals")])
    body = (
        f"🗓 <b><u>أرشيف الإشارات حسب التاريخ — سجل الإدارة</u></b>\n"
        f"<blockquote>▎📚 إجمالي الأيام المسجلة: <b>{total_days}</b> · صفحة <b>{page + 1}/{total_pages}</b>\n"
        f"▎اختر أي تاريخ أدناه لاستعراض إشاراته الكاملة مع عدّادات الربح والخسارة والوقف.</blockquote>"
    )
    if not rows:
        body += "\n\n<i>لا توجد إشارات مسجلة في الأرشيف بعد.</i>"
    await render(q, body, kb.admin_nav(rows_kb), bot)


def _pl_period_label(period: str) -> str:
    from fysb.services import signal_archive as _sa
    p = (period or "all").strip().lower()
    return {
        "all": "كل الإشارات المغلقة والمحققة",
        "today": _sa.ar_date_label(_sa.today_iso()),
        "yesterday": _sa.ar_date_label(_sa.yesterday_iso()),
        "7d": "آخر 7 أيام",
    }.get(p) or _sa.ar_date_label(p)


def _pl_period_row(period: str, cb_prefix: str) -> list:
    p = (period or "all").strip().lower()
    return [
        kb.btn(("◉ " if p == "all" else "") + "🗂 الكل", f"{cb_prefix}:all"),
        kb.btn(("◉ " if p == "today" else "") + "📅 اليوم", f"{cb_prefix}:today"),
        kb.btn(("◉ " if p == "yesterday" else "") + "📆 أمس", f"{cb_prefix}:yesterday"),
        kb.btn(("◉ " if p == "7d" else "") + "🗓 7 أيام", f"{cb_prefix}:7d"),
    ]


def _pl_exec_card(b: dict, title: str = "الملخص التنفيذي العام") -> str:
    from fysb.theme import bar
    net_usd = float(b["net_usd"] or 0.0)
    dot = "🟢" if net_usd >= 0 else "🔴"
    wr_bar = bar(int(b["wins"]), max(1, int(b["total"])), width=10)
    return (
        f"<blockquote>👑 <b>{title}</b> (مسطرة 0.01 لوت)\n"
        f"▎💵 صافي الربح الحقيقي: <b>{net_usd:+,.2f} $</b> {dot}\n"
        f"▎📊 الصفقات: <b>{b['total']}</b> (🏆 {b['wins']} رابحة · 🛑 {b['losses']} وقف)\n"
        f"▎🎯 نسبة النجاح: <b>{b['win_rate']:.0f}%</b> {wr_bar}\n"
        f"▎📈 إجمالي الأرباح: <b>{b['gross_profit_usd']:+,.2f} $</b> · "
        f"📉 إجمالي الخسائر: <b>{b['gross_loss_usd']:+,.2f} $</b>\n"
        f"▎⚖️ معامل الربح (Profit Factor): <b>{b['pf']}</b>\n"
        f"▎🏁 التوزيع: 👑 TP3: <b>{b['tp3_n']}</b> · 🥈 TP2: <b>{b['tp2_n']}</b> · "
        f"🥇 TP1: <b>{b['tp1_n']}</b> · 🏅 مؤمّنة: <b>{b.get('locked_n', 0)}</b> · "
        f"🛑 وقف: <b>{b['sl_n']}</b></blockquote>"
    )


def royal_locks_board() -> str:
    """v8.14.11 — «القفلات الملكية»: the owner's private vision of the royal
    lock law — every armed trade, its floor, its live distance in REAL
    dollars, and the honest ledger of closed locks. Zero counters, zero
    fabricated percentages — everything read from the recorded truth."""
    from fysb.services.signal_archive import today_iso, date_bounds
    from fysb import instruments as _ins
    lo, hi = date_bounds(today_iso())
    week_lo = date_bounds((config.mecca_now() - timedelta(days=6)).strftime("%Y-%m-%d"))[0]
    with db.conn() as c:
        armed = c.execute("""SELECT id, symbol, direction, timeframe, strategy, entry,
                             locked_sl, locked_pips, live_px, profit_50_at, status
                             FROM signals WHERE profit_50_hit=1
                             AND status IN ('RUNNING','TP1_HIT','TP2_HIT')
                             ORDER BY profit_50_at DESC LIMIT 20""").fetchall()
        n_today = c.execute("SELECT COUNT(*) n FROM signals WHERE status='LOCKED_50' AND closed_at>=? AND closed_at<?",
                            (lo, hi)).fetchone()["n"]
        n_week = c.execute("SELECT COUNT(*) n FROM signals WHERE status='LOCKED_50' AND closed_at>=?",
                           (week_lo,)).fetchone()["n"]
        n_all = c.execute("SELECT COUNT(*) n FROM signals WHERE status='LOCKED_50'").fetchone()["n"]
        recent = c.execute("""SELECT id, symbol, direction, pips_result, closed_at FROM signals
                             WHERE status='LOCKED_50' ORDER BY closed_at DESC LIMIT 10""").fetchall()
    lines = [
        "🏅 <b><u>القفلات الملكية — لوحة رؤية القانون</u></b>",
        "✨ العتبة الموثقة: <b>+5.00 $</b> بـ 0.01 لوت لكل الأدوات (بالدولار الحقيقي لكل سوق)",
        "<blockquote>"
        f"▎🔒 صفقات مؤمّنة الآن: <b>{len(armed)}</b>\n"
        f"▎🏆 قفلات مكتملة: اليوم <b>{n_today}</b> · 7 أيام <b>{n_week}</b> · الإجمالي <b>{n_all}</b> "
        "(كل قفلة = <b>+5.00 $</b> / 0.01 لوت بلا استثناء)"
        "</blockquote>",
    ]
    if armed:
        lines.append("👑 <b>الأرضيات الحية</b> (السعر فوق الأرضية = القفل يحرس):")
        for r in armed:
            sym, floor, live = r["symbol"], float(r["locked_sl"] or 0), float(r["live_px"] or 0)
            if live > 0 and floor > 0:
                raw = (live - floor) if r["direction"] == "BUY" else (floor - live)
                pips_gap = _ins.pips(sym, floor, live) if raw > 0 else -_ins.pips(sym, live, floor)
                usd_gap = _ins.usd_move(sym, pips_gap, 0.01, px=live)
                dist = (f"ارتفاع <b>{pips_gap:+.1f} pips</b> (<code>{usd_gap:+,.2f} $</code> فوق الأرضية / 0.01)"
                        if pips_gap >= 0 else
                        f"⚠️ تحت الأرضية بـ<b>{-pips_gap:.1f} pips</b> — القفل سيلتقط عند العودة")
            else:
                dist = "بانتظار نبضة السعر"
            lines.append(
                f"▎🏅 <code>#{r['id']}</code> <b>{esc(r['symbol'])}</b> "
                f"{'🟢 شراء' if r['direction'] == 'BUY' else '🔴 بيع'} ⏱ {esc(r['timeframe'])} — "
                f"الأرضية <code>{_ins.fmt_price(sym, floor) if floor else '—'}</code> · {dist}")
    else:
        lines.append("<blockquote>▎لا صفقات مؤمّنة الآن — كل إشارة تلمس +5$ موثقاً ستظهر هنا فوراً بأرضيتها الحية.</blockquote>")
    if recent:
        lines.append("📜 <b>آخر القفلات المكتملة</b> (كلها فوز موثق لا خسارة):")
        for r in recent:
            lines.append(
                f"▎🏆 <code>#{r['id']}</code> <b>{esc(r['symbol'])}</b> "
                f"{'🟢' if r['direction'] == 'BUY' else '🔴'} <b>+{float(r['pips_result'] or 0):+.1f} pips</b> "
                f"— {esc(str(r['closed_at'] or '').replace('T', ' ')[:16])}")
    return "\n".join(lines)


async def admin_royal_locks(q, uid, context, bot):
    body = await afn.run(royal_locks_board)
    rows_kb = [[kb.btn("💰 محاسبة P/L", "a:sigpl:all")]]
    await render(q, body, kb.admin_nav(rows_kb), bot)


async def admin_signal_pl(q, uid, context, bot, period: str = "all"):
    """v6.20.5 — Luxury Institutional P/L Hub grouped cleanly by Market Sector
    (Metals / Forex / Crypto / Indices) with apples-to-apples USD totals,
    Profit Factor, Win-Rate bar, and 1-tap drill-down per sector & instrument."""
    from fysb.services import signal_archive as _sa
    period = (period or "all").strip().lower()
    data = await afn.run(_sa.admin_pl_detailed, period)
    grand = data["grand"]
    sectors = data["sectors"]
    p_label = _pl_period_label(period)

    lines = [
        f"💰 <b><u>محاسبة الربح والخسارة المؤسسية</u></b>",
        f"✨ <b>{esc(p_label)}</b>",
    ]
    rows_kb = [_pl_period_row(period, "a:sigpl")]
    rows_kb.append([kb.btn("🏅 القفلات الملكية", "a:royallocks", style="primary")])

    if grand["total"] == 0:
        lines.append(
            "<blockquote>▎لا توجد إشارات مغلقة أو محققة في هذه الفترة بعد.\n"
            "▎تُبنى المحاسبة الحقيقية تلقائياً مع كل هدف أو وقف يسجّله المتتبع.</blockquote>"
        )
    else:
        lines.append("")
        lines.append(_pl_exec_card(grand, "الملخص التنفيذي العام — جميع الأسواق"))

        sec_btns = []
        sym_btns = []
        for sb in sectors:
            sec_key = sb["sector"]
            icon, sec_title, sec_short = _sa.PL_SECTOR_META[sec_key]
            sec_dot = "🟢" if sb["net_usd"] >= 0 else "🔴"
            lines.append("")
            lines.append(
                f"{icon} <b><u>سوق {sec_title}</u></b> — صافي <b>{sb['net_usd']:+,.2f} $</b> {sec_dot}"
            )
            block = [
                f"▎📊 <b>{sb['total']}</b> صفقة · نجاح <b>{sb['win_rate']:.0f}%</b> "
                f"(🏆 {sb['wins']} / 🛑 {sb['losses']}) · PF <b>{sb['pf']}</b>"
            ]
            for s in sb["symbols"]:
                s_dot = "🟢" if s["net_usd"] >= 0 else "🔴"
                block.append(
                    f"▎• <b>{esc(s['symbol'])}</b> {s_dot} — {s['total']} صفقة "
                    f"({s['wins']}W/{s['losses']}L · {s['win_rate']:.0f}%)\n"
                    f"   └ صافي <b>{s['net_pips']:+.1f} pips</b> (متوسط {s['avg_pips']:+.1f}p) "
                    f"· 💵 <b>{s['net_usd']:+,.2f} $</b>"
                )
                sym_btns.append(
                    kb.btn(
                        f"{icon} {s['symbol']} ({s['net_usd']:+,.2f}$)",
                        f"a:sigplsym:{s['symbol']}:{period}",
                    )
                )
            lines.append("<blockquote>" + "\n".join(block) + "</blockquote>")
            sec_btns.append(
                kb.btn(f"{icon} {sec_short} ({sb['total']})", f"a:sigplcat:{sec_key}:{period}")
            )

        lines.append("")
        lines.append("<i>💡 اضغط على أي سوق أو أداة أدناه لفتح كشف المحاسبة التفصيلي وصفقة بصفقة.</i>")

        if sec_btns:
            rows_kb.append(sec_btns)
        for i in range(0, min(len(sym_btns), 8), 2):
            rows_kb.append(sym_btns[i:i + 2])

    rows_kb.append([kb.btn("🗓 أرشيف التواريخ", "a:sigdates:0"), kb.btn("↩️ مركز الإشارات", "a:signals")])
    await render(q, "\n".join(lines), kb.admin_nav(rows_kb), bot)


async def admin_signal_pl_sector(q, uid, context, bot, sector: str, period: str = "all"):
    """v6.20.5 — Focused Luxury P/L Ledger for ONE Market Sector (Metals/Forex/Crypto/Indices)."""
    from fysb.services import signal_archive as _sa
    sec_key = (sector or "metal").strip().lower()
    if sec_key not in _sa.PL_SECTOR_META:
        sec_key = "forex"
    period = (period or "all").strip().lower()
    icon, sec_title, _short = _sa.PL_SECTOR_META[sec_key]
    data = await afn.run(_sa.admin_pl_detailed, period, sec_key)
    grand = data["grand"]
    sec_list = data["sectors"]
    p_label = _pl_period_label(period)

    lines = [
        f"{icon} <b><u>محاسبة سوق {sec_title}</u></b>",
        f"✨ <b>{esc(p_label)}</b>",
    ]
    rows_kb = [_pl_period_row(period, f"a:sigplcat:{sec_key}")]
    rows_kb.append([kb.btn("🏅 القفلات الملكية", "a:royallocks", style="primary")])

    if grand["total"] == 0 or not sec_list:
        lines.append("<blockquote>▎لا توجد إشارات مغلقة في هذا السوق خلال الفترة المختارة.</blockquote>")
    else:
        lines.append("")
        lines.append(_pl_exec_card(grand, f"ملخص قطاع {sec_title}"))
        sym_block = []
        sym_btns = []
        for s in sec_list[0]["symbols"]:
            s_dot = "🟢" if s["net_usd"] >= 0 else "🔴"
            sym_block.append(
                f"▎• <b>{esc(s['symbol'])}</b> {s_dot} — <b>{s['total']}</b> صفقة · نجاح <b>{s['win_rate']:.0f}%</b> "
                f"({s['wins']}W/{s['losses']}L) · PF <b>{s['pf']}</b>\n"
                f"   └ صافي <b>{s['net_pips']:+.1f} pips</b> · أرباح {s['gross_profit_pips']:+.1f}p / خسائر {s['gross_loss_pips']:+.1f}p\n"
                f"   └ 💵 <b>{s['net_usd']:+,.2f} $</b> (0.01 لوت) · <b>{s['net_usd'] * 10:+,.2f} $</b> (0.10 لوت)"
            )
            sym_btns.append(
                kb.btn(f"{icon} {s['symbol']} ({s['net_usd']:+,.2f}$)", f"a:sigplsym:{s['symbol']}:{period}")
            )
        lines.append("")
        lines.append(f"📋 <b>تفصيل الأدوات داخل {sec_title}</b>")
        lines.append("<blockquote>" + "\n".join(sym_block) + "</blockquote>")
        for i in range(0, min(len(sym_btns), 10), 2):
            rows_kb.append(sym_btns[i:i + 2])

    rows_kb.append([kb.btn("💰 كل الأسواق", f"a:sigpl:{period}"), kb.btn("↩️ مركز الإشارات", "a:signals")])
    await render(q, "\n".join(lines), kb.admin_nav(rows_kb), bot)


async def admin_signal_pl_symbol(q, uid, context, bot, symbol: str, period: str = "all"):
    """v6.20.5 — Deep Institutional P/L Ledger for ONE Instrument with
    lot-scale preview, best/worst trade, and clickable trade-by-trade history."""
    from fysb import instruments
    from fysb.services import signal_archive as _sa
    from fysb.theme import signal_status_ar
    sym = instruments.normalize(symbol)
    period = (period or "all").strip().lower()
    data = await afn.run(_sa.admin_pl_detailed, period, "", sym)
    s = data["by_symbol"].get(sym)
    sec_key = s["sector"] if s else _sa.sector_of_symbol(sym)
    icon, sec_title, sec_short = _sa.PL_SECTOR_META[sec_key]
    p_label = _pl_period_label(period)

    lines = [
        f"{icon} <b><u>كشف المحاسبة التفصيلي — {esc(sym)}</u></b>",
        f"✨ سوق {sec_title} · <b>{esc(p_label)}</b>",
    ]
    rows_kb = [_pl_period_row(period, f"a:sigplsym:{sym}")]
    rows_kb.append([kb.btn("🏅 القفلات الملكية", "a:royallocks", style="primary")])

    if not s or s["total"] == 0:
        lines.append("<blockquote>▎لا توجد صفقات مغلقة لهذه الأداة خلال الفترة المختارة.</blockquote>")
    else:
        lines.append("")
        lines.append(_pl_exec_card(s, f"أداء {sym}"))
        pips_card = [
            f"▎📐 <b>صافي النقاط:</b> <b>{s['net_pips']:+.1f} pips</b> (متوسط <b>{s['avg_pips']:+.1f}p</b>/صفقة)",
            f"▎🟢 مجموع نقاط الربح: <b>{s['gross_profit_pips']:+.1f}p</b> · 🔴 مجموع نقاط الوقف: <b>{s['gross_loss_pips']:+.1f}p</b>",
            f"▎🏆 أفضل صفقة: <b>{float(s['best_pips'] or 0):+.1f}p</b> ({float(s['best_usd'] or 0):+,.2f} $)",
            f"▎📉 أسوأ صفقة: <b>{float(s['worst_pips'] or 0):+.1f}p</b> ({float(s['worst_usd'] or 0):+,.2f} $)",
            f"▎💼 <b>مقياس اللوت:</b> 0.01لوت: <b>{s['net_usd']:+,.2f}$</b> · "
            f"0.10لوت: <b>{s['net_usd'] * 10:+,.2f}$</b> · 1.00لوت: <b>{s['net_usd'] * 100:+,.2f}$</b>",
        ]
        lines.append("<blockquote>" + "\n".join(pips_card) + "</blockquote>")

        # Clickable trade-by-trade ledger (up to 8 most recent)
        trade_lines = []
        for tr in s["trades"][:8]:
            d_ico = "🟢" if tr["direction"] == "BUY" else "🔴"
            pips = float(tr.get("pips_result") or 0.0)
            usd = float(tr.get("usd") or 0.0)
            day = (tr.get("created_at") or "")[:10]
            trade_lines.append(
                f"▎{d_ico} <b>#{tr['seq']}</b> {tr['direction']} {tr['timeframe']} · "
                f"{signal_status_ar(tr)} · <b>{pips:+.1f}p</b> ({usd:+,.2f}$) · {day}"
            )
            rows_kb.append([
                kb.btn(
                    f"{d_ico} #{tr['seq']} {tr['direction']} {tr['timeframe']} · {pips:+.1f}p ({usd:+,.2f}$) · {day}",
                    f"a:signalview:{tr['id']}",
                )
            ])
        lines.append("")
        lines.append(f"📜 <b>سجل الصفقات المغلقة ({min(len(s['trades']), 8)} من {s['total']})</b>")
        lines.append("<blockquote>" + "\n".join(trade_lines) + "</blockquote>")

    rows_kb.append([
        kb.btn(f"{icon} قطاع {sec_short}", f"a:sigplcat:{sec_key}:{period}"),
        kb.btn("💰 كل الأسواق", f"a:sigpl:{period}"),
    ])
    rows_kb.append([kb.btn("↩️ مركز الإشارات", "a:signals")])
    await render(q, "\n".join(lines), kb.admin_nav(rows_kb), bot)


async def admin_signal_view(q, uid, context, bot, sid: int):
    r, deliveries = await afn.run(_signal_with_deliveries, sid)
    if not r:
        await render(q, "الإشارة غير موجودة.", kb.admin_nav(), bot)
        return
    from fysb import instruments
    from fysb.engines.tracker import seq_of
    from fysb.theme import is_signal_closed
    sig = dict(r)
    seq = await afn.run(seq_of, sid)
    creator_lbl = await afn.run(signal_creator_label, sig)

    def _demo_count_for(signal_id: int) -> int:
        with db.conn() as c:
            row = c.execute("SELECT COUNT(*) n FROM demo_positions WHERE signal_id=?", (signal_id,)).fetchone()
            return int(row["n"] if row else 0)

    demo_n = await afn.run(_demo_count_for, sid)
    live_note = ""
    sig_open = (not is_signal_closed(sig)) and sig["status"] in ("ACTIVE", "RUNNING", "TP1_HIT", "TP2_HIT")
    if sig_open:
        try:
            quote = await asyncio.to_thread(market_data.fetch_price, sig["symbol"])
            price = float(quote["price"])
            from fysb.engines.tracker import drift_pips
            entry = float(sig["entry"])
            drift = drift_pips(sig["symbol"], sig["direction"], entry, price)
            usd01 = instruments.usd_move(sig["symbol"], drift, 0.01, px=price)
            side = "أعلى الدخول" if price > entry else ("عند الدخول" if price == entry else "أدنى الدخول")
            live_note = (
                f"\n▎💰 <b>سعر السوق الحي:</b> <code>{instruments.fmt_price(sig['symbol'], price)}</code>"
                f" · {side} · <b>{drift:+.1f} pips</b> (<code>{usd01:+,.2f}$</code> / 0.01)"
            )
            sl = float(sig["stop"])
            beyond = price >= sl if sig["direction"] == "SELL" else price <= sl
            if beyond:
                live_note += "\n▎⚠️ السعر تجاوز وقف الخسارة — اضغط «🔄 فحص الآن» لتحصيل الحالة فوراً."
        except Exception:
            live_note = "\n▎⚠️ تعذر جلب السعر اللحظي الآن (المزود غير متاح لحظياً)."
    elif sig["status"] in ("TP1_HIT", "TP2_HIT"):
        live_note = f"\n▎🔒 <b>صفقة مغلقة على ربح محقق ({float(sig.get('pips_result') or 0):+.1f} pips)</b> — أُغلقت عند التأمين."

    hit_tp = int(sig.get("hit_tp") or 0)
    tp_bar = f"TP1 {'✅' if hit_tp >= 1 else '⏳'} · TP2 {'✅' if hit_tp >= 2 else '⏳'} · TP3 {'✅' if hit_tp >= 3 else '⏳'}"
    pips_res = float(sig.get("pips_result") or 0.0)
    pips_usd = instruments.usd_move(sig["symbol"], pips_res, 0.01, px=float(sig["entry"])) if pips_res else 0.0
    filled_txt = human_dt(sig["filled_at"]) if sig.get("filled_at") else "لم تتفعل بعد"
    exec_dossier = (
        f"\n\n👑 <b><u>بطاقة التدقيق الإداري — الإشارة #{seq}</u></b>\n"
        f"<blockquote>"
        f"▎👤 <b>منشئ / مصدر الإشارة:</b> <b>{esc(creator_lbl)}</b>\n"
        f"▎🏁 <b>الأهداف المحققة:</b> {tp_bar}\n"
        f"▎📈 <b>النتيجة المسجلة:</b> <b>{pips_res:+.1f} pips</b>"
        + (f" (<code>{pips_usd:+,.2f}$</code> لكل 0.01 لوت)" if pips_res else "")
        + f"\n▎⏱ <b>وقت التفعيل في السوق:</b> {filled_txt}\n"
        f"▎📬 <b>المستلمون:</b> <b>{deliveries}</b> · 🎮 <b>صفقات تجريبية:</b> <b>{demo_n}</b>"
        f"{live_note}"
        f"</blockquote>"
    )

    rows_kb = [[kb.signal_demo_launch_btn(sid, "🎮 تجريبي الآن — فتح على الشارت")]]
    if sig_open:
        rows_kb.append([kb.btn("🚫 إلغاء الإشارة", f"a:cancelsignal:{sid}", style="danger"),
                        kb.btn("🔄 فحص وتحديث الآن", "a:tracknow", style="primary")])
    rows_kb.append([
        kb.btn("📊 شارت الإشارة", f"a:sigchart:{sid}", style="primary"),
        kb.btn("🏆 نشر إثبات للقنوات", f"a:sigproof:{sid}", style="success"),
    ])
    rows_kb.append([kb.btn("🗑 حذف نهائي للإشارة", f"a:delsignal:{sid}", style="danger")])
    rows_kb.append([kb.btn("👥 المستلمون", f"a:signalrecipients:{sid}", style="primary"),
                    kb.btn("↩️ مركز الإشارات", "a:signals", style="primary")])
    body = render_signal_card(sig) + exec_dossier
    await render(q, body, kb.admin_nav(rows_kb), bot)


async def admin_delete_signal_confirm(q, uid, context, bot, sid: int):
    """Luxury double-confirmation before PERMANENT signal deletion."""
    r, deliveries = await afn.run(_signal_with_deliveries, sid)
    if not r:
        await render(q, "الإشارة غير موجودة (ربما حُذفت سابقاً).", kb.admin_nav(), bot)
        return
    from fysb.theme import signal_status_ar
    sig = dict(r)
    body = (f"🗑 <b>حذف نهائي للإشارة #{sid}</b>\n"
            f"<blockquote>▎🌐 {esc(sig['symbol'])} · {esc(sig['direction'])} · {esc(sig['timeframe'])}\n"
            f"▎📌 الحالة: <b>{signal_status_ar(sig)}</b> · الجودة {sig['score']}/100\n"
            f"▎📬 مستلمون مسجلون: <b>{deliveries}</b>\n"
            f"▎⚠️ سيُمحى السجل <b>بأكمله نهائياً</b> — الإشارة، التسليمات، كل شيء. لا أرشيف ولا رجوع.</blockquote>")
    rows_kb = [
        [kb.btn("⚠️ تأكيد الحذف النهائي", f"a:delsignalgo:{sid}")],
        [kb.btn("↩️ تراجع", f"a:signalview:{sid}")],
    ]
    await render(q, body, kb.admin_nav(rows_kb), bot)


async def admin_delete_signal_go(q, uid, context, bot, sid: int):
    """ERASE the signal row + its deliveries forever (audit-logged)."""
    sig, d, s = await afn.run(_signal_delete_tx, sid)
    if not s:
        await render(q, "الإشارة غير موجودة (ربما حُذفت سابقاً).", kb.admin_nav(), bot)
        return
    await afn.run(audit_log.log, uid, f"signal_deleted_forever:{sid}")
    body = (f"✅ <b>تم حذف الإشارة #{sid} نهائياً</b>"
            + (f" ({esc(sig['symbol'])} {esc(sig['direction'])})" if sig else "")
            + f"\n<blockquote>▎🗑 سجل الإشارة: مُمحى\n▎📬 سجلات التسليم المُمحاة: <b>{d}</b>\n"
              f"▎🔒 سُجّل في سجل التدقيق</blockquote>")
    await render(q, body, kb.admin_nav([[kb.btn("↩️ مركز الإشارات", "a:signals")]]), bot)


async def admin_signal_recipients(q, uid, context, bot, sid: int):
    rows = await afn.run(_signal_recipients, sid)
    lines = [f"📬 <b>مستلمو الإشارة #{sid}</b>"]
    lines += [f"  <code>{r['user_id']}</code> · {human_dt(r['delivered_at'])}" for r in rows] or ["  لا يوجد مستلمون."]
    await render(q, "\n".join(lines), kb.admin_nav([[kb.btn("↩️ الإشارة", f"a:signalview:{sid}")]]), bot)


async def admin_signal_purge(q, uid, context, bot):
    """Bulk permanent deletion of CANCELLED/EXPIRED signals (double confirm)."""
    n = await afn.run(_dead_signals_count)
    if not n:
        await render(q, "✅ لا توجد إشارات ملغاة أو منتهية — السجل نظيف بالكامل.",
                     kb.admin_nav([[kb.btn("↩️ الإشارات", "a:signals")]]), bot)
        return
    await render(q, f"🧹 <b>تنظيف الإشارات الميتة</b>\n<blockquote>▎إشارات ملغاة/منتهية: <b>{n}</b>\n"
                    f"▎⚠️ حذف نهائي بلا أرشيف — يشمل سجلات التسليم المرتبطة.</blockquote>",
                 kb.admin_nav([[kb.btn(f"⚠️ حذف {n} إشارة نهائياً", "a:signalpurgego")],
                               [kb.btn("↩️ تراجع", "a:signals")]]), bot)


async def admin_signal_purge_go(q, uid, context, bot):
    d, s = await afn.run(_signal_purge_tx)
    await afn.run(audit_log.log, uid, f"signals_purged:{s}")
    await render(q, f"✅ <b>تم التنظيف النهائي</b>\n<blockquote>▎🗑 إشارات مُمحاة: <b>{s}</b>\n▎📬 تسليمات مُمحاة: <b>{d}</b>\n"
                    f"▎🔒 سُجّل في سجل التدقيق</blockquote>",
                 kb.admin_nav([[kb.btn("↩️ مركز الإشارات", "a:signals")]]), bot)


async def admin_cancel_signal(q, uid, context, bot, sid: int):
    cancelled = await afn.run(_cancel_signal_tx, sid)
    await afn.run(audit_log.log, uid, f"cancel_signal:{sid}")
    if cancelled:
        from fysb.engines.tracker import TERMINAL, seq_of
        from fysb.theme import dir_badge, esc, status_ar
        sig = await afn.run(_signal_row_full, sid)
        seq = await afn.run(seq_of, sid)
        text = (f"⛔ <b><u>تحديث الإشارة #{seq}</u></b> — {esc(sig['symbol'])} {dir_badge(sig['direction'])}\n"
                f"<blockquote>▎📌 الحالة الجديدة: <b>{status_ar('CANCELLED')}</b>\n"
                f"▎📌 السبب: قرار إداري موثق من الإدارة</blockquote>")
        uids = await afn.run(_signal_recipient_uids, sid)
        for note_uid in uids + [config.ADMIN_ID]:
            try:
                await bot.send_message(note_uid, text, parse_mode="HTML")
            except Exception:
                pass
    await admin_signal_view(q, uid, context, bot, sid)


async def admin_new_signal(q, uid, context, bot):
    context.user_data["awaiting_signal"] = True
    await render(q, "📝 أرسل الإشارة بالتنسيق:\n<code>SYMBOL | BUY/SELL | TF | ENTRY | SL | TP1 | TP2 | TP3 | SCORE | STRATEGY | السبب</code>\n\n"
                    "يُفحص الاتجاه والنسبة (TP1 ≥ 2R) ومنع التكرار آلياً، ثم تُوجّه للمستخدمين المؤهلين ضمن حدود باقاتهم.",
                 kb.admin_nav(), bot)


async def admin_track_now(q, uid, context, bot):
    """Run one tracker pass immediately (admin action) with real provider data,
    live-editing ALL user + linked channel cards and sending status/expiry alerts."""
    from fysb.engines import tracker
    from fysb.handlers.background import _safe_edit, _safe_send
    await render(q, "🔄 جارٍ فحص الإشارات المفتوحة وتحديث البطاقات الحية (مستخدمون + قنوات)...", None)
    result = await asyncio.to_thread(tracker.check_open_signals, config.market_api_key())
    edited = 0
    sent = 0
    for _uid, chat_id, mid, text in result.get("edits", []):
        if await _safe_edit(bot, chat_id, mid, text):
            edited += 1
    for target, text in result.get("notifications", []):
        if await _safe_send(bot, target, text):
            sent += 1
    expired, exp_edits, exp_notes = await afn.run(tracker.expire_with_alerts)
    for _uid, chat_id, mid, text in exp_edits:
        if await _safe_edit(bot, chat_id, mid, text):
            edited += 1
    for target, text in exp_notes:
        if await _safe_send(bot, target, text):
            sent += 1
    stats = result["stats"]
    await render(
        q,
        f"✅ <b>نتيجة الفحص والتتبع الحي</b>\n"
        f"<blockquote>"
        f"▎📊 فُحصت: <b>{stats['checked']}</b> · حُدثت حالتها: <b>{stats['updated']}</b>\n"
        f"▎🔄 بطاقات حية حُدّثت (مستخدمون + قنوات): <b>{edited}</b>\n"
        f"▎📨 تنبيهات أُرسلت (مستخدمون + قنوات): <b>{sent}</b>\n"
        f"▎⏳ انتهت صلاحيتها: <b>{expired}</b> · تخطيت (قدم البيانات): {stats['skipped_stale']} · أخطاء مزود: {stats['errors']}"
        f"</blockquote>",
        kb.admin_nav([[kb.btn("↩️ الإشارات", "a:signals"), kb.btn("🔄 فحص مجدداً", "a:tracknow")]]),
        bot,
    )


async def update_message(q, text):
    """Send a transient progress note (fresh message pattern)."""
    await render(q, text, None)


# --- Trading engine --------------------------------------------------------------
async def admin_engine(q, uid, context, bot):
    enabled = await afn.run(db.get_setting, "engine_enabled", "0") == "1"
    interval = await afn.run(db.get_setting_int, "engine_interval_seconds", 300)
    min_score = await afn.run(db.get_setting_int, "engine_min_score", 80)
    scan_tfs_raw = await afn.run(db.get_setting, "engine_scan_timeframes", "M15,H1")
    markets, runs = await afn.run(_engine_page_rows)
    lines = ["🧠 <b>محرك السوق والتحليل</b>",
             f"📌 الحالة: <b>{'🟢 يعمل' if enabled else '⚪ متوقف'}</b>",
             f"⏱ فاصل الدورة: {interval} ثانية",
             f"⭐ الحد الأدنى للجودة: {min_score}/100",
             f"📐 فريمات المسح الآلي: <b>{esc(scan_tfs_raw or 'M15,H1')}</b>",
             f"🌐 أسواق جاهزة (مفعلة+موثقة): <b>{markets}</b>",
             "",
             "<b>آخر الدورات:</b>"]
    for r in runs:
        lines.append(f"  {r['created_at'][11:16]} {esc(r['symbol'])} {esc(r['timeframe'])}: {ar_run_line(r['result'], r['detail'])}")
    rows_kb = [
        [kb.btn(f"{'⏹ إيقاف المحرك' if enabled else '▶️ تشغيل المحرك'}", "a:enginetoggle", style="danger" if enabled else "success")],
        [kb.btn("🔍 تشخيص بوابات الإشارات", "a:enginediag", style="success"), kb.btn(f"📐 فريمات المسح الآلي ({scan_tfs_raw or 'M15,H1'})", "a:scantfs", style="primary")],
        [kb.btn("➖ فاصل", f"a:engineadj:interval:-60"), kb.btn(f"⏱ {interval}s", "a:plannoop"), kb.btn("➕ فاصل", f"a:engineadj:interval:60")],
        [kb.btn("➖ جودة", f"a:engineadj:minscore:-5"), kb.btn(f"⭐ {min_score}", "a:plannoop"), kb.btn("➕ جودة", f"a:engineadj:minscore:5")],
        [kb.btn("🚀 دورة فورية الآن", "a:enginenow"), kb.btn("🧹 تنظيف السجل", "a:engineclean")],
        [kb.btn("📰 درع الأخبار", "a:newsguard", style="primary"), kb.btn("🏆 مصفوفة الاستراتيجيات", "a:leaderboard", style="success")],
        [kb.btn("📊 الشارتات وجدولتها", "a:chartsmenu", style="primary"), kb.btn("🧪 اختبار تاريخي (Backtest)", "a:backtest")],
    ]
    await render(q, "\n".join(lines), kb.admin_nav(rows_kb), bot)


_SCAN_TF_CHOICES = ("M5", "M15", "M30", "H1", "H4", "D1")


def _toggle_scan_tf_sync(code: str) -> str:
    code = (code or "").strip().upper()
    raw = (db.get_setting("engine_scan_timeframes", "M15,H1") or "M15,H1").strip()
    cur = [x.strip().upper() for x in raw.split(",") if x.strip()]
    if code in cur:
        if len(cur) > 1:
            cur.remove(code)
    else:
        if code in _SCAN_TF_CHOICES:
            cur.append(code)
    ordered = [tf for tf in _SCAN_TF_CHOICES if tf in cur] or ["M15"]
    new_val = ",".join(ordered)
    db.set_setting("engine_scan_timeframes", new_val)
    return new_val


async def admin_scan_tfs(q, uid, context, bot):
    raw = await afn.run(db.get_setting, "engine_scan_timeframes", "M15,H1")
    active = {x.strip().upper() for x in (raw or "M15,H1").split(",") if x.strip()}
    rows_kb = []
    row = []
    for tf in _SCAN_TF_CHOICES:
        on = tf in active
        row.append(kb.btn(f"{'✅' if on else '⬜'} {tf}", f"a:togglescantf:{tf}", style="success" if on else None))
        if len(row) == 3:
            rows_kb.append(row)
            row = []
    if row:
        rows_kb.append(row)
    rows_kb.append([kb.btn("↩️ المحرك", "a:engine")])
    body = (
        "📐 <b><u>فريمات المسح الآلي متعدد الأطر</u></b>\n"
        f"<blockquote>▎الفريمات النشطة حالياً: <b>{esc(raw or 'M15,H1')}</b>\n"
        "▎اضغط على أي فريم لتفعيله أو إيقافه في دورة المحرك الآلي (يبقى فريم واحد نشط على الأقل).</blockquote>"
    )
    await render(q, body, kb.admin_nav(rows_kb), bot)


async def admin_scan_tf_toggle(q, uid, context, bot, code: str):
    new_val = await afn.run(_toggle_scan_tf_sync, code)
    await afn.run(audit_log.log, uid, f"engine_scan_timeframes:{new_val}")
    await admin_scan_tfs(q, uid, context, bot)


# --- v8.11.0/8.11.1 (P1): Signal-Gate Diagnostics — royal buttoned center ---
def _engine_diag_sync() -> dict:
    """One pooled pass over every issuance gate (admin eyes only, no vendor names)."""
    from fysb.engines import news_guard as _ng, risk as _risk
    with db.conn() as c:
        armed = [r["symbol"] for r in c.execute(
            "SELECT symbol FROM market_catalog WHERE enabled=1 AND verified=1 ORDER BY symbol").fetchall()]
        last_runs = c.execute(
            "SELECT e.symbol, e.result, e.detail, e.created_at FROM engine_runs e "
            "JOIN (SELECT symbol, MAX(id) AS mid FROM engine_runs GROUP BY symbol) t ON e.id=t.mid").fetchall()
        tfs_enabled = [r["code"] for r in c.execute(
            "SELECT code FROM timeframe_catalog WHERE enabled=1 AND verified=1").fetchall()]
    gates = {
        "engine": db.get_setting("engine_enabled", "0") == "1",
        "kill": db.kill_switch_on(),
        "push": db.get_setting("signal_push_enabled", "1") == "1",
        "scan_tfs": (db.get_setting("engine_scan_timeframes", "M15,H1") or "").strip(),
        "interval": db.get_setting_int("engine_interval_seconds", 300),
        "min_score": db.get_setting_int("engine_min_score", 80),
        "td_key_source": config.market_api_key_source(),
        "news": _ng.is_enabled(),
        "news_window": _ng.window_minutes(),
        "news_fail_open": _ng.fail_open(),
        "news_cache_max_h": max(6, min(168, db.get_setting_int("news_guard_cache_max_hours", 48))),
        "news_health": _ng.feed_health(),
        "provider_state": market_data.provider_status().get("state", "?"),
        "risk": _risk.open_caps_snapshot(),
        "tfs_enabled": tfs_enabled,
    }
    per_market = {r["symbol"]: dict(r) for r in last_runs}
    # Aggregate the last attempt of every armed market into honest totals.
    totals: dict[str, int] = {}
    blocked_markets = 0
    for sym in armed:
        r = per_market.get(sym)
        if not r:
            totals["بدون دورة بعد"] = totals.get("بدون دورة بعد", 0) + 1
            continue
        res = (r["result"] or "").upper()
        label = theme.ar_result(res)
        totals[label] = totals.get(label, 0) + 1
        if res not in ("SIGNAL_CREATED", "NO_SETUP", "DUPLICATE"):
            blocked_markets += 1
    return {
        "armed": armed,
        "runs": per_market,
        "totals": sorted(totals.items(), key=lambda kv: -kv[1]),
        "blocked_markets": blocked_markets,
        "gates": gates,
    }


def _probe_market_data_sync() -> str:
    """REAL live-data probe (admin-only): actual fetch, actual ages, sanitized."""
    out = []
    try:
        q = market_data.fetch_price("XAUUSD", use_cache=False)
        out.append(f"▎🥇 السعر اللحظي للذهب: <b>{float(q['price']):,.2f}</b> ✅")
    except RuntimeError as e:
        out.append(f"▎🥇 السعر اللحظي للذهب: ⚠️ تعذر — {esc(str(e))[:90]}")
    try:
        p = market_data.fetch_candles("XAUUSD", "1h", use_cache=False)
        age_min = int(p["age_seconds"]) // 60
        limit_min = max(0, market_data.staleness_limit("1h") // 60)
        verdict = "✅ طازجة ومؤهلة للتحقق" if not market_data.is_stale(p) else "⚠️ قديمة — بوابة القدم سترفضها"
        out.append(f"▎🕯 آخر شمعة ساعة مغلقة: <code>{esc(p['last_candle_iso'])}</code> UTC")
        out.append(f"▎⏱ عمرها: <b>{age_min} دقيقة</b> · الحد المسموح لهذا الفريم: <b>{limit_min} دقيقة</b>")
        out.append(f"▎🧪 الحكم: <b>{verdict}</b> · الشموع المتاحة: <b>{len(p['values'])}</b>")
        # v8.13.2 — freeze verdict + the failover chain verdicts (admin-only)
        if p.get("feed_frozen"):
            out.append(f"▎🧊 <b>التغذية مجمّدة!</b> نفس اللقطة تكررت {p.get('frozen_repeats', 1)} مرة "
                       "والسوق مفتوح — راجع اتصال السيرفر الصادر وصلاحية مفتاح السوق ثم أعد التشغيل")
        _chain = p.get("chain_errors") or []
        if _chain:
            out.append(f"▎🧾 حكم السلسلة: <code>{esc(', '.join(_chain))[:140]}</code>")
    except RuntimeError as e:
        out.append(f"▎🕯 شموع الساعة: ⚠️ تعذر — {esc(str(e))[:90]}")
    return "\n".join(out)


def _primary_blocker(gates: dict, diag: dict) -> str:
    """The ONE dominant blocker, honest and first-glance."""
    rk = gates["risk"]
    if gates["kill"]:
        return "🛡 مفتاح القتل مشغّل — لا إصدار إطلاقاً حتى تعطيله من إدارة المخاطر"
    if not gates["engine"]:
        return "⚪ المحرك متوقف عمداً — شغّله من صفحة المحرك ليبدأ البحث الآلي"
    if rk["ceiling_full"]:
        return (f"🛡 السقف الكلي ممتلئ ({rk['open']}/{rk['cap_open']}) — إشارة مفتوحة منذ "
                f"{rk['oldest_open_hours']} س تحجب كل الجديد حتى تُغلق أو يُرفع السقف")
    if rk["daily_breached"]:
        return f"💥 قاطع الخسارة اليومي مشغّل ({rk['daily_pnl_pct']:+.1f}%) — وقاية اليومية"
    if gates["news"] and gates["news_health"]["state"] == "UNKNOWN" and not gates["news_fail_open"]:
        return "📰 درع الأخبار بلا بيانات تقويم صالحة — يمنع الإصدار (راجع درع الأخبار)"
    if diag["totals"]:
        top_label, top_n = diag["totals"][0]
        if diag["blocked_markets"] and top_label not in ("✅ إشارة ولدت وسُلّمت", "🧭 لا فرصة مؤهلة — شروط الاستراتيجيات غير مكتملة فعلاً"):
            return f"{top_label} — {top_n} من {len(diag['armed'])} سوق في آخر دورة"
    return "✅ لا مانع بنيوي — الحالة سوقية صادقة (فرص غير مكتملة أو أسواق مغلقة)"


async def admin_engine_diagnostics(q, uid, context, bot, notice: str = ""):
    d = await afn.run(_engine_diag_sync)
    g = d["gates"]
    nh = g["news_health"]
    rk = g["risk"]
    td_txt = {"env": "✅ من .env", "panel": "✅ من اللوحة (مشفّر)",
              "": "⚪ غير مضبوط"}.get(g["td_key_source"], "؟")
    lines = [
        "🔍 <b><u>تشخيص بوابات الإشارات — الحكم الفوري</u></b>",
        "",
        f"<blockquote>⛔ <b>المانع الرئيسي الآن:</b> <b>{esc(_primary_blocker(g, d))}</b></blockquote>",
        "",
        f"🟢 المحرك: <b>{'يعمل' if g['engine'] else 'متوقف'}</b> · "
        f"🛡 القتل: <b>{'مشغّل!' if g['kill'] else 'حر'}</b> · "
        f"الدفع: <b>{'✅' if g['push'] else '⛔'}</b> · "
        f"📡 البيانات: <b>{esc(g['provider_state'])}</b> · "
        f"🔑 الموسّع: <b>{td_txt}</b>",
        f"📰 الأخبار: <b>{'مفعّلة ±' + str(g['news_window']) + 'د' if g['news'] else 'متوقف'}</b> "
        f"({'🟢 حي' if nh['state'] == 'OK' else ('🟡 محفوظ ' + str(nh['cache_age_minutes']) + 'د' if nh['state'] == 'CACHED' else '🔴 بلا بيانات')}) · "
        f"⏱ المسح: <b>{esc(g['scan_tfs'] or 'M15,H1')}</b> كل <b>{g['interval']}ث</b> · "
        f"⭐ الجودة ≥ <b>{g['min_score']}</b> · 🌐 الأسواق المسلّحة: <b>{len(d['armed'])}</b>",
        "",
        f"🛡 <b>السقف الخفي:</b> <b>{rk['open']}/{rk['cap_open']}</b> "
        f"{'⛔ ممتلئ — يحجب' if rk['ceiling_full'] else '✅ متاح'} · "
        f"أعلى سوق: <b>{esc(rk['worst_symbol'] or '—')} {rk['worst_symbol_open']}/{rk['cap_per_symbol']}</b>"
        f"{' ⛔' if rk['symbol_full'] else ''} · "
        f"الترابط: <b>{esc(rk['worst_bucket'] or '—')} {rk['worst_bucket_open']}/{rk['cap_correlated']}</b>"
        f"{' ⛔' if rk['correlated_full'] else ''}",
        f"💥 قاطع اليوم: <b>{rk['daily_pnl_pct']:+.1f}%</b> (الحد -{rk['max_daily_loss_pct']:.1f}%) "
        f"{'⛔ مشغّل' if rk['daily_breached'] else '✅'} · أقدم إشارة مفتوحة: <b>{rk['oldest_open_hours']} س</b>",
    ]
    if d["totals"]:
        parts = [f"{lbl} <b>{n}</b>" for lbl, n in d["totals"][:4]]
        lines.append("")
        lines.append("📊 <b>إجمالي آخر دورة:</b> " + " · ".join(parts))
    if notice:
        lines = [f"<blockquote>🧪 <b>نتيجة الفحص الحي الفعلي:</b>\n{notice}</blockquote>", ""] + lines
    rows_kb = [
        [kb.btn("🧪 فحص بيانات حي", "a:enginediagprobe", style="success"),
         kb.btn(f"🌐 قرار كل سوق ({len(d['armed'])})", "a:enginediagmarkets:0", style="primary")],
        [kb.btn("🧾 سجل المحرك", "a:enginediagruns:0"),
         kb.btn("🛡 إدارة السقف" + (" ⛔" if rk["ceiling_full"] else ""),
                "a:enginediagceiling", style="danger" if rk["ceiling_full"] else "primary")],
        [kb.btn("🧠 المحرك", "a:engine"), kb.btn("📰 درع الأخبار", "a:newsguard")],
    ]
    await render(q, "\n".join(lines), kb.admin_nav(rows_kb), bot)


_DIAG_MARKETS_PER_PAGE = 6
_DIAG_RUNS_PER_PAGE = 8


def _runs_page_sync(per: int, offset: int) -> list[dict]:
    """(thread-offload helper) one paged slice of the engine_runs journal."""
    with db.conn() as c:
        return [dict(r) for r in c.execute(
            "SELECT symbol, timeframe, result, detail, created_at FROM engine_runs "
            "ORDER BY id DESC LIMIT ? OFFSET ?", (per + 1, offset)).fetchall()]


async def admin_engine_diag_markets(q, uid, context, bot, page: int = 0):
    """Paged per-market verdicts — royal Arabic, compact, no wall of text."""
    d = await afn.run(_engine_diag_sync)
    items = []
    for sym in d["armed"]:
        r = d["runs"].get(sym)
        if not r:
            items.append(f"• <b>{esc(sym)}</b> — ⚪ لم تُجرَّب بعد في دورة مسجلة")
            continue
        line = f"• <b>{esc(sym)}</b> {esc(r['timeframe'])}: {theme.ar_run_line(r['result'], r['detail'] or '')}"
        items.append(line)
    total_pages = max(1, (len(items) + _DIAG_MARKETS_PER_PAGE - 1) // _DIAG_MARKETS_PER_PAGE)
    page = max(0, min(page, total_pages - 1))
    chunk = items[page * _DIAG_MARKETS_PER_PAGE:(page + 1) * _DIAG_MARKETS_PER_PAGE]
    body = (
        "🌐 <b><u>قرار كل سوق مسلّح — آخر دورة</u></b>\n"
        f"<blockquote>▎صفحة {page + 1}/{total_pages} · {len(items)} سوق — كل قرار بسببه العربي الصادق</blockquote>\n\n"
        + "\n".join(chunk)
    )
    rows = []
    if total_pages > 1:
        nav = []
        if page > 0:
            nav.append(kb.btn("⬅️ السابق", f"a:enginediagmarkets:{page - 1}"))
        nav.append(kb.btn(f"📄 {page + 1}/{total_pages}", "a:plannoop"))
        if page < total_pages - 1:
            nav.append(kb.btn("➡️ التالي", f"a:enginediagmarkets:{page + 1}"))
        rows.append(nav)
    rows.append([kb.btn("🔍 التشخيص", "a:enginediag")])
    await render(q, body, kb.admin_nav(rows), bot)


async def admin_engine_diag_runs(q, uid, context, bot, page: int = 0):
    """Paged engine_runs journal — Arabic verdicts, 8 per page, honest timestamps."""
    per = _DIAG_RUNS_PER_PAGE
    rows = await afn.run(_runs_page_sync, per, page * per)
    has_more = len(rows) > per
    rows = rows[:per]
    if not rows:
        body = "🧾 <b>سجل المحرك فارغ</b>\n<i>شغّل «🚀 دورة فورية الآن» من صفحة المحرك.</i>"
    else:
        lines = ["🧾 <b><u>سجل المحرك — آخر القرارات</u></b>",
                 f"<blockquote>▎صفحة {page + 1} · قرارات موثقة بأسبابها العربية</blockquote>", ""]
        for r in rows:
            lines.append(f"• {human_dt(r['created_at'])} · <b>{esc(r['symbol'])}</b> {esc(r['timeframe'])}\n"
                         f"   {theme.ar_run_line(r['result'], r['detail'] or '')}")
        body = "\n".join(lines)
    nav = []
    if page > 0:
        nav.append(kb.btn("⬅️ السابق", f"a:enginediagruns:{page - 1}"))
    if has_more:
        nav.append(kb.btn("➡️ التالي", f"a:enginediagruns:{page + 1}"))
    rows_kb = ([nav] if nav else []) + [[kb.btn("🔍 التشخيص", "a:enginediag")]]
    await render(q, body, kb.admin_nav(rows_kb), bot)


async def admin_engine_diag_ceiling(q, uid, context, bot):
    """The hidden ceiling, fully visible, with REAL management buttons."""
    from fysb.engines import risk as _risk
    rk = await afn.run(_risk.open_caps_snapshot)
    lines = [
        "🛡 <b><u>إدارة السقف الخفي — العدّادات الحية</u></b>",
        "",
        f"<blockquote>⛔ <b>{rk['open']}/{rk['cap_open']}</b> "
        f"{'ممتلئ — كل إشارة جديدة محجوبة حتى تُغلق صفقة أو يُعدّل السقف' if rk['ceiling_full'] else 'متاح — يوجد مجال للإصدار'}</blockquote>",
        "",
        f"▎أعلى سوق: <b>{esc(rk['worst_symbol'] or '—')} {rk['worst_symbol_open']}/{rk['cap_per_symbol']}</b>"
        f"{' ⛔ ممتلئ' if rk['symbol_full'] else ''}",
        f"▎الترابط الاتجاهي: <b>{esc(rk['worst_bucket'] or '—')} {rk['worst_bucket_open']}/{rk['cap_correlated']}</b>"
        f"{' ⛔ ممتلئ' if rk['correlated_full'] else ''}",
        f"▎قاطع اليوم: <b>{rk['daily_pnl_pct']:+.1f}%</b> (الحد -{rk['max_daily_loss_pct']:.1f}%) "
        f"{'⛔ مشغّل' if rk['daily_breached'] else '✅'}",
        "",
        "<b>📜 الإشارات المفتوحة الآن:</b>",
    ]
    for r in rk["open_rows"]:
        lines.append(f"  • <b>{esc(r['symbol'])}</b> {esc(r['direction'])} — منذ <b>{r['hours']} س</b>")
    if not rk["open_rows"]:
        lines.append("  <i>لا إشارات مفتوحة — السقف حر تماماً.</i>")
    rows_kb = [
        [kb.btn("📡 إدارة الإشارات المفتوحة", "a:signals", style="success")],
        [kb.btn("🛡 إدارة المخاطر (رفع السقف / القتل)", "a:risk", style="primary")],
        [kb.btn("🔍 التشخيص", "a:enginediag")],
    ]
    await render(q, "\n".join(lines), kb.admin_nav(rows_kb), bot)


async def admin_engine_diag_probe(q, uid, context, bot):
    """Standalone probe card — the result ALONE, no repeated wall of diagnostics."""
    notice = await afn.run(_probe_market_data_sync)
    await afn.run(audit_log.log, uid, "enginediag:probe")
    body = ("🧪 <b><u>فحص بيانات حي — النتائج الفعلية الآن</u></b>\n"
            f"<blockquote>{notice}</blockquote>\n"
            "<i>الفحص يجلب البيانات من السلسلة الحقيقية لحظة الطلب — لا كاش ولا افتراض.</i>")
    rows_kb = [
        [kb.btn("🔄 إعادة الفحص", "a:enginediagprobe", style="success")],
        [kb.btn("🔍 التشخيص الكامل", "a:enginediag"), kb.btn("🧠 المحرك", "a:engine")],
    ]
    await render(q, body, kb.admin_nav(rows_kb), bot)


# --- v6.25.0 / v7.4.0 Live News Guard, Auto-Broadcast, Leaderboard & Strength -
def _news_guard_state_sync(force_refresh: bool = False) -> tuple[bool, int, bool, dict, str]:
    from fysb.engines import news_guard as _ng
    on = _ng.is_enabled()
    win = _ng.window_minutes()
    auto_b = _ng.auto_broadcast_enabled()
    dests = _ng.broadcast_destinations()
    body = _ng.render_news_calendar_html(enabled=on, buf_min=win, force_refresh=force_refresh)
    return on, win, auto_b, dests, body


def _news_guard_toggle_sync() -> bool:
    cur = (db.get_setting("news_guard_enabled", "1") or "1") == "1"
    nxt = "0" if cur else "1"
    db.set_setting("news_guard_enabled", nxt)
    return nxt == "1"


def _news_guard_set_win_sync(mins: int) -> int:
    m = max(5, min(120, int(mins)))
    db.set_setting("news_guard_minutes", str(m))
    return m


def _news_auto_toggle_sync() -> bool:
    cur = (db.get_setting("news_auto_broadcast", "0") or "0") == "1"
    nxt = "0" if cur else "1"
    db.set_setting("news_auto_broadcast", nxt)
    return nxt == "1"


def _news_dest_toggle_sync(dest: str) -> bool:
    key_map = {
        "users": "news_broadcast_users",
        "partners": "news_broadcast_partners",
        "channels": "news_broadcast_channels",
    }
    k = key_map.get((dest or "").lower())
    if not k:
        return False
    cur = (db.get_setting(k, "1") or "1") == "1"
    nxt = "0" if cur else "1"
    db.set_setting(k, nxt)
    return nxt == "1"


async def admin_news_guard(q, uid, context, bot, *, force_refresh: bool = False, notice: str = ""):
    on, win, auto_b, dests, body = await afn.run(_news_guard_state_sync, force_refresh)
    if notice:
        body = f"<blockquote>{esc(notice)}</blockquote>\n\n" + body
    rows_kb = [
        [
            kb.btn("⏹ إيقاف الدرع" if on else "▶️ تفعيل الدرع", "a:newsguardtoggle",
                   style="danger" if on else "success"),
            kb.btn(
                "📢 البث التلقائي: 🟢 مفعّل" if auto_b else "📢 البث التلقائي: ⚪ متوقف",
                "a:newsauto",
                style="success" if auto_b else "primary",
            ),
        ],
        [
            kb.btn(f"{'🟢' if dests['users'] else '⚪'} المستخدمون", "a:newsdest:users",
                   style="success" if dests["users"] else "primary"),
            kb.btn(f"{'🟢' if dests['partners'] else '⚪'} الشركاء", "a:newsdest:partners",
                   style="success" if dests["partners"] else "primary"),
            kb.btn(f"{'🟢' if dests['channels'] else '⚪'} القنوات", "a:newsdest:channels",
                   style="success" if dests["channels"] else "primary"),
        ],
        [
            kb.btn(f"{'✅ ' if win == 15 else ''}±15د", "a:newsguardwin:15", style="success" if win == 15 else "primary"),
            kb.btn(f"{'✅ ' if win == 30 else ''}±30د", "a:newsguardwin:30", style="success" if win == 30 else "primary"),
            kb.btn(f"{'✅ ' if win == 45 else ''}±45د", "a:newsguardwin:45", style="success" if win == 45 else "primary"),
            kb.btn(f"{'✅ ' if win == 60 else ''}±60د", "a:newsguardwin:60", style="success" if win == 60 else "primary"),
        ],
        [
            kb.btn("🟢 الفتح عند الفراغ: مفعّل" if db.get_setting("news_guard_fail_open", "0") == "1"
                   else "⚪ الفتح عند الفراغ: متوقف", "a:newsfailopen",
                   style="success" if db.get_setting("news_guard_fail_open", "0") == "1" else "primary"),
            kb.btn(f"🩺 حصر الكاش: {max(6, min(168, db.get_setting_int('news_guard_cache_max_hours', 48)))}س",
                   "a:newscachehrs", style="primary"),
        ],
        [kb.btn("📤 إرسال نشرة الأخبار الآن", "a:newssendnow", style="success")],
        [kb.btn("🔄 تحديث التقويم الآن", "a:newsrefresh", style="primary"), kb.btn("↩️ المحرك", "a:engine")],
    ]
    await render(q, body, kb.admin_nav(rows_kb), bot)


async def admin_news_guard_toggle(q, uid, context, bot):
    nxt = await afn.run(_news_guard_toggle_sync)
    await afn.run(audit_log.log, uid, f"news_guard_enabled:{int(nxt)}")
    await admin_news_guard(q, uid, context, bot)


async def admin_news_fail_open(q, uid, context, bot):
    """v8.11.0 (P3): on a genuine calendar data VOID, allow issuance (logged)
    instead of the documented UNKNOWN-blocks rule. Admin's explicit choice."""
    cur = (db.get_setting("news_guard_fail_open", "0") or "0") == "1"
    nxt = "0" if cur else "1"
    await afn.run(db.set_setting, "news_guard_fail_open", nxt)
    await afn.run(audit_log.log, uid, f"news_guard_fail_open:{nxt}")
    note = ("🟢 وضع الفتح عند الفراغ مفعّل: انعدام بيانات التقويم نهائياً لن يمنع الإصدار "
            "(الأخبار المعروفة في الكاش تظل تحجب كالمعتاد)."
            if nxt == "1" else
            "⚪ الوضع الافتراضي الصارم: انعدام بيانات التقويم يمنع الإصدار (UNKNOWN).")
    await admin_news_guard(q, uid, context, bot, notice=note)


_CACHE_HRS_CHOICES = (6, 12, 24, 48, 72, 168)


async def admin_news_cache_hours(q, uid, context, bot):
    """v8.11.0 (P3): pick how long cached calendar events stay usable for the
    High-impact block decision when live refresh fails (absolute timestamps
    stay truthful)."""
    cur = max(6, min(168, db.get_setting_int("news_guard_cache_max_hours", 48)))
    row = []
    rows_kb = []
    for h in _CACHE_HRS_CHOICES:
        row.append(kb.btn(f"{'✅ ' if cur == h else ''}{h}س", f"a:newscachehrsset:{h}",
                          style="success" if cur == h else "primary"))
        if len(row) == 3:
            rows_kb.append(row)
            row = []
    if row:
        rows_kb.append(row)
    rows_kb.append([kb.btn("↩️ درع الأخبار", "a:newsguard")])
    body = (
        "🩺 <b><u>حصر صلاحية بيانات التقويم المحفوظة</u></b>\n"
        "<blockquote>▎عند تعذر التحديث الحي، تعمل قرارات حظر الأخبار ببيانات محفوظة صالحة — "
        "أزمنة الأحداث مطلقة وصادقة.\n"
        f"▎الاختيار الحالي: <b>{cur} ساعة</b>\n"
        "▎تجاوز الحد أو انعدام البيانات نهائياً = حالة «UNKNOWN» (تمنع الإصدار افتراضياً، "
        "أو تسمح به إن فعّلت «الفتح عند الفراغ»).</blockquote>"
    )
    await render(q, body, kb.admin_nav(rows_kb), bot)


async def admin_news_cache_hours_set(q, uid, context, bot, hours: int):
    h = max(6, min(168, int(hours)))
    await afn.run(db.set_setting, "news_guard_cache_max_hours", h)
    await afn.run(audit_log.log, uid, f"news_guard_cache_max_hours:{h}")
    await admin_news_guard(q, uid, context, bot, notice=f"🩺 صارت بيانات التقويم المحفوظة صالحة حتى {h} ساعة عند تعذر التحديث.")


async def admin_news_guard_win(q, uid, context, bot, mins: int):
    m = await afn.run(_news_guard_set_win_sync, mins)
    await afn.run(audit_log.log, uid, f"news_guard_minutes:{m}")
    await admin_news_guard(q, uid, context, bot)


async def admin_news_auto_toggle(q, uid, context, bot):
    nxt = await afn.run(_news_auto_toggle_sync)
    await afn.run(audit_log.log, uid, f"news_auto_broadcast:{int(nxt)}")
    note = "🟢 تم تفعيل البث التلقائي الفاخر للأخبار للمستلمين المحددين." if nxt else "⚪ تم إيقاف البث التلقائي للأخبار."
    await admin_news_guard(q, uid, context, bot, notice=note)


async def admin_news_dest_toggle(q, uid, context, bot, dest: str):
    nxt = await afn.run(_news_dest_toggle_sync, dest)
    await afn.run(audit_log.log, uid, f"news_broadcast_dest:{dest}={int(nxt)}")
    await admin_news_guard(q, uid, context, bot, notice="✅ تم تحديث وجهات بث الأخبار.")


async def admin_news_send_now(q, uid, context, bot):
    from fysb.engines import news_guard as _ng
    card = await afn.run(_ng.render_luxury_news_digest_card, None, force_refresh=True)
    stats = await _ng.broadcast_news_to_targets(bot, card, include_admin=True)
    await afn.run(
        audit_log.log, uid,
        f"news_broadcast_now:u={stats['users']},p={stats['partners']},c={stats['channels']},f={stats['failed']}",
    )
    note = (
        f"📤 تم إرسال النشرة الاقتصادية الفاخرة الآن — "
        f"مستخدمون: {stats['users']} · شركاء: {stats['partners']} · قنوات: {stats['channels']}"
        + (f" · فشل: {stats['failed']}" if stats["failed"] else "")
    )
    await admin_news_guard(q, uid, context, bot, notice=note)


def _active_channel_chat_ids() -> list[str]:
    with db.conn() as c:
        rows = c.execute("SELECT chat_id FROM broadcast_channels WHERE enabled=1").fetchall()
    return [str(r["chat_id"]) for r in rows if r["chat_id"]]


async def admin_leaderboard(q, uid, context, bot, period: str = "30d", notice: str = ""):
    if period not in ("7d", "30d", "all"):
        period = "30d"
    from fysb.services import leaderboard as _lb
    body = await afn.run(_lb.render_leaderboard_card, period, False)
    if notice:
        body = f"<blockquote>{esc(notice)}</blockquote>\n\n" + body
    p_row = [
        kb.btn("📅 7 أيام" + (" ✓" if period == "7d" else ""), "a:lb:7d", style="success" if period == "7d" else "primary"),
        kb.btn("📆 30 يوماً" + (" ✓" if period == "30d" else ""), "a:lb:30d", style="success" if period == "30d" else "primary"),
        kb.btn("📚 الكل" + (" ✓" if period == "all" else ""), "a:lb:all", style="success" if period == "all" else "primary"),
    ]
    rows_kb = [
        p_row,
        [kb.btn("📢 نشر المصفوفة في القنوات الآن", f"a:lbpublish:{period}", style="success")],
        [kb.btn("🧭 قوة العملات (8)", "a:strength", style="primary"), kb.btn("📰 درع الأخبار", "a:newsguard", style="primary")],
        [kb.btn("↩️ المحرك", "a:engine")],
    ]
    await render(q, body, kb.admin_nav(rows_kb), bot)


async def admin_leaderboard_publish(q, uid, context, bot, period: str = "30d"):
    if period not in ("7d", "30d", "all"):
        period = "30d"
    from fysb.services import leaderboard as _lb
    card_txt = await afn.run(_lb.render_leaderboard_card, period, True)
    chat_ids = await afn.run(_active_channel_chat_ids)
    sent = 0
    failed = 0
    for cid in chat_ids:
        try:
            target = int(cid) if cid.lstrip("-").isdigit() else cid
            await bot.send_message(target, card_txt, parse_mode="HTML")
            sent += 1
        except Exception:
            failed += 1
    await afn.run(audit_log.log, uid, f"leaderboard_published:{period}:sent={sent}")
    note = (
        f"✅ تم نشر مصفوفة الاستراتيجيات ({period}) في {sent} قناة" + (f" · فشل {failed}" if failed else "")
        if chat_ids else "⚠️ لا توجد قنوات مفعّلة حالياً للنشر."
    )
    await admin_leaderboard(q, uid, context, bot, period=period, notice=note)


async def admin_strength(q, uid, context, bot, *, force_refresh: bool = False):
    from fysb.engines import intelligence as _intel
    body = await afn.run(_intel.render_strength_card, None, force_refresh=force_refresh)
    rows_kb = [
        [kb.btn("🔄 تحديث فوري (حي)", "a:strengthrefresh", style="success"), kb.btn("🏆 مصفوفة الاستراتيجيات", "a:leaderboard", style="primary")],
        [kb.btn("📰 درع الأخبار", "a:newsguard", style="primary"), kb.btn("↩️ المحرك", "a:engine")],
    ]
    await render(q, body, kb.admin_nav(rows_kb), bot)


# --- v6.26.0 Local Zero-Cost Pillow Charts & Before/After Proof Publisher -----
def _toggle_charts_sync() -> bool:
    cur = (db.get_setting("charts_enabled", "1") or "1") == "1"
    nxt = "0" if cur else "1"
    db.set_setting("charts_enabled", nxt)
    return nxt == "1"


def _charts_settings_sync() -> tuple[bool, int, bool, int]:
    enabled = (db.get_setting("charts_enabled", "1") or "1") == "1"
    sched = max(0, db.get_setting_int("chart_schedule_minutes", 0))
    to_ch = (db.get_setting("chart_schedule_channels", "0") or "0") == "1"
    with db.conn() as c:
        open_n = c.execute(
            "SELECT COUNT(*) n FROM signals WHERE status IN ('RUNNING','TP1_HIT','TP2_HIT') AND (closed_at IS NULL OR closed_at='')"
        ).fetchone()["n"]
    return enabled, sched, to_ch, open_n


def _open_running_signals_sync(limit: int = 3) -> list[dict]:
    with db.conn() as c:
        rows = c.execute(
            """SELECT * FROM signals
               WHERE status IN ('RUNNING','TP1_HIT','TP2_HIT')
                 AND (closed_at IS NULL OR closed_at='')
               ORDER BY id DESC LIMIT ?""",
            (max(1, int(limit)),),
        ).fetchall()
    return [dict(r) for r in rows]


async def admin_charts_menu(q, uid, context, bot, notice: str = ""):
    from fysb.services import chart_channels as _cch
    enabled, sched, to_ch, open_n = await afn.run(_charts_settings_sync)
    cchs = await afn.run(_cch.list_channels, False)
    active_cchs = [c for c in cchs if int(c.get("enabled") or 0) == 1]
    sched_txt = "⏹ متوقفة (يدوي فقط)" if sched <= 0 else f"🟢 كل <b>{sched}</b> دقيقة تلقائياً"
    dest_txt = "📺 الأدمن + جميع القنوات المفعّلة" if to_ch else "👑 الأدمن فقط"
    cch_line = (
        f"<b>{len(active_cchs)}</b> مفعّلة من أصل <b>{len(cchs)}</b> "
        f"({', '.join(esc(c['title'] or c['chat_id']) + ': ' + esc(_cch.format_markets_label(c['markets'])) for c in active_cchs[:3])}"
        + (" …" if len(active_cchs) > 3 else "") + ")"
        if active_cchs else f"<b>0</b> (اضغط لإضافة قناة تستقبل الشارت فقط حسب السوق)"
    )
    body = (
        "📊 <b><u>مركز الشارتات الملكية والجدولة التلقائية</u></b>\n"
        "<blockquote>"
        f"▎⚡ توليد الشارتات المحلية (مجاني 100%): <b>{'🟢 مفعّل' if enabled else '⚪ متوقف'}</b>\n"
        f"▎⏱ جدولة تحديث شارتات الصفقات المفتوحة: {sched_txt}\n"
        f"▎📤 وجهة الإرسال المجدول: <b>{dest_txt}</b>\n"
        f"▎📡 الصفقات المفتوحة الجارية الآن: <b>{open_n}</b>\n"
        f"▎📺 قنوات الشارت فقط (حسب السوق): {cch_line}"
        "</blockquote>"
        + (f"\n\n<i>{notice}</i>" if notice else "")
    )
    rows_kb = [
        [kb.btn(
            "🔴 إيقاف الشارتات" if enabled else "🟢 تفعيل الشارتات",
            "a:chartstoggle",
            style="danger" if enabled else "success",
        )],
        [kb.btn(f"📺 قنوات الشارت فقط حسب السوق ({len(active_cchs)}/{len(cchs)})", "a:chartchs", style="success")],
        [
            kb.btn(f"{'✅ ' if sched == 0 else ''}⏹ إيقاف", "a:chartsched:0", style="danger" if sched == 0 else "primary"),
            kb.btn(f"{'✅ ' if sched == 5 else ''}⏱ 5د", "a:chartsched:5", style="success" if sched == 5 else "primary"),
            kb.btn(f"{'✅ ' if sched == 10 else ''}⏱ 10د", "a:chartsched:10", style="success" if sched == 10 else "primary"),
        ],
        [
            kb.btn(f"{'✅ ' if sched == 15 else ''}⏱ 15د", "a:chartsched:15", style="success" if sched == 15 else "primary"),
            kb.btn(f"{'✅ ' if sched == 30 else ''}⏱ 30د", "a:chartsched:30", style="success" if sched == 30 else "primary"),
            kb.btn(f"{'✅ ' if sched == 60 else ''}⏱ 60د", "a:chartsched:60", style="success" if sched == 60 else "primary"),
        ],
        [kb.btn(
            f"📺 النشر المجدول للقنوات العامة: {'🟢 مفعّل' if to_ch else '⚪ للأدمن فقط'}",
            "a:chartschedch",
            style="success" if to_ch else "primary",
        )],
        [kb.btn("📤 إرسال شارتات الصفقات المفتوحة الآن", "a:chartsendnow", style="success")],
        [kb.btn("↩️ المحرك", "a:engine")],
    ]
    await render(q, body, kb.admin_nav(rows_kb), bot)


async def admin_chart_channels_menu(q, uid, context, bot, notice: str = ""):
    from fysb.services import chart_channels as _cch
    cchs = await afn.run(_cch.list_channels, False)
    lines = []
    rows_kb = [[kb.btn("➕ إضافة قناة شارت فقط", "a:chartch_add", style="success")]]
    for ch in cchs:
        st = "🟢" if int(ch.get("enabled") or 0) == 1 else "⚪"
        mkts = _cch.format_markets_label(ch.get("markets") or "ALL")
        title = ch.get("title") or ch.get("chat_id")
        lines.append(f"▎{st} <b>{esc(title)}</b> (<code>{esc(ch['chat_id'])}</code>) ← <b>{esc(mkts)}</b>")
        rows_kb.append([
            kb.btn(f"{st} {title[:18]} · {mkts[:18]}", f"a:chartch:{ch['id']}", style="primary"),
        ])
    if not lines:
        lines.append("▎لا توجد قنوات شارت مخصصة بعد.\n▎اضغط «➕ إضافة قناة شارت فقط» ثم اختر السوق (مثلاً XAUUSD أو BTCUSD أو الكل) ليرسل لها البوت الشارتات فقط تلقائياً!")
    body = (
        "📺 <b><u>قنوات الشارتات المخصصة حسب السوق (شارت فقط)</u></b>\n"
        "<blockquote>"
        + "\n".join(lines)
        + "</blockquote>"
        + (f"\n\n<i>{notice}</i>" if notice else "")
    )
    rows_kb.append([kb.btn("↩️ مركز الشارتات", "a:chartsmenu")])
    await render(q, body, kb.admin_nav(rows_kb), bot)


async def admin_chart_channel_add_prompt(q, uid, context, bot):
    context.user_data["awaiting_chart_channel_add"] = True
    body = (
        "➕ <b><u>إضافة قناة شارت فقط (حسب السوق)</u></b>\n"
        "<blockquote>"
        "▎1️⃣ أضف البوت كـ <b>مشرف (Admin)</b> في القناة أو المجموعة مع صلاحية إرسال الصور.\n"
        "▎2️⃣ أرسل الآن معرف القناة هنا (مثال: <code>@MyGoldCharts</code> أو <code>-1001234567890</code>).\n"
        "▎💡 يمكنك كتابة اسم القناة في سطر ثانٍ اختيارياً.\n"
        "▎3️⃣ بعدها مباشرة ستختار <b>السوق</b> الذي يرسل له الشارت فقط!"
        "</blockquote>"
    )
    await render(q, body, kb.admin_nav([[kb.btn("↩️ إلغاء", "a:chartchs")]]), bot)


async def admin_chart_channel_view(q, uid, context, bot, cid: int, notice: str = ""):
    from fysb.services import chart_channels as _cch
    ch = await afn.run(_cch.get_channel, cid)
    if not ch:
        await admin_chart_channels_menu(q, uid, context, bot, notice="⚠️ القناة غير موجودة.")
        return
    en = int(ch.get("enabled") or 0) == 1
    s_sig = int(ch.get("send_on_signal") or 0) == 1
    s_trk = int(ch.get("send_on_tracking") or 0) == 1
    s_sch = int(ch.get("send_on_schedule") or 0) == 1
    mkts = _cch.format_markets_label(ch.get("markets") or "ALL")
    body = (
        f"📺 <b><u>إدارة قناة الشارت — {esc(ch.get('title') or ch['chat_id'])}</u></b>\n"
        "<blockquote>"
        f"▎🆔 المعرّف: <code>{esc(ch['chat_id'])}</code>\n"
        f"▎⚡ الحالة: <b>{'🟢 مفعّلة' if en else '⚪ متوقفة'}</b>\n"
        f"▎🌐 الأسواق المختارة (شارت فقط): <b>{esc(mkts)}</b>\n"
        f"▎📡 عند صدور إشارة جديدة: <b>{'🟢 نعم' if s_sig else '⚪ لا'}</b>\n"
        f"▎🎯 عند تحديثات التتبع (TP/SL/دخول): <b>{'🟢 نعم' if s_trk else '⚪ لا'}</b>\n"
        f"▎⏱ مع الجدولة التلقائية الدورية: <b>{'🟢 نعم' if s_sch else '⚪ لا'}</b>"
        "</blockquote>"
        + (f"\n\n<i>{notice}</i>" if notice else "")
    )
    rows_kb = [
        [kb.btn("🌐 اختيار الأسواق لهذه القناة", f"a:chartch_mkts:{cid}", style="success")],
        [
            kb.btn(f"{'🟢' if en else '⚪'} الحالة", f"a:chartch_tog:{cid}", style="success" if en else "danger"),
            kb.btn("📤 إرسال شارت الآن", f"a:chartch_test:{cid}", style="primary"),
        ],
        [
            kb.btn(f"{'🟢' if s_sig else '⚪'} عند الإشارة", f"a:chartch_flg:{cid}:send_on_signal"),
            kb.btn(f"{'🟢' if s_trk else '⚪'} عند التتبع", f"a:chartch_flg:{cid}:send_on_tracking"),
            kb.btn(f"{'🟢' if s_sch else '⚪'} المجدول", f"a:chartch_flg:{cid}:send_on_schedule"),
        ],
        [kb.btn("🗑 حذف القناة", f"a:chartch_del:{cid}", style="danger")],
        [kb.btn("↩️ قنوات الشارتات", "a:chartchs")],
    ]
    await render(q, body, kb.admin_nav(rows_kb), bot)


async def admin_chart_channel_toggle(q, uid, context, bot, cid: int):
    from fysb.services import chart_channels as _cch
    en = await afn.run(_cch.toggle_channel, cid)
    await afn.run(audit_log.log, uid, f"chart_channel_toggle:{cid}:{int(en)}")
    await admin_chart_channel_view(q, uid, context, bot, cid, notice="✅ تم تحديث حالة القناة.")


async def admin_chart_channel_flag_toggle(q, uid, context, bot, cid: int, field: str):
    from fysb.services import chart_channels as _cch
    await afn.run(_cch.toggle_flag, cid, field)
    await admin_chart_channel_view(q, uid, context, bot, cid, notice="✅ تم تحديث خيار الإرسال.")


async def admin_chart_channel_markets(q, uid, context, bot, cid: int, notice: str = ""):
    from fysb.services import chart_channels as _cch
    ch = await afn.run(_cch.get_channel, cid)
    if not ch:
        await admin_chart_channels_menu(q, uid, context, bot, notice="⚠️ القناة غير موجودة.")
        return
    selected = _cch.parse_markets_csv(ch.get("markets") or "ALL")
    is_all = selected == ["ALL"]
    all_mkts = await afn.run(_cch.available_markets)
    body = (
        f"🌐 <b><u>اختر الأسواق لقناة الشارت — {esc(ch.get('title') or ch['chat_id'])}</u></b>\n"
        "<blockquote>"
        f"▎✅ المختار حالياً: <b>{esc(_cch.format_markets_label(ch.get('markets') or 'ALL'))}</b>\n"
        "▎💡 اضغط على أي سوق لتفعيله أو إلغائه — ستستقبل هذه القناة <b>الشارت فقط</b> للأسواق المختارة هنا."
        "</blockquote>"
        + (f"\n\n<i>{notice}</i>" if notice else "")
    )
    rows_kb = [[
        kb.btn(f"{'✅ ' if is_all else ''}🌐 جميع الأسواق (ALL)", f"a:chartch_mkt:{cid}:ALL", style="success" if is_all else "primary")
    ]]
    row_buf = []
    for sym in all_mkts:
        on = (not is_all) and (sym in selected)
        row_buf.append(kb.btn(f"{'✅ ' if on else ''}{sym}", f"a:chartch_mkt:{cid}:{sym}", style="success" if on else "primary"))
        if len(row_buf) == 3:
            rows_kb.append(row_buf)
            row_buf = []
    if row_buf:
        rows_kb.append(row_buf)
    rows_kb.append([kb.btn("✔️ حفظ والعودة للقناة", f"a:chartch:{cid}", style="success")])
    await render(q, body, kb.admin_nav(rows_kb), bot)


async def admin_chart_channel_market_toggle(q, uid, context, bot, cid: int, sym: str):
    from fysb.services import chart_channels as _cch
    mkts = await afn.run(_cch.toggle_market, cid, sym)
    await afn.run(audit_log.log, uid, f"chart_channel_markets:{cid}:{mkts}")
    await admin_chart_channel_markets(q, uid, context, bot, cid, notice=f"✅ الأسواق المحددة الآن: {esc(_cch.format_markets_label(mkts))}")


async def admin_chart_channel_test(q, uid, context, bot, cid: int):
    import io as _io
    from fysb.handlers.user_pages import build_signal_chart_bundle
    from fysb.services import chart_channels as _cch
    ch = await afn.run(_cch.get_channel, cid)
    if not ch:
        await admin_chart_channels_menu(q, uid, context, bot, notice="⚠️ القناة غير موجودة.")
        return
    sigs = await afn.run(_open_running_signals_sync, 15)
    matched = [s for s in sigs if _cch.market_matches(ch.get("markets") or "ALL", s.get("symbol") or "")]
    if not matched:
        # Fall back to latest signal matching the channel's markets
        def _latest_matching():
            with db.conn() as c:
                rows = [dict(r) for r in c.execute("SELECT * FROM signals ORDER BY id DESC LIMIT 30").fetchall()]
            return [r for r in rows if _cch.market_matches(ch.get("markets") or "ALL", r.get("symbol") or "")]
        matched = await afn.run(_latest_matching)
    if not matched:
        await admin_chart_channel_view(q, uid, context, bot, cid, notice="⚠️ لا توجد إشارة مطابقة لأسواق هذه القناة بعد.")
        return
    sig = matched[0]
    b = await asyncio.to_thread(build_signal_chart_bundle, sig, False)
    if not b or not b.get("png"):
        await admin_chart_channel_view(q, uid, context, bot, cid, notice="⚠️ تعذر توليد الشارت حالياً (تحقق من مزود الشموع).")
        return
    cap = _cch.build_luxury_signal_chart_caption(sig, b.get("metrics"))
    try:
        target = int(ch["chat_id"]) if str(ch["chat_id"]).lstrip("-").isdigit() else ch["chat_id"]
        await bot.send_photo(chat_id=target, photo=_io.BytesIO(b["png"]), caption=cap, parse_mode="HTML")
        await afn.run(_cch.touch_last_sent, [cid])
        await admin_chart_channel_view(q, uid, context, bot, cid, notice=f"✅ تم إرسال شارت {esc(sig['symbol'])} إلى القناة بنجاح!")
    except Exception as exc:
        await admin_chart_channel_view(q, uid, context, bot, cid, notice=f"❌ تعذر الإرسال للقناة ({esc(str(exc)[:80])}) — تأكد أن البوت مشرف.")


async def admin_chart_channel_delete(q, uid, context, bot, cid: int):
    from fysb.services import chart_channels as _cch
    await afn.run(_cch.delete_channel, cid)
    await afn.run(audit_log.log, uid, f"chart_channel_deleted:{cid}")
    await admin_chart_channels_menu(q, uid, context, bot, notice="🗑 تم حذف قناة الشارت.")


async def admin_charts_toggle(q, uid, context, bot):
    enabled = await afn.run(_toggle_charts_sync)
    await afn.run(audit_log.log, uid, f"charts_enabled:{int(enabled)}")
    await admin_charts_menu(q, uid, context, bot, notice="✅ تم تحديث حالة الشارتات.")


async def admin_chart_sched_set(q, uid, context, bot, minutes: int):
    m = int(minutes) if int(minutes) in (0, 5, 10, 15, 30, 60) else 0
    await afn.run(db.set_setting, "chart_schedule_minutes", str(m))
    await afn.run(audit_log.log, uid, f"chart_schedule_minutes:{m}")
    note = "⏹ تم إيقاف الإرسال المجدول للشارتات." if m == 0 else f"✅ تم تفعيل إرسال الشارتات تلقائياً كل {m} دقيقة."
    await admin_charts_menu(q, uid, context, bot, notice=note)


async def admin_chart_sched_ch_toggle(q, uid, context, bot):
    cur = (await afn.run(db.get_setting, "chart_schedule_channels", "0") or "0") == "1"
    nxt = "0" if cur else "1"
    await afn.run(db.set_setting, "chart_schedule_channels", nxt)
    await afn.run(audit_log.log, uid, f"chart_schedule_channels:{nxt}")
    await admin_charts_menu(q, uid, context, bot, notice="✅ تم تحديث وجهة الإرسال المجدول.")


async def admin_chart_send_now(q, uid, context, bot):
    sigs = await afn.run(_open_running_signals_sync, 10)
    if not sigs:
        await admin_charts_menu(q, uid, context, bot, notice="ℹ️ لا توجد صفقات مفتوحة جارية حالياً لإرسال شارتها.")
        return
    from fysb.handlers.user_pages import build_signal_chart_bundle
    from fysb.services import chart_channels as _cch
    import io as _io
    to_ch = (await afn.run(db.get_setting, "chart_schedule_channels", "0") or "0") == "1"
    chat_ids = await afn.run(_active_channel_chat_ids) if to_ch else []
    sent_admin, sent_ch, sent_cch = 0, 0, 0
    for sig in sigs:
        b = await asyncio.to_thread(build_signal_chart_bundle, sig, sig.get("pips_result", 0) > 0)
        if not b["png"]:
            continue
        cap = (
            f"📊 <b><u>تحديث شارت حي — {esc(sig['symbol'])} {sig['direction']}</u></b>\n"
            f"▎⏱ {esc(sig['timeframe'])} · 🧠 {esc(sig['strategy'])}\n"
            f"{b['pips_line']}\n"
            f"▎🛡 متابعة مباشرة من محرك ForexYemeni Royal."
        )
        if sent_admin < 5:
            try:
                pub_kb = kb.InlineKeyboardMarkup([[
                    kb.btn("🏆 نشر الإثبات للقنوات الآن", f"a:sigproof:{sig['id']}", style="success"),
                ]])
                await bot.send_photo(chat_id=uid, photo=_io.BytesIO(b["png"]), caption=cap, reply_markup=pub_kb, parse_mode="HTML")
                sent_admin += 1
            except Exception:
                pass
        for cid in chat_ids:
            try:
                target = int(cid) if cid.lstrip("-").isdigit() else cid
                await bot.send_photo(chat_id=target, photo=_io.BytesIO(b["png"]), caption=cap, parse_mode="HTML")
                sent_ch += 1
            except Exception:
                pass
        sent_cch += await _cch.deliver_chart_to_matching_channels(
            bot, sig, b["png"], cap, mode="schedule", skip_private=True
        )
    parts = [f"✅ تم إرسال {sent_admin} شارت حي لك"]
    if sent_cch:
        parts.append(f"{sent_cch} لقنوات الشارت المخصصة")
    if to_ch:
        parts.append(f"{sent_ch} للقنوات العامة")
    await admin_charts_menu(q, uid, context, bot, notice=" · ".join(parts) + ".")


async def admin_signal_chart(q, uid, context, bot, sid: int):
    r, _ = await afn.run(_signal_with_deliveries, sid)
    if not r:
        await render(q, "الإشارة غير موجودة.", kb.admin_nav(), bot)
        return
    from fysb.handlers.user_pages import build_signal_chart_bundle
    sig = dict(r)
    bundle = await asyncio.to_thread(build_signal_chart_bundle, sig, False)
    png_bytes, is_proof = bundle["png"], bundle["is_proof"]
    if not png_bytes:
        await render(q, "⚠️ تعذر توليد الشارت حالياً.", kb.admin_nav([[kb.btn("↩️ الإشارة", f"a:signalview:{sid}")]]), bot)
        return
    import io as _io
    cap = (
        f"{'🏆 إثبات صفقة' if is_proof else '📊 شارت الإشارة'} #{sid} · "
        f"<b>{esc(sig['symbol'])}</b> {sig['direction']} ({esc(sig['timeframe'])})\n"
        f"{bundle['pips_line']}"
    )
    try:
        await bot.send_photo(chat_id=uid, photo=_io.BytesIO(png_bytes), caption=cap, parse_mode="HTML")
    except Exception:
        await render(q, "⚠️ تعذر إرسال صورة الشارت.", kb.admin_nav([[kb.btn("↩️ الإشارة", f"a:signalview:{sid}")]]), bot)


async def admin_signal_proof_publish(q, uid, context, bot, sid: int):
    r, _ = await afn.run(_signal_with_deliveries, sid)
    if not r:
        await render(q, "الإشارة غير موجودة.", kb.admin_nav(), bot)
        return
    from fysb.handlers.user_pages import build_signal_chart_bundle
    sig = dict(r)
    bundle = await asyncio.to_thread(build_signal_chart_bundle, sig, True)
    png_bytes = bundle["png"]
    if not png_bytes:
        await render(q, "⚠️ تعذر توليد شارت الإثبات حالياً.", kb.admin_nav([[kb.btn("↩️ الإشارة", f"a:signalview:{sid}")]]), bot)
        return
    cap = (
        f"🏆 <b><u>إثبات صفقة موثّق — ForexYemeni Royal</u></b>\n"
        f"▎📌 <b>{esc(sig['symbol'])}</b> {sig['direction']} · ⏱ {esc(sig['timeframe'])} · {esc(sig['strategy'])}\n"
        f"{bundle['pips_line']}\n"
        f"▎🛡 شارت قبل/بعد موثّق تلقائياً من محرك ForexYemeni."
    )
    import io as _io
    sent, failed = 0, 0
    try:
        await bot.send_photo(chat_id=uid, photo=_io.BytesIO(png_bytes), caption=cap, parse_mode="HTML")
    except Exception:
        pass
    chat_ids = await afn.run(_active_channel_chat_ids)
    for cid in chat_ids:
        try:
            target = int(cid) if cid.lstrip("-").isdigit() else cid
            await bot.send_photo(chat_id=target, photo=_io.BytesIO(png_bytes), caption=cap, parse_mode="HTML")
            sent += 1
        except Exception:
            failed += 1
    await afn.run(audit_log.log, uid, f"signal_proof_publish:{sid}:sent={sent}")
    await render(
        q,
        f"🏆 <b>تم توليد شارت الإثبات ونشره!</b>\n<blockquote>▎📤 تم الإرسال إليك وإلى <b>{sent}</b> قناة نشطة (فشل: {failed}).</blockquote>",
        kb.admin_nav([[kb.btn("↩️ الإشارة", f"a:signalview:{sid}")]]),
        bot,
    )


def _enabled_backtest_markets() -> list[str]:
    """Return ALL enabled markets from market_catalog (no verified/LIMIT truncation)."""
    with db.conn() as c:
        rows = c.execute(
            "SELECT symbol FROM market_catalog WHERE enabled=1 ORDER BY symbol"
        ).fetchall()
    return [str(r["symbol"]).upper() for r in rows if r["symbol"]] or ["XAUUSD", "EURUSD", "BTCUSD"]


def _enabled_backtest_strategies() -> list[str]:
    with db.conn() as c:
        rows = c.execute(
            "SELECT code FROM strategy_catalog WHERE enabled=1 ORDER BY code"
        ).fetchall()
    return [str(r["code"]).upper() for r in rows if r["code"]]


def _effective_backtest_min_score() -> int:
    from fysb.engines.quality_gate import DEFAULT_MIN_SCORE
    v = db.get_setting("engine_min_score", "")
    if v and str(v).strip().isdigit():
        return int(v)
    return db.get_setting_int("min_signal_score", DEFAULT_MIN_SCORE)


async def admin_backtest(q, uid, context, bot, page: int = 0):
    mkts = await afn.run(_enabled_backtest_markets)
    per_page = 8
    total_pages = max(1, (len(mkts) + per_page - 1) // per_page)
    pg = max(0, min(int(page), total_pages - 1))
    chunk = mkts[pg * per_page:(pg + 1) * per_page]
    rows_kb = []
    for sym in chunk:
        rows_kb.append([
            kb.btn(f"🧪 {sym} M15", f"a:btrun:{sym}:M15"),
            kb.btn(f"H1", f"a:btrun:{sym}:H1"),
            kb.btn(f"H4", f"a:btrun:{sym}:H4"),
        ])
    if total_pages > 1:
        nav = []
        if pg > 0:
            nav.append(kb.btn("⬅️ السابق", f"a:backtest:{pg - 1}", style="primary"))
        nav.append(kb.btn(f"📄 {pg + 1}/{total_pages}", f"a:backtest:{pg}", style="primary"))
        if pg + 1 < total_pages:
            nav.append(kb.btn("التالي ➡️", f"a:backtest:{pg + 1}", style="primary"))
        rows_kb.append(nav)
    rows_kb.append([kb.btn("🧠 العودة للمحرك", "a:engine")])
    min_sc = await afn.run(_effective_backtest_min_score)
    en_strats = await afn.run(_enabled_backtest_strategies)
    body = (
        "🧪 <b><u>مختبر الاختبار التاريخي المؤسسي (Walk-Forward Backtest)</u></b>\n"
        "<blockquote>"
        f"▎🌐 إجمالي الأسواق المفعّلة المتاحة: <b>{len(mkts)} سوق</b> (صفحة {pg + 1}/{total_pages})\n"
        f"▎🧠 الاستراتيجيات المفعّلة: <b>{len(en_strats)}</b> · ⭐ حد الجودة المعتمد: <b>{min_sc}/100</b>\n"
        "▎🛡 يطبّق فلتر الإجماع، السبريد الحقيقي لكل سوق، قاعدة SL-أولاً، وتأمين الدخول بعد TP1.\n"
        "▎📈 يحسب نسبة النجاح، عامل الربح (Profit Factor)، صافي R، وأقصى تراجع (Max DD)."
        "</blockquote>\n"
        "اختر السوق والإطار الزمني لبدء الاختبار:"
    )
    await render(q, body, kb.admin_nav(rows_kb), bot)


async def admin_backtest_run(q, uid, context, bot, symbol: str, tf: str, scope: str = "enabled"):
    from fysb.engines.backtest import format_report, run_backtest
    sym = (symbol or "XAUUSD").strip().upper()
    tf_u = (tf or "M15").strip().upper()
    sc_mode = (scope or "enabled").strip().lower()
    min_sc = await afn.run(_effective_backtest_min_score)
    strats = None if sc_mode == "all" else (await afn.run(_enabled_backtest_strategies) or None)
    from fysb.db import IMPLEMENTED_STRATEGIES
    scope_lbl = f"جميع الـ {len(IMPLEMENTED_STRATEGIES)} استراتيجية" if sc_mode == "all" else f"الاستراتيجيات المفعّلة ({len(strats or [])})"
    await render(
        q,
        f"🧪 <b>جارٍ تشغيل الاختبار التاريخي المؤسسي لـ {esc(sym)} ({esc(tf_u)})…</b>\n"
        f"<i>النطاق: {scope_lbl} · حد الجودة: {min_sc}/100 · سبريد السوق الحقيقي — يرجى الانتظار بضع ثوانٍ.</i>",
        None,
        bot,
    )
    rep = await asyncio.to_thread(
        run_backtest,
        sym,
        tf_u,
        strategies=strats,
        min_score=min_sc,
        api_key=config.market_api_key(),
    )
    await afn.run(audit_log.log, uid, f"backtest:{sym}:{tf_u}:{sc_mode}:n={rep.filled_trades}:r={rep.net_r}")
    alt_scope = "all" if sc_mode != "all" else "enabled"
    alt_lbl = "🧠 اختبار كل الـ 13" if alt_scope == "all" else "✅ اختبار المفعّلة فقط"
    rows_kb = [
        [
            kb.btn(("◉ " if tf_u == "M15" else "") + "⏱ M15", f"a:btrun:{sym}:M15:{sc_mode}"),
            kb.btn(("◉ " if tf_u == "H1" else "") + "⏱ H1", f"a:btrun:{sym}:H1:{sc_mode}"),
            kb.btn(("◉ " if tf_u == "H4" else "") + "⏱ H4", f"a:btrun:{sym}:H4:{sc_mode}"),
        ],
        [
            kb.btn(alt_lbl, f"a:btrun:{sym}:{tf_u}:{alt_scope}", style="primary"),
            kb.btn(f"🔄 إعادة {sym}", f"a:btrun:{sym}:{tf_u}:{sc_mode}", style="success"),
        ],
        [kb.btn("🧪 كل الأسواق", "a:backtest:0"), kb.btn("🧠 المحرك", "a:engine")],
    ]
    await render(q, format_report(rep), kb.admin_nav(rows_kb), bot)


async def admin_engine_toggle(q, uid, context, bot):
    val = "0" if await afn.run(db.get_setting, "engine_enabled", "0") == "1" else "1"
    await afn.run(db.set_setting, "engine_enabled", val)
    await afn.run(audit_log.log, uid, f"engine:{val}")
    await admin_engine(q, uid, context, bot)


async def admin_engine_adjust(q, uid, context, bot, key: str, delta: float):
    bounds = {"interval": ("engine_interval_seconds", 120, 3600), "minscore": ("engine_min_score", 50, 95)}
    if key not in bounds:
        await render(q, "إعداد غير صالح.", kb.admin_nav(), bot)
        return
    setting_key, lo, hi = bounds[key]
    cur = await afn.run(db.get_setting_int, setting_key, 300 if key == "interval" else 72)
    new_val = int(max(lo, min(hi, cur + delta)))
    await afn.run(db.set_setting, setting_key, new_val)
    if key == "minscore":
        # v8.0.0 (UI-2): Keep `min_signal_score` and `engine_min_score` 100% synchronized
        await afn.run(db.set_setting, "min_signal_score", new_val)
    val_after = await afn.run(db.get_setting, setting_key)
    await afn.run(audit_log.log, uid, f"engine_setting:{setting_key}:{val_after}")
    await admin_engine(q, uid, context, bot)


def _signal_by_id(sid: int):
    with db.conn() as c:
        r = c.execute("SELECT * FROM signals WHERE id=?", (sid,)).fetchone()
    return dict(r) if r else None


async def admin_engine_now(q, uid, context, bot):
    from fysb.engines.signal_engine import emit_to_users, generate_signal, opposite_close_advisory
    from fysb.handlers.background import _safe_send
    from fysb.handlers.inputs import bot_sender_from
    from fysb.ui.cards import diagnose_no_signal
    await render(q, "🚀 جارٍ تشغيل دورة فورية للمحرك على الأسواق المفعلة...", None)
    markets = await afn.run(_enabled_verified_market_dicts)
    if not markets:
        await render(q, "⛔ لا توجد أسواق مفعلة وموثقة. فعّل سوقاً من قسم الأسواق أولاً (زر التحقق).", kb.admin_nav(), bot)
        return
    created_cnt = 0
    guarded_cnt = 0
    results = []
    for m in markets:
        res = await asyncio.to_thread(generate_signal, m["symbol"])
        st = (res.get("status") or "").upper()
        if st == "SIGNAL_CREATED" and res.get("signal_id"):
            created_cnt += 1
            sig = await afn.run(_signal_by_id, int(res["signal_id"]))
            extra = ""
            if sig:
                try:
                    for target, adv_txt in await afn.run(opposite_close_advisory, sig):
                        await _safe_send(bot, target, adv_txt)
                except Exception:
                    pass
                stats = await emit_to_users(sig, bot_sender_from(bot), bot=bot, include_channels=True)
                extra = f" · 📤 مستلمون: <b>{stats.get('sent', 0)}</b> · قنوات: <b>{stats.get('channels_sent', 0)}</b>"
            results.append(
                f"▎🟢 <b>{esc(m['symbol'])}</b> — <b>صدرت إشارة حقيقية ⭐ {res.get('score', 0)}/100</b>{extra}"
            )
        else:
            guarded_cnt += 1
            icon, title, _exp, _rec = diagnose_no_signal(st, res.get("reason", ""))
            reason_txt = f"\n   <code>{esc(res['reason'][:90])}</code>" if res.get("reason") else ""
            results.append(f"▎{icon} <b>{esc(m['symbol'])}</b> — {title}{reason_txt}")
    header = (
        "👑 <b><u>تقرير الدورة الفورية للمحرك المؤسسي</u></b>\n"
        "━━━━━━━━━━━━━━━━━━\n"
        f"▎🌐 الأسواق المفحوصة: <b>{len(markets)}</b> · 🟢 إشارات صادرة: <b>{created_cnt}</b> · 🛡 محجوبة لحماية رأس المال: <b>{guarded_cnt}</b>\n"
        "━━━━━━━━━━━━━━━━━━\n"
        "<blockquote>" + "\n".join(results) + "</blockquote>\n"
        "<i>👑 المحرك لا يصنّع إشارات أبداً — الإشارة تصدر فقط عند اكتمال كافة شروط الجودة والاتجاه.</i>"
    )
    await render(
        q,
        header,
        kb.admin_nav([
            [kb.btn("🚀 دورة فورية مجدداً", "a:enginenow", style="success"), kb.btn("⚡ إشارة الآن", "a:signow", style="success")],
            [kb.btn("🧠 المحرك", "a:engine", style="primary"), kb.btn("📡 الإشارات", "a:signals", style="primary")],
        ]),
        bot,
    )


async def admin_engine_clean(q, uid, context, bot):
    cleaned = await afn.run(_engine_runs_clean)
    await afn.run(audit_log.log, uid, f"engine_runs_cleaned:{cleaned}")
    await render(q, f"🧹 أُرشفت وأُزيلت {abs(cleaned)} سجل دورة قديم (بقي آخر 500).", kb.admin_nav(), bot)


# --- Strategies ------------------------------------------------------------------
# v6.9.1 — the full world catalog (36 methodologies) exceeds Telegram's 4096
# character message limit in one board (measured: 4678 chars ⇒ BadRequest
# "Message is too long" ⇒ the generic error card the owner reported).
# The royal fix: paginate — 10 strategies per page, global rank numbers
# preserved across pages, prev/next navigation, and every action button
# carries its page so toggling/verifying lands back on the same page.
STRATS_PER_PAGE = 10


async def admin_strategies(q, uid, context, bot, page: int = 0):
    """v6.10.0 (owner report 9) — the plain-text wall is retired: every
    methodology is now its own BUTTON «🟢 ICT · 82.5» opening a REAL identity
    page (rules of the engine + measured stats + verify/toggle actions).
    The board always fits Telegram's limits — no more «حدث خطأ» on tap."""
    from fysb.services import strategy_stats
    rows = await afn.run(_strategy_catalog_rows)
    codes = await afn.run(strategy_stats.ordered_codes, [r["code"] for r in rows])
    by_code = {r["code"]: r for r in rows}
    ordered = [by_code[c] for c in codes if c in by_code] + [r for r in rows if r["code"] not in codes]
    board = {d["code"]: d for d in await afn.run(strategy_stats.leaderboard)}
    total = len(ordered)
    verified_n = sum(1 for r in ordered if r["verified"])
    active_n = sum(1 for r in ordered if r["enabled"] and r["verified"])
    per_page = 12
    pages = max(1, (total + per_page - 1) // per_page)
    page = max(0, min(int(page or 0), pages - 1))
    chunk = ordered[page * per_page:(page + 1) * per_page]
    body = (f"🧩 <b><u>حوكمة الاستراتيجيات — مرتبة من الأقوى</u></b>\n"
            f"<blockquote>▎📄 الصفحة <b>{page + 1}</b> من <b>{pages}</b> — الترتيب عالمي متصل\n"
            f"▎🗂 إجمالي المنهجيات: <b>{total}</b> · موثقة ومختبرة: <b>{verified_n}</b> · جاهزة للإشارات: <b>{active_n}</b>\n"
            "▎اضغط أي استراتيجية لفتح بطاقتها الملكية: القواعد الميكانيكية الحقيقية + الأداء المقاس + التفعيل/التحقق</blockquote>")
    rows_kb = []
    grid = []
    for r in chunk:
        rank = page * per_page + len(grid) + 1
        state = "🟢" if (r["enabled"] and r["verified"]) else ("🔵" if r["verified"] else "⚪")
        d = board.get(r["code"])
        strength = f" · {d['strength']:.0f}" if d else ""
        grid.append(kb.btn(f"{state} {rank}. {r['code']}{strength}",
                           f"a:strategyinfo:{r['code']}:{page}"))
        if len(grid) == 2:
            rows_kb.append(grid)
            grid = []
    if grid:
        rows_kb.append(grid)
    pager = []
    if page > 0:
        pager.append(kb.btn("◀️ الأقوى السابقة", f"a:strategies:{page - 1}"))
    pager.append(kb.btn(f"📄 {page + 1}/{pages}", "a:plannoop"))
    if page < pages - 1:
        pager.append(kb.btn("الأدنى التالية ▶️", f"a:strategies:{page + 1}"))
    rows_kb.append(pager)
    await render(q, body, kb.admin_nav(rows_kb), bot)


async def admin_strategy_info(q, uid, context, bot, code: str, page: int = 0):
    """The royal identity card of ONE strategy — real rules, real stats, and
    the actions (verify / toggle) the old error message promised but the board
    never actually offered."""
    from fysb.services import strategy_info
    from fysb.services import strategy_stats
    code = (code or "").upper()
    r = await afn.run(_strategy_row, code)
    if not r:
        await render(q, "الاستراتيجية غير موجودة.", kb.admin_nav(), bot)
        return
    rec = dict(r)
    body = await afn.run(strategy_info.info_page, code)
    board = {d["code"]: d for d in await afn.run(strategy_stats.leaderboard)}
    d = board.get(code)
    if d:
        body += (f"\n\n📈 <b>قياس مباشر:</b> {d['n']} مغلقة · رابحة {d['win_rate']:.0f}% · "
                 f"TP3 {d['tp3_share']:.0f}% · متوسط R {d['avg_r']:+.2f} · قوة {d['strength']:.1f}")
    rows_kb = []
    from fysb.engines.strategies import VERIFIABLE
    if code in VERIFIABLE and not rec["verified"]:
        rows_kb.append([kb.btn("✅ تحقق وتفعيل (محرك منفذ ومختبَر آلياً)", f"a:strategy_verify:{code}:{page}")])
    elif rec["verified"]:
        action = "إيقاف" if rec["enabled"] else "تفعيل"
        rows_kb.append([kb.btn(f"{'🟢' if rec['enabled'] else '⚪'} {action} الاستراتيجية", f"a:strategy_toggle:{code}:{page}")])
    else:
        rows_kb.append([kb.btn("🔒 مغلقة — تُفتح بعد تنفيذ محركها واختباره آلياً", "a:plannoop")])
    rows_kb.append([kb.btn("↩️ لوحة الاستراتيجيات", f"a:strategies:{page}")])
    await render(q, body, kb.admin_nav(rows_kb), bot)


async def admin_strategy_toggle(q, uid, context, bot, code: str, page: int = 0):
    r = await afn.run(_strategy_toggle_state, code)
    if not r:
        await render(q, "الاستراتيجية غير موجودة.", kb.admin_nav(), bot)
        return
    if not r["verified"]:
        await render(q, "⛔ الاستراتيجية غير مختبرة؛ لا يمكن تفعيلها. استخدم زر التحقق إن كانت ضمن المنفذة.",
                     kb.admin_nav(), bot)
        return
    await afn.run(_strategy_toggle_apply, code, 0 if r["enabled"] else 1)
    await afn.run(audit_log.log, uid, f"strategy_toggle:{code}")
    await admin_strategies(q, uid, context, bot, page)


async def admin_strategy_verify(q, uid, context, bot, code: str, page: int = 0):
    if code not in VERIFIABLE:
        await render(q, "⛔ هذه الاستراتيجية غير منفذة بعد؛ لا يمكن التحقق منها.", kb.admin_nav(), bot)
        return
    await afn.run(_strategy_verify_apply, code)
    await afn.run(audit_log.log, uid, f"strategy_verified:{code}")
    await admin_strategies(q, uid, context, bot, page)


# --- Operating modes -------------------------------------------------------------
async def admin_modes(q, uid, context, bot):
    rows, current = await afn.run(_modes_page_rows)
    lines = [f"⚙️ <b>أوضاع التشغيل</b>\nالوضع الحالي: <b>{esc(current)}</b>", ""]
    rows_kb = []
    for r in rows:
        state = "🟢" if r["enabled"] else "⚪"
        exec_state = "قابل للتنفيذ" if r["executable"] else "غير قابل للتنفيذ (مغلق)"
        lines.append(f"{state} <b>{esc(r['code'])} — {esc(r['title'])}</b> ({exec_state})\n  {esc(r['description'])}")
        if r["executable"]:
            rows_kb.append([kb.btn(f"تعيين {esc(r['code'])} كوضع حالي", f"a:mode_set:{r['code']}")])
        else:
            rows_kb.append([kb.btn(f"🔒 {esc(r['code'])} مغلق حتى توثيق محركه", "a:plannoop")])
    await render(q, "\n".join(lines), kb.admin_nav(rows_kb), bot)


async def admin_mode_set(q, uid, context, bot, code: str):
    r = await afn.run(_mode_row, code)
    if not r or not r["executable"] or not r["enabled"]:
        await render(q, "⛔ هذا الوضع غير قابل للتنفيذ في هذه النسخة.", kb.admin_nav(), bot)
        return
    if code == "C":
        # LIVE requires the explicit double-confirmation flow; never settable here.
        await render(q, "⛔ الوضع LIVE يُفعّل حصراً عبر قسم «التداول الحقيقي» بتأكيد مزدوج.", kb.admin_nav(), bot)
        return
    await afn.run(db.set_setting, "trading_mode", r["code"])
    await afn.run(audit_log.log, uid, f"mode_set:{code}")
    await admin_modes(q, uid, context, bot)


# --- Risk management -------------------------------------------------------------
def risk_board_text() -> str:
    """v8.13.7 — the risk engine board, rebuilt daily (sync — runs via afn.run
    from both the admin callback and the EOD renewal in the tracker loop)."""
    ks = db.kill_switch_on()
    vf = db.get_setting("volatility_filter", "1")
    eod_t = db.get_setting("eod_close_utc", "21:30")
    eod_on = db.get_setting("eod_close_enabled", "1") == "1"
    last = db.get_setting("eod_last_close_date", "—")
    keys = [
        ("risk_per_signal_pct", "المخاطرة لكل إشارة"),
        ("max_daily_risk_pct", "أقصى مخاطرة يومية"),
        ("max_daily_loss_pct", "أقصى خسارة يومية"),
        ("max_open_per_symbol", "أقصى إشارات مفتوحة لكل سوق"),
        ("max_open_signals", "أقصى إشارات مفتوحة (الكلي)"),
        ("max_correlated_signals", "أقصى صفقات مترابطة بنفس الاتجاه"),
        ("min_rr", "أدنى عائد/مخاطرة"),
        ("atr_pct_max", "أقصى تقلب ATR%"),
    ]
    lines = ["🛡 <b>محرك إدارة المخاطر</b>",
             f"⛔ Kill Switch: <b>{'مفعل — يمنع أي إشارة جديدة' if ks else 'غير مفعل'}</b>",
             f"🌊 فلتر التقلب: <b>{'مفعل' if vf == '1' else 'متوقف'}</b>",
             f"🌙 إغلاق يوم التداول اليومي: <b>{'مفعل ' + eod_t + ' UTC' if eod_on else 'متوقف'}</b>"
             f" · آخر إغلاق: <b>{last}</b>",
             "<blockquote>▎💡 <b>مفتاح القتل للطوارئ فقط</b>: يوقف إصدار كل الإشارات الجديدة فوراً "
             "(الصادرة تُتابع) — لخطر بنيوي كارثي، لا للاستخدام اليومي؛ نهاية اليوم الصحيحة هي "
             "زر «إغلاق يوم التداول».\n"
             "▎✨ <b>سقف السوق</b> يمنع تكدّس الإشارات على سوق واحد دون خنق باقي الأسواق.\n"
             "▎👑 <b>⚡ إشارة الآن للإدارة</b> مستثناة من سقف الإشارات الآلية ولا تستهلك حصتها.\n"
             "▎🌙 <b>اللوحة تتجدد يومياً</b> مع إغلاق السوق: العدّادات تصفر ويبدأ يوم جديد بنظام جديد.</blockquote>",
             "<b>الحدود (تطبق على السيرفر قبل أي إشارة):</b>"]
    for key, title in keys:
        val = db.get_setting_float(key, 0)
        lines.append(f"  {title}: <b>{val:g}</b>")
    return "\n".join(lines)


async def admin_risk(q, uid, context, bot):
    keys = [
        ("risk_per_signal_pct", "المخاطرة لكل إشارة", 0.1, 0.1),
        ("max_daily_risk_pct", "أقصى مخاطرة يومية", 0.5, 0.5),
        ("max_daily_loss_pct", "أقصى خسارة يومية", 0.5, 1.0),
        ("max_open_per_symbol", "أقصى إشارات مفتوحة لكل سوق", 1, 3),
        ("max_open_signals", "أقصى إشارات مفتوحة (الكلي)", 1, 12),
        ("max_correlated_signals", "أقصى صفقات مترابطة بنفس الاتجاه", 1, 3),
        ("min_rr", "أدنى عائد/مخاطرة", 0.1, 0.5),
        ("atr_pct_max", "أقصى تقلب ATR%", 0.5, 1.0),
    ]
    rows_kb = []
    for key, title, step, _d in keys:
        val = await afn.run(db.get_setting_float, key, 0)
        rows_kb.append([kb.btn("➖", f"a:riskadj:{key}:-{step}"), kb.btn(f"{title} {val:g}", "a:plannoop"), kb.btn("➕", f"a:riskadj:{key}:{step}")])
    rows_kb.append([kb.btn("🌙 إغلاق يوم التداول الآن", "a:eodclosenow", style="success")])
    rows_kb.append([kb.btn("🧹 صفحة جديدة — مسح تاريخ الإشارات", "a:freshpage", style="danger")])
    rows_kb.append([kb.btn("⛔ تبديل Kill Switch", "a:killswitch", style="danger"),
                    kb.btn("🌊 فلتر التقلب", "a:volfilter", style="primary")])
    text = await afn.run(risk_board_text)
    await render(q, text, kb.admin_nav(rows_kb), bot)


async def admin_fresh_page(q, uid, context, bot):
    """v8.13.8 — royal fresh page CONFIRMATION (double-confirm, no undo)."""
    counts = await afn.run(tracker.fresh_page_counts)
    text = (
        "🧹 <b><u>صفحة جديدة — مسح تاريخ الإشارات</u></b>\n"
        "<blockquote>"
        f"▎📊 الإشارات المحذوفة: <b>{counts['signals_total']}</b> "
        f"(المفتوحة الآن: <b>{counts['signals_open']}</b>)\n"
        f"▎📎 التسليمات: <b>{counts['signal_deliveries']}</b> · سجل الجورنال: <b>{counts['trade_journal']}</b>\n"
        f"▎🏛 أوامر التنفيذ: <b>{counts['exchange_orders']}</b> · أوامر الجسر: <b>{counts['bridge_orders']}</b>\n"
        "▎⚠️ <b>لا أرشفة ولا تراجع</b> — حذف كلي نهائي بقرار المالك\n"
        "▎🔔 قبل المسح: يُرسل التنبيه الإجباري «يوم جديد إشارات جديدة ونظام جديد» لكل الحاملين\n"
        "▎🏆 سباق المدارس يبدأ من صفر حقيقي · مراكز الدمو وسجلات التشخيص لا تُمسّ\n"
        "▎🔍 البحث عن الفرص يستمر فوراً — أول إشارة جديدة تحمل الرقم #1"
        "</blockquote>"
    )
    await render(q, text, kb.admin_nav([
        [kb.btn("✅ تأكيد المسح النهائي", "a:freshpagego", style="danger")],
        [kb.btn("↩️ رجوع بلا مسح", "a:risk", style="primary")],
    ]), bot)


async def admin_fresh_page_go(q, uid, context, bot):
    """v8.13.8 — execute: mandatory notice first, then the FK-safe total wipe,
    then the audit trail + the renewed risk board."""
    from fysb.engines import tracker as _trk
    holders = await afn.run(_trk.fresh_page_holders)
    notified = 0
    for t_uid in holders:
        try:
            await bot.send_message(t_uid, _trk.EOD_MANDATORY_TEXT, parse_mode="HTML")
            notified += 1
        except Exception:
            pass
    counts = await afn.run(_trk.fresh_page_wipe)
    total = sum(counts.values())
    await afn.run(audit_log.log, uid, f"fresh_page:wiped:{total}")
    sig_n = counts.get("signals", 0)
    await render(q, f"✅ <b>صفحة جديدة — تم المسح الكلي</b>\n"
                    f"<blockquote>▎🗑 الإشارات المحذوفة: <b>{sig_n}</b> · المجموع الكلي: <b>{total}</b> صف\n"
                    f"▎🔔 التنبيه الإجباري وصل إلى <b>{notified}</b> مستلم\n"
                    f"▎🏆 سباق المدارس من صفر · الترقيم الجديد يبدأ من #1\n"
                    f"▎🔍 المحرك يبحث عن أول فرصة جديدة الآن</blockquote>",
                 kb.admin_nav([[kb.btn("🛡 محرك إدارة المخاطر", "a:risk", style="primary")]]), bot)


async def admin_risk_adjust(q, uid, context, bot, key: str, delta: float):
    bounds = {"risk_per_signal_pct": (0.1, 5.0), "max_daily_risk_pct": (1.0, 20.0),
              "max_daily_loss_pct": (1.0, 30.0), "max_open_per_symbol": (1, 10),
              "max_open_signals": (1, 50), "max_correlated_signals": (1, 10),
              "min_rr": (1.0, 10.0), "atr_pct_max": (1.0, 15.0)}
    if key not in bounds:
        await render(q, "إعداد غير صالح.", kb.admin_nav(), bot)
        return
    lo, hi = bounds[key]
    cur = await afn.run(db.get_setting_float, key, lo)
    await afn.run(db.set_setting, key, round(max(lo, min(hi, cur + delta)), 2))
    val_after = await afn.run(db.get_setting, key)
    await afn.run(audit_log.log, uid, f"risk_setting:{key}:{val_after}")
    await admin_risk(q, uid, context, bot)


async def admin_kill_switch(q, uid, context, bot):
    val = "0" if await afn.run(db.kill_switch_on) else "1"
    await afn.run(db.set_setting, "global_kill_switch", val)
    await afn.run(audit_log.log, uid, f"kill_switch:{val}")
    await admin_risk(q, uid, context, bot)


async def admin_vol_filter(q, uid, context, bot):
    val = "0" if await afn.run(db.get_setting, "volatility_filter", "1") == "1" else "1"
    await afn.run(db.set_setting, "volatility_filter", val)
    await afn.run(audit_log.log, uid, f"volatility_filter:{val}")
    await admin_risk(q, uid, context, bot)


async def admin_eod_close_now(q, uid, context, bot):
    """v8.13.7 — manual royal day-close button: ALL open signals end with the
    day immediately, every holder gets the mandatory notice, the day boundary
    is stamped (renewed board next tracker tick), and the engine keeps hunting."""
    from fysb.engines import tracker as _trk
    count, edits, notes = await afn.run(_trk.eod_close_all, "manual")
    sent = 0
    for t_uid, text in notes:
        try:
            await bot.send_message(t_uid, text, parse_mode="HTML")
            sent += 1
        except Exception:
            pass
    for _uid, chat_id, message_id, text in edits:
        try:
            await bot.edit_message_text(text, chat_id=chat_id, message_id=message_id, parse_mode="HTML")
        except Exception:
            pass
    await afn.run(audit_log.log, uid, f"eod_close_now:{count}")
    await render(q, f"🌙 <b>أُغلق يوم التداول يدوياً</b> — أُنهيت متابعة <b>{count}</b> إشارة مفتوحة "
                    f"وأُرسل التنبيه الإجباري إلى <b>{sent}</b> مستلم. البحث عن فرص جديدة يستأنف فوراً.",
                 kb.admin_nav([[kb.btn("🛡 محرك إدارة المخاطر", "a:risk", style="primary")]]), bot)


# --- Markets ---------------------------------------------------------------------
CATEGORY_ORDER = ["metal", "forex", "crypto", "stock", "index", "commodity"]
_ADMIN_MKT_PAGE_SIZE = 12


async def admin_markets(q, uid, context, bot, notice: str = ""):
    """v8.3.0 — ULTRA-LUXURY ROYAL MARKETS HUB: clean category buttons
    (المعادن · الفوركس · العملات الرقمية · الأسهم · المؤشرات · السلع) with
    real enabled/total counts across 101 global instruments — zero clutter."""
    from fysb.theme import CATEGORY_AR, CATEGORY_ICON
    rows = await afn.run(_market_catalog_all)
    if not rows:
        await render(q, "🌐 لا توجد أسواق في الفهرس بعد.", kb.admin_nav([[kb.btn("➕ إضافة سوق", "a:market_add")]]), bot)
        return
    total = len(rows)
    enabled = sum(1 for r in rows if r["enabled"])
    closed = total - enabled
    sections: dict[str, list] = {}
    for r in rows:
        sections.setdefault((r.get("category") or "forex"), []).append(r)
    ordered = [c for c in CATEGORY_ORDER if c in sections] + \
              [c for c in sections if c not in CATEGORY_ORDER]

    lines = [
        "🌐 <b><u>مركز إدارة الأسواق العالمي الملكي — FOREXYEMENI</u></b>",
        "<blockquote>"
        f"▎🗂 إجمالي الأدوات الحقيقية: <b>{total}</b> أداة عالمية\n"
        f"▎🟢 مفعّلة حالياً: <b>{enabled}</b> · 🔒 مغلقة افتراضياً: <b>{closed}</b>\n"
        "▎⚡ اختر أي قسم بالأسفل ثم اضغط على أي سوق لتفعيله 🟢 أو إغلاقه 🔒 بضغطة واحدة."
        "</blockquote>",
    ]
    if notice:
        lines.append(f"<i>✨ {esc(notice)}</i>")
    lines.append("\n<b>📋 ملخص الأقسام العالمية:</b>")
    for cat in ordered:
        icon = CATEGORY_ICON.get(cat, "🌐")
        title = CATEGORY_AR.get(cat, cat)
        grp = sections[cat]
        sec_en = sum(1 for r in grp if r["enabled"])
        st_dot = "🟢" if sec_en > 0 else "🔒"
        lines.append(f"▎{st_dot} {icon} <b>{title}</b>: <b>{sec_en}</b> مفعّل من أصل <code>{len(grp)}</code>")

    # 2-per-row luxury category buttons (المعادن | الفوركس / العملات الرقمية | الأسهم / المؤشرات | السلع)
    rows_kb = []
    pair_buf = []
    for cat in ordered:
        icon = CATEGORY_ICON.get(cat, "🌐")
        title = CATEGORY_AR.get(cat, cat)
        grp = sections[cat]
        sec_en = sum(1 for r in grp if r["enabled"])
        btn_style = "success" if sec_en > 0 else "primary"
        pair_buf.append(kb.btn(f"{icon} {title} ({sec_en}/{len(grp)})", f"a:mktcat:{cat}:1", style=btn_style))
        if len(pair_buf) == 2:
            rows_kb.append(pair_buf)
            pair_buf = []
    if pair_buf:
        rows_kb.append(pair_buf)

    rows_kb.append([kb.btn(f"🟢 عرض الأسواق المفعّلة الآن فقط ({enabled})", "a:mktcat:enabled:1", style="success")])
    rows_kb.append([
        kb.btn("🎯 أسواق إشارات الإدارة", "a:adminmkts", style="primary"),
        kb.btn("➕ إضافة سوق مخصص", "a:market_add"),
    ])
    await render(q, "\n".join(lines), kb.admin_nav(rows_kb), bot)


async def admin_markets_category(q, uid, context, bot, category: str, page: int = 1, notice: str = ""):
    """v8.3.0 — Paginated luxury board for a single market category with 1-tap toggle."""
    from fysb.theme import CATEGORY_AR, CATEGORY_ICON
    cat = (category or "metal").strip().lower()
    rows = await afn.run(_market_catalog_all)
    if cat == "enabled":
        grp = [r for r in rows if r["enabled"]]
        icon, title = "🟢", "الأسواق المفعّلة حالياً"
    else:
        grp = [r for r in rows if (r.get("category") or "forex").lower() == cat]
        icon = CATEGORY_ICON.get(cat, "🌐")
        title = CATEGORY_AR.get(cat, cat)

    # Stable deterministic order (XAUUSD first, then alphabetical) so tapping any
    # symbol on Page 1/2/3 NEVER jumps it off the page — it flips 🔒 ↔ 🟢 in place!
    grp.sort(key=lambda r: (0 if r["symbol"] == "XAUUSD" else 1, r["symbol"]))
    total_cat = len(grp)
    en_cat = sum(1 for r in grp if r["enabled"])
    total_pages = max(1, (total_cat + _ADMIN_MKT_PAGE_SIZE - 1) // _ADMIN_MKT_PAGE_SIZE)
    page = max(1, min(int(page or 1), total_pages))
    chunk = grp[(page - 1) * _ADMIN_MKT_PAGE_SIZE: page * _ADMIN_MKT_PAGE_SIZE]

    lines = [
        f"{icon} <b><u>قسم {title} — إدارة الأسواق</u></b>",
        "<blockquote>"
        f"▎📊 المفعّلة: <b>{en_cat}</b> من أصل <b>{total_cat}</b> · صفحة <code>{page}/{total_pages}</code>\n"
        "▎⚡ اضغط أي سوق بالأسفل لتفعيله 🟢 أو إغلاقه 🔒 فوراً بضغطة واحدة."
        "</blockquote>",
    ]
    if notice:
        lines.append(f"<i>✨ {esc(notice)}</i>")
    if not chunk:
        lines.append("\n⚪ لا توجد أدوات مفعّلة في هذا العرض حالياً.")
    else:
        for r in chunk:
            st = "🟢 مفعّل" if r["enabled"] else "🔒 مغلق"
            lines.append(f"▎{st} · <b>{esc(r['symbol'])}</b> — {esc(r['title'])}")

    rows_kb = []
    grid = []
    ctx_tag = f"{cat}_{page}"
    for r in chunk:
        is_on = bool(r["enabled"])
        lbl = f"{'🟢' if is_on else '🔒'} {r['symbol']}"
        grid.append(kb.btn(lbl, f"a:market_toggle:{r['symbol']}:{ctx_tag}", style="success" if is_on else "primary"))
        if len(grid) == 2:
            rows_kb.append(grid)
            grid = []
    if grid:
        rows_kb.append(grid)

    if total_pages > 1:
        nav_row = []
        if page > 1:
            nav_row.append(kb.btn("◀️ السابق", f"a:mktcat:{cat}:{page - 1}"))
        nav_row.append(kb.btn(f"📄 {page}/{total_pages}", f"a:mktcat:{cat}:{page}"))
        if page < total_pages:
            nav_row.append(kb.btn("التالي ▶️", f"a:mktcat:{cat}:{page + 1}"))
        rows_kb.append(nav_row)

    if cat != "enabled" and total_cat > 0:
        rows_kb.append([
            kb.btn(f"✅ تفعيل كل {title}", f"a:mktbulk:{cat}:on", style="success"),
            kb.btn(f"🔒 إغلاق كل {title}", f"a:mktbulk:{cat}:off", style="danger"),
        ])
    rows_kb.append([kb.btn("↩️ كل أقسام الأسواق", "a:markets", style="primary")])
    await render(q, "\n".join(lines), kb.admin_nav(rows_kb), bot)


def _sync_enabled_symbols_to_vip(symbols: list[str]) -> None:
    """Ensure VIP plan stays synced with Admin-activated markets immediately."""
    syms = [str(s or "").strip().upper() for s in (symbols or []) if str(s or "").strip()]
    if not syms:
        return
    with db.tx() as c:
        row = c.execute("SELECT markets FROM plans WHERE code='VIP'").fetchone()
        if not row:
            return
        cur = str(row["markets"] or "").strip()
        if cur.upper() == "ALL":
            return
        cur_list = [x.strip().upper() for x in cur.split(",") if x.strip()]
        changed = False
        for s in syms:
            if s not in cur_list:
                cur_list.append(s)
                changed = True
        if changed:
            c.execute("UPDATE plans SET markets=? WHERE code='VIP'", (",".join(cur_list),))
    try:
        from fysb.services import subscriptions as _subs
        _subs.invalidate_plans_cache()
    except Exception:
        pass


async def admin_markets_bulk(q, uid, context, bot, category: str, mode: str):
    """Enable or disable all instruments in a category at once."""
    from fysb.theme import CATEGORY_AR
    cat = (category or "").strip().lower()
    enable = (mode or "").strip().lower() == "on"
    def _apply_bulk():
        with db.tx() as c:
            if enable:
                syms = [r["symbol"] for r in c.execute(
                    "SELECT symbol FROM market_catalog WHERE LOWER(category)=?", (cat,)).fetchall()]
                cur = c.execute(
                    "UPDATE market_catalog SET enabled=1, verified=1, updated_at=? WHERE LOWER(category)=?",
                    (config.now(), cat),
                )
            else:
                syms = []
                cur = c.execute(
                    "UPDATE market_catalog SET enabled=0, updated_at=? WHERE LOWER(category)=? AND symbol<>?",
                    (config.now(), cat, db.GOLD_DEFAULT_SYMBOL),
                )
            return int(cur.rowcount or 0), syms
    cnt, syms = await afn.run(_apply_bulk)
    if enable and syms:
        await afn.run(_sync_enabled_symbols_to_vip, syms)
    await afn.run(audit_log.log, uid, f"markets_bulk:{cat}:{'on' if enable else 'off'}:{cnt}")
    title = CATEGORY_AR.get(cat, cat)
    msg = (f"تم تفعيل جميع أسواق قسم {title} ({cnt} أداة) بنجاح 🟢"
           if enable else f"تم إغلاق أسواق قسم {title} ({cnt} أداة) بنجاح 🔒")
    await admin_markets_category(q, uid, context, bot, cat, page=1, notice=msg)


# --- Admin personal signal-receive market filter (v6.17.0) ----------------------
def _parse_csv_syms(raw: str) -> list[str]:
    out = []
    for x in (raw or "").split(","):
        s = x.strip().upper().replace("/", "")
        if s and s != "ALL" and s not in out:
            out.append(s)
    return out


async def admin_signal_markets(q, uid, context, bot, notice: str = ""):
    """v6.17.0: multi-toggle market filter for ADMIN_ID's own personal signal &
    tracker alerts — NEVER disables any market for users or channels."""
    live = [m["symbol"] for m in await afn.run(_enabled_verified_market_dicts)]
    raw = (await afn.run(db.get_setting, "admin_signal_markets", "") or "").strip()
    selected = _parse_csv_syms(raw)
    all_mode = (not selected)
    active_set = set(live) if all_mode else (set(selected) & set(live))
    if not all_mode and not active_set and live:
        # fallback cleanly if previously-selected markets were disabled
        all_mode = True
        active_set = set(live)

    summary = "🌍 جميع الأسواق المفعّلة" if all_mode else " · ".join(sorted(active_set))
    lines = [
        "🎯 <b><u>أسواق إشارات حساب الإدارة</u></b>",
        "<blockquote>"
        f"▎📌 ما يصلك حالياً على حسابك: <b>{esc(summary)}</b>\n"
        f"▎🌐 إجمالي الأسواق المفعّلة على المنصة: <b>{len(live)}</b>\n"
        "▎✨ هذا الإعداد يتحكم بما يصلك <b>أنت شخصياً</b> من الإشارات وتحديثات التتبع فقط — "
        "ولا يغلق أي سوق على المستخدمين أو القنوات."
        "</blockquote>",
    ]
    if notice:
        lines.append(f"<i>{esc(notice)}</i>")
    if not live:
        lines.append("⚠️ لا توجد أسواق مفعّلة وموثّقة حالياً — فعّل الأسواق من قسم 🌐 الأسواق أولاً.")
    else:
        lines.append("<b>اضغط أي سوق لتشغيله ✅ أو إيقافه ⚪ على حساب الإدارة:</b>")

    rows_kb = []
    rows_kb.append([kb.btn(
        ("✅ " if all_mode else "🌍 ") + "استلام جميع الأسواق المفعّلة",
        "a:adminmktsall",
        style="success" if all_mode else "primary",
    )])
    if "XAUUSD" in live:
        gold_only = (not all_mode and active_set == {"XAUUSD"})
        rows_kb.append([kb.btn(
            ("⭐ " if gold_only else "🥇 ") + "تحديد الذهب فقط (XAUUSD)",
            "a:adminmktsonly:XAUUSD",
            style="success" if gold_only else "primary",
        )])
    for i in range(0, len(live), 3):
        rows_kb.append([
            kb.btn(
                ("✅ " if s in active_set else "⚪ ") + s,
                f"a:adminmktstog:{s}",
                style="success" if s in active_set else "",
            )
            for s in live[i:i + 3]
        ])
    rows_kb.append([kb.btn("📡 مركز الإشارات", "a:signals"), kb.btn("🌐 إدارة الأسواق", "a:markets")])
    await render(q, "\n".join(lines), kb.admin_nav(rows_kb), bot)


async def admin_signal_markets_toggle(q, uid, context, bot, symbol: str):
    symbol = (symbol or "").strip().upper().replace("/", "")
    live = [m["symbol"] for m in await afn.run(_enabled_verified_market_dicts)]
    if symbol not in live:
        await admin_signal_markets(q, uid, context, bot, notice=f"السوق {symbol} غير مفعّل حالياً.")
        return
    raw = (await afn.run(db.get_setting, "admin_signal_markets", "") or "").strip()
    selected = _parse_csv_syms(raw)
    cur = list(live) if not selected else [s for s in selected if s in live]
    if not cur:
        cur = list(live)
    notice = ""
    if symbol in cur:
        if len(cur) <= 1:
            notice = "يجب إبقاء سوق واحد على الأقل نشطاً (أو أوقف دفع الإشارات من الإشعارات)."
        else:
            cur = [s for s in cur if s != symbol]
            notice = f"تم إيقاف استلام إشارات {symbol} على حساب الإدارة (تبقى مفعّلة للمستخدمين)."
    else:
        cur.append(symbol)
        notice = f"تم تفعيل استلام إشارات {symbol} على حساب الإدارة."
    new_val = "" if set(cur) >= set(live) else ",".join(sorted(set(cur)))
    await afn.run(db.set_setting, "admin_signal_markets", new_val)
    await afn.run(audit_log.log, uid, f"admin_signal_markets:{new_val or 'ALL'}")
    await admin_signal_markets(q, uid, context, bot, notice=notice)


async def admin_signal_markets_only(q, uid, context, bot, symbol: str):
    symbol = (symbol or "").strip().upper().replace("/", "")
    live = [m["symbol"] for m in await afn.run(_enabled_verified_market_dicts)]
    if symbol not in live:
        await admin_signal_markets(q, uid, context, bot, notice=f"السوق {symbol} غير مفعّل حالياً.")
        return
    new_val = "" if live == [symbol] else symbol
    await afn.run(db.set_setting, "admin_signal_markets", new_val)
    await afn.run(audit_log.log, uid, f"admin_signal_markets:only:{symbol}")
    await admin_signal_markets(
        q, uid, context, bot,
        notice=f"تم ضبط حساب الإدارة لاستلام إشارات {symbol} فقط (بينما باقي الأسواق تعمل للمستخدمين والقنوات).",
    )


async def admin_signal_markets_all(q, uid, context, bot):
    await afn.run(db.set_setting, "admin_signal_markets", "")
    await afn.run(audit_log.log, uid, "admin_signal_markets:ALL")
    await admin_signal_markets(q, uid, context, bot, notice="تم تفعيل استلام جميع الأسواق المفعّلة على حساب الإدارة.")


async def admin_market_toggle(q, uid, context, bot, symbol: str, ctx_tag: str = ""):
    sym_clean = (symbol or "").strip().upper()
    r = await afn.run(_market_state, sym_clean)
    if not r:
        await render(q, f"السوق {esc(sym_clean)} غير موجود.", kb.admin_nav(), bot)
        return
    nxt = 0 if int(r["enabled"] or 0) else 1
    await afn.run(_market_toggle_apply, sym_clean, nxt)
    if nxt == 1:
        await afn.run(_sync_enabled_symbols_to_vip, [sym_clean])
    await afn.run(audit_log.log, uid, f"market_toggle:{sym_clean}:{nxt}")
    notice = f"تم {'تفعيل' if nxt else 'إغلاق'} {sym_clean} بنجاح {'🟢' if nxt else '🔒'}"
    if ctx_tag and "_" in ctx_tag:
        cat, pg_s = ctx_tag.rsplit("_", 1)
        try:
            pg = int(pg_s)
        except ValueError:
            pg = 1
        await admin_markets_category(q, uid, context, bot, cat, page=pg, notice=notice)
    else:
        await admin_markets(q, uid, context, bot, notice=notice)


async def admin_market_verify(q, uid, context, bot, symbol: str):
    """REAL provider verification: fetch a live candle through the data chain."""
    r = await afn.run(_market_provider_symbol, symbol)
    if not r:
        await render(q, "السوق غير موجود.", kb.admin_nav(), bot)
        return
    await render(q, f"🔄 جارٍ اختبار <b>{esc(symbol)}</b> مع السوق اللحظي فعلياً...", None)
    try:
        payload = await asyncio.to_thread(market_data.fetch_candles, r["provider_symbol"], "15min", config.market_api_key(), 40, False)
        ok = True
        detail = f"استلم {len(payload['values'])} شمعة حية · آخر شمعة {payload['last_candle_iso']}"
    except RuntimeError as e:
        ok = False
        detail = str(e)
    await afn.run(_market_verify_apply, symbol, 1 if ok else 0)
    await afn.run(audit_log.log, uid, f"market_verify:{symbol}:{ok}")
    icon = "✅" if ok else "❌"
    await render(q, f"{icon} <b>نتيجة التحقق من {esc(symbol)}</b>\n{esc(detail)}\n\n"
                    f"<i>{'أصبح السوق موثقاً — يمكنك تفعيله الآن.' if ok else 'لم يوافق المزود؛ بقي السوق مغلقاً.'}</i>",
                 kb.admin_nav(), bot)


async def admin_market_add(q, uid, context, bot):
    context.user_data["awaiting_market_add"] = True
    await render(q, "➕ أرسل:\n<code>SYMBOL | PROVIDER_SYMBOL | الاسم | التصنيف</code>\nالتصنيف: forex / metal / crypto / index",
                 kb.admin_nav(), bot)


# --- Timeframes ------------------------------------------------------------------
async def admin_timeframes(q, uid, context, bot):
    rows = await afn.run(_timeframe_rows)
    lines = ["⏱ <b>إدارة الأطر الزمنية</b>\nالإطار غير الموثق لا يُتاح للمستخدمين أو المحرك.", ""]
    rows_kb = []
    for r in rows:
        state = "🟢" if (r["enabled"] and r["verified"]) else ("🔵 موثق" if r["verified"] else "⚪ مغلق")
        lines.append(f"{state} <b>{r['code']}</b> · {esc(r['provider_interval'])}")
        row = [kb.btn(f"{r['code']}: {'إيقاف' if r['enabled'] else 'تفعيل'}", f"a:tf_toggle:{r['code']}")]
        if not r["verified"]:
            row.append(kb.btn("✅ تحقق", f"a:tf_verify:{r['code']}"))
        rows_kb.append(row)
    await render(q, "\n".join(lines), kb.admin_nav(rows_kb), bot)


async def admin_tf_toggle(q, uid, context, bot, code: str):
    r = await afn.run(_tf_state, code)
    if not r:
        await render(q, "الإطار غير موجود.", kb.admin_nav(), bot)
        return
    if not r["verified"]:
        await render(q, "⛔ الإطار غير موثق لدى المزود.", kb.admin_nav(), bot)
        return
    await afn.run(_tf_toggle_apply, code, 0 if r["enabled"] else 1)
    await afn.run(audit_log.log, uid, f"timeframe_toggle:{code}")
    await admin_timeframes(q, uid, context, bot)


async def admin_tf_verify(q, uid, context, bot, code: str):
    from fysb.engines.market_data import parse_interval
    interval = parse_interval(code)
    r = await afn.run(_verified_provider_symbol)
    symbol = r["provider_symbol"] if r else "XAU/USD"
    try:
        payload = await asyncio.to_thread(market_data.fetch_candles, symbol, interval, config.market_api_key(), 40, False)
        ok, detail = True, f"استلم {len(payload['values'])} شمعة بإطار {interval}"
    except RuntimeError as e:
        ok, detail = False, str(e)
    await afn.run(_tf_verify_apply, code, 1 if ok else 0)
    await afn.run(audit_log.log, uid, f"tf_verify:{code}:{ok}")
    await render(q, f"{'✅' if ok else '❌'} <b>تحقق الإطار {code}</b>\n{esc(detail)}", kb.admin_nav(), bot)


# --- Brokers (real adapter registry: add API → test → approve) ----------------------
def _safe_broker_cfg(raw: str) -> tuple[dict, bool]:
    from fysb.services import secrets as secrets_service
    try:
        return secrets_service.decrypt_json(raw or ""), True
    except Exception:
        return {}, False


async def admin_brokers(q, uid, context, bot):
    rows = await afn.run(_broker_rows)
    body = [f"🏦 <b><u>إدارة الوسطاء (Broker Adapters)</u></b>",
            "▎سجّل وسيطاً بمفاتيح API حقيقية، افحص الاتصال فعلياً، ثم اعتمده من هنا.",
            "▎المفاتيح لا تُعرض ولا تُطبع أبداً — تُخزن مشفرة (ENC1) في قاعدة البيانات فقط.", ""]
    rows_kb = []
    for r in rows:
        cfg, dec_ok = _safe_broker_cfg(r["config_json"] or "")
        state_icon = {"CONFIGURED": "🔵", "VERIFIED": "🟡", "ACTIVE": "🟢", "DISABLED": "⚪"}.get(r["status"], "🔵")
        body.append(f"▎{state_icon} <b>#{r['id']} {esc(r['title'])}</b> · {esc(r['key'])} · <b>{esc(r['status'])}</b>")
        if not dec_ok:
            body.append("   ⚠️ تعذر فك تشفير المفاتيح (تحقق من BROKER_SECRET_KEY أو أعد إدخال الوسيط)")
        elif cfg.get("account_id"):
            body.append(f"   🆔 حساب: <code>{esc(cfg['account_id'])}</code>")
        row = [kb.btn("🔌 فحص اتصال", f"a:brokertest:{r['id']}")]
        if r["status"] == "VERIFIED":
            row.append(kb.btn("✅ اعتماد", f"a:brokerapprove:{r['id']}"))
        row.append(kb.btn("🗑 حذف", f"a:brokerdel:{r['id']}"))
        rows_kb.append(row)
    if not rows:
        body.append("▎السجل فارغ (0 وسطاء).")
    body.append("")
    body.append("🔒 تنفيذ الأوامر الحقيقي يبقى مغلقاً حتى توثيق محرك تنفيذ كامل — الاعتماد هنا يوثق الاتصال فقط.")
    rows_kb.append([kb.btn("➕ إضافة وسيط (موجّه)", "a:brokeradd")])
    await render(q, "\n".join(body), kb.admin_nav(rows_kb), bot)


async def admin_broker_add(q, uid, context, bot):
    clear_flows(context.user_data)
    rows = [[kb.btn(meta["title"], f"a:brokertype:{key}")] for key, meta in BROKER_PRESETS.items()]
    await render(q, "🏦 <b><u>إضافة وسيط — اختر النوع</u></b>\n▎سيُطلب منك: الاسم ثم API Key ثم Secret ثم معرّف الحساب.",
                 kb.admin_nav(rows), bot)


async def admin_broker_type(q, uid, context, bot, key: str):
    meta = BROKER_PRESETS.get(key)
    if not meta:
        await render(q, "نوع غير معروف.", kb.admin_nav(), bot)
        return
    clear_flows(context.user_data)
    context.user_data["broker_draft"] = {"step": "name", "type": key,
                                         "base_url": meta["base_url"], "api_key": "", "api_secret": "", "account_id": ""}
    await render(q, f"🏦 <b>{esc(meta['title'])}</b>\n▎طريقة المصادقة: <b>{esc(meta['auth'])}</b>\n\n"
                    "✍️ أرسل <b>اسم الوسيط</b> عندك (مثال: حسابي الرئيسي):",
                 kb.admin_nav([[kb.btn("🗑 إلغاء", "a:brokercancel")]]), bot)


async def admin_broker_cancel(q, uid, context, bot):
    context.user_data.pop("broker_draft", None)
    await render(q, "🗑 أُلغيت إضافة الوسيط.", kb.admin_nav([[kb.btn("🏦 الوسطاء", "a:brokers")]]), bot)


async def admin_broker_test(q, uid, context, bot, bid: int):
    r = await afn.run(_broker_adapter, bid)
    if not r:
        await render(q, "الوسيط غير موجود.", kb.admin_nav(), bot)
        return
    cfg, dec_ok = _safe_broker_cfg(r["config_json"] or "")
    if not dec_ok:
        await render(q, "❌ تعذر فك تشفير مفاتيح الوسيط — تحقق من <code>BROKER_SECRET_KEY</code> أو احذف الوسيط وأعد إضافته.",
                     kb.admin_nav([[kb.btn("↩️ الوسطاء", "a:brokers")]]), bot)
        return
    await render(q, f"🔌 جارٍ فحص الاتصال الحقيقي مع <b>{esc(r['title'])}</b>...", None)
    ok, detail = await asyncio.to_thread(broker_test_connectivity, cfg, r["key"])
    new_status = "VERIFIED" if ok else ("CONFIGURED" if r["status"] not in ("ACTIVE",) else "ACTIVE")
    await afn.run(_broker_status_apply, bid, new_status)
    await afn.run(audit_log.log, uid, f"broker_test:{bid}:{ok}")
    icon = "✅" if ok else "❌"
    note = "يمكنك اعتماد الوسيط الآن." if ok else "لم ينجح الفحص — راجع المفاتيح/العنوان ثم أعد الفحص."
    await render(q, f"{icon} <b>نتيجة فحص الاتصال</b>\n▎الوسيط: <b>{esc(r['title'])}</b>\n▎النتيجة: <b>{esc(detail)}</b>\n\n{note}",
                 kb.admin_nav([[kb.btn("↩️ الوسطاء", "a:brokers")]]), bot)


async def admin_broker_approve(q, uid, context, bot, bid: int):
    r = await afn.run(_broker_status, bid)
    if not r:
        await render(q, "الوسيط غير موجود.", kb.admin_nav(), bot)
        return
    if r["status"] != "VERIFIED":
        await render(q, "⛔ لا يُعتمد وسيط قبل فحص اتصال ناجح — اضغط «فحص اتصال» أولاً.", kb.admin_nav(), bot)
        return
    await afn.run(_broker_approve_apply, bid)
    await afn.run(audit_log.log, uid, f"broker_approved:{bid}")
    await admin_brokers(q, uid, context, bot)


async def admin_broker_delete(q, uid, context, bot, bid: int):
    await afn.run(_broker_delete_row, bid)
    await afn.run(audit_log.log, uid, f"broker_deleted:{bid}")
    await admin_brokers(q, uid, context, bot)


async def admin_accounts(q, uid, context, bot):
    rows = await afn.run(_account_rows)
    lines = ["🔗 <b>حسابات التداول المرتبطة</b>", ""]
    if rows:
        for r in rows:
            lines.append(f"  #{r['id']} {esc(r['full_name'] or '')} · {esc(r['broker'])} · {esc(r['account_type'])} · {esc(r['status'])}")
    else:
        lines.append("لا توجد حسابات وسطاء مرتبطة (متاح بعد توثيق Broker Adapter).")
    await render(q, "\n".join(lines), kb.admin_nav(), bot)


# --- Demo overview (admin) ----------------------------------------------------------
async def admin_demo(q, uid, context, bot):
    accounts, open_n, closed, recent = await afn.run(_demo_overview_rows)
    lines = ["🧪 <b>مركز التداول التجريبي</b>", "",
             f"👥 حسابات تجريبية: <b>{accounts}</b>",
             f"📂 صفقات مفتوحة: <b>{open_n}</b>",
             f"📜 صفقات مغلقة: <b>{closed['n']}</b> (صافي {closed['s']:+,.2f})", "",
             "<b>أحدث الصفقات:</b>"]
    for r in recent:
        emoji = "📈" if (r["pnl"] or 0) >= 0 else "📉"
        lines.append(f"  {emoji} #{r['id']} {esc(r['full_name'] or '')} {esc(r['symbol'])} {r['direction']} · {r['lots']:g} lots · {esc(r['status'])}")
    if not recent:
        lines.append("  لا توجد صفقات بعد.")
    await render(q, "\n".join(lines), kb.admin_nav(), bot)


# --- LIVE (locked, double confirmation) ----------------------------------------------
async def admin_live(q, uid, context, bot):
    live = await afn.run(db.get_setting, "live_enabled", "0") == "1"
    body = [
        "🔒 <b>التداول الحقيقي (LIVE)</b>",
        "━━━━━━━━━━━━━━━━━━━━",
        f"📌 الحالة: <b>{'⚠️ مفعل' if live else '🟢 مغلق — الوضع الآمن الافتراضي'}</b>",
        "",
        "التنفيذ الحقيقي يتطلب تكامل وسيط موثقاً ومختبراً. لا يوجد تكامل في هذه النسخة،",
        "لذلك يبقى LIVE مغلقياً ولا يُنفذ أي أمر حقيقي مهما كانت الإعدادات.",
        "",
        "<i>لا يوجد زر تفعيل هنا عن قصد: تفعيل LIVE دون وسيط موثق = ادعاء زائف، ومرفوض في هذه المنصة.</i>",
    ]
    await render(q, "\n".join(body), kb.admin_nav(), bot)


# --- v6.11.0 thread-offload helpers ---
def _pl_account_rows(period: str = "all") -> list[dict]:
    """(v6.20.4 PostgreSQL + SQLite safe) closed-signals P/L accounting per symbol."""
    from fysb.services import signal_archive as _sa
    return _sa.admin_pl_rows(period)


def _signal_with_deliveries(sid: int):
    """(v6.11.0 thread-offload helper) full signal row + delivery count, ONE trip."""
    with db.conn() as c:
        r = c.execute("SELECT * FROM signals WHERE id=?", (sid,)).fetchone()
        deliveries = c.execute("SELECT COUNT(*) n FROM signal_deliveries WHERE signal_id=?", (sid,)).fetchone()["n"]
    return r, deliveries


def _signal_delete_tx(sid: int):
    """(v6.11.0 thread-offload helper) erase one signal + its deliveries (tx)."""
    with db.tx() as c:
        sig = c.execute("SELECT symbol, direction FROM signals WHERE id=?", (sid,)).fetchone()
        d = abs(c.execute("DELETE FROM signal_deliveries WHERE signal_id=?", (sid,)).rowcount)
        s = abs(c.execute("DELETE FROM signals WHERE id=?", (sid,)).rowcount)
    return sig, d, s


def _signal_recipients(sid: int) -> list:
    """(v6.11.0 thread-offload helper) last delivery rows of one signal."""
    with db.conn() as c:
        return c.execute("""SELECT d.user_id, d.delivered_at FROM signal_deliveries d
                            WHERE d.signal_id=? ORDER BY d.delivered_at DESC LIMIT 15""", (sid,)).fetchall()


def _signal_recipient_uids(sid: int) -> list[int]:
    """(v6.11.0 thread-offload helper) user ids that received one signal (excludes channel rows <=0)."""
    with db.conn() as c:
        return [r["user_id"] for r in c.execute(
            "SELECT user_id FROM signal_deliveries WHERE signal_id=? AND user_id>0", (sid,))]


def _signal_row_full(sid: int) -> dict:
    """(v6.11.0 thread-offload helper) one full signal row as dict."""
    with db.conn() as c:
        return dict(c.execute("SELECT * FROM signals WHERE id=?", (sid,)).fetchone())


def _dead_signals_count() -> int:
    """(v6.11.0 thread-offload helper) count of CANCELLED/EXPIRED signals."""
    with db.conn() as c:
        return c.execute("SELECT COUNT(*) n FROM signals WHERE status IN ('CANCELLED','EXPIRED')").fetchone()["n"]


def _signal_purge_tx():
    """(v6.11.0 thread-offload helper) bulk-erase dead signals + deliveries (tx)."""
    with db.tx() as c:
        d = abs(c.execute("DELETE FROM signal_deliveries WHERE signal_id IN "
                          "(SELECT id FROM signals WHERE status IN ('CANCELLED','EXPIRED'))").rowcount)
        s = abs(c.execute("DELETE FROM signals WHERE status IN ('CANCELLED','EXPIRED')").rowcount)
    return d, s


def _cancel_signal_tx(sid: int) -> int:
    """(v6.11.0 thread-offload helper) cancel an open signal (tx) — rowcount."""
    with db.tx() as c:
        cur = c.execute("UPDATE signals SET status='CANCELLED', closed_at=? WHERE id=? AND status IN ('ACTIVE','RUNNING','TP1_HIT','TP2_HIT') AND (closed_at IS NULL OR closed_at='')",
                        (config.now(), sid))
        return cur.rowcount


def _engine_page_rows():
    """(v6.11.0 thread-offload helper) armed-markets count + last engine runs."""
    with db.conn() as c:
        markets = c.execute("SELECT COUNT(*) n FROM market_catalog WHERE enabled=1 AND verified=1").fetchone()["n"]
        runs = c.execute("SELECT * FROM engine_runs ORDER BY id DESC LIMIT 10").fetchall()
    return markets, runs


def _enabled_verified_market_dicts() -> list[dict]:
    """(v6.11.0 thread-offload helper) enabled+verified market rows."""
    with db.conn() as c:
        return [dict(r) for r in c.execute("SELECT symbol FROM market_catalog WHERE enabled=1 AND verified=1").fetchall()]


def _engine_runs_clean() -> int:
    """(v6.11.0 thread-offload helper) archive-trim engine_runs to last 500 — rowcount."""
    with db.conn() as c:
        cur = c.execute("""DELETE FROM engine_runs WHERE id NOT IN
                           (SELECT id FROM engine_runs ORDER BY id DESC LIMIT 500)""")
        return cur.rowcount


def _strategy_catalog_rows() -> list[dict]:
    """(v6.11.0 thread-offload helper) the whole strategy_catalog as dicts."""
    with db.conn() as c:
        return [dict(r) for r in c.execute("SELECT * FROM strategy_catalog").fetchall()]


def _strategy_row(code: str):
    """(v6.11.0 thread-offload helper) one strategy_catalog row by code."""
    with db.conn() as c:
        return c.execute("SELECT * FROM strategy_catalog WHERE code=?", (code,)).fetchone()


def _strategy_toggle_state(code: str):
    """(v6.11.0 thread-offload helper) enabled/verified flags of one strategy."""
    with db.conn() as c:
        return c.execute("SELECT enabled,verified FROM strategy_catalog WHERE code=?", (code,)).fetchone()


def _strategy_toggle_apply(code: str, new_val: int) -> None:
    """(v6.11.0 thread-offload helper) write the enabled flag of one strategy."""
    with db.conn() as c:
        c.execute("UPDATE strategy_catalog SET enabled=?,updated_at=? WHERE code=?",
                  (new_val, config.now(), code))


def _strategy_verify_apply(code: str) -> None:
    """(v6.11.0 thread-offload helper) mark one strategy verified+enabled."""
    with db.conn() as c:
        c.execute("UPDATE strategy_catalog SET verified=1,enabled=1,updated_at=? WHERE code=?", (config.now(), code))


def _modes_page_rows():
    """(v6.11.0 thread-offload helper) mode registry rows + current trading mode."""
    with db.conn() as c:
        rows = c.execute("SELECT * FROM mode_registry ORDER BY code").fetchall()
        current = db.get_setting("trading_mode", "SIGNALS_ONLY")
    return rows, current


def _mode_row(code: str):
    """(v6.11.0 thread-offload helper) one mode_registry row by code."""
    with db.conn() as c:
        return c.execute("SELECT * FROM mode_registry WHERE code=?", (code,)).fetchone()


def _market_catalog_all() -> list[dict]:
    """(v6.11.0 thread-offload helper) the whole market_catalog as dicts."""
    with db.conn() as c:
        return [dict(r) for r in c.execute("SELECT * FROM market_catalog ORDER BY symbol").fetchall()]


def _market_state(symbol: str):
    """(v6.11.0 thread-offload helper) enabled/verified flags of one market."""
    with db.conn() as c:
        return c.execute("SELECT symbol,enabled,verified FROM market_catalog WHERE UPPER(symbol)=UPPER(?)", (symbol,)).fetchone()


def _market_toggle_apply(symbol: str, new_val: int) -> None:
    """(v8.4.0 atomic tx) write enabled (+ auto-verify on enable) for one market."""
    with db.tx() as c:
        if new_val:
            c.execute(
                "UPDATE market_catalog SET enabled=1, verified=1, updated_at=? WHERE UPPER(symbol)=UPPER(?)",
                (config.now(), symbol),
            )
        else:
            c.execute(
                "UPDATE market_catalog SET enabled=0, updated_at=? WHERE UPPER(symbol)=UPPER(?)",
                (config.now(), symbol),
            )


def _market_provider_symbol(symbol: str):
    """(v6.11.0 thread-offload helper) provider symbol of one market row."""
    with db.conn() as c:
        return c.execute("SELECT provider_symbol FROM market_catalog WHERE symbol=?", (symbol,)).fetchone()


def _market_verify_apply(symbol: str, verified: int) -> None:
    """(v6.11.0 thread-offload helper) write the verified flag of one market."""
    with db.conn() as c:
        c.execute("UPDATE market_catalog SET verified=?, updated_at=? WHERE symbol=?", (verified, config.now(), symbol))


def _timeframe_rows() -> list:
    """(v6.11.0 thread-offload helper) the whole timeframe_catalog."""
    with db.conn() as c:
        return c.execute("SELECT * FROM timeframe_catalog ORDER BY code").fetchall()


def _tf_state(code: str):
    """(v6.11.0 thread-offload helper) enabled/verified flags of one timeframe."""
    with db.conn() as c:
        return c.execute("SELECT enabled,verified FROM timeframe_catalog WHERE code=?", (code,)).fetchone()


def _tf_toggle_apply(code: str, new_val: int) -> None:
    """(v6.11.0 thread-offload helper) write the enabled flag of one timeframe."""
    with db.conn() as c:
        c.execute("UPDATE timeframe_catalog SET enabled=?,updated_at=? WHERE code=?",
                  (new_val, config.now(), code))


def _verified_provider_symbol():
    """(v6.11.0 thread-offload helper) one verified market to probe TFs against."""
    with db.conn() as c:
        return c.execute("SELECT provider_symbol FROM market_catalog WHERE verified=1 LIMIT 1").fetchone()


def _tf_verify_apply(code: str, verified: int) -> None:
    """(v6.11.0 thread-offload helper) write the verified flag of one timeframe."""
    with db.conn() as c:
        c.execute("UPDATE timeframe_catalog SET verified=?, updated_at=? WHERE code=?", (verified, config.now(), code))


def _broker_rows() -> list:
    """(v6.11.0 thread-offload helper) all broker_adapters, newest first."""
    with db.conn() as c:
        return c.execute("SELECT * FROM broker_adapters ORDER BY id DESC").fetchall()


def _broker_adapter(bid: int):
    """(v6.11.0 thread-offload helper) one full broker_adapter row."""
    with db.conn() as c:
        return c.execute("SELECT * FROM broker_adapters WHERE id=?", (bid,)).fetchone()


def _broker_status(bid: int):
    """(v6.11.0 thread-offload helper) approval status of one broker_adapter."""
    with db.conn() as c:
        return c.execute("SELECT status FROM broker_adapters WHERE id=?", (bid,)).fetchone()


def _broker_status_apply(bid: int, new_status: str) -> None:
    """(v6.11.0 thread-offload helper) write broker_adapter status after a test."""
    with db.conn() as c:
        c.execute("UPDATE broker_adapters SET status=?, updated_at=? WHERE id=?", (new_status, config.now(), bid))


def _broker_approve_apply(bid: int) -> None:
    """(v6.11.0 thread-offload helper) mark one broker_adapter ACTIVE+enabled."""
    with db.conn() as c:
        c.execute("UPDATE broker_adapters SET status='ACTIVE', enabled=1, updated_at=? WHERE id=?", (config.now(), bid))


def _broker_delete_row(bid: int) -> None:
    """(v6.11.0 thread-offload helper) erase one broker_adapter."""
    with db.conn() as c:
        c.execute("DELETE FROM broker_adapters WHERE id=?", (bid,))


def _account_rows() -> list:
    """(v6.11.0 thread-offload helper) latest linked user_accounts with names."""
    with db.conn() as c:
        return c.execute("""SELECT a.*, u.full_name FROM user_accounts a LEFT JOIN users u ON u.user_id=a.user_id
                            ORDER BY a.id DESC LIMIT 10""").fetchall()


def _demo_overview_rows():
    """(v6.11.0 thread-offload helper) demo counters + recent positions, ONE trip."""
    with db.conn() as c:
        accounts = c.execute("SELECT COUNT(*) n FROM demo_accounts").fetchone()["n"]
        open_n = c.execute("SELECT COUNT(*) n FROM demo_positions WHERE status='OPEN'").fetchone()["n"]
        closed = c.execute("SELECT COUNT(*) n, COALESCE(SUM(pnl),0) s FROM demo_positions WHERE status!='OPEN'").fetchone()
        recent = c.execute("""SELECT p.*, u.full_name FROM demo_positions p LEFT JOIN users u ON u.user_id=p.user_id
                              ORDER BY p.id DESC LIMIT 8""").fetchall()
    return accounts, open_n, closed, recent
