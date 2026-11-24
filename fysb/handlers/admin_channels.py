"""Admin «القنوات والمجموعات» — auto-analysis & auto-signal broadcaster.

Capabilities (all real, all server-side):
  * register a channel/group by @username or -100... id (the bot must be an
    admin there; ownership is verified via get_chat before saving);
  * per-channel switches: auto-analysis, auto-signals, enabled;
  * per-channel scope: markets, timeframes, strategies (or ALL);
  * schedule: interval minutes, Mecca time (UTC+3 fixed — no DST), with quiet
    hours so followers never get woken at 3 AM;
  * "post now" buttons for a live analysis or a live signal;
  * the background loop (channels_loop) delivers the posts — every post
    carries the bot identity @ForexYemeni1_BOT.

Nothing here reveals internal provider names or machine codes.
"""
import asyncio
import json

from telegram.ext import ContextTypes

from fysb import afn, config, db, instruments
from fysb.config import log
from fysb.handlers.common import render
from fysb.handlers.user_pages import _safe_int
from fysb.services import audit as audit_log
from fysb.services.analytics import run_analysis
from fysb.theme import ICON, esc, strategy_name, tf_name
from fysb.ui import htmlguard
from fysb.ui import keyboards as kb

MECCA_OFFSET_HOURS = 3  # Asia/Riyadh = UTC+3 year-round (no DST)

MARKET_TF_DEFAULTS = ["M15", "H1"]
INTERVAL_CHOICES = [30, 60, 120, 240, 360, 720, 1440]


def mecca_now():
    from datetime import datetime, timedelta, timezone
    return datetime.now(timezone(timedelta(hours=MECCA_OFFSET_HOURS)))


def in_quiet_hours(now=None) -> bool:
    """Quiet window (default 01:00-07:59 Mecca) — no automated posts."""
    now = now or mecca_now()
    lo, hi = 1, 7
    return lo <= now.hour <= hi


def list_channels() -> list[dict]:
    with db.conn() as c:
        return [dict(r) for r in c.execute("SELECT * FROM broadcast_channels ORDER BY id DESC").fetchall()]


def get_channel(cid: int):
    with db.conn() as c:
        r = c.execute("SELECT * FROM broadcast_channels WHERE id=?", (cid,)).fetchone()
    return dict(r) if r else None


def add_channel(chat_id: str, title: str, kind: str) -> int:
    with db.conn() as c:
        cur = c.execute("""INSERT INTO broadcast_channels(chat_id,title,kind,enabled,auto_analysis,auto_signals,
                           markets,timeframes,strategies,interval_minutes,last_sent,created_at,updated_at)
                           VALUES(?,?,?,1,1,1,?,?,?,60,'',?,?)""",
                        (chat_id, title, kind, "XAUUSD", "M15,H1", "ALL", config.now(), config.now()))
        return cur.lastrowid


def update_channel(cid: int, **fields) -> None:
    allowed = {"enabled", "auto_analysis", "auto_signals", "markets", "timeframes",
               "strategies", "interval_minutes", "last_sent", "title"}
    sets = ", ".join(f"{k}=?" for k in fields if k in allowed)
    vals = [v for k, v in fields.items() if k in allowed]
    if not sets:
        return
    with db.conn() as c:
        c.execute(f"UPDATE broadcast_channels SET {sets}, updated_at=? WHERE id=?", (*vals, config.now(), cid))


def delete_channel(cid: int) -> None:
    with db.conn() as c:
        c.execute("DELETE FROM broadcast_channels WHERE id=?", (cid,))


def due_channels() -> list[dict]:
    """Channels whose interval elapsed (Mecca time) and are outside quiet hours."""
    if in_quiet_hours():
        return []
    out = []
    for ch in list_channels():
        if not ch["enabled"] or not (ch["auto_analysis"] or ch["auto_signals"]):
            continue
        interval = max(15, int(ch["interval_minutes"] or 60))
        last = ch["last_sent"] or ""
        if not last:
            out.append(ch)
            continue
        try:
            from datetime import datetime
            last_dt = datetime.fromisoformat(last)
            elapsed = (mecca_now() - last_dt).total_seconds() / 60.0
            if elapsed >= interval:
                out.append(ch)
        except Exception:
            out.append(ch)
    return out


