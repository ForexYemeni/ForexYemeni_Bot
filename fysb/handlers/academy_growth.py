"""Telegram UI handlers for Phase 4 & v7.1.0 Royal Academy Studio:
  1. Independent Trading Academy:
     - Intensive 15-lesson curriculum (5 FREE + 10 PAID) with multi-question
       interactive quizzes & pedagogical feedback
     - Full media delivery (Video / Audio / Voice / PDF / Photo / Text)
     - Identical payment methods to Subscription Plans (USDT On-Chain Fast Lane
       + Yemeni wallets & manual methods + Copy button + Receipt upload + Gift codes)
     - Complete Admin Academy Studio: inspect/preview every lesson, add new lessons
       (Text/Video/Audio/Voice/PDF/Photo), attach/replace media, edit content/quiz,
       delete lessons, custom price & custom duration
  2. Gift Subscriptions & Redeem Codes (PRO/VIP/ROYAL/ACADEMY, any duration)
  3. Tiered Affiliate / Commission System (15%->35%, dashboard, Admin 1-tap payouts)
"""
from __future__ import annotations

import asyncio
import io

from fysb import afn, config, db
from fysb.handlers.common import render
from fysb.services import academy, affiliates, gifts
from fysb.services import audit as audit_log
from fysb.services import payments as payments_service
from fysb.theme import esc
from fysb.ui import keyboards as kb

_NUM_EMOJI = ("1️⃣", "2️⃣", "3️⃣", "4️⃣", "5️⃣")


def _user_full_name_sync(uid: int) -> str:
    with db.conn() as c:
        r = c.execute("SELECT full_name, username FROM users WHERE user_id=?", (int(uid),)).fetchone()
    if not r:
        return f"Trader {uid}"
    return (r["full_name"] or r["username"] or f"Trader {uid}").strip()


async def send_lesson_media(bot, chat_id: int, les: dict) -> bool:
    """Send a lesson's native Telegram media attachment (VIDEO / AUDIO / VOICE / PDF / PHOTO)."""
    fid = str(les.get("media_file_id") or "").strip()
    mtype = str(les.get("media_type") or "TEXT").upper()
    if not fid or mtype == "TEXT":
        return False
    cap_extra = f"\n{esc(les['media_caption'])}" if les.get("media_caption") else ""
    caption = f"🎓 <b>{esc(les['title'])}</b>\n<i>{academy.media_badge(mtype)}</i>{cap_extra}"[:1000]
    try:
        if mtype == "VIDEO":
            await bot.send_video(chat_id=chat_id, video=fid, caption=caption, parse_mode="HTML")
        elif mtype == "AUDIO":
            await bot.send_audio(chat_id=chat_id, audio=fid, caption=caption, parse_mode="HTML")
        elif mtype == "VOICE":
            await bot.send_voice(chat_id=chat_id, voice=fid, caption=caption, parse_mode="HTML")
        elif mtype == "PDF":
            await bot.send_document(chat_id=chat_id, document=fid, caption=caption, parse_mode="HTML")
        elif mtype == "PHOTO":
            await bot.send_photo(chat_id=chat_id, photo=fid, caption=caption, parse_mode="HTML")
        else:
            return False
        return True
    except Exception:
        return False


def _parse_quiz_line(raw: str) -> list[dict] | None:
    """Parse Admin quiz input lines: `السؤال | خيار1 | خيار2 | خيار3 | رقم_الصحيح [| تفسير]`"""
    out: list[dict] = []
    for line in (raw or "").strip().splitlines():
        line = line.strip()
        if not line:
            continue
        parts = [p.strip() for p in line.split("|")]
        if len(parts) < 4:
            continue
        q_txt = parts[0]
        # Last numeric token indicates 1-based correct index; optional explanation after it
        exp = ""
        ans_idx = 0
        opts: list[str] = []
        if len(parts) >= 6 and parts[4].isdigit():
            opts = parts[1:4]
            ans_idx = max(0, min(len(opts) - 1, int(parts[4]) - 1))
            exp = parts[5]
        elif len(parts) >= 5 and parts[-1].isdigit():
            opts = parts[1:-1]
            ans_idx = max(0, min(len(opts) - 1, int(parts[-1]) - 1))
        elif len(parts) >= 6 and parts[-2].isdigit():
            opts = parts[1:-2]
            ans_idx = max(0, min(len(opts) - 1, int(parts[-2]) - 1))
            exp = parts[-1]
        else:
            opts = parts[1:]
            ans_idx = 0
        if q_txt and len(opts) >= 2:
            out.append({"q": q_txt, "opts": opts[:4], "ans": ans_idx, "exp": exp})
    return out or None


