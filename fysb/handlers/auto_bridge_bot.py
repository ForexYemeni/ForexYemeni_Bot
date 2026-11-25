"""v7.7.2 Native Telegram Bot UI for Royal Auto-Bridge (User + Admin Governance + cTrader Luxury Guide + Promos/Digest)."""
from __future__ import annotations

import io
from telegram import InlineKeyboardMarkup, Update
from telegram.ext import ContextTypes

from fysb import afn, config, db
from fysb.handlers.common import render
from fysb.services import audit as audit_log, auto_bridge as ab
from fysb.theme import esc
from fysb.ui import keyboards as kb


def render_ctrader_luxury_guide_html() -> str:
    """Ultra-luxury formatted Arabic guide for activating cTrader Demo & Live copy trading."""
    return (
        "👑 <b><u>الدليل الملكي الفاخر لتفعيل النسخ الآلي عبر cTrader (Demo &amp; Live)</u></b>\n"
        "━━━━━━━━━━━━━━━━━━━━━\n"
        "<blockquote>"
        "⚡ <b>نظام النسخ المباشر 100% مجاني ومن الهاتف بدون VPS وبدون MT5!</b>\n"
        "يدعم جميع الوسطاء الداعمين لـ cTrader مثل: <b>IC Markets · Pepperstone · FTMO · FxPro · FundedNext · cTrader Demo</b>.\n"
        "التنفيذ فوري عبر بروتوكول <b>Spotware Open API v2</b> مع إغلاق 50% عند TP1 + نقل الوقف للدخول (BE) تلقائياً."
        "</blockquote>\n\n"
        "🔹 <b>المرحلة 1️⃣ — إنشاء تطبيق Open API المجاني (مرة واحدة فقط — دقيقتان)</b>\n"
        "<blockquote>"
        "1. افتح بوابة مطوري cTrader الرسمية من هاتفك:\n"
        "   🔗 <code>https://openapi.ctrader.com</code>\n"
        "2. سجّل الدخول بنفس <b>cTrader ID</b> (البريد الإلكتروني وكلمة المرور لحسابك في تطبيق cTrader).\n"
        "3. اضغط <b>Create New Application</b> وأدخل:\n"
        "   • اسم التطبيق: <code>ForexYemeni Bridge</code>\n"
        "   • Redirect URI: <code>https://openapi.ctrader.com/apps</code>\n"
        "4. بعد الحفظ انسخ المفتاحين:\n"
        "   • 🔑 <b>Client ID</b>\n"
        "   • 🔐 <b>Client Secret</b>\n"
        "<i>💡 ملاحظة للإدارة: إذا حفظت Client ID و Client Secret مرة واحدة في لوحة الإدارة، لن يحتاج أي عضو لإنشاء تطبيق — سيلصق فقط الـ Access Token!</i>"
        "</blockquote>\n\n"
        "🔹 <b>المرحلة 2️⃣ — استخراج Access Token بصلاحية التداول (30 ثانية)</b>\n"
        "<blockquote>"
        "1. من نفس صفحة التطبيقات، اضغط <b>Playground</b> بجانب تطبيقك.\n"
        "2. اختر صلاحية <b>Trading</b> (وليست Read-Only) واضغط <b>Get token</b>.\n"
        "3. وافق على الحساب التجريبي <b>Demo</b> (أو <b>Live</b>) الذي تريد النسخ عليه.\n"
        "4. انسخ كود <b>Access Token</b> الطويل الذي سيظهر لك."
        "</blockquote>\n\n"
        "🔹 <b>المرحلة 3️⃣ — الربط والاختبار الحي بضغطة واحدة داخل البوت</b>\n"
        "<blockquote>"
        "1. اضغط زر <b>👑 ربط cTrader</b> أسفل هذه الرسالة.\n"
        "2. اختر <b>🧪 تجريبي Demo (آمن)</b> ثم الصق <b>Access Token</b> (سيحذف البوت رسالتك تلقائياً ويُشفّر المفتاح بـ AES).\n"
        "3. اضغط <b>🔍 فحص الاتصال</b> ← سيكتشف البوت رقم حسابك تلقائياً ويعرض رصيدك الحي 💵.\n"
        "4. افتح تطبيق <b>cTrader</b> على هاتفك ثم اضغط <b>🧪 صفقة اختبار 0.01</b> ← ستشاهد صفقة <code>EURUSD BUY 0.01</code> تفتح أمام عينيك خلال ثانيتين! 🎉"
        "</blockquote>\n\n"
        "🛡 <b>الأمان الكامل:</b> جميع المفاتيح مشفّرة بالكامل (`ENC1:`)، ولا يمكن للبوت سحب أي أموال أبداً — الصلاحية محصورة في فتح وإدارة الصفقات فقط مع درع السحب اليومي Daily DD Shield."
    )


def _nav_for(uid: int, extra_rows: list | None = None) -> InlineKeyboardMarkup:
    """Route Admin back to Admin Command Center and regular users to User Home."""
    if uid == config.ADMIN_ID:
        rows = list(extra_rows or [])
        rows.append([kb.btn("⚡ لوحة إدارة جسر النسخ", "a:bridge", style="primary")])
        return kb.admin_nav(rows)
    return kb.user_nav(extra_rows)


