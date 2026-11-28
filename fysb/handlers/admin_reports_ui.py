"""v8.14.18 — «التقرير التفاعلي الشامل» (the Comprehensive Interactive Report).

The owner picks a SPECIFIC market, strategy, and/or timeframe — or ALL of any
dimension — and receives the honest closed-ledger truth for exactly that
slice: real counts by status, per-symbol pips with their true USD equivalent
(0.01 lot via usd_move), win/loss by STATUS semantics, hit-rate, average
quality and average planned RR — every number a real SQL aggregate, zero
mixed-unit sums, zero fabrication.

Flow: a:repfilter (control card) → a:repf:m|s|t (pickers) → a:repf:m:<VAL>
      → a:repf:run (the report page) → a:repf:clear (reset).
State lives in context.user_data["rep_filter"] — per-admin, isolated.
"""
from __future__ import annotations

from telegram import Update
from telegram.ext import ContextTypes

from fysb import afn, config, db
from fysb.handlers.common import render
from fysb.services import audit as audit_log
from fysb.ui import keyboards as kb

ALL_LABEL = "الكل"


def _get_filter(ud: dict) -> dict:
    f = ud.get("rep_filter")
    if not isinstance(f, dict):
        f = {"market": None, "strategy": None, "timeframe": None}
        ud["rep_filter"] = f
    return f


def _lab(v: str | None) -> str:
    return ALL_LABEL if not v else str(v)


def _distinct(col: str, limit: int = 18) -> list[str]:
    """Real distinct values from the live signals table (sync, pooled)."""
    col_safe = {"symbol": "symbol", "strategy": "strategy", "timeframe": "timeframe"}.get(col)
    if not col_safe:
        return []
    with db.conn() as c:
        rows = c.execute(
            f"""SELECT DISTINCT {col_safe} v FROM signals
                WHERE {col_safe} IS NOT NULL AND {col_safe}!=''
                ORDER BY {col_safe} LIMIT ?""", (int(limit),)).fetchall()
    return [str(r["v"]) for r in rows]


def _matching_count(f: dict) -> int:
    where, params = ["1=1"], []
    for key, col in (("market", "symbol"), ("strategy", "strategy"), ("timeframe", "timeframe")):
        if f.get(key):
            where.append(f"{col}=?")
            params.append(f[key])
    with db.conn() as c:
        return int(c.execute(f"SELECT COUNT(*) n FROM signals WHERE {' AND '.join(where)}",
                             params).fetchone()["n"])


def _filter_card_text(f: dict) -> str:
    n = _matching_count(f)
    return (
        "🧭 <b><u>التقرير التفاعلي الشامل</u></b>\n"
        "<blockquote>"
        f"▎📈 السوق: <b>{_lab(f.get('market'))}</b>\n"
        f"▎🧠 الاستراتيجية: <b>{_lab(f.get('strategy'))}</b>\n"
        f"▎⏱ الإطار الزمني: <b>{_lab(f.get('timeframe'))}</b>\n"
        f"▎📡 الإشارات المطابقة الآن: <b>{n}</b> (عدد حقيقي من قاعدة البيانات)"
        "</blockquote>\n"
        "<i>اختر بُعداً لتصفية التقرير، أو اتركه «الكل» — ثم ولّد التقرير الصادق.</i>"
    )


def _filter_kb(f: dict):
    return kb.admin_nav([
        [kb.btn(f"📈 السوق: {_lab(f.get('market'))}", "a:repf:m", style="primary"),
         kb.btn(f"🧠 الاستراتيجية: {_lab(f.get('strategy'))}", "a:repf:s", style="primary")],
        [kb.btn(f"⏱ الإطار الزمني: {_lab(f.get('timeframe'))}", "a:repf:t", style="primary")],
        [kb.btn("📊 توليد التقرير الصادق", "a:repf:run", style="success"),
         kb.btn("♻️ تصفير الفلاتر", "a:repf:clear", style="danger")],
    ])


async def rep_filter_home(q, uid: int, context: ContextTypes.DEFAULT_TYPE, bot):
    f = _get_filter(context.user_data)
    await render(q, _filter_card_text(f), _filter_kb(f), bot)


async def rep_filter_pick(q, uid: int, context: ContextTypes.DEFAULT_TYPE, bot, parts: list[str]):
    f = _get_filter(context.user_data)
    kind = parts[0] if parts else ""
    key, col = {"m": ("market", "symbol"), "s": ("strategy", "strategy"),
                "t": ("timeframe", "timeframe")}.get(kind, ("", ""))
    if not key:
        await rep_filter_home(q, uid, context, bot)
        return

    # a value was chosen
    if len(parts) >= 2:
        val = ":".join(parts[1:])  # symbols/strategies never contain ':', but stay safe
        f[key] = None if val == "-" else val[:24]
        await render(q, _filter_card_text(f), _filter_kb(f), bot)
        return

    # open the picker: ALL + the real distinct values from the live table
    values = await afn.run(_distinct, col)
    title = {"m": "📈 اختر السوق", "s": "🧠 اختر الاستراتيجية", "t": "⏱ اختر الإطار الزمني"}[kind]
    data_prefix = {"m": "a:repf:m:", "s": "a:repf:s:", "t": "a:repf:t:"}[kind]
    rows = [[kb.btn(f"🌐 {ALL_LABEL}", data_prefix + "-", style="success")]]
    for v in values:
        rows.append([kb.btn(str(v), data_prefix + str(v), style="primary")])
    if not values:
        rows.append([kb.btn("— لا قيم مسجلة بعد —", "a:repf:none", style="danger")])
    rows.append([kb.btn("↩️ بطاقة التصفية", "a:repfilter", style="primary")])
    await render(q, f"{title}\n<i>القيم من السجل الحقيقي للإشارات المنشورة.</i>",
                 kb.admin_nav(rows), bot)