async def _render_academy_checkout(q, uid: int, context, bot):
    """Show the EXACT SAME payment methods as subscription plans (USDT Blockchain Fast Lane
    + all enabled manual payment methods + Gift code)."""
    price = await afn.run(academy.paid_price_usd)
    days = await afn.run(academy.paid_duration_days)
    dur = "مدى الحياة ♾" if days == 0 else f"{days} يوماً"
    methods = await afn.run(payments_service.list_methods, enabled_only=True)
    chains = await afn.run(payments_service.configured_chains)

    body = (
        "👑 <b><u>الاشتراك في المسار الاحترافي — أكاديمية ForexYemeni الملكية</u></b>\n"
        "<blockquote>"
        "▎🎓 <b>المنتج:</b> المسار الاحترافي الكامل (مستقل بالكامل عن باقات الإشارات)\n"
        "▎📚 <b>المحتوى:</b> 10+ دروس مؤسسية مكثفة (SMC · ICT · Silver Bullet · Wyckoff · CRT · AMD · Turtle Soup · SK · DXY &amp; News · Psychology) + مرفقات الإدارة (فيديو/صوت/PDF)\n"
        "▎📝 <b>الاختبارات:</b> اختبارات تطبيقية تفاعلية لكل درس + تصحيح وتفسير فوري\n"
        "▎🏆 <b>الشهادة:</b> شهادة تخرج ملكية معتمدة PNG برقم تسلسلي فريد\n"
        f"▎💵 <b>سعر الدورة:</b> <b>{price:g} USD</b> · ⏳ المدة: <b>{dur}</b>"
        "</blockquote>"
    )
    rows_kb = []
    if chains:
        body += (
            "\n\n⛓ <b><u>التفعيل الفوري بالبلوكيشن — المحافظ الموثقة</u></b>\n"
            "<blockquote>"
            "▎⚡ اختر شبكة USDT المناسبة وأرسل الـ TXID — يتم التحقق على البلوكيشن وفتح الأكاديمية فوراً 100%."
            "</blockquote>"
        )
        for ch in chains:
            rows_kb.append([kb.btn(f"⚡ اشتراك فوري — USDT {ch}", f"u:acadusdt:{ch}", style="success")])
    if methods:
        body += "\n\n💳 <b><u>وسائل الدفع المفعّلة (نفس طرق دفع الباقات):</u></b>"
        for m in methods:
            rows_kb.append([kb.btn(f"🧾 اشتراك عبر {esc(m['title'])}", f"u:acadpay:{m['id']}", style="primary")])
    else:
        rows_kb.append([kb.btn("🧾 طلب اشتراك (دفع يدوي)", "u:acadpay:0", style="primary")])
    rows_kb.append([kb.btn("🎁 لدي كود هدية للأكاديمية", "u:redeem", style="success")])
    rows_kb.append([kb.btn("📗 المسار المجاني", "u:acadtrack:FREE"), kb.btn("↩️ الأكاديمية", "u:academy")])
    await render(q, body, kb.user_nav(rows_kb), bot)


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
            await _render_academy_checkout(q, uid, context, bot)
            return True

        lessons = await afn.run(academy.list_lessons, track)
        prog = await afn.run(academy.user_progress, uid)
        done = prog["completed_codes"]
        rows_kb = [
            [
                kb.btn(
                    f"{'✅ ' if l['code'] in done else academy.media_icon(l['media_type']) + ' '}{l['title'][:44]}",
                    f"u:acadles:{l['id']}",
                    style="success" if l["code"] in done else "primary",
                )
            ]
            for l in lessons
        ]
        rows_kb.append([kb.btn("↩️ الأكاديمية", "u:academy")])
        title = (
            f"📗 <b><u>المسار المجاني التأسيسي ({len(lessons)} دروس مكثفة)</u></b>"
            if track == "FREE"
            else f"👑 <b><u>المسار الاحترافي المؤسسي ({len(lessons)} دروس مكثفة)</u></b>"
        )
        await render(
            q,
            f"{title}\n<blockquote>▎اختر أي درس أدناه لقراءة الشرح المكثف ومشاهدة مرفقاته ثم اجتياز اختباره التفاعلي:</blockquote>",
            kb.user_nav(rows_kb),
            bot,
        )
        return True

    if action.startswith("acadmedia:"):
        if not await afn.run(academy.is_enabled):
            await render(q, "🔴 الأكاديمية مغلقة مؤقتاً من الإدارة.", kb.user_nav(), bot)
            return True
        ref = action.split(":", 1)[1].strip()
        les = await afn.run(academy.get_lesson_any, ref)
        if not les or not les.get("enabled"):
            await render(q, "الدرس غير متاح.", kb.user_nav([[kb.btn("↩️ الأكاديمية", "u:academy")]]), bot)
            return True
        if str(les["track"]).upper() == "PAID" and not await afn.run(academy.has_paid_access, uid):
            await _render_academy_checkout(q, uid, context, bot)
            return True
        ok = await send_lesson_media(bot, uid, les)
        if not ok:
            await render(q, "⚠️ لا يوجد مرفق وسائط لهذا الدرس حالياً.", kb.user_nav([[kb.btn("↩️ الدرس", f"u:acadles:{les['id']}")]]), bot)
        return True

    if action.startswith("acadles:"):
        if not await afn.run(academy.is_enabled):
            await render(q, "🔴 الأكاديمية مغلقة مؤقتاً من الإدارة.", kb.user_nav(), bot)
            return True
        parts = action.split(":")
        ref = parts[1].strip() if len(parts) > 1 else ""
        q_idx = int(parts[2]) if len(parts) > 2 and parts[2].isdigit() else 0
        les = await afn.run(academy.get_lesson_any, ref)
        if not les or not les.get("enabled"):
            await render(q, "الدرس غير متاح.", kb.user_nav([[kb.btn("↩️ الأكاديمية", "u:academy")]]), bot)
            return True
        track = str(les["track"]).upper()
        if track == "PAID" and not await afn.run(academy.has_paid_access, uid):
            await _render_academy_checkout(q, uid, context, bot)
            return True
        # Deliver video/audio/voice/PDF automatically on initial lesson open (q_idx == 0)
        if q_idx == 0 and les.get("media_file_id"):
            await send_lesson_media(bot, uid, les)

        body = await afn.run(academy.render_lesson_card, uid, les, q_idx, False)
        rows_kb = []
        if les.get("media_file_id"):
            rows_kb.append([kb.btn(f"{academy.media_icon(les['media_type'])} إعادة إرسال المرفق ({academy.media_badge(les['media_type'])})", f"u:acadmedia:{les['id']}", style="success")])
        quiz = les.get("quiz") or []
        if quiz:
            safe_qi = min(max(0, q_idx), len(quiz) - 1)
            qcur = quiz[safe_qi]
            for idx, opt in enumerate(qcur.get("opts") or []):
                em = _NUM_EMOJI[idx] if idx < len(_NUM_EMOJI) else f"{idx + 1})"
                rows_kb.append([
                    kb.btn(
                        f"{em} {opt[:42]}",
                        f"u:acadquiz:{les['id']}:{safe_qi}:{idx}",
                        style="primary",
                    )
                ])
        else:
            rows_kb.append([kb.btn("✅ تأكيد إتمام الدرس", f"u:acadquiz:{les['id']}:0:0", style="success")])
        rows_kb.append([kb.btn("↩️ قائمة الدروس", f"u:acadtrack:{track}"), kb.btn("🎓 الأكاديمية", "u:academy")])
        await render(q, body, kb.user_nav(rows_kb), bot)
        return True

    if action.startswith("acadquiz:"):
        parts = action.split(":")
        if len(parts) < 3:
            return True
        ref = parts[1].strip()
        if len(parts) >= 4 and parts[2].isdigit() and parts[3].isdigit():
            q_idx, opt_idx = int(parts[2]), int(parts[3])
        else:
            q_idx, opt_idx = 0, int(parts[2]) if parts[2].isdigit() else 0
        res = await afn.run(academy.submit_quiz_answer, uid, ref, opt_idx, q_idx)
        if not res.get("ok"):
            await render(q, "تعذر تصحيح الاختبار.", kb.user_nav([[kb.btn("↩️ الأكاديمية", "u:academy")]]), bot)
            return True
        les = res["lesson"]
        track = str(les["track"]).upper()
        exp_block = f"\n▎💡 <b>التفسير العلمي:</b> {esc(res['explanation'])}" if res.get("explanation") else ""

        if res["correct"]:
            if res["next_q_idx"] is not None:
                nxt_q = res["next_q_idx"]
                body = (
                    f"✅ <b><u>إجابة صحيحة! ({q_idx + 1} من {res['total_questions']})</u></b>\n"
                    f"<blockquote>"
                    f"▎📚 الدرس: <b>{esc(les['title'])}</b>"
                    f"{exp_block}\n"
                    f"▎🔥 ممتاز! انتقل الآن إلى <b>السؤال {nxt_q + 1} من {res['total_questions']}</b> لإتمام الدرس."
                    f"</blockquote>"
                )
                rows_kb = [
                    [kb.btn(f"➡️ السؤال التالي ({nxt_q + 1}/{res['total_questions']})", f"u:acadles:{les['id']}:{nxt_q}", style="success")],
                    [kb.btn("↩️ قائمة الدروس", f"u:acadtrack:{track}")],
                ]
            else:
                prog = await afn.run(academy.user_progress, uid)
                nxt_les = await afn.run(academy.next_lesson_in_track, les["id"])
                body = (
                    f"🎉 <b><u>أحسنت! تم اجتياز الاختبار وإتمام الدرس 100%</u></b>\n"
                    f"<blockquote>"
                    f"▎📚 الدرس المكتمل: <b>{esc(les['title'])}</b>"
                    f"{exp_block}\n"
                    f"▎📈 تقدمك الإجمالي في الأكاديمية: <b>{prog['overall_pct']:.0f}%</b> ({prog['total_done']}/{prog['total_all']})"
                    f"</blockquote>"
                )
                rows_kb = []
                if nxt_les:
                    rows_kb.append([kb.btn(f"➡️ الدرس التالي: {nxt_les['title'][:34]}", f"u:acadles:{nxt_les['id']}", style="success")])
                rows_kb.append([kb.btn("📚 قائمة دروس المسار", f"u:acadtrack:{track}", style="primary")])
                if (track == "FREE" and prog["cert_ready_free"]) or (track == "PAID" and prog["cert_ready_paid"]):
                    rows_kb.append([kb.btn("🏆 إصدار شهادتي الملكية الآن!", f"u:acadcert:{track}", style="success")])
        else:
            body = (
                f"❌ <b><u>إجابة غير دقيقة — حاول مجدداً! (السؤال {q_idx + 1} من {res['total_questions']})</u></b>\n"
                f"<blockquote>"
                f"▎📚 الدرس: <b>{esc(les['title'])}</b>\n"
                f"▎💡 راجع القاعدة في الدرس بعناية ثم أعد اختيار الإجابة الصحيحة لتثبيت الفهم المؤسسي."
                f"</blockquote>"
            )
            rows_kb = [
                [kb.btn(f"🔄 إعادة محاولة السؤال {q_idx + 1}", f"u:acadles:{les['id']}:{q_idx}", style="primary")],
                [kb.btn("📖 قراءة الدرس من البداية", f"u:acadles:{les['id']}:0")],
            ]
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
        await _render_academy_checkout(q, uid, context, bot)
        return True

    if action.startswith("acadusdt:"):
        chain = action.split(":", 1)[1].strip().upper()
        chains = await afn.run(payments_service.configured_chains)
        if chain not in chains:
            await render(
                q,
                "⛓ هذه الشبكة غير موثقة حالياً — اختر شبكة أخرى أو وسيلة دفع مفعّلة.",
                kb.user_nav([[kb.btn("↩️ طرق دفع الأكاديمية", "u:acadbuy")]]),
                bot,
            )
            return True
        price = await afn.run(academy.paid_price_usd)
        pid = await afn.run(payments_service.create_request, uid, "ACADEMY", price, f"USDT-{chain}")
        req = await afn.run(payments_service.get_request, pid)
        from fysb.handlers.user_pages import _render_chain_payment
        await _render_chain_payment(q, bot, req, chain)
        return True

    if action.startswith("acadpay:"):
        try:
            mid = int(action.split(":", 1)[1].strip())
        except ValueError:
            mid = 0
        price = await afn.run(academy.paid_price_usd)
        days = await afn.run(academy.paid_duration_days)
        dur = "مدى الحياة ♾" if days == 0 else f"{days} يوماً"
        method = await afn.run(payments_service.get_method, mid) if mid else None
        pay_req = await afn.run(
            payments_service.create_request,
            uid,
            "ACADEMY",
            price,
            method["title"] if method else "MANUAL",
        )
        context.user_data["awaiting_receipt"] = pay_req
        body = [
            f"🧾 <b>طلب اشتراك أكاديمية #{pay_req}</b>",
            "🎓 المنتج: <b>أكاديمية ForexYemeni الملكية (المسار الاحترافي المستقل)</b>",
            f"💰 المبلغ المطلوب: <b>{price:g} USD</b> · ⏳ المدة: <b>{dur}</b>",
        ]
        is_usdt = bool(method and "USDT" in (method["title"] or "").upper())
        chains = (await afn.run(payments_service.configured_chains)) if is_usdt else []
        if method:
            body.append("\n💳 <b>وسيلة الدفع:</b> " + esc(method["title"]))
            if method.get("country"):
                body.append("🌍 الدولة: " + esc(method["country"]))
            if method.get("account_ref"):
                body.append("🔢 بيانات الحساب: <code>" + esc(method["account_ref"]) + "</code>")
            if method.get("instructions"):
                body.append("📝 التعليمات: " + esc(method["instructions"]))
        rows_kb = []
        if is_usdt and chains:
            body += [
                "\n⛓ <b><u>التفعيل الفوري بالتحقق البلوكيشن — اختر شبكتك</u></b>",
                f"▎💵 ارسل <b>{price:g} USDT</b> إلى عنوان الإدارة على الشبكة التي تناسبك ثم اضغط الشبكة بالأسفل وأرسل <b>TXID</b>.",
            ]
            for ch in chains:
                rows_kb.append([kb.btn(f"⛓ الدفع عبر {ch}", f"u:paychain:{pay_req}:{ch}", style="success")])
        else:
            body.append("\n📸 <b>أرسل صورة إيصال التحويل هنا مباشرة</b> — سيُرسل للإدارة فوراً ويُفتح لك المسار الاحترافي فور الاعتماد.")
            if method and method.get("account_ref"):
                rows_kb.append([kb.copy_btn("📋 نسخ رقم الحساب / المحفظة", method["account_ref"], style="primary")])
        rows_kb.append([kb.btn("🧾 مدفوعاتي", "u:payments"), kb.btn("↩️ الأكاديمية", "u:academy")])
        if config.ADMIN_ID and uid != config.ADMIN_ID:
            try:
                await bot.send_message(
                    config.ADMIN_ID,
                    f"🎓 <b>طلب اشتراك أكاديمية جديد #{pay_req}</b>\n"
                    f"▎👤 UID: <code>{uid}</code>\n"
                    f"▎💵 المبلغ: <b>{price:g} USD</b> · الوسيلة: <b>{esc(method['title'] if method else 'MANUAL')}</b>",
                    parse_mode="HTML",
                )
            except Exception:
                pass
        await render(q, "\n".join(body), kb.user_nav(rows_kb), bot)
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

    # --- 3) UNIFIED USER AFFILIATE, PLAN-UNLOCK & PAYOUT HUB ---
    if action == "affiliate":
        from urllib.parse import quote as _urlquote
        # v8.9.0: the bot's @username never changes at runtime → cached lookup
        # instead of a live Telegram get_me() round-trip on every tap.
        bot_u = await afn.run(affiliates.cached_bot_username)
        # v8.9.0: ONE snapshot, reused by the body AND the buttons. This screen
        # used to take 100 DB connections (dashboard 63 + stats 38 + link) —
        # ~15 s on Supabase. Now it is a single stats read + one config read.
        st = await afn.run(affiliates.affiliate_stats, uid)
        link = await afn.run(affiliates.unified_invite_link, uid, bot_u)
        body = await afn.run(affiliates.render_affiliate_dashboard, uid, bot_u, st, None)
        share_url = (
            "https://t.me/share/url?url=" + _urlquote(link)
            + "&text=" + _urlquote("👑 انضم إلى FOREXYEMENI — إشارات وتحليلات ملكية لحظية بالشارت الفاخر!")
        )
        rows_kb = [
            [
                kb.copy_btn("📋 نسخ رابط الدعوة", link, style="success"),
                kb.url_btn("📤 مشاركة الرابط", share_url, style="primary"),
            ]
        ]
        if st["balance_unlock_enabled"]:
            rows_kb.append([
                kb.btn(
                    f"💎 فتح باقة من رصيد أرباحي ({st['available_usd']:,.2f}$)",
                    "u:affunlock",
                    style="success" if st["available_usd"] > 0 else "primary",
                )
            ])
        if st["available_usd"] >= st["min_payout_usd"]:
            rows_kb.append([kb.btn(f"💸 طلب سحب أرباحي ({st['available_usd']:,.2f}$)", "u:affpayout", style="success")])
        else:
            rows_kb.append([kb.btn(f"💸 طلب السحب (الحد الأدنى {st['min_payout_usd']:g}$)", "u:affpayout", style="primary")])
        rows_kb.append([
            kb.btn("🔄 تحديث رصيدي", "u:affiliate", style="primary"),
            kb.btn("🎁 كود هدية", "u:redeem", style="success"),
        ])
        await render(q, body, kb.user_nav(rows_kb), bot)
        return True

    if action == "affunlock":
        st = await afn.run(affiliates.affiliate_stats, uid)
        if not st["balance_unlock_enabled"]:
            await render(
                q,
                "🔒 <b>ميزة فتح الباقات من رصيد العمولات متوقفة حالياً من الإدارة.</b>",
                kb.user_nav([[kb.btn("↩️ عمولاتي", "u:affiliate", style="primary")]]),
                bot,
            )
            return True
        products = await afn.run(affiliates.list_unlockable_products)
        avail = float(st["available_usd"] or 0.0)
        lines = [
            "💎 <b><u>فتح باقة فوري من رصيد أرباحك</u></b>",
            "━━━━━━━━━━━━━━━━━━",
            "<blockquote>"
            f"▎✅ <b>رصيدك المتاح الآن:</b> <b>{avail:,.2f} $</b>\n"
            "▎⚡ اختر أي باقة بالأسفل لفتحها وتفعيلها <b>فوراً خلال ثانية واحدة</b> خصماً من رصيدك بدون أي تحويل خارجي!"
            "</blockquote>",
        ]
        rows_kb = []
        for pr in products:
            price = float(pr["price_usd"])
            days_txt = f" · {pr['duration_days']} يوم" if pr["duration_days"] > 0 else " · مدى الحياة"
            can = avail >= price
            if can:
                lines.append(f"▎🟢 <b>{esc(pr['title'])}</b> — <b>{price:g}$</b>{days_txt} <i>(متاح للفتح الآن!)</i>")
                rows_kb.append([kb.btn(
                    f"✅ فتح {pr['title'][:26]} ({price:g}$)",
                    f"u:affunlockgo:{pr['code']}",
                    style="success",
                )])
            else:
                need = round(price - avail, 2)
                lines.append(f"▎⚪ <b>{esc(pr['title'])}</b> — <b>{price:g}$</b>{days_txt} <i>(متبقي لك {need:g}$)</i>")
                rows_kb.append([kb.btn(
                    f"🔒 {pr['title'][:24]} ({price:g}$ · ينقصك {need:g}$)",
                    f"u:affunlockgo:{pr['code']}",
                    style="primary",
                )])
        rows_kb.append([kb.btn("↩️ العودة لمحفظة العمولات", "u:affiliate", style="primary")])
        await render(q, "\n".join(lines), kb.user_nav(rows_kb), bot)
        return True

    if action.startswith("affunlockgo:"):
        code = action.split(":", 1)[1].strip().upper()
        res = await afn.run(affiliates.redeem_balance_for_plan, uid, code)
        if not res.get("ok"):
            await render(
                q,
                f"⏳ <b>تعذر الفتح حالياً</b>\n<blockquote>▎{esc(res.get('error', 'رصيد غير كافٍ'))}</blockquote>",
                kb.user_nav([
                    [kb.btn("💎 عرض كل الباقات المتاحة", "u:affunlock", style="primary")],
                    [kb.btn("↩️ محفظة العمولات", "u:affiliate", style="primary")],
                ]),
                bot,
            )
            return True
        await afn.run(audit_log.log, uid, f"affiliate_balance_unlock:{code}:{res['price_usd']}")
        days_line = (
            f"▎⏱ المدة المفعّلة: <b>{res['duration_days']} يوم</b>\n"
            if res.get("duration_days", 0) > 0 else
            "▎⏱ الصلاحية: <b>مدى الحياة 👑</b>\n"
        )
        await render(
            q,
            f"🎉 <b><u>تم فتح وتفعيل {esc(res['target_title'])} بنجاح!</u></b>\n"
            "━━━━━━━━━━━━━━━━━━\n"
            "<blockquote>"
            f"▎💎 المنتج المفعّل: <b>{esc(res['target_title'])}</b>\n"
            f"{days_line}"
            f"▎💵 المبلغ المخصوم من رصيدك: <b>{res['price_usd']:,.2f} $</b>\n"
            f"▎✅ رصيدك المتبقي: <b>{res['remaining_usd']:,.2f} $</b>"
            "</blockquote>\n"
            "✨ <i>مبارك لك! صلاحياتك الجديدة مفعّلة بالكامل الآن.</i>",
            kb.user_nav([
                [kb.btn("👑 لوحة التحكم", "u:dashboard", style="success"),
                 kb.btn("💸 محفظة العمولات", "u:affiliate", style="primary")],
            ]),
            bot,
        )
        if config.ADMIN_ID and uid != config.ADMIN_ID:
            try:
                await bot.send_message(
                    config.ADMIN_ID,
                    "💎 <b><u>فتح باقة فوري من رصيد العمولات!</u></b>\n"
                    "<blockquote>"
                    f"▎👤 المستخدم: <code>{uid}</code>\n"
                    f"▎🏆 المنتج: <b>{esc(res['target_title'])}</b>\n"
                    f"▎💵 المبلغ المستبدل من الرصيد: <b>{res['price_usd']:,.2f} $</b>\n"
                    f"▎✅ المتبقي في محفظته: <b>{res['remaining_usd']:,.2f} $</b>"
                    "</blockquote>",
                    parse_mode="HTML",
                )
            except Exception:
                pass
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