def _user_bridge_home_kb(uid: int, conns: list[dict]) -> InlineKeyboardMarkup:
    rows: list[list] = [
        [kb.btn("👑 ربط cTrader (Demo/Live)", "u:br_new:ctrader", style="success")],
        [kb.btn("⚡ ربط TradeLocker", "u:br_new:tradelocker", style="primary"),
         kb.btn("🔥 ربط Deriv", "u:br_new:deriv", style="primary")],
        [kb.btn("🖥 ربط MT5 Bridge EA (.mq5)", "u:br_new:mt5_bridge", style="primary")],
    ]
    for c in conns[:6]:
        st_ic = "❄️" if c.get("admin_frozen") else ("🟢" if c.get("enabled") else "⏸")
        bal = float(c.get("last_balance") or 0.0)
        rows.append([kb.btn(
            f"{st_ic} {c['platform_name']} ({c['account_mode']}) · ${bal:,.0f}",
            f"u:br_view:{c['id']}",
            style="success" if (c.get("enabled") and not c.get("admin_frozen")) else "primary",
        )])
    rows.append([
        kb.btn("📖 دليل تفعيل cTrader", "u:br_guide", style="success"),
        kb.btn("📜 سجل أوامر النسخ", "u:br_orders", style="primary"),
    ])
    if uid != config.ADMIN_ID:
        rows.append([kb.btn("🧪 التداول التجريبي", "u:demo")])
    return _nav_for(uid, rows)


async def render_user_bridge_home(q, uid: int, bot) -> None:
    ok_d, msg_d = await afn.run(ab.user_can_copy, uid, "DEMO")
    ok_l, msg_l = await afn.run(ab.user_can_copy, uid, "LIVE")
    conns = await afn.run(ab.list_connections, uid, 10)
    g_cid, g_csec = await afn.run(ab.get_global_ctrader_app)
    lines = [
        "⚡ <b><u>جسر النسخ الملكي للمنصات (Royal Auto-Bridge — مجاني 100%)</u></b>",
        "━━━━━━━━━━━━━━━━━━━━━",
        "<blockquote>",
        f"▎🧪 النسخ التجريبي (Broker Demo): <b>{'🟢 متاح' if ok_d else '🔴 غير متاح'}</b> — {esc(msg_d)}",
        f"▎💎 النسخ الحقيقي (Live): <b>{'🟢 متاح' if ok_l else '🔒 مقفول'}</b> — {esc(msg_l)}",
        f"▎🔌 حساباتك المربوطة: <b>{len(conns)}</b>",
        f"▎👑 تطبيق cTrader المركزي: <b>{'🟢 جاهز (يكفي لصق Access Token فقط!)' if (g_cid and g_csec) else '⚙️ يتطلب Client ID/Secret أو ضبط الإدارة'}</b>",
        "</blockquote>",
        "<i>اختر المنصة للربط الفوري من هنا، أو اضغط على أي حساب مربوط لفحصه وإرسال صفقة اختبار 0.01 لوت.</i>",
    ]
    await render(q, "\n".join(lines), _user_bridge_home_kb(uid, conns), bot)


