"""Admin trading sections: Signals, Engine, Strategies, Modes, Risk, Markets,
Timeframes, Brokers, Accounts, Demo, Live — everything wired to real logic."""
import asyncio
import json

from telegram.ext import ContextTypes

from fysb import afn, config, db
from fysb.engines import market_data
from fysb.engines.strategies import VERIFIABLE
from fysb.handlers.common import render, safe
from fysb.handlers.inputs import BROKER_PRESETS, broker_test_connectivity, clear_flows
from fysb.handlers.user_pages import _safe_int
from fysb.services import audit as audit_log
from fysb.theme import ICON, esc, human_dt
from fysb.ui.cards import render_signal_card, render_signal_compact
from fysb.ui import keyboards as kb
from fysb.ui.cards import render_signal_card


# --- Signals management ----------------------------------------------------------
# v6.10.0 (owner report 5) — the signals center now carries the FULL lifecycle
# categories (بانتظار التنفيذ · قيد التنفيذ في السوق · أهداف جزئية · ضربت الوقف
# · حققت الأهداف · ملغاة/منتهية) plus a REAL profit/loss accounting page per
# instrument — every number from the banked pips the tracker recorded.
SIG_CATS = {
    "pending": ("⛔ بانتظار التنفيذ", "ACTIVE", "بانتظار"),
    "running": ("🚀 قيد التنفيذ في السوق", "RUNNING", "قيد التنفيذ"),
    "partial": ("🏁 أهداف جزئية", "TP1_HIT,TP2_HIT", "أهداف جزئية"),
    "sl": ("🛑 ضربت الوقف", "SL_HIT", "ضربت الوقف"),
    "wins": ("👑 حققت الأهداف", "TP3_HIT", "حققت الأهداف"),
    "dead": ("🧹 ملغاة/منتهية", "CANCELLED,EXPIRED", "ملغاة/منتهية"),
    "all": ("🗂 الكل", "", "الكل"),
}


def _signals_page_body(statuses: tuple, page: int, size: int = 8, scope: str = "all") -> tuple[int, list, int, dict]:
    from fysb.services import signal_archive as _sa
    total, rows, summary = _sa.admin_signals_page(scope=scope, statuses=statuses, page=page, size=size)
    rows_kb = [[kb.btn(render_signal_compact(r), f"a:signalview:{r['id']}")] for r in rows]
    return total, rows_kb, len(rows), summary