# =============================================================================
#                       ADMIN ACADEMY STUDIO & GROWTH
# =============================================================================
async def _render_admin_lesson_inspector(q, uid: int, les_id: int, bot, notice: str = ""):
    les = await afn.run(academy.get_lesson_any, les_id)
    if not les:
        await render(q, "الدرس غير موجود.", kb.admin_nav([[kb.btn("📚 كل الدروس", "a:acadlessons")]]), bot)
        return
    body = await afn.run(academy.render_lesson_card, uid, les, 0, True)
    if notice:
        body = f"<blockquote>{esc(notice)}</blockquote>\n\n" + body
    rows_kb = [
        [kb.btn("👁 معاينة + إرسال المرفق كطالب", f"a:acadles_preview:{les['id']}", style="success")],
        [
            kb.btn("📎 إرفاق فيديو/صوت/PDF", f"a:acadles_media:{les['id']}", style="primary"),
            kb.btn("✏️ تعديل النص", f"a:acadles_edit:{les['id']}", style="primary"),
        ],
        [
            kb.btn("📝 تعديل الاختبار", f"a:acadles_quiz:{les['id']}", style="primary"),
            kb.btn(
                "🔄 نقل إلى المجاني 📗" if les["track"] == "PAID" else "🔄 نقل إلى الاحترافي 👑",
                f"a:acadles_track:{les['id']}",
            ),
        ],
        [
            kb.btn("⚪ إخفاء الدرس" if les["enabled"] else "🟢 إظهار الدرس", f"a:acadles_tog:{les['id']}", style="danger" if les["enabled"] else "success"),
            kb.btn("🗑 حذف الدرس", f"a:acadles_del:{les['id']}", style="danger"),
        ],
        [kb.btn("↩️ كل الدروس", "a:acadlessons"), kb.btn("🎓 الأكاديمية", "a:academy")],
    ]
    await render(q, body, kb.admin_nav(rows_kb), bot)