# ============================================================================
# Admin UI
# ============================================================================
async def admin_channels(q, uid, context, bot):
    chans = await afn.run(list_channels)
    body = [f"📺 <b><u>إدارة القنوات والمجموعات</u></b>",
            "▎اربط قناتك أو مجموعتك ليصلك التحليل والإشارات تلقائياً بتوقيت مكة المكرمة.",
            "▎الشرط الوحيد: أضف البوت مشرفاً (Admin) في القناة/المجموعة أولاً.", ""]
    rows = []
    for ch in chans:
        state = "🟢" if ch["enabled"] else "⚪"
        mode = []
        if ch["auto_analysis"]:
            mode.append("تحليل")
        if ch["auto_signals"]:
            mode.append("إشارات")
        body.append(f"▎{state} <b>#{ch['id']} {esc(ch['title'] or ch['chat_id'])}</b> · "
                    f"{' + '.join(mode) or 'متوقف'} · كل {ch['interval_minutes']}د · "
                    f"{esc('، '.join(ch['markets'].split(',')))} ({tf_name(ch['timeframes'].split(',')[0])})")
        rows.append([kb.btn(f"⚙️ إعداد #{ch['id']}", f"a:chanview:{ch['id']}")])
    if not chans:
        body.append("▎لا توجد قنوات مرتبطة بعد.")
    rows.append([kb.btn("➕ ربط قناة / مجموعة", "a:chanadd")])
    await render(q, "\n".join(body), kb.admin_nav(rows), bot)


async def admin_chan_add(q, uid, context, bot):
    context.user_data["awaiting_chan_add"] = True
    await render(q,
                 "📺 <b><u>ربط قناة أو مجموعة</u></b>\n"
                 "<blockquote>"
                 "▎1️⃣ أضف البوت <b>مشرفاً</b> في القناة/المجموعة.\n"
                 "▎2️⃣ أرسل هنا <b>@اسم_القناة</b> أو <b>الرابط</b> (https://t.me/...) أو معرف المجموعة الرقمي (<code>-100...</code>).\n"
                 "▎3️⃣ سأتحقق فعلياً أن البوت موجود ويستطيع النشر، ثم أفتح لك الإعدادات.\n"
                 "</blockquote>",
                 kb.admin_nav([[kb.btn("🗑 إلغاء", "a:chancancel")]]), bot)


async def admin_chan_cancel(q, uid, context, bot):
    context.user_data.pop("awaiting_chan_add", None)
    await render(q, "🗑 أُلغي ربط القناة.", kb.admin_nav([[kb.btn("📺 القنوات", "a:channels")]]), bot)


async def handle_add_input(update, context, uid: int, text: str) -> None:
    """Text step of the channel-add flow (verify + save)."""
    ud = context.user_data
    ud.pop("awaiting_chan_add", None)
    raw = (text or "").strip()
    chat_ref = raw
    if "t.me/" in raw:
        chat_ref = "@" + raw.split("t.me/", 1)[1].lstrip("/").split("?")[0].strip()
    if not (chat_ref.startswith("@") or chat_ref.lstrip("-").isdigit()):
        await update.effective_message.reply_text(
            "❌ صيغة غير مفهومة. أرسل @اسم_القناة أو رابط https://t.me/... أو المعرف الرقمي -100...")
        return
    try:
        chat = await context.bot.get_chat(chat_ref)
        member = await context.bot.get_chat_member(chat.id, (await context.bot.get_me()).id)
        if member.status not in ("administrator", "creator"):
            raise RuntimeError("status")
    except Exception as e:
        await update.effective_message.reply_text(
            "⛔ تعذر التحقق من القناة:\n"
            "<blockquote>▎تأكد أن البوت <b>مشرف</b> فيها مع صلاحية نشر الرسائل، وأن المعرف صحيح.\n"
            "▎للمجموعات الخاصة أرسل المعرف الرقمي.</blockquote>\n"
            f"<i>({esc(type(e).__name__)})</i>", parse_mode="HTML")
        return
    kind = "channel" if getattr(chat, "type", "") in ("channel",) else "group"
    title = getattr(chat, "title", None) or chat_ref
    cid = await afn.run(add_channel, str(chat.id), title, kind)
    await afn.run(audit_log.log, uid, f"channel_linked:{cid}:{kind}")
    await update.effective_message.reply_text(
        f"✅ <b>تم ربط {esc(title)}</b> بنجاح وتحقق فعلي من صلاحية النشر.\n"
        "⚙️ افتح إعداداتها لاختيار الأسواق والأطر والجدولة:",
        parse_mode="HTML",
        reply_markup=kb.admin_nav([[kb.btn(f"⚙️ إعداد #{cid}", f"a:chanview:{cid}")],
                                   [kb.btn("📺 كل القنوات", "a:channels")]]))


