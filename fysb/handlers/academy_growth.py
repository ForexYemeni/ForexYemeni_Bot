"""Telegram UI handlers for Phase 4 (v6.27.0):
  1. Independent Trading Academy (User + Admin toggle/pricing/curriculum/certificates)
  2. Gift Subscriptions & Redeem Codes (any tier PRO/VIP/ROYAL/ACADEMY, any duration)
  3. Tiered Affiliate / Commission System (15%->35%, dashboard, Admin 1-tap payouts)
"""
from __future__ import annotations

import asyncio
import io

from fysb import afn, config, db
from fysb.handlers.common import render
from fysb.services import academy, affiliates, gifts
from fysb.services import audit as audit_log
from fysb.theme import esc
from fysb.ui import keyboards as kb


def _user_full_name_sync(uid: int) -> str:
    with db.conn() as c:
        r = c.execute("SELECT full_name, username FROM users WHERE user_id=?", (int(uid),)).fetchone()
    if not r:
        return f"Trader {uid}"
    return (r["full_name"] or r["username"] or f"Trader {uid}").strip()


async def handle_user_growth(q, uid: int, action: str, context, bot) -> bool:
    """Handle User callbacks for Academy, Gift Redeem & Tiered Affiliates.
    Returns True if handled, False otherwise."""
    # --- 1) INDEPENDENT TRADING ACADEMY ---
    if action == "academy":
        if not await afn.run(academy.is_enabled):
            await render(
                q,
                "🎓 <b><u>أكاديمية ForexYemeni للتداول</u></b>\n"
                "<blockquote>▎🔴 الأكاديمية مغلقة مؤقتاً من الإدارة للصيانة والتطوير — ترقّب إعادة الافتتاح قريباً!</blockquote>",
                kb.user_nav(),
                bot,
            )
            return True
        body = await afn.run(academy.render_academy_home_card, uid)
        prog = await afn.run(academy.user_progress, uid)
        rows_kb = [
            [
                kb.btn(f"📗 المسار المجاني ({prog['free_done']}/{prog['free_total']})", "u:acadtrack:FREE", style="success"),
                kb.btn(f"👑 المسار الاحترافي ({prog['paid_done']}/{prog['paid_total']})", "u:acadtrack:PAID", style="primary"),
            ],
        ]
        if not prog["has_paid"]:
            price = await afn.run(academy.paid_price_usd)
            rows_kb.append([kb.btn(f"🔓 فتح المسار الاحترافي المستقل ({price:g}$)", "u:acadbuy", style="success")])
        cert_row = []
        if prog["cert_ready_free"]:
            cert_row.append(kb.btn("📜 شهادة المسار المجاني", "u:acadcert:FREE", style="success"))
        if prog["cert_ready_paid"]:
            cert_row.append(kb.btn("🏆 الشهادة الملكية الاحترافية", "u:acadcert:PAID", style="success"))
        if cert_row:
            rows_kb.append(cert_row)
        rows_kb.append([kb.btn("🎁 لدي كود هدية للأكاديمية", "u:redeem", style="primary")])
        await render(q, body, kb.user_nav(rows_kb), bot)
        return True

    if action.startswith("acadtrack:"):
        if not await afn.run(academy.is_enabled):
            await render(q, "🔴 الأكاديمية مغلقة مؤقتاً من الإدارة.", kb.user_nav(), bot)
            return True
        track = action.split(":", 1)[1].strip().upper()
        if track not in ("FREE", "PAID"):
            track = "FREE"
        has_paid = await afn.run(academy.has_paid_access, uid)
        if track == "PAID" and not has_paid:
            price = await afn.run(academy.paid_price_usd)
            days = await afn.run(academy.paid_duration_days)
            dur = "مدى الحياة ♾" if days == 0 else f"{days} يوماً"
            body = (
                "👑 <b><u>المسار الاحترافي المدفوع — أكاديمية ForexYemeni</u></b>\n"
                "<blockquote>"
                "▎🔒 هذا المسار مستقل بالكامل عن باقات الإشارات ويضم <b>10 دروس مؤسسية متقدمة</b>:\n"
                "▎• Smart Money Concepts (SMC) &amp; Order Blocks\n"
                "▎• ICT FVG + Silver Bullet + Wyckoff Spring\n"
                "▎• CRT + AMD Power of 3 + Turtle Soup + SK\n"
                "▎• درع الأخبار، ارتباط DXY، وسيكولوجية المحترفين\n"
                "▎🏆 شهادة تخرج ملكية معتمدة PNG برقم تسلسلي فريد.\n\n"
                f"▎💵 رسوم الانضمام المستقل: <b>{price:g} $</b> ({dur})"
                "</blockquote>"
            )
            rows_kb = [
                [kb.btn(f"💳 طلب فتح الأكاديمية ({price:g}$)", "u:acadbuy", style="success")],
                [kb.btn("🎁 تفعيل كود هدية", "u:redeem", style="primary"), kb.btn("📗 المسار المجاني", "u:acadtrack:FREE")],
                [kb.btn("↩️ الأكاديمية", "u:academy")],
            ]
            await render(q, body, kb.user_nav(rows_kb), bot)
            return True

        lessons = await afn.run(academy.list_lessons, track)
        prog = await afn.run(academy.user_progress, uid)
        done = prog["completed_codes"]
        rows_kb = [
            [kb.btn(f"{'✅ ' if l['code'] in done else '📖 '}{l['title'][:46]}", f"u:acadles:{l['code']}", style="success" if l["code"] in done else "primary")]
            for l in lessons
        ]
        rows_kb.append([kb.btn("↩️ الأكاديمية", "u:academy")])
        title = "📗 <b><u>المسار المجاني — الأساسيات (5 دروس)</u></b>" if track == "FREE" else "👑 <b><u>المسار الاحترافي — الـ 13 استراتيجية المؤسسية</u></b>"
        await render(q, f"{title}\n<blockquote>▎اختر أي درس أدناه لقراءته واجتياز اختباره:</blockquote>", kb.user_nav(rows_kb), bot)
        return True

    if action.startswith("acadles:"):
        if not await afn.run(academy.is_enabled):
            await render(q, "🔴 الأكاديمية مغلقة مؤقتاً من الإدارة.", kb.user_nav(), bot)
            return True
        code = action.split(":", 1)[1].strip()
        les = await afn.run(academy.get_lesson, code)
        if not les or not les.get("enabled"):
            await render(q, "الدرس غير متاح.", kb.user_nav([[kb.btn("↩️ الأكاديمية", "u:academy")]]), bot)
            return True
        track = str(les["track"]).upper()
        if track == "PAID" and not await afn.run(academy.has_paid_access, uid):
            await render(q, "🔒 هذا الدرس ضمن المسار الاحترافي المدفوع.", kb.user_nav([[kb.btn("🔓 فتح المسار الاحترافي", "u:acadbuy", style="success")]]), bot)
            return True
        body = await afn.run(academy.render_lesson_card, uid, les)
        rows_kb = []
        quiz = les.get("quiz") or []
        if quiz:
            q0 = quiz[0]
            for idx, opt in enumerate(q0["opts"]):
                rows_kb.append([kb.btn(f"📝 إجابة {idx + 1}: {opt[:42]}", f"u:acadquiz:{code}:{idx}", style="primary")])
        else:
            rows_kb.append([kb.btn("✅ تحديد كمكتمل", f"u:acadquiz:{code}:0", style="success")])
        rows_kb.append([kb.btn("↩️ قائمة الدروس", f"u:acadtrack:{track}"), kb.btn("🎓 الأكاديمية", "u:academy")])
        await render(q, body, kb.user_nav(rows_kb), bot)
        return True

    if action.startswith("acadquiz:"):
        parts = action.split(":")
        if len(parts) < 3:
            return True
        code, opt_idx = parts[1], int(parts[2])
        res = await afn.run(academy.submit_quiz_answer, uid, code, opt_idx)
        if not res.get("ok"):
            await render(q, "تعذر تصحيح الاختبار.", kb.user_nav([[kb.btn("↩️ الأكاديمية", "u:academy")]]), bot)
            return True
        les = res["lesson"]
        track = str(les["track"]).upper()
        if res["correct"]:
            prog = await afn.run(academy.user_progress, uid)
            body = (
                f"🎉 <b><u>إجابة صحيحة! تم إتمام الدرس بنجاح</u></b>\n"
                f"<blockquote>"
                f"▎📚 الدرس: <b>{esc(les['title'])}</b>\n"
                f"▎📈 تقدمك الإجمالي في الأكاديمية: <b>{prog['overall_pct']:.0f}%</b> ({prog['total_done']}/{prog['total_all']})"
                f"</blockquote>"
            )
            rows_kb = [[kb.btn("➡️ الدرس التالي / القائمة", f"u:acadtrack:{track}", style="success")]]
            if (track == "FREE" and prog["cert_ready_free"]) or (track == "PAID" and prog["cert_ready_paid"]):
                rows_kb.append([kb.btn("🏆 إصدار شهادتي الملكية الآن!", f"u:acadcert:{track}", style="success")])
        else:
            body = (
                "❌ <b><u>إجابة غير دقيقة — حاول مجدداً!</u></b>\n"
                "<blockquote>▎💡 راجع الدرس جيداً ثم أعد اختيار الإجابة الصحيحة لتثبيت الفهم وإتمام الدرس.</blockquote>"
            )
            rows_kb = [[kb.btn("🔄 إعادة المحاولة", f"u:acadles:{code}", style="primary")]]
        rows_kb.append([kb.btn("🎓 الأكاديمية", "u:academy")])
        await render(q, body, kb.user_nav(rows_kb), bot)
        return True

    if action.startswith("acadcert:"):
        track = action.split(":", 1)[1].strip().upper()
        if track not in ("FREE", "PAID"):
            track = "FREE"
        prog = await afn.run(academy.user_progress, uid)
        ready = prog["cert_ready_paid"] if track == "PAID" else prog["cert_ready_free"]
        if not ready:
            await render(
                q,
                "🔒 أكمل جميع دروس المسار واجتز اختباراتها أولاً لتفعيل الشهادة الملكية المعتمدة.",
                kb.user_nav([[kb.btn("↩️ الأكاديمية", "u:academy")]]),
                bot,
            )
            return True
        full_name = await afn.run(_user_full_name_sync, uid)
        png = await asyncio.to_thread(academy.render_certificate_png, uid, full_name, track)
        if not png:
            await render(q, "⚠️ تعذر إصدار صورة الشهادة حالياً.", kb.user_nav([[kb.btn("↩️ الأكاديمية", "u:academy")]]), bot)
            return True
        serial = academy.certificate_serial(uid, track)
        cap = (
            f"🏆 <b><u>شهادة تخرج ملكية معتمدة — أكاديمية ForexYemeni</u></b>\n"
            f"▎👤 الطالب: <b>{esc(full_name)}</b>\n"
            f"▎🎓 المسار: <b>{'المسار الاحترافي (13 استراتيجية)' if track == 'PAID' else 'المسار المجاني التأسيسي'}</b>\n"
            f"▎🔐 الرقم التسلسلي: <code>{serial}</code>"
        )
        try:
            await bot.send_photo(chat_id=uid, photo=io.BytesIO(png), caption=cap, parse_mode="HTML")
        except Exception:
            await render(q, "⚠️ تعذر إرسال صورة الشهادة.", kb.user_nav([[kb.btn("↩️ الأكاديمية", "u:academy")]]), bot)
        return True

    if action == "acadbuy":
        price = await afn.run(academy.paid_price_usd)
        days = await afn.run(academy.paid_duration_days)
        dur = "مدى الحياة ♾" if days == 0 else f"{days} يوماً"
        from fysb.services import payments as pay_svc
        pid = await afn.run(pay_svc.create_request, uid, "ACADEMY", price, "MANUAL")
        body = (
            f"💳 <b><u>طلب اشتراك مستقل — المسار الاحترافي للأكاديمية</u></b>\n"
            f"<blockquote>"
            f"▎🧾 رقم الطلب: <code>#{pid}</code>\n"
            f"▎🎓 المنتج: <b>أكاديمية ForexYemeni الاحترافية (مستقلة عن الإشارات)</b>\n"
            f"▎💵 المبلغ المطلوب: <b>{price:g} $</b> · المدة: <b>{dur}</b>\n"
            f"▎📌 يمكنك إرفاق إيصال الدفع من قسم <b>مدفوعاتي</b> أو عبر الدعم لتفعيل وصولك فوراً."
            f"</blockquote>"
        )
        if config.ADMIN_ID:
            try:
                await bot.send_message(
                    config.ADMIN_ID,
                    f"🎓 <b>طلب اشتراك أكاديمية جديد #{pid}</b>\nUID: <code>{uid}</code> · المبلغ: <b>{price:g}$</b>",
                    parse_mode="HTML",
                )
            except Exception:
                pass
        rows_kb = [
            [kb.btn("🧾 متابعة مدفوعاتي", "u:payments", style="success"), kb.btn("🎁 لدي كود هدية", "u:redeem", style="primary")],
            [kb.btn("↩️ الأكاديمية", "u:academy")],
        ]
        await render(q, body, kb.user_nav(rows_kb), bot)
        return True

    # --- 2) GIFT CODE REDEEM ---
    if action == "redeem":
        context.user_data["awaiting_gift_code"] = True
        body = (
            "🎁 <b><u>تفعيل كود اشتراك هدية</u></b>\n"
            "<blockquote>"
            "▎أرسل الآن <b>كود الهدية</b> في رسالة واحدة (مثال: <code>FY-ABCD-1234</code>).\n"
            "▎يدعم تفعيل باقات <b>PRO / VIP / ROYAL</b> أو <b>الأكاديمية الاحترافية</b> تلقائياً."
            "</blockquote>"
        )
        await render(q, body, kb.user_nav([[kb.btn("❌ إلغاء", "u:home", style="danger")]]), bot)
        return True

    # --- 3) TIERED AFFILIATE DASHBOARD ---
    if action == "affiliate":
        bot_u = ""
        try:
            me = await bot.get_me()
            bot_u = getattr(me, "username", "") or ""
        except Exception:
            pass
        body = await afn.run(affiliates.render_affiliate_dashboard, uid, bot_u)
        st = await afn.run(affiliates.affiliate_stats, uid)
        rows_kb = []
        if st["available_usd"] >= st["min_payout_usd"]:
            rows_kb.append([kb.btn(f"💸 طلب سحب أرباحي ({st['available_usd']:,.2f}$)", "u:affpayout", style="success")])
        else:
            rows_kb.append([kb.btn(f"💸 طلب السحب (الحد الأدنى {st['min_payout_usd']:g}$)", "u:affpayout", style="primary")])
        rows_kb.append([kb.btn("🔄 تحديث رصيدي", "u:affiliate", style="primary"), kb.btn("🎁 كود هدية", "u:redeem")])
        await render(q, body, kb.user_nav(rows_kb), bot)
        return True

    if action == "affpayout":
        st = await afn.run(affiliates.affiliate_stats, uid)
        if st["available_usd"] < st["min_payout_usd"]:
            await render(
                q,
                f"⏳ <b>رصيدك المتاح حالياً: {st['available_usd']:,.2f}$</b>\n"
                f"<blockquote>▎الحد الأدنى للسحب هو <b>{st['min_payout_usd']:g}$</b> — شارك رابط دعوتك لزيادة أرباحك حتى 35%!</blockquote>",
                kb.user_nav([[kb.btn("↩️ برنامج الشركاء", "u:affiliate")]]),
                bot,
            )
            return True
        context.user_data["awaiting_aff_wallet"] = True
        await render(
            q,
            f"💸 <b><u>طلب سحب أرباح الشركاء ({st['available_usd']:,.2f}$)</u></b>\n"
            "<blockquote>▎أرسل الآن <b>عنوان محفظة USDT (TRC20)</b> أو وسيلة الاستلام التي ترغب بالسحب إليها:</blockquote>",
            kb.user_nav([[kb.btn("❌ إلغاء", "u:affiliate", style="danger")]]),
            bot,
        )
        return True

    return False


