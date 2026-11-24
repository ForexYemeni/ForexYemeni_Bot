"""Admin Partners Command Center (`a:part*`) & Partner Private Panel (`/partner`, `p:*`).

Strict isolation guarantees:
  * Admin routes (`a:part*`) are guarded by dispatcher's ADMIN_ID gate.
  * Partner routes (`p:*` and `/partner`) are strictly gated to registered,
    enabled partners (`partners_service.get_partner_by_user(uid)`).
  * A partner can ONLY see and control channels where `owner_partner_id == partner.id`.
  * Linking a partner channel verifies via Telegram `get_chat_member` that BOTH
    the partner is creator/administrator of that channel AND the bot is admin.
"""
from __future__ import annotations

from telegram import InlineKeyboardMarkup, Update
from telegram.ext import ContextTypes

from fysb import afn, config, db
from fysb.handlers.common import render
from fysb.services import audit as audit_log
from fysb.services import partners as partners_svc
from fysb.theme import esc, strategy_name, tf_name
from fysb.ui import keyboards as kb


def _fmt_quota(used: int, limit: int) -> str:
    """Format a partner manual-post daily quota cleanly in Arabic."""
    if limit <= 0:
        return f"♾️ مفتوح دائماً (استُخدم اليوم: {used})"
    return f"{used}/{limit} اليوم"


def _fmt_limit_badge(limit: int) -> str:
    return "♾️ مفتوح دائماً" if limit <= 0 else f"{limit}/يوم"


# ============================================================================
# SUPER ADMIN — Channel Partners & VIP Gatekeeper Command Center
# ============================================================================

async def admin_partners(q, uid, context, bot):
    plist = await afn.run(partners_svc.list_partners)
    lines = [
        "🤝 <b><u>مركز شركاء القنوات وحارس VIP الملكي</u></b>",
        "━━━━━━━━━━━━━━━━━━",
        "<blockquote>",
        "▎💰 <b>نموذج تقاسم الأرباح:</b> يضيف الشريك البوت لقناته، ويُرفق كل منشور بزر اشتراك خاص به، وتُحتسب عمولته تلقائياً فور تفعيل أي اشتراك مدفوع.",
        "▎🔐 <b>حارس قنوات VIP:</b> يُدخل المشتركين المدفوعين تلقائياً برابط شخصي لمرة واحدة ويُخرجهم تلقائياً فور انتهاء اشتراكهم.",
        "</blockquote>",
    ]
    rows_kb = [
        [
            kb.btn("➕ إضافة شريك جديد", "a:partadd", style="success"),
            kb.btn("🛡 فحص وطرد منتهيي VIP", "a:vipsweep", style="danger"),
        ],
    ]
    if not plist:
        lines.append("\n<i>لا يوجد شركاء مسجّلون بعد — اضغط «➕ إضافة شريك جديد» للبدء.</i>")
    else:
        lines.append(f"\n<b>الشركاء المسجّلون ({len(plist)}):</b>")
        for p in plist[:20]:
            st = await afn.run(partners_svc.partner_stats, p["id"])
            dot = "🟢" if p["enabled"] else "⚪"
            mode_ar = partners_svc.POST_MODE_SHORT.get(p["post_mode"], p["post_mode"])
            vip_tag = " · 🔐VIP" if p.get("perm_vip_gatekeeper") else ""
            lines.append(
                f"▎{dot} <b>#{p['id']} {esc(p['title'])}</b> (<code>{p['user_id']}</code>) · "
                f"{p['commission_pct']:.0f}% · {mode_ar}{vip_tag}\n"
                f"   👥 مدعوون: <b>{st.get('invited', 0)}</b> · 💎 مشتركون: <b>{st.get('converted', 0)}</b> · "
                f"💰 مستحق: <b>{st.get('earned_pending', 0):.2f}$</b>"
            )
            rows_kb.append([
                kb.btn(
                    f"{dot} #{p['id']} {p['title'][:18]} · {p['commission_pct']:.0f}% · {st.get('earned_pending', 0):.0f}$",
                    f"a:partview:{p['id']}",
                    style="success" if p["enabled"] else "danger",
                )
            ])
    rows_kb.append([kb.btn("📺 القنوات والمجموعات", "a:channels", style="primary")])
    await render(q, "\n".join(lines), kb.admin_nav(rows_kb), bot)


async def admin_partner_add(q, uid, context, bot):
    context.user_data["awaiting_partner_add"] = True
    body = (
        "➕ <b><u>إضافة شريك قناة جديد</u></b>\n"
        "━━━━━━━━━━━━━━━━━━\n"
        "<blockquote>"
        "▎✍️ أرسل الآن <b>معرّف تلغرام (Telegram ID)</b> للشريك، ويمكنك إتباعه بالاسم ونسبة العمولة:\n\n"
        "▎مثال 1 (سريع — عمولة 25% افتراضياً):\n"
        "<code>123456789</code>\n\n"
        "▎مثال 2 (بالاسم والنسبة):\n"
        "<code>123456789 | قناة الذهب الملكي | 30</code>\n\n"
        "▎👑 فور التسجيل سيحصل الشريك على رابط إحالة حصري + لوحة <code>/partner</code> الخاصة به."
        "</blockquote>"
    )
    await render(q, body, kb.admin_nav([[kb.btn("🗑 إلغاء", "a:partcancel", style="danger")]]), bot)


async def admin_partner_cancel(q, uid, context, bot):
    context.user_data.pop("awaiting_partner_add", None)
    await admin_partners(q, uid, context, bot)