async def handle_admin_growth(q, uid: int, act: str, arg: str, arg2: str, context, bot) -> bool:
    """Handle Admin callbacks for Academy Studio, Gift Codes & Affiliate Payouts.
    Returns True if handled, False otherwise."""
    # --- 1) ADMIN ACADEMY STUDIO ---
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
                kb.btn(f"✏️ تعديل السعر برقم مخصص ({st['price_usd']:g}$)", "a:acadprice_custom", style="primary"),
                kb.btn("⏳ مدة مخصصة", "a:acaddur_custom", style="primary"),
            ],
            [
                kb.btn(f"{'✅ ' if st['duration_days'] == 90 else ''}90d", "a:acaddur:90"),
                kb.btn(f"{'✅ ' if st['duration_days'] == 180 else ''}180d", "a:acaddur:180"),
                kb.btn(f"{'✅ ' if st['duration_days'] == 365 else ''}365d", "a:acaddur:365"),
                kb.btn(f"{'✅ ' if st['duration_days'] == 0 else ''}♾ مدى الحياة", "a:acaddur:0", style="success"),
            ],
            [
                kb.btn("📚 عرض وإدارة الدروس", "a:acadlessons", style="primary"),
                kb.btn("➕ إضافة درس جديد", "a:acadnew", style="success"),
            ],
            [kb.btn("👑 منح وصول لمستخدم", "a:acadgrant", style="success")],
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

    if act == "acadprice_custom":
        context.user_data["awaiting_acad_price"] = True
        cur_p = await afn.run(academy.paid_price_usd)
        await render(
            q,
            f"💵 <b><u>تعديل سعر الدورة (المسار الاحترافي)</u></b>\n"
            f"<blockquote>▎السعر الحالي: <b>{cur_p:g} USD</b>\n"
            "▎أرسل الآن <b>السعر الجديد بالدولار</b> (مثال: <code>35</code> أو <code>120</code> أو <code>19.99</code>):</blockquote>",
            kb.admin_nav([[kb.btn("❌ إلغاء", "a:academy", style="danger")]]),
            bot,
        )
        return True

    if act == "acaddur_custom":
        context.user_data["awaiting_acad_dur"] = True
        cur_d = await afn.run(academy.paid_duration_days)
        await render(
            q,
            f"⏳ <b><u>تعديل مدة اشتراك الأكاديمية بالأيام</u></b>\n"
            f"<blockquote>▎المدة الحالية: <b>{'مدى الحياة ♾' if cur_d == 0 else f'{cur_d} يوماً'}</b>\n"
            "▎أرسل عدد الأيام (أو <code>0</code> لجعلها <b>مدى الحياة</b>):</blockquote>",
            kb.admin_nav([[kb.btn("❌ إلغاء", "a:academy", style="danger")]]),
            bot,
        )
        return True

    if act == "acaddur":
        try:
            d = int(arg or 365)
            await afn.run(academy.set_pricing, None, d)
            await afn.run(audit_log.log, uid, f"academy_duration:{d}")
        except Exception:
            pass
        return await handle_admin_growth(q, uid, "academy", "", "", context, bot)

    if act == "acadlessons":
        flt = (arg or "").strip().upper()
        track_arg = flt if flt in ("FREE", "PAID") else None
        lessons = await afn.run(academy.list_lessons, track_arg, True)
        rows_kb = [
            [
                kb.btn(f"{'✅ ' if not track_arg else ''}الكل", "a:acadlessons", style="success" if not track_arg else "primary"),
                kb.btn(f"{'✅ ' if track_arg == 'FREE' else ''}📗 المجاني", "a:acadlessons:FREE", style="success" if track_arg == "FREE" else "primary"),
                kb.btn(f"{'✅ ' if track_arg == 'PAID' else ''}👑 الاحترافي", "a:acadlessons:PAID", style="success" if track_arg == "PAID" else "primary"),
            ],
            [kb.btn("➕ إضافة درس جديد (فيديو / نص / صوت / PDF)", "a:acadnew", style="success")],
        ]
        for l in lessons:
            vis = "🟢" if l["enabled"] else "⚪"
            tr = "📗" if l["track"] == "FREE" else "👑"
            mi = academy.media_icon(l["media_type"])
            rows_kb.append([
                kb.btn(
                    f"{vis} {tr}{mi} {l['title'][:38]}",
                    f"a:acadles_view:{l['id']}",
                    style="success" if l["track"] == "FREE" else "primary",
                )
            ])
        rows_kb.append([kb.btn("↩️ الأكاديمية", "a:academy")])
        await render(
            q,
            f"📚 <b><u>استوديو دروس الأكاديمية ({len(lessons)} درس)</u></b>\n"
            "<blockquote>▎اضغط على أي درس أدناه لـ <b>رؤية محتواه بالكامل، معاينة مرفقاته، تعديل نصه/اختباره، إرفاق فيديو أو صوت أو PDF، أو حذفه</b>.</blockquote>",
            kb.admin_nav(rows_kb),
            bot,
        )
        return True

    if act == "acadles_view":
        await _render_admin_lesson_inspector(q, uid, arg, bot)
        return True

    if act == "acadles_preview":
        les = await afn.run(academy.get_lesson_any, arg)
        if not les:
            await render(q, "الدرس غير موجود.", kb.admin_nav([[kb.btn("📚 كل الدروس", "a:acadlessons")]]), bot)
            return True
        if les.get("media_file_id"):
            await send_lesson_media(bot, uid, les)
        body = await afn.run(academy.render_lesson_card, uid, les, 0, False)
        await render(
            q,
            "👁 <b><u>معاينة الدرس كما يظهر للطالب:</u></b>\n\n" + body,
            kb.admin_nav([[kb.btn("↩️ إدارة هذا الدرس", f"a:acadles_view:{les['id']}")]]),
            bot,
        )
        return True

    if act == "acadles_media":
        les = await afn.run(academy.get_lesson_any, arg)
        if not les:
            return await handle_admin_growth(q, uid, "acadlessons", "", "", context, bot)
        context.user_data["awaiting_acad_media_for"] = int(les["id"])
        await render(
            q,
            f"📎 <b><u>إرفاق / تحديث وسائط للدرس: {esc(les['title'])}</u></b>\n"
            "<blockquote>"
            "▎أرسل الآن مباشرة في هذه المحادثة أياً مما يلي:\n"
            "• 🎬 <b>فيديو تعليمي</b> (MP4)\n"
            "• 🎙 <b>تسجيل صوتي (Voice)</b> أو 🎧 <b>ملف صوتي (MP3/Audio)</b>\n"
            "• 📄 <b>ملف PDF / مستند</b>\n"
            "• 🖼 <b>صورة / شارت توضيحي</b>\n"
            "▎سيُحفظ تلقائياً ويُعرض لكل طالب يفتح الدرس مجاناً 100%!"
            "</blockquote>",
            kb.admin_nav([[kb.btn("❌ إلغاء", f"a:acadles_view:{les['id']}", style="danger")]]),
            bot,
        )
        return True

    if act == "acadles_edit":
        les = await afn.run(academy.get_lesson_any, arg)
        if not les:
            return await handle_admin_growth(q, uid, "acadlessons", "", "", context, bot)
        context.user_data["awaiting_acad_edit_for"] = int(les["id"])
        await render(
            q,
            f"✏️ <b><u>تعديل عنوان ومحتوى الدرس: {esc(les['title'])}</u></b>\n"
            "<blockquote>"
            "▎أرسل النص الجديد بالتنسيق:\n"
            "<code>السطر الأول: العنوان الجديد</code>\n"
            "<code>بقية الأسطر: المحتوى التفصيلي للدرس</code>\n\n"
            "▎<i>(أو أرسل المحتوى مباشرة في سطر واحد لتحديث المحتوى فقط)</i>"
            "</blockquote>",
            kb.admin_nav([[kb.btn("❌ إلغاء", f"a:acadles_view:{les['id']}", style="danger")]]),
            bot,
        )
        return True

    if act == "acadles_quiz":
        les = await afn.run(academy.get_lesson_any, arg)
        if not les:
            return await handle_admin_growth(q, uid, "acadlessons", "", "", context, bot)
        context.user_data["awaiting_acad_quiz_for"] = int(les["id"])
        await render(
            q,
            f"📝 <b><u>تعديل / إضافة أسئلة اختبار الدرس: {esc(les['title'])}</u></b>\n"
            "<blockquote>"
            "▎أرسل سؤالاً في كل سطر بالتنسيق:\n"
            "<code>السؤال | خيار1 | خيار2 | خيار3 | رقم_الصحيح | تفسير اختياري</code>\n\n"
            "▎<b>مثال:</b>\n"
            "<code>ما أفضل منطقة شراء في ICT؟ | Discount أقل من 50% | Premium فوق 50% | عند القمة | 1 | نشتري دائماً في الخصم</code>"
            "</blockquote>",
            kb.admin_nav([[kb.btn("❌ إلغاء", f"a:acadles_view:{les['id']}", style="danger")]]),
            bot,
        )
        return True

    if act == "acadles_track":
        await afn.run(academy.toggle_lesson_track, arg)
        await _render_admin_lesson_inspector(q, uid, arg, bot, notice="✅ تم تبديل مسار الدرس بنجاح.")
        return True

    if act == "acadles_tog":
        await afn.run(academy.toggle_lesson_enabled, arg)
        await _render_admin_lesson_inspector(q, uid, arg, bot, notice="✅ تم تحديث حالة إظهار الدرس.")
        return True

    if act == "acadles_del":
        les = await afn.run(academy.get_lesson_any, arg)
        if not les:
            return await handle_admin_growth(q, uid, "acadlessons", "", "", context, bot)
        await render(
            q,
            f"🗑 <b><u>تأكيد حذف الدرس نهائياً؟</u></b>\n"
            f"<blockquote>▎📚 الدرس: <b>{esc(les['title'])}</b>\n▎⚠️ سيتم حذفه من المنهج نهائياً.</blockquote>",
            kb.admin_nav([
                [kb.btn("🗑 نعم، احذف نهائياً", f"a:acadles_delgo:{les['id']}", style="danger")],
                [kb.btn("❌ إلغاء", f"a:acadles_view:{les['id']}")],
            ]),
            bot,
        )
        return True

    if act == "acadles_delgo":
        ok = await afn.run(academy.delete_lesson, arg)
        if ok:
            await afn.run(audit_log.log, uid, f"academy_lesson_deleted:{arg}")
        return await handle_admin_growth(q, uid, "acadlessons", "", "", context, bot)

    # --- Guided Wizard: Add New Lesson (Text / Video / Audio / Voice / PDF / Photo) ---
    if act == "acadnew":
        context.user_data.pop("acad_new_draft", None)
        await render(
            q,
            "➕ <b><u>إضافة درس جديد إلى الأكاديمية (الخطوة 1/4)</u></b>\n"
            "<blockquote>▎اختر المسار الذي سيندرج تحته الدرس الجديد:</blockquote>",
            kb.admin_nav([
                [
                    kb.btn("📗 المسار المجاني (FREE)", "a:acadnew_tr:FREE", style="success"),
                    kb.btn("👑 المسار الاحترافي (PAID)", "a:acadnew_tr:PAID", style="primary"),
                ],
                [kb.btn("❌ إلغاء", "a:acadlessons", style="danger")],
            ]),
            bot,
        )
        return True

    if act == "acadnew_tr":
        tr = "FREE" if str(arg).upper() == "FREE" else "PAID"
        context.user_data["acad_new_draft"] = {"track": tr, "step": "pick_media_type"}
        await render(
            q,
            f"➕ <b><u>إضافة درس جديد (الخطوة 2/4) — {'📗 مجاني' if tr == 'FREE' else '👑 احترافي'}</u></b>\n"
            "<blockquote>▎اختر نوع الدرس الذي ترغب بإضافته:</blockquote>",
            kb.admin_nav([
                [kb.btn("📝 درس نصي مكثف", "a:acadnew_mt:TEXT", style="primary")],
                [
                    kb.btn("🎬 درس فيديو", "a:acadnew_mt:VIDEO", style="success"),
                    kb.btn("🎙 درس صوتي / فويس", "a:acadnew_mt:AUDIO", style="success"),
                ],
                [
                    kb.btn("📄 ملف PDF / كتاب", "a:acadnew_mt:PDF", style="primary"),
                    kb.btn("🖼 شارت / صورة", "a:acadnew_mt:PHOTO", style="primary"),
                ],
                [kb.btn("❌ إلغاء", "a:acadlessons", style="danger")],
            ]),
            bot,
        )
        return True

    if act == "acadnew_mt":
        mt = str(arg or "TEXT").upper()
        if mt not in academy.MEDIA_BADGES:
            mt = "TEXT"
        draft = context.user_data.get("acad_new_draft") or {"track": "PAID"}
        draft["media_type"] = mt
        draft["step"] = "title"
        context.user_data["acad_new_draft"] = draft
        await render(
            q,
            f"➕ <b><u>إضافة درس جديد (الخطوة 3/4) — {academy.media_badge(mt)}</u></b>\n"
            "<blockquote>▎أرسل الآن <b>عنوان الدرس</b> (مثال: <code>16. استراتيجية قنص الذهب في جلسة نيويورك</code>):</blockquote>",
            kb.admin_nav([[kb.btn("❌ إلغاء", "a:acadlessons", style="danger")]]),
            bot,
        )
        return True

    if act == "acadnew_save":
        draft = context.user_data.pop("acad_new_draft", None)
        if not draft or not draft.get("title"):
            return await handle_admin_growth(q, uid, "acadlessons", "", "", context, bot)
        created = await afn.run(
            academy.create_lesson,
            title=draft["title"],
            track=draft.get("track", "PAID"),
            content=draft.get("content", ""),
            media_type=draft.get("media_type", "TEXT"),
            media_file_id=draft.get("media_file_id", ""),
            media_caption=draft.get("media_caption", ""),
            quiz=draft.get("quiz") or [],
        )
        await afn.run(audit_log.log, uid, f"academy_lesson_created:{created['code']}:{created['media_type']}")
        await _render_admin_lesson_inspector(q, uid, created["id"], bot, notice="🎉 تم إضافة الدرس الجديد ونشره في الأكاديمية بنجاح!")
        return True

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

    # --- 3) ADMIN AFFILIATES, COMMISSION CONFIG & PLAN-UNLOCK CONTROLS ---
    if act == "affiliates":
        body = await afn.run(affiliates.render_admin_affiliates_card)
        pending = await afn.run(affiliates.list_payouts, "PENDING", 8)
        rows_kb = [
            [kb.btn("⚙️ ضبط نسب العمولة وميزة فتح الباقات", "a:affcfg", style="success")],
        ]
        for p in pending:
            rows_kb.append([
                kb.btn(f"✅ اعتماد #{p['id']} ({p['amount_usd']:g}$)", f"a:affapprove:{p['id']}", style="success"),
                kb.btn(f"❌ رفض #{p['id']}", f"a:affreject:{p['id']}", style="danger"),
            ])
        rows_kb.append([
            kb.btn("🔄 تحديث", "a:affiliates", style="primary"),
            kb.btn("↩️ المالية", "a:hub:finance", style="primary"),
        ])
        await render(q, body, kb.admin_nav(rows_kb), bot)
        return True

    if act == "affcfg":
        cfg = await afn.run(affiliates.get_config)
        body = await afn.run(affiliates.render_admin_config_card)
        r = cfg["tier_rates"]
        rows_kb = [
            [
                kb.btn(
                    "⏹ إيقاف نظام العمولات" if cfg["enabled"] else "▶️ تفعيل نظام العمولات",
                    "a:affset:toggle_en",
                    style="danger" if cfg["enabled"] else "success",
                ),
                kb.btn(
                    "🔀 تبديل لنسبة موحّدة" if cfg["mode"] == "TIERED" else "🔀 تبديل لرتب متدرجة",
                    "a:affset:toggle_mode",
                    style="primary",
                ),
            ],
        ]
        if cfg["mode"] == "FLAT":
            rows_kb.append([
                kb.btn("➖ 1%", "a:affset:flat:-1", style="primary"),
                kb.btn(f"💰 النسبة الموحدة: {cfg['flat_pct']:g}%", "a:affcfg", style="success"),
                kb.btn("➕ 1%", "a:affset:flat:1", style="primary"),
            ])
        else:
            for tcode, tlbl in (("bronze", "🥉 برونزي"), ("silver", "🥈 فضي"), ("gold", "🥇 ذهبي"), ("royal", "👑 ملكي")):
                val = r[tcode.upper()]
                rows_kb.append([
                    kb.btn("➖", f"a:affset:tier_{tcode}:-1", style="primary"),
                    kb.btn(f"{tlbl}: {val:g}%", "a:affcfg", style="primary"),
                    kb.btn("➕", f"a:affset:tier_{tcode}:1", style="primary"),
                ])
        rows_kb.append([
            kb.btn("➖ 5$", "a:affset:minpay:-5", style="primary"),
            kb.btn(f"💳 حد السحب: {cfg['min_payout_usd']:g}$", "a:affcfg", style="primary"),
            kb.btn("➕ 5$", "a:affset:minpay:5", style="primary"),
        ])
        rows_kb.append([
            kb.btn(
                f"💎 فتح من الرصيد: {'🟢 مفعّل' if cfg['balance_unlock_enabled'] else '⚪ متوقف'}",
                "a:affset:toggle_bal",
                style="success" if cfg["balance_unlock_enabled"] else "danger",
            ),
            kb.btn(
                f"🎁 فتح تلقائي: {'🟢 مفعّل' if cfg['milestone_enabled'] else '⚪ متوقف'}",
                "a:affset:toggle_ms",
                style="success" if cfg["milestone_enabled"] else "danger",
            ),
        ])
        rows_kb.append([
            kb.btn("➖", "a:affset:ms_refs:-1", style="primary"),
            kb.btn(f"👥 كل {cfg['milestone_paid_refs']} مشترك مدفوع", "a:affcfg", style="success"),
            kb.btn("➕", "a:affset:ms_refs:1", style="primary"),
        ])
        rows_kb.append([
            kb.btn(f"🏆 الجائزة: {cfg['milestone_target']} (تبديل)", "a:affset:ms_target", style="primary"),
            kb.btn(f"⏱ المدة: {cfg['milestone_days']} يوم (تبديل)", "a:affset:ms_days", style="primary"),
        ])
        rows_kb.append([kb.btn("↩️ لوحة العمولات", "a:affiliates", style="primary")])
        await render(q, body, kb.admin_nav(rows_kb), bot)
        return True

    if act == "affset":
        cfg = await afn.run(affiliates.get_config)
        sub = (arg or "").strip().lower()
        delta = 0.0
        try:
            delta = float(arg2) if arg2 else 0.0
        except ValueError:
            delta = 0.0
        if sub == "toggle_en":
            await afn.run(affiliates.set_config, enabled=not cfg["enabled"])
        elif sub == "toggle_mode":
            await afn.run(affiliates.set_config, mode="FLAT" if cfg["mode"] == "TIERED" else "TIERED")
        elif sub == "flat":
            await afn.run(affiliates.set_config, flat_pct=cfg["flat_pct"] + delta)
        elif sub in ("tier_bronze", "tier_silver", "tier_gold", "tier_royal"):
            tkey = sub.split("_", 1)[1].upper()
            cur_v = float(cfg["tier_rates"].get(tkey, 15.0))
            await afn.run(affiliates.set_config, **{sub: cur_v + delta})
        elif sub == "minpay":
            await afn.run(affiliates.set_config, min_payout_usd=cfg["min_payout_usd"] + delta)
        elif sub == "toggle_bal":
            await afn.run(affiliates.set_config, balance_unlock_enabled=not cfg["balance_unlock_enabled"])
        elif sub == "toggle_ms":
            await afn.run(affiliates.set_config, milestone_enabled=not cfg["milestone_enabled"])
        elif sub == "ms_refs":
            await afn.run(affiliates.set_config, milestone_paid_refs=int(cfg["milestone_paid_refs"] + delta))
        elif sub == "ms_target":
            targets = list(affiliates.MILESTONE_TARGETS)
            cur_i = targets.index(cfg["milestone_target"]) if cfg["milestone_target"] in targets else 0
            nxt_t = targets[(cur_i + 1) % len(targets)]
            await afn.run(affiliates.set_config, milestone_target=nxt_t)
        elif sub == "ms_days":
            dlist = list(affiliates.MILESTONE_DAYS_CHOICES)
            cur_i = dlist.index(cfg["milestone_days"]) if cfg["milestone_days"] in dlist else 2
            nxt_d = dlist[(cur_i + 1) % len(dlist)]
            await afn.run(affiliates.set_config, milestone_days=nxt_d)
        await afn.run(audit_log.log, uid, f"affiliate_cfg:{sub}:{arg2}")
        return await handle_admin_growth(q, uid, "affcfg", "", "", context, bot)

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


