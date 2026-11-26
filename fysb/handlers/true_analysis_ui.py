"""v8.4.0 — Ultra-Luxury Telegram UI for «👑 اشتراك تحليل ForexYemeni الحقيقي».

Handles BOTH:
  - User flows: `u:true_analysis`, `u:tatrial`, `u:tabuy`, `u:tausdt:<chain>`,
    `u:tapay:<mid>`, `u:tamoncancel:<id>`
  - Admin flows: `a:true_analysis`, `a:tatoggle_global`, `a:taset:<price|days|trial>`,
    `a:talist`, `a:utamenu:<uid>`, `a:utagrant:<uid>:<days|trial>`, `a:utarevoke:<uid>`
"""
from __future__ import annotations

from fysb import afn, config, db, instruments
from fysb.handlers.common import render
from fysb.services import audit as audit_log
from fysb.services import payments as payments_service
from fysb.services import true_analysis as ta_svc
from fysb.theme import esc, human_dt
from fysb.ui import keyboards as kb


def _is_active_partner_sync(uid: int) -> bool:
    if uid == config.ADMIN_ID:
        return False
    try:
        from fysb.services import partners as _partners
        p = _partners.get_partner_by_user(uid)
        return bool(p and p.get("enabled"))
    except Exception:
        return False


async def _nav_for(uid: int, extra_rows: list | None = None):
    """Role-aware luxury navigation: Admin -> admin_nav, Partner -> partner_nav (p:home), User -> user_nav."""
    if uid == config.ADMIN_ID:
        return kb.admin_nav(extra_rows)
    if await afn.run(_is_active_partner_sync, uid):
        from telegram import InlineKeyboardMarkup
        rows = list(extra_rows or [])
        rows.append([kb.btn("👑 لوحة الشريك الرئيسية", "p:home", style="primary")])
        return InlineKeyboardMarkup(rows)
    return kb.user_nav(extra_rows)