async def handle_user_bridge(q, uid: int, action: str, context: ContextTypes.DEFAULT_TYPE, bot) -> bool:
    """Handle all `u:bridge` and `u:br_*` callbacks natively inside Telegram."""
    if not await afn.run(ab.is_bridge_visible_to_user, uid):
        context.user_data.pop("bridge_draft", None)
        await render(
            q,
            "🧪 <b>التداول التجريبي</b>\n\nاضغط أدناه للعودة إلى حسابك التجريبي أو القائمة الرئيسية.",
            kb.user_nav([[kb.btn("🧪 التداول التجريبي", "u:demo", style="success")]]),
            bot,
        )
        return True

    if action == "bridge":
        await render_user_bridge_home(q, uid, bot)
        return True

    if action == "br_guide":
        rows = [
            [kb.btn("👑 ابدأ ربط cTrader الآن", "u:br_new:ctrader", style="success")],
            [kb.btn("↩️ جسر النسخ الملكي", "u:bridge", style="primary")],
        ]
        await render(q, render_ctrader_luxury_guide_html(), _nav_for(uid, rows), bot)
        return True

    if action == "br_orders":
        ords = await afn.run(ab.list_orders, uid, 15)
        if not ords:
            body = "📜 <b>سجل أوامر النسخ الآلي</b>\n\nلا توجد أوامر نسخ منفّذة بعد — اربط حسابك وجرّب صفقة اختبار 0.01!"
        else:
            lines = ["📜 <b><u>أحدث أوامر النسخ الآلي</u></b>\n"]
            for o in ords[:12]:
                ic = "🟢" if o["status"] == "EXECUTED" else ("⏳" if o["status"] in ("QUEUED", "DISPATCHED") else "🔴")
                act_ar = "🛡 50%+BE" if o["action_type"] == "PARTIAL_BE" else o["direction"]
                lines.append(
                    f"{ic} <b>{esc(o['symbol'])}</b> {act_ar} · <code>{o['lots']}</code> لوت · "
                    f"<b>{esc(o['platform'])}</b> ({esc(o['account_mode'])}) · <code>{esc(o['status'])}</code>\n"
                    f"   <i>{esc((o.get('detail') or '')[:90])}</i>"
                )
            body = "\n".join(lines)
        await render(q, body, _nav_for(uid, [[kb.btn("↩️ جسر النسخ", "u:bridge")]]), bot)
        return True

    if action.startswith("br_new:"):
        plat = action.split(":", 1)[1].strip().lower()
        if plat not in ab.PLATFORMS:
            return True
        pinfo = ab.PLATFORMS[plat]
        body = (
            f"🔗 <b>ربط منصة {esc(pinfo['name'])}</b>\n"
            f"<blockquote>{esc(pinfo['desc'])}</blockquote>\n\n"
            "اختر نوع الحساب الذي تريد ربطه (ننصح بالبدء بـ <b>🧪 Demo</b> للتجربة الآمنة أولاً):"
        )
        rows = [
            [kb.btn("🧪 حساب تجريبي Demo (مُوصى به وآمن)", f"u:br_mode:{plat}:DEMO", style="success")],
            [kb.btn("💎 حساب حقيقي Live", f"u:br_mode:{plat}:LIVE", style="danger")],
            [kb.btn("↩️ جسر النسخ", "u:bridge")],
        ]
        await render(q, body, _nav_for(uid, rows), bot)
        return True

    if action.startswith("br_mode:"):
        parts = action.split(":")
        plat = parts[1].lower() if len(parts) > 1 else "ctrader"
        amode = parts[2].upper() if len(parts) > 2 else "DEMO"
        ok_p, reason = await afn.run(ab.user_can_copy, uid, amode)
        if not ok_p:
            await render(
                q,
                f"⛔ <b>النسخ على حساب {amode} غير متاح حالياً:</b>\n{esc(reason)}",
                _nav_for(uid, [[kb.btn("↩️ جسر النسخ", "u:bridge")]]),
                bot,
            )
            return True

        if plat == "mt5_bridge":
            res = await afn.run(ab.save_connection, uid, "mt5_bridge", amode, partial_tp_be=True)
            if not res.get("ok"):
                await render(q, f"❌ {esc(res.get('error') or 'تعذر الحفظ')}", _nav_for(uid, [[kb.btn("↩️ جسر النسخ", "u:bridge")]]), bot)
                return True
            conn = res["connection"]
            body = (
                f"🖥 <b>تم إنشاء جسر MT5 ({amode}) بنجاح!</b>\n"
                f"<blockquote>"
                f"▎🔑 مفتاح الجسر الخاص بك:\n<code>{esc(conn['bridge_key'])}</code>\n"
                f"▎اضغط الزر بالأسفل لاستلام ملف <b>ForexYemeni_Bridge.mq5</b> جاهزاً للتثبيت على MT5 فوراً."
                f"</blockquote>"
            )
            await render(q, body, _nav_for(uid, [
                [kb.btn("⬇️ إرسال ملف ForexYemeni_Bridge.mq5 الآن", f"u:br_ea:{conn['id']}", style="success")],
                [kb.btn("↩️ جسر النسخ", "u:bridge")],
            ]), bot)
            return True

        context.user_data["bridge_draft"] = {"platform": plat, "account_mode": amode, "creds": {}}
        if plat == "ctrader":
            g_cid, g_csec = await afn.run(ab.get_global_ctrader_app)
            if g_cid and g_csec:
                context.user_data["bridge_draft"]["step"] = "ctrader_token_only"
                prompt = (
                    f"👑 <b>ربط cTrader ({amode}) — خطوة واحدة فقط!</b>\n"
                    "<blockquote>"
                    "تطبيق cTrader المركزي مضبوط ومشفّر جاهز من الإدارة ✅.\n"
                    "أرسل الآن <b>cTrader Access Token</b> الخاص بك (من Playground بصلاحية Trading).\n"
                    "<i>🔒 ستُحذف رسالتك تلقائياً ويُشفّر التوكن فوراً.</i>"
                    "</blockquote>"
                )
            else:
                context.user_data["bridge_draft"]["step"] = "ctrader_token"
                prompt = (
                    f"👑 <b>ربط cTrader ({amode}) — الخطوة 1 من 3</b>\n"
                    "<blockquote>"
                    "أرسل الآن <b>cTrader Access Token</b> الخاص بك (من <code>openapi.ctrader.com</code> ← Playground ← Trading).\n"
                    "<i>🔒 ستُحذف رسالتك تلقائياً ويُشفّر التوكن بـ AES.</i>"
                    "</blockquote>"
                )
        elif plat == "deriv":
            context.user_data["bridge_draft"]["step"] = "deriv_token"
            prompt = (
                f"🔥 <b>ربط Deriv ({amode})</b>\n"
                "<blockquote>أرسل الآن <b>Deriv API Token</b> (بصلاحية Trade) من إعدادات حسابك في Deriv.</blockquote>"
            )
        else:  # tradelocker
            context.user_data["bridge_draft"]["step"] = "tl_email"
            prompt = (
                f"⚡ <b>ربط TradeLocker ({amode}) — الخطوة 1 من 3</b>\n"
                "<blockquote>أرسل البريد الإلكتروني لحسابك في TradeLocker:</blockquote>"
            )
        await render(q, prompt, _nav_for(uid, [[kb.btn("❌ إلغاء", "u:bridge", style="danger")]]), bot)
        return True

    if action.startswith("br_view:"):
        cid = int(action.split(":", 1)[1] or 0)
        conns = await afn.run(ab.list_connections, uid, 20)
        c = next((x for x in conns if int(x["id"]) == cid), None)
        if not c:
            await render_user_bridge_home(q, uid, bot)
            return True
        st_ar = "❄️ مجمّد من الإدارة" if c.get("admin_frozen") else ("🟢 نشط" if c.get("enabled") else "⏸ متوقف مؤقتاً")
        body = (
            f"🔌 <b>{esc(c['platform_name'])} — {esc(c['account_mode'])}</b>\n"
            f"<blockquote>"
            f"▎الحالة: <b>{st_ar}</b> ({esc(c.get('last_status') or 'READY')})\n"
            f"▎💰 الرصيد الحي: <b>${float(c.get('last_balance') or 0):,.2f}</b>\n"
            f"▎⚖️ المخاطرة: <b>{c['risk_pct']}%</b> · 🛡 درع DD: <b>{c['max_daily_dd_pct']}%</b>\n"
            f"▎🛡 إغلاق 50% TP1 + BE: <b>{'مفعّل ✅' if c['partial_tp_be'] else 'متوقف'}</b>\n"
            f"▎🌐 الأسواق: <b>{esc(c['markets'])}</b>"
            + (f"\n▎🔑 Bridge Key: <code>{esc(c['bridge_key'])}</code>" if c.get("bridge_key") else "")
            + "</blockquote>"
        )
        rows = [
            [kb.btn("🔍 فحص الاتصال والرصيد الحي", f"u:br_test:{cid}", style="primary"),
             kb.btn("🧪 صفقة اختبار 0.01 لوت", f"u:br_testtrade:{cid}", style="success")],
        ]
        if c["platform"] == "mt5_bridge":
            rows.append([kb.btn("⬇️ إرسال ملف ForexYemeni_Bridge.mq5", f"u:br_ea:{cid}", style="success")])
        rows.append([
            kb.btn("⏸ إيقاف مؤقت" if c["enabled"] else "▶️ تشغيل النسخ", f"u:br_tog:{cid}",
                   style="primary" if c["enabled"] else "success"),
            kb.btn("🗑 حذف الربط", f"u:br_del:{cid}", style="danger"),
        ])
        rows.append([kb.btn("↩️ جسر النسخ", "u:bridge")])
        await render(q, body, _nav_for(uid, rows), bot)
        return True

    if action.startswith("br_test:"):
        cid = int(action.split(":", 1)[1] or 0)
        res = await afn.run(ab.test_connection, cid, uid, uid == config.ADMIN_ID)
        msg = res.get("message") if res.get("ok") else f"❌ {res.get('error') or 'فشل الاتصال'}"
        await render(
            q,
            f"🔍 <b>نتيجة فحص الاتصال المباشر</b>\n<blockquote>{esc(str(msg))}</blockquote>",
            _nav_for(uid, [
                [kb.btn("🧪 تجربة صفقة اختبار 0.01 لوت الآن", f"u:br_testtrade:{cid}", style="success")],
                [kb.btn("↩️ تفاصيل الحساب", f"u:br_view:{cid}"), kb.btn("⚡ جسر النسخ", "u:bridge")],
            ]),
            bot,
        )
        return True

    if action.startswith("br_testtrade:"):
        cid = int(action.split(":", 1)[1] or 0)
        res = await afn.run(ab.execute_test_trade, cid, "EURUSD", "BUY", 0.01, uid, uid == config.ADMIN_ID)
        msg = res.get("message") if res.get("ok") else f"❌ {res.get('error') or 'فشل التنفيذ'}"
        await render(
            q,
            f"🧪 <b>نتيجة صفقة الاختبار (EURUSD BUY 0.01)</b>\n<blockquote>{esc(str(msg))}</blockquote>",
            _nav_for(uid, [
                [kb.btn("📜 سجل أوامر النسخ", "u:br_orders", style="primary"),
                 kb.btn("↩️ تفاصيل الحساب", f"u:br_view:{cid}")],
            ]),
            bot,
        )
        return True

    if action.startswith("br_ea:"):
        cid = int(action.split(":", 1)[1] or 0)
        conns = await afn.run(ab.list_connections, uid, 20)
        c = next((x for x in conns if int(x["id"]) == cid), None)
        if not c:
            return True
        src = ab.render_mt5_ea_source(c, config.WEBAPP_URL or "https://YOUR-BOT-URL")
        bio = io.BytesIO(src.encode("utf-8"))
        bio.name = "ForexYemeni_Bridge.mq5"
        await bot.send_document(
            chat_id=uid,
            document=bio,
            filename="ForexYemeni_Bridge.mq5",
            caption="🖥 <b>ForexYemeni_Bridge.mq5</b> جاهز بمفتاحك الخاص! انسخه إلى <code>MQL5/Experts</code> ثم فعّل WebRequest لرابط السيرفر.",
            parse_mode="HTML",
        )
        return True

    if action.startswith("br_tog:"):
        cid = int(action.split(":", 1)[1] or 0)
        await afn.run(ab.toggle_connection, cid, uid, uid == config.ADMIN_ID)
        await render_user_bridge_home(q, uid, bot)
        return True

    if action.startswith("br_del:"):
        cid = int(action.split(":", 1)[1] or 0)
        await afn.run(ab.delete_connection, cid, uid, uid == config.ADMIN_ID)
        await render_user_bridge_home(q, uid, bot)
        return True

    return False


