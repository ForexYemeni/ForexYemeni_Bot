"""User-facing pages — luxury Arabic UI, every tile functional, quota enforced."""
import asyncio
from datetime import timedelta

from telegram import InlineKeyboardButton, Update
from telegram.ext import ContextTypes

from fysb import afn, config, db, instruments
from fysb.config import log
from fysb.handlers.common import render, safe
from fysb.services import payments as payments_service
from fysb.services import subscriptions as subs
from fysb.services import support as support_service
from fysb.services import users as users_service
from fysb.theme import ICON, bar, esc, human_dt, strategy_name, tf_name
from fysb.ui import keyboards as kb
from fysb.ui.cards import (render_analysis_card, render_plan_card,
                           render_signal_card, render_signal_compact,
                           render_user_card)

TICKET_TYPES = [("عام", "general"), ("مشكلة تقنية", "technical"), ("المدفوعات", "billing"), ("اقتراح", "suggestion")]


async def _upsell(q, uid, bot, why: str) -> None:
    """The luxury locked-content card — shown whenever a plan gate trips."""
    plan = await afn.run(subs.plan_for, uid)
    plans = await afn.run(_plans_for_sale)
    body = (f"{ICON['lock']} <b><u>محتوى ملكي — يتطلب باقة أعلى</u></b>\n"
            f"<blockquote>"
            f"▎⛔ {esc(why)}.\n"
            f"▎💎 باقتك الحالية: <b>{esc(plan['title'])}</b>\n"
            f"▎✨ رقّ باقتك لفتح أسواق وأطر ومنهجيات أكثر وحدوداً يومية أعلى.\n"
            f"</blockquote>")
    rows_kb = [[kb.btn(f"💎 {esc(p['title'])} · {p['price']:g} {p['currency']}", f"u:planview:{p['code']}")] for p in plans]
    rows_kb.append([kb.btn("↩️ رجوع", "u:preferences")])
    await render(q, body, kb.user_nav(rows_kb), bot)


async def _render_chain_payment(q, bot, req, chain: str) -> None:
    """v6.9.1 — the ROYAL chain payment page, shared by u:paychain (existing
    request) and u:usdtpay (documented-wallet fast lane from the plan page).
    One single source of truth: both entries render the identical contract."""
    from fysb.services.chain_verify import EVM_CHAINS
    chain = (chain or "").upper()
    label = {"TRC20": "USDT TRC20 (شبكة Tron)",
             **{k: v["label"] for k, v in EVM_CHAINS.items()}}.get(chain, chain)
    wallet = await afn.run(payments_service.chain_wallet, chain)
    pid = req["id"]
    if not wallet:
        await render(q, "⛓ هذه الشبكة غير مفعلة حالياً — اختر شبكة أخرى من صفحة الباقة أو تواصل مع الدعم.",
                     kb.user_nav([[kb.btn("🧾 مدفوعاتي", "u:payments")]]), bot)
        return
    body = [
        f"⛓ <b><u>تعليمات الدفع على {label}</u></b>",
        "<blockquote>"
        f"▎🧾 الطلب: <b>#{pid}</b> · المبلغ المطلوب: <b>{float(req['amount']):g} USDT</b>\n"
        f"▎⛓ أرسل المبلغ إلى العنوان الملكي التالي:\n"
        f"▎<code>{esc(wallet)}</code>\n"
        "▎⚠️ تأكد أن شبكة التحويل في محفظتك هي نفسها المختارة — الإرسال على شبكة خاطئة يعني خسارة الأموال.\n"
        "▎🔗 بعد نجاح التحويل انسخ <b>TXID</b> المعاملة (64 خانة) من محفظتك."
        "</blockquote>",
        "<i>بمجرد إرسال الـ TXID يُفحص البلوكيشن فعلياً: التأكيد + عقد USDT + عنوان الإدارة + المبلغ — "
        "وعند التوافق تُفتح باقتك فوراً ويُحجب الـ TXID نهائياً.</i>",
    ]
    await render(q, "\n".join(body), kb.user_nav([
        [kb.btn(f"⛓ أرسل TXID — تحقق فوري على {chain}", f"u:usdttxt:{pid}:{chain}")],
        [kb.btn("↩️ وسائل الدفع الأخرى", f"u:planview:{req['plan_code']}")],
        [kb.btn("🧾 مدفوعاتي", "u:payments")],
    ]), bot)