async def _render_signals_center(q, bot, cat: str = "all", page: int = 0, scope: str = "today"):
    from fysb.services import signal_archive as _sa
    title, st_csv, _tab = SIG_CATS.get(cat, SIG_CATS["all"])
    statuses = tuple(s for s in st_csv.split(",") if s)
    size = 8
    counts = await afn.run(_sa.admin_scope_counts)
    total, rows_kb, shown, summary = await afn.run(_signals_page_body, statuses, page, size, scope)
    total_pages = max(1, (total + size - 1) // size)

    # Pagination row
    nav_row = []
    if page > 0:
        nav_row.append(kb.btn("◀️ السابق", f"a:sigday:{scope}:{cat}:{page - 1}"))
    if (page + 1) * size < total:
        nav_row.append(kb.btn("التالي ▶️", f"a:sigday:{scope}:{cat}:{page + 1}"))
    if nav_row:
        rows_kb.append(nav_row)

    # Row 0: Royal Date Scope Tabs (Today / Yesterday / By Date / All)
    day_iso = _sa.resolve_scope_date(scope)
    is_today = scope == "today"
    is_yest = scope == "yesterday"
    is_custom_date = bool(day_iso) and not is_today and not is_yest
    is_all = scope == "all"
    date_row = [
        kb.btn(("◉ " if is_today else "") + f"📅 اليوم ({counts['today']})", f"a:sigday:today:{cat}:0",
               style="primary" if is_today else None),
        kb.btn(("◉ " if is_yest else "") + f"📆 أمس ({counts['yesterday']})", f"a:sigday:yesterday:{cat}:0",
               style="primary" if is_yest else None),
        kb.btn(("◉ " if is_custom_date else "") + f"🗓 بالتاريخ ({counts['days']})", "a:sigdates:0",
               style="primary" if is_custom_date else None),
        kb.btn(("◉ " if is_all else "") + f"🗂 الكل ({counts['total']})", f"a:sigday:all:{cat}:0",
               style="primary" if is_all else None),
    ]
    # Category pills bound to active date scope
    cat_row = [kb.btn(("◉ " if cat == key else "") + SIG_CATS[key][2], f"a:sigday:{scope}:{key}:0")
               for key in list(SIG_CATS)[:4]]
    cat_row2 = [kb.btn(("◉ " if cat == key else "") + SIG_CATS[key][2], f"a:sigday:{scope}:{key}:0")
                for key in list(SIG_CATS)[4:]]
    rows_kb.insert(0, date_row)
    rows_kb.insert(1, cat_row)
    rows_kb.insert(2, cat_row2)

    pl_cb = f"a:sigpl:{day_iso}" if is_custom_date else ("a:sigpl" if is_all else f"a:sigpl:{scope}")
    rows_kb.append([kb.btn("💰 محاسبة الربح والخسارة — كل الأدوات", "a:sigpl"),
                    kb.btn("📊 محاسبة هذا اليوم", pl_cb)] if not is_all
                   else [kb.btn("💰 محاسبة الربح والخسارة — كل الأدوات", "a:sigpl")])
    rows_kb.append([kb.btn("⚡ إشارة الآن (بمراحل)", "a:signow", style="success"),
                    kb.btn("🎯 أسواق إشاراتي", "a:adminmkts", style="primary")])
    rows_kb.append([kb.btn("➕ نشر إشارة يدوية", "a:newsignal"), kb.btn("🔄 فحص مفتوحة الآن", "a:tracknow")])
    rows_kb.append([kb.btn("🧹 تنظيف الملغاة والمنتهية", "a:signalpurge")])

    if is_today:
        scope_label = f"{_sa.ar_date_label(_sa.today_iso())} · {title}"
    elif is_yest:
        scope_label = f"{_sa.ar_date_label(_sa.yesterday_iso())} · {title}"
    elif is_custom_date:
        scope_label = f"{_sa.ar_date_label(day_iso)} · {title}"
    else:
        scope_label = f"🗂 جميع التواريخ · {title}"

    extra = ["▎الترقيم تسلسلي حقيقي — نفس الرقم في كل التنبيهات والبطاقات."]
    if shown == 0:
        if is_today:
            extra.append("▎✨ لا توجد إشارات مطابقة اليوم حتى الآن — جرّب «📆 أمس» أو «🗓 بالتاريخ» أو «🗂 الكل».")
        else:
            extra.append("▎لا توجد إشارات في هذا النطاق.")

    body = _sa.luxury_day_header("مركز إدارة الإشارات", scope_label, summary, page, total_pages, extra)
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
        f"🥇 TP1: <b>{b['tp1_n']}</b> · 🛑 وقف: <b>{b['sl_n']}</b></blockquote>"
    )


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
    from fysb.theme import is_signal_closed
    sig = dict(r)
    live_note = ""
    sig_open = (not is_signal_closed(sig)) and sig["status"] in ("ACTIVE", "RUNNING", "TP1_HIT", "TP2_HIT")
    if sig_open:
        try:
            quote = await asyncio.to_thread(market_data.fetch_price, sig["symbol"])
            price = float(quote["price"])
            from fysb import instruments
            from fysb.engines.tracker import drift_pips
            entry = float(sig["entry"])
            drift = drift_pips(sig["symbol"], sig["direction"], entry, price)
            side = "أعلى الدخول" if price > entry else ("عند الدخول" if price == entry else "أدنى الدخول")
            live_note = (f"\n💰 <b>سعر السوق الآن:</b> <code>{instruments.fmt_price(sig['symbol'], price)}</code>"
                         f" · {side} · <b>{drift:+.1f} pips</b>")
            sl = float(sig["stop"])
            beyond = price >= sl if sig["direction"] == "SELL" else price <= sl
            if beyond:
                live_note += "\n⚠️ السعر تجاوز وقف الخسارة — اضغط «فحص مفتوحة الآن» لتحصيل الحالة فوراً."
        except Exception:
            live_note = "\n⚠️ تعذر جلب السعر اللحظي الآن (المزود غير متاح لحظياً)."
    elif sig["status"] in ("TP1_HIT", "TP2_HIT"):
        live_note = f"\n🔒 <b>صفقة مغلقة على ربح محقق ({float(sig.get('pips_result') or 0):+.1f} pips)</b> — أُغلقت عند التأمين."
    rows_kb = []
    if sig_open:
        rows_kb.append([kb.btn("🚫 إلغاء الإشارة", f"a:cancelsignal:{sid}"), kb.btn("🔄 فحص الآن", "a:tracknow")])
    rows_kb.append([
        kb.btn("📊 شارت الإشارة", f"a:sigchart:{sid}", style="primary"),
        kb.btn("🏆 نشر إثبات للقنوات", f"a:sigproof:{sid}", style="success"),
    ])
    rows_kb.append([kb.btn("🗑 حذف نهائي للإشارة", f"a:delsignal:{sid}")])
    rows_kb.append([kb.btn("👥 المستلمون", f"a:signalrecipients:{sid}"), kb.btn("↩️ الإشارات", "a:signals")])
    body = render_signal_card(sig) + f"\n\n📬 استلمتها <b>{deliveries}</b> نقطة مستخدم.{live_note}"
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
              f"▎🔒 سُجّل في سجل التدقيق الملكي</blockquote>")
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
                    f"▎🔒 سُجّل في سجل التدقيق الملكي</blockquote>",
                 kb.admin_nav([[kb.btn("↩️ مركز الإشارات", "a:signals")]]), bot)