# ---------------------------------------------------------------------------
# Admin Native Telegram Bridge Governance Studio (`a:bridge`, `a:br_*`)
# ---------------------------------------------------------------------------

async def render_admin_bridge_home(q, bot) -> None:
    pol = await afn.run(ab.get_policy)
    conns = await afn.run(ab.list_connections, None, 100)
    ovs = await afn.run(ab.list_user_overrides)
    g_cid, g_csec = await afn.run(ab.get_global_ctrader_app)
    mode = pol["access_mode"]
    mode_ar = {"OFF": "🛑 إيقاف كلي (OFF)", "ALL": "🌐 مفتوح للجميع (ALL)",
               "PLANS": "💎 باقات محددة (PLANS)", "SELECTED": "🎯 محددون فقط (SELECTED)"}.get(mode, mode)
    body = (
        "⚡ <b><u>مركز التحكم الصارم بجسر النسخ الملكي (Royal Auto-Bridge)</u></b>\n"
        "━━━━━━━━━━━━━━━━━━━━━\n"
        "<blockquote>\n"
        f"▎⚡ الحالة العامة: <b>{'🟢 مفعّل' if pol['enabled'] else '🔴 متوقف'}</b>\n"
        f"▎🚨 مفتاح الطوارئ (Kill Switch): <b>{'🔴 مفعّل (الكل متوقف)' if pol['kill_switch'] else '🟢 طبيعي'}</b>\n"
        f"▎💎 النسخ الحقيقي (Live): <b>{'🟢 مسموح' if pol['allow_live'] else '🔒 مقفول (Demo فقط)'}</b>\n"
        f"▎🎯 سياسة الوصول: <b>{mode_ar}</b>\n"
        f"▎💠 الباقات المسموحة: <code>{esc(','.join(pol['allowed_plans']))}</code>\n"
        f"▎👑 تطبيق cTrader المركزي: <b>{'🟢 مضبوط ومشفّر' if (g_cid and g_csec) else '⚪ غير مضبوط'}</b>\n"
        f"▎🔌 حسابات مربوطة: <b>{len(conns)}</b> · 👤 قواعد أعضاء خاصة: <b>{len(ovs)}</b>\n"
        "</blockquote>\n"
        "<i>يمكنك التحكم بكل شيء من هنا مباشرة داخل أزرار البوت أو من لوحة الإدارة بالويب.</i>"
    )
    rows = [
        [kb.btn(f"⚡ الجسر: {'🟢' if pol['enabled'] else '🔴'}", "a:br_tog:bridge_enabled",
                style="success" if pol["enabled"] else "danger"),
         kb.btn(f"🚨 طوارئ: {'🔴' if pol['kill_switch'] else '⚪'}", "a:br_tog:bridge_kill_switch",
                style="danger" if pol["kill_switch"] else "primary")],
        [kb.btn(f"💎 نسخ Live: {'🟢 مسموح' if pol['allow_live'] else '🔒 Demo فقط'}", "a:br_tog:bridge_allow_live",
                style="success" if pol["allow_live"] else "primary")],
        [kb.btn("🛑 إيقاف للجميع (OFF)", "a:br_mode:OFF", style="danger" if mode == "OFF" else "primary"),
         kb.btn("🌐 الكل (ALL)", "a:br_mode:ALL", style="success" if mode == "ALL" else "primary")],
        [kb.btn("💎 باقات (PLANS)", "a:br_mode:PLANS", style="success" if mode == "PLANS" else "primary"),
         kb.btn("🎯 محددون (SELECTED)", "a:br_mode:SELECTED", style="success" if mode == "SELECTED" else "primary")],
        [kb.btn("👑 ضبط مفاتيح تطبيق cTrader المركزية", "a:br_ctapp", style="success")],
        [kb.btn("💎 تعديل الباقات المسموحة", "a:br_plans", style="primary"),
         kb.btn("👤 سماح/منع عضو بالـ ID", "a:br_user", style="primary")],
        [kb.btn(f"🔌 الحسابات المربوطة ({len(conns)})", "a:br_conns", style="primary"),
         kb.btn("📜 سجل أوامر النسخ", "a:br_orders", style="primary")],
        [kb.btn("👑 ربط حسابك الشخصي (cTrader / Demo)", "u:bridge", style="success")],
        [kb.btn("📖 دليل تفعيل cTrader الفاخر", "a:br_guide", style="primary")],
    ]
    await render(q, body, kb.admin_nav(rows), bot)