async def admin_partner_view(q, uid, context, bot, pid: int):
    p = await afn.run(partners_svc.get_partner, pid)
    if not p:
        return await admin_partners(q, uid, context, bot)
    st = await afn.run(partners_svc.partner_stats, pid)
    chans = await afn.run(partners_svc.partner_channels, pid)
    mq = await afn.run(partners_svc.manual_quota_status, pid, p)
    link = partners_svc.partner_link(p["campaign_code"])
    mode_ar = partners_svc.POST_MODE_AR.get(p["post_mode"], p["post_mode"])

    perm_lines = []
    for col, label in partners_svc.PARTNER_PERM_FIELDS:
        perm_lines.append(f"▎{'✅' if p.get(col) else '⛔'} {label}")

    ch_lines = (
        [
            f"▎• <b>{esc(c['title'])}</b> ({'🟢' if c['enabled'] else '⚪'}"
            + (" · 🔐VIP" if c.get("is_vip_gate") else "")
            + f" · #{c['id']})"
            for c in chans
        ]
        if chans
        else ["▎<i>لم يربط الشريك قناته بعد — يدخل /partner ← ربط قناتي.</i>"]
    )

    body = [
        f"🤝 <b><u>ملف الشريك #{p['id']} — {esc(p['title'])}</u></b>",
        "━━━━━━━━━━━━━━━━━━",
        "<blockquote>",
        f"▎🆔 المعرّف: <code>{p['user_id']}</code> · الحالة: <b>{'🟢 مفعّل' if p['enabled'] else '🔴 متوقف'}</b>",
        f"▎🔗 كود الإحالة: <code>{esc(p['campaign_code'])}</code>",
        f"▎💰 نسبة العمولة: <b>{p['commission_pct']:.0f}%</b>",
        f"▎📣 وضع النشر: <b>{mode_ar}</b>",
        f"▎🔬 حد «تحليل الآن» اليومي: <b>{_fmt_quota(mq['analysis_used'], mq['analysis_limit'])}</b>",
        f"▎📡 حد «إشارة الآن» اليومي: <b>{_fmt_quota(mq['signal_used'], mq['signal_limit'])}</b>",
        f"▎🏷 التوقيع المخصص: <b>{esc(p.get('brand_label') or 'افتراضي البوت')}</b>",
        "</blockquote>",
        "<blockquote>",
        f"▎👥 المدعوون: <b>{st.get('invited', 0)}</b> · 💎 المشتركون: <b>{st.get('converted', 0)}</b> (تحويل: <b>{st.get('conv_rate_pct', 0):.1f}%</b>)",
        f"▎📊 إجمالي المبيعات: <b>{st.get('gross_sales', 0):,.2f} $</b>",
        f"▎💵 المستحق للصرف الآن: <b>{st.get('earned_pending', 0):,.2f} $</b> · المصروف سابقاً: <b>{st.get('paid_out', 0):,.2f} $</b>",
        f"▎🔐 أعضاء VIP النشطون: <b>{st.get('vip_active_members', 0)}</b>",
        "</blockquote>",
        "<b>قنوات الشريك:</b>\n<blockquote>" + "\n".join(ch_lines) + "</blockquote>",
        "<b>مصفوفة الصلاحيات:</b>\n<blockquote>" + "\n".join(perm_lines) + "</blockquote>",
    ]

    pct_row = [
        kb.btn(
            (f"✅ {v}%" if int(round(p["commission_pct"])) == v else f"{v}%"),
            f"a:partpct:{pid}:{v}",
            style="success" if int(round(p["commission_pct"])) == v else "primary",
        )
        for v in (15, 20, 25, 30, 40)
    ]
    mode_row = [
        kb.btn(
            ("✅ " if p["post_mode"] == m else "") + partners_svc.POST_MODE_SHORT[m],
            f"a:partmode:{pid}:{m}",
            style="success" if p["post_mode"] == m else "primary",
        )
        for m in partners_svc.POST_MODES
    ]

    perm_rows = []
    pf = list(partners_svc.PARTNER_PERM_FIELDS)
    for i in range(0, len(pf), 2):
        row = []
        for col, label in pf[i:i + 2]:
            on = bool(p.get(col))
            short = label.split("(", 1)[0].strip()
            row.append(
                kb.btn(
                    ("✅ " if on else "⛔ ") + short,
                    f"a:partperm:{pid}:{col}",
                    style="success" if on else "danger",
                )
            )
        perm_rows.append(row)

    rows_kb = [
        [kb.copy_btn("📋 نسخ رابط الشريك", link, style="primary")],
        pct_row,
        mode_row,
        [kb.btn("🚀 حدود النشر اليدوي (تحليل / إشارة الآن)", f"a:partlim:{pid}", style="primary")],
        *perm_rows,
        [
            kb.btn(
                f"💸 تسوية المستحق ({st.get('earned_pending', 0):.2f}$)",
                f"a:partsettle:{pid}",
                style="success",
            ),
            kb.btn(
                "⏹ إيقاف الشريك" if p["enabled"] else "▶️ تفعيل الشريك",
                f"a:parttoggle:{pid}",
                style="danger" if p["enabled"] else "success",
            ),
        ],
        [
            kb.btn("🗑 حذف الشريك", f"a:partdel:{pid}", style="danger"),
            kb.btn("↩️ كل الشركاء", "a:partners", style="primary"),
        ],
    ]
    await render(q, "\n".join(body), kb.admin_nav(rows_kb), bot)


async def admin_partner_toggle(q, uid, context, bot, pid: int):
    p = await afn.run(partners_svc.get_partner, pid)
    if p:
        new_state = 0 if p["enabled"] else 1
        await afn.run(partners_svc.update_partner, pid, enabled=new_state)
        await afn.run(audit_log.log, uid, f"partner_toggle:{pid}:{new_state}")
        if bot:
            try:
                if new_state == 0:
                    await bot.send_message(
                        p["user_id"],
                        "ℹ️ <b><u>تم إيقاف وضع الشريك مؤقتاً</u></b>\n"
                        "<blockquote>عاد حسابك الآن كحساب مستخدم عادي — أرسل /start لفتح القائمة الرئيسية.</blockquote>",
                        parse_mode="HTML",
                        reply_markup=kb.user_nav(),
                    )
                else:
                    await bot.send_message(
                        p["user_id"],
                        "👑 <b><u>تم إعادة تفعيل حسابك كشريك قناة!</u></b>\n"
                        "<blockquote>حسابك يعمل الآن في وضع الشريك الحصري — اضغط بالأسفل لفتح لوحة الشريك.</blockquote>",
                        parse_mode="HTML",
                        reply_markup=InlineKeyboardMarkup([[kb.btn("👑 فتح لوحة الشريك", "p:home", style="success")]]),
                    )
            except Exception:
                pass
    await admin_partner_view(q, uid, context, bot, pid)


async def admin_partner_pct(q, uid, context, bot, pid: int, pct: int):
    await afn.run(partners_svc.update_partner, pid, commission_pct=float(pct))
    await afn.run(audit_log.log, uid, f"partner_pct:{pid}:{pct}")
    await admin_partner_view(q, uid, context, bot, pid)


async def admin_partner_mode(q, uid, context, bot, pid: int, mode: str):
    await afn.run(partners_svc.update_partner, pid, post_mode=mode)
    # Also sync partner's linked channels so the new mode takes effect immediately
    chans = await afn.run(partners_svc.partner_channels, pid)
    from fysb.handlers import admin_channels
    for c in chans:
        await afn.run(admin_channels.update_channel, c["id"], post_mode=mode)
    await afn.run(audit_log.log, uid, f"partner_mode:{pid}:{mode}")
    await admin_partner_view(q, uid, context, bot, pid)


async def admin_partner_perm(q, uid, context, bot, pid: int, col: str):
    p = await afn.run(partners_svc.get_partner, pid)
    if p and col in dict(partners_svc.PARTNER_PERM_FIELDS):
        new_val = 0 if p.get(col) else 1
        await afn.run(partners_svc.update_partner, pid, **{col: new_val})
        await afn.run(audit_log.log, uid, f"partner_perm:{pid}:{col}:{new_val}")
    await admin_partner_view(q, uid, context, bot, pid)