async def handle_admin_growth(q, uid: int, act: str, arg: str, arg2: str, context, bot) -> bool:
    """Handle Admin callbacks for Academy, Gift Codes & Affiliate Payouts.
    Returns True if handled, False otherwise."""
    # --- 1) ADMIN ACADEMY ---
    if act == "academy":
        body = await afn.run(academy.render_admin_academy_card)
        st = await afn.run(academy.admin_stats)
        rows_kb = [
            [kb.btn(
                "🔴 إغلاق الأكاديمية" if st["enabled"] else "🟢 فتح الأكاديمية",
                "a:acadtoggle",
                style="danger" if st["enabled"] else "success",
            )],
            [
                kb.btn(f"{'✅ ' if st['price_usd'] == 29 else ''}$29", "a:acadprice:29"),
                kb.btn(f"{'✅ ' if st['price_usd'] == 49 else ''}$49", "a:acadprice:49"),
                kb.btn(f"{'✅ ' if st['price_usd'] == 79 else ''}$79", "a:acadprice:79"),
                kb.btn(f"{'✅ ' if st['price_usd'] == 99 else ''}$99", "a:acadprice:99"),
            ],
            [
                kb.btn(f"{'✅ ' if st['duration_days'] == 90 else ''}90d", "a:acaddur:90"),
                kb.btn(f"{'✅ ' if st['duration_days'] == 180 else ''}180d", "a:acaddur:180"),
                kb.btn(f"{'✅ ' if st['duration_days'] == 365 else ''}365d", "a:acaddur:365"),
                kb.btn(f"{'✅ ' if st['duration_days'] == 0 else ''}♾ مدى الحياة", "a:acaddur:0", style="success"),
            ],
            [
                kb.btn("📚 إدارة الدروس والمسارات", "a:acadlessons", style="primary"),
                kb.btn("👑 منح وصول لمستخدم", "a:acadgrant", style="success"),
            ],
            [kb.btn("↩️ المالية", "a:hub:finance")],
        ]
        await render(q, body, kb.admin_nav(rows_kb), bot)
        return True

    if act == "acadtoggle":
        cur = await afn.run(academy.is_enabled)
        nxt = await afn.run(academy.set_enabled, not cur)
        await afn.run(audit_log.log, uid, f"academy_enabled:{int(nxt)}")
        return await handle_admin_growth(q, uid, "academy", "", "", context, bot)

    if act == "acadprice":
        try:
            p = float(arg or 49)
            await afn.run(academy.set_pricing, p, None)
            await afn.run(audit_log.log, uid, f"academy_price:{p:g}")
        except Exception:
            pass
        return await handle_admin_growth(q, uid, "academy", "", "", context, bot)

    if act == "acaddur":
        try:
            d = int(arg or 365)
            await afn.run(academy.set_pricing, None, d)
            await afn.run(audit_log.log, uid, f"academy_duration:{d}")
        except Exception:
            pass
        return await handle_admin_growth(q, uid, "academy", "", "", context, bot)

    if act == "acadlessons":
        lessons = await afn.run(academy.list_lessons, None, True)
        rows_kb = [
            [
                kb.btn(
                    f"{'🟢' if l['enabled'] else '⚪'} {'📗' if l['track']=='FREE' else '👑'} {l['title'][:28]}",
                    f"a:acadles_track:{l['code']}",
                    style="success" if l["track"] == "FREE" else "primary",
                ),
                kb.btn("👁 إخفاء" if l["enabled"] else "✅ إظهار", f"a:acadles_tog:{l['code']}", style="danger" if l["enabled"] else "success"),
            ]
            for l in lessons
        ]
        rows_kb.append([kb.btn("↩️ الأكاديمية", "a:academy")])
        await render(
            q,
            "📚 <b><u>إدارة منهج الأكاديمية (15 درساً)</u></b>\n"
            "<blockquote>▎اضغط على اسم الدرس لتبديل مساره (مجاني 📗 ↔ مدفوع 👑)، أو الزر الجانبي لإظهاره/إخفائه.</blockquote>",
            kb.admin_nav(rows_kb),
            bot,
        )
        return True

    if act == "acadles_track":
        await afn.run(academy.toggle_lesson_track, arg)
        return await handle_admin_growth(q, uid, "acadlessons", "", "", context, bot)

    if act == "acadles_tog":
        await afn.run(academy.toggle_lesson_enabled, arg)
        return await handle_admin_growth(q, uid, "acadlessons", "", "", context, bot)

    if act == "acadgrant":
        context.user_data["awaiting_acad_grant"] = True
        await render(
            q,
            "👑 <b><u>منح وصول للمسار الاحترافي بالأكاديمية</u></b>\n"
            "<blockquote>▎أرسل: <code>UID</code> أو <code>UID DAYS</code> (مثال: <code>123456789 365</code> أو <code>123456789 0</code> لمدى الحياة):</blockquote>",
            kb.admin_nav([[kb.btn("❌ إلغاء", "a:academy", style="danger")]]),
            bot,
        )
        return True

    # --- 2) ADMIN GIFTS & REDEEM CODES ---
    if act == "gifts":
        body = await afn.run(gifts.render_admin_gifts_card)
        rows_kb = [
            [
                kb.btn("🎟 كود PRO 7d", "a:giftgen:PRO:7", style="primary"),
                kb.btn("🎟 كود PRO 30d", "a:giftgen:PRO:30", style="primary"),
            ],
            [
                kb.btn("🎟 كود VIP 7d", "a:giftgen:VIP:7", style="success"),
                kb.btn("🎟 كود VIP 30d", "a:giftgen:VIP:30", style="success"),
            ],
            [
                kb.btn("👑 كود ROYAL 7d", "a:giftgen:ROYAL:7", style="success"),
                kb.btn("👑 كود ROYAL 30d", "a:giftgen:ROYAL:30", style="success"),
            ],
            [
                kb.btn("🎓 كود ACADEMY 365d", "a:giftgen:ACADEMY:365", style="primary"),
                kb.btn("🎓 كود ACADEMY ♾", "a:giftgen:ACADEMY:0", style="success"),
            ],
            [
                kb.btn("⚙️ كود بمدة وعدد مخصص", "a:giftcustom", style="primary"),
                kb.btn("🎁 إهداء مباشر لـ UID", "a:giftdirect", style="success"),
            ],
            [kb.btn("↩️ المالية", "a:hub:finance")],
        ]
        await render(q, body, kb.admin_nav(rows_kb), bot)
        return True

    if act == "giftgen":
        target = (arg or "VIP").strip().upper()
        days = int(arg2 or 30)
        res = await afn.run(gifts.create_gift_code, target, days, created_by=uid)
        await afn.run(audit_log.log, uid, f"gift_code_created:{res['code']}:{target}:{days}d")
        dur = "مدى الحياة ♾" if days == 0 else f"{days} يوماً"
        await render(
            q,
            f"🎁 <b><u>تم إصدار كود الهدية بنجاح!</u></b>\n"
            f"<blockquote>"
            f"▎🎟 الكود: <code>{res['code']}</code>\n"
            f"▎🏆 الباقة: <b>{gifts.target_label(target)}</b>\n"
            f"▎⏳ المدة: <b>{dur}</b> · الاستخدامات: <b>1</b>\n"
            f"▎📌 أرسله للمستفيد ليضغط «تفعيل كود هدية» أو يرسل <code>/redeem {res['code']}</code>"
            f"</blockquote>",
            kb.admin_nav([[kb.btn("🎁 كل الهدايا", "a:gifts", style="success")]]),
            bot,
        )
        return True

    if act == "giftcustom":
        context.user_data["awaiting_gift_custom"] = True
        await render(
            q,
            "⚙️ <b><u>إنشاء كود هدية مخصص (أي باقة وأي مدة)</u></b>\n"
            "<blockquote>"
            "▎أرسل السطر بالتنسيق:\n"
            "<code>PLAN DAYS [MAX_USES] [NOTE]</code>\n"
            "▎أمثلة:\n"
            "• <code>VIP 14 5 هدية رمضان</code>\n"
            "• <code>ROYAL 45 1 مسابقة القناة</code>\n"
            "• <code>ACADEMY 180 10 منحة طلابية</code>"
            "</blockquote>",
            kb.admin_nav([[kb.btn("❌ إلغاء", "a:gifts", style="danger")]]),
            bot,
        )
        return True

    if act == "giftdirect":
        context.user_data["awaiting_gift_direct"] = True
        await render(
            q,
            "🎁 <b><u>إهداء اشتراك مباشر لـ UID (أي باقة وأي مدة)</u></b>\n"
            "<blockquote>"
            "▎أرسل السطر بالتنسيق:\n"
            "<code>UID PLAN DAYS [NOTE]</code>\n"
            "▎أمثلة:\n"
            "• <code>123456789 ROYAL 15 هدية ملكية خاصة</code>\n"
            "• <code>123456789 ACADEMY 0 إهداء مدى الحياة</code>"
            "</blockquote>",
            kb.admin_nav([[kb.btn("❌ إلغاء", "a:gifts", style="danger")]]),
            bot,
        )
        return True

    # --- 3) ADMIN AFFILIATES & 1-TAP PAYOUTS ---
    if act == "affiliates":
        body = await afn.run(affiliates.render_admin_affiliates_card)
        pending = await afn.run(affiliates.list_payouts, "PENDING", 8)
        rows_kb = [
            [
                kb.btn(f"✅ اعتماد #{p['id']} ({p['amount_usd']:g}$)", f"a:affapprove:{p['id']}", style="success"),
                kb.btn(f"❌ رفض #{p['id']}", f"a:affreject:{p['id']}", style="danger"),
            ]
            for p in pending
        ]
        rows_kb.append([kb.btn("🔄 تحديث", "a:affiliates", style="primary"), kb.btn("↩️ المالية", "a:hub:finance")])
        await render(q, body, kb.admin_nav(rows_kb), bot)
        return True

    if act in ("affapprove", "affreject"):
        pid = int(arg or 0)
        if act == "affapprove":
            row = await afn.run(affiliates.approve_payout, pid, "Approved by Admin")
            if row:
                await afn.run(audit_log.log, uid, f"affiliate_payout_approved:{pid}:{row['amount_usd']}")
                try:
                    await bot.send_message(
                        row["user_id"],
                        f"✅ <b><u>تم اعتماد وتحويل طلب سحب أرباحك #{pid}!</u></b>\n"
                        f"<blockquote>▎💵 المبلغ: <b>{row['amount_usd']:,.2f} $</b>\n"
                        f"▎💳 المحفظة: <code>{esc(row['wallet_address'])}</code></blockquote>",
                        parse_mode="HTML",
                    )
                except Exception:
                    pass
        else:
            row = await afn.run(affiliates.reject_payout, pid, "Rejected by Admin")
            if row:
                await afn.run(audit_log.log, uid, f"affiliate_payout_rejected:{pid}")
                try:
                    await bot.send_message(
                        row["user_id"],
                        f"❌ <b>تم رفض طلب السحب #{pid}</b> وأُعيد المبلغ ({row['amount_usd']:,.2f}$) إلى رصيدك المتاح — تواصل مع الدعم لمزيد من التفاصيل.",
                        parse_mode="HTML",
                    )
                except Exception:
                    pass
        return await handle_admin_growth(q, uid, "affiliates", "", "", context, bot)

    return False


