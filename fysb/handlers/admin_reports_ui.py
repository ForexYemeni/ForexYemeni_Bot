"""v8.14.20 — «التقرير الموحد الفاخر» (the LUXURY UNIFIED REPORT).

The owner's decree, executed 100% real:
① EVERY dimension is truly complete: the pickers merge the FULL bot catalogs
   (market_catalog / strategy_catalog / timeframe_catalog — every market,
   strategy and timeframe the bot owns, zero LIMIT) with every value that
   actually traded in the live signals table, each with its real count.
② The FOURTH dimension: an honest period (24h/3d/7d/14d/30d/60d/90d/180d/365d
   or FULL HISTORY) bounds the counter, the report, the trades detail and the
   broadcast — the period is named on every surface, nothing is unbounded
   silently.
③ بث لي + بث للقنوات: a dedicated «📤 بث لي التقرير» button delivers the exact
   report (photo + full text) to the owner's chat; every send failure is shown
   with its real reason — never swallowed; every channel broadcast leaves an
   automatic owner's copy.
④ The REAL market picture: the executive PNG of the EXACT slice & period
   (rendered from real aggregates, the 50-pip decree included) is one tap away
   and rides with every preview/broadcast.

Flow: a:repfilter (control card) → a:repf:m|s|t|d (pickers) → a:repf:<k>:<VAL>
      → a:repf:run (report page) → a:repfp (PNG) / a:repfme (بث لي) /
      a:repfd (trades detail) / a:repfb:preview|confirm|cancel (channels).
State lives in context.user_data["rep_filter"] / ["repf_broadcast_text"] /
["repf_png"] — per-admin, isolated.
"""
from __future__ import annotations

import io

from telegram import Update
from telegram.ext import ContextTypes

from fysb import afn, config, db
from fysb.handlers.common import render
from fysb.services import audit as audit_log
from fysb.ui import keyboards as kb

ALL_LABEL = "الكل"
PERIOD_PRESETS = (1, 3, 7, 14, 30, 60, 90, 180, 365)
PAGE_SIZE = 40  # picker pagination — Telegram caps inline keyboards at 100 buttons

# v8.14.21 — the market picker is ORGANIZED by real catalog categories,
# not a giant alphabetical wall. Gold-first order, honest Arabic labels.
MARKET_CATEGORY_AR = (
    ("metal", "🥇 المعادن"),
    ("forex", "💱 الفوركس"),
    ("crypto", "₿ العملات الرقمية"),
    ("index", "📊 المؤشرات"),
    ("stock", "📈 الأسهم"),
    ("commodity", "🛢 السلع"),
    ("other", "🖥 أخرى"),
)

_PICK_KINDS = {
    "m": ("market", "symbol", ("market_catalog", "symbol"), "📈 اختر السوق", "a:repf:m:"),
    "s": ("strategy", "strategy", ("strategy_catalog", "code"), "🧠 اختر الاستراتيجية", "a:repf:s:"),
    "t": ("timeframe", "timeframe", ("timeframe_catalog", "code"), "⏱ اختر الإطار الزمني", "a:repf:t:"),
}


def _get_filter(ud: dict) -> dict:
    f = ud.get("rep_filter")
    if not isinstance(f, dict) or "days" not in f:
        f = {"market": None, "strategy": None, "timeframe": None, "days": None}
        ud["rep_filter"] = f
    return f


def _lab(v: str | None) -> str:
    return ALL_LABEL if not v else str(v)


def _period_lab(days: int | None) -> str:
    from fysb.services.digest import period_ar
    return period_ar(days)


# ---------------------------------------------------------------------------
# Sync DB helpers — ALWAYS executed through afn.run (no db access in async)
# ---------------------------------------------------------------------------
def _catalog_values(table: str, col: str) -> list[str]:
    with db.conn() as c:
        rows = c.execute(f"SELECT {col} v FROM {table}").fetchall()
    return sorted({str(r["v"]).strip() for r in rows if str(r["v"]).strip()})


def _signal_values(col: str) -> list[str]:
    with db.conn() as c:
        rows = c.execute(f"SELECT DISTINCT {col} v FROM signals "
                         f"WHERE {col} IS NOT NULL AND {col}!=''").fetchall()
    return sorted({str(r["v"]).strip() for r in rows if str(r["v"]).strip()})


def _all_values(col: str, table: str, cat_col: str) -> list[str]:
    """v8.14.20 — the COMPLETE universe: every catalog entry the bot owns
    UNION every value that actually traded. No LIMIT — nothing is hidden."""
    return sorted(set(_catalog_values(table, cat_col)) | set(_signal_values(col)))