async def user_page(q, action: str, uid: int, context) -> None:
    bot = context.bot
    # v6.8.1 hot path: throttled last_seen + profile read in ONE pooled trip.
    # v6.11.0: every blocking call below runs in the worker pool — the loop
    # never waits on WAN, so buttons stay instant even while Supabase is slow.
    u = await afn.run(users_service.touch_and_get, uid)
    if not u:
        await render(q, "لم يتم العثور على حسابك. أرسل /start أولاً.", kb.user_menu(), bot)
        return

    # ---------- Home / profile ----------
    if action == "home":
        await render(q, f"👑 <b>{config.BRAND}</b>\nالقائمة الرئيسية:", kb.user_menu(), bot)

    elif action == "profile":
        profile = await afn.run(users_service.profile_summary, uid)
        await render(q, render_user_card(u, profile),
                     kb.user_nav([
                         [kb.btn("🎛 تخصيص الإشارات", "u:preferences"), kb.btn("💎 الاشتراك", "u:plans")],
                         [kb.btn("🧾 مدفوعاتي", "u:payments"), kb.btn("🎧 الدعم", "u:support")],
                     ]), bot)

    elif action == "dashboard":
        p = await afn.run(subs.plan_for, uid)
        state = await afn.run(subs.quota_state, uid)
        total, active, wins, delivered, anl_used = await afn.run(_dashboard_counts, uid)
        plan_title = esc(p["title"] if p else "Free")
        anl_quota = int(p["max_analyses_per_day"] or 0) if p else 0
        mrow = await afn.run(_market_row, u["market"])
        mcat = (mrow or {}).get("category") if mrow else instruments.spec(u["market"]).category
        from fysb.engines.market_data import market_state
        mstate = market_state(mcat)
        mstate_txt = "🟢 مفتوح" if mstate else ("🔴 مغلق" if mstate is False else "🟡 غير محدد")
        body = (f"💎 <b>الباقة:</b> {plan_title}\n\n"
                f"📡 <b>الإشارات:</b>\n"
                f"▎استهلاك اليوم: <b>{state['used']}/{state['quota']}</b> {bar(state['used'], state['quota'])}\n"
                f"▎🧾 إجمالي ما وصلتك: <b>{delivered}</b>\n\n"
                f"🔬 <b>التحليلات:</b>\n"
                f"▎استهلاك اليوم: <b>{anl_used}/{anl_quota}</b>\n\n"
                f"🛡 وضع المنصة: <b>{esc(await afn.run(db.get_setting, 'trading_mode', 'SIGNALS_ONLY'))}</b>\n"
                f"🌍 أفضلك ({esc(u['market'])}): <b>{mstate_txt}</b>\n"
                f"📈 إجمالي الإشارات المسجلة: <b>{total}</b> (نشطة: {active} · مكتملة: {wins})")
        await render(q, body, kb.user_nav([
            [kb.btn("📡 فتح الإشارات", "u:signals"), kb.btn("🔬 تحليل جديد", "u:analytics")],
            [kb.btn("🚀 إشارة الآن", "u:signow"), kb.btn("🎛 تخصيص التنبيهات", "u:preferences")],
            [kb.btn("💎 الباقات", "u:plans"), kb.btn("📈 إحصائياتي", "u:my_stats")],
        ]), bot)

    # ---------- Analytics (composite institutional engine, plan-gated) ----------
    elif action == "analytics":
        from fysb.services import analytics as anl
        if not await afn.run(anl.analytics_enabled):
            await render(q, "🔬 قسم التحليلات معطل حالياً من الإدارة.", kb.user_nav(), bot)
            return
        state = await afn.run(anl.quota_state, uid)
        plan = await afn.run(subs.plan_for, uid)
        is_admin = uid == config.ADMIN_ID
        p_markets = [x.strip().upper() for x in (plan["markets"] or "").split(",") if x.strip()]
        rows = await afn.run(_catalog_rows)
        if not rows:
            # LEGACY-DEAD CATALOG FALLBACK (picker-empty bug 2026-11-22): the
            # picker must never dead-end — offer the plan's own markets (the
            # catalog self-heals on the next boot anyway).
            from fysb.db import SEED_MARKETS
            titles = {m: t for m, t, *_rest in SEED_MARKETS}
            rows = [{"symbol": m, "title": titles.get(m, m), "enabled": 1, "verified": 1}
                    for m in p_markets]
        if is_admin:
            allowed_rows, locked_rows = list(rows), []
        else:
            allowed_rows = [r for r in rows if r["symbol"].upper() in p_markets]
            locked_rows = [r for r in rows if r["symbol"].upper() not in p_markets]
        if not allowed_rows and not locked_rows:
            await render(q, "لا توجد أسواق مفعلة بعد — تواصل مع الإدارة.", kb.user_nav(), bot)
            return

        # v6.8.0 — LUXURY CATEGORY SECTIONS (المعادن · الفوركس · العملات الرقمية
        # · المؤشرات · الأسهم): every market belongs to its royal section, with
        # a real state marker (🟢 open trading / 🔵 verified analysis / ⚪ closed).
        # v6.8.1 — OWNER RULE: «يجب أن تظهر له الكل ولكن الباقيات مغلقة ليشترك»
        # — the FULL catalog is rendered for everyone; markets outside the plan
        # appear with a 🔒 lock badge and tapping one opens the luxury upsell
        # card (subscribe path) instead of being hidden.
        from fysb.theme import CATEGORY_AR, CATEGORY_ICON

        allowed_syms = {r["symbol"].upper() for r in allowed_rows}

        def _state_icon(r) -> str:
            en, ver = int(r.get("enabled") or 0), int(r.get("verified") or 0)
            if r["symbol"].upper() not in allowed_syms and not is_admin:
                return "🔒"
            if en and ver:
                return "🟢"
            return "🔵" if ver else "⚪"

        def _section_rows(section_rows: list[dict]) -> list[list]:
            out: list[list] = []
            for i in range(0, len(section_rows), 3):
                chunk = section_rows[i:i + 3]
                out.append([kb.btn(f"{_state_icon(r)} {esc(r['symbol'])}",
                                   f"u:anlsym:{r['symbol']}")
                            for r in chunk])
            return out

        sections: dict[str, list] = {}
        for r in (list(rows) if not is_admin else allowed_rows):
            sections.setdefault((r.get("category") or instruments.spec(r["symbol"]).category),
                                []).append(r)
        from fysb.handlers.admin_trading import CATEGORY_ORDER
        ordered_sections = [c for c in CATEGORY_ORDER if c in sections] + \
                           [c for c in sections if c not in CATEGORY_ORDER]

        mkt_block = ""
        for cat in ordered_sections:
            icon = CATEGORY_ICON.get(cat, "🌐")
            title = CATEGORY_AR.get(cat, cat)
            grp = sections[cat]
            open_n = sum(1 for r in grp if r["symbol"].upper() in allowed_syms or is_admin)
            mkt_block += (f"\n{icon} <b><u>{title}</u></b> — "
                          f"{('كلها مفتوحة لك' if open_n == len(grp) else f'مفتوح لك {open_n} من {len(grp)}')}\n")
            for r in grp:
                mkt_block += f"▎{_state_icon(r)} <b>{esc(r['symbol'])}</b> — {esc(r['title'])}\n"
        if not is_admin and locked_rows:
            mkt_block += (f"\n{ICON['lock']} <b>الأسواق المقفولة 🔒 تفتح فوراً مع باقة أعلى — اضغط عليها لعرض الباقات.</b>")

        body = (f"🔬 <b><u>التحليلات الاحترافية المؤسسية</u></b>\n"
                f"<blockquote>"
                + (f"▎👑 لوحة الإدارة: <b>تحليل حر — أي سوق × أي منهجية × أي إطار، بلا حدود</b>\n"
                   if is_admin else
                   f"▎حصة اليوم: <b>{state['used']}/{state['quota']}</b> {bar(state['used'], state['quota'])}\n"
                   f"▎💎 باقتك: <b>{esc(plan['title'])}</b>\n")
                + "</blockquote>\n"
                + ("<blockquote>▎🟢 مفتوح للتداول والإشارات · 🔵 موثق للتحليل · ⚪ مغلق بانتظار الإدارة</blockquote>"
                   if is_admin else
                   "<blockquote>▎🟢 مفتوح لك الآن · 🔵 موثق داخل باقتك · 🔒 مقفول — يفتح مع باقة أعلى</blockquote>")
                + f"{ICON['globe']} <b>{'كل أسواق العالم — بأقسامها الملكية:' if is_admin else 'كل أسواق العالم — مفتوح لك 🔎 والمقفول 🔒 ينتظر ترقيتك:'}</b>"
                + mkt_block)

        mkt_rows: list[list] = []
        for cat in ordered_sections:
            mkt_rows.extend(_section_rows(sections[cat]))
        await render(q, body, kb.user_nav(mkt_rows), bot)

    elif action == "upgrade":
        plans = await afn.run(_plans_for_sale)
        body = (f"{ICON['diamond']} <b><u>بطاقة الترقية الملكية</u></b>\n"
                "<blockquote>"
                "▎🔒 هذا المحتوى يمنحك باقات أعلى: أسواق إضافية، أطر زمنية أكثر،\n"
                "▎استراتيجيات مؤسسية موسعة، وحدود إشارات وتحليلات يومية أعلى.\n"
                "▎الترقية فورية بعد تأكيد الدفع من الإدارة.\n"
                "</blockquote>")
        rows_kb = [[kb.btn(f"💎 {esc(p['title'])} · {p['price']:g} {p['currency']} / {'دائم' if not p['duration_days'] else str(p['duration_days']) + ' يوم'}",
                           f"u:planview:{p['code']}")] for p in plans]
        rows_kb.append([kb.btn("↩️ التحليلات", "u:analytics")])
        await render(q, body, kb.user_nav(rows_kb), bot)

    elif action.startswith("anlsym:"):
        from fysb.services import analytics as anl
        symbol = action.split(":", 1)[1]
        plan = await afn.run(subs.plan_for, uid)
        is_admin = uid == config.ADMIN_ID
        p_markets = [x.strip().upper() for x in (plan["markets"] or "").split(",") if x.strip()]
        if not is_admin and symbol.upper() not in p_markets:
            await _upsell(q, uid, bot, "هذا السوق غير مشمول في باقتك")
            return
        # v6.8.1 NOTE: the picker NEVER dead-ends (regression-pinned live bug) —
        # even a ⚪ closed market keeps flowing; the analysis stage itself is
        # honest when provider data is unavailable.
        draft = {"symbol": symbol}
        context.user_data["anl_draft"] = draft
        p_tfs = [x.strip().upper() for x in (plan["timeframes"] or "").split(",") if x.strip()]
        enabled = await afn.run(_enabled_tf_codes)
        # PICKER CAN NEVER BE EMPTY (user bug report 2026-11-22): plan codes are
        # the access authority — if the legacy catalog filter empties them, we
        # fall back to the plan's own timeframes. Boot self-heal fixes the root.
        # v6.8.0: the ADMIN analyzes ANY market × ANY timeframe × ANY strategy —
        # the plan gates apply to customers only.
        usable = [t for t in p_tfs if t in enabled] or p_tfs
        if is_admin:
            usable = sorted(enabled)
        if not usable:
            await _upsell(q, uid, bot, "باقتك الحالية لا تتضمن أطراً زمنية — رقّ باقتك للمتابعة")
            return
        body = (f"🌐 <b>السوق:</b> <b>{esc(symbol)}</b>\n"
                f"⏱ <b>اختر الإطار الزمني</b> — "
                + ("كل الأطر متاحة لتحليل الإدارة:" if is_admin else f"الأطر المسموحة لباقتك ({esc(plan['title'])}):"))
        rows = [[kb.btn(f"⏱ {tf_name(t)}", f"u:anltf:{t}") for t in usable[i:i + 4]] for i in range(0, len(usable), 4)]
        if len(usable) > 1:
            rows.insert(0, [kb.btn("👑 تحليل متعدد الأطباق (الكل)", "u:anltf:ALL")])
        else:
            body += "\n▎إطار واحد متاح حالياً — اختره للمتابعة."
        await render(q, body, kb.user_nav(rows), bot)

    elif action.startswith("anltf:"):
        from fysb.services import analytics as anl
        draft = context.user_data.get("anl_draft") or {}
        if not draft.get("symbol"):
            await render(q, "ابدأ من قسم التحليلات أولاً.", kb.user_nav([[kb.btn("🔬 التحليلات", "u:analytics")]]), bot)
            return
        tf = action.split(":", 1)[1]
        plan = await afn.run(subs.plan_for, uid)
        is_admin = uid == config.ADMIN_ID
        p_tfs = [x.strip().upper() for x in (plan["timeframes"] or "").split(",") if x.strip()]
        if not is_admin and tf.upper() != "ALL" and tf.upper() not in p_tfs:
            await _upsell(q, uid, bot, "هذا الإطار الزمني غير مشمول في باقتك")
            return
        draft["tf"] = tf.upper()
        context.user_data["anl_draft"] = draft
        p_strats = [x.strip().upper() for x in (plan["strategies"] or "").split(",") if x.strip()]
        enabled = await afn.run(_enabled_strategy_codes)
        # PICKER CAN NEVER BE EMPTY: fall back to the plan's own methodologies
        # (implemented ones first) — a legacy-dead catalog can never blank this page.
        usable = [s for s in p_strats if s in enabled and s != "ALL"]
        if not usable:
            from fysb.db import IMPLEMENTED_STRATEGIES
            usable = [s for s in p_strats if s != "ALL" and s in IMPLEMENTED_STRATEGIES] \
                or [s for s in p_strats if s != "ALL"]
        # v6.8.1 — owner rule (same as markets): the FULL implemented universe
        # is visible to everyone in guaranteed strength order; methodologies
        # outside the plan wear 🔒 and tapping one opens the upsell card.
        from fysb.services import strategy_stats as _ss
        universe = await afn.run(_ss.ordered_codes, sorted(enabled))
        if is_admin:
            usable = universe
        elif not universe:
            pass  # dead catalog — the plan fallback above keeps the page alive
        else:
            usable = universe
        locked = set() if is_admin else {s for s in usable if s not in p_strats}
        if not usable:
            await _upsell(q, uid, bot, "باقتك الحالية لا تتضمن منهجيات — رقّ باقتك للمتابعة")
            return
        tf_txt = "تحليل متعدد الأطباق" if draft["tf"] == "ALL" else tf
        body = (f"⏱ <b>الإطار:</b> <b>{esc(tf_txt)}</b>\n"
                f"🧠 <b>اختر المنهجية</b> — "
                + ("كل الاستراتيجيات المنفذة متاحة لتحليل الإدارة:" if is_admin
                   else "كل المنهجيات ظاهرة — 🔒 تفتح مع باقة أعلى:"))
        rows = [[kb.btn(("🔒 " if s in locked else "") + strategy_name(s), f"u:anlstrat:{s}")
                 for s in usable[i:i + 2]]
                for i in range(0, len(usable), 2)]
        if len(usable) > 1:
            rows.insert(0, [kb.btn("👑 جميع المنهجيات (الأفضل تُختار لك)", "u:anlstrat:ALL")])
        await render(q, body, kb.user_nav(rows), bot)

    elif action.startswith("anlstrat:"):
        from fysb.services import analytics as anl
        draft = context.user_data.get("anl_draft") or {}
        if not draft.get("symbol") or not draft.get("tf"):
            await render(q, "أكمل الخطوات السابقة أولاً — ابدأ من قسم التحليلات.", kb.user_nav(), bot)
            return
        strategy = action.split(":", 1)[1]
        plan = await afn.run(subs.plan_for, uid)
        is_admin = uid == config.ADMIN_ID
        p_strats = [x.strip().upper() for x in (plan["strategies"] or "").split(",") if x.strip()]
        if not is_admin and strategy.upper() != "ALL" and strategy.upper() not in p_strats:
            await _upsell(q, uid, bot, "هذه المنهجية غير مشمولة في باقتك")
            return
        draft["strategy"] = strategy.upper()
        state = await afn.run(anl.quota_state, uid)
        tf_txt = "تحليل متعدد الأطباق (الأدنى ← الأعلى)" if draft["tf"] == "ALL" else tf_name(draft["tf"])
        quota_line = ("👑 تحليل إدارة ملكي — بلا حدود" if is_admin
                      else f"▎حصتك قبل التوليد: <b>{state['used']}/{state['quota']}</b>")
        await render(q, f"🔬 <b>جارٍ توليد التحليل المؤسسي المركب</b>\n▎السوق: <b>{esc(draft['symbol'])}</b> · الإطار: <b>{esc(tf_txt)}</b>\n{quota_line}", None)
        res = await asyncio.to_thread(anl.run_analysis, uid, draft["symbol"], draft["tf"],
                                      "", "", draft["strategy"],
                                      enforce_plan=not is_admin)
        context.user_data.pop("anl_draft", None)
        if not res.get("ok"):
            await render(q, f"⛔ {esc(res.get('error', 'تعذر التوليد'))}",
                         kb.user_nav([[kb.btn("↩️ التحليلات", "u:analytics")]]), bot)
            return
        an = res["analysis"]
        q_state = res.get("quota") or (await afn.run(anl.quota_state, uid))
        row = await afn.run(_market_row, an["symbol"])
        card_text = render_analysis_card(an, market_row=row)
        card_text += f"\n\n📊 <b>حصتك الآن:</b> <b>{q_state['used']}/{q_state['quota']}</b> {bar(q_state['used'], q_state['quota'])}"
        await render(q, card_text,
                     kb.user_nav([[kb.btn("🔬 تحليل جديد", "u:analytics"),
                                   kb.btn("📡 الإشارات", "u:signals")]]), bot)

    # ---------- Signals (quota enforced server-side) ----------
    elif action == "signals":
        state = await afn.run(subs.quota_state, uid)
        plan = await afn.run(subs.plan_for, uid)
        markets = [instruments.normalize(x) for x in (plan["markets"] or "XAUUSD").split(",") if x.strip()]
        timeframes = [x.strip() for x in (plan["timeframes"] or "M15").split(",") if x.strip()]
        strategies = [x.strip() for x in (plan["strategies"] or "ICT").split(",") if x.strip()]
        min_score = int(plan["min_score"] or 60)
        marks_m = ",".join("?" for _ in markets)
        marks_t = ",".join("?" for _ in timeframes)
        marks_s = ",".join("?" for _ in strategies)
        args = markets + timeframes + strategies + [min_score]
        rows = await afn.run(_live_signals_page, markets, timeframes, strategies, min_score)
        # Which of these are still viewable under the remaining quota?
        delivered_ids = await afn.run(_delivered_set, uid, [r["id"] for r in rows])
        fresh = [r for r in rows if r["id"] in delivered_ids or state["remaining"] > 0]
        lines = [f"💎 <b>الباقة:</b> {esc(plan['title'])}",
                 f"📊 الحد اليومي: <b>{state['used']}/{state['quota']}</b> (المتبقي: <b>{state['remaining']}</b>)",
                 "اختر إشارة لعرض بطاقة الدخول الكاملة:"]
        rows_kb = [[kb.btn(f"{'🟢' if r['direction'] == 'BUY' else '🔴'} #{r['seq']} {esc(r['symbol'])} {r['direction']} · {r['score']}/100",
                           f"u:signalview:{r['id']}")] for r in fresh[:8]]
        if not rows_kb:
            rows_kb = [[kb.btn("🔄 تحديث الإشارات", "u:signals")]]
        # v6.10.0 (owner report 7) — «إشارة الآن» right inside the user's
        # signals hub + the watcher status line (honest auto-search).
        from fysb.handlers import signal_now as _sn
        w = await afn.run(_sn.active_watcher, uid)
        if w:
            lines.append(f"⏰ بحث تلقائي نشط: <b>{esc(w['symbol'])} · {esc(w['strategy'])}</b> كل {w['interval_minutes']} دقيقة")
            rows_kb.append([kb.btn("🔴 إلغاء البحث التلقائي", f"u:watchoff:{w['id']}")])
        rows_kb.append([kb.btn("🚀 إشارة الآن — توليد بمراحل حقيقية", "u:signow")])
        await render(q, "\n".join(lines), kb.user_nav(rows_kb), bot)

    elif action.startswith("signalview:"):
        sid = _safe_int(action.split(":", 1)[1])
        if sid is None:
            await render(q, "معرّف غير صالح.", kb.user_nav(), bot)
            return
        sig = await afn.run(_signal_row, sid)
        if not sig or sig["status"] in ("CANCELLED",):
            await render(q, "الإشارة غير متاحة.", kb.user_nav(), bot)
            return
        # Server-side quota consumption (anti-bypass: re-check on every callback)
        consumption = await afn.run(subs.consume_on_view, uid, sid)
        if not consumption["ok"] and not consumption.get("already"):
            plan = await afn.run(subs.plan_for, uid)
            await render(q,
                         f"⛔ <b>انتهى حد الإشارات اليومي</b>\n"
                         f"الباقة: {esc(plan['title'])} · المستهلك: {consumption['used']}/{consumption['quota']}\n"
                         f"الحد يتجدد يومياً على السيرفر ولا يمكن تجاوزه من الجهاز.",
                         kb.user_nav([[kb.btn("💎 ترقية الباقة", "u:plans"), kb.btn("🔄 لاحقاً", "u:signals")]]), bot)
            return
        row = await afn.run(_market_row, sig["symbol"])
        card_text = render_signal_card(dict(sig), user_view=True, row=row)
        # Live market pulse for open trades — the card is alive, not a snapshot.
        if sig["status"] in ("ACTIVE", "RUNNING", "TP1_HIT", "TP2_HIT"):
            try:
                from fysb.engines import market_data
                from fysb.engines.tracker import drift_pips
                quote = await asyncio.to_thread(market_data.fetch_price, sig["symbol"])
                price = float(quote["price"])
                entry = float(sig["entry"])
                drift = drift_pips(sig["symbol"], sig["direction"], entry, price)
                side = "أعلى الدخول" if price > entry else ("عند الدخول" if price == entry else "أدنى الدخول")
                card_text += (f"\n\n<blockquote>💰 <b>سعر السوق الآن:</b> "
                              f"<code>{instruments.fmt_price(sig['symbol'], price, row)}</code>"
                              f" · {side} · <b>{drift:+.1f} pips</b> من الدخول</blockquote>")
            except Exception:
                pass
        from fysb.services import user_trade
        personal = await afn.run(user_trade.personal_block, dict(sig), uid)
        if personal:
            card_text += "\n\n<blockquote>" + personal + "</blockquote>"
        await render(q, card_text,
                     kb.user_nav([[kb.btn("🧪 متابعة تجريبية", f"u:demoopen:{sid}"),
                                   kb.btn("↩️ كل الإشارات", "u:signals")]]), bot)

    # ---------- Preferences ----------
    elif action == "preferences":
        plan = await afn.run(subs.plan_for, uid)
        mkt_pref = "الكل (حسب باقتك)" if not (u["market"] or "").strip() else esc(u["market"])
        tf_pref = "الكل (حسب باقتك)" if not (u["timeframe"] or "").strip() else tf_name(u["timeframe"])
        body = (f"🎛 <b>تخصيص الإشارات</b>\n\n"
                f"<blockquote>"
                f"▎🌐 السوق المفضل: <b>{mkt_pref}</b>\n"
                f"▎⏱ الإطار المفضل: <b>{tf_pref}</b>\n"
                f"▎🧠 الاستراتيجية المفضلة: <b>{esc(strategy_name(u['strategy']))}</b>\n"
                f"▎⭐ الجودة الدنيا: <b>{u['min_score']}/100</b>\n"
                f"▎🔔 الإشعارات: <b>{'مفعلة' if u['notifications'] else 'متوقفة'}</b>\n"
                f"</blockquote>\n"
                f"<i>الخيارات مقيدة بمزايا باقتك ({esc(plan['title'])}) — والخيار «الكل» يعني استلام كل ما تسمح به باقتك.</i>")
        await render(q, body, kb.user_nav([
            [kb.btn("🌐 السوق", "u:markets"), kb.btn("⏱ الإطار", "u:timeframes")],
            [kb.btn("🧠 الاستراتيجية", "u:strategies"), kb.btn("⭐ الجودة", "u:quality")],
            [kb.btn("🔔 الإشعارات", "u:notifications")],
        ]), bot)

    elif action == "markets":
        plan = await afn.run(subs.plan_for, uid)
        allowed = [x.strip().upper() for x in (plan["markets"] or "").split(",") if x.strip()]
        rows = await afn.run(_live_market_rows)
        available = [r["symbol"] for r in rows if r["symbol"].upper() in allowed] or allowed
        rows_kb = [[kb.btn("🌍 الكل (حسب باقتك)", "u:setmarket:ALL")]]
        rows_kb += [[kb.btn(("✅ " if s == u["market"] else "") + s, f"u:setmarket:{s}") for s in available[i:i + 3]]
                    for i in range(0, len(available), 3)]
        rows_kb.append([kb.btn("↩️ التفضيلات", "u:preferences")])
        await render(q, "🌐 <b>اختر السوق المفضل</b>\nمقيد بالأسواق المتاحة في باقتك والموثقة لدى المزود:",
                     kb.user_nav(rows_kb), bot)

    elif action.startswith("setmarket:"):
        symbol = action.split(":", 1)[1]
        plan = await afn.run(subs.plan_for, uid)
        allowed = [x.strip().upper() for x in (plan["markets"] or "").split(",") if x.strip()]
        if symbol == "ALL":
            await afn.run(users_service.set_preference, uid, "market", "", {"market"})
            await render(q, "✅ سيصلك <b>كل ما تسمح به باقتك</b> من الأسواق مباشرة.", kb.user_nav([[kb.btn("↩️ التفضيلات", "u:preferences")]]), bot)
            return
        if symbol.upper() not in allowed:
            await _upsell(q, uid, bot, "هذا السوق غير متاح في باقتك")
            return
        await afn.run(users_service.set_preference, uid, "market", symbol, {"market"})
        await render(q, f"✅ تم حفظ السوق المفضل: <b>{esc(symbol)}</b>", kb.user_nav([[kb.btn("↩️ التفضيلات", "u:preferences")]]), bot)

    elif action == "timeframes":
        plan = await afn.run(subs.plan_for, uid)
        allowed = [x.strip().upper() for x in (plan["timeframes"] or "").split(",") if x.strip()]
        enabled = await afn.run(_enabled_tf_codes)
        usable = [t for t in allowed if t in enabled] or allowed
        rows_kb = [[kb.btn("🌍 الكل (حسب باقتك)", "u:settf:ALL")]]
        rows_kb += [[kb.btn(("✅ " if t == (u["timeframe"] or "").upper() else "") + f"{t} — {tf_name(t)}", f"u:settf:{t}") for t in usable[i:i + 3]]
                    for i in range(0, len(usable), 3)]
        rows_kb.append([kb.btn("↩️ التفضيلات", "u:preferences")])
        await render(q, "⏱ <b>اختر الإطار الزمني المفضل</b>", kb.user_nav(rows_kb), bot)

    elif action.startswith("settf:"):
        tf = action.split(":", 1)[1]
        plan = await afn.run(subs.plan_for, uid)
        allowed = [x.strip().upper() for x in (plan["timeframes"] or "").split(",") if x.strip()]
        if tf == "ALL":
            await afn.run(users_service.set_preference, uid, "timeframe", "", {"timeframe"})
            await render(q, "✅ سيصلك <b>كل ما تسمح به باقتك</b> من الأطر الزمنية.", kb.user_nav([[kb.btn("↩️ التفضيلات", "u:preferences")]]), bot)
            return
        if tf.upper() not in allowed:
            await _upsell(q, uid, bot, "هذا الإطار غير متاح في باقتك")
            return
        await afn.run(users_service.set_preference, uid, "timeframe", tf, {"timeframe"})
        await render(q, f"✅ تم حفظ الإطار: <b>{esc(tf_name(tf))}</b>", kb.user_nav([[kb.btn("↩️ التفضيلات", "u:preferences")]]), bot)

    elif action == "strategies":
        plan = await afn.run(subs.plan_for, uid)
        allowed = [x.strip().upper() for x in (plan["strategies"] or "").split(",") if x.strip()]
        enabled = await afn.run(_enabled_strategy_codes)
        usable = [s for s in allowed if s in enabled and s != "ALL"] or [s for s in allowed if s != "ALL"]
        # v6.8.0 — REAL GUARANTEED ORDERING: measured strength first (closed
        # signals), then the documented baseline — never a cosmetic shuffle.
        from fysb.services import strategy_stats
        usable = await afn.run(strategy_stats.ordered_codes, usable)
        board = {d["code"]: d["rank"] for d in (await afn.run(strategy_stats.leaderboard))}
        medals = {1: "🥇", 2: "🥈", 3: "🥉"}
        body = ("🧠 <b>اختر الاستراتيجية المفضلة — مرتبة من الأقوى للأفضل دائماً</b>\n\n" + (await afn.run(strategy_stats.leaderboard_text)))
        rows_kb = [[kb.btn("👑 الكل (حسب باقتك)", "u:setstrategy:ALL")]]
        def _label(s: str) -> str:
            rank = board.get(s)
            prefix = medals.get(rank, f"{rank}.") if rank else "" 
            return ("✅ " if s == (u["strategy"] or "").upper() else "") + (prefix + " " if prefix else "") + strategy_name(s)
        rows_kb += [[kb.btn(_label(s), f"u:setstrategy:{s}") for s in usable[i:i + 2]]
                    for i in range(0, len(usable), 2)]
        rows_kb.append([kb.btn("↩️ التفضيلات", "u:preferences")])
        await render(q, body, kb.user_nav(rows_kb), bot)

    elif action.startswith("setstrategy:"):
        st = action.split(":", 1)[1]
        plan = await afn.run(subs.plan_for, uid)
        allowed = [x.strip().upper() for x in (plan["strategies"] or "").split(",") if x.strip()]
        if st == "ALL":
            await afn.run(users_service.set_preference, uid, "strategy", "", {"strategy"})
            await render(q, "✅ سيصلك <b>كل ما تسمح به باقتك</b> من المنهجيات.", kb.user_nav([[kb.btn("↩️ التفضيلات", "u:preferences")]]), bot)
            return
        if st.upper() not in allowed:
            await _upsell(q, uid, bot, "هذه الاستراتيجية غير متاحة في باقتك")
            return
        await afn.run(users_service.set_preference, uid, "strategy", st, {"strategy"})
        await render(q, f"✅ تم حفظ الاستراتيجية: <b>{esc(strategy_name(st))}</b>", kb.user_nav([[kb.btn("↩️ التفضيلات", "u:preferences")]]), bot)

    elif action == "quality":
        plan = await afn.run(subs.plan_for, uid)
        floor = int(plan["min_score"] or 60)
        options = [n for n in (60, 65, 70, 75, 80, 85, 90) if n >= floor] or [floor]
        rows_kb = [[kb.btn(f"{n} / 100", f"u:setquality:{n}")] for n in options]
        rows_kb.append([kb.btn("↩️ التفضيلات", "u:preferences")])
        await render(q, f"⭐ <b>الحد الأدنى لجودة الإشارة</b>\n(الأدنى المسموح بباقتك: {floor})", kb.user_nav(rows_kb), bot)

    elif action.startswith("setquality:"):
        n = _safe_int(action.split(":", 1)[1])
        plan = await afn.run(subs.plan_for, uid)
        floor = int(plan["min_score"] or 60)
        if n is None or n < floor or n > 100:
            await render(q, "⛔ قيمة أقل من حد باقتك.", kb.user_nav(), bot)
            return
        await afn.run(users_service.set_preference, uid, "min_score", n, {"min_score"})
        await render(q, f"✅ الجودة الدنيا الآن: <b>{n}</b>", kb.user_nav([[kb.btn("↩️ التفضيلات", "u:preferences")]]), bot)

    elif action == "notifications":
        new = 0 if u["notifications"] else 1
        await afn.run(users_service.set_notifications, uid, bool(new))
        await render(q, f"🔔 الإشعارات الآن <b>{'مفعلة' if new else 'متوقفة'}</b>.",
                     kb.user_nav([[kb.btn("↩️ التفضيلات", "u:preferences")]]), bot)

    # ---------- Royal referrals (invite → open → subscribe) ----------
    elif action == "referrals":
        from urllib.parse import quote as _urlquote
        from fysb.services import referrals as ref
        st = await afn.run(ref.user_stats, uid)
        # v6.8.1 — LUXURY SHARE TEXT: the message a friend receives when the
        # user taps «شارك رابط الدعوة». No bot-handle duplication, no raw
        # tech-talk — a clean royal invitation, then the clickable link.
        share_text = (
            "👑 دعوة خاصة إليك من FOREXYEMENI\n\n"
            "✨ تحليلات وإشارات تداول ملكية لحظية\n"
            "🥇 الذهب مفعّل الآن — متابعة لحظية حتى الأهداف\n"
            "🧪 تنفيذ تجريبي آلي + إدارة صفقات ملكية\n"
            "💎 باقات لكل مستوى ودعم فاخر\n\n"
            "⬇️ اضغط الرابط وابدأ الآن"
        )
        share_link = ("https://t.me/share/url?url=" + _urlquote(st["link"])
                      + "&text=" + _urlquote(share_text))
        body = [
            "🤝 <b><u>دعوة الأصدقاء — بطاقة إحالات ملكية</u></b>",
            "",
            "<blockquote>"
            "▎🔗 رابط الدعوة الخاص بك — رابط أزرق حقيقي قابل للنقر، والمحال يدخل عبره تلقائياً فيُحسب لك:\n"
            f"▎<a href=\"{esc(st['link'])}\"><b>اضغط هنا لفتح رابط الدعوة</b></a>\n"
            f"▎🧾 الرابط الكامل للنسخ اليدوي:\n"
            f"▎<code>{esc(st['link'])}</code>\n"
            "▎<i>اضغط الرابط الأزرق ليفتح مباشرة — أو استخدم زر المشاركة الفاخر بالأسفل.</i>\n"
            "</blockquote>",
            "",
            "📊 <b>تقدمك نحو الأهداف الملكية:</b>",
            "<blockquote>"
            f"▎👥 انضموا عبر رابطك: <b>{st['invited']}</b> من <b>{st['target_invited']}</b> {bar(st['invited'], st['target_invited'])}\n"
            f"▎💎 اشتركوا فعلاً بالباقات: <b>{st['converted']}</b> من <b>{st['target_converted']}</b> {bar(st['converted'], st['target_converted'])}\n"
            f"▎📈 نسبة التحويل الحقيقية: <b>{st['rate']:.1f}%</b>"
            "</blockquote>",
            "",
            "🎁 <b>مكافآت الإحالة الملكية:</b>",
            "▎🥉 أول مشترك عبر رابطك — تُفتح لك ميزة خاصة من الإدارة.",
            "▎🥈 3 مشتركين فعلاً — يوم ممتد على باقتك.",
            "▎🥇 5 مشتركين فعلاً — مكافأة الإحالة الكبرى.",
            "<i>العدّ آلي وموثوق 100% (لا يحتسب إلا الاشتراك الفعلي)، وتُمنح المكافآت يدوياً من الإدارة عند تحقيق كل مستوى.</i>",
        ]
        rows_kb = [
            [InlineKeyboardButton("🟢 📤 شارك رابط الدعوة (نص فاخر جاهز)", url=share_link)],
            [InlineKeyboardButton("🔗 فتح رابط الدعوة", url=st["link"])],
            [kb.btn("🟡 💎 الباقات", "u:plans")],
        ]
        await render(q, "\n".join(body), kb.user_nav(rows_kb), bot)

    # ---------- Plans & payments ----------
    elif action == "plans":
        rows = await afn.run(_all_plans)
        current = await afn.run(subs.plan_for, uid)
        rows_kb = [[kb.btn(f"💎 {esc(r['title'])} · {r['price']:g} {r['currency']} · {'مجاني' if not r['duration_days'] else str(r['duration_days']) + ' يوم'}",
                           f"u:planview:{r['code']}")] for r in rows]
        rows_kb.append([kb.btn("🧾 مدفوعاتي", "u:payments")])
        await render(q, f"💎 <b>الاشتراكات المميزة</b>\nباقتك الحالية: <b>{esc(current['title'])}</b>\nاختر باقة لعرض مزاياها:",
                     kb.user_nav(rows_kb), bot)

    elif action.startswith("planview:"):
        code = action.split(":", 1)[1]
        p = await afn.run(_plan_row, code)
        if not p:
            await render(q, "هذه الباقة غير متاحة حالياً.", kb.user_nav([[kb.btn("↩️ الباقات", "u:plans")]]), bot)
            return
        methods = await afn.run(payments_service.list_methods, enabled_only=True)
        chains = await afn.run(payments_service.configured_chains)
        rows_kb = []
        body = render_plan_card(dict(p))
        # v6.10.0 (owner report 8) — the plan's strategies: ONE LINE EACH with
        # a button per methodology opening the REAL identity page in-chat.
        plan_strats = [x.strip().upper() for x in (p["strategies"] or "").split(",") if x.strip() and x.upper() != "ALL"]
        if plan_strats:
            from fysb.services import strategy_info as _si
            body += "\n\n🧠 <b><u>الاستراتيجيات داخل هذه الباقة — اضغط أي واحدة لقراءة عملها الحقيقي:</u></b>"
            for s in plan_strats:
                mark = "🟢" if _si.implemented(s) else "⚪"
                body += f"\n▎{mark} <b>{esc(s)}</b> — {esc(_si.SHORT_LABEL.get(s, s))}"
            for i in range(0, len(plan_strats), 2):
                rows_kb.append([kb.btn(f"🧠 {s}", f"u:stratinfo:{s}:{code}") for s in plan_strats[i:i + 2]])
        if code != "FREE":
            # v6.9.1 ROYAL FAST LANE — the DOCUMENTED USDT wallets (the ✅ ones
            # the owner configured in «محافظ USDT») now appear DIRECTLY in the
            # user's plan page, above any manual method: one tap on the verified
            # network → the royal chain page → TXID → on-chain instant opening.
            # No longer hidden behind a manually-added “USDT” payment method.
            if chains:
                body += (
                    "\n\n⛓ <b><u>التفعيل الفوري بالبلوكيشن — المحافظ الموثقة</u></b>\n"
                    "<blockquote>"
                    "▎ادفع USDT على شبكتك الموثقة ← أرسل TXID ← يُفحص البلوكيشن فعلياً\n"
                    "▎(التأكيد + عقد USDT + العنوان الملكي + المبلغ)\n"
                    "▎⚡ تُفتح باقتك فوراً 100% بلا انتظار مراجعة — والـ TXID يُحجب نهائياً بعد القبول\n"
                    f"▎🔗 الشبكات الموثقة الآن: <b>{esc(', '.join(chains))}</b>"
                    "</blockquote>"
                )
                for ch in chains:
                    rows_kb.append([kb.btn(f"⚡ اشتراك فوري — USDT {ch}", f"u:usdtpay:{code}:{ch}")])
            if methods:
                for m in methods:
                    rows_kb.append([kb.btn(f"🧾 اشتراك عبر {esc(m['title'])}", f"u:requestplan:{code}:{m['id']}")])
            else:
                rows_kb.append([kb.btn("🧾 طلب اشتراك (دفع يدوي)", f"u:requestplan:{code}:0")])
        rows_kb.append([kb.btn("↩️ كل الباقات", "u:plans")])
        await render(q, body, kb.user_nav(rows_kb), bot)

    elif action.startswith("stratinfo:"):
        # v6.10.0 (owner report 8) — the REAL identity page of one methodology,
        # opened in the same chat from a package page (or preferences).
        parts = action.split(":")
        s_code = (parts[1] if len(parts) > 1 else "").upper()
        from_plan = parts[2] if len(parts) > 2 else ""
        from fysb.services import strategy_info as _si
        body = await afn.run(_si.info_page, s_code)
        rows_kb = [[kb.btn("↩️ الباقة", f"u:planview:{from_plan}")]] if from_plan else []
        rows_kb.append([kb.btn("💎 كل الباقات", "u:plans")])
        await render(q, body, kb.user_nav(rows_kb), bot)

    elif action.startswith("usdtpay:"):
        # v6.9.1 — direct entry into the blockchain fast lane from the plan
        # page's DOCUMENTED wallets: creates the request and lands on the
        # same royal chain page as u:paychain (identical contract, zero drift).
        _, code, chain = action.split(":", 2)
        chain = chain.upper()
        p = await afn.run(_plan_row, code)
        if not p or code == "FREE":
            await render(q, "الباقة غير متاحة.", kb.user_nav([[kb.btn("↩️ كل الباقات", "u:plans")]]), bot)
            return
        if chain not in (await afn.run(payments_service.configured_chains)):
            await render(q, "⛓ هذه الشبكة غير موثقة حالياً — اختر شبكة موثقة من صفحة الباقة أو تواصل مع الدعم.",
                         kb.user_nav([[kb.btn("↩️ الباقة", f"u:planview:{code}")]]), bot)
            return
        pid = await afn.run(payments_service.create_request, uid, code, p["price"], f"USDT-{chain}")
        req = await afn.run(payments_service.get_request, pid)
        await _render_chain_payment(q, bot, req, chain)

    elif action.startswith("requestplan:"):
        try:
            _, code, mid = action.split(":", 2)
            mid = int(mid)
        except ValueError:
            await render(q, "طلب غير صالح.", kb.user_nav(), bot)
            return
        p = await afn.run(_plan_row, code)
        if not p:
            await render(q, "الباقة غير متاحة.", kb.user_nav(), bot)
            return
        method = await afn.run(payments_service.get_method, mid) if mid else None
        pay_req = await afn.run(payments_service.create_request, uid, code, p["price"], method["title"] if method else "MANUAL")
        context.user_data["awaiting_receipt"] = pay_req
        body = [f"🧾 <b>طلب اشتراك #{pay_req}</b>",
                f"💎 الباقة: <b>{esc(p['title'])}</b>",
                f"💰 المبلغ: <b>{p['price']:g} {esc(p['currency'])}</b>"]
        is_usdt = bool(method and "USDT" in (method["title"] or "").upper())
        chains = (await afn.run(payments_service.configured_chains)) if is_usdt else []
        if method:
            body.append("\n💳 <b>وسيلة الدفع:</b> " + esc(method["title"]))
            if method["country"]:
                body.append("🌍 الدولة: " + esc(method["country"]))
            if method["account_ref"]:
                body.append("🔢 بيانات الحساب: <code>" + esc(method["account_ref"]) + "</code>")
            if method["instructions"]:
                body.append("📝 التعليمات: " + esc(method["instructions"]))
        if is_usdt and chains:
            # v6.8.1 — BLOCKCHAIN FAST LANE, EVERY NETWORK: the user picks the
            # USDT network he actually pays on (TRC20 / BEP20 / ERC20 / Polygon) —
            # the chosen network is verified ON-CHAIN and the plan opens
            # immediately; the verified TXID is then blocked from any second use.
            body += [
                "\n⛓ <b><u>التفعيل الفوري بالتحقق البلوكيشن — اختر شبكتك</u></b>",
                f"▎💵 ارسل <b>{p['price']:g} USDT</b> إلى عنوان الإدارة على الشبكة التي تناسبك،",
                "▎🔗 ثم اضغط الشبكة بالأسفل وأرسل <b>TXID</b> المعاملة من محفظتك —",
                "▎⚡ يُتحقق من المعاملة على البلوكيشن مباشرة وتُفتح الباقة فوراً 100%،",
                "▎🔒 ويُحجب الـ TXID نهائياً بعد قبوله فلا يصلح لتفعيل آخر.",
            ]
            rows_kb = [[kb.btn(f"⛓ الدفع عبر {ch}", f"u:paychain:{pay_req}:{ch}")] for ch in chains]
            rows_kb.append([kb.btn("🧾 مدفوعاتي", "u:payments")])
        elif is_usdt:
            body += ["\n⛓ تفعيل البلوكيشن الفوري يتطلب ضبط محافظ الشبكات من الإدارة — أرسل صورة الإيصال الآن للمراجعة اليدوية."]
            rows_kb = [[kb.btn("🧾 مدفوعاتي", "u:payments")]]
        else:
            body.append("\n📸 <b>أرسل صورة الإيصال هنا</b> — يبقى الاشتراك معلقاً حتى مراجعة الإدارة.")
            rows_kb = [[kb.btn("🧾 مدفوعاتي", "u:payments")]]
        await render(q, "\n".join(body), kb.user_nav(rows_kb), bot)

    elif action.startswith("paychain:"):
        # v6.8.1 — network-specific payment instructions (one page per chain)
        _, pid_s, chain = action.split(":", 2)
        pid = _safe_int(pid_s)
        chain = chain.upper()
        req = (await afn.run(payments_service.get_request, pid)) if pid else None
        if not req or req["user_id"] != uid or req["status"] != "PENDING":
            await render(q, "الطلب غير متاح (مُراجع مسبقاً أو غير موجود).", kb.user_nav(), bot)
            return
        await _render_chain_payment(q, bot, req, chain)

    elif action.startswith("usdttxt:"):
        # v6.8.0 — awaiting the user's on-chain TXID for instant verification
        # v6.8.1 — the chain rides in the callback (u:usdttxt:{pid}:{CHAIN})
        parts = action.split(":", 2)
        pid = _safe_int(parts[1])
        chain = (parts[2].upper() if len(parts) > 2 else "TRC20")
        req = (await afn.run(payments_service.get_request, pid)) if pid else None
        if not req or req["user_id"] != uid or req["status"] != "PENDING":
            await render(q, "الطلب غير متاح للتحقق (مُراجع مسبقاً أو غير موجود).", kb.user_nav(), bot)
            return
        if not (await afn.run(payments_service.chain_wallet, chain)):
            await render(q, "⛓ هذه الشبكة غير مضبوطة حالياً — اختر شبكة أخرى.", kb.user_nav(), bot)
            return
        context.user_data["awaiting_usdt_txid"] = pid
        context.user_data["usdt_chain"] = chain
        await render(q, f"⛓ <b>أرسل الآن TXID المعاملة</b> (64 خانة سداسية عشرية)\n"
                        f"انسخها من محفظتك بعد إرسال <b>USDT على شبكة {chain}</b> — سيُفحص البلوكيشن فوراً:\n"
                        "التأكيد + العنوان الملكي + المبلغ، ثم تُفتح باقتك مباشرة.",
                     kb.user_nav([[kb.btn("↩️ الباقات", "u:plans")]]), bot)

    elif action == "payments":
        rows = await afn.run(_my_payments, uid)
        rows_kb = [[kb.btn(f"🧾 #{r['id']} · {esc(r['plan_code'])} · {r['amount']:g} · {esc(r['status'])}",
                           f"u:paymentview:{r['id']}")] for r in rows]
        if not rows_kb:
            rows_kb = [[kb.btn("💎 استعراض الباقات", "u:plans")]]
        await render(q, "🧾 <b>محفظة المدفوعات</b>", kb.user_nav(rows_kb), bot)

    elif action.startswith("paymentview:"):
        pid = _safe_int(action.split(":", 1)[1])
        r = (await afn.run(payments_service.get_request, pid)) if pid else None
        if not r or r["user_id"] != uid:
            await render(q, "طلب الدفع غير موجود.", kb.user_nav(), bot)
            return
        plan_title = await afn.run(_plan_title_of, r["plan_code"])
        from fysb.ui.cards import render_payment_card
        await render(q, render_payment_card(r, plan_title["title"] if plan_title else ""),
                     kb.user_nav([[kb.btn("↩️ مدفوعاتي", "u:payments"), kb.btn("💎 الباقات", "u:plans")]]), bot)

    # ---------- Demo trading ----------
    elif action == "demo":
        from fysb.engines import demo as demo_engine
        api_key = config.market_api_key()
        overview = await afn.run(demo_engine.account_overview, uid, api_key)
        body = [
            "🧪 <b>التداول التجريبي — محاكاة على بيانات حقيقية</b>",
            "",
            f"💰 الرصيد: <b>{overview['balance']:,.2f} {overview['currency']}</b>",
            f"📊 Equity: <b>{overview['equity']:,.2f}</b> (عائم: {overview['floating']:+,.2f})",
            f"📉 Drawdown: <b>{overview['drawdown_pct']:.2f}%</b>",
            f"📂 صفقات مفتوحة: <b>{len(overview['open'])}</b>",
        ]
        if overview["win_rate"] is not None:
            body.append(f"🎯 نسبة الرابحة (آخر 10): <b>{overview['win_rate']:.0f}%</b>")
        if overview["data_stale"]:
            body.append("\n⚠️ تعذر جلب أسعار بعض الصفقات الآن — القيم تعرض آخر بيانات متاحة.")
        body.append("\n<i>محاكاة كاملة مع سبريد وانزلاق؛ رصيد افتراضي منفصل تماماً عن أي تداول حقيقي.</i>")
        s = overview["settings"]
        lot_word = "تلقائي حسب المخاطرة" if s["lot_mode"] == "auto" else f"ثابت {s['fixed_lot']:g}"
        body.append(f"\n⚙️ <b>إعداداتك:</b> مخاطرة <b>{s['risk_pct']:g}%</b> · اللوت: "
                    f"<b>{lot_word}</b> · "
                    f"التنفيذ التلقائي: <b>{'مفعل 🤖' if s['auto_trade'] else 'متوقف'}</b>")
        await render(q, "\n".join(body), kb.user_nav([
            [kb.btn("🚀 فتح صفقة تجريبية", "u:demopick"), kb.btn("📂 الصفقات المفتوحة", "u:demoopen_list")],
            [kb.btn("📜 سجل الصفقات", "u:demohistory"), kb.btn("🔄 تصفير الحساب", "u:demoreset")],
            [kb.btn("⚙️ إعدادات حسابي التجريبي", "u:demoset")],
        ]), bot)

    elif action == "demoset":
        from fysb.engines import demo as demo_engine
        s = await afn.run(demo_engine.demo_settings, uid)
        lot_word = "تلقائي حسب المخاطرة ورأس المال" if s["lot_mode"] == "auto" else f"ثابت {s['fixed_lot']:g}"
        body = (
            "⚙️ <b><u>إعدادات حسابي التجريبي</u></b>\n"
            "<blockquote>"
            f"▎💰 الرصيد الحالي: <b>{s['balance']:,.2f} USD</b>\n"
            f"▎⚖️ المخاطرة لكل صفقة: <b>{s['risk_pct']:g}%</b>\n"
            f"▎📦 حجم اللوت: <b>{lot_word}</b>\n"
            f"▎🤖 التنفيذ التلقائي عند وصول إشارة: <b>{'مفعل — تُفتح الصفقة فوراً بحجمك ومخاطرتك' if s['auto_trade'] else 'متوقف'}</b>\n"
            f"</blockquote>\n"
            "<i>عند تفعيل التنفيذ التلقائي: تُحسب الصفقة من رصيدك ومخاطرتك، يصلك تنبيه عند نصف الهدف الأول "
            "ويُغلق 50% من الصفقة تلقائياً لتأمين الربح ويُنقل الوقف لنقطة الدخول، وتُحسب النتائج داخل تيليجرام.</i>")
        await render(q, body, kb.user_nav([
            [kb.btn("💰 تعديل الرصيد", "u:demobal"), kb.btn("⚖️ المخاطرة", "u:demorisk")],
            [kb.btn("📦 اللوت", "u:demolot"), kb.btn(f"🤖 التنفيذ التلقائي: {'إيقاف' if s['auto_trade'] else 'تفعيل'}", "u:demoauto")],
            [kb.btn("↩️ التجريبي", "u:demo")],
        ]), bot)

    elif action == "demobal":
        context.user_data["awaiting_demo_bal_user"] = True
        await render(q, "💰 <b>أرسل رصيدك التجريبي الجديد</b> رقماً فقط (مثال: 5000).\n"
                        "<i>سيُعاد ضبط الحساب بهذا الرصيد وتُغلق الصفقات المفتوحة.</i>",
                     kb.user_nav([[kb.btn("↩️ الإعدادات", "u:demoset")]]), bot)

    elif action == "demorisk":
        await render(q, "⚖️ <b>اختر نسبة المخاطرة لكل صفقة</b> (من رأس المال):",
                     kb.user_nav([[kb.btn(f"⚖️ {v:g}%", f"u:demosetrisk:{v}") for v in (0.5, 1.0, 2.0)],
                                  [kb.btn(f"⚖️ {v:g}%", f"u:demosetrisk:{v}") for v in (3.0, 5.0)],
                                  [kb.btn("↩️ الإعدادات", "u:demoset")]]), bot)

    elif action.startswith("demosetrisk:"):
        from fysb.engines import demo as demo_engine
        v = float(action.split(":", 1)[1])
        await afn.run(demo_engine.set_risk_pct, uid, v)
        await render(q, f"✅ أصبحت مخاطرتك لكل صفقة: <b>{v:g}%</b> من رأس مالك — سيُحسب حجم اللوت تلقائياً "
                        f"من رصيدك وهذه النسبة ومسافة الوقف (بدون إدخال لوت).\n"
                        f"🤖 <b>التنفيذ التلقائي مُسلّح:</b> كل إشارة مؤهلة تُفتح في حسابك فوراً بهذا الحساب — "
                        f"يمكنك تعطيله من زر التنفيذ التلقائي.",
                     kb.user_nav([[kb.btn("↩️ الإعدادات", "u:demoset")]]), bot)

    elif action == "demolot":
        await render(q, "📦 <b>حجم اللوت</b>\nالوضع التلقائي يحسب اللوت من رصيدك ونسبة مخاطرتك ومسافة الوقف — الأكثر أماناً واحترافية:",
                     kb.user_nav([[kb.btn("🤖 تلقائي حسب المخاطرة", "u:demosetlot:auto:0")],
                                  [kb.btn("📦 ثابت 0.01", "u:demosetlot:fixed:0.01"), kb.btn("📦 ثابت 0.05", "u:demosetlot:fixed:0.05")],
                                  [kb.btn("📦 ثابت 0.10", "u:demosetlot:fixed:0.10"), kb.btn("📦 ثابت 0.50", "u:demosetlot:fixed:0.50")],
                                  [kb.btn("↩️ الإعدادات", "u:demoset")]]), bot)

    elif action.startswith("demosetlot:"):
        from fysb.engines import demo as demo_engine
        _pfx, mode, lot = action.split(":", 2)
        await afn.run(demo_engine.set_lot_mode, uid, mode, float(lot) if float(lot) > 0 else None)
        word = "تلقائي حسب المخاطرة ورأس المال" if mode == "auto" else f"ثابت {float(lot):g}"
        await render(q, f"✅ حجم اللوت الآن: <b>{word}</b>", kb.user_nav([[kb.btn("↩️ الإعدادات", "u:demoset")]]), bot)

    elif action == "demoauto":
        from fysb.engines import demo as demo_engine
        s = await afn.run(demo_engine.demo_settings, uid)
        await afn.run(demo_engine.set_auto_trade, uid, not s["auto_trade"])
        state = "مفعل 🤖 — ستنفذ كل إشارة مؤهلة تلقائياً في حسابك بحجمك ومخاطرتك" if not s["auto_trade"] else "متوقف"
        await render(q, f"🤖 التنفيذ التلقائي الآن: <b>{state}</b>.",
                     kb.user_nav([[kb.btn("↩️ الإعدادات", "u:demoset")]]), bot)

    elif action == "demopick":
        rows = await afn.run(_open_signals_rows)
        rows_kb = [[kb.btn(f"#{r['seq']} {esc(r['symbol'])} {r['direction']} {r['score']}/100", f"u:demolots:{r['id']}")] for r in rows]
        rows_kb.append([kb.btn("↩️ التجريبي", "u:demo")])
        await render(q, "🚀 <b>فتح صفقة تجريبية</b>\nاختر إشارة لإدخالها محاكاةً (سعر الدخول الحقيقي + سبريد وانزلاق):",
                     kb.user_nav(rows_kb), bot)

    elif action.startswith("demolots:"):
        sid = _safe_int(action.split(":", 1)[1])
        sig = await afn.run(_signal_row, sid)
        if not sig:
            await render(q, "الإشارة غير متاحة.", kb.user_nav(), bot)
            return
        context.user_data["demo_signal"] = dict(sig)
        rows_kb = [[kb.btn(f"📦 {x}", f"u:democonfirm:{sid}:{x}")] for x in (0.01, 0.05, 0.10, 0.50, 1.00)]
        await render(q, f"📦 <b>حجم الصفقة</b>\n{esc(sig['symbol'])} {esc(sig['direction'])} — الدخول {sig['entry']}\nاختر حجم اللوت:",
                     kb.user_nav(rows_kb), bot)

    elif action.startswith("democonfirm:"):
        try:
            _, sid_s, lots_s = action.split(":", 2)
            sid, lots = int(sid_s), float(lots_s)
        except ValueError:
            await render(q, "طلب غير صالح.", kb.user_nav(), bot)
            return
        sig_row = context.user_data.get("demo_signal") or {}
        if sig_row.get("id") != sid:
            sig = await afn.run(_signal_row, sid)
            sig_row = dict(sig) if sig else {}
        if not sig_row:
            await render(q, "الإشارة غير متاحة.", kb.user_nav(), bot)
            return
        from fysb.engines import demo as demo_engine
        res = await afn.run(demo_engine.open_position, uid, sig_row["symbol"], sig_row["direction"], lots,
                            sl=sig_row.get("sl") or 0, tp=sig_row.get("tp1") or 0,
                            api_key=config.market_api_key())
        if not res.get("ok"):
            await render(q, f"⛔ تعذر فتح الصفقة التجريبية:\n{esc(res.get('error', ''))}", kb.user_nav(), bot)
            return
        sym = sig_row["symbol"]
        await render(q,
                     f"✅ <b>تم فتح صفقة تجريبية #{res['position_id']}</b>\n\n"
                     f"🌐 {esc(sym)} · {esc(sig_row['direction'])} · 📦 {lots:g} lots\n"
                     f"🎯 سعر الدخول (مع السبريد): <code>{instruments.fmt_price(sym, res['entry'], row=(await afn.run(_market_row, sym)))}</code>\n"
                     f"🕐 وقت السعر: {human_dt(res['price_time'])}\n\n"
                     f"<i>يُغلق تلقائياً عند SL/TP بيانات حقيقية، أو الإغلاق اليدوي من الصفقات المفتوحة.</i>",
                     kb.user_nav([[kb.btn("📂 الصفقات المفتوحة", "u:demoopen_list")]]), bot)

    elif action == "demoopen_list":
        from fysb.engines import demo as demo_engine
        api_key = config.market_api_key()
        await afn.run(demo_engine.auto_close_on_targets, uid, api_key)
        overview = await afn.run(demo_engine.account_overview, uid, api_key)
        if not overview["open"]:
            await render(q, "📂 لا توجد صفقات مفتوحة.", kb.user_nav([[kb.btn("🚀 فتح صفقة", "u:demopick")]]), bot)
            return
        rows_kb = []
        for p in overview["open"]:
            cur = p.get("current")
            cur_txt = instruments.fmt_price(p["symbol"], cur, (await afn.run(_market_row, p["symbol"]))) if cur else "—"
            rows_kb.append([kb.btn(f"#{p['id']} {esc(p['symbol'])} {p['direction']} {p['lots']:g} · الآن {cur_txt}",
                                   f"u:democlose:{p['id']}")])
        await render(q, "📂 <b>الصفقات المفتوحة</b>\nاضغط على صفقة لإغلاقها بسعر السوق الحالي:",
                     kb.user_nav(rows_kb), bot)

    elif action.startswith("democlose:"):
        pid = _safe_int(action.split(":", 1)[1])
        from fysb.engines import demo as demo_engine
        res = await afn.run(demo_engine.close_position, uid, pid, config.market_api_key())
        if not res.get("ok"):
            await render(q, f"⛔ {esc(res.get('error', 'تعذر الإغلاق'))}", kb.user_nav(), bot)
            return
        sym = ""
        emoji = "📈" if res["pnl"] >= 0 else "📉"
        await render(q,
                     f"{emoji} <b>أُغلقت الصفقة #{pid}</b>\n\n"
                     f"💰 النتيجة: <b>{res['pnl']:+,.2f} USD</b>\n"
                     f"🏁 سعر الإغلاق: <code>{res['exit']}</code>\n"
                     f"🕐 {human_dt(config.now())}",
                     kb.user_nav([[kb.btn("🧪 التجريبي", "u:demo")]]), bot)

    elif action == "demohistory":
        from fysb.engines import demo as demo_engine
        overview = await afn.run(demo_engine.account_overview, uid)
        lines = ["📜 <b>سجل الصفقات التجريبية</b>"]
        if not overview["closed"]:
            lines.append("لا توجد صفقات مغلقة بعد.")
        for p in overview["closed"]:
            emoji = "📈" if (p["pnl"] or 0) >= 0 else "📉"
            lines.append(f"{emoji} #{p['id']} {esc(p['symbol'])} {p['direction']} · {p['pnl']:+.2f} · {esc(p['reason'])} · {human_dt(p['closed_at'])}")
        await render(q, "\n".join(lines), kb.user_nav(), bot)

    elif action == "demoreset":
        from fysb.engines import demo as demo_engine
        await afn.run(demo_engine.reset_account, uid)
        await render(q, "🔄 <b>تم تصفير الحساب التجريبي</b> وإعادة الرصيد الابتدائي. الصفقات المفتوحة أُغلقت إدارياً.",
                     kb.user_nav([[kb.btn("🧪 التجريبي", "u:demo")]]), bot)

    # ---------- Stats / support / help ----------
    elif action == "my_stats":
        profile = await afn.run(users_service.profile_summary, uid)
        wins, sls = await afn.run(_my_stats_counts, uid)
        body = (f"📈 <b>إحصائياتي</b>\n\n"
                f"📡 إشارات استلمتها: <b>{profile['consumed_total']}</b>\n"
                f"🟢 مكتملة TP3: <b>{wins}</b>\n🔴 ضربت SL: <b>{sls}</b>\n"
                f"🧪 صفقات تجريبية: انظر قسم التداول التجريبي\n")
        await render(q, body, kb.user_nav([[kb.btn("🧪 التداول التجريبي", "u:demo"), kb.btn("📡 الإشارات", "u:signals")]]), bot)

    elif action == "support":
        context.user_data["awaiting_ticket"] = True
        cfg = await afn.run(support_service.config_row) or {}
        body = ["🎧 <b>الدعم الفني</b>"]
        if any(cfg.get(x) for x in ("username", "phone", "channel", "group_chat")):
            body.append("\n<b>وسائل التواصل:</b>")
            if cfg.get("username"):
                body.append(f"  ✉️ @{esc(cfg['username'])}")
            if cfg.get("phone"):
                body.append(f"  📞 {esc(cfg['phone'])}")
            if cfg.get("channel"):
                body.append(f"  📢 {esc(cfg['channel'])}")
            if cfg.get("group_chat"):
                body.append(f"  👥 {esc(cfg['group_chat'])}")
        body.append("\n<b>لفتح تذكرة:</b> أرسل رسالتك هنا كنص واحد، ويمكن إرفاق صورة بعد الرسالة.")
        mine = await afn.run(support_service.user_tickets, uid)
        rows_kb = [[kb.btn(f"🎫 تذكرة #{t['id']} · {esc(t['subject'])} · {esc(t['status'])}", f"u:ticket:{t['id']}")] for t in mine]
        await render(q, "\n".join(body), kb.user_nav(rows_kb), bot)

    elif action.startswith("ticket:"):
        tid = _safe_int(action.split(":", 1)[1])
        t = (await afn.run(support_service.get_ticket, tid)) if tid else None
        if not t or t["user_id"] != uid:
            await render(q, "التذكرة غير موجودة.", kb.user_nav(), bot)
            return
        msgs = await afn.run(support_service.ticket_messages, tid)
        lines = [f"🎫 <b>تذكرة #{tid}</b> · {esc(t['subject'])} · <b>{esc(t['status'])}</b>", ""]
        for m in msgs[-8:]:
            who = "🧑 أنت" if m["sender_role"] == "user" else "🎧 الدعم"
            lines.append(f"{who} ({human_dt(m['created_at'])}):")
            lines.append(f"{esc(m['body'][:300])}\n")
        context.user_data["awaiting_ticket_reply"] = tid
        rows_kb = [[kb.btn("🔓 إعادة فتح", f"u:ticketreopen:{tid}")]] if t["status"] == "CLOSED" else []
        rows_kb.append([kb.btn("↩️ الدعم", "u:support")])
        await render(q, "\n".join(lines), kb.user_nav(rows_kb), bot)

    elif action.startswith("ticketreopen:"):
        tid = _safe_int(action.split(":", 1)[1])
        t = (await afn.run(support_service.get_ticket, tid)) if tid else None
        if t and t["user_id"] == uid:
            await afn.run(support_service.set_status, tid, "OPEN")
            await _notify_admin_ticket(bot, tid, "أعاد المستخدم فتح التذكرة")
            await render(q, f"✅ أُعيد فتح التذكرة #{tid}.", kb.user_nav(), bot)
        else:
            await render(q, "التذكرة غير موجودة.", kb.user_nav(), bot)

    elif action.startswith("picktype:"):
        idx = _safe_int(action.split(":", 1)[1]) or 0
        ttype = TICKET_TYPES[idx][1] if 0 <= idx < len(TICKET_TYPES) else "general"
        context.user_data["ticket_type"] = ttype
        context.user_data["awaiting_ticket"] = True
        await render(q, f"📝 اكتب موضوع ورسالتك في رسالة واحدة.", kb.user_nav(), bot)

    # ---------- «إشارة الآن» — the royal staged generation (v6.10.0) ----------
    elif action == "signow":
        from fysb.handlers import signal_now as _sn
        await _sn.open_flow(q, uid, context, bot, is_admin=(uid == config.ADMIN_ID))

    elif action.startswith("signowstyle:"):
        from fysb.handlers import signal_now as _sn
        await _sn.pick_style(q, uid, context, bot, action.split(":", 1)[1])

    elif action.startswith("signowsym:"):
        from fysb.handlers import signal_now as _sn
        await _sn.pick_symbol(q, uid, context, bot, action.split(":", 1)[1])

    elif action.startswith("signowtf:"):
        from fysb.handlers import signal_now as _sn
        await _sn.pick_tf(q, uid, context, bot, action.split(":", 1)[1])

    elif action.startswith("signowstrat:"):
        from fysb.handlers import signal_now as _sn
        await _sn.pick_strategy(q, uid, context, bot, action.split(":", 1)[1])

    elif action.startswith("signowwatch:"):
        from fysb.handlers import signal_now as _sn
        minutes = _safe_int(action.split(":", 1)[1]) or 30
        await _sn.start_watch(q, uid, context, bot, minutes)

    elif action.startswith("watchoff:"):
        from fysb.handlers import signal_now as _sn
        await _sn.stop_watch(q, uid, context, bot, _safe_int(action.split(":", 1)[1]) or 0)

    elif action == "help":
        await render(q,
                     "❓ <b>مركز المساعدة</b>\n\n"
                     "• <b>الإشارات:</b> تصل بعد نشرها من الإدارة أو من محرك السوق؛ كل إشارة تستهلك من حد باقتك اليومي.\n"
                     "• <b>الحد اليومي:</b> محفوظ على السيرفر ويتجدد كل يوم UTC؛ لا يمكن تجاوزه من الجهاز.\n"
                     "• <b>التجريبي:</b> محاكاة بأسعار حقيقية مع سبريد وانزلاق؛ منفصل تماماً عن التداول الحقيقي.\n"
                     "• <b>التداول الحقيقي:</b> مغلق افتراضياً ولا ينفذ أي أمر لدى وسيط.\n"
                     "• <b>الدعم:</b> أرسل رسالة وسيتم فتح تذكرة وإشعار الإدارة.\n\n"
                     "<i>المحتوى تحليلي وليس نصيحة استثمارية.</i>",
                     kb.user_nav(), bot)

    else:
        await render(q, "هذا القسم غير متاح. استخدم القائمة الرئيسية.", kb.user_nav(), bot)