async def admin_cancel_signal(q, uid, context, bot, sid: int):
    cancelled = await afn.run(_cancel_signal_tx, sid)
    await afn.run(audit_log.log, uid, f"cancel_signal:{sid}")
    if cancelled:
        from fysb.engines.tracker import TERMINAL, seq_of
        from fysb.theme import dir_badge, esc, status_ar
        sig = await afn.run(_signal_row_full, sid)
        seq = await afn.run(seq_of, sid)
        text = (f"⛔ <b><u>تحديث الإشارة الملكية #{seq}</u></b> — {esc(sig['symbol'])} {dir_badge(sig['direction'])}\n"
                f"<blockquote>▎📌 الحالة الجديدة: <b>{status_ar('CANCELLED')}</b>\n"
                f"▎📌 السبب: قرار إداري موثق من الإدارة الملكية</blockquote>")
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
        lines.append(f"  {r['created_at'][11:16]} {esc(r['symbol'])} {esc(r['timeframe'])}: {esc(r['result'])}")
    rows_kb = [
        [kb.btn(f"{'⏹ إيقاف المحرك' if enabled else '▶️ تشغيل المحرك'}", "a:enginetoggle", style="danger" if enabled else "success")],
        [kb.btn(f"📐 فريمات المسح الآلي ({scan_tfs_raw or 'M15,H1'})", "a:scantfs", style="primary")],
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


# --- v6.25.0 Live News Guard, Strategy Leaderboard & Currency Strength ---------
def _news_guard_state_sync() -> tuple[bool, int, str]:
    from fysb.engines import news_guard as _ng
    on = _ng.is_enabled()
    win = _ng.window_minutes()
    body = _ng.render_news_calendar_html(enabled=on, buf_min=win)
    return on, win, body


def _news_guard_toggle_sync() -> bool:
    cur = (db.get_setting("news_guard_enabled", "1") or "1") == "1"
    nxt = "0" if cur else "1"
    db.set_setting("news_guard_enabled", nxt)
    return nxt == "1"


def _news_guard_set_win_sync(mins: int) -> int:
    m = max(5, min(120, int(mins)))
    db.set_setting("news_guard_minutes", str(m))
    return m


async def admin_news_guard(q, uid, context, bot):
    on, win, body = await afn.run(_news_guard_state_sync)
    rows_kb = [
        [kb.btn("⏹ إيقاف درع الأخبار" if on else "▶️ تفعيل درع الأخبار", "a:newsguardtoggle",
                style="danger" if on else "success")],
        [
            kb.btn(f"{'✅ ' if win == 15 else ''}±15د", "a:newsguardwin:15", style="success" if win == 15 else "primary"),
            kb.btn(f"{'✅ ' if win == 30 else ''}±30د", "a:newsguardwin:30", style="success" if win == 30 else "primary"),
            kb.btn(f"{'✅ ' if win == 45 else ''}±45د", "a:newsguardwin:45", style="success" if win == 45 else "primary"),
            kb.btn(f"{'✅ ' if win == 60 else ''}±60د", "a:newsguardwin:60", style="success" if win == 60 else "primary"),
        ],
        [kb.btn("🔄 تحديث التقويم", "a:newsguard", style="primary"), kb.btn("↩️ المحرك", "a:engine")],
    ]
    await render(q, body, kb.admin_nav(rows_kb), bot)


async def admin_news_guard_toggle(q, uid, context, bot):
    nxt = await afn.run(_news_guard_toggle_sync)
    await afn.run(audit_log.log, uid, f"news_guard_enabled:{int(nxt)}")
    await admin_news_guard(q, uid, context, bot)


async def admin_news_guard_win(q, uid, context, bot, mins: int):
    m = await afn.run(_news_guard_set_win_sync, mins)
    await afn.run(audit_log.log, uid, f"news_guard_minutes:{m}")
    await admin_news_guard(q, uid, context, bot)


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


async def admin_strength(q, uid, context, bot):
    from fysb.engines import intelligence as _intel
    body = await afn.run(_intel.render_strength_card)
    rows_kb = [
        [kb.btn("🔄 تحديث فوري", "a:strength", style="success"), kb.btn("🏆 مصفوفة الاستراتيجيات", "a:leaderboard", style="primary")],
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
    with db.conn() as c:
        rows = c.execute(
            "SELECT symbol FROM market_catalog WHERE enabled=1 AND verified=1 ORDER BY symbol LIMIT 12"
        ).fetchall()
    return [r["symbol"] for r in rows] or ["XAUUSD", "EURUSD", "BTCUSD"]


async def admin_backtest(q, uid, context, bot):
    mkts = await afn.run(_enabled_backtest_markets)
    rows_kb = []
    for sym in mkts[:8]:
        rows_kb.append([
            kb.btn(f"🧪 {sym} M15", f"a:btrun:{sym}:M15"),
            kb.btn(f"🧪 {sym} H1", f"a:btrun:{sym}:H1"),
        ])
    rows_kb.append([kb.btn("🧠 العودة للمحرك", "a:engine")])
    body = (
        "🧪 <b><u>مختبر الاختبار التاريخي المؤسسي (Walk-Forward Backtest)</u></b>\n"
        "<blockquote>"
        "▎📊 يختبر جميع الاستراتيجيات الـ 13 على شموع السوق التاريخية المغلقة (بلا Repainting).\n"
        "▎🛡 يطبّق فلتر الإجماع الاتجاهي، وقاعدة SL-أولاً، وتأمين نقطة الدخول بعد TP1.\n"
        "▎📈 يحسب نسبة النجاح، عامل الربح (Profit Factor)، صافي R، وأقصى تراجع (Max DD)."
        "</blockquote>\n"
        "اختر السوق والإطار الزمني لبدء الاختبار:"
    )
    await render(q, body, kb.admin_nav(rows_kb), bot)


async def admin_backtest_run(q, uid, context, bot, symbol: str, tf: str):
    from fysb.engines.backtest import format_report, run_backtest
    from fysb.engines.quality_gate import DEFAULT_MIN_SCORE
    sym = (symbol or "XAUUSD").strip().upper()
    tf_u = (tf or "M15").strip().upper()
    min_sc = await afn.run(db.get_setting_int, "min_signal_score", DEFAULT_MIN_SCORE)
    await render(q, f"🧪 <b>جارٍ تشغيل الاختبار التاريخي المؤسسي لـ {esc(sym)} ({esc(tf_u)})…</b>\n<i>يُفحص حتى 800 شمعة مغلقة مع فلتر الفريم الأعلى والتذبذب — يرجى الانتظار بضع ثوانٍ.</i>", None, bot)
    rep = await asyncio.to_thread(run_backtest, sym, tf_u, min_score=min_sc, api_key=config.market_api_key())
    await afn.run(audit_log.log, uid, f"backtest:{sym}:{tf_u}:n={rep.filled_trades}:r={rep.net_r}")
    rows_kb = [
        [
            kb.btn(("◉ " if tf_u == "M15" else "") + "⏱ M15", f"a:btrun:{sym}:M15"),
            kb.btn(("◉ " if tf_u == "H1" else "") + "⏱ H1", f"a:btrun:{sym}:H1"),
            kb.btn(("◉ " if tf_u == "H4" else "") + "⏱ H4", f"a:btrun:{sym}:H4"),
        ],
        [kb.btn(f"🔄 إعادة {sym} {tf_u}", f"a:btrun:{sym}:{tf_u}"), kb.btn("🧪 سوق آخر", "a:backtest")],
        [kb.btn("🧠 المحرك", "a:engine"), kb.btn("📡 الإشارات", "a:signals")],
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
    cur = await afn.run(db.get_setting_int, setting_key, 300 if key == "interval" else 80)
    await afn.run(db.set_setting, setting_key, int(max(lo, min(hi, cur + delta))))
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
    ks = await afn.run(db.kill_switch_on)
    vf = await afn.run(db.get_setting, "volatility_filter", "1")
    lines = ["🛡 <b>محرك إدارة المخاطر</b>",
             f"⛔ Kill Switch: <b>{'مفعل — يمنع أي إشارة جديدة' if ks else 'غير مفعل'}</b>",
             f"🌊 فلتر التقلب: <b>{'مفعل' if vf == '1' else 'متوقف'}</b>",
             "<blockquote>▎✨ <b>سقف السوق</b> يمنع تكدّس الإشارات على سوق واحد دون خنق باقي الأسواق.\n"
             "▎👑 <b>⚡ إشارة الآن للإدارة</b> مستثناة من سقف الإشارات الآلية ولا تستهلك حصتها.</blockquote>",
             "<b>الحدود (تطبق على السيرفر قبل أي إشارة):</b>"]
    rows_kb = []
    for key, title, step, _d in keys:
        val = await afn.run(db.get_setting_float, key, 0)
        lines.append(f"  {title}: <b>{val:g}</b>")
        rows_kb.append([kb.btn("➖", f"a:riskadj:{key}:-{step}"), kb.btn(f"{title} {val:g}", "a:plannoop"), kb.btn("➕", f"a:riskadj:{key}:{step}")])
    rows_kb.append([kb.btn("⛔ تبديل Kill Switch", "a:killswitch", style="danger"),
                    kb.btn("🌊 فلتر التقلب", "a:volfilter", style="primary")])
    await render(q, "\n".join(lines), kb.admin_nav(rows_kb), bot)


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


# --- Markets ---------------------------------------------------------------------
CATEGORY_ORDER = ["metal", "forex", "crypto", "index", "stock", "commodity"]


async def admin_markets(q, uid, context, bot):
    """v6.8.1 — LUXURY CATALOG BOARD: every market inside its royal category
    section (المعادن · الفوركس · الرقمية · المؤشرات · الأسهم · السلع), with
    real counts, live-state markers and a 2-per-row button grid — the old
    flat alphabetical wall is gone."""
    from fysb.theme import CATEGORY_AR, CATEGORY_ICON
    rows = await afn.run(_market_catalog_all)
    if not rows:
        await render(q, "🌐 لا توجد أسواق في الفهرس بعد.", kb.admin_nav([[kb.btn("➕ إضافة سوق", "a:market_add")]]), bot)
        return
    total = len(rows)
    verified = sum(1 for r in rows if r["verified"])
    enabled = sum(1 for r in rows if r["enabled"])
    live = sum(1 for r in rows if r["enabled"] and r["verified"])
    lines = ["🌐 <b><u>إدارة الأسواق — الفهرس الملكي بأقسامه</u></b>",
             "<blockquote>"
             f"▎🗂 إجمالي الأدوات: <b>{total}</b> · موثقة: <b>{verified}</b> · مفعلة: <b>{enabled}</b> · جاهزة للتداول: <b>{live}</b>\n"
             "▎السوق الجديد يبقى مغلقاً حتى يثبت المزود بياناته فعلياً (زر تحقق)</blockquote>"]
    sections: dict[str, list] = {}
    for r in rows:
        sections.setdefault((r.get("category") or "forex"), []).append(r)
    ordered = [c for c in CATEGORY_ORDER if c in sections] + \
              [c for c in sections if c not in CATEGORY_ORDER]
    rows_kb = []
    for cat in ordered:
        icon = CATEGORY_ICON.get(cat, "🌐")
        title = CATEGORY_AR.get(cat, cat)
        grp = sections[cat]
        sec_ver = sum(1 for r in grp if r["verified"])
        sec_en = sum(1 for r in grp if r["enabled"])
        lines.append(f"\n{icon} <b><u>{title}</u></b> — {len(grp)} أدوات · موثقة {sec_ver} · مفعلة {sec_en}")
        for r in grp:
            state = "🟢" if (r["enabled"] and r["verified"]) else ("🔵" if r["verified"] else "⚪")
            lines.append(f"▎{state} <b>{esc(r['symbol'])}</b> — {esc(r['title'])} · <code>{esc(r['provider_symbol'])}</code>"
                         f" · pip={r['pip_size']} · digits={r['digits']}")
        # 2-per-row toggle grid per section (compact luxury board)
        grid = []
        for r in grp:
            mark = "🟢" if (r["enabled"] and r["verified"]) else ("🔵" if r["verified"] else "⚪")
            grid.append(kb.btn(f"{mark} {r['symbol']}: {'إيقاف' if r['enabled'] else 'تفعيل'}",
                               f"a:market_toggle:{r['symbol']}"))
            if len(grid) == 2:
                rows_kb.append(grid)
                grid = []
        if grid:
            rows_kb.append(grid)
        unverified = [r for r in grp if not r["verified"]]
        while unverified:
            pair, unverified = unverified[:2], unverified[2:]
            rows_kb.append([kb.btn(f"✅ تحقق {r['symbol']} من المزود", f"a:market_verify:{r['symbol']}") for r in pair])
    rows_kb.append([kb.btn("➕ إضافة سوق جديد", "a:market_add")])
    rows_kb.append([kb.btn("🎯 أسواق إشارات حساب الإدارة", "a:adminmkts", style="primary")])
    await render(q, "\n".join(lines), kb.admin_nav(rows_kb), bot)


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


async def admin_market_toggle(q, uid, context, bot, symbol: str):
    r = await afn.run(_market_state, symbol)
    if not r:
        await render(q, "السوق غير موجود.", kb.admin_nav(), bot)
        return
    if not r["verified"]:
        await render(q, "⛔ السوق غير موثق لدى المزود — لا يمكن تفعيله قبل التحقق.", kb.admin_nav(), bot)
        return
    await afn.run(_market_toggle_apply, symbol, 0 if r["enabled"] else 1)
    await afn.run(audit_log.log, uid, f"market_toggle:{symbol}")
    await admin_markets(q, uid, context, bot)


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
        return c.execute("SELECT enabled,verified FROM market_catalog WHERE symbol=?", (symbol,)).fetchone()


def _market_toggle_apply(symbol: str, new_val: int) -> None:
    """(v6.11.0 thread-offload helper) write the enabled flag of one market."""
    with db.conn() as c:
        c.execute("UPDATE market_catalog SET enabled=?,updated_at=? WHERE symbol=?",
                  (new_val, config.now(), symbol))


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