def _counts_map(col: str, days: int | None) -> dict[str, int]:
    """Real per-value signal counts inside the ACTIVE period (one GROUP BY)."""
    from fysb.services.digest import cutoff_iso
    q = f"SELECT {col} v, COUNT(*) n FROM signals WHERE {col} IS NOT NULL AND {col}!=''"
    params: list = []
    since = cutoff_iso(days)
    if since:
        q += " AND created_at>=?"
        params.append(since)
    with db.conn() as c:
        rows = c.execute(q + " GROUP BY " + col, params).fetchall()
    return {str(r["v"]): int(r["n"]) for r in rows}


def _market_catalog_categories() -> dict[str, list[str]]:
    """v8.14.21 — symbol → its real catalog category (the organizing spine)."""
    with db.conn() as c:
        rows = c.execute("SELECT symbol, COALESCE(category,'') cat FROM market_catalog").fetchall()
    out: dict[str, list[str]] = {}
    for r in rows:
        out.setdefault(str(r["cat"]).strip() or "other", []).append(str(r["symbol"]).strip())
    return {k: sorted(set(v)) for k, v in out.items()}


def _picker_entries(col: str, table: str, cat_col: str, days: int | None) -> list[tuple[str, int, str]]:
    """[(value, real_period_count, category)] — the COMPLETE universe sorted
    active-first (the values you actually trade lead the list)."""
    universe = _all_values(col, table, cat_col)
    counts = _counts_map(col, days)
    cat_of: dict[str, str] = {}
    if col == "symbol":
        cats = _market_catalog_categories()
        cat_of = {s: c for c, syms in cats.items() for s in syms}
    return sorted(((v, counts.get(v, 0), cat_of.get(v, "other")) for v in universe),
                  key=lambda t: (-t[1], t[0]))


def _market_hub(days: int | None) -> tuple[list[tuple[str, str, int, int]], list[str]]:
    """(categories [(cat, ar_label, n_markets, n_signals)], live_only_symbols) —
    real counts inside the period, live-only symbols listed honestly."""
    cats = _market_catalog_categories()
    counts = _counts_map("symbol", days)
    catalog_syms = {s for syms in cats.values() for s in syms}
    live = _signal_values("symbol")
    live_only = sorted(set(live) - catalog_syms)
    out = []
    for cat, ar in MARKET_CATEGORY_AR:
        syms = cats.get(cat, [])
        out.append((cat, ar, len(syms), sum(counts.get(s, 0) for s in syms)))
    return out, live_only


def _matching_count(f: dict) -> int:
    from fysb.services.digest import cutoff_iso
    where, params = ["1=1"], []
    for key, col in (("market", "symbol"), ("strategy", "strategy"), ("timeframe", "timeframe")):
        if f.get(key):
            where.append(f"{col}=?")
            params.append(f[key])
    since = cutoff_iso(f.get("days"))
    if since:
        where.append("created_at>=?")
        params.append(since)
    with db.conn() as c:
        return int(c.execute(f"SELECT COUNT(*) n FROM signals WHERE {' AND '.join(where)}",
                             params).fetchone()["n"])


def _build_slice_png(f: dict) -> tuple[bytes | None, dict]:
    """The REAL market picture: the executive card rendered from the exact
    slice & period aggregates (the 50-pip decree included). Returns
    (png_bytes | None, honest_digest_dict).
    v8.14.22 — ONE TRUTH: include_private=True — the owner's picture counts
    EXACTLY the population the interactive report counts (every source, no
    silent exclusions). The weekly channel digest keeps its official-only
    policy; the owner's slice never diverges from his own report again."""
    from fysb.services import digest as _digest
    from fysb.ui import share_cards
    d = _digest.build_digest_data(days=f.get("days"), market=f.get("market"),
                                 strategy=f.get("strategy"), timeframe=f.get("timeframe"),
                                 include_private=True)
    try:
        png = share_cards.render_performance_digest_card(d)
    except Exception:
        png = None
    return png, d


def _broadcast_targets() -> list[str]:
    with db.conn() as c:
        return [str(r["chat_id"]).strip()
                for r in c.execute("SELECT chat_id FROM broadcast_channels WHERE enabled=1").fetchall()]