# --- helpers ------------------------------------------------------------------
def _safe_int(v):
    try:
        return int(v)
    except (TypeError, ValueError):
        return None


def _plans_for_sale():
    """(v6.11.0 thread-offload helper) purchasable plans, cheapest first."""
    with db.conn() as c:
        return c.execute("SELECT * FROM plans WHERE enabled=1 AND price>0 ORDER BY price").fetchall()


def _all_plans():
    """(v6.11.0 thread-offload helper) every enabled plan."""
    with db.conn() as c:
        return c.execute("SELECT * FROM plans WHERE enabled=1 ORDER BY price").fetchall()


def _plan_row(code: str):
    """(v6.11.0 thread-offload helper) one enabled plan row by code."""
    with db.conn() as c:
        return c.execute("SELECT * FROM plans WHERE code=? AND enabled=1", (code,)).fetchone()


def _plan_title_of(code: str):
    """(v6.11.0 thread-offload helper) plan title row for payment cards."""
    with db.conn() as c:
        return c.execute("SELECT title FROM plans WHERE code=?", (code,)).fetchone()


def _my_payments(uid: int):
    """(v6.11.0 thread-offload helper) the user's last 8 payment requests."""
    with db.conn() as c:
        return c.execute("SELECT * FROM payments WHERE user_id=? ORDER BY id DESC LIMIT 8", (uid,)).fetchall()


