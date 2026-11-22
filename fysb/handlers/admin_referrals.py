"""Admin «نظام الإحالات» — the royal referral & conversion command center.

Every campaign is a real deep-link funnel:
  https://t.me/<bot>?start=ref<CODE>
The panel tracks BOTH royal counts per campaign — invited vs actually
subscribed — against the launch targets (default 50 invited → 5 real
subscribers), with luxury progress bars and a per-campaign invitee ledger.
"""
from fysb import config, db
from fysb.handlers.common import render
from fysb.services import audit as audit_log
from fysb.services import referrals as ref
from fysb.theme import bar, esc
from fysb.ui import keyboards as kb

TARGET_INVITED_CHOICES = [10, 25, 50, 100, 200]
TARGET_CONVERTED_CHOICES = [1, 3, 5, 10, 25]


def _invitee_badge(row: dict) -> str:
    if row["converted"]:
        return f"🟢 <b>مشترك فعلاً</b> ({esc(row['plan_code'] or '—')})"
    return "🔵 دعوة مفتوحة"


async def admin_refs(q, uid, context, bot):
    st = ref.overall_stats()
    camps = ref.list_campaigns()
    body = [
        "🤝 <b><u>نظام الإحالات والتحويل الملكي</u></b>",
        "▎كل حملة = رابط دعوة فاخر يتبع من فتح البوت ومن اشترك فعلاً بالباقات.",
        "",
        "<blockquote>"
        f"▎👥 إجمالي المدعوين: <b>{st['invited']}</b>\n"
        f"▎💎 المشتركون الفعليون: <b>{st['converted']}</b>\n"
        f"▎📈 نسبة التحويل الحقيقية: <b>{st['rate']:.1f}%</b>\n"
        f"▎🗂 الحملات: <b>{st['campaigns']}</b>"
        "</blockquote>",
        "",
    ]
    rows = []
    for c in camps[:8]:
        mark = "🟢" if c["converted_met"] else ("🟡" if c["invited"] else "⚪")
        body.append(f"▎{mark} <b>{esc(c['title'])}</b> · مدعوون <b>{c['invited']}</b>/{c['target_invited']}"
                    f" · مشتركون <b>{c['converted']}</b>/{c['target_converted']} ({c['rate']:.1f}%)")
        rows.append([kb.btn(f"⚙️ تفاصيل: {esc(c['title'])}", f"a:refview:{c['id']}")])
    if not camps:
        body.append("▎لا توجد حملات بعد — أنشئ أول حملة دعوة ملكية.")
    rows.append([kb.btn("🟡 حملة دعوة جديدة", "a:refnew"),
                 kb.btn("🔵 تحديث", "a:refs")])
    from fysb.theme import header
    await render(q, header("نظام الإحالات", "الدعوات والمشتركين الفعليين — لا تقديرات") + "\n" + "\n".join(body),
                 kb.admin_nav(rows), bot)


async def admin_ref_new(q, uid, context, bot):
    code = ref.new_code()
    camp = ref.ensure_campaign(code, f"حملة إحالات #{code}", owner_id=config.ADMIN_ID)
    audit_log.log(uid, f"referral_campaign_created:{camp['id']}")
    await admin_ref_view(q, uid, context, bot, camp["id"])


async def admin_ref_view(q, uid, context, bot, cid: int):
    camp = ref.get_campaign(cid)
    if not camp:
        await render(q, "الحملة غير موجودة.", kb.admin_nav([[kb.btn("🤝 الإحالات", "a:refs")]]), bot)
        return
    st = ref.campaign_stats(cid)
    link = ref.link_for(camp["code"])
    body = [
        f"🎯 <b><u>{esc(camp['title'])}</u></b> · {'🟢 نشطة' if camp['enabled'] else '⚪ متوقفة'}",
        "",
        "<blockquote>"
        f"▎🔗 رابط الدعوة (انسخه): <code>{esc(link)}</code>\n"
        f"▎🔑 رمز الحملة: <code>{esc(camp['code'])}</code>"
        "</blockquote>",
        "",
        "📊 <b>التقدم نحو الأهداف الملكية:</b>",
        "<blockquote>"
        f"▎👥 الدعوات: <b>{st['invited']}</b> من <b>{st['target_invited']}</b> {bar(st['invited'], st['target_invited'])}\n"
        f"▎💎 المشتركون الفعليون: <b>{st['converted']}</b> من <b>{st['target_converted']}</b> {bar(st['converted'], st['target_converted'])}\n"
        f"▎📈 نسبة التحويل: <b>{st['rate']:.1f}%</b>"
        "</blockquote>",
        "",
    ]
    if st["converted_met"]:
        body.append("✨ <b>تم تحقيق هدف المشتركين الملكي! 🥇</b>")
    rows = [
        [kb.btn("👥 قائمة المدعوين", f"a:reflist:{cid}"), kb.btn("🔵 تحديث", f"a:refview:{cid}")],
        [kb.btn(f"{'🟠 إيقاف الحملة' if camp['enabled'] else '🟢 تفعيل الحملة'}", f"a:reftoggle:{cid}")],
        [kb.btn("🎯 هدف الدعوات", f"a:reftgti:{cid}"), kb.btn("💎 هدف المشتركين", f"a:reftgtc:{cid}")],
        [kb.btn("🔴 حذف الحملة", f"a:refdel:{cid}"), kb.btn("🤝 كل الحملات", "a:refs")],
    ]
    await render(q, "\n".join(body), kb.admin_nav(rows), bot)