# ---------------------------------------------------------------------------
# Control card & pickers
# ---------------------------------------------------------------------------
def _filter_card_text(f: dict) -> str:
    n = _matching_count(f)
    return (
        "🧭 <b><u>التقرير التفاعلي الشامل</u></b>\n"
        "<blockquote>"
        f"▎📈 السوق: <b>{_lab(f.get('market'))}</b>\n"
        f"▎🧠 الاستراتيجية: <b>{_lab(f.get('strategy'))}</b>\n"
        f"▎⏱ الإطار الزمني: <b>{_lab(f.get('timeframe'))}</b>\n"
        f"▎⏳ الفترة الزمنية: <b>{_period_lab(f.get('days'))}</b>\n"
        f"▎📡 الإشارات المطابقة الآن: <b>{n}</b> (عدد حقيقي من قاعدة البيانات)"
        "</blockquote>\n"
        "<i>اختر بُعداً لتصفية التقرير، أو اتركه «الكل» — ثم ولّد التقرير الصادق.</i>"
    )


def _filter_kb(f: dict):
    return kb.admin_nav([
        [kb.btn(f"📈 السوق: {_lab(f.get('market'))}", "a:repf:m", style="primary"),
         kb.btn(f"🧠 الاستراتيجية: {_lab(f.get('strategy'))}", "a:repf:s", style="primary")],
        [kb.btn(f"⏱ الإطار الزمني: {_lab(f.get('timeframe'))}", "a:repf:t", style="primary"),
         kb.btn(f"⏳ الفترة: {_period_lab(f.get('days'))}", "a:repf:d", style="primary")],
        [kb.btn("📊 توليد التقرير الصادق", "a:repf:run", style="success"),
         kb.btn("♻️ تصفير الفلاتر", "a:repf:clear", style="danger")],
    ])


async def rep_filter_home(q, uid: int, context: ContextTypes.DEFAULT_TYPE, bot):
    f = _get_filter(context.user_data)
    await render(q, _filter_card_text(f), _filter_kb(f), bot)


async def rep_filter_pick(q, uid: int, context: ContextTypes.DEFAULT_TYPE, bot, parts: list[str]):
    f = _get_filter(context.user_data)
    kind = parts[0] if parts else ""
    spec = _PICK_KINDS.get(kind)
    if not spec:
        await rep_filter_home(q, uid, context, bot)
        return
    key, col, (cat_table, cat_col), title, data_prefix = spec

    # a value was chosen
    if len(parts) >= 2:
        val = ":".join(parts[1:])  # symbols/strategies never contain ':', but stay safe
        f[key] = None if val == "-" else val[:24]
        await render(q, _filter_card_text(f), _filter_kb(f), bot)
        return

    if kind == "m":
        # v8.14.21 — the market picker opens ORGANIZED: real categories, not a wall
        await rep_filter_market_hub(q, uid, context, bot)
        return

    # open the picker: the COMPLETE universe (catalog ∪ live signals), zero LIMIT,
    # sorted ACTIVE-FIRST — what you actually trade leads the list
    entries = await afn.run(_picker_entries, col, cat_table, cat_col, f.get("days"))
    values = [v for v, _n, _c in entries]
    counts = {v: n for v, n, _c in entries}
    await _render_picker(q, bot, f, kind, title, data_prefix, values, counts, page=0)


async def rep_filter_pick_page(q, uid: int, context: ContextTypes.DEFAULT_TYPE, bot,
                               kind: str, page_s: str):
    """v8.14.20 — luxury pagination for the COMPLETE pickers (101 markets
    cannot fit one Telegram keyboard — every page stays real & complete)."""
    spec = _PICK_KINDS.get(kind)
    f = _get_filter(context.user_data)
    if not spec:
        await rep_filter_home(q, uid, context, bot)
        return
    _key, col, (cat_table, cat_col), title, data_prefix = spec
    try:
        page = max(0, int(page_s or 0))
    except (TypeError, ValueError):
        page = 0
    values = await afn.run(_all_values, col, cat_table, cat_col)
    counts = await afn.run(_counts_map, col, f.get("days"))
    await _render_picker(q, bot, f, kind, title, data_prefix, values, counts, page=page)