async def admin_chan_view(q, uid, context, bot, cid: int):
    ch = await afn.run(get_channel, cid)
    if not ch:
        await render(q, "القناة غير موجودة.", kb.admin_nav([[kb.btn("📺 القنوات", "a:channels")]]), bot)
        return
    markets = [m.strip() for m in (ch["markets"] or "").split(",") if m.strip()]
    tfs = [t.strip() for t in (ch["timeframes"] or "").split(",") if t.strip()]
    strats = [s.strip() for s in (ch["strategies"] or "").split(",") if s.strip()]
    strat_label = "جميع الاستراتيجيات" if "ALL" in [s.upper() for s in strats] else \
        "، ".join(strategy_name(s) for s in strats)
    body = [
        f"⚙️ <b><u>إعداد القناة #{ch['id']}</u></b>",
        f"▎📌 الاسم: <b>{esc(ch['title'] or ch['chat_id'])}</b> · النوع: {'قناة' if ch['kind'] == 'channel' else 'مجموعة'}",
        f"▎{'🟢 مفعلة للنشر التلقائي' if ch['enabled'] else '⚪ متوقفة'}",
        "",
        "<blockquote>"
        f"▎🔬 التحليل التلقائي: <b>{'مفعل' if ch['auto_analysis'] else 'متوقف'}</b>\n"
        f"▎📡 الإشارات التلقائية: <b>{'مفعلة' if ch['auto_signals'] else 'متوقفة'}</b>\n"
        f"▎🌐 الأسواق: <b>{esc('، '.join(markets))}</b>\n"
        f"▎⏱ الأطر: <b>{esc('، '.join(tf_name(t) for t in tfs))}</b>\n"
        f"▎🧠 الاستراتيجيات: <b>{esc(strat_label)}</b>\n"
        f"▎⏰ الجدولة: كل <b>{ch['interval_minutes']} دقيقة</b> (توقيت مكة · سكون ليلي 01-07)\n"
        f"▎🕐 آخر إرسال: {esc(ch['last_sent'][:19].replace('T', ' ') or '—')} مكة"
        "</blockquote>",
    ]
    rows = [
        [kb.btn(f"{'🟠 ⏹ إيقاف القناة' if ch['enabled'] else '🟢 ▶️ تفعيل القناة'}", f"a:chantoggle:{cid}")],
        [kb.btn(f"{'🟠 🔬 إيقاف التحليل' if ch['auto_analysis'] else '🟢 🔬 تفعيل التحليل'}", f"a:chanmode:{cid}:analysis"),
         kb.btn(f"{'🟠 📡 إيقاف الإشارات' if ch['auto_signals'] else '🟢 📡 تفعيل الإشارات'}", f"a:chanmode:{cid}:signals")],
        [kb.btn("🟣 🌐 الأسواق", f"a:chanfeat:{cid}:markets"), kb.btn("🟣 ⏱ الأطر", f"a:chanfeat:{cid}:timeframes"),
         kb.btn("🟣 🧠 الاستراتيجيات", f"a:chanfeat:{cid}:strategies")],
        [kb.btn("🔵 ⏲ الجدولة", f"a:chaninterval:{cid}")],
        [kb.btn("🟢 🚀 تحليل الآن", f"a:chanpost:{cid}:analysis"), kb.btn("🟢 📡 إشارة الآن", f"a:chanpost:{cid}:signal")],
        [kb.btn("🔴 إزالة الربط", f"a:chandel:{cid}"), kb.btn("🔵 كل القنوات", "a:channels")],
    ]
    await render(q, "\n".join(body), kb.admin_nav(rows), bot)