async def handle_admin_bridge(q, uid: int, act: str, arg: str, arg2: str, context: ContextTypes.DEFAULT_TYPE, bot) -> bool:
    if act == "bridge":
        await render_admin_bridge_home(q, bot)
        return True

    if act == "br_tog":
        key = arg
        if key in ("bridge_enabled", "bridge_kill_switch", "bridge_allow_live"):
            cur = (await afn.run(db.get_setting, key, "1" if key != "bridge_kill_switch" else "0")) == "1"
            await afn.run(db.set_setting, key, "0" if cur else "1")
            await afn.run(audit_log.log, uid, f"bot_bridge_toggle:{key}={int(not cur)}")
        await render_admin_bridge_home(q, bot)
        return True

    if act == "br_mode":
        mode = arg.upper()
        if mode in ab.ACCESS_MODES:
            await afn.run(ab.update_policy, enabled=(mode != "OFF"), access_mode=mode)
            await afn.run(audit_log.log, uid, f"bot_bridge_mode:{mode}")
        await render_admin_bridge_home(q, bot)
        return True

    if act == "br_ctapp":
        context.user_data["admin_bridge_input"] = "ctapp_cid"
        await render(
            q,
            "👑 <b>ضبط مفاتيح تطبيق cTrader Open API المركزية (مشفّرة AES)</b>\n"
            "<blockquote>أرسل الآن <b>cTrader Client ID</b> من بوابة <code>openapi.ctrader.com</code>\n"
            "(أو أرسل <code>CLEAR</code> لمسح المفاتيح المركزية).</blockquote>",
            kb.admin_nav([[kb.btn("❌ إلغاء", "a:bridge", style="danger")]]),
            bot,
        )
        return True

    if act == "br_plans":
        context.user_data["admin_bridge_input"] = "plans"
        await render(
            q,
            "💎 <b>تعديل الباقات المسموحة لجسر النسخ</b>\n"
            "<blockquote>أرسل رموز الباقات مفصولة بفاصلة، مثال:\n<code>PRO,VIP,ROYAL</code> أو <code>VIP,ROYAL</code></blockquote>",
            kb.admin_nav([[kb.btn("❌ إلغاء", "a:bridge", style="danger")]]),
            bot,
        )
        return True

    if act == "br_user":
        context.user_data["admin_bridge_input"] = "user_rule"
        await render(
            q,
            "👤 <b>سماح أو منع عضو محدد من جسر النسخ</b>\n"
            "<blockquote>أرسل رقم ID العضو متبوعاً بـ <code>ALLOW</code> أو <code>DENY</code> أو <code>AUTO</code>\n"
            "مثال:\n<code>123456789 ALLOW</code>\n<code>123456789 DENY</code></blockquote>",
            kb.admin_nav([[kb.btn("❌ إلغاء", "a:bridge", style="danger")]]),
            bot,
        )
        return True

    if act == "br_user_set":
        t_uid = int(arg or 0)
        mode = (arg2 or "AUTO").upper()
        if t_uid > 0:
            await afn.run(ab.set_user_override, t_uid, mode, actor=uid)
            await afn.run(audit_log.log, uid, f"bot_bridge_user:{t_uid}:{mode}")
        from fysb.handlers import admin_core
        await admin_core.admin_user_detail(q, uid, context, bot, t_uid)
        return True

    if act == "br_conns":
        conns = await afn.run(ab.list_connections, None, 25)
        if not conns:
            await render(q, "🔌 <b>الحسابات المربوطة</b>\n\nلا توجد حسابات مربوطة بعد.",
                         kb.admin_nav([[kb.btn("↩️ جسر النسخ", "a:bridge")]]), bot)
            return True
        lines = [f"🔌 <b><u>الحسابات المربوطة ({len(conns)})</u></b>\n"]
        rows = []
        for c in conns[:12]:
            ic = "❄️" if c.get("admin_frozen") else ("🟢" if c.get("enabled") else "⏸")
            nm = c.get("full_name") or c.get("username") or f"UID {c['user_id']}"
            lines.append(
                f"{ic} <b>{esc(str(nm))}</b> (<code>{c['user_id']}</code>) · {esc(c['platform'])} "
                f"<b>{esc(c['account_mode'])}</b> · ${float(c.get('last_balance') or 0):,.2f}"
            )
            rows.append([
                kb.btn(f"🔍 فحص #{c['id']}", f"a:br_ctest:{c['id']}", style="primary"),
                kb.btn(f"🧪 0.01 #{c['id']}", f"a:br_ctrade:{c['id']}", style="success"),
                kb.btn("🔥 فك" if c.get("admin_frozen") else "❄️ تجميد", f"a:br_cfrz:{c['id']}",
                       style="success" if c.get("admin_frozen") else "danger"),
                kb.btn("🗑", f"a:br_cdel:{c['id']}", style="danger"),
            ])
        rows.append([kb.btn("↩️ جسر النسخ", "a:bridge")])
        await render(q, "\n".join(lines), kb.admin_nav(rows), bot)
        return True

    if act == "br_ctest":
        res = await afn.run(ab.test_connection, int(arg or 0), None, True)
        msg = res.get("message") if res.get("ok") else f"❌ {res.get('error') or 'خطأ'}"
        await render(q, f"🔍 <b>نتيجة الفحص</b>\n<blockquote>{esc(str(msg))}</blockquote>",
                     kb.admin_nav([[kb.btn("↩️ الحسابات المربوطة", "a:br_conns")]]), bot)
        return True

    if act == "br_ctrade":
        res = await afn.run(ab.execute_test_trade, int(arg or 0), "EURUSD", "BUY", 0.01, None, True)
        msg = res.get("message") if res.get("ok") else f"❌ {res.get('error') or 'خطأ'}"
        await render(q, f"🧪 <b>نتيجة صفقة الاختبار</b>\n<blockquote>{esc(str(msg))}</blockquote>",
                     kb.admin_nav([[kb.btn("↩️ الحسابات المربوطة", "a:br_conns")]]), bot)
        return True

    if act == "br_cfrz":
        await afn.run(ab.admin_freeze_connection, int(arg or 0), None)
        return await handle_admin_bridge(q, uid, "br_conns", "", "", context, bot)

    if act == "br_cdel":
        await afn.run(ab.delete_connection, int(arg or 0), None, True)
        return await handle_admin_bridge(q, uid, "br_conns", "", "", context, bot)

    if act == "br_orders":
        ords = await afn.run(ab.list_orders, None, 20)
        lines = ["📜 <b><u>سجل أوامر جسر النسخ</u></b>\n"]
        if not ords:
            lines.append("لا توجد أوامر مسجلة بعد.")
        for o in ords[:15]:
            ic = "🟢" if o["status"] == "EXECUTED" else ("⏳" if o["status"] in ("QUEUED", "DISPATCHED") else "🔴")
            lines.append(
                f"{ic} <code>UID {o['user_id']}</code> · <b>{esc(o['symbol'])}</b> {esc(o['direction'])} "
                f"({o['lots']}) · {esc(o['platform'])}/{esc(o['account_mode'])} · <code>{esc(o['status'])}</code>"
            )
        await render(q, "\n".join(lines), kb.admin_nav([[kb.btn("↩️ جسر النسخ", "a:bridge")]]), bot)
        return True

    if act == "br_guide":
        await render(q, render_ctrader_luxury_guide_html(),
                     kb.admin_nav([
                         [kb.btn("👑 ضبط مفاتيح cTrader المركزية", "a:br_ctapp", style="success")],
                         [kb.btn("↩️ جسر النسخ", "a:bridge")],
                     ]), bot)
        return True

    if act == "promos":
        from fysb.services import promos as _promos
        items = await afn.run(_promos.list_promos, False)
        lines = [f"🎟 <b><u>أكواد الخصم ونجوم تيليجرام ({len(items)})</u></b>\n"]
        if not items:
            lines.append("لا توجد أكواد خصم بعد — اضغط بالأسفل لإنشاء كود فوري.")
        for p in items[:15]:
            lines.append(
                f"{'🟢' if p['enabled'] else '🔴'} <code>{esc(p['code'])}</code> · "
                f"خصم <b>{p['discount_pct']}%</b> · استخدام {p['used_count']}/{p['max_uses']}"
            )
        await render(q, "\n".join(lines), kb.admin_nav([
            [kb.btn("➕ إنشاء كود خصم جديد", "a:promo_new", style="success")],
            [kb.btn("↩️ المالية", "a:hub:finance")],
        ]), bot)
        return True

    if act == "promo_new":
        context.user_data["admin_bridge_input"] = "promo_new"
        await render(
            q,
            "🎟 <b>إنشاء كود خصم جديد</b>\n"
            "<blockquote>أرسل الكود ونسبة الخصم بمسافة، مثال:\n<code>ROYAL20 20</code> (كود ROYAL20 بخصم 20%)</blockquote>",
            kb.admin_nav([[kb.btn("❌ إلغاء", "a:promos", style="danger")]]),
            bot,
        )
        return True

    if act == "digest_now":
        from fysb.services import digest as _digest
        res = await _digest.publish_digest(bot, days=7, include_channels=True, notify_admin=True)
        await render(
            q,
            f"📊 <b>تم بث تقرير الأداء الأسبوعي الفاخر بنجاح!</b>\n"
            f"<blockquote>الصفقات المحللة: {res.get('stats', {}).get('total', 0)} · القنوات: {res.get('channels_sent', 0)}</blockquote>",
            kb.admin_nav([[kb.btn("↩️ المالية", "a:hub:finance")]]),
            bot,
        )
        return True

    return False