# ===========================================================================
# USER SIDE — Ultra-Luxury Showcase, Free Trial, Checkout & Live Radar Center
# ===========================================================================
async def render_user_true_analysis(q, uid: int, context, bot, notice: str = "") -> None:
    st = await afn.run(ta_svc.has_access, uid)
    price = st["price_usd"]
    days = st["duration_days"]
    trial_days = st["trial_days"]

    if st["active"]:
        mons = await afn.run(ta_svc.user_monitors, uid)
        if st["mode"] == "ADMIN":
            badge = "👑 صلاحية الإدارة العليا — مفتوح بلا حدود"
        elif st["mode"] == "LIFETIME":
            badge = "👑 اشتراك ملكي دائم — مفتوح بلا حدود"
        elif st["mode"] == "TRIAL":
            badge = f"🎁 فترة تجريبية نشطة · متبقي <b>{st['days_left']} يوم</b>"
        else:
            badge = f"🟢 اشتراك مفعّل · متبقي <b>{st['days_left']} يوم</b>"

        lines = [
            "👑 <b><u>مركز رادار تحليل ForexYemeni الحقيقي — مفعّل</u></b>",
            "<blockquote>"
            f"▎💎 <b>حالة الاشتراك:</b> {badge}\n"
            "▎⚡ <b>التنفيذ الفوري:</b> عند اكتمال شروط الشارت لحظياً تُصدر إشارتك الخاصة فوراً مع الشارت.\n"
            "▎👁‍🗨 <b>المراقبة المزدوجة الحية:</b> أراقب إغلاق شمعة الثبات بجسم حقيقي (≥ 55%) ثم تأكيد الإطار الأصغر وأطلق إشارتك الخاصة تلقائياً.\n"
            "▎🛡 <b>حارس الاستمرارية:</b> أثناء نشاط إشارتك لا يُصدر لك تحليلاً متضارباً بل يعرض لك استمرارية الصفقة والأرباح الحية."
            "</blockquote>",
        ]
        if notice:
            lines.append(f"<i>✨ {esc(notice)}</i>")

        rows_kb = []
        if not mons:
            lines.append("\n📡 <b>راداراتك النشطة الآن:</b> لا توجد مراقبات نشطة حالياً — اطلب أي تحليل وسيتولى الرادار متابعته تلقائياً!")
        else:
            lines.append(f"\n📡 <b><u>راداراتك وإشاراتك النشطة الآن ({len(mons)}):</u></b>")
            for m in mons[:8]:
                sym = m["symbol"]
                d_ic = "🟢 BUY" if m["direction"] == "BUY" else "🔴 SELL"
                mst = m["status"]
                if mst == "SIGNAL_ACTIVE":
                    st_lbl = f"💎 إشارة نشطة ومستمرة (#{m['signal_id']})"
                    rows_kb.append([kb.btn(f"📊 شارت إشارة {sym} (#{m['signal_id']})", f"u:sigchart:{m['signal_id']}", style="success")])
                elif mst == "WATCHING_CONFIRM":
                    st_lbl = f"✅ تأكّد {m['stability_tf']} · ⏳ بانتظار {m['confirm_tf']}"
                    rows_kb.append([kb.btn(f"🗑 إلغاء مراقبة {sym}", f"u:tamoncancel:{m['id']}", style="danger")])
                else:
                    st_lbl = f"⏳ بانتظار إغلاق {m['stability_tf']} + تأكيد {m['confirm_tf']}"
                    rows_kb.append([kb.btn(f"🗑 إلغاء مراقبة {sym}", f"u:tamoncancel:{m['id']}", style="danger")])
                lines.append(f"▎<b>{esc(sym)}</b> ({d_ic}) — {st_lbl}")

        rows_kb.append([kb.btn("👑 ابدأ تحليل ForexYemeni الحقيقي الآن", "u:ta_anl", style="success")])
        if st["mode"] == "TRIAL":
            rows_kb.append([kb.btn(f"👑 الترقية للاشتراك الكامل ({price:g}$ / {days} يوم)", "u:tabuy", style="primary")])
        await render(q, "\n".join(lines), await _nav_for(uid, rows_kb), bot)
        return

    # Non-subscriber Ultra-Luxury Royal Pitch Card
    trial_line = (
        f"▎🎁 <b>فترة تجريبية مجانية متاحة لك الآن:</b> <b>{trial_days} أيام كاملة</b> لتختبر الدقة الحقيقية بنفسك!\n"
        if st["trial_available"] else ""
    )
    lines = [
        "👑 <b><u>اشتراك تحليل ForexYemeni الحقيقي — النسخة المؤسسية الخاصة</u></b>",
        "<blockquote>"
        "▎💎 <b>ميزة حصرية نخبوية — غير مفعّلة للمستخدمين العاديين ولا للقنوات!</b>\n"
        "▎صُممت لتعطيك <b>تحليلاً حقيقياً 100% يتحوّل تلقائياً إلى إشارة حقيقية خاصة بك وحدك</b> مع شارت ForexYemeni المؤسسي."
        "</blockquote>",
        "\n✨ <b><u>ما الذي تحصل عليه بشكل حقيقي ومضمون 100%؟</u></b>",
        "<blockquote>"
        "▎1️⃣ <b>رادار مراقبة الشارت بمرحلتين (صفر وهم):</b>\n"
        "   لا تراقب الشارت بنفسك! عند طلب أي تحليل يراقب المحرك <b>إغلاق شمعة الثبات الحقيقية</b> (مثل H1 بجسم ≥ 55% وفلتر ATR لمنع الكسر الكاذب) ثم <b>تأكيد الإطار التنفيذي</b> (مثل M15)، ويُرسل لك <b>الإشارة الحقيقية + الشارت المحدّث</b> فور اكتمال الشرطين.\n"
        "▎2️⃣ <b>التنفيذ الفوري المباشر الذكي ⚡:</b>\n"
        "   إن كانت شروط الشارت مكتملة ومغلقة فعلياً لحظة التحليل، لا نُضيّع الفرصة — تصلك الإشارة الحقيقية والشارت <b>فوراً في نفس اللحظة</b>.\n"
        "▎3️⃣ <b>حارس الاستمرارية ومنع التضارب 🛡:</b>\n"
        "   إذا طلبت تحليلاً آخر وإشارتك ما زالت نشطة، يخبرك المحرك فوراً بأن تحليلك وإشارتك مستمرّان ويعرض لك الأرباح والنقاط الحية.\n"
        "▎4️⃣ <b>إلغاء وحماية تلقائية ⛔:</b>\n"
        "   إن كسر السعر مستوى الإبطال قبل التأكيد، يُلغى التحليل فوراً ويصلك تنبيه حماية — لا توجد إشارات عشوائية أبداً.\n"
        "▎5️⃣ <b>خصوصية كاملة 100% 🔒:</b>\n"
        "   إشاراتك وتتبّع أهدافك (TP1 / TP2 / TP3 / Break-Even) خاصة بحسابك فقط ولا تُنشر في أي قناة."
        "</blockquote>",
        "<blockquote>"
        f"▎💰 <b>رسوم الاشتراك الحصري:</b> <b>{price:g} USD</b> · المدة: <b>{days} يوماً</b>\n"
        f"{trial_line}"
        "▎👑 كما يمكن للإدارة تفعيل الميزة لك مباشرة من لوحة التحكم."
        "</blockquote>",
    ]
    if notice:
        lines.append(f"<i>⚠️ {esc(notice)}</i>")

    rows_kb = []
    if st["trial_available"]:
        rows_kb.append([kb.btn(
            f"🎁 تفعيل الفترة التجريبية المجانية ({trial_days} أيام) الآن",
            "u:tatrial",
            style="success",
        )])
    if st["enabled_global"]:
        rows_kb.append([kb.btn(
            f"👑 الاشتراك الآن — {price:g}$ / {days} يوم",
            "u:tabuy",
            style="primary",
        )])
    rows_kb.append([kb.btn("💎 عرض باقات المنصة", "u:plans"), kb.btn("🔬 التحليلات", "u:analytics")])
    await render(q, "\n".join(lines), kb.user_nav(rows_kb), bot)