async def admin_chan_toggle(q, uid, context, bot, cid: int):
    ch = await afn.run(get_channel, cid)
    if not ch:
        return await admin_channels(q, uid, context, bot)
    await afn.run(update_channel, cid, enabled=0 if ch["enabled"] else 1)
    await afn.run(audit_log.log, uid, f"channel_toggle:{cid}")
    await admin_chan_view(q, uid, context, bot, cid)


async def admin_chan_mode(q, uid, context, bot, cid: int, mode: str):
    ch = await afn.run(get_channel, cid)
    if not ch:
        return await admin_channels(q, uid, context, bot)
    if mode == "analysis":
        await afn.run(update_channel, cid, auto_analysis=0 if ch["auto_analysis"] else 1)
    elif mode == "signals":
        await afn.run(update_channel, cid, auto_signals=0 if ch["auto_signals"] else 1)
    await afn.run(audit_log.log, uid, f"channel_mode:{cid}:{mode}")
    await admin_chan_view(q, uid, context, bot, cid)


async def admin_chan_interval(q, uid, context, bot, cid: int):
    rows = [[kb.btn(f"⏲ {n} دقيقة", f"a:chansetinterval:{cid}:{n}")] for n in INTERVAL_CHOICES]
    await render(q, "⏲ <b>اختر فاصل الجدولة</b> (توقيت مكة — مع سكون ليلي تلقائي):",
                 kb.admin_nav(rows), bot)


async def admin_chan_set_interval(q, uid, context, bot, cid: int, minutes: int):
    await afn.run(update_channel, cid, interval_minutes=max(15, min(1440, minutes)))
    await afn.run(audit_log.log, uid, f"channel_interval:{cid}:{minutes}")
    await admin_chan_view(q, uid, context, bot, cid)


async def admin_chan_feat(q, uid, context, bot, cid: int, kind: str):
    ch = await afn.run(get_channel, cid)
    if not ch:
        return
    current = [x.strip() for x in (ch.get(kind) or "").split(",") if x.strip()]
    if kind == "markets":
        items = await afn.run(_enabled_verified_market_symbols)
        title = "الأسواق"
    elif kind == "timeframes":
        items = ["M1", "M5", "M15", "M30", "H1", "H4", "D1"]
        title = "الأطر الزمنية"
    else:
        from fysb.engines.strategies import VERIFIABLE
        items = list(VERIFIABLE) + ["ALL"]
        title = "الاستراتيجيات"
    rows = [[kb.btn(("✅ " if (it.upper() in [x.upper() for x in current]) else "▫️ ") +
                    (strategy_name(it) if kind == "strategies" else it),
                    f"a:chanfeatset:{cid}:{kind}:{it}") for it in items[i:i + 3]]
            for i in range(0, len(items), 3)]
    rows.append([kb.btn("↩️ الإعداد", f"a:chanview:{cid}")])
    await render(q, f"⚙️ <b>تعديل {title} للقناة #{cid}</b>\nاضغط للتبديل (الاستراتيجيات تتحول لجميعها عند اختيار ALL):",
                 kb.admin_nav(rows), bot)


async def admin_chan_feat_set(q, uid, context, bot, cid: int, kind: str, item: str):
    ch = await afn.run(get_channel, cid)
    if not ch:
        return
    current = [x.strip() for x in (ch.get(kind) or "").split(",") if x.strip()]
    if item.upper() in [x.upper() for x in current]:
        current = [x for x in current if x.upper() != item.upper()]
    else:
        current.append(item)
    await afn.run(update_channel, cid, **{kind: ",".join(current)})
    await afn.run(audit_log.log, uid, f"channel_feat:{cid}:{kind}:{item}")
    await admin_chan_feat(q, uid, context, bot, cid, kind)


async def admin_chan_delete(q, uid, context, bot, cid: int):
    await afn.run(delete_channel, cid)
    await afn.run(audit_log.log, uid, f"channel_deleted:{cid}")
    await admin_channels(q, uid, context, bot)