# ---------------------------------------------------------------------------
# Guided Chat Text Inputs (Auto-Deletes Secrets for Privacy!)
# ---------------------------------------------------------------------------

async def _try_delete(msg) -> None:
    try:
        await msg.delete()
    except Exception:
        pass


async def handle_bridge_text_input(update: Update, context: ContextTypes.DEFAULT_TYPE, uid: int, text: str) -> bool:
    """Process guided chat inputs for User bridge connection wizard and Admin bridge governance."""
    msg = update.effective_message
    txt = (text or "").strip()

    # 1. Admin inputs
    adm_state = context.user_data.get("admin_bridge_input")
    if adm_state and uid == config.ADMIN_ID:
        if adm_state == "ctapp_cid":
            await _try_delete(msg)
            if txt.upper() == "CLEAR":
                context.user_data.pop("admin_bridge_input", None)
                await afn.run(ab.set_global_ctrader_app, "", "")
                await msg.reply_text("✅ تم مسح مفاتيح تطبيق cTrader المركزية.", reply_markup=kb.admin_nav([[kb.btn("↩️ جسر النسخ", "a:bridge")]]))
                return True
            context.user_data["admin_ctapp_cid"] = txt
            context.user_data["admin_bridge_input"] = "ctapp_csec"
            await msg.reply_text(
                "🔐 <b>الخطوة 2 من 2:</b> أرسل الآن <b>cTrader Client Secret</b> (ستُحذف رسالتك وتُشفّر فوراً):",
                parse_mode="HTML",
            )
            return True
        if adm_state == "ctapp_csec":
            await _try_delete(msg)
            cid = context.user_data.pop("admin_ctapp_cid", "")
            context.user_data.pop("admin_bridge_input", None)
            await afn.run(ab.set_global_ctrader_app, cid, txt)
            await msg.reply_text(
                "👑 <b>تم حفظ مفاتيح تطبيق cTrader Open API المركزية مشفّرة بنجاح!</b>\n"
                "الآن يمكنك أنت أو أي عضو ربط حساب cTrader بلصق <b>Access Token</b> فقط.",
                reply_markup=kb.admin_nav([
                    [kb.btn("👑 ربط حساب cTrader الآن", "u:br_new:ctrader", style="success")],
                    [kb.btn("↩️ جسر النسخ", "a:bridge")],
                ]),
                parse_mode="HTML",
            )
            return True
        if adm_state == "plans":
            context.user_data.pop("admin_bridge_input", None)
            pol = await afn.run(ab.update_policy, allowed_plans=txt)
            await msg.reply_text(
                f"✅ تم تحديث الباقات المسموحة إلى: <code>{esc(','.join(pol['allowed_plans']))}</code>",
                reply_markup=kb.admin_nav([[kb.btn("↩️ جسر النسخ", "a:bridge")]]),
                parse_mode="HTML",
            )
            return True
        if adm_state == "user_rule":
            context.user_data.pop("admin_bridge_input", None)
            parts = txt.split()
            if len(parts) < 2 or not parts[0].isdigit():
                await msg.reply_text("⚠️ الصيغة المطلوبة: <code>123456789 ALLOW</code> أو <code>DENY</code>", parse_mode="HTML")
                return True
            t_uid = int(parts[0])
            mode = parts[1].upper()
            res = await afn.run(ab.set_user_override, t_uid, mode, actor=uid)
            await msg.reply_text(
                f"✅ تم ضبط قاعدة العضو <code>{t_uid}</code> إلى <b>{esc(res.get('override_mode', mode))}</b>"
                if res.get("ok") else f"❌ {esc(res.get('error') or 'خطأ')}",
                reply_markup=kb.admin_nav([[kb.btn("↩️ جسر النسخ", "a:bridge")]]),
                parse_mode="HTML",
            )
            return True
        if adm_state == "promo_new":
            context.user_data.pop("admin_bridge_input", None)
            from fysb.services import promos as _promos
            parts = txt.split()
            code = parts[0].upper() if parts else ""
            pct = int(parts[1]) if len(parts) > 1 and parts[1].isdigit() else 20
            res = await afn.run(_promos.create_promo, code, pct, 100, 30, uid)
            await msg.reply_text(
                f"🎟 تم إنشاء كود <code>{esc(res['code'])}</code> بخصم <b>{res['discount_pct']}%</b>!"
                if res.get("ok") else f"❌ {esc(res.get('error') or 'خطأ')}",
                reply_markup=kb.admin_nav([[kb.btn("🎟 أكواد الخصم", "a:promos")]]),
                parse_mode="HTML",
            )
            return True

    # 2. User bridge connection wizard
    draft = context.user_data.get("bridge_draft")
    if not isinstance(draft, dict):
        return False
    step = draft.get("step") or ""
    plat = draft.get("platform") or "ctrader"
    amode = draft.get("account_mode") or "DEMO"
    creds = draft.setdefault("creds", {})

    if step == "ctrader_token_only":
        await _try_delete(msg)
        creds["access_token"] = txt
        context.user_data.pop("bridge_draft", None)
        return await _finish_user_connect(msg, uid, plat, amode, creds)

    if step == "ctrader_token":
        await _try_delete(msg)
        creds["access_token"] = txt
        draft["step"] = "ctrader_cid"
        await msg.reply_text("🔑 <b>الخطوة 2 من 3:</b> أرسل الآن <b>cTrader Client ID</b>:", parse_mode="HTML")
        return True

    if step == "ctrader_cid":
        await _try_delete(msg)
        creds["client_id"] = txt
        draft["step"] = "ctrader_csec"
        await msg.reply_text("🔐 <b>الخطوة 3 من 3:</b> أرسل الآن <b>cTrader Client Secret</b>:", parse_mode="HTML")
        return True

    if step == "ctrader_csec":
        await _try_delete(msg)
        creds["client_secret"] = txt
        context.user_data.pop("bridge_draft", None)
        return await _finish_user_connect(msg, uid, plat, amode, creds)

    if step == "deriv_token":
        await _try_delete(msg)
        creds["api_token"] = txt
        context.user_data.pop("bridge_draft", None)
        return await _finish_user_connect(msg, uid, plat, amode, creds)

    if step == "tl_email":
        creds["email"] = txt
        draft["step"] = "tl_pass"
        await msg.reply_text("🔐 <b>الخطوة 2 من 3:</b> أرسل كلمة المرور لحساب TradeLocker:", parse_mode="HTML")
        return True

    if step == "tl_pass":
        await _try_delete(msg)
        creds["password"] = txt
        draft["step"] = "tl_server"
        await msg.reply_text("🌐 <b>الخطوة 3 من 3:</b> أرسل اسم السيرفر (مثال: <code>FundedNext-Demo</code>):", parse_mode="HTML")
        return True

    if step == "tl_server":
        creds["server"] = txt
        context.user_data.pop("bridge_draft", None)
        return await _finish_user_connect(msg, uid, plat, amode, creds)

    return False