async def _render_ta_checkout(q, uid: int, context, bot) -> None:
    st = await afn.run(ta_svc.has_access, uid)
    price = st["price_usd"]
    days = st["duration_days"]
    methods = await afn.run(payments_service.list_methods, enabled_only=True)
    chains = await afn.run(payments_service.configured_chains)
    rows_kb = []
    for ch in chains:
        rows_kb.append([kb.btn(f"⚡ دفع فوري USDT ({ch}) — {price:g}$", f"u:tausdt:{ch}", style="success")])
    for m in methods:
        rows_kb.append([kb.btn(f"💳 {m['title']} — {price:g}$", f"u:tapay:{m['id']}", style="primary")])
    if not rows_kb:
        rows_kb.append([kb.btn("🧾 طلب اشتراك (دفع يدوي)", "u:tapay:0", style="primary")])
        rows_kb.append([kb.btn("🎧 تواصل مع الإدارة للتفعيل المباشر", "u:support", style="primary")])
    rows_kb.append([kb.btn("↩️ العودة إلى تفاصيل الميزة", "u:true_analysis")])
    body = (
        f"👑 <b><u>الاشتراك في تحليل ForexYemeni الحقيقي</u></b>\n"
        f"<blockquote>"
        f"▎💰 <b>المبلغ:</b> <b>{price:g} USD</b>\n"
        f"▎📅 <b>المدة:</b> <b>{days} يوماً</b>\n"
        f"▎⚡ <b>التفعيل:</b> تلقائي فوري بعد تأكيد الدفع أو موافقة الإدارة."
        f"</blockquote>\n"
        f"اختر وسيلة الدفع المناسبة لك بالأسفل:"
    )
    await render(q, body, kb.user_nav(rows_kb), bot)