# ============================================================================
# Delivery — publish-reliability layer (multi-market fix, 2026-11-22)
#
# ROOT CAUSE of «⛔ فشل النشر — تأكد أن البوت مشرف بصلاحية النشر» with 2+
# markets: the old code joined 2-3 analysis cards and hard-cut text[:3900],
# which can slice a tag/entity in half — Telegram rejects the WHOLE post with
# «Can't parse entities», and the catch-all blamed the bot's permissions.
# The rebuild: one royal message PER MARKET (each guard-truncated so markup
# can never break), a pre-flight permission probe, precise error diagnosis,
# and a 3-level send fallback (HTML → repaired → plain).
# ============================================================================

def _chat_ref(ch: dict):
    return int(ch["chat_id"]) if str(ch["chat_id"]).lstrip("-").isdigit() else ch["chat_id"]


async def _verify_posting_rights(bot, ch: dict) -> str | None:
    """Pre-flight probe: None = can post, else a precise Arabic diagnosis."""
    try:
        me = await bot.get_me()
        member = await bot.get_chat_member(_chat_ref(ch), me.id)
        status = getattr(member, "status", "")
        if status == "creator":
            return None
        if status != "administrator":
            return "البوت ليس مشرفاً في القناة — أضفه مشرفاً بصلاحية «نشر الرسائل» ثم أعد المحاولة."
        if ch["kind"] == "channel" and getattr(member, "can_post_messages", None) is False:
            return "البوت مشرف لكن صلاحية «نشر الرسائل» غير مفعلة له — فعّلها من إعدادات القناة ثم أعد المحاولة."
        if ch["kind"] == "group" and getattr(member, "can_send_messages", None) is False:
            return "البوت مشرف لكن صلاحية «إرسال الرسائل» غير مفعلة له — فعّلها من إعدادات المجموعة ثم أعد المحاولة."
        return None
    except Exception as e:
        s = str(e).lower()
        if "not found" in s:
            return "القناة غير موجودة أو المعرف خاطئ — احذف الربط وأعد ربط القناة من «القنوات والمجموعات»."
        if "kicked" in s or "deactivated" in s:
            return "البوت طُرد من القناة — أعد إضافته مشرفاً بصلاحية النشر ثم أعد المحاولة."
        return None  # unverifiable right now — the actual send attempt decides


def _diagnose_send_error(e: Exception) -> str:
    s = str(e).lower()
    if "kicked" in s or "deactivated" in s:
        return "⛔ فشل النشر — البوت طُرد من القناة. أعد إضافته مشرفاً بصلاحية «نشر الرسائل» ثم أعد المحاولة."
    if "not found" in s:
        return "⛔ فشل النشر — القناة غير موجودة أو البوت ليس عضواً فيها. احذف الربط وأعد ربط القناة."
    if "parse entities" in s or "unsupported start tag" in s or "unclosed" in s or "unexpected end tag" in s:
        return "⛔ فشل النشر — خطأ تنسيق مؤقت في الرسالة. الحماية التلقائية أصلحت التنسيق — أعد المحاولة."
    if "not enough rights" in s or "administrator" in s or "need admin" in s:
        return "⛔ فشل النشر — البوت مشرف لكن بدون صلاحية «نشر الرسائل». فعّل الصلاحية ثم أعد المحاولة."
    if "too long" in s:
        return "⛔ فشل النشر — الرسالة أطول من حد تلغرام. قلّل عدد الأسواق المختارة لهذه القناة."
    return f"⛔ فشل النشر — ({esc(type(e).__name__)}). تأكد أن البوت مشرفاً بصلاحية «نشر الرسائل» ثم أعد المحاولة."


async def _send_html(bot, chat_ref, text: str) -> dict:
    """3-level guarded send: HTML → guard-repaired → plain. Never corrupts.
    v6.10.0: returns the sent Message object so the caller can pin the
    delivery reference (chat_id + message_id) for LIVE channel tracking."""
    variants = [("html", text, "HTML"),
                ("repaired", htmlguard.truncate_safe(text, 3900), "HTML"),
                ("plain", htmlguard.plain_fallback(text), None)]
    last_err: Exception | None = None
    for mode, body, parse_mode in variants:
        try:
            if parse_mode:
                msg = await bot.send_message(chat_ref, body, parse_mode=parse_mode,
                                             disable_web_page_preview=True)
            else:
                msg = await bot.send_message(chat_ref, body, disable_web_page_preview=True)
            return {"ok": True, "mode": mode, "message": msg}
        except Exception as e:
            last_err = e
            s = str(e).lower()
            parse_issue = ("parse entities" in s or "unsupported start tag" in s
                           or "unclosed" in s or "unexpected end tag" in s)
            if mode != "plain" and parse_issue:
                continue  # escalate to the next guard level
            return {"ok": False, "error": _diagnose_send_error(e)}
    return {"ok": False, "error": _diagnose_send_error(last_err or RuntimeError("send failed"))}