def _catalog_rows():
    """(v6.11.0 thread-offload helper) FULL market catalog as dicts."""
    with db.conn() as c:
        return [dict(r) for r in c.execute("SELECT * FROM market_catalog ORDER BY symbol").fetchall()]


def _live_market_rows():
    """(v6.11.0 thread-offload helper) enabled+verified market rows."""
    with db.conn() as c:
        return c.execute("SELECT * FROM market_catalog WHERE enabled=1 AND verified=1").fetchall()


def _enabled_tf_codes():
    """(v6.11.0 thread-offload helper) enabled+verified timeframe codes."""
    with db.conn() as c:
        return {r["code"] for r in c.execute("SELECT code FROM timeframe_catalog WHERE enabled=1 AND verified=1")}


def _enabled_strategy_codes():
    """(v6.11.0 thread-offload helper) enabled+verified strategy codes."""
    with db.conn() as c:
        return {r["code"] for r in c.execute("SELECT code FROM strategy_catalog WHERE enabled=1 AND verified=1")}


def _dashboard_counts(uid: int):
    """(v6.11.0 thread-offload helper) ONE pooled trip for the 5 dashboard counters."""
    with db.conn() as c:
        total = c.execute("SELECT COUNT(*) n FROM signals").fetchone()["n"]
        active = c.execute("SELECT COUNT(*) n FROM signals WHERE status IN ('ACTIVE','RUNNING','TP1_HIT','TP2_HIT')").fetchone()["n"]
        wins = c.execute("SELECT COUNT(*) n FROM signals WHERE status='TP3_HIT'").fetchone()["n"]
        delivered = c.execute("SELECT COUNT(*) n FROM signal_deliveries WHERE user_id=?", (uid,)).fetchone()["n"]
        anl_used = c.execute("SELECT COUNT(*) n FROM analyses WHERE user_id=? AND created_at>=?",
                             (uid, db.day_start_utc())).fetchone()["n"]
    return total, active, wins, delivered, anl_used