async def admin_partner_limits(q, uid, context, bot, pid: int):
    """Per-partner daily manual posting limits panel (Analysis Now & Signal Now)."""
    p = await afn.run(partners_svc.get_partner, pid)
    if not p:
        return await admin_partners(q, uid, context, bot)
    mq = await afn.run(partners_svc.manual_quota_status, pid, p)
    a_lim, s_lim = mq["analysis_limit"], mq["signal_limit"]

    body = (
        f"🚀 <b><u>حدود النشر اليدوي اليومي — {esc(p['title'])}</u></b>\n"
        "━━━━━━━━━━━━━━━━━━\n"
        "<blockquote>"
        f"▎🔬 حد <b>«تحليل الآن»</b>: <b>{_fmt_quota(mq['analysis_used'], a_lim)}</b>\n"
        f"▎📡 حد <b>«إشارة الآن»</b>: <b>{_fmt_quota(mq['signal_used'], s_lim)}</b>\n"
        "▎♾️ القيمة <b>0</b> تعني <b>مفتوح دائماً بلا حد يومي</b>.\n"
        "▎🛡 الفرص الفارغة (لا توجد فرصة حالياً) لا تُحتسب من رصيد الشريك."
        "</blockquote>"
    )

    presets = (3, 6, 15, 30, 0)
    an_row = [
        kb.btn(
            (("✅ " if a_lim == v else "") + ("♾️ مفتوح" if v == 0 else f"🔬 {v}")),
            f"a:partlimset:{pid}:analysis:{v}",
            style="success" if a_lim == v else "primary",
        )
        for v in presets
    ]
    sig_row = [
        kb.btn(
            (("✅ " if s_lim == v else "") + ("♾️ مفتوح" if v == 0 else f"📡 {v}")),
            f"a:partlimset:{pid}:signal:{v}",
            style="success" if s_lim == v else "primary",
        )
        for v in presets
    ]
    rows_kb = [
        [kb.btn("♾️ فتح الكل بلا حد دائماً (تحليل + إشارة)", f"a:partlimset:{pid}:both:0", style="success")],
        an_row,
        sig_row,
        [
            kb.btn("✏️ رقم مخصص للتحليل", f"a:partlimask:{pid}:analysis", style="primary"),
            kb.btn("✏️ رقم مخصص للإشارات", f"a:partlimask:{pid}:signal", style="primary"),
        ],
        [
            kb.btn("🔄 تصفير عداد اليوم", f"a:partlimreset:{pid}", style="danger"),
            kb.btn(f"↩️ ملف الشريك #{pid}", f"a:partview:{pid}", style="primary"),
        ],
    ]
    await render(q, body, kb.admin_nav(rows_kb), bot)


async def admin_partner_limit_set(q, uid, context, bot, pid: int, kind: str, val: int):
    v = max(0, min(9999, int(val)))
    upd = {}
    if kind in ("analysis", "both"):
        upd["manual_analysis_limit"] = v
    if kind in ("signal", "both"):
        upd["manual_signal_limit"] = v
    if upd:
        await afn.run(partners_svc.update_partner, pid, **upd)
        await afn.run(audit_log.log, uid, f"partner_limit:{pid}:{kind}:{v}")
    await admin_partner_limits(q, uid, context, bot, pid)


async def admin_partner_limit_ask(q, uid, context, bot, pid: int, kind: str):
    label = "تحليل الآن 🔬" if kind == "analysis" else "إشارة الآن 📡"
    context.user_data["awaiting_partner_limit"] = (int(pid), kind)
    await render(
        q,
        f"✏️ <b><u>تعيين حد يومي مخصص — {label}</u></b>\n"
        "<blockquote>"
        "أرسل الآن الرقم اليومي المسموح لهذا الشريك (مثال: <code>10</code> أو <code>25</code>).\n"
        "أو أرسل <code>0</code> لجعله <b>♾️ مفتوحاً دائماً بلا حد</b>."
        "</blockquote>",
        kb.admin_nav([[kb.btn("🚫 إلغاء", f"a:partlim:{pid}", style="danger")]]),
        bot,
    )


async def admin_partner_limit_reset(q, uid, context, bot, pid: int):
    await afn.run(partners_svc.reset_manual_quota, pid)
    await afn.run(audit_log.log, uid, f"partner_limit_reset:{pid}")
    await admin_partner_limits(q, uid, context, bot, pid)


async def handle_partner_limit_input(update: Update, context: ContextTypes.DEFAULT_TYPE, uid: int, text: str) -> bool:
    info = context.user_data.pop("awaiting_partner_limit", None)
    if not info:
        return False
    pid, kind = info
    raw = text.strip().replace(",", "")
    if not raw.isdigit():
        await update.effective_message.reply_text(
            "⛔ أرسل رقماً صحيحاً فقط (مثل <code>10</code> أو <code>0</code> لمفتوح دائماً).",
            parse_mode="HTML",
            reply_markup=kb.admin_nav([[kb.btn("↩️ حدود الشريك", f"a:partlim:{pid}", style="primary")]]),
        )
        return True
    v = max(0, min(9999, int(raw)))
    col = "manual_analysis_limit" if kind == "analysis" else "manual_signal_limit"
    label = "تحليل الآن 🔬" if kind == "analysis" else "إشارة الآن 📡"
    await afn.run(partners_svc.update_partner, pid, **{col: v})
    await afn.run(audit_log.log, uid, f"partner_limit_custom:{pid}:{kind}:{v}")
    await update.effective_message.reply_text(
        f"✅ <b>تم تحديث حد {label} للشريك #{pid}:</b> <b>{_fmt_limit_badge(v)}</b>",
        parse_mode="HTML",
        reply_markup=kb.admin_nav([
            [kb.btn("🚀 حدود النشر اليدوي", f"a:partlim:{pid}", style="success")],
            [kb.btn(f"↩️ ملف الشريك #{pid}", f"a:partview:{pid}", style="primary")],
        ]),
    )
    return True


async def admin_partner_settle(q, uid, context, bot, pid: int):
    p = await afn.run(partners_svc.get_partner, pid)
    if not p:
        return await admin_partners(q, uid, context, bot)
    res = await afn.run(partners_svc.settle_partner, pid)
    await afn.run(audit_log.log, uid, f"partner_settle:{pid}:{res['total']}")
    if res["total"] > 0 and bot:
        try:
            await bot.send_message(
                p["user_id"],
                "💸 <b><u>تمت تسوية وصرف أرباح الشراكة!</u></b>\n"
                "<blockquote>"
                f"▎✅ المبلغ المسوّى: <b>{res['total']:,.2f} $</b> ({res['count']} عمولات)\n"
                "▎👑 شكراً لشراكتك مع <b>FOREXYEMENI</b>."
                "</blockquote>",
                parse_mode="HTML",
                reply_markup=InlineKeyboardMarkup([[kb.btn("👑 لوحة الشريك", "p:home", style="success")]]),
            )
        except Exception:
            pass
    await render(
        q,
        f"✅ <b>تمت تسوية أرباح الشريك #{pid}</b>\n"
        f"<blockquote>▎💰 الإجمالي المصروف: <b>{res['total']:,.2f} $</b> ({res['count']} عملية)</blockquote>",
        kb.admin_nav([[kb.btn(f"↩️ ملف الشريك #{pid}", f"a:partview:{pid}", style="primary")]]),
        bot,
    )


async def admin_partner_delete(q, uid, context, bot, pid: int):
    p = await afn.run(partners_svc.get_partner, pid)
    await afn.run(partners_svc.delete_partner, pid)
    await afn.run(audit_log.log, uid, f"partner_delete:{pid}")
    if p and bot:
        try:
            await bot.send_message(
                p["user_id"],
                "ℹ️ <b><u>تم إنهاء حساب الشريك</u></b>\n"
                "<blockquote>عاد حسابك الآن كمستخدم عادي بالكامل — أرسل /start لفتح القائمة الرئيسية للمستخدم.</blockquote>",
                parse_mode="HTML",
                reply_markup=kb.user_nav(),
            )
        except Exception:
            pass
    await admin_partners(q, uid, context, bot)