def _channel_delivery_uid(ch: dict) -> int:
    """Unique negative user_id key per channel so UNIQUE(user_id, signal_id)
    supports UNLIMITED linked channels simultaneously (tracker treats user_id<=0
    as channel posts and routes edits/alerts to chat_id). Synthetic test dicts
    without 'id' gracefully fall back to 0."""
    try:
        cid = int(ch.get("id") or 0)
    except (TypeError, ValueError):
        cid = 0
    return -cid if cid > 0 else 0


def pin_channel_delivery(ch: dict, signal_id: int, msg) -> None:
    """v6.14.0 — record the channel post as a LIVE delivery (user_id=-channel.id,
    chat_id=channel, message_id=post). Multi-channel safe: every linked channel
    gets its own unique negative user_id so tracker edits ALL channels live."""
    if msg is None:
        return
    message_id = getattr(msg, "message_id", 0)
    if not message_id:
        return
    cuid = _channel_delivery_uid(ch)
    with db.conn() as c:
        c.execute("""INSERT OR IGNORE INTO signal_deliveries(user_id,signal_id,delivered_at,chat_id,message_id)
                     VALUES(?,?,?,?,?)""",
                  (cuid, signal_id, config.now(), str(ch["chat_id"]), int(message_id)))


def _channel_already_pinned(ch: dict, signal_id: int) -> bool:
    cuid = _channel_delivery_uid(ch)
    with db.conn() as c:
        r = c.execute(
            "SELECT 1 FROM signal_deliveries WHERE signal_id=? AND (user_id=? OR (user_id<=0 AND chat_id=?)) LIMIT 1",
            (signal_id, cuid, str(ch["chat_id"])),
        ).fetchone()
    return r is not None


def _channel_matches_signal(ch: dict, sig: dict) -> bool:
    """Check whether an enabled auto_signals channel accepts this signal's market/TF/strategy."""
    from fysb.engines.signal_engine import _norm_sym
    sig_sym = _norm_sym(sig.get("symbol", ""))
    sig_tf = (sig.get("timeframe") or "").strip().upper()
    sig_strat = (sig.get("strategy") or "").strip().upper()

    c_mkts = {_norm_sym(x) for x in (ch.get("markets") or "").split(",") if x.strip()}
    if c_mkts and "ALL" not in c_mkts and sig_sym not in c_mkts:
        return False
    c_tfs = {x.strip().upper() for x in (ch.get("timeframes") or "").split(",") if x.strip()}
    if c_tfs and "ALL" not in c_tfs and sig_tf not in c_tfs:
        return False
    c_strats = {x.strip().upper() for x in (ch.get("strategies") or "").split(",") if x.strip()}
    if c_strats and "ALL" not in c_strats and sig_strat not in c_strats:
        return False
    return True


async def emit_to_channels(sig: dict, bot) -> dict:
    """Broadcast a generated signal to all matching enabled broadcast_channels.

    Skips any channel that already received this signal_id, pins each posted
    message via pin_channel_delivery (multi-channel safe), and updates last_sent.
    """
    from fysb.ui.cards import render_signal_card
    from fysb.engines.entry_state import birth_pulse_line

    stats = {"channels_sent": 0, "channels_failed": 0}
    channels = await afn.run(list_channels)
    if not channels:
        return stats
    row = await afn.run(_market_row, sig["symbol"])
    card = render_signal_card(dict(sig), user_view=True, row=row)
    pulse = birth_pulse_line(dict(sig), row)
    if pulse:
        card += "\n\n<blockquote>" + pulse + "</blockquote>"
    text = card + f"\n\n📡 <b>{config.BOT_SIGNATURE}</b>"

    for ch in channels:
        if not ch.get("enabled") or not ch.get("auto_signals"):
            continue
        if not _channel_matches_signal(ch, sig):
            continue
        if await afn.run(_channel_already_pinned, ch, sig["id"]):
            continue
        send = await _send_html(bot, _chat_ref(ch), text)
        if send.get("ok"):
            await afn.run(pin_channel_delivery, ch, sig["id"], send.get("message"))
            await afn.run(update_channel, ch["id"], last_sent=mecca_now().isoformat())
            stats["channels_sent"] += 1
        else:
            stats["channels_failed"] += 1
    return stats