def _live_signals_page(markets, timeframes, strategies, min_score):
    """(v6.11.0 thread-offload helper) live signals with the REAL seq number
    (same ROW_NUMBER as the admin center — identical # on both sides)."""
    marks_m = ",".join("?" for _ in markets)
    marks_t = ",".join("?" for _ in timeframes)
    marks_s = ",".join("?" for _ in strategies)
    args = list(markets) + list(timeframes) + list(strategies) + [min_score]
    with db.conn() as c:
        return c.execute(f"""SELECT *, ROW_NUMBER() OVER (ORDER BY id) AS seq
                             FROM signals WHERE status IN ('ACTIVE','RUNNING','TP1_HIT','TP2_HIT')
                             AND REPLACE(symbol,'/','') IN ({marks_m}) AND timeframe IN ({marks_t})
                             AND strategy IN ({marks_s}) AND score>=?
                             ORDER BY id DESC LIMIT 12""", args).fetchall()


def _delivered_set(uid: int, sids: list) -> set:
    """(v6.11.0 thread-offload helper) ids of signals already delivered to uid.
    ONE pooled trip instead of one query per row."""
    if not sids:
        return set()
    marks = ",".join("?" for _ in sids)
    with db.conn() as c:
        rows = c.execute(f"SELECT signal_id FROM signal_deliveries WHERE user_id=? AND signal_id IN ({marks})",
                         [uid] + list(sids)).fetchall()
    return {r["signal_id"] for r in rows}