async def handle_growth_text_input(update, context, uid: int, text: str) -> bool:
    """Process one-shot text prompts for Phase 4 (gift code, payout wallet, admin grants/gifts)."""
    bot = context.bot
    ud = context.user_data

    # User: Redeem Gift Code
    if ud.pop("awaiting_gift_code", None):
        res = await afn.run(gifts.redeem_code, uid, text)
        if not res.get("ok"):
            await update.message.reply_text(
                f"❌ <b>تعذر تفعيل الكود:</b> {esc(res.get('error', 'كود غير صالح'))}",
                reply_markup=kb.user_nav([[kb.btn("🔄 تجربة كود آخر", "u:redeem", style="primary")]]),
                parse_mode="HTML",
            )
            return True
        card = gifts.render_gift_received_card(
            res["target_plan"], res["duration_days"], note=res.get("note", ""), code=res["code"],
        )
        await update.message.reply_text(card, reply_markup=kb.user_nav(), parse_mode="HTML")
        if config.ADMIN_ID and uid != config.ADMIN_ID:
            try:
                await bot.send_message(
                    config.ADMIN_ID,
                    f"🎁 <b>استرداد كود هدية:</b> المستخدم <code>{uid}</code> فعّل الكود <code>{res['code']}</code> "
                    f"({res['target_plan']} · {res['duration_days']}d)",
                    parse_mode="HTML",
                )
            except Exception:
                pass
        return True

    # User: Affiliate Payout Wallet
    if ud.pop("awaiting_aff_wallet", None):
        res = await afn.run(affiliates.request_payout, uid, text)
        if not res.get("ok"):
            await update.message.reply_text(
                f"❌ {esc(res.get('error', 'تعذر تسجيل الطلب'))}",
                reply_markup=kb.user_nav([[kb.btn("↩️ برنامج الشركاء", "u:affiliate")]]),
                parse_mode="HTML",
            )
            return True
        await update.message.reply_text(
            f"✅ <b><u>تم استلام طلب سحب أرباحك #{res['payout_id']}</u></b>\n"
            f"<blockquote>▎💵 المبلغ: <b>{res['amount_usd']:,.2f} $</b>\n"
            f"▎💳 المحفظة: <code>{esc(res['wallet_address'])}</code>\n"
            f"▎⏳ ستتم مراجعته واعتماده من الإدارة قريباً.</blockquote>",
            reply_markup=kb.user_nav([[kb.btn("🤝 برنامج الشركاء", "u:affiliate")]]),
            parse_mode="HTML",
        )
        if config.ADMIN_ID:
            try:
                await bot.send_message(
                    config.ADMIN_ID,
                    f"💸 <b>طلب سحب عمولات شركاء #{res['payout_id']}</b>\n"
                    f"UID: <code>{uid}</code> · المبلغ: <b>{res['amount_usd']:,.2f}$</b>\n"
                    f"المحفظة: <code>{esc(res['wallet_address'])}</code>",
                    reply_markup=kb.InlineKeyboardMarkup([[
                        kb.btn("✅ اعتماد فوري", f"a:affapprove:{res['payout_id']}", style="success"),
                        kb.btn("❌ رفض", f"a:affreject:{res['payout_id']}", style="danger"),
                    ]]),
                    parse_mode="HTML",
                )
            except Exception:
                pass
        return True

    if uid != config.ADMIN_ID:
        return False

    # Admin: Grant Paid Academy
    if ud.pop("awaiting_acad_grant", None):
        parts = text.strip().split()
        try:
            target_uid = int(parts[0])
            days = int(parts[1]) if len(parts) > 1 else None
        except Exception:
            await update.message.reply_text("❌ صيغة غير صحيحة. أرسل: <code>UID [DAYS]</code>", parse_mode="HTML")
            return True
        res = await afn.run(academy.grant_paid_access, target_uid, days, "ADMIN_GRANT", uid)
        await afn.run(audit_log.log, uid, f"academy_granted:{target_uid}:{res['duration_days']}d")
        dur_txt = "مدى الحياة ♾" if res["lifetime"] else f"{res['duration_days']} يوماً"
        try:
            await bot.send_message(
                target_uid,
                gifts.render_gift_received_card("ACADEMY", res["duration_days"], note="منحة إدارية من أكاديمية ForexYemeni"),
                parse_mode="HTML",
            )
        except Exception:
            pass
        await update.message.reply_text(
            f"✅ تم منح المسار الاحترافي للأكاديمية للمستخدم <code>{target_uid}</code> ({dur_txt}).",
            reply_markup=kb.admin_nav([[kb.btn("🎓 الأكاديمية", "a:academy")]]),
            parse_mode="HTML",
        )
        return True

    # Admin: Custom Gift Code (`PLAN DAYS [MAX_USES] [NOTE]`)
    if ud.pop("awaiting_gift_custom", None):
        parts = text.strip().split(maxsplit=3)
        try:
            target_plan = gifts.normalize_target(parts[0])
            days = int(parts[1])
            max_uses = int(parts[2]) if len(parts) > 2 else 1
            note = parts[3] if len(parts) > 3 else ""
        except Exception:
            await update.message.reply_text(
                "❌ الصيغة: <code>PLAN DAYS [MAX_USES] [NOTE]</code> (مثال: <code>VIP 14 5 هدية</code>)",
                parse_mode="HTML",
            )
            return True
        res = await afn.run(gifts.create_gift_code, target_plan, days, max_uses=max_uses, note=note, created_by=uid)
        await afn.run(audit_log.log, uid, f"gift_custom:{res['code']}:{target_plan}:{days}d:uses={max_uses}")
        await update.message.reply_text(
            f"🎁 <b>تم إصدار الكود المخصص!</b>\n"
            f"<blockquote>▎🎟 الكود: <code>{res['code']}</code>\n"
            f"▎🏆 الهدف: <b>{gifts.target_label(target_plan)}</b> · المدة: <b>{days}d</b> · الاستخدامات: <b>{max_uses}</b></blockquote>",
            reply_markup=kb.admin_nav([[kb.btn("🎁 الهدايا والأكواد", "a:gifts")]]),
            parse_mode="HTML",
        )
        return True

    # Admin: Direct UID Gift (`UID PLAN DAYS [NOTE]`)
    if ud.pop("awaiting_gift_direct", None):
        parts = text.strip().split(maxsplit=3)
        try:
            target_uid = int(parts[0])
            target_plan = gifts.normalize_target(parts[1])
            days = int(parts[2])
            note = parts[3] if len(parts) > 3 else ""
        except Exception:
            await update.message.reply_text(
                "❌ الصيغة: <code>UID PLAN DAYS [NOTE]</code> (مثال: <code>123456789 ROYAL 30 هدية</code>)",
                parse_mode="HTML",
            )
            return True
        res = await afn.run(gifts.gift_direct, target_uid, target_plan, days, granted_by=uid, note=note)
        await afn.run(audit_log.log, uid, f"gift_direct:{target_uid}:{target_plan}:{days}d")
        try:
            await bot.send_message(
                target_uid,
                gifts.render_gift_received_card(target_plan, days, note=note),
                parse_mode="HTML",
            )
        except Exception:
            pass
        await update.message.reply_text(
            f"🎁 <b>تم إهداء المستخدم <code>{target_uid}</code> بنجاح!</b>\n"
            f"<blockquote>▎🏆 {gifts.target_label(target_plan)} · المدة: <b>{'مدى الحياة ♾' if days == 0 else f'{days} يوماً'}</b></blockquote>",
            reply_markup=kb.admin_nav([[kb.btn("🎁 الهدايا والأكواد", "a:gifts")]]),
            parse_mode="HTML",
        )
        return True

    return False