async def post_analysis(cid: int, bot, ch: dict | None = None) -> dict:
    """Compose + send ONE luxury analysis message PER MARKET (never merged)."""
    ch = ch or await afn.run(get_channel, cid)
    if not ch:
        return {"ok": False, "error": "القناة غير موجودة"}
    markets = [m.strip() for m in (ch["markets"] or "XAUUSD").split(",") if m.strip()]
    tfs = [t.strip() for t in (ch["timeframes"] or "M15").split(",") if t.strip()]
    tf = tfs[0] if tfs else "M15"
    strat = "ALL" if "ALL" in [s.upper() for s in (ch["strategies"] or "").split(",")] else (ch["strategies"] or "ICT").split(",")[0]

    rights = await _verify_posting_rights(bot, ch)
    if rights:
        return {"ok": False, "error": "⛔ فشل النشر — " + rights}

    chat_ref = _chat_ref(ch)
    posted: list[str] = []
    failed: list[str] = []
    for sym in markets[:3]:
        res = await afn.run(run_analysis, 0, sym, tf, "", "", strat, enforce_plan=False)
        if not res.get("ok"):
            failed.append(f"{sym}: {res.get('error', 'تعذر التحليل')}")
            continue
        an = res["analysis"]
        from fysb.ui.cards import render_analysis_card
        row = await afn.run(_market_row, sym)
        card = render_analysis_card(an, market_row=row)
        text = (f"👑 <b>{config.BRAND}</b> — التحليل الدوري · <b>{esc(sym)}</b> ({tf_name(tf)})\n\n"
                + card
                + f"\n\n📡 <b>{config.BOT_SIGNATURE}</b>")
        send = await _send_html(bot, chat_ref, text)
        if send.get("ok"):
            posted.append(sym)
        else:
            failed.append(f"{sym}: {send.get('error', 'فشل الإرسال')}")
    if not posted:
        return {"ok": False, "error": (failed[0] if failed else "تعذر توليد أي تحليل قابل للنشر الآن")}
    await afn.run(update_channel, cid, last_sent=mecca_now().isoformat())
    out = {"ok": True, "posted": posted}
    if failed:
        out["partial"] = failed
    return out


async def post_signal(cid: int, bot, ch: dict | None = None) -> dict:
    """Run the real engine once for the channel's markets and post the signal."""
    ch = ch or await afn.run(get_channel, cid)
    if not ch:
        return {"ok": False, "error": "القناة غير موجودة"}
    from fysb.engines.signal_engine import generate_signal

    rights = await _verify_posting_rights(bot, ch)
    if rights:
        return {"ok": False, "error": "⛔ فشل النشر — " + rights}

    markets = [m.strip() for m in (ch["markets"] or "XAUUSD").split(",") if m.strip()]
    tfs = [t.strip() for t in (ch["timeframes"] or "M15").split(",") if t.strip()]
    for sym in markets[:3]:
        for tf in tfs[:2]:
            try:
                res = await asyncio.to_thread(generate_signal, sym, tf)
            except Exception:
                continue
            if res.get("status") == "SIGNAL_CREATED":
                sig = await afn.run(_signal_row_by_id, res["signal_id"])
                if sig:
                    from fysb.ui.cards import render_signal_card
                    from fysb.engines.signal_engine import emit_to_users, opposite_close_advisory
                    from fysb.handlers.inputs import bot_sender_from
                    try:
                        await opposite_close_advisory(dict(sig), bot_sender_from(bot))
                    except Exception:
                        pass
                    row = await afn.run(_market_row, sym)
                    text = render_signal_card(dict(sig), user_view=True, row=row) + f"\n\n📡 <b>{config.BOT_SIGNATURE}</b>"
                    send = await _send_html(bot, _chat_ref(ch), text)
                    if send.get("ok"):
                        # Pin this channel post first (multi-channel safe), then
                        # unify delivery so eligible users + any other matching
                        # channels receive the same real signal too.
                        await afn.run(pin_channel_delivery, ch, res["signal_id"], send.get("message"))
                        await afn.run(update_channel, cid, last_sent=mecca_now().isoformat())
                        try:
                            await emit_to_users(dict(sig), bot_sender_from(bot), bot=bot, include_channels=True)
                        except Exception:
                            pass
                        return {"ok": True}
                    return send  # precise diagnosis from the guarded sender
                break
    return {"ok": False, "error": "لا توجد إشارة مؤهلة الآن — ظروف السوق لم تحقق الاستراتيجية (لا إشارات مصطنعة)"}