async def rep_filter_market_hub(q, uid: int, context: ContextTypes.DEFAULT_TYPE, bot):
    """v8.14.21 — the market picker opens ORGANIZED: real catalog categories
    (المعادن · الفوركس · العملات الرقمية · المؤشرات · الأسهم · السلع) with
    their REAL market counts and signal counts inside the period — plus the
    complete alphabetical view and live-only symbols. Nothing is hidden."""
    f = _get_filter(context.user_data)
    cats, live_only = await afn.run(_market_hub, f.get("days"))
    rows: list = []
    pair: list = []
    for cat, ar, n_markets, n_signals in cats:
        if n_markets == 0:
            continue
        pair.append(kb.btn(f"{ar} · {n_markets} سوق · {n_signals} إشارة",
                           f"a:repf:cat:m:{cat}:0", style="primary"))
        if len(pair) == 2:
            rows.append(pair)
            pair = []
    if pair:
        rows.append(pair)
    if live_only:
        rows.append([kb.btn(f"📡 من السجل الحي فقط · {len(live_only)}",
                            "a:repf:cat:m:__live__:0", style="success")])
    rows.append([kb.btn("🖥 كل الأسواق أبجدياً", "a:repf:pg:m:0", style="primary"),
                 kb.btn(f"🌐 {ALL_LABEL}", "a:repf:m:-", style="success")])
    rows.append([kb.btn("↩️ بطاقة التصفية", "a:repfilter", style="primary")])
    await render(
        q,
        "📈 <b><u>اختر السوق — مصنّفاً ومرتباً</u></b>\n"
        "<blockquote>▎الفئات من كتالوج البوت الحقيقي — الأعداد حقيقية وضمن الفترة: "
        f"<b>{_period_lab(f.get('days'))}</b>\n"
        "▎كل قيمة داخل الفئة تظهر بعددها الفعلي — والقائمة كاملة بلا أي اقتصاص</blockquote>\n"
        "<i>اختر فئة ثم سوقاً، أو «🌐 الكل».</i>",
        kb.admin_nav(rows), bot)