def _signal_row(sid: int):
    """(v6.11.0 thread-offload helper) one signal row by id."""
    with db.conn() as c:
        return c.execute("SELECT * FROM signals WHERE id=?", (sid,)).fetchone()


def _open_signals_rows():
    """(v6.11.0 thread-offload helper) latest open signals for the demo picker."""
    with db.conn() as c:
        return c.execute("""SELECT *, ROW_NUMBER() OVER (ORDER BY id) AS seq
                            FROM signals WHERE status IN ('ACTIVE','RUNNING','TP1_HIT')
                            ORDER BY id DESC LIMIT 6""").fetchall()


def _my_stats_counts(uid: int):
    """(v6.11.0 thread-offload helper) TP3/SL counters over delivered signals."""
    with db.conn() as c:
        wins = c.execute("""SELECT COUNT(*) n FROM signal_deliveries d JOIN signals s ON s.id=d.signal_id
                            WHERE d.user_id=? AND s.status='TP3_HIT'""", (uid,)).fetchone()["n"]
        sls = c.execute("""SELECT COUNT(*) n FROM signal_deliveries d JOIN signals s ON s.id=d.signal_id
                           WHERE d.user_id=? AND s.status='SL_HIT'""", (uid,)).fetchone()["n"]
    return wins, sls


def _market_row(symbol: str):
    with db.conn() as c:
        r = c.execute("SELECT * FROM market_catalog WHERE symbol=?", (symbol,)).fetchone()
    return dict(r) if r else None


def _already_delivered(uid: int, sid: int) -> bool:
    with db.conn() as c:
        return c.execute("SELECT 1 FROM signal_deliveries WHERE user_id=? AND signal_id=?", (uid, sid)).fetchone() is not None


async def _notify_admin_ticket(bot, tid: int, note: str) -> None:
    if config.ADMIN_ID:
        try:
            await bot.send_message(config.ADMIN_ID, f"🎫 تذكرة #{tid}: {note}")
        except Exception:
            pass