async def admin_vip_sweep(q, uid, context, bot):
    await render(q, "🛡 جارٍ فحص جميع قنوات VIP وإخراج المنتهية اشتراكاتهم تلقائياً...", None)
    res = await partners_svc.sweep_expired_vip_members(bot)
    await afn.run(audit_log.log, uid, f"vip_sweep:removed={res['removed']}")
    body = (
        "🛡 <b><u>نتيجة فحص حارس قنوات VIP</u></b>\n"
        "━━━━━━━━━━━━━━━━━━\n"
        "<blockquote>"
        f"▎👥 أعضاء فُحصوا: <b>{res['checked']}</b>\n"
        f"▎🚪 أُخرجوا لانتهاء الاشتراك: <b>{res['removed']}</b>\n"
        f"▎⚠️ أخطاء صلاحيات: <b>{res['errors']}</b>"
        "</blockquote>\n"
        "<i>ملاحظة: يجب أن يملك البوت صلاحية «حظر المستخدمين» و«دعوة عبر الروابط» في قنوات VIP.</i>"
    )
    await render(
        q,
        body,
        kb.admin_nav([[kb.btn("🤝 شركاء القنوات", "a:partners", style="primary")]]),
        bot,
    )


# ============================================================================
# PARTNER PRIVATE DASHBOARD — `/partner` & `p:*` callbacks (Partner-Only Mode)
# ============================================================================

def _partner_nav(extra_rows: list | None = None, include_home: bool = True) -> InlineKeyboardMarkup:
    """Partner-Only navigation wrapper — NEVER exposes `u:home` or regular user menus."""
    rows = list(extra_rows or [])
    if include_home:
        rows.append([kb.btn("👑 لوحة الشريك الرئيسية", "p:home", style="primary")])
    return InlineKeyboardMarkup(rows)


async def render_partner_start(update: Update, p: dict) -> None:
    """Called by /start and /menu when the user is an active partner — opens
    ONLY the Partner Command Panel (regular user account is closed)."""
    text, markup = await _build_partner_home(p)
    await update.effective_message.reply_text(
        text, parse_mode="HTML", reply_markup=markup, disable_web_page_preview=True
    )