async def admin_chan_post(q, uid, context, bot, cid: int, kind: str):
    await render(q, "🚀 جارٍ التوليد على البيانات اللحظية...", None)
    res = await (post_analysis(cid, context.bot) if kind == "analysis" else post_signal(cid, context.bot))
    if res.get("ok") and not res.get("partial"):
        await afn.run(audit_log.log, uid, f"channel_post:{cid}:{kind}:ok")
        posted = res.get("posted") or []
        extra = ("\n▎🌐 الأسواق المنشورة: <b>" + esc("، ".join(posted)) + "</b>") if len(posted) > 1 else ""
        await render(q, "✅ <b>نُشر بنجاح في القناة.</b>" + extra,
                     kb.admin_nav([[kb.btn(f"⚙️ إعداد #{cid}", f"a:chanview:{cid}")]]), bot)
    elif res.get("ok"):
        await afn.run(audit_log.log, uid, f"channel_post:{cid}:{kind}:partial")
        lines = "\n".join("▎⚠️ " + esc(f) for f in res.get("partial", []))
        await render(q, "⚠️ <b>نشر جزئي — بعض الأسواق نُشرت وبعضها فشل:</b>\n" + lines,
                     kb.admin_nav([[kb.btn(f"⚙️ إعداد #{cid}", f"a:chanview:{cid}")]]), bot)
    else:
        await afn.run(audit_log.log, uid, f"channel_post:{cid}:{kind}:fail")
        await render(q, f"⛔ {esc(res.get('error', 'تعذر النشر'))}",
                     kb.admin_nav([[kb.btn(f"⚙️ إعداد #{cid}", f"a:chanview:{cid}")]]), bot)


async def channels_loop(app):
    """Scheduled auto-posts (analysis + signals) per channel, Mecca time."""
    while True:
        try:
            for ch in await afn.run(due_channels):
                try:
                    if ch["auto_analysis"]:
                        await post_analysis(ch["id"], app.bot, ch)
                    if ch["auto_signals"]:
                        await post_signal(ch["id"], app.bot, ch)
                except Exception:
                    log.exception("channel auto-post failed cid=%s", ch["id"])
        except asyncio.CancelledError:
            raise
        except Exception:
            log.exception("channels loop iteration failed")
        await asyncio.sleep(120)


# --- v6.11.0 thread-offload helpers ---
def _enabled_verified_market_symbols() -> list[str]:
    """(v6.11.0 thread-offload helper) live market choices for channel scope."""
    with db.conn() as c:
        return [r["symbol"] for r in c.execute(
            "SELECT symbol FROM market_catalog WHERE enabled=1 AND verified=1 ORDER BY symbol").fetchall()]


def _market_row(symbol: str):
    """(v6.11.0 thread-offload helper) market_catalog row for card rendering."""
    with db.conn() as c:
        r = c.execute("SELECT * FROM market_catalog WHERE symbol=?", (symbol,)).fetchone()
        return dict(r) if r else None


def _signal_row_by_id(signal_id: int):
    """(v6.11.0 thread-offload helper) one full signal row by id."""
    with db.conn() as c:
        return c.execute("SELECT * FROM signals WHERE id=?", (signal_id,)).fetchone()