async def _finish_user_connect(msg, uid: int, plat: str, amode: str, creds: dict) -> bool:
    res = await afn.run(ab.save_connection, uid, plat, amode, creds=creds, partial_tp_be=True)
    if not res.get("ok"):
        await msg.reply_text(
            f"❌ <b>تعذر حفظ الربط:</b> {esc(res.get('error') or 'خطأ')}",
            reply_markup=_nav_for(uid, [[kb.btn("↩️ جسر النسخ", "u:bridge")]]),
            parse_mode="HTML",
        )
        return True
    cid = res["connection"]["id"]
    # Immediately run live verification so the user gets instant balance feedback!
    tst = await afn.run(ab.test_connection, cid, uid, uid == config.ADMIN_ID)
    status_line = tst.get("message") if tst.get("ok") else f"⚠️ تم الحفظ لكن تعذر الفحص الآن: {tst.get('error') or ''}"
    await msg.reply_text(
        f"🔐 <b>تم حفظ وتشفير حساب {esc(ab.PLATFORMS[plat]['name'])} ({amode}) بنجاح!</b>\n"
        f"<blockquote>{esc(str(status_line))}</blockquote>\n"
        "اضغط الزر بالأسفل لتجربة فتح صفقة اختبار حقيقية <b>0.01 لوت</b> الآن:",
        reply_markup=_nav_for(uid, [
            [kb.btn("🧪 إرسال صفقة اختبار 0.01 لوت الآن", f"u:br_testtrade:{cid}", style="success")],
            [kb.btn("🔍 إعادة فحص الاتصال", f"u:br_test:{cid}", style="primary"),
             kb.btn("⚡ جسر النسخ", "u:bridge")],
        ]),
        parse_mode="HTML",
    )
    return True