async def partner_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Entry command `/partner` — accessible ONLY to active registered partners."""
    uid = update.effective_user.id
    p = await afn.run(partners_svc.get_partner_by_user, uid)
    if not p or not p.get("enabled"):
        await update.effective_message.reply_text(
            "🤝 <b>بوابة شركاء القنوات</b>\n"
            "<blockquote>هذا القسم مخصّص لأصحاب القنوات المعتمدين في برنامج شراكة تقاسم الأرباح.\n"
            "للتقدّم بطلب شراكة، تواصل مع الإدارة عبر قسم الدعم الفني.</blockquote>",
            parse_mode="HTML",
            reply_markup=kb.user_nav([[kb.btn("🎧 تواصل للشراكة", "u:support", style="success")]]),
        )
        return
    await render_partner_start(update, p)


async def _build_partner_home(p: dict) -> tuple[str, InlineKeyboardMarkup]:
    st = await afn.run(partners_svc.partner_stats, p["id"])
    chans = await afn.run(partners_svc.partner_channels, p["id"])
    link = partners_svc.partner_link(p["campaign_code"])
    mode_ar = partners_svc.POST_MODE_AR.get(p["post_mode"], p["post_mode"])

    body = [
        f"👑 <b><u>لوحة الشريك الملكي — {esc(p['title'])}</u></b>",
        "━━━━━━━━━━━━━━━━━━",
        "<blockquote>",
        f"▎🛡 وضع الحساب: <b>حساب شريك قناة حصري</b>",
        f"▎💰 نسبة عمولتك: <b>{p['commission_pct']:.0f}%</b> من كل اشتراك مدفوع",
        f"▎📣 وضع النشر المعتمد: <b>{mode_ar}</b>",
        f"▎🔗 رابط قناتك الحصري:\n<code>{esc(link)}</code>",
        "</blockquote>",
        "<blockquote>",
        f"▎👥 دخلوا البوت عبر قناتك: <b>{st.get('invited', 0)}</b>",
        f"▎💎 اشتركوا فعلياً: <b>{st.get('converted', 0)}</b> (نسبة التحويل: <b>{st.get('conv_rate_pct', 0):.1f}%</b>)",
        f"▎💵 رصيدك المستحق الآن: <b>{st.get('earned_pending', 0):,.2f} $</b>",
        f"▎✅ إجمالي المصروف سابقاً: <b>{st.get('paid_out', 0):,.2f} $</b>",
        f"▎🔐 أعضاء قنوات VIP النشطون: <b>{st.get('vip_active_members', 0)}</b>",
        "</blockquote>",
    ]
    rows_kb = [
        [kb.copy_btn("📋 نسخ رابط الإحالة", link, style="success"),
         kb.url_btn("📤 مشاركة الرابط", f"https://t.me/share/url?url={link}", style="primary")],
        [kb.btn("➕ ربط قناتي بالبوت", "p:addch", style="success"),
         kb.btn(f"💸 طلب سحب ({st.get('earned_pending', 0):.2f}$)", "p:payout", style="success")],
    ]
    for c in chans:
        dot = "🟢" if c["enabled"] else "⚪"
        vip = " 🔐VIP" if c.get("is_vip_gate") else ""
        rows_kb.append([kb.btn(f"{dot} إدارة: {c['title'][:24]}{vip}", f"p:ch:{c['id']}", style="primary")])
    if p.get("perm_custom_branding"):
        rows_kb.append([kb.btn("🏷 تعديل توقيع قناتي", "p:brand", style="primary")])
    rows_kb.append([
        kb.btn("🎧 تواصل مع الإدارة", "p:contact", style="primary"),
        kb.btn("🔄 تحديث الإحصائيات", "p:home", style="primary"),
    ])
    return "\n".join(body), _partner_nav(rows_kb, include_home=False)


async def handle_partner_callback(q, uid: int, context, bot, raw_action: str):
    """Route all `p:*` callbacks with strict partner ownership checks."""
    p = await afn.run(partners_svc.get_partner_by_user, uid)
    if not p or not p.get("enabled"):
        await render(q, "⛔ هذه اللوحة مخصّصة للشركاء المفعّلين فقط.", kb.user_nav(), bot)
        return

    parts = raw_action.split(":")
    act = parts[0]

    if act == "home":
        text, markup = await _build_partner_home(p)
        await render(q, text, markup, bot)
        return

    if act == "contact":
        from fysb.services import support as support_service
        from fysb.handlers.user_pages import _support_contact_rows
        cfg = await afn.run(support_service.config_row) or {}
        contact_rows = _support_contact_rows(cfg)
        body = (
            "🎧 <b><u>التواصل المباشر مع إدارة FOREXYEMENI</u></b>\n"
            "━━━━━━━━━━━━━━━━━━\n"
            "<blockquote>اختر وسيلة التواصل المناسبة لك بالأسفل للتنسيق حول قناتك أو تسوية أرباحك.</blockquote>"
        )
        await render(q, body, _partner_nav(contact_rows, include_home=True), bot)
        return

    if act == "addch":
        context.user_data["awaiting_partner_channel"] = p["id"]
        body = (
            "➕ <b><u>ربط قناتك بالبوت</u></b>\n"
            "━━━━━━━━━━━━━━━━━━\n"
            "<blockquote>"
            "▎1️⃣ أضف البوت <b>مشرفاً</b> في قناتك بصلاحية <b>نشر وتعديل الرسائل</b> "
            "(وصلاحية <b>دعوة وحظر الأعضاء</b> إذا كنت ستفعّل حارس VIP).\n"
            "▎2️⃣ أرسل الآن <b>معرّف القناة</b> (مثل <code>@MyChannel</code> أو <code>-100...</code>) "
            "أو <b>قم بتحويل أي منشور</b> من قناتك إلى هنا.\n"
            "▎🛡 سيتحقق البوت تلقائياً أنك مالك أو مشرف القناة."
            "</blockquote>"
        )
        await render(q, body, _partner_nav([[kb.btn("🚫 إلغاء", "p:home", style="danger")]], include_home=False), bot)
        return

    if act == "brand":
        if not p.get("perm_custom_branding"):
            await render(q, "⛔ صلاحية التوقيع المخصص غير مفعّلة لحسابك.", _partner_nav(), bot)
            return
        context.user_data["awaiting_partner_brand"] = p["id"]
        await render(
            q,
            "🏷 <b>تخصيص توقيع قناتك</b>\n"
            "<blockquote>أرسل اسم أو معرّف قناتك ليظهر بجانب توقيع البوت أسفل المنشورات (أو أرسل <code>-</code> لإعادة الافتراضي):</blockquote>",
            _partner_nav([[kb.btn("🚫 إلغاء", "p:home", style="danger")]], include_home=False),
            bot,
        )
        return

    if act == "payout":
        st = await afn.run(partners_svc.partner_stats, p["id"])
        pending = float(st.get("earned_pending") or 0.0)
        if pending <= 0:
            await render(
                q,
                "💰 <b>طلب سحب الأرباح</b>\n<blockquote>لا يوجد رصيد مستحق للسحب حالياً (<code>0.00 $</code>). شارك رابطك لبدء تحقيق الأرباح!</blockquote>",
                _partner_nav(),
                bot,
            )
            return
        if config.ADMIN_ID and bot:
            try:
                await bot.send_message(
                    config.ADMIN_ID,
                    "💸 <b><u>طلب سحب أرباح شريك قناة</u></b>\n"
                    "━━━━━━━━━━━━━━━━━━\n"
                    "<blockquote>"
                    f"▎🤝 الشريك: <b>{esc(p['title'])}</b> (<code>{uid}</code>)\n"
                    f"▎💰 الرصيد المطلوب تسويته: <b>{pending:,.2f} $</b>\n"
                    f"▎💎 عدد المشتركين المحقَّقين: <b>{st.get('converted', 0)}</b>"
                    "</blockquote>",
                    parse_mode="HTML",
                    reply_markup=InlineKeyboardMarkup([
                        [kb.btn(f"✅ اعتماد التسوية ({pending:.2f}$)", f"a:partsettle:{p['id']}", style="success")],
                        [kb.btn("🤝 فتح ملف الشريك", f"a:partview:{p['id']}", style="primary")],
                    ]),
                )
            except Exception:
                pass
        await render(
            q,
            f"✅ <b>تم إرسال طلب سحب بقيمة {pending:,.2f} $ إلى الإدارة بنجاح!</b>\n"
            "<blockquote>سيتم مراجعة الطلب وتحويل أرباحك وإشعارك فور الاعتماد.</blockquote>",
            _partner_nav(),
            bot,
        )
        return

    # --- Channel-scoped actions (verify ownership strictly!) ---
    cid = int(parts[1]) if len(parts) > 1 and parts[1].isdigit() else 0
    from fysb.handlers import admin_channels
    ch = await afn.run(admin_channels.get_channel, cid) if cid else None
    if not ch or int(ch.get("owner_partner_id") or 0) != int(p["id"]):
        await render(q, "⛔ القناة غير موجودة أو لا تتبع حسابك.", _partner_nav(), bot)
        return

    if act == "ch":
        await _render_partner_channel(q, p, ch, bot)
        return

    if act == "chtoggle":
        await afn.run(admin_channels.update_channel, cid, enabled=0 if ch["enabled"] else 1)
        ch = await afn.run(admin_channels.get_channel, cid)
        await _render_partner_channel(q, p, ch, bot)
        return

    if act == "chall":
        upd = {"enabled": 1}
        if p.get("perm_auto_signals"):
            upd["auto_signals"] = 1
        if p.get("perm_auto_analysis"):
            upd["auto_analysis"] = 1
        if p.get("perm_choose_markets"):
            upd["markets"] = "ALL"
            upd["timeframes"] = "ALL"
            upd["strategies"] = "ALL"
        await afn.run(admin_channels.update_channel, cid, **upd)
        ch = await afn.run(admin_channels.get_channel, cid)
        await _render_partner_channel(q, p, ch, bot)
        return

    if act == "chmode":
        which = parts[2] if len(parts) > 2 else ""
        if which == "analysis":
            if not p.get("perm_auto_analysis"):
                await render(q, "⛔ صلاحية النشر التلقائي للتحليلات غير مفعّلة لحسابك — تواصل مع الإدارة.",
                             _partner_nav([[kb.btn("↩️ إدارة القناة", f"p:ch:{cid}")]]), bot)
                return
            await afn.run(admin_channels.update_channel, cid, auto_analysis=0 if ch["auto_analysis"] else 1)
        elif which == "signals":
            if not p.get("perm_auto_signals"):
                await render(q, "⛔ صلاحية النشر التلقائي للإشارات غير مفعّلة لحسابك — تواصل مع الإدارة.",
                             _partner_nav([[kb.btn("↩️ إدارة القناة", f"p:ch:{cid}")]]), bot)
                return
            await afn.run(admin_channels.update_channel, cid, auto_signals=0 if ch["auto_signals"] else 1)
        ch = await afn.run(admin_channels.get_channel, cid)
        await _render_partner_channel(q, p, ch, bot)
        return

    if act == "chint":
        cur_iv = int(ch.get("interval_minutes") or 60)
        rows = [
            [
                kb.btn(
                    (f"✅ كل {m} د" if cur_iv == m else f"كل {m} د"),
                    f"p:chsetint:{cid}:{m}",
                    style="success" if cur_iv == m else "primary",
                )
                for m in (30, 60, 120)
            ],
            [
                kb.btn(
                    (f"✅ كل {m} د" if cur_iv == m else f"كل {m} د"),
                    f"p:chsetint:{cid}:{m}",
                    style="success" if cur_iv == m else "primary",
                )
                for m in (180, 240, 360)
            ],
            [kb.btn("↩️ إدارة القناة", f"p:ch:{cid}", style="primary")],
        ]
        await render(
            q,
            f"⏰ <b><u>جدولة النشر التلقائي لقناتك ({esc(ch['title'])})</u></b>\n"
            f"<blockquote>الفاصل الحالي: كل <b>{cur_iv} دقيقة</b> (توقيت مكة · سكون ليلي 01:00–07:00).\n"
            f"اختر الفاصل الزمني المناسب للنشر الدوري للتحليلات والإشارات:</blockquote>",
            _partner_nav(rows),
            bot,
        )
        return

    if act == "chsetint":
        mins = int(parts[2]) if len(parts) > 2 and parts[2].isdigit() else 60
        mins = max(30, min(360, mins))
        await afn.run(admin_channels.update_channel, cid, interval_minutes=mins)
        ch = await afn.run(admin_channels.get_channel, cid)
        await _render_partner_channel(q, p, ch, bot)
        return

    if act == "chvip":
        if not p.get("perm_vip_gatekeeper"):
            await render(q, "⛔ صلاحية حارس VIP غير مفعّلة لحسابك — تواصل مع الإدارة لتفعيلها.",
                         _partner_nav([[kb.btn("↩️ إعداد القناة", f"p:ch:{cid}")]]), bot)
            return
        await afn.run(admin_channels.update_channel, cid, is_vip_gate=0 if ch.get("is_vip_gate") else 1)
        ch = await afn.run(admin_channels.get_channel, cid)
        await _render_partner_channel(q, p, ch, bot)
        return

    if act == "chpost":
        kind = parts[2] if len(parts) > 2 else "analysis"
        if kind not in ("analysis", "signal"):
            kind = "analysis"
        if not p.get("perm_manual_post"):
            await render(q, "⛔ صلاحية النشر اليدوي غير مفعّلة لحسابك.",
                         _partner_nav([[kb.btn("↩️ إعداد القناة", f"p:ch:{cid}")]]), bot)
            return
        ok_q, used_now, cap = await afn.run(partners_svc.can_manual_post, p["id"], kind)
        kind_ar = "تحليل الآن" if kind == "analysis" else "إشارة الآن"
        if not ok_q:
            await render(
                q,
                f"⏳ <b>بلغت الحد اليومي لـ «{kind_ar}» ({used_now}/{cap} اليوم)</b> — البث التلقائي مستمر دون توقف.\n"
                "<blockquote>يمكنك التواصل مع الإدارة لرفع الحد اليومي أو فتحه دائماً.</blockquote>",
                _partner_nav([
                    [kb.btn("🎧 تواصل مع الإدارة", "p:contact", style="primary")],
                    [kb.btn("↩️ إعداد القناة", f"p:ch:{cid}", style="primary")],
                ]),
                bot,
            )
            return
        await render(q, "🚀 جارٍ التوليد والنشر في قناتك على البيانات الحقيقية...", None)
        res = await (
            admin_channels.post_analysis(cid, bot, ch)
            if kind == "analysis"
            else admin_channels.post_signal(cid, bot, ch)
        )
        if res.get("ok"):
            used_after, cap_after = await afn.run(partners_svc.record_manual_post, p["id"], kind)
            quota_txt = (
                f"♾️ مفتوح دائماً (استُخدم اليوم: <b>{used_after}</b>)"
                if cap_after <= 0
                else f"<b>{used_after}/{cap_after}</b>"
            )
            await render(
                q,
                f"✅ <b>تم النشر بنجاح في قناتك ({esc(ch['title'])})!</b>\n"
                f"<blockquote>استخدام «{kind_ar}» اليوم: {quota_txt}</blockquote>",
                _partner_nav([[kb.btn("↩️ إعداد القناة", f"p:ch:{cid}", style="primary")]]),
                bot,
            )
        else:
            from fysb.ui.cards import render_no_signal_card
            txt = (
                render_no_signal_card(
                    symbol=ch.get("markets") or "ALL",
                    strategy_label="جميع المنهجيات",
                    status="NO_SETUP",
                    reason=res.get("error", ""),
                    show_watch_hint=False,
                )
                if kind == "signal"
                else f"⛔ {esc(res.get('error', 'تعذر النشر'))}"
            )
            await render(q, txt, _partner_nav([[kb.btn("↩️ إعداد القناة", f"p:ch:{cid}", style="primary")]]), bot)
        return

    if act == "chfeat":
        kind = parts[2] if len(parts) > 2 else "markets"
        if kind not in ("markets", "timeframes", "strategies"):
            kind = "markets"
        if not p.get("perm_choose_markets"):
            await render(q, "⛔ اختيار الأسواق والأطر والاستراتيجيات مضبوط من قبل الإدارة.",
                         _partner_nav([[kb.btn("↩️ إعداد القناة", f"p:ch:{cid}")]]), bot)
            return
        await _render_partner_feat(q, ch, kind, bot)
        return

    if act == "chfeatset":
        if not p.get("perm_choose_markets"):
            return
        kind = parts[2] if len(parts) > 2 else "markets"
        if kind not in ("markets", "timeframes", "strategies"):
            return
        item = parts[3] if len(parts) > 3 else "ALL"
        cur = [x.strip() for x in (ch.get(kind) or "").split(",") if x.strip()]
        if item.upper() == "ALL":
            cur = ["ALL"]
        else:
            cur = [x for x in cur if x.upper() != "ALL"]
            if item.upper() in [x.upper() for x in cur]:
                cur = [x for x in cur if x.upper() != item.upper()]
            else:
                cur.append(item)
            if not cur:
                cur = ["ALL"]
        await afn.run(admin_channels.update_channel, cid, **{kind: ",".join(cur)})
        ch = await afn.run(admin_channels.get_channel, cid)
        await _render_partner_feat(q, ch, kind, bot)
        return

    if act == "chdel":
        await afn.run(admin_channels.delete_channel, cid)
        text, markup = await _build_partner_home(p)
        await render(q, text, markup, bot)
        return


async def _render_partner_channel(q, p: dict, ch: dict, bot):
    cid = ch["id"]
    mode_ar = partners_svc.POST_MODE_SHORT.get(partners_svc.effective_post_mode(ch, p), "تشويقي")
    mkts_raw = [x.strip() for x in (ch.get("markets") or "").split(",") if x.strip()]
    tfs_raw = [x.strip() for x in (ch.get("timeframes") or "").split(",") if x.strip()]
    strats_raw = [x.strip() for x in (ch.get("strategies") or "").split(",") if x.strip()]

    mkt_label = "جميع الأسواق (ALL)" if (not mkts_raw or "ALL" in [m.upper() for m in mkts_raw]) else "، ".join(mkts_raw)
    tf_label = "جميع الأطر (ALL)" if (not tfs_raw or "ALL" in [t.upper() for t in tfs_raw]) else "، ".join(tf_name(t) for t in tfs_raw)
    strat_label = (
        "جميع الاستراتيجيات (13)"
        if (not strats_raw or "ALL" in [s.upper() for s in strats_raw])
        else "، ".join(strategy_name(s) for s in strats_raw)
    )

    auto_an_txt = (
        ("🟢 مفعل" if ch.get("auto_analysis") else "⚪ متوقف")
        if p.get("perm_auto_analysis")
        else "🔒 مقفل من الإدارة"
    )
    auto_sig_txt = (
        ("🟢 مفعلة" if ch.get("auto_signals") else "⚪ متوقفة")
        if p.get("perm_auto_signals")
        else "🔒 مقفل من الإدارة"
    )
    iv = int(ch.get("interval_minutes") or 60)
    last_sent = (ch.get("last_sent") or "")[:19].replace("T", " ") or "—"
    mq = await afn.run(partners_svc.manual_quota_status, p["id"], p)
    an_q_txt = "♾️ مفتوح" if mq["analysis_limit"] <= 0 else f"{mq['analysis_used']}/{mq['analysis_limit']}"
    sig_q_txt = "♾️ مفتوح" if mq["signal_limit"] <= 0 else f"{mq['signal_used']}/{mq['signal_limit']}"

    body = (
        f"📺 <b><u>إدارة قناتك — {esc(ch['title'])}</u></b>\n"
        "━━━━━━━━━━━━━━━━━━\n"
        "<blockquote>"
        f"▎📌 حالة القناة: <b>{'🟢 البث نشط' if ch['enabled'] else '⚪ متوقف'}</b>\n"
        f"▎🔬 النشر التلقائي للتحليلات: <b>{auto_an_txt}</b>\n"
        f"▎📡 النشر التلقائي للإشارات: <b>{auto_sig_txt}</b>\n"
        f"▎🚀 رصيد النشر اليدوي اليوم: تحليل <b>{an_q_txt}</b> · إشارة <b>{sig_q_txt}</b>\n"
        f"▎📣 وضع النشر: <b>{mode_ar}</b>\n"
        f"▎🔐 حارس قناة VIP: <b>{'🟢 مفعّل (إدخال/طرد تلقائي)' if ch.get('is_vip_gate') else '⚪ متوقف'}</b>\n"
        f"▎🌐 الأسواق: <b>{esc(mkt_label)}</b>\n"
        f"▎⏱ الأطر الزمنية: <b>{esc(tf_label)}</b>\n"
        f"▎🧠 الاستراتيجيات: <b>{esc(strat_label)}</b>\n"
        f"▎⏰ الجدولة: كل <b>{iv} دقيقة</b> (توقيت مكة · سكون ليلي 01–07)\n"
        f"▎🕐 آخر نشر: <code>{esc(last_sent)}</code>"
        "</blockquote>"
    )

    rows = [
        [kb.btn("🌐 تفعيل البث الشامل لكل الأسواق والأطر", f"p:chall:{cid}", style="success")],
        [kb.btn("⏹ إيقاف القناة" if ch["enabled"] else "▶️ تفعيل القناة", f"p:chtoggle:{cid}",
                style="danger" if ch["enabled"] else "success")],
    ]

    auto_row = []
    if p.get("perm_auto_analysis"):
        auto_row.append(
            kb.btn(
                "⏹ إيقاف التحليل التلقائي" if ch.get("auto_analysis") else "▶️ تفعيل التحليل التلقائي",
                f"p:chmode:{cid}:analysis",
                style="danger" if ch.get("auto_analysis") else "success",
            )
        )
    if p.get("perm_auto_signals"):
        auto_row.append(
            kb.btn(
                "⏹ إيقاف الإشارات التلقائية" if ch.get("auto_signals") else "▶️ تفعيل الإشارات التلقائية",
                f"p:chmode:{cid}:signals",
                style="danger" if ch.get("auto_signals") else "success",
            )
        )
    if auto_row:
        rows.append(auto_row)

    if p.get("perm_choose_markets"):
        rows.append([
            kb.btn("🌐 الأسواق", f"p:chfeat:{cid}:markets", style="primary"),
            kb.btn("⏱ الأطر", f"p:chfeat:{cid}:timeframes", style="primary"),
            kb.btn("🧠 الاستراتيجيات", f"p:chfeat:{cid}:strategies", style="primary"),
        ])

    if p.get("perm_auto_analysis") or p.get("perm_auto_signals"):
        rows.append([kb.btn(f"⏰ الجدولة (كل {iv} دقيقة)", f"p:chint:{cid}", style="primary")])

    if p.get("perm_vip_gatekeeper"):
        rows.append([
            kb.btn(
                "🔐 إيقاف حارس VIP" if ch.get("is_vip_gate") else "🔐 تفعيل حارس VIP لهذه القناة",
                f"p:chvip:{cid}",
                style="danger" if ch.get("is_vip_gate") else "success",
            )
        ])

    if p.get("perm_manual_post"):
        rows.append([
            kb.btn("🚀 تحليل الآن", f"p:chpost:{cid}:analysis", style="success"),
            kb.btn("📡 إشارة الآن", f"p:chpost:{cid}:signal", style="success"),
        ])

    rows.append([
        kb.btn("🗑 فصل القناة", f"p:chdel:{cid}", style="danger"),
        kb.btn("↩️ لوحة الشريك", "p:home", style="primary"),
    ])
    await render(q, body, _partner_nav(rows, include_home=False), bot)


async def _render_partner_feat(q, ch: dict, kind: str, bot):
    from fysb.handlers import admin_channels
    cid = ch["id"]
    cur = [x.strip().upper() for x in (ch.get(kind) or "").split(",") if x.strip()]
    if kind == "markets":
        items = ["ALL"] + await afn.run(admin_channels._enabled_verified_market_symbols)
        title = "الأسواق"
    elif kind == "timeframes":
        items = ["ALL", "M1", "M5", "M15", "M30", "H1", "H4", "D1"]
        title = "الأطر الزمنية"
    else:
        from fysb.engines.strategies import VERIFIABLE
        items = ["ALL"] + list(VERIFIABLE)
        title = "الاستراتيجيات (13)"
    rows = [
        [
            kb.btn(
                ("✅ " if it.upper() in cur else "▫️ ")
                + ("الكل (ALL)" if it == "ALL" else (strategy_name(it) if kind == "strategies" else it)),
                f"p:chfeatset:{cid}:{kind}:{it}",
                style="success" if it.upper() in cur else "primary",
            )
            for it in items[i:i + 3]
        ]
        for i in range(0, len(items), 3)
    ]
    rows.append([kb.btn("↩️ إدارة القناة", f"p:ch:{cid}", style="primary")])
    await render(
        q,
        f"⚙️ <b>تعديل {title} لقناتك ({esc(ch['title'])})</b>\n"
        "اضغط للتبديل (اختر <b>الكل (ALL)</b> للبث الشامل تلقائياً):",
        _partner_nav(rows),
        bot,
    )


# ============================================================================
# Input Handlers (called from inputs.py)
# ============================================================================

async def handle_partner_add_input(update: Update, context: ContextTypes.DEFAULT_TYPE, uid: int, text: str) -> bool:
    """Super Admin sends: `USER_ID` or `USER_ID | Title | Pct`."""
    context.user_data.pop("awaiting_partner_add", None)
    parts = [x.strip() for x in text.split("|")]
    raw_uid = parts[0].strip()
    if not raw_uid.isdigit():
        await update.effective_message.reply_text(
            "⛔ يجب إرسال معرّف تلغرام رقمي صحيح (مثال: <code>123456789 | قناة الذهب | 25</code>).",
            parse_mode="HTML",
        )
        return True
    p_uid = int(raw_uid)
    title = parts[1] if len(parts) > 1 and parts[1] else f"شريك #{p_uid}"
    pct = 25.0
    if len(parts) > 2 and parts[2]:
        try:
            pct = float(parts[2].replace("%", "").strip())
        except ValueError:
            pct = 25.0
    p = await afn.run(partners_svc.create_partner, p_uid, title, pct, "teaser", 1)
    await afn.run(audit_log.log, uid, f"partner_added:{p['id']}:{p_uid}")
    link = partners_svc.partner_link(p["campaign_code"])
    await update.effective_message.reply_text(
        f"✅ <b><u>تم تسجيل الشريك #{p['id']} بنجاح!</u></b>\n"
        "━━━━━━━━━━━━━━━━━━\n"
        "<blockquote>"
        f"▎🤝 الاسم: <b>{esc(p['title'])}</b> (<code>{p_uid}</code>)\n"
        f"▎💰 نسبة العمولة: <b>{p['commission_pct']:.0f}%</b> · الوضع: <b>🔒 تشويقي</b>\n"
        f"▎🔗 رابط الشريك الحصري:\n<code>{esc(link)}</code>\n"
        "▎👑 تحوّل حساب الشريك الآن إلى <b>وضع الشريك الحصري</b> — سيفتح له البوت بلوحة الشريك فقط حتى تعطيله أو حذفه."
        "</blockquote>",
        parse_mode="HTML",
        reply_markup=kb.admin_nav([[kb.btn(f"⚙️ إدارة الشريك #{p['id']}", f"a:partview:{p['id']}", style="success")]]),
        disable_web_page_preview=True,
    )
    try:
        await update.get_bot().send_message(
            p_uid,
            "👑 <b><u>مبروك — تم ترقية حسابك إلى شريك رسمي في FOREXYEMENI!</u></b>\n"
            "━━━━━━━━━━━━━━━━━━\n"
            "<blockquote>"
            f"▎🛡 وضع الحساب: <b>حساب شريك قناة حصري</b> (يفتح البوت الآن بلوحة الشريك فقط).\n"
            f"▎💰 عمولتك المعتمدة: <b>{p['commission_pct']:.0f}%</b> من كل اشتراك عبر قناتك.\n"
            f"▎🔗 رابطك الحصري:\n<code>{esc(link)}</code>\n"
            "▎🚀 اضغط الزر بالأسفل أو أرسل <code>/start</code> لفتح لوحة الشريك وربط قناتك الآن!"
            "</blockquote>",
            parse_mode="HTML",
            reply_markup=InlineKeyboardMarkup([[kb.btn("👑 فتح لوحة الشريك الآن", "p:home", style="success")]]),
            disable_web_page_preview=True,
        )
    except Exception:
        pass
    return True


async def handle_partner_channel_input(update: Update, context: ContextTypes.DEFAULT_TYPE, uid: int, text: str) -> bool:
    """Partner sends `@channel`, `-100...`, or forwards a post from their channel.
    Verifies via Telegram API that BOTH the partner AND the bot are admins."""
    pid = context.user_data.pop("awaiting_partner_channel", None)
    if not pid:
        return False
    p = await afn.run(partners_svc.get_partner_by_user, uid)
    if not p or int(p["id"]) != int(pid) or not p.get("enabled"):
        await update.effective_message.reply_text("⛔ حساب الشريك غير مفعّل.")
        return True

    msg = update.effective_message
    fwd_chat = getattr(msg, "forward_from_chat", None)
    if fwd_chat is None and getattr(msg, "forward_origin", None) is not None:
        fwd_chat = getattr(msg.forward_origin, "chat", None)

    bot = update.get_bot()
    chat_ref = None
    if fwd_chat is not None:
        chat_ref = fwd_chat.id
    else:
        raw = text.strip()
        if raw.startswith("https://t.me/") or raw.startswith("t.me/"):
            raw = "@" + raw.rstrip("/").split("/")[-1]
        if raw.startswith("@") or raw.lstrip("-").isdigit():
            chat_ref = int(raw) if raw.lstrip("-").isdigit() else raw
    if chat_ref is None:
        await msg.reply_text(
            "⛔ لم أتمكن من التعرّف على القناة — أرسل <code>@ChannelName</code> أو <code>-100...</code> أو حوّل منشوراً منها.",
            parse_mode="HTML",
        )
        return True

    try:
        chat = await bot.get_chat(chat_ref)
    except Exception:
        await msg.reply_text("⛔ تعذّر الوصول إلى القناة — تأكد من إضافة البوت مشرفاً فيها أولاً ثم أعد المحاولة.")
        return True

    # Verify partner is creator/administrator of that channel
    try:
        member = await bot.get_chat_member(chat.id, uid)
        if getattr(member, "status", "") not in ("creator", "administrator"):
            await msg.reply_text("⛔ يجب أن تكون أنت مالكاً أو مشرفاً في هذه القناة لربطها بحسابك.")
            return True
    except Exception:
        await msg.reply_text("⛔ لم أتمكن من التحقق من صلاحيتك في القناة — تأكد أن البوت مشرف فيها.")
        return True

    # Verify bot is administrator in that channel
    try:
        me = await bot.get_me()
        bot_m = await bot.get_chat_member(chat.id, me.id)
        if getattr(bot_m, "status", "") not in ("creator", "administrator"):
            await msg.reply_text("⛔ يجب رفع البوت مشرفاً في القناة بصلاحية النشر والتعديل أولاً.")
            return True
    except Exception:
        pass

    kind = "channel" if getattr(chat, "type", "") == "channel" else "group"
    title = getattr(chat, "title", None) or str(chat.id)
    res = await afn.run(partners_svc.link_partner_channel, p["id"], str(chat.id), title, kind, 0)
    if not res.get("ok"):
        await msg.reply_text(f"⛔ {esc(res.get('error', 'تعذر الربط'))}", parse_mode="HTML")
        return True
    cid = res["channel_id"]
    await msg.reply_text(
        f"✅ <b><u>تم ربط قناتك بنجاح!</u></b>\n"
        "<blockquote>"
        f"▎📺 القناة: <b>{esc(title)}</b>\n"
        "▎📡 سيبدأ البوت تلقائياً بنشر التحديثات مرفقةً بزر اشتراكك الخاص.\n"
        "▎🔐 يمكنك تفعيل «حارس VIP» أو تعديل الأسواق من إدارة القناة بالأسفل."
        "</blockquote>",
        parse_mode="HTML",
        reply_markup=_partner_nav([
            [kb.btn("⚙️ إدارة قناتي الآن", f"p:ch:{cid}", style="success")],
        ], include_home=True),
    )
    return True


async def handle_partner_brand_input(update: Update, context: ContextTypes.DEFAULT_TYPE, uid: int, text: str) -> bool:
    pid = context.user_data.pop("awaiting_partner_brand", None)
    if not pid:
        return False
    p = await afn.run(partners_svc.get_partner_by_user, uid)
    if not p or int(p["id"]) != int(pid):
        return False
    val = "" if text.strip() == "-" else text.strip()[:48]
    await afn.run(partners_svc.update_partner, p["id"], brand_label=val)
    await update.effective_message.reply_text(
        f"✅ تم حفظ توقيع قناتك: <b>{esc(val or 'الافتراضي')}</b>",
        parse_mode="HTML",
        reply_markup=_partner_nav(),
    )
    return True