async def handle_user_true_analysis(q, uid: int, action: str, context, bot) -> bool:
    """Return True if `action` was handled by this module."""
    if action == "true_analysis":
        await render_user_true_analysis(q, uid, context, bot)
        return True

    if action == "tatrial":
        res = await afn.run(ta_svc.activate_user_trial, uid)
        if res.get("ok"):
            await render_user_true_analysis(
                q, uid, context, bot,
                notice=f"🎉 تهانينا! تم تفعيل فترتك التجريبية المجانية ({res['days']} أيام) بنجاح — اطلب أول تحليل الآن!",
            )
        else:
            await render_user_true_analysis(q, uid, context, bot, notice=res.get("error", "تعذر التفعيل"))
        return True

    if action == "tabuy":
        await _render_ta_checkout(q, uid, context, bot)
        return True

    if action.startswith("tausdt:"):
        chain = action.split(":", 1)[1].strip().upper()
        chains = await afn.run(payments_service.configured_chains)
        if chain not in chains:
            await render(
                q,
                "⛓ هذه الشبكة غير موثقة حالياً — اختر شبكة أخرى أو وسيلة دفع مفعّلة.",
                kb.user_nav([[kb.btn("↩️ طرق الدفع", "u:tabuy")]]),
                bot,
            )
            return True
        cfg = await afn.run(ta_svc.settings_snapshot)
        pid = await afn.run(payments_service.create_request, uid, "TRUE_ANALYSIS", cfg["price_usd"], f"USDT-{chain}")
        req = await afn.run(payments_service.get_request, pid)
        from fysb.handlers.user_pages import _render_chain_payment
        await _render_chain_payment(q, bot, req, chain)
        return True

    if action.startswith("tapay:"):
        try:
            mid = int(action.split(":", 1)[1].strip())
        except ValueError:
            mid = 0
        cfg = await afn.run(ta_svc.settings_snapshot)
        m_row = await afn.run(payments_service.get_method, mid) if mid else None
        method = dict(m_row) if m_row else None
        pid = await afn.run(
            payments_service.create_request,
            uid,
            "TRUE_ANALYSIS",
            cfg["price_usd"],
            method["title"] if method else "MANUAL",
        )
        context.user_data["awaiting_receipt"] = pid
        acct_ref = (method.get("account_ref") or "") if method else ""
        instr = (method.get("instructions") or "") if method else ""
        lines = [
            f"🧾 <b><u>طلب اشتراك تحليل ForexYemeni الحقيقي #{pid}</u></b>",
            f"<blockquote>"
            f"▎💰 <b>المبلغ:</b> <b>{cfg['price_usd']:g} USD</b> · المدة: <b>{cfg['duration_days']} يوماً</b>\n"
            f"▎💳 <b>الوسيلة:</b> {esc(method['title'] if method else 'تحويل يدوي')}\n"
            + (f"▎📋 <b>بيانات التحويل:</b> <code>{esc(acct_ref)}</code>\n" if acct_ref else "")
            + (f"▎📝 <b>التعليمات:</b> {esc(instr)}\n" if instr else "")
            + "</blockquote>\n"
            "📸 <b>أرسل الآن صورة إيصال التحويل أو رقم العملية هنا في المحادثة</b>، وسيتم التفعيل فور مراجعة الإدارة.",
        ]
        await render(q, "\n".join(lines), kb.user_nav([[kb.btn("↩️ العودة", "u:true_analysis")]]), bot)
        return True

    if action.startswith("tamoncancel:"):
        try:
            mid = int(action.split(":", 1)[1].strip())
        except ValueError:
            mid = 0
        ok = await afn.run(ta_svc.cancel_monitor, uid, mid) if mid else False
        msg = "تم إلغاء المراقبة بنجاح — يمكنك الآن طلب تحليل ForexYemeni الحقيقي من جديد." if ok else "المراقبة غير نشطة أو انتهت مسبقاً."
        await render_user_true_analysis(q, uid, context, bot, notice=msg)
        return True

    # ------------------------------------------------------------------
    # v8.5.2: Dedicated, 100% Separate «👑 تحليل ForexYemeni الحقيقي» Wizard
    # (u:ta_anl -> u:ta_cat: -> u:ta_sym: -> u:ta_tf: -> u:ta_run:)
    # Completely separated from standard «🔬 تحليل الآن» (u:analytics)!
    # ------------------------------------------------------------------
    if action == "ta_anl" or action.startswith("ta_cat:"):
        st = await afn.run(ta_svc.has_access, uid)
        if not st["active"]:
            await render_user_true_analysis(
                q, uid, context, bot,
                notice="هذه الميزة الحصرية تتطلب تفعيل اشتراك أو فترة تجريبية لـ «تحليل ForexYemeni الحقيقي».",
            )
            return True
        parts = action.split(":") if action.startswith("ta_cat:") else []
        cat = parts[1].strip().lower() if len(parts) > 1 else ""
        try:
            pg = max(1, int(parts[2])) if len(parts) > 2 else 1
        except ValueError:
            pg = 1
        await _render_ta_market_hub(q, uid, bot, category=cat, page=pg)
        return True

    if action.startswith("ta_sym:"):
        st = await afn.run(ta_svc.has_access, uid)
        if not st["active"]:
            await render_user_true_analysis(q, uid, context, bot)
            return True
        symbol = instruments.normalize(action.split(":", 1)[1].strip())
        # Continuity Guard runs EXCLUSIVELY inside True Analysis (never blocks normal «تحليل الآن»)
        cont = await afn.run(ta_svc.check_active_continuity, uid, symbol)
        if cont:
            live_px = 0.0
            if cont["kind"] == "ACTIVE_SIGNAL":
                try:
                    import asyncio as _asyncio
                    from fysb.engines import market_data as _md
                    mrow_c = await afn.run(_load_market_row, symbol)
                    psym_c = (mrow_c["provider_symbol"] if mrow_c else symbol)
                    q_px = await _asyncio.to_thread(_md.fetch_price, psym_c, config.market_api_key(), True)
                    live_px = float((q_px or {}).get("price") or 0.0)
                except Exception:
                    live_px = 0.0
            c_body, c_rows = await afn.run(ta_svc.render_continuity_card, cont, live_px)
            await render(q, c_body, await _nav_for(uid, c_rows), bot)
            return True

        context.user_data["ta_draft"] = {"symbol": symbol}
        tf_pairs = [
            ("M15", "⏱ M15 → M5 (مضاربة سريعة)"),
            ("M30", "⏱ M30 → M15 (متوازن)"),
            ("H1",  "👑 H1 → M15 (مؤسسي موصى به)"),
            ("H4",  "💎 H4 → H1 (اتجاهي كبير)"),
            ("D1",  "🏛 D1 → H1 (يومي سوينغ)"),
        ]
        body = (
            f"👑 <b><u>تحليل ForexYemeni الحقيقي — {esc(symbol)}</u></b>\n"
            "<blockquote>"
            "▎⏱ <b>اختر إطار الثبات والتأكيد للرادار الحي:</b>\n"
            "▎سيراقب الرادار إغلاق الشمعة الكبرى بجسم حقيقي (≥ 55%) ثم تأكيد الإطار الأصغر "
            "مع جميع الدروع المؤسسية الـ 5 (عطلة السوق · الأخبار القوية · منع المطاردة · ارتداد OB/FVG · أهداف السيولة)."
            "</blockquote>"
        )
        rows = [[kb.btn(lbl, f"u:ta_tf:{code}", style="success" if code == "H1" else "primary")]
                for code, lbl in tf_pairs]
        rows.append([kb.btn("↩️ تغيير السوق", "u:ta_anl")])
        await render(q, body, await _nav_for(uid, rows), bot)
        return True

    if action.startswith("ta_tf:"):
        st = await afn.run(ta_svc.has_access, uid)
        if not st["active"]:
            await render_user_true_analysis(q, uid, context, bot)
            return True
        draft = context.user_data.get("ta_draft") or {}
        if not draft.get("symbol"):
            await _render_ta_market_hub(q, uid, bot)
            return True
        tf_code = action.split(":", 1)[1].strip().upper()
        draft["tf"] = tf_code
        context.user_data["ta_draft"] = draft
        from fysb.services import strategy_stats as _ss
        from fysb.theme import strategy_name
        enabled_s = await afn.run(_enabled_strategy_codes_sync)
        universe = await afn.run(_ss.ordered_codes, sorted(enabled_s)) if enabled_s else ["ICT", "SMC", "PRICE_ACTION"]
        body = (
            f"👑 <b><u>تحليل ForexYemeni الحقيقي — {esc(draft['symbol'])} ({esc(tf_code)})</u></b>\n"
            "<blockquote>▎🧠 اختر المنهجية المؤسسية لتشغيل التحليل الحقيقي والرادار الحي:</blockquote>"
        )
        rows = [[kb.btn("👑 المنظومة المؤسسية الشاملة (تختار الأقوى تلقائياً)", "u:ta_run:ALL", style="success")]]
        for i in range(0, len(universe), 2):
            rows.append([kb.btn(strategy_name(s), f"u:ta_run:{s}") for s in universe[i:i + 2]])
        rows.append([kb.btn("↩️ تغيير الإطار", f"u:ta_sym:{draft['symbol']}")])
        await render(q, body, await _nav_for(uid, rows), bot)
        return True

    if action.startswith("ta_run:"):
        import asyncio as _asyncio
        from fysb.services import analytics as anl
        from fysb.ui import charts as _charts
        from fysb.ui.cards import render_analysis_card

        st = await afn.run(ta_svc.has_access, uid)
        if not st["active"]:
            await render_user_true_analysis(q, uid, context, bot)
            return True
        draft = context.user_data.get("ta_draft") or {}
        if not draft.get("symbol") or not draft.get("tf"):
            await _render_ta_market_hub(q, uid, bot)
            return True
        strategy = action.split(":", 1)[1].strip().upper() or "ALL"
        sym = draft["symbol"]
        tf_code = draft["tf"]
        context.user_data.pop("ta_draft", None)

        await render(
            q,
            f"👑 <b>جارٍ تشغيل محرك تحليل ForexYemeni الحقيقي...</b>\n"
            f"<blockquote>▎💎 السوق: <b>{esc(sym)}</b> · الإطار: <b>{esc(tf_code)}</b> · المنهجية: <b>{esc(strategy)}</b>\n"
            f"▎🛡 جارٍ فحص الشموع المغلقة الحقيقية ومناطق الأوامر (OB/FVG) ودرع الأخبار والسيولة...</blockquote>",
            None,
        )
        res = await _asyncio.to_thread(
            anl.run_analysis, uid, sym, tf_code, "", "", strategy, enforce_plan=False,
        )
        if not res.get("ok"):
            await render(
                q,
                f"⛔ {esc(res.get('error') or 'تعذر قراءة السوق حالياً')}",
                await _nav_for(uid, [[kb.btn("↩️ المحاولة مرة أخرى", "u:ta_anl", style="primary")]]),
                bot,
            )
            return True

        an = res["analysis"]
        row = await afn.run(_load_market_row, an["symbol"])
        chat_id = getattr(getattr(q, "message", None), "chat_id", None) or uid
        # Always send Ultra-Luxury 1600x1000 Institutional Analysis Chart PNG!
        await _charts.send_analysis_chart_photo(bot, chat_id, an, market_row=row)

        card_text = render_analysis_card(an, market_row=row)
        ta_res = await afn.run(ta_svc.evaluate_on_analysis_created, uid, an)
        ta_mode = ta_res.get("mode")
        rows_out = [
            [kb.btn("👑 لوحة رادار تحليلاتي", "u:true_analysis", style="success")],
            [kb.btn("👑 تحليل سوق آخر (ForexYemeni الحقيقي)", "u:ta_anl", style="primary")],
        ]
        if ta_mode == "MONITORING":
            card_text += ta_svc.render_monitoring_started_banner(ta_res)
        elif ta_mode == "INSTANT_SIGNAL":
            card_text += (
                "\n\n⚡ <b><u>تنفيذ فوري حقيقي — رادار ForexYemeni الحقيقي</u></b>\n"
                "<blockquote>▎شروط الإغلاق والثبات مكتملة فعلياً على الشارت الآن! "
                "تم إصدار إشارتك الحقيقية الخاصة مع الشارت في الرسالة التالية فوراً ✅</blockquote>"
            )
        elif ta_mode == "NEUTRAL":
            card_text += (
                "\n\n🟡 <b><u>رادار ForexYemeni الحقيقي — وضع الانتظار الذكي</u></b>\n"
                "<blockquote>▎السوق حيادي/عرضي حالياً بلا اتجاه مؤسسي حاسم — لم يُفعّل رادار وهمي حفاظاً على رأس مالك.</blockquote>"
            )

        await render(q, card_text, await _nav_for(uid, rows_out), bot)

        if ta_mode == "INSTANT_SIGNAL" and bot is not None:
            try:
                await ta_svc._deliver_private_signal_with_chart(
                    bot,
                    uid,
                    ta_res["signal"],
                    instant=True,
                    stab_tf=ta_res["stability_tf"],
                    conf_tf=ta_res["confirm_tf"],
                    body_pct=float(ta_res.get("body_pct") or 65.0),
                    candles=ta_res.get("candles") or [],
                    trigger_kind=str(ta_res.get("trigger_kind") or "BREAKOUT"),
                    tp_grounded=bool(ta_res.get("tp_grounded")),
                )
            except Exception:
                pass
        return True

    return False