# =============================================================================
#                   TEXT & MEDIA INPUT HANDLERS (ADMIN + USER)
# =============================================================================
async def handle_growth_media_input(update, context, uid: int) -> bool:
    """Capture Admin Video / Audio / Voice / PDF / Photo uploads for Academy lessons."""
    if uid != config.ADMIN_ID:
        return False
    ud = context.user_data
    msg = update.effective_message
    if not msg:
        return False

    attach_id = ud.get("awaiting_acad_media_for")
    draft = ud.get("acad_new_draft")
    draft_waiting_media = bool(draft and draft.get("step") == "media")
    if not attach_id and not draft_waiting_media:
        return False

    media_type = ""
    file_id = ""
    caption = (msg.caption or "").strip()

    if getattr(msg, "video", None):
        media_type = "VIDEO"
        file_id = msg.video.file_id
        caption = caption or (getattr(msg.video, "file_name", "") or "")
    elif getattr(msg, "voice", None):
        media_type = "VOICE"
        file_id = msg.voice.file_id
    elif getattr(msg, "audio", None):
        media_type = "AUDIO"
        file_id = msg.audio.file_id
        caption = caption or (getattr(msg.audio, "title", "") or getattr(msg.audio, "file_name", "") or "")
    elif getattr(msg, "document", None):
        media_type = "PDF"
        file_id = msg.document.file_id
        caption = caption or (getattr(msg.document, "file_name", "") or "")
    elif getattr(msg, "photo", None):
        media_type = "PHOTO"
        file_id = msg.photo[-1].file_id
    else:
        return False

    # Case 1: Attaching/replacing media on an existing lesson
    if attach_id:
        ud.pop("awaiting_acad_media_for", None)
        updated = await afn.run(academy.update_lesson_media, int(attach_id), media_type, file_id, caption)
        if not updated:
            await msg.reply_text("❌ تعذر إيجاد الدرس لتحديث مرفقه.")
            return True
        await afn.run(audit_log.log, uid, f"academy_lesson_media:{updated['id']}:{media_type}")
        await msg.reply_text(
            f"✅ <b>تم حفظ مرفق الدرس بنجاح!</b>\n"
            f"<blockquote>▎📚 الدرس: <b>{esc(updated['title'])}</b>\n"
            f"▎📎 النوع: <b>{academy.media_badge(media_type)}</b></blockquote>",
            reply_markup=kb.admin_nav([[kb.btn("👁 عرض وإدارة الدرس", f"a:acadles_view:{updated['id']}", style="success")]]),
            parse_mode="HTML",
        )
        return True

    # Case 2: New lesson wizard step == 'media'
    draft["media_type"] = media_type
    draft["media_file_id"] = file_id
    if caption and not draft.get("media_caption"):
        draft["media_caption"] = caption
    if caption and not draft.get("content"):
        draft["content"] = caption
    draft["step"] = "quiz"
    ud["acad_new_draft"] = draft
    await msg.reply_text(
        f"✅ <b>تم استلام المرفق ({academy.media_badge(media_type)}) بنجاح!</b>\n\n"
        "📝 <b>الخطوة الأخيرة (اختياري): إضافة سؤال اختبار للدرس</b>\n"
        "<blockquote>"
        "▎أرسل سؤالاً بالتنسيق:\n"
        "<code>السؤال | خيار1 | خيار2 | خيار3 | رقم_الصحيح | تفسير اختياري</code>\n"
        "▎أو اضغط الزر أدناه للحفظ والنشر فوراً بدون سؤال:"
        "</blockquote>",
        reply_markup=kb.admin_nav([
            [kb.btn("🚀 حفظ ونشر الدرس الآن", "a:acadnew_save", style="success")],
            [kb.btn("❌ إلغاء", "a:acadlessons", style="danger")],
        ]),
        parse_mode="HTML",
    )
    return True