async def admin_ref_toggle(q, uid, context, bot, cid: int):
    camp = ref.get_campaign(cid)
    if camp:
        ref.set_enabled(cid, 0 if camp["enabled"] else 1)
        audit_log.log(uid, f"referral_toggle:{cid}:{'on' if not camp['enabled'] else 'off'}")
    await admin_ref_view(q, uid, context, bot, cid)


async def admin_ref_delete(q, uid, context, bot, cid: int):
    ref.delete_campaign(cid)
    audit_log.log(uid, f"referral_deleted:{cid}")
    await admin_refs(q, uid, context, bot)


async def admin_ref_targets(q, uid, context, bot, cid: int, kind: str):
    camp = ref.get_campaign(cid)
    if not camp:
        return
    label = "الدعوات" if kind == "invited" else "المشتركين الفعليين"
    choices = TARGET_INVITED_CHOICES if kind == "invited" else TARGET_CONVERTED_CHOICES
    rows = [[kb.btn(f"🎯 {n}", f"a:reftgtset:{cid}:{kind}:{n}") for n in choices]]
    await render(q, f"🎯 <b>اختر هدف {label} للحملة</b>\n"
                f"<blockquote>▎المعيار الملكي الافتراضي: دعوة <b>50</b> شخصاً ← <b>5</b> مشتركين فعلاً على الأقل.</blockquote>",
                kb.admin_nav(rows + [[kb.btn("↩️ الحملة", f"a:refview:{cid}")]]), bot)


async def admin_ref_targets_set(q, uid, context, bot, cid: int, kind: str, value: int):
    if kind == "invited":
        ref.set_targets(cid, target_invited=value)
    else:
        ref.set_targets(cid, target_converted=value)
    audit_log.log(uid, f"referral_target:{cid}:{kind}:{value}")
    await admin_ref_view(q, uid, context, bot, cid)


async def admin_ref_list(q, uid, context, bot, cid: int, page: int = 0):
    camp = ref.get_campaign(cid)
    if not camp:
        return await admin_refs(q, uid, context, bot)
    per = 10
    rows = ref.invitees(cid, limit=per, offset=page * per)
    total = ref.invitees_total(cid)
    pages = max(1, (total + per - 1) // per)
    body = [f"👥 <b><u>قائمة المدعوين — {esc(camp['title'])}</u></b>",
            f"▎الإجمالي: <b>{total}</b> مدعو",
            ""]
    if not rows:
        body.append("▎لا توجد دعوات مسجلة بعد — شارك رابط الحملة لتبدأ القياس.")
    for r in rows:
        name = r["full_name"] or (f"@{r['username']}" if r["username"] else str(r["invitee_id"]))
        body.append(f"▎👤 <b>{esc(name[:28])}</b> · {_invitee_badge(r)}\n"
                    f"▎   🕒 {esc((r['invited_at'] or '')[:19].replace('T', ' ') or '—')}")
    nav = []
    if pages > 1:
        prev_b = kb.btn("◀️ السابق", f"a:reflist:{cid}:{max(0, page - 1)}")
        next_b = kb.btn("التالي ▶️", f"a:reflist:{cid}:{min(pages - 1, page + 1)}")
        nav.append([prev_b, kb.btn(f"📄 {page + 1}/{pages}", "a:refnoop"), next_b])
    await render(q, "\n".join(body),
                 kb.admin_nav(nav + [[kb.btn("↩️ الحملة", f"a:refview:{cid}")]]), bot)