def _load_market_row(symbol: str) -> dict | None:
    with db.conn() as c:
        r = c.execute("SELECT * FROM market_catalog WHERE UPPER(symbol)=?", (symbol.upper(),)).fetchone()
    return dict(r) if r else None


def _enabled_strategy_codes_sync() -> list[str]:
    with db.conn() as c:
        rows = c.execute("SELECT code FROM strategy_catalog WHERE enabled=1").fetchall()
    return [str(r["code"]).upper() for r in rows if r["code"]]


async def _render_ta_market_hub(q, uid: int, bot, *, category: str = "", page: int = 1) -> None:
    """Dedicated categorized market selector for «👑 تحليل ForexYemeni الحقيقي»."""
    from fysb.theme import CATEGORY_AR, CATEGORY_ICON, CATEGORY_ORDER

    def _fetch_live_markets():
        with db.conn() as c:
            return [dict(r) for r in c.execute(
                "SELECT symbol, title, category FROM market_catalog WHERE enabled=1 AND verified=1 ORDER BY symbol"
            ).fetchall()]

    rows_db = await afn.run(_fetch_live_markets)
    by_cat: dict[str, list[dict]] = {}
    for r in rows_db:
        c_key = str(r.get("category") or instruments.spec(r["symbol"]).category or "forex").lower()
        by_cat.setdefault(c_key, []).append(r)

    ordered_cats = [c for c in CATEGORY_ORDER if by_cat.get(c)] + [c for c in by_cat if c not in CATEGORY_ORDER]
    cat = (category or "").strip().lower()

    if cat and by_cat.get(cat):
        items = sorted(by_cat[cat], key=lambda r: (0 if r["symbol"] == "XAUUSD" else 1, r["symbol"]))
        per_page = 12
        total_pages = max(1, (len(items) + per_page - 1) // per_page)
        pg = max(1, min(page, total_pages))
        chunk = items[(pg - 1) * per_page: pg * per_page]
        icon = CATEGORY_ICON.get(cat, "🌐")
        title = CATEGORY_AR.get(cat, cat)
        body = (
            f"👑 <b><u>تحليل ForexYemeni الحقيقي — {icon} {title}</u></b>\n"
            f"<blockquote>▎اختر السوق لتشغيل التحليل الحقيقي + الرادار المزدوج + الشارت المؤسسي "
            f"(صفحة {pg}/{total_pages}):</blockquote>"
        )
        kb_rows = [[kb.btn(f"👑 {r['symbol']}", f"u:ta_sym:{r['symbol']}", style="primary") for r in chunk[i:i + 3]]
                   for i in range(0, len(chunk), 3)]
        if total_pages > 1:
            nav = []
            if pg > 1:
                nav.append(kb.btn("◀️ السابق", f"u:ta_cat:{cat}:{pg - 1}"))
            if pg < total_pages:
                nav.append(kb.btn("التالي ▶️", f"u:ta_cat:{cat}:{pg + 1}"))
            if nav:
                kb_rows.append(nav)
        kb_rows.append([kb.btn("↩️ جميع الأقسام", "u:ta_anl")])
        await render(q, body, await _nav_for(uid, kb_rows), bot)
        return

    body = (
        "👑 <b><u>تحليل ForexYemeni الحقيقي — اختر السوق</u></b>\n"
        "<blockquote>"
        "▎💎 <b>منفصل بالكامل عن «تحليل الآن» العادي!</b>\n"
        "▎هنا يعمل المحرك المؤسسي الكامل (الشارت الفاخر + رادار الإغلاق المزدوج + ارتداد OB/FVG + درع الأخبار وعطلة السوق + إشاراتك الخاصة)."
        "</blockquote>"
    )
    kb_rows = []
    # Quick royal picks (XAUUSD, BTCUSD, EURUSD if enabled)
    live_syms = {r["symbol"].upper() for r in rows_db}
    quick = [s for s in ("XAUUSD", "BTCUSD", "EURUSD", "GBPUSD") if s in live_syms]
    if quick:
        kb_rows.append([kb.btn(f"⚡ {s}", f"u:ta_sym:{s}", style="success") for s in quick[:3]])
    cat_btns = [
        kb.btn(f"{CATEGORY_ICON.get(c, '🌐')} {CATEGORY_AR.get(c, c)} ({len(by_cat[c])})", f"u:ta_cat:{c}:1", style="primary")
        for c in ordered_cats
    ]
    for i in range(0, len(cat_btns), 2):
        kb_rows.append(cat_btns[i:i + 2])
    kb_rows.append([kb.btn("👁‍🗨 لوحة رادار تحليلاتي", "u:true_analysis")])
    await render(q, body, await _nav_for(uid, kb_rows), bot)


# ===========================================================================
# ADMIN SIDE — Editable Settings Studio + Per-User 1-Tap Grant/Revoke
# ===========================================================================
async def render_admin_studio(q, uid: int, context, bot, notice: str = "") -> None:
    cfg = await afn.run(ta_svc.settings_snapshot)
    st_lbl = "🟢 مفعّل عالمياً" if cfg["enabled_global"] else "🔴 متوقف مؤقتاً"
    trial_lbl = f"<b>{cfg['trial_days']} أيام</b> 🎁" if cfg["trial_days"] > 0 else "<b>معطّلة (0)</b> 🔒"
    lines = [
        "👑 <b><u>إدارة اشتراك تحليل ForexYemeni الحقيقي</u></b>",
        "<blockquote>"
        f"▎⚡ <b>الحالة العامة:</b> {st_lbl}\n"
        f"▎💰 <b>سعر الاشتراك:</b> <b>{cfg['price_usd']:g} USD</b>\n"
        f"▎📅 <b>مدة الاشتراك:</b> <b>{cfg['duration_days']} يوماً</b>\n"
        f"▎🎁 <b>المدة التجريبية للمستخدمين:</b> {trial_lbl}\n"
        f"▎👥 <b>المشتركون النشطون:</b> <b>{cfg['subscribers_count']}</b> · "
        f"👁‍🗨 <b>رادارات تراقب الآن:</b> <b>{cfg['watching_count']}</b> · "
        f"💎 <b>إشارات تحليل نشطة:</b> <b>{cfg['active_signals_count']}</b>"
        "</blockquote>",
        "<i>✨ كل الإعدادات قابلة للتعديل الفوري بالأسفل، كما يمكنك فتح الميزة أو إيقافها لأي مستخدم من ملفه الشخصي داخل «👥 المستخدمين».</i>",
    ]
    if notice:
        lines.append(f"\n<i>✨ {esc(notice)}</i>")

    rows_kb = [
        [kb.btn("👑 تشغيل تحليل ForexYemeni الحقيقي الآن", "u:ta_anl", style="success")],
        [kb.btn(f"💰 تعديل السعر ({cfg['price_usd']:g}$)", "a:taset:price", style="primary"),
         kb.btn(f"📅 تعديل المدة ({cfg['duration_days']} يوم)", "a:taset:days", style="primary")],
        [kb.btn(f"🎁 تعديل المدة التجريبية ({cfg['trial_days']} يوم)", "a:taset:trial", style="success")],
        [kb.btn(
            "🔴 إيقاف الميزة مؤقتاً" if cfg["enabled_global"] else "🟢 تفعيل الميزة عالمياً",
            "a:tatoggle_global",
            style="danger" if cfg["enabled_global"] else "success",
        )],
        [kb.btn("👥 عرض المشتركين والرادارات النشطة", "a:talist", style="primary")],
        [kb.btn("👥 فتح الميزة لمستخدم (قائمة المستخدمين)", "a:users")],
    ]
    await render(q, "\n".join(lines), kb.admin_nav(rows_kb), bot)


async def render_admin_user_ta_menu(q, uid: int, context, bot, target: int, notice: str = "") -> None:
    st = await afn.run(ta_svc.has_access, target)
    trial_days = max(1, st["trial_days"] or 3)
    status_txt = (
        ("👑 مفعّل دائم (مفتوح)" if st["mode"] == "LIFETIME"
         else f"🟢 مفعّل · متبقي <b>{st['days_left']} يوم</b> ({human_dt(st['until'])})")
        if st["active"] else "⚪ غير مفعّل لهذا المستخدم"
    )
    lines = [
        f"👑 <b><u>إدارة تحليل ForexYemeni الحقيقي — المستخدم {target}</u></b>",
        "<blockquote>"
        f"▎📌 <b>الحالة الحالية:</b> {status_txt}\n"
        f"▎🎁 <b>المدة التجريبية المضبوطة:</b> <b>{st['trial_days']} أيام</b> · "
        f"استُخدمت مسبقاً: <b>{'نعم' if st['trial_used'] else 'لا'}</b>"
        "</blockquote>",
    ]
    if notice:
        lines.append(f"<i>✨ {esc(notice)}</i>")

    rows_kb = [
        [kb.btn(f"🎁 تفعيل تجريبي ({trial_days} أيام)", f"a:utagrant:{target}:trial", style="success")],
        [kb.btn("✅ تفعيل 30 يوماً", f"a:utagrant:{target}:30", style="success"),
         kb.btn("✅ تفعيل 90 يوماً", f"a:utagrant:{target}:90", style="success")],
        [kb.btn("👑 تفعيل دائم مفتوح", f"a:utagrant:{target}:0", style="primary")],
    ]
    if st["active"]:
        rows_kb.append([kb.btn("⛔ إيقاف الميزة لهذا المستخدم", f"a:utarevoke:{target}", style="danger")])
    rows_kb.append([kb.btn("↩️ ملف المستخدم", f"a:userdetail:{target}")])
    await render(q, "\n".join(lines), kb.admin_nav(rows_kb), bot)


async def handle_admin_true_analysis(q, uid: int, act: str, arg: str, arg2: str, context, bot) -> bool:
    if act == "true_analysis":
        await render_admin_studio(q, uid, context, bot)
        return True

    if act == "tatoggle_global":
        cur = await afn.run(db.get_setting, "true_analysis_enabled_global", "1")
        nxt = "0" if cur == "1" else "1"
        await afn.run(db.set_setting, "true_analysis_enabled_global", nxt)
        await afn.run(audit_log.log, uid, f"true_analysis_global:{nxt}")
        await render_admin_studio(
            q, uid, context, bot,
            notice="تم تفعيل ميزة تحليل ForexYemeni الحقيقي 🟢" if nxt == "1" else "تم إيقاف الميزة مؤقتاً 🔴",
        )
        return True

    if act == "taset":
        field = (arg or "").strip().lower()
        prompts = {
            "price": "💰 أرسل الآن <b>سعر اشتراك تحليل ForexYemeni الحقيقي بالدولار (USD)</b> (مثال: <code>79</code> أو <code>99</code>):",
            "days": "📅 أرسل الآن <b>مدة الاشتراك بالأيام</b> (مثال: <code>30</code> أو <code>90</code>):",
            "trial": "🎁 أرسل الآن <b>عدد أيام الفترة التجريبية المجانية</b> (مثال: <code>3</code> أو <code>7</code>، أو أرسل <code>0</code> لإيقاف الفترة التجريبية):",
        }
        if field not in prompts:
            await render_admin_studio(q, uid, context, bot)
            return True
        context.user_data["awaiting_ta_setting"] = field
        await render(
            q,
            prompts[field],
            kb.admin_nav([[kb.btn("↩️ إلغاء والعودة", "a:true_analysis")]]),
            bot,
        )
        return True

    if act == "talist":
        data = await afn.run(ta_svc.list_subscribers_and_monitors, 25)
        subs_l = data["subscribers"]
        mons_l = data["monitors"]
        lines = [
            "👥 <b><u>المشتركون والرادارات النشطة — تحليل ForexYemeni الحقيقي</u></b>",
            f"\n<b>💎 المشتركون المفعّلون ({len(subs_l)}):</b>",
        ]
        if not subs_l:
            lines.append("▎⚪ لا يوجد مشتركون مفعّلون حالياً.")
        else:
            for s in subs_l[:15]:
                un = f"@{s['username']}" if s.get("username") else str(s["user_id"])
                exp = human_dt(s["true_analysis_until"]) if s.get("true_analysis_until") else "دائم ♾"
                lines.append(f"▎👑 <code>{s['user_id']}</code> ({esc(un)}) — حتى: <b>{esc(exp)}</b>")
        lines.append(f"\n<b>👁‍🗨 الرادارات والإشارات النشطة ({len(mons_l)}):</b>")
        if not mons_l:
            lines.append("▎⚪ لا توجد مراقبات نشطة حالياً.")
        else:
            for m in mons_l[:15]:
                lines.append(
                    f"▎<code>#{m['id']}</code> · مستخدم <code>{m['user_id']}</code> · "
                    f"<b>{esc(m['symbol'])}</b> {m['direction']} · <b>{esc(m['status'])}</b>"
                )
        await render(q, "\n".join(lines), kb.admin_nav([[kb.btn("↩️ لوحة الميزة", "a:true_analysis", style="primary")]]), bot)
        return True

    if act == "utamenu":
        target = int(arg or 0)
        await render_admin_user_ta_menu(q, uid, context, bot, target)
        return True

    if act == "utagrant":
        target = int(arg or 0)
        raw_d = (arg2 or "30").strip().lower()
        cfg = await afn.run(ta_svc.settings_snapshot)
        is_trial = (raw_d == "trial")
        days = max(1, cfg["trial_days"] or 3) if is_trial else max(0, int(raw_d or 0))
        await afn.run(ta_svc.grant_access, target, days, uid, is_trial=is_trial)
        dur_txt = "دائم مفتوح ♾" if days == 0 else f"{days} يوماً"
        try:
            await bot.send_message(
                target,
                f"👑 <b><u>تهانينا! تم تفعيل «تحليل ForexYemeni الحقيقي» لحسابك</u></b>\n"
                f"<blockquote>"
                f"▎💎 <b>النوع:</b> {'فترة تجريبية ملكية 🎁' if is_trial else 'اشتراك مؤسسي خاص 👑'}\n"
                f"▎📅 <b>الصلاحية:</b> <b>{dur_txt}</b>\n"
                f"▎👁‍🗨 عند طلب أي تحليل الآن، سيراقب رادار ForexYemeni الشارت تلقائياً ويُرسل لك إشاراتك الحقيقية الخاصة مع الشارت فور التأكيد أو التنفيذ الفوري!"
                f"</blockquote>",
                parse_mode="HTML",
            )
        except Exception:
            pass
        await render_admin_user_ta_menu(
            q, uid, context, bot, target,
            notice=f"تم تفعيل الميزة للمستخدم {target} ({dur_txt}) وإرسال إشعار ملكي له ✅",
        )
        return True

    if act == "utarevoke":
        target = int(arg or 0)
        await afn.run(ta_svc.revoke_access, target, uid)
        try:
            await bot.send_message(
                target,
                "⛔ تم إيقاف ميزة «تحليل ForexYemeni الحقيقي» على حسابك من قبل الإدارة.",
                parse_mode="HTML",
            )
        except Exception:
            pass
        await render_admin_user_ta_menu(
            q, uid, context, bot, target,
            notice=f"تم إيقاف الميزة للمستخدم {target} بنجاح.",
        )
        return True

    return False