async def handle_growth_text_input(update, context, uid: int, text: str) -> bool:
    """Process one-shot and wizard text prompts for Academy Studio, Gift Codes & Affiliates."""
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

    # Admin: Custom Academy Course Price
    if ud.pop("awaiting_acad_price", None):
        try:
            p = float(text.replace("$", "").replace(",", "").strip())
            if not (1.0 <= p <= 100000.0):
                raise ValueError("range")
            new_p, _ = await afn.run(academy.set_pricing, p, None)
            await afn.run(audit_log.log, uid, f"academy_price_custom:{new_p:g}")
            await update.message.reply_text(
                f"✅ <b>تم تحديث سعر الدورة الاحترافية إلى: {new_p:g} USD</b>",
                reply_markup=kb.admin_nav([[kb.btn("🎓 استوديو الأكاديمية", "a:academy", style="success")]]),
                parse_mode="HTML",
            )
        except Exception:
            ud["awaiting_acad_price"] = True
            await update.message.reply_text(
                "❌ يرجى إدخال رقم صحيح للسعر بالدولار (مثال: <code>49</code> أو <code>125</code>).",
                parse_mode="HTML",
            )
        return True

    # Admin: Custom Academy Duration
    if ud.pop("awaiting_acad_dur", None):
        try:
            d = int(text.strip())
            if not (0 <= d <= 36500):
                raise ValueError("range")
            _, new_d = await afn.run(academy.set_pricing, None, d)
            await afn.run(audit_log.log, uid, f"academy_dur_custom:{new_d}")
            dur_lbl = "مدى الحياة ♾" if new_d == 0 else f"{new_d} يوماً"
            await update.message.reply_text(
                f"✅ <b>تم تحديث مدة اشتراك الأكاديمية إلى: {dur_lbl}</b>",
                reply_markup=kb.admin_nav([[kb.btn("🎓 استوديو الأكاديمية", "a:academy", style="success")]]),
                parse_mode="HTML",
            )
        except Exception:
            ud["awaiting_acad_dur"] = True
            await update.message.reply_text(
                "❌ يرجى إدخال عدد أيام صحيح (مثال: <code>365</code> أو <code>0</code> لمدى الحياة).",
                parse_mode="HTML",
            )
        return True

    # Admin: Edit existing lesson title/content
    edit_id = ud.pop("awaiting_acad_edit_for", None)
    if edit_id:
        lines = text.splitlines()
        if len(lines) >= 2:
            new_title = lines[0].strip()
            new_content = "\n".join(lines[1:]).strip()
        else:
            new_title = None
            new_content = text.strip()
        updated = await afn.run(academy.update_lesson_content, int(edit_id), new_title, new_content)
        if not updated:
            await update.message.reply_text("❌ الدرس غير موجود.")
            return True
        await afn.run(audit_log.log, uid, f"academy_lesson_edited:{updated['id']}")
        await update.message.reply_text(
            f"✅ <b>تم تحديث الدرس بنجاح:</b> {esc(updated['title'])}",
            reply_markup=kb.admin_nav([[kb.btn("👁 عرض وإدارة الدرس", f"a:acadles_view:{updated['id']}", style="success")]]),
            parse_mode="HTML",
        )
        return True

    # Admin: Edit existing lesson quiz
    quiz_id = ud.pop("awaiting_acad_quiz_for", None)
    if quiz_id:
        parsed = _parse_quiz_line(text)
        if not parsed:
            ud["awaiting_acad_quiz_for"] = quiz_id
            await update.message.reply_text(
                "❌ صيغة غير مكتملة. أرسل: <code>السؤال | خيار1 | خيار2 | خيار3 | رقم_الصحيح</code>",
                parse_mode="HTML",
            )
            return True
        updated = await afn.run(academy.update_lesson_quiz, int(quiz_id), parsed)
        await afn.run(audit_log.log, uid, f"academy_lesson_quiz:{quiz_id}:n={len(parsed)}")
        await update.message.reply_text(
            f"✅ <b>تم حفظ {len(parsed)} سؤال اختبار للدرس بنجاح!</b>",
            reply_markup=kb.admin_nav([[kb.btn("👁 عرض وإدارة الدرس", f"a:acadles_view:{updated['id']}", style="success")]]),
            parse_mode="HTML",
        )
        return True

    # Admin: Guided New Lesson Wizard (title -> content -> media -> quiz)
    draft = ud.get("acad_new_draft")
    if draft:
        step = draft.get("step")
        if step == "title":
            draft["title"] = text.strip()[:140]
            draft["step"] = "content"
            ud["acad_new_draft"] = draft
            await update.message.reply_text(
                f"✅ العنوان: <b>{esc(draft['title'])}</b>\n\n"
                "📝 <b>الخطوة 4/4: أرسل النص الشارح أو الملخص المكثف للدرس:</b>\n"
                "<blockquote>▎يمكنك كتابة الشرح الكامل والقواعد والمثال العملي (أو ملخص قصير إذا كان الدرس فيديو/صوت/PDF).</blockquote>",
                reply_markup=kb.admin_nav([[kb.btn("❌ إلغاء", "a:acadlessons", style="danger")]]),
                parse_mode="HTML",
            )
            return True

        if step == "content":
            draft["content"] = text.strip()
            mt = draft.get("media_type", "TEXT")
            if mt != "TEXT":
                draft["step"] = "media"
                ud["acad_new_draft"] = draft
                await update.message.reply_text(
                    f"📎 <b>أرسل الآن ملف الدرس ({academy.media_badge(mt)}) مباشرة في هذه المحادثة:</b>\n"
                    "<blockquote>▎يدعم رفع أو إعادة توجيه: فيديو (MP4) · صوت/فويس · ملف PDF · صورة.</blockquote>",
                    reply_markup=kb.admin_nav([[kb.btn("❌ إلغاء", "a:acadlessons", style="danger")]]),
                    parse_mode="HTML",
                )
            else:
                draft["step"] = "quiz"
                ud["acad_new_draft"] = draft
                await update.message.reply_text(
                    "📝 <b>اختياري — أضف سؤال اختبار للدرس أو احفظه مباشرة:</b>\n"
                    "<blockquote>"
                    "▎لإضافة سؤال أرسل:\n"
                    "<code>السؤال | خيار1 | خيار2 | خيار3 | رقم_الصحيح | تفسير اختياري</code>\n"
                    "▎أو اضغط «حفظ ونشر الدرس الآن» بالأسفل:"
                    "</blockquote>",
                    reply_markup=kb.admin_nav([
                        [kb.btn("🚀 حفظ ونشر الدرس الآن", "a:acadnew_save", style="success")],
                        [kb.btn("❌ إلغاء", "a:acadlessons", style="danger")],
                    ]),
                    parse_mode="HTML",
                )
            return True

        if step == "media":
            await update.message.reply_text(
                f"📎 بانتظار رفع الملف ({academy.media_badge(draft.get('media_type', 'VIDEO'))}) — أرسل فيديو أو صوت أو فويس أو PDF وليس نصاً.",
                reply_markup=kb.admin_nav([[kb.btn("⏭ حفظ كدرس نصي فقط", "a:acadnew_save", style="primary")]]),
                parse_mode="HTML",
            )
            return True

        if step == "quiz":
            parsed = _parse_quiz_line(text)
            if not parsed:
                await update.message.reply_text(
                    "❌ صيغة السؤال غير صحيحة. أرسل: <code>السؤال | خيار1 | خيار2 | خيار3 | رقم_الصحيح</code> أو اضغط الحفظ مباشرة.",
                    reply_markup=kb.admin_nav([[kb.btn("🚀 حفظ ونشر الدرس الآن", "a:acadnew_save", style="success")]]),
                    parse_mode="HTML",
                )
                return True
            draft["quiz"] = parsed
            ud.pop("acad_new_draft", None)
            created = await afn.run(
                academy.create_lesson,
                title=draft["title"],
                track=draft.get("track", "PAID"),
                content=draft.get("content", ""),
                media_type=draft.get("media_type", "TEXT"),
                media_file_id=draft.get("media_file_id", ""),
                media_caption=draft.get("media_caption", ""),
                quiz=parsed,
            )
            await afn.run(audit_log.log, uid, f"academy_lesson_created:{created['code']}:{created['media_type']}")
            await update.message.reply_text(
                f"🎉 <b><u>تم إنشاء ونشر الدرس الجديد بنجاح!</u></b>\n"
                f"<blockquote>▎📚 <b>{esc(created['title'])}</b>\n"
                f"▎🏷 المسار: <b>{'📗 مجاني' if created['track']=='FREE' else '👑 احترافي'}</b> · {academy.media_badge(created['media_type'])}\n"
                f"▎📝 عدد أسئلة الاختبار: <b>{len(parsed)}</b></blockquote>",
                reply_markup=kb.admin_nav([[kb.btn("👁 عرض وإدارة الدرس", f"a:acadles_view:{created['id']}", style="success")]]),
                parse_mode="HTML",
            )
            return True

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