async def rep_filter_pick_category(q, uid: int, context: ContextTypes.DEFAULT_TYPE, bot,
                                   kind: str, cat: str, page_s: str):
    """v8.14.21 — one ORGANIZED category page: only its symbols (catalog ∪ live),
    active-first, with real counts; paginated when huge; the hub is one tap back."""
    spec = _PICK_KINDS.get(kind)
    f = _get_filter(context.user_data)
    if not spec or kind != "m":
        await rep_filter_home(q, uid, context, bot)
        return
    _key, col, (cat_table, cat_col), title, data_prefix = spec
    try:
        page = max(0, int(page_s or 0))
    except (TypeError, ValueError):
        page = 0
    entries = await afn.run(_picker_entries, col, cat_table, cat_col, f.get("days"))
    counts = {v: n for v, n, _c in entries}
    if cat == "__live__":
        catalog_syms = set(await afn.run(_catalog_values, "market_catalog", "symbol"))
        values = sorted(set(_signal_values("symbol")) - catalog_syms)
        ar_label = "📡 من السجل الحي فقط (خارج الكتالوج)"
    else:
        values = [v for v, _n, c3 in entries if c3 == cat]
        ar_label = dict(MARKET_CATEGORY_AR).get(cat, cat)
    last = max(0, (len(values) - 1) // PAGE_SIZE)
    page = min(page, last)
    chunk = values[page * PAGE_SIZE:(page + 1) * PAGE_SIZE]
    rows = [[kb.btn(f"🌐 {ALL_LABEL}", data_prefix + "-", style="success")]]
    pair: list = []
    for v in chunk:
        pair.append(kb.btn(f"{v} · {counts.get(v, 0)}", data_prefix + str(v), style="primary"))
        if len(pair) == 2:
            rows.append(pair)
            pair = []
    if pair:
        rows.append(pair)
    if not values:
        rows.append([kb.btn("— لا أسواق في هذه الفئة —", "a:repf:none", style="danger")])
    if last > 0:
        pager = []
        if page > 0:
            pager.append(kb.btn("◀️ السابق", f"a:repf:cat:{kind}:{cat}:{page - 1}", style="primary"))
        pager.append(kb.btn(f"📄 {page + 1}/{last + 1} — {len(values)} قيمة", "a:repf:none", style="primary"))
        if page < last:
            pager.append(kb.btn("التالي ▶️", f"a:repf:cat:{kind}:{cat}:{page + 1}", style="primary"))
        rows.append(pager)
    rows.append([kb.btn("↩️ فئات الأسواق", "a:repf:m", style="primary"),
                 kb.btn(f"🌐 {ALL_LABEL}", "a:repf:m:-", style="success")])
    await render(
        q,
        f"{ar_label}\n"
        "<blockquote>▎قيم هذه الفئة كاملة 100% — الأعداد حقيقية وضمن الفترة: "
        f"<b>{_period_lab(f.get('days'))}</b> — مرتبة الأنشط أولاً</blockquote>\n"
        "<i>اختر سوقاً، أو «🌐 الكل».</i>",
        kb.admin_nav(rows), bot)


async def rep_filter_escape(q, uid: int, context: ContextTypes.DEFAULT_TYPE, bot):
    """v8.14.21 — the honest escape hatch: ONE tap clears the slice and shows
    EVERYTHING the period actually holds. An empty filtered report is never a
    dead end — the real numbers are one tap away."""
    f = _get_filter(context.user_data)
    f["market"] = None
    f["strategy"] = None
    f["timeframe"] = None
    await rep_filter_run(q, uid, context, bot)


async def _render_picker(q, bot, f: dict, kind: str, title: str, data_prefix: str,
                         values: list[str], counts: dict[str, int], page: int):
    last = max(0, (len(values) - 1) // PAGE_SIZE)
    page = min(page, last)
    chunk = values[page * PAGE_SIZE:(page + 1) * PAGE_SIZE]
    rows = [[kb.btn(f"🌐 {ALL_LABEL}", data_prefix + "-", style="success")]]
    pair: list = []
    for v in chunk:
        pair.append(kb.btn(f"{v} · {counts.get(v, 0)}", data_prefix + str(v), style="primary"))
        if len(pair) == 2:
            rows.append(pair)
            pair = []
    if pair:
        rows.append(pair)
    if not values:
        rows.append([kb.btn("— لا قيم مسجلة بعد —", "a:repf:none", style="danger")])
    if last > 0:
        pager = []
        if page > 0:
            pager.append(kb.btn("◀️ السابق", f"a:repf:pg:{kind}:{page - 1}", style="primary"))
        pager.append(kb.btn(f"📄 {page + 1}/{last + 1} — {len(values)} قيمة", "a:repf:none", style="primary"))
        if page < last:
            pager.append(kb.btn("التالي ▶️", f"a:repf:pg:{kind}:{page + 1}", style="primary"))
        rows.append(pager)
    rows.append([kb.btn("↩️ بطاقة التصفية", "a:repfilter", style="primary")])
    await render(
        q,
        f"{title}\n"
        "<blockquote>▎القائمة الكاملة 100%: كل ما يملكه البوت في كتالوجه + كل ما تداول فعلاً في سجله — بلا أي اقتصاص\n"
        f"▎الأعداد بجانب كل قيمة حقيقية وضمن الفترة: <b>{_period_lab(f.get('days'))}</b></blockquote>\n"
        "<i>اختر قيمة، أو «الكل».</i>",
        kb.admin_nav(rows), bot)


async def rep_filter_days(q, uid: int, context: ContextTypes.DEFAULT_TYPE, bot):
    """v8.14.20 — the FOURTH dimension: the honest period picker."""
    f = _get_filter(context.user_data)
    rows: list = []
    pair: list = []
    for dnum in PERIOD_PRESETS:
        label = "⏳ 24 ساعة" if dnum == 1 else f"⏳ {dnum} يوم"
        pair.append(kb.btn(label, f"a:repf:d:{dnum}", style="primary"))
        if len(pair) == 2:
            rows.append(pair)
            pair = []
    if pair:
        rows.append(pair)
    rows.append([kb.btn("🌐 كل التاريخ (بلا حدود)", "a:repf:d:-", style="success")])
    rows.append([kb.btn("↩️ بطاقة التصفية", "a:repfilter", style="primary")])
    await render(
        q,
        "⏳ <b><u>اختر الفترة الزمنية</u></b>\n"
        f"<blockquote>▎الفترة الحالية: <b>{_period_lab(f.get('days'))}</b>\n"
        "▎كل أرقام التقرير والتفاصيل والبث ستُحصر داخل الفترة المختارة — الصدق كامل</blockquote>\n"
        "<i>أو «كل التاريخ» لكل حياة البوت.</i>",
        kb.admin_nav(rows), bot)


async def rep_filter_days_set(q, uid: int, context: ContextTypes.DEFAULT_TYPE, bot, val: str):
    f = _get_filter(context.user_data)
    if val == "-":
        f["days"] = None
    else:
        try:
            f["days"] = max(1, int(val))
        except (TypeError, ValueError):
            f["days"] = None
    await render(q, _filter_card_text(f), _filter_kb(f), bot)


async def rep_filter_run(q, uid: int, context: ContextTypes.DEFAULT_TYPE, bot):
    f = _get_filter(context.user_data)
    from fysb.services import reports
    body = await afn.run(reports.filtered_signals_report,
                         f.get("market"), f.get("strategy"), f.get("timeframe"),
                         f.get("days"))
    png, _d = await afn.run(_build_slice_png, f)
    context.user_data["repf_png"] = png
    await afn.run(audit_log.log, uid,
                  f"interactive_report:{f.get('market') or 'ALL'}:{f.get('strategy') or 'ALL'}:"
                  f"{f.get('timeframe') or 'ALL'}:{f.get('days') or 'FULL'}")
    rows = [
        [kb.btn("🖼 صورة السوق الحقيقية", "a:repfp", style="success"),
         kb.btn("📤 بث لي التقرير", "a:repfme", style="success")],
        [kb.btn("📋 تفاصيل الصفقات المغلقة", "a:repfd", style="primary")],
        [kb.btn("📤 بث هذا التقرير للقنوات", "a:repfb:preview", style="primary")],
        [kb.btn("🎲 كل ما لديك في الفترة", "a:repf:escape", style="success"),
         kb.btn("🧭 تعديل التصفية", "a:repfilter", style="primary")],
        [kb.btn("🔄 تحديث التقرير", "a:repf:run", style="primary"),
         kb.btn("♻️ تصفير الفلاتر", "a:repf:clear", style="danger")],
    ]
    await render(q, body, kb.admin_nav(rows), bot)


# ---------------------------------------------------------------------------
# ④ the REAL market picture — the executive PNG of the exact slice & period
# ---------------------------------------------------------------------------
async def rep_send_png(q, uid: int, context: ContextTypes.DEFAULT_TYPE, bot):
    f = _get_filter(context.user_data)
    png, d = await afn.run(_build_slice_png, f)
    if png is None or int(d.get("total") or 0) == 0:
        await render(q,
                     "🖼 <b>لا توجد بيانات حقيقية في هذا المقطع بعد</b>\n"
                     "<blockquote>▎لا تُولَّد صورة بلا أرقام حقيقية — وسّع التصفية أو الفترة، أو أرسل إشارات أولاً.</blockquote>",
                     kb.admin_nav([[kb.btn("📊 رجوع للتقرير", "a:repf:run", style="primary")]]), bot)
        return
    from fysb.services.digest import slice_ar
    cap = (f"🖼 الصورة التنفيذية الحقيقية — {slice_ar(f.get('market'), f.get('strategy'), f.get('timeframe'))}"
           f" — {_period_lab(f.get('days'))}\nكل رقم فيها محسوب من قاعدة البيانات — قانون الخمسين نقطة مطبق.")
    try:
        await await_bot_photo(bot, config.ADMIN_ID, png, caption=cap)
        photo_ok, why = True, ""
    except Exception as e:
        photo_ok, why = False, str(e)[:160]
    if photo_ok:
        await render(q,
                     "✅ <b><u>وصلك السوق الحقيقي — صورة تنفيذية</u></b>\n"
                     f"<blockquote>▎المقطع: {slice_ar(f.get('market'), f.get('strategy'), f.get('timeframe'))}\n"
                     f"▎الفترة: <b>{_period_lab(f.get('days'))}</b>\n"
                     "▎الصورة مبنية من الأرقام الحقيقية نفسها للتقرير — بلا أي زخرفة</blockquote>",
                     kb.admin_nav([[kb.btn("📊 رجوع للتقرير", "a:repf:run", style="primary")]]), bot)
    else:
        await render(q,
                     f"⚠️ <b>تعذر إرسال الصورة — السبب الحقيقي:</b>\n"
                     f"<blockquote>▎{why}</blockquote>",
                     kb.admin_nav([[kb.btn("📊 رجوع للتقرير", "a:repf:run", style="primary")]]), bot)


# ---------------------------------------------------------------------------
# ③ بث لي — the report lands on the owner's chat, failures shown, never hidden
# ---------------------------------------------------------------------------
async def rep_send_me(q, uid: int, context: ContextTypes.DEFAULT_TYPE, bot):
    f = _get_filter(context.user_data)
    from fysb.services import reports
    body = await afn.run(reports.filtered_signals_report,
                         f.get("market"), f.get("strategy"), f.get("timeframe"),
                         f.get("days"))
    png, d = await afn.run(_build_slice_png, f)
    errors: list[str] = []
    parts = 0
    try:
        if png and int(d.get("total") or 0) > 0:
            await await_bot_photo(bot, config.ADMIN_ID, png,
                                  caption="📤 التقرير الصادق — الصورة التنفيذية الحقيقية للمقطع")
        parts += await afn.run(_chunk_count, body)
        await await_bot_send(bot, config.ADMIN_ID, body)
    except Exception as e:
        errors.append(str(e)[:160])
    if errors:
        await render(q,
                     f"⚠️ <b>تعذر وصول التقرير إليك — السبب الحقيقي:</b>\n"
                     f"<blockquote>▎{errors[0]}</blockquote>\n"
                     "<i>هذا عيب واضح لا يُكتم — حاول مرة أخرى أو أفِش الخطأ لمطوّر البوت.</i>",
                     kb.admin_nav([[kb.btn("📊 رجوع للتقرير", "a:repf:run", style="primary")]]), bot)
        return
    await render(q,
                 "📤 <b><u>وصلك التقرير كاملاً في شاتك</u></b>\n"
                 f"<blockquote>▎النص: <b>{parts}</b> رسالة"
                 + (" · مع صورة السوق التنفيذية" if png and int(d.get("total") or 0) > 0 else "")
                 + f"\n▎الفترة: <b>{_period_lab(f.get('days'))}</b></blockquote>",
                 kb.admin_nav([[kb.btn("📊 رجوع للتقرير", "a:repf:run", style="primary")]]), bot)


def _chunk_count(text: str) -> int:
    return max(1, (len(text or "") + 3899) // 3900)


async def rep_filter_trades(q, uid: int, context: ContextTypes.DEFAULT_TYPE, bot):
    """v8.14.19 — the A-to-Z completeness: every closed trade of the slice,
    truthfully settled (the 50-pip decree included), newest first."""
    f = _get_filter(context.user_data)
    from fysb.services import reports
    body = await afn.run(reports.filtered_trades_detail,
                         f.get("market"), f.get("strategy"), f.get("timeframe"),
                         days=f.get("days"))
    rows = [
        [kb.btn("📊 رجوع للتقرير", "a:repf:run", style="success")],
        [kb.btn("🧭 تعديل التصفية", "a:repfilter", style="primary")],
    ]
    await render(q, body, kb.admin_nav(rows), bot)


# ---------------------------------------------------------------------------
# preview-first broadcast of the EXACT filtered report — failures shown
# ---------------------------------------------------------------------------
async def rep_filter_broadcast(q, uid: int, context: ContextTypes.DEFAULT_TYPE, bot, arg: str):
    """v8.14.20 — broadcast the EXACT filtered report (photo + full text),
    preview-first: the deliverable lands on the owner's chat alone, then he
    confirms. Nothing leaves the bot until his explicit confirmation, every
    failure is reported with its real reason, and the owner always receives a
    copy of what the channels received."""
    f = _get_filter(context.user_data)
    from fysb.services import reports
    body = await afn.run(reports.filtered_signals_report,
                         f.get("market"), f.get("strategy"), f.get("timeframe"),
                         f.get("days"))
    if arg == "confirm":
        text = str(context.user_data.get("repf_broadcast_text") or "")
        png = context.user_data.get("repf_png")
        if not text:
            await render(q, "⛔ لا يوجد تقرير جاهز للبث — ولّد التقرير أولاً.",
                         kb.admin_nav(), bot)
            return
        targets = await afn.run(_broadcast_targets)
        if not targets:
            # honest: no enabled channels — keep the staged state, suggest بث لي
            await render(q,
                         "⛔ <b>لا توجد قنوات مفعّلة للبث</b>\n"
                         "<blockquote>▎فعّل قناة من «📺 القنوات والمجموعات» أولاً — أو استخدم زر «📤 بث لي التقرير» فوراً.\n"
                         "▎التقرير الجاهز محفوظ ولن يضيع.</blockquote>",
                         kb.admin_nav([
                             [kb.btn("📤 بث لي التقرير", "a:repfme", style="success")],
                             [kb.btn("↩️ رجوع للتقرير", "a:repf:run", style="primary")],
                         ]), bot)
            return
        context.user_data.pop("repf_broadcast_text", None)
        context.user_data.pop("repf_png", None)
        sent, errors = 0, []
        for cid in [t for t in targets if t]:
            try:
                if png:
                    await await_bot_photo(bot, cid, png,
                                          caption="📤 التقرير الصادق — الصورة التنفيذية الحقيقية")
                await await_bot_send(bot, cid, text)
                sent += 1
            except Exception as e:
                errors.append(f"{cid}: {str(e)[:120]}")
        await afn.run(audit_log.log, uid,
                      f"interactive_report_broadcast:{f.get('market') or 'ALL'}:{f.get('strategy') or 'ALL'}:"
                      f"{f.get('timeframe') or 'ALL'}:{f.get('days') or 'FULL'}:{sent}")
        owner_note = "📥 نسخة الملك — هذه النسخة نفسها بُثت للقنوات حرفياً"
        owner_errors = []
        try:
            await await_bot_send(bot, config.ADMIN_ID, f"{owner_note}\n──────────────\n{text}")
            if png:
                await await_bot_photo(bot, config.ADMIN_ID, png,
                                      caption="📥 نسخة الملك — الصورة التنفيذية بُثت للقنوات")
        except Exception as e:
            owner_errors.append(str(e)[:120])
        err_txt = ""
        if errors:
            err_txt = "\n".join(f"▎⚠️ {e}" for e in errors[:3])
        own_txt = f"\n▎⚠️ تعذر تسليم نسخة الملك: {owner_errors[0]}" if owner_errors else ""
        await render(
            q,
            f"✅ <b>تم بث التقرير الصادق للقنوات</b>\n"
            f"<blockquote>▎القنوات المستلمة: <b>{sent}</b> · أخطاء: <b>{len(errors)}</b>\n"
            f"▎الصورة التنفيذية: {'أُرسلت مع البث' if png else 'لا بيانات كافية — لم تُرسل صورة'}\n"
            f"▎التصفية: سوق {_lab(f.get('market'))} · استراتيجية {_lab(f.get('strategy'))} · "
            f"إطار {_lab(f.get('timeframe'))} · فترة {_period_lab(f.get('days'))}{own_txt}</blockquote>\n"
            + (err_txt + "\n" if err_txt else "")
            + "<i>وصلتك نسخة الملك من كل ما بُث — البث الحقيقي لا يُخفى عنك.</i>",
            kb.admin_nav([[kb.btn("🧭 التقرير التفاعلي", "a:repfilter", style="primary")]]), bot)
        return

    if arg == "cancel":
        context.user_data.pop("repf_broadcast_text", None)
        context.user_data.pop("repf_png", None)
        await render(q, "❌ <b>أُلغي البث</b> — لم يُرسل التقرير لأحد.",
                     kb.admin_nav([[kb.btn("📊 رجوع للتقرير", "a:repf:run", style="primary")]]), bot)
        return

    # preview: the deliverable lands on the OWNER's chat alone + a confirm card
    png, d = await afn.run(_build_slice_png, f)
    context.user_data["repf_broadcast_text"] = body
    context.user_data["repf_png"] = png
    errors: list[str] = []
    try:
        if png and int(d.get("total") or 0) > 0:
            await await_bot_photo(bot, config.ADMIN_ID, png,
                                  caption="📤 معاينة البث الملكية — الصورة التنفيذية الحقيقية")
    except Exception as e:
        errors.append(f"الصورة: {str(e)[:120]}")
    try:
        await await_bot_send(bot, str(config.ADMIN_ID), body)
    except Exception as e:
        errors.append(f"النص: {str(e)[:120]}")
    err_txt = ""
    if errors:
        err_txt = ("⚠️ <b>تعذر تسليم المعاينة إليك — السبب الحقيقي:</b>\n"
                   "<blockquote>▎" + errors[0] + "</blockquote>\n")
    await render(
        q,
        "📤 <b><u>معاينة البث الملكية</u></b>\n"
        "<blockquote>▎التقرير الصادق الحقيقي كاملاً أُرسل لك الآن في رسالة منفصلة — "
        "هذه النسخة نفسها هي التي ستُبث للقنوات حرفياً (نصاً وصورة)\n"
        "▎التصفية: سوق <b>{}</b> · استراتيجية <b>{}</b> · إطار <b>{}</b> · فترة <b>{}</b></blockquote>\n".format(
            _lab(f.get("market")), _lab(f.get("strategy")),
            _lab(f.get("timeframe")), _period_lab(f.get("days")))
        + err_txt
        + "<i>لا شيء يُبث حتى تؤكد أنت القرار.</i>",
        kb.admin_nav([
            [kb.btn("✅ تأكيد البث للقنوات", "a:repfb:confirm", style="success"),
             kb.btn("❌ إلغاء", "a:repfb:cancel", style="danger")],
        ]), bot)


async def await_bot_send(bot, chat_id: str, text: str):
    """Chunked text delivery to one chat (4096-char Telegram cap, honestly split)."""
    chunk = 3900
    for i in range(0, max(len(text), 1), chunk):
        await bot.send_message(chat_id=int(chat_id), text=text[i:i + chunk],
                               parse_mode="HTML", disable_web_page_preview=True)


async def await_bot_photo(bot, chat_id, png: bytes, caption: str = ""):
    """v8.14.20 — the executive PNG delivery (photo rides with the report)."""
    try:
        cid = int(chat_id)
    except (TypeError, ValueError):
        cid = chat_id
    await bot.send_photo(cid, photo=io.BytesIO(png), caption=(caption or "")[:1024],
                         parse_mode="HTML")


async def rep_filter_clear(q, uid: int, context: ContextTypes.DEFAULT_TYPE, bot):
    context.user_data["rep_filter"] = {"market": None, "strategy": None, "timeframe": None, "days": None}
    f = _get_filter(context.user_data)
    await render(q, "♻️ <b>صُفّرت الفلاتر</b> — كل الأبعاد والفترة: الكل.\n" + _filter_card_text(f),
                 _filter_kb(f), bot)