async def rep_filter_run(q, uid: int, context: ContextTypes.DEFAULT_TYPE, bot):
    f = _get_filter(context.user_data)
    from fysb.services import reports
    body = await afn.run(reports.filtered_signals_report,
                         f.get("market"), f.get("strategy"), f.get("timeframe"))
    await afn.run(audit_log.log, uid,
                  f"interactive_report:{f.get('market') or 'ALL'}:{f.get('strategy') or 'ALL'}:{f.get('timeframe') or 'ALL'}")
    rows = [
        [kb.btn("📋 تفاصيل الصفقات المغلقة", "a:repfd", style="primary")],
        [kb.btn("📤 بث هذا التقرير للقنوات", "a:repfb:preview", style="success")],
        [kb.btn("🔄 تحديث التقرير", "a:repf:run", style="primary"),
         kb.btn("🧭 تعديل التصفية", "a:repfilter", style="primary")],
        [kb.btn("♻️ تصفير الفلاتر", "a:repf:clear", style="danger")],
    ]
    await render(q, body, kb.admin_nav(rows), bot)


async def rep_filter_trades(q, uid: int, context: ContextTypes.DEFAULT_TYPE, bot):
    """v8.14.19 — the A-to-Z completeness: every closed trade of the slice,
    truthfully settled (the 50-pip decree included), newest first."""
    f = _get_filter(context.user_data)
    from fysb.services import reports
    body = await afn.run(reports.filtered_trades_detail,
                         f.get("market"), f.get("strategy"), f.get("timeframe"))
    rows = [
        [kb.btn("📊 رجوع للتقرير", "a:repf:run", style="success")],
        [kb.btn("🧭 تعديل التصفية", "a:repfilter", style="primary")],
    ]
    await render(q, body, kb.admin_nav(rows), bot)


def _broadcast_targets() -> list[str]:
    """Sync helper — enabled broadcast channels (db access stays out of async bodies)."""
    with db.conn() as c:
        return [str(r["chat_id"]).strip()
                for r in c.execute("SELECT chat_id FROM broadcast_channels WHERE enabled=1").fetchall()]


async def rep_filter_broadcast(q, uid: int, context: ContextTypes.DEFAULT_TYPE, bot, arg: str):
    """v8.14.19 — broadcast the EXACT filtered report, preview-first: the
    deliverable lands on the owner's chat alone, then he confirms. Nothing
    leaves the bot until his explicit confirmation."""
    f = _get_filter(context.user_data)
    from fysb.services import reports
    body = await afn.run(reports.filtered_signals_report,
                         f.get("market"), f.get("strategy"), f.get("timeframe"))
    if arg == "confirm":
        text = str(context.user_data.get("repf_broadcast_text") or "")
        context.user_data.pop("repf_broadcast_text", None)
        if not text:
            await render(q, "⛔ لا يوجد تقرير جاهز للبث — ولّد التقرير أولاً.",
                         kb.admin_nav(), bot)
            return
        targets = await afn.run(_broadcast_targets)
        sent, errors = 0, 0
        for cid in [t for t in targets if t]:
            try:
                await await_bot_send(bot, cid, text)
                sent += 1
            except Exception:
                errors += 1
        await afn.run(audit_log.log, uid,
                      f"interactive_report_broadcast:{f.get('market') or 'ALL'}:{f.get('strategy') or 'ALL'}:{f.get('timeframe') or 'ALL'}:{sent}")
        await render(
            q,
            f"✅ <b>تم بث التقرير الصادق للقنوات</b>\n"
            f"<blockquote>▎القنوات المستلمة: <b>{sent}</b> · أخطاء: <b>{errors}</b>\n"
            f"▎التصفية: سوق {_lab(f.get('market'))} · استراتيجية {_lab(f.get('strategy'))} · إطار {_lab(f.get('timeframe'))}</blockquote>",
            kb.admin_nav([[kb.btn("🧭 التقرير التفاعلي", "a:repfilter", style="primary")]]), bot)
        return

    if arg == "cancel":
        context.user_data.pop("repf_broadcast_text", None)
        await render(q, "❌ <b>أُلغي البث</b> — لم يُرسل التقرير لأحد.",
                     kb.admin_nav([[kb.btn("📊 رجوع للتقرير", "a:repf:run", style="primary")]]), bot)
        return

    # preview: the deliverable lands on the OWNER's chat alone + a confirm card
    context.user_data["repf_broadcast_text"] = body
    try:
        await await_bot_send(bot, str(config.ADMIN_ID), body)
    except Exception:
        pass
    await render(
        q,
        "📤 <b><u>معاينة البث الملكية</u></b>\n"
        "<blockquote>▎التقرير الصادق الحقيقي كاملاً أُرسل لك الآن في رسالة منفصلة — "
        "هذه النسخة نفسها هي التي ستُبث للقنوات حرفياً\n"
        f"▎التصفية: سوق <b>{_lab(f.get('market'))}</b> · استراتيجية <b>{_lab(f.get('strategy'))}</b> · "
        f"إطار <b>{_lab(f.get('timeframe'))}</b></blockquote>\n"
        "<i>لا شيء يُبث حتى تؤكد أنت القرار:</i>",
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


async def rep_filter_clear(q, uid: int, context: ContextTypes.DEFAULT_TYPE, bot):
    context.user_data["rep_filter"] = {"market": None, "strategy": None, "timeframe": None}
    f = _get_filter(context.user_data)
    await render(q, "♻️ <b>صُفّرت الفلاتر</b> — كل الأبعاد: الكل.\n" + _filter_card_text(f),
                 _filter_kb(f), bot)
