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
from fysb.ui.cards import (plan_tier_icon, render_analysis_card,
                           render_luxury_upgrade_card, render_plan_card,
                           render_plans_showcase_card, render_signal_card,
                           render_signal_compact, render_user_card)

TICKET_TYPES = [("عام", "general"), ("مشكلة تقنية", "technical"), ("المدفوعات", "billing"), ("اقتراح", "suggestion")]


async def _upsell(
    q,
    uid,
    bot,
    why: str,
    unlock_kind: str | None = None,
    unlock_item: str | None = None,
    trial_expired: bool = False,
    quota_exhausted: bool = False,
) -> None:
    """v6.22.1 Ultra-luxury locked-content, daily-quota & trial-expiry upgrade card."""
    plan = dict(await afn.run(subs.plan_for, uid))
    plans = [dict(r) for r in await afn.run(_plans_for_sale)]
    rec = (
        await afn.run(subs.cheapest_plan_unlocking, unlock_kind, unlock_item)
        if (unlock_kind and unlock_item)
        else await afn.run(subs.next_higher_plan, plan.get("code", "FREE"))
    )
    body = render_luxury_upgrade_card(
        why=why,
        current_plan=plan,
        paid_plans=plans,
        recommended_plan=rec,
        trial_expired=trial_expired,
        quota_exhausted=quota_exhausted,
    )
    cur_price = float(plan.get("price") or 0.0)
    target_plans = [p for p in plans if float(p.get("price") or 0) > cur_price] or plans
    rows_kb = []
    for p in target_plans:
        code = (p["code"] or "").upper()
        ic = plan_tier_icon(code)
        is_rec = bool(rec and (rec["code"] or "").upper() == code)
        tag = " ⭐ المقترحة لك" if is_rec else (" 🔥" if code == "VIP" else (" 🏆" if code == "ROYAL" else ""))
        rows_kb.append([
            kb.btn(
                f"{ic} الترقية إلى {esc(p['code'])}{tag} · {p['price']:g} {p['currency']}",
                f"u:planview:{p['code']}",
                style="success" if (is_rec or code in ("VIP", "ROYAL")) else "primary",
            )
        ])
    rows_kb.append([kb.btn("💎 مقارنة جميع الباقات", "u:plans", style="primary")])
    await render(q, body, kb.user_nav(rows_kb), bot)


async def _guard_free_trial(q, uid: int, u, bot, feature_ar: str) -> bool:
    """Return True and render the luxury trial-expired upgrade card if the user's
    FREE trial window has ended."""
    if uid == config.ADMIN_ID:
        return False
    trial = await afn.run(subs.free_trial_status, uid, u)
    if trial.get("expired"):
        await _upsell(
            q, uid, bot,
            f"انتهت فترتك المجانية ({trial.get('trial_days', 7)} يوم) — قم بالترقية للاستمرار في {feature_ar}",
            trial_expired=True,
        )
        return True
    return False


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
        [kb.copy_btn("📋 نسخ عنوان المحفظة بضغطة واحدة", wallet, style="primary")],
        [kb.btn(f"⚡ أرسل TXID — تحقق فوري على {chain}", f"u:usdttxt:{pid}:{chain}", style="success")],
        [kb.btn("↩️ وسائل الدفع الأخرى", f"u:planview:{req['plan_code']}")],
        [kb.btn("🧾 مدفوعاتي", "u:payments")],
    ]), bot)


def _parse_user_markets(raw: str) -> list[str]:
    out = []
    for x in (raw or "").split(","):
        s = x.strip().upper().replace("/", "")
        if s and s != "ALL" and s not in out:
            out.append(s)
    return out


def _format_user_markets(raw: str) -> str:
    syms = _parse_user_markets(raw)
    return "الكل (حسب باقتك)" if not syms else " · ".join(syms)


def _home_snapshot(uid: int, preloaded_u=None) -> tuple[dict | None, dict | None, dict]:
    """Single-connection snapshot for /start & u:home (v6.20.1 — 1 checkout instead of 9)."""
    day = db.day_start_utc()
    with db.conn() as c:
        u = dict(preloaded_u) if preloaded_u is not None else None
        if u is None:
            r = c.execute("SELECT * FROM users WHERE user_id=?", (uid,)).fetchone()
            u = dict(r) if r else None
        plan = subs.plan_for(uid, conn_obj=c)
        used = c.execute(
            "SELECT COUNT(*) n FROM signal_deliveries WHERE user_id=? AND delivered_at>=?",
            (uid, day),
        ).fetchone()["n"]
    quota = (max(1, int(plan["max_signals_per_day"] or 1))
             if (plan and plan["code"] != "FREE")
             else max(1, db.get_setting_int("free_daily_signals", int((plan or {}).get("max_signals_per_day") or 1))))
    state = {"plan": plan["code"] if plan else "FREE", "quota": quota,
             "used": used, "remaining": max(0, quota - used), "day": day}
    trial = subs.free_trial_status(uid, user_row=u, plan_row=plan)
    return u, plan, state, trial


async def build_user_home_card(uid: int, tg_name: str = "", preloaded_u=None) -> str:
    """v6.20.1: Luxury executive user home banner shared by /start and u:home (single DB checkout)."""
    u, plan, state, trial = await afn.run(_home_snapshot, uid, preloaded_u)
    plan_title = esc(plan["title"] if plan else "Free")
    mkts_txt = esc(_format_user_markets((u["market"] if u else "") or ""))
    auto_on = bool(u and u["notifications"])
    notif_txt = "🟢 شغّالة — تصلك الفرص التلقائية فوراً" if auto_on else "🔴 متوقفة — اضغط الزر الأخضر بالأسفل للتشغيل"
    greet = f"أهلاً بك يا <b>{esc(tg_name)}</b>" if tg_name else "مرحباً بك في منصتك المؤسسية"
    trial_line = ""
    if trial.get("is_free") and not trial.get("unlimited"):
        if trial.get("expired"):
            trial_line = "▎🔒 <b>الفترة المجانية:</b> منتهية — اضغط <b>💎 الاشتراك والباقات</b> للترقية الآن\n"
        else:
            dl = trial.get("days_left") or 0
            hl = trial.get("hours_left") or 0
            rem = f"{dl} يوم" if dl >= 1 else f"{hl} ساعة"
            trial_line = f"▎⏳ <b>الفترة المجانية التجريبية:</b> متبقي <b>{rem}</b> (من أصل {trial.get('trial_days', 7)} يوم)\n"
    return (
        f"👑 <b><u>{esc(config.BRAND)}</u></b>\n"
        f"{greet}\n\n"
        "<blockquote>"
        f"▎💎 باقتك الحالية: <b>{plan_title}</b>\n"
        f"{trial_line}"
        f"▎📡 رصيد الإشارات اليومي: <b>{state['used']}/{state['quota']}</b> {bar(state['used'], state['quota'])}\n"
        f"▎🌐 أسواق إشاراتك: <b>{mkts_txt}</b>\n"
        f"▎⚡ الإشارات التلقائية: <b>{notif_txt}</b>"
        "</blockquote>\n"
        "✨ <i>ابدأ فوراً بـ <b>⚡ إشارة الآن</b> أو <b>🔬 تحليل الآن</b> من الصف الأول، "
        "وتحكّم بتشغيل أو إيقاف <b>الإشارات التلقائية</b> بضغطة واحدة من الزر أسفل القائمة.</i>"
    )


async def _render_user_markets(q, uid: int, u, bot, notice: str = ""):
    """v6.17.0: Multi-toggle user market subscriptions within their plan."""
    plan = await afn.run(subs.plan_for, uid)
    allowed = [x.strip().upper().replace("/", "") for x in (plan["markets"] or "").split(",") if x.strip()]
    rows = await afn.run(_live_market_rows)
    available = [r["symbol"].upper().replace("/", "") for r in rows if r["symbol"].upper().replace("/", "") in allowed] or allowed
    selected = _parse_user_markets(u["market"] or "")
    all_mode = (not selected)
    active_set = set(available) if all_mode else (set(selected) & set(available))
    if not all_mode and not active_set and available:
        all_mode = True
        active_set = set(available)

    summary = "🌍 كل أسواق باقتك" if all_mode else " · ".join(sorted(active_set))
    lines = [
        "🌐 <b><u>أسواق إشاراتي</u></b>",
        "<blockquote>"
        f"▎💎 باقتك: <b>{esc(plan['title'])}</b> ({len(available)} سوق متاح)\n"
        f"▎📌 الأسواق النشطة حالياً: <b>{esc(summary)}</b>\n"
        "▎✨ اضغط أي سوق لتشغيله ✅ أو إيقافه ⚪ — مثلاً أغلق الذهب وأبقِ البيتكوين حسب باقتك."
        "</blockquote>",
    ]
    if notice:
        lines.append(f"<i>{esc(notice)}</i>")

    rows_kb = [[kb.btn(
        ("✅ " if all_mode else "🌍 ") + "تفعيل كل أسواق باقتي",
        "u:setmarket:ALL",
        style="success" if all_mode else "primary",
    )]]
    for i in range(0, len(available), 3):
        rows_kb.append([
            kb.btn(
                ("✅ " if s in active_set else "⚪ ") + s,
                f"u:togmarket:{s}",
                style="success" if s in active_set else "",
            )
            for s in available[i:i + 3]
        ])
    rows_kb.append([kb.btn("💎 ترقية الباقة لمزيد من الأسواق", "u:plans", style="primary")])
    rows_kb.append([kb.btn("↩️ التخصيص", "u:preferences")])
    await render(q, "\n".join(lines), kb.user_nav(rows_kb), bot)


async def user_page(q, action: str, uid: int, context, preloaded_u=None) -> None:
    bot = context.bot
    # v6.20.1: reuse preloaded user row from dispatcher._route_user so we NEVER
    # hit the database twice for the exact same user row on one button tap!
    u = preloaded_u if preloaded_u is not None else await afn.run(users_service.touch_and_get, uid)
    if not u:
        await render(q, "لم يتم العثور على حسابك. أرسل /start أولاً.", kb.user_menu(), bot)
        return

    # ---------- Home / profile ----------
    if action == "home":
        if uid == config.ADMIN_ID:
            await render(
                q,
                f"👑 <b>{config.BRAND} | SUPER ADMIN</b>\nاختر القسم من لوحة التحكم الرئيسية:",
                kb.admin_menu(),
                bot,
            )
            return
        home_text = await build_user_home_card(uid, (u["full_name"] or u["username"] or "").strip(), preloaded_u=u)
        await render(q, home_text, kb.user_menu(auto_signals_on=bool(u["notifications"])), bot)

    elif action == "autosig_toggle":
        new_val = 0 if u["notifications"] else 1
        await afn.run(users_service.set_notifications, uid, bool(new_val))
        u = dict(u)
        u["notifications"] = new_val
        try:
            await q.answer(
                "🟢 تم تشغيل الإشارات التلقائية — ستصلك الفرص فوراً"
                if new_val else
                "🔴 تم إيقاف الإشارات التلقائية — يمكنك إعادة تشغيلها بأي وقت",
                show_alert=False,
            )
        except Exception:
            pass
        if uid == config.ADMIN_ID:
            await render(
                q,
                f"👑 <b>{config.BRAND} | SUPER ADMIN</b>\nاختر القسم من لوحة التحكم الرئيسية:",
                kb.admin_menu(),
                bot,
            )
            return
        home_text = await build_user_home_card(uid, (u["full_name"] or u["username"] or "").strip(), preloaded_u=u)
        await render(q, home_text, kb.user_menu(auto_signals_on=bool(new_val)), bot)

    elif action == "profile":
        profile = await afn.run(users_service.profile_summary, uid, u)
        await render(q, render_user_card(u, profile),
                     kb.user_nav([
                         [kb.btn("📈 إحصائياتي", "u:my_stats", style="primary"), kb.btn("🎛 تخصيص الإشارات", "u:preferences")],
                         [kb.btn("💎 الاشتراك", "u:plans", style="success"), kb.btn("🧾 مدفوعاتي", "u:payments")],
                         [kb.btn("🎧 الدعم", "u:support")],
                     ]), bot)

    elif action == "dashboard":
        first_mkt = (_parse_user_markets(u["market"] or "") or ["XAUUSD"])[0]
        snap = await afn.run(_dashboard_snapshot, uid, first_mkt)
        p = snap["plan"]
        state = snap["state"]
        total, active, wins, delivered, anl_used = snap["counts"]
        plan_title = esc(p["title"] if p else "Free")
        anl_quota = int(p["max_analyses_per_day"] or 0) if p else 0
        mrow = snap["market_row"]
        mcat = (mrow or {}).get("category") if mrow else instruments.spec(first_mkt).category
        from fysb.engines.market_data import market_state
        mstate = market_state(mcat)
        mstate_txt = "🟢 مفتوح" if mstate else ("🔴 مغلق" if mstate is False else "🟡 غير محدد")
        body = (f"💎 <b>الباقة:</b> {plan_title}\n\n"
                f"📡 <b>الإشارات:</b>\n"
                f"▎استهلاك اليوم: <b>{state['used']}/{state['quota']}</b> {bar(state['used'], state['quota'])}\n"
                f"▎🧾 إجمالي ما وصلتك: <b>{delivered}</b>\n\n"
                f"🔬 <b>التحليلات:</b>\n"
                f"▎استهلاك اليوم: <b>{anl_used}/{anl_quota}</b>\n\n"
                f"🛡 وضع المنصة: <b>{esc(snap['trading_mode'])}</b>\n"
                f"🌍 أسواقك ({esc(_format_user_markets(u['market'] or ''))}): <b>{mstate_txt}</b>\n"
                f"📈 إجمالي الإشارات المسجلة: <b>{total}</b> (نشطة: {active} · مكتملة: {wins})")
        dash_rows = [
            [kb.btn("📡 فتح الإشارات", "u:signals"), kb.btn("🔬 تحليل جديد", "u:analytics")],
            [kb.btn("🚀 إشارة الآن", "u:signow"), kb.btn("🎛 تخصيص التنبيهات", "u:preferences")],
            [kb.btn("🎓 الأكاديمية المستقلة", "u:academy", style="primary"), kb.btn("🎁 كود هدية", "u:redeem", style="success")],
            [kb.btn("🤝 عمولات الشركاء (35%)", "u:affiliate", style="success"), kb.btn("💎 الباقات", "u:plans")],
            [kb.btn("🛡 ملف المخاطرة", "u:risk"), kb.btn("📓 مفكرة التداول", "u:journal")],
            [kb.btn("🔗 ربط منصات الكريبتو (Binance/Bybit/OKX)", "u:exchange", style="primary")],
        ]
        if config.webapp_ready():
            dash_rows.insert(0, [kb.webapp_btn("📱 فتح التطبيق المصغر الملكي (Mini App)", config.webapp_page_url("/"), style="success")])
        await render(q, body, kb.user_nav(dash_rows), bot)

    # ---------- Analytics (composite institutional engine, plan-gated) ----------
    elif action == "analytics":
        if await _guard_free_trial(q, uid, u, bot, "التحليلات الفورية"):
            return
        from fysb.services import analytics as anl
        if not await afn.run(anl.analytics_enabled):
            await render(q, "🔬 قسم التحليلات معطل حالياً من الإدارة.", kb.user_nav(), bot)
            return
        state = await afn.run(anl.quota_state, uid)
        plan = await afn.run(subs.plan_for, uid)
        is_admin = uid == config.ADMIN_ID
        if not is_admin and (state["quota"] <= 0 or state["remaining"] <= 0):
            why_txt = (
                "التحليلات الفورية غير مفعّلة في باقتك الحالية"
                if state["quota"] <= 0
                else f"انتهت حصة التحليلات اليومية ({state['used']}/{state['quota']}) — تتجدد يومياً"
            )
            await _upsell(q, uid, bot, why_txt, quota_exhausted=True)
            return
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
        await _upsell(
            q, uid, bot,
            "افتح الأسواق العالمية والأطر الزمنية والمدارس المؤسسية الـ 13 وحدوداً يومية أعلى",
        )

    elif action.startswith("anlsym:"):
        if await _guard_free_trial(q, uid, u, bot, "التحليلات الفورية"):
            return
        from fysb.services import analytics as anl
        symbol = action.split(":", 1)[1]
        plan = await afn.run(subs.plan_for, uid)
        is_admin = uid == config.ADMIN_ID
        p_markets = [x.strip().upper() for x in (plan["markets"] or "").split(",") if x.strip()]
        if not is_admin and "ALL" not in p_markets and symbol.upper() not in p_markets:
            await _upsell(q, uid, bot, f"السوق {symbol.upper()} غير مشمول في باقتك الحالية",
                          unlock_kind="markets", unlock_item=symbol.upper())
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
        if not is_admin and "ALL" not in p_tfs and tf.upper() != "ALL" and tf.upper() not in p_tfs:
            await _upsell(q, uid, bot, f"الإطار الزمني {tf.upper()} غير مشمول في باقتك الحالية",
                          unlock_kind="timeframes", unlock_item=tf.upper())
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
        if not is_admin and "ALL" not in p_strats and strategy.upper() != "ALL" and strategy.upper() not in p_strats:
            await _upsell(q, uid, bot, f"الاستراتيجية {strategy.upper()} غير مشمولة في باقتك الحالية",
                          unlock_kind="strategies", unlock_item=strategy.upper())
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
            err_msg = res.get("error", "تعذر التوليد")
            if res.get("quota_exhausted") or "حصة التحليلات" in err_msg:
                await _upsell(q, uid, bot, err_msg, quota_exhausted=True)
                return
            await render(q, f"⛔ {esc(err_msg)}",
                         kb.user_nav([[kb.btn("↩️ التحليلات", "u:analytics", style="primary")]]), bot)
            return
        an = res["analysis"]
        q_state = res.get("quota") or (await afn.run(anl.quota_state, uid))
        row = await afn.run(_market_row, an["symbol"])
        # v8.1.0: Automatic Ultra-Luxury Institutional ForexYemeni Analysis Chart for Users & Admin
        try:
            import io as _io
            from fysb.ui import charts as _charts
            if await afn.run(_charts.charts_enabled):
                png = await asyncio.to_thread(_charts.render_analysis_chart, an, row)
                if png and bot is not None and hasattr(bot, "send_photo"):
                    chat_id = getattr(getattr(q, "message", None), "chat_id", None) or uid
                    cap = _charts.render_analysis_caption(an, market_row=row)
                    await bot.send_photo(
                        chat_id=chat_id,
                        photo=_io.BytesIO(png),
                        caption=cap,
                        parse_mode="HTML",
                    )
        except Exception as _ce:
            log.debug("analysis chart photo delivery skipped: %s", _ce)
        card_text = render_analysis_card(an, market_row=row)
        card_text += f"\n\n📊 <b>حصتك الآن:</b> <b>{q_state['used']}/{q_state['quota']}</b> {bar(q_state['used'], q_state['quota'])}"
        await render(q, card_text,
                     kb.user_nav([[kb.btn("🔬 تحليل جديد", "u:analytics"),
                                   kb.btn("📡 الإشارات", "u:signals")]]), bot)

    # ---------- Signals (v6.20.4 luxury date-grouped archive: Today / Yesterday / By Date) ----------
    elif action == "signals" or action.startswith("sigday:"):
        if await _guard_free_trial(q, uid, u, bot, "مركز الإشارات"):
            return
        from fysb.services import signal_archive as _sa
        from fysb.ui.cards import render_signal_compact
        scope = "today"
        page = 0
        if action.startswith("sigday:"):
            parts = action.split(":")
            scope = (parts[1] if len(parts) > 1 else "today") or "today"
            page = _safe_int(parts[2]) if len(parts) > 2 else 0
            page = max(0, page or 0)

        plan = await afn.run(subs.plan_for, uid)
        state = await afn.run(subs.quota_state, uid, plan)
        markets = [instruments.normalize(x) for x in (plan["markets"] or "XAUUSD").split(",") if x.strip()]
        timeframes = [x.strip() for x in (plan["timeframes"] or "M15").split(",") if x.strip()]
        strategies = [x.strip() for x in (plan["strategies"] or "ICT").split(",") if x.strip()]
        min_score = int(plan["min_score"] or 60)

        size = 8
        counts = await afn.run(_sa.user_scope_counts, uid, markets, timeframes, strategies, min_score)
        total, rows, summary, delivered_ids = await afn.run(
            _sa.user_signals_page, uid, markets, timeframes, strategies, min_score, scope, page, size
        )
        total_pages = max(1, (total + size - 1) // size)

        day_iso = _sa.resolve_scope_date(scope)
        is_today = scope == "today"
        is_yest = scope == "yesterday"
        is_open = scope == "open"
        is_all = scope == "all"
        is_custom_date = bool(day_iso) and not is_today and not is_yest

        rows_kb = [
            [
                kb.btn(("◉ " if is_today else "") + f"📅 اليوم ({counts['today']})", "u:sigday:today:0",
                       style="primary" if is_today else None),
                kb.btn(("◉ " if is_yest else "") + f"📆 أمس ({counts['yesterday']})", "u:sigday:yesterday:0",
                       style="primary" if is_yest else None),
                kb.btn(("◉ " if is_custom_date else "") + f"🗓 بالتاريخ ({counts['days']})", "u:sigdates:0",
                       style="primary" if is_custom_date else None),
            ],
            [
                kb.btn(("◉ " if is_open else "") + f"🟢 المفتوحة الآن ({counts['open']})", "u:sigday:open:0",
                       style="success" if is_open else None),
                kb.btn(("◉ " if is_all else "") + f"🗂 كل الإشارات ({counts['total']})", "u:sigday:all:0"),
            ],
        ]

        from fysb.ui.cards import signal_row_style
        for r in rows:
            is_deliv = int(r["id"]) in delivered_ids
            prefix = "" if (is_deliv or state["remaining"] > 0) else "🔒 "
            rows_kb.append([kb.btn(prefix + render_signal_compact(r), f"u:signalview:{r['id']}", style=signal_row_style(r))])

        nav_row = []
        if page > 0:
            nav_row.append(kb.btn("◀️ السابق", f"u:sigday:{scope}:{page - 1}"))
        if (page + 1) * size < total:
            nav_row.append(kb.btn("التالي ▶️", f"u:sigday:{scope}:{page + 1}"))
        if nav_row:
            rows_kb.append(nav_row)

        if not rows:
            rows_kb.append([kb.btn("🔄 تحديث الإشارات", f"u:sigday:{scope}:0")])

        extra = [
            f"▎💎 الباقة: <b>{esc(plan['title'])}</b> · الحد اليومي: <b>{state['used']}/{state['quota']}</b> (المتبقي: <b>{state['remaining']}</b>)",
            "▎📌 الإشارات التي استلمتها محفوظة في أرشيفك ولا تختفي أبداً بعد الإغلاق.",
        ]
        # v6.10.0 (owner report 7) — «إشارة الآن» right inside the user's
        # signals hub + the watcher status line (honest auto-search).
        from fysb.handlers import signal_now as _sn
        w = await afn.run(_sn.active_watcher, uid)
        if w:
            extra.append(f"▎⏰ بحث تلقائي نشط: <b>{esc(w['symbol'])} · {esc(w['strategy'])}</b> كل {w['interval_minutes']} دقيقة")
            rows_kb.append([kb.btn("🔴 إلغاء البحث التلقائي", f"u:watchoff:{w['id']}")])
        if not rows:
            if is_today:
                extra.append("▎✨ لم تصلك إشارات حية اليوم بعد — المحرك يراقب الأسواق تلقائياً وستصلك كل إشارة حقيقية فور اكتمال شروطها، أو اضغط «🚀 إشارة الآن».")
            elif is_open:
                extra.append("▎✨ لا توجد صفقات مفتوحة مرسلة لحسابك حالياً — ستظهر هنا وتُتتبع لحظياً فور صدور إشارة جديدة.")
            else:
                extra.append("▎لا توجد إشارات مرسلة لحسابك في هذا التاريخ.")

        rows_kb.append([kb.btn("🚀 إشارة الآن — توليد بمراحل حقيقية", "u:signow", style="success")])
        rows_kb.append([
            kb.btn("🛡 ملف المخاطرة واللوت", "u:risk", style="primary"),
            kb.btn("📓 مفكرة التداول", "u:journal", style="success"),
        ])

        if is_today:
            scope_label = _sa.ar_date_label(_sa.today_iso())
        elif is_yest:
            scope_label = _sa.ar_date_label(_sa.yesterday_iso())
        elif is_custom_date:
            scope_label = _sa.ar_date_label(day_iso)
        elif is_open:
            scope_label = "🟢 الصفقات المفتوحة الآن"
        else:
            scope_label = "🗂 سجل جميع الإشارات"

        body = _sa.luxury_day_header("مركز الإشارات والأرشيف", scope_label, summary, page, total_pages, extra)
        await render(q, body, kb.user_nav(rows_kb), bot)

    elif action.startswith("sigdates:"):
        if await _guard_free_trial(q, uid, u, bot, "أرشيف الإشارات"):
            return
        from fysb.services import signal_archive as _sa
        page = max(0, _safe_int(action.split(":", 1)[1]) or 0)
        plan = await afn.run(subs.plan_for, uid)
        markets = [instruments.normalize(x) for x in (plan["markets"] or "XAUUSD").split(",") if x.strip()]
        timeframes = [x.strip() for x in (plan["timeframes"] or "M15").split(",") if x.strip()]
        strategies = [x.strip() for x in (plan["strategies"] or "ICT").split(",") if x.strip()]
        min_score = int(plan["min_score"] or 60)
        size = 10
        total_days, rows = await afn.run(_sa.user_dates_page, uid, markets, timeframes, strategies, min_score, page, size)
        total_pages = max(1, (total_days + size - 1) // size)
        rows_kb = [
            [kb.btn(_sa.format_date_journal_button(r), f"u:sigday:{r['day']}:0")]
            for r in rows
        ]
        nav_row = []
        if page > 0:
            nav_row.append(kb.btn("◀️ أحدث", f"u:sigdates:{page - 1}"))
        if (page + 1) * size < total_days:
            nav_row.append(kb.btn("أقدم ▶️", f"u:sigdates:{page + 1}"))
        if nav_row:
            rows_kb.append(nav_row)
        rows_kb.append([
            kb.btn("📅 إشارات اليوم", "u:sigday:today:0", style="primary"),
            kb.btn("📆 إشارات أمس", "u:sigday:yesterday:0"),
        ])
        rows_kb.append([kb.btn("🟢 المفتوحة الآن", "u:sigday:open:0"), kb.btn("↩️ الإشارات", "u:signals")])
        body = (
            f"🗓 <b><u>أرشيف إشاراتك حسب التاريخ</u></b>\n"
            f"<blockquote>▎📚 إجمالي الأيام المسجلة في حسابك: <b>{total_days}</b> · صفحة <b>{page + 1}/{total_pages}</b>\n"
            f"▎اختر أي تاريخ لاستعراض جميع إشاراته المفتوحة والمغلقة مع نتائجها الحقيقية.</blockquote>"
        )
        if not rows:
            body += "\n\n<i>لا توجد إشارات مسجلة في أرشيفك بعد.</i>"
        await render(q, body, kb.user_nav(rows_kb), bot)

    elif action.startswith("signalview:"):
        if await _guard_free_trial(q, uid, u, bot, "تفاصيل الإشارات"):
            return
        sid = _safe_int(action.split(":", 1)[1])
        if sid is None:
            await render(q, "معرّف غير صالح.", kb.user_nav(), bot)
            return
        sig = await afn.run(_signal_row, sid)
        if not sig or sig["status"] in ("CANCELLED",):
            await render(q, "الإشارة غير متاحة.", kb.user_nav(), bot)
            return
        # v6.23.0: strict post-join & privacy guard — a user can NEVER view or get
        # enrolled into tracking for pre-join signals or another user's private SIGNAL_NOW.
        if uid != config.ADMIN_ID and int(sig["created_by"] or 0) != uid:
            is_delivered = await afn.run(_user_has_delivered_signal, uid, sid)
            if not is_delivered:
                from fysb.engines.signal_engine import _parse_iso_utc
                is_other_priv = (sig["source"] or "").upper() == "SIGNAL_NOW"
                sig_dt = _parse_iso_utc(sig["created_at"] or "")
                join_dt = _parse_iso_utc((u["joined_at"] if u and "joined_at" in u.keys() else "") or "")
                is_pre_join = (sig_dt is not None and join_dt is not None and sig_dt < join_dt)
                if is_other_priv or is_pre_join:
                    await render(
                        q,
                        "⛔ <b>هذه الإشارة لم تُرسل لحسابك</b>\n"
                        "<blockquote>▎لا يمكن عرض أو تتبع إشارات سابقة قبل انضمامك أو خاصة بمستخدم آخر.\n"
                        "▎المحرك يراقب الأسواق تلقائياً وستصلك كل إشارة حية جديدة فور صدورها.</blockquote>",
                        kb.user_nav([[kb.btn("🚀 إشارة الآن", "u:signow", style="success")], [kb.btn("↩️ الإشارات", "u:signals")]]),
                        bot,
                    )
                    return
        # Server-side quota consumption (anti-bypass: re-check on every callback)
        consumption = await afn.run(subs.consume_on_view, uid, sid)
        if not consumption["ok"] and not consumption.get("already"):
            await _upsell(
                q, uid, bot,
                f"انتهت حصة الإشارات اليومية ({consumption['used']}/{consumption['quota']}) — تتجدد يومياً",
                quota_exhausted=True,
            )
            return
        row = await afn.run(_market_row, sig["symbol"])
        card_text = render_signal_card(dict(sig), user_view=True, row=row)
        # Live market pulse ONLY for still-OPEN trades — v6.20.3: CLOSED signals
        # (including TP1/TP2 closed at Breakeven) NEVER show a drifting market price.
        from fysb.theme import is_signal_closed
        sig_d = dict(sig)
        if is_signal_closed(sig_d):
            if (sig_d.get("status") or "").upper() in ("TP1_HIT", "TP2_HIT"):
                banked = float(sig_d.get("pips_result") or 0.0)
                card_text += (
                    f"\n\n<blockquote>🔒 <b>الصفقة مغلقة نهائياً على ربح محقق ({banked:+.1f} pips)</b> — "
                    f"أُغلق الجزء المتبقي عند نقطة التأمين (الدخول) وانتهت متابعة هذه الإشارة؛ "
                    f"المحرك يبحث الآن عن الفرص الجديدة.</blockquote>"
                )
        elif sig["status"] in ("ACTIVE", "RUNNING", "TP1_HIT", "TP2_HIT"):
            try:
                from fysb.engines import market_data
                from fysb.engines.tracker import drift_pips
                price = market_data.cached_price(sig["symbol"], max_age_seconds=120.0)
                if price <= 0:
                    price = float(sig_d.get("live_px") or 0.0)
                if price <= 0:
                    quote = await asyncio.wait_for(
                        asyncio.to_thread(market_data.fetch_price, sig["symbol"]),
                        timeout=1.5,
                    )
                    price = float((quote or {}).get("price") or 0.0)
                if price > 0:
                    entry = float(sig["entry"])
                    drift = drift_pips(sig["symbol"], sig["direction"], entry, price)
                    side = "أعلى الدخول" if price > entry else ("عند الدخول" if price == entry else "أدنى الدخول")
                    card_text += (f"\n\n<blockquote>💰 <b>سعر السوق الآن:</b> "
                                  f"<code>{instruments.fmt_price(sig['symbol'], price, row)}</code>"
                                  f" · {side} · <b>{drift:+.1f} pips</b> من الدخول</blockquote>")
            except Exception:
                pass
        from fysb.services import user_trade
        personal = await afn.run(user_trade.personal_block, dict(sig), uid, row)
        if personal:
            card_text += "\n\n<blockquote>" + personal + "</blockquote>"
        # v6.14.0: deliver signal card as its own dedicated message AND pin
        # chat_id/message_id in signal_deliveries so the tracker updates this
        # card live on TP1/TP2/TP3/SL/price moves!
        action_rows = [
            [kb.signal_demo_launch_btn(sid)],
            [kb.btn("🧮 احسب اللوت", f"u:lotcalc:{sid}", style="primary"),
             kb.btn("📊 شارت الإشارة", f"u:sigchart:{sid}", style="primary")],
            [kb.btn("🖼 بطاقة ربح للمشاركة", f"u:sharecard:sig:{sid}", style="primary")],
            [kb.btn("✅ دخلت الصفقة", f"u:jtake:{sid}", style="success"),
             kb.btn("❌ تجاهلتها", f"u:jskip:{sid}", style="danger")],
        ]
        nav_btns = action_rows + (
            [[kb.btn("📅 إشارات اليوم", "u:sigday:today:0"), kb.btn("🗓 أرشيف التواريخ", "u:sigdates:0")]]
            if is_signal_closed(sig_d)
            else [[kb.btn("🧪 متابعة تجريبية", f"u:demoopen:{sid}", style="success"),
                   kb.btn("↩️ كل الإشارات", "u:signals")]]
        )
        sent_msg = await render(
            q, card_text,
            kb.user_nav(nav_btns),
            bot, force_new=True,
        )
        if sent_msg is not None:
            from fysb.engines.signal_engine import record_delivery_refs
            await afn.run(record_delivery_refs, uid, sid, sent_msg)

    # ---------- Preferences ----------
    elif action == "preferences":
        plan = await afn.run(subs.plan_for, uid)
        mkt_pref = esc(_format_user_markets(u["market"] or ""))
        tf_pref = "الكل (حسب باقتك)" if not (u["timeframe"] or "").strip() else tf_name(u["timeframe"])
        body = (f"🎛 <b><u>أسواقي وتخصيص الإشارات</u></b>\n\n"
                f"<blockquote>"
                f"▎🌐 أسواق الإشارات: <b>{mkt_pref}</b>\n"
                f"▎⏱ الإطار المفضل: <b>{tf_pref}</b>\n"
                f"▎🧠 الاستراتيجية المفضلة: <b>{esc(strategy_name(u['strategy']))}</b>\n"
                f"▎⭐ الجودة الدنيا: <b>{u['min_score']}/100</b>\n"
                f"▎🔔 الإشعارات: <b>{'مفعلة' if u['notifications'] else 'متوقفة'}</b>\n"
                f"</blockquote>\n"
                f"<i>الخيارات مقيدة بمزايا باقتك ({esc(plan['title'])}) — ويمكنك تفعيل أو إيقاف أي سوق فردياً من «🌐 أسواق إشاراتي».</i>")
        await render(q, body, kb.user_nav([
            [kb.btn("🌐 أسواق إشاراتي", "u:markets", style="primary"), kb.btn("⏱ الإطار", "u:timeframes")],
            [kb.btn("🧠 الاستراتيجية", "u:strategies"), kb.btn("⭐ الجودة", "u:quality")],
            [kb.btn("🔔 الإشعارات", "u:notifications")],
        ]), bot)

    elif action == "markets":
        await _render_user_markets(q, uid, u, bot)

    elif action.startswith("togmarket:"):
        symbol = action.split(":", 1)[1].strip().upper().replace("/", "")
        plan = await afn.run(subs.plan_for, uid)
        allowed = [x.strip().upper().replace("/", "") for x in (plan["markets"] or "").split(",") if x.strip()]
        if "ALL" not in allowed and symbol not in allowed:
            await _upsell(q, uid, bot, f"السوق {symbol} غير متاح في باقتك الحالية",
                          unlock_kind="markets", unlock_item=symbol)
            return
        rows = await afn.run(_live_market_rows)
        available = [r["symbol"].upper().replace("/", "") for r in rows if r["symbol"].upper().replace("/", "") in allowed] or allowed
        selected = _parse_user_markets(u["market"] or "")
        cur = list(available) if not selected else [s for s in selected if s in available]
        if not cur:
            cur = list(available)
        notice = ""
        if symbol in cur:
            if len(cur) <= 1:
                notice = "يجب إبقاء سوق واحد على الأقل نشطاً (ولإيقاف الإشارات مؤقتاً استخدم 🔔 الإشعارات)."
            else:
                cur = [s for s in cur if s != symbol]
                notice = f"تم إيقاف إشارات {symbol} على حسابك."
        else:
            cur.append(symbol)
            notice = f"تم تفعيل إشارات {symbol} على حسابك."
        new_val = "" if set(cur) >= set(available) else ",".join(sorted(set(cur)))
        await afn.run(users_service.set_preference, uid, "market", new_val, {"market"})
        u2 = await afn.run(users_service.get_user, uid)
        await _render_user_markets(q, uid, u2 or u, bot, notice=notice)

    elif action.startswith("setmarket:"):
        symbol = action.split(":", 1)[1].strip().upper().replace("/", "")
        plan = await afn.run(subs.plan_for, uid)
        allowed = [x.strip().upper().replace("/", "") for x in (plan["markets"] or "").split(",") if x.strip()]
        if symbol == "ALL":
            await afn.run(users_service.set_preference, uid, "market", "", {"market"})
            u2 = await afn.run(users_service.get_user, uid)
            await _render_user_markets(q, uid, u2 or u, bot, notice="✅ تم تفعيل جميع أسواق باقتك بنجاح.")
            return
        if "ALL" not in allowed and symbol not in allowed:
            await _upsell(q, uid, bot, f"السوق {symbol} غير متاح في باقتك الحالية",
                          unlock_kind="markets", unlock_item=symbol)
            return
        await afn.run(users_service.set_preference, uid, "market", symbol, {"market"})
        await render(q, f"✅ تم حفظ السوق المفضل: <b>{esc(symbol)}</b>", kb.user_nav([[kb.btn("🌐 أسواق إشاراتي", "u:markets")], [kb.btn("↩️ التفضيلات", "u:preferences")]]), bot)

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
        if "ALL" not in allowed and tf.upper() not in allowed:
            await _upsell(q, uid, bot, f"الإطار الزمني {tf.upper()} غير متاح في باقتك الحالية",
                          unlock_kind="timeframes", unlock_item=tf.upper())
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
        if "ALL" not in allowed and st.upper() not in allowed:
            await _upsell(q, uid, bot, f"الاستراتيجية {st.upper()} غير متاحة في باقتك الحالية",
                          unlock_kind="strategies", unlock_item=st.upper())
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
            [kb.copy_btn("📋 نسخ رابط الدعوة بضغطة واحدة", st["link"], style="primary")],
            [kb.url_btn("📤 شارك رابط الدعوة (نص فاخر جاهز)", share_link, style="success")],
            [kb.url_btn("🔗 فتح رابط الدعوة", st["link"])],
            [kb.btn("💸 لوحة عمولات الشركاء (15%–35%)", "u:affiliate", style="success"),
             kb.btn("💎 الباقات", "u:plans", style="primary")],
        ]
        await render(q, "\n".join(body), kb.user_nav(rows_kb), bot)

    # ---------- Plans & payments ----------
    elif action == "plans":
        rows = [dict(r) for r in await afn.run(_all_plans)]
        current = dict(await afn.run(subs.plan_for, uid))
        trial = await afn.run(subs.free_trial_status, uid, u, current)
        rows_kb = []
        for r in rows:
            code = (r["code"] or "").upper()
            ic = plan_tier_icon(code)
            price = float(r["price"] or 0)
            dur = int(r["duration_days"] or 0)
            if code == "FREE":
                p_lbl = f"مجاني · {'مفتوح' if dur == 0 else f'{dur} يوم'}"
            else:
                p_lbl = f"{price:g} {r['currency']} · {'دائم' if dur == 0 else f'{dur} يوم'}"
            badge = " 🔥" if code == "VIP" else (" ✨" if code == "ROYAL" else "")
            btn_style = "success" if code in ("VIP", "ROYAL") else "primary"
            rows_kb.append([kb.btn(f"{ic} {esc(r['title'])}{badge} · {p_lbl}", f"u:planview:{r['code']}", style=btn_style)])
        rows_kb.append([kb.btn("🧾 مدفوعاتي وطلباتي", "u:payments", style="primary")])
        body = render_plans_showcase_card(rows, current, trial_info=trial)
        await render(q, body, kb.user_nav(rows_kb), bot)

    elif action.startswith("planview:"):
        code = action.split(":", 1)[1]
        p = await afn.run(_plan_row, code)
        if not p:
            await render(q, "هذه الباقة غير متاحة حالياً.", kb.user_nav([[kb.btn("↩️ الباقات", "u:plans", style="primary")]]), bot)
            return
        methods = await afn.run(payments_service.list_methods, enabled_only=True)
        chains = await afn.run(payments_service.configured_chains)
        trial = await afn.run(subs.free_trial_status, uid, u) if code.upper() == "FREE" else None
        rows_kb = []
        body = render_plan_card(dict(p), trial_info=trial)
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
            from fysb.services import promos as _promos
            orig_price = float(p["price"] or 0.0)
            active_promo = context.user_data.get("active_promo") or {}
            promo_code = ""
            promo_pct = 0
            final_price = orig_price
            if active_promo.get("code"):
                v_pr = await afn.run(_promos.validate_promo, active_promo["code"], uid)
                if v_pr.get("ok"):
                    promo_code = v_pr["code"]
                    promo_pct = int(v_pr["discount_pct"])
                    final_price, saved_usd = _promos.apply_discount(orig_price, promo_pct)
                    body += (
                        f"\n\n🎟 <b>كود الخصم مفعّل:</b> <code>{esc(promo_code)}</code> "
                        f"(<b>-{promo_pct}%</b> · وفّرت <b>${saved_usd:.2f}</b>)\n"
                        f"▎💰 السعر بعد الخصم: <b>${final_price:g} {esc(p['currency'])}</b> "
                        f"<s>${orig_price:g}</s>"
                    )
                else:
                    context.user_data.pop("active_promo", None)

            if await afn.run(_promos.stars_enabled) and final_price > 0:
                stars_amt = await afn.run(_promos.plan_stars_price, final_price)
                rows_kb.append([kb.btn(
                    f"⭐ دفع فوري بنجوم تيليجرام — {stars_amt} ⭐ XTR",
                    f"u:starspay:{code}",
                    style="success",
                )])

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
                    rows_kb.append([kb.btn(f"⚡ اشتراك فوري — USDT {ch}", f"u:usdtpay:{code}:{ch}", style="success")])
            if methods:
                for m in methods:
                    rows_kb.append([kb.btn(f"🧾 اشتراك عبر {esc(m['title'])}", f"u:requestplan:{code}:{m['id']}")])
            else:
                rows_kb.append([kb.btn("🧾 طلب اشتراك (دفع يدوي)", f"u:requestplan:{code}:0")])
            rows_kb.append([kb.btn(
                f"🎟 {'تغيير' if promo_code else 'إدخال'} كود خصم (%)",
                f"u:applypromo:{code}",
                style="primary",
            )])
        rows_kb.append([kb.btn("↩️ كل الباقات", "u:plans")])
        await render(q, body, kb.user_nav(rows_kb), bot)

    elif action.startswith("applypromo:"):
        code = action.split(":", 1)[1]
        context.user_data["awaiting_promo_code"] = code
        await render(
            q,
            "🎟 <b>أدخل كود الخصم النسبي (%) الآن</b>\n"
            "<i>مثال: <code>ROYAL30</code> — سيُحسب السعر المخفّض تلقائياً للـ USDT ونجوم تيليجرام.</i>",
            kb.user_nav([[kb.btn("↩️ العودة للباقة", f"u:planview:{code}")]]),
            bot,
        )

    elif action.startswith("starspay:"):
        code = action.split(":", 1)[1].upper()
        p = await afn.run(_plan_row, code)
        if not p or code == "FREE":
            await render(q, "الباقة غير متاحة.", kb.user_nav([[kb.btn("↩️ كل الباقات", "u:plans")]]), bot)
            return
        from telegram import LabeledPrice
        from fysb.services import promos as _promos
        orig_price = float(p["price"] or 0.0)
        final_price = orig_price
        promo_code = ""
        active_promo = context.user_data.get("active_promo") or {}
        if active_promo.get("code"):
            v_pr = await afn.run(_promos.validate_promo, active_promo["code"], uid)
            if v_pr.get("ok"):
                promo_code = v_pr["code"]
                final_price, _ = _promos.apply_discount(orig_price, int(v_pr["discount_pct"]))
        stars_amt = await afn.run(_promos.plan_stars_price, final_price)
        payload = f"plan:{code}:{promo_code}"
        try:
            await bot.send_invoice(
                chat_id=uid,
                title=f"{config.BRAND} — {p['title']}",
                description=f"تفعيل فوري لباقة {p['title']} لمدة {int(p.get('duration_days') or 30)} يوماً"
                            + (f" (خصم {promo_code})" if promo_code else ""),
                payload=payload,
                provider_token="",
                currency="XTR",
                prices=[LabeledPrice(label=f"باقة {p['title']}", amount=stars_amt)],
            )
            await render(
                q,
                f"⭐ <b>تم إرسال فاتورة نجوم تيليجرام بنجاح!</b>\n"
                f"▎💎 الباقة: <b>{esc(p['title'])}</b> · ⭐ <b>{stars_amt} XTR</b>\n"
                f"▎⚡ اضغط زر <b>الدفع</b> في الفاتورة بالأسفل لتفعيل باقتك فوراً خلال 3 ثوانٍ.",
                kb.user_nav([[kb.btn("↩️ الباقة", f"u:planview:{code}")]]),
                bot,
            )
        except Exception as exc:
            await render(
                q,
                f"⚠️ تعذر إرسال فاتورة النجوم حالياً ({esc(str(exc)[:120])}).\nيمكنك الاشتراك عبر USDT أو الدفع اليدوي.",
                kb.user_nav([[kb.btn("↩️ الباقة", f"u:planview:{code}")]]),
                bot,
            )

    elif action.startswith("sharecard:"):
        # v7.6.0 Branded PNG PnL Share Card (Signal or Demo Trade)
        parts = action.split(":")
        kind = parts[1] if len(parts) > 1 else "sig"
        target_id = _safe_int(parts[2] if len(parts) > 2 else "0")
        from fysb.ui import share_cards as _sc
        bot_un = ""
        try:
            me_obj = await bot.get_me()
            bot_un = getattr(me_obj, "username", "") or ""
        except Exception:
            pass
        payload = None
        if kind == "demo" and target_id:
            from fysb.engines import demo as _de
            payload = await afn.run(_de.share_card_payload_for_position, uid, target_id, bot_un)
        elif target_id:
            sig = await afn.run(_signal_row, target_id)
            if sig:
                sig_d = dict(sig)
                row = await afn.run(_market_row, sig_d["symbol"])
                from fysb.ui import charts as _charts
                m = _charts.live_signal_metrics(sig_d, row=row)
                bn = (bot_un or config.BRAND or "ForexYemeniBot").lstrip("@")
                payload = {
                    "kind": "signal",
                    "symbol": sig_d["symbol"],
                    "direction": sig_d["direction"],
                    "strategy": sig_d.get("strategy") or "ROYAL",
                    "timeframe": sig_d.get("timeframe") or "M15",
                    "entry": float(sig_d.get("entry") or 0),
                    "exit_price": float(m.get("live_px") or sig_d.get("tp1") or sig_d.get("entry") or 0),
                    "tp3": float(sig_d.get("tp3") or sig_d.get("tp1") or 0),
                    "tp_label": str(sig_d.get("status") or "ACTIVE").upper(),
                    "pips": float(m.get("pips") or sig_d.get("pips_result") or 0),
                    "rr": float(sig_d.get("rr") or 0),
                    "score": int(sig_d.get("score") or 0),
                    "ref_link": f"t.me/{bn}?start=ref_{uid}",
                    "ref_label": f"SHARED VIA {config.BRAND} • REF #{uid}",
                    "timestamp": str(sig_d.get("closed_at") or sig_d.get("created_at") or config.now()),
                }
        png = await asyncio.to_thread(_sc.render_pnl_share_card, payload) if payload else None
        if not png:
            await render(q, "⚠️ تعذر إنشاء بطاقة الربح حالياً.", kb.user_nav([[kb.btn("↩️ رجوع", "u:signals")]]), bot)
            return
        import io as _io
        await bot.send_photo(
            chat_id=uid,
            photo=_io.BytesIO(png),
            caption=(
                f"🏆 <b>بطاقة الربح الملكية — {esc(payload['symbol'])}</b>\n"
                f"▎🔗 رابط إحالتك مضمّن على البطاقة: <code>{esc(payload['ref_link'])}</code>\n"
                "<i>شاركها الآن في قنواتك وحالاتك لجلب مشتركين جدد وكسب عمولتك تلقائياً!</i>"
            ),
            parse_mode="HTML",
        )
        return

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
        from fysb.services import promos as _promos
        final_price = float(p["price"] or 0.0)
        promo_code, promo_pct = "", 0
        ap = context.user_data.get("active_promo") or {}
        if ap.get("code"):
            vp = await afn.run(_promos.validate_promo, ap["code"], uid)
            if vp.get("ok"):
                promo_code, promo_pct = vp["code"], int(vp["discount_pct"])
                final_price, _ = _promos.apply_discount(final_price, promo_pct)
        pid = await afn.run(payments_service.create_request, uid, code, final_price, f"USDT-{chain}")
        if promo_code and promo_pct:
            def _tag_pr():
                with db.tx() as c:
                    c.execute("UPDATE payments SET promo_code=?, discount_pct=? WHERE id=?", (promo_code, promo_pct, pid))
            await afn.run(_tag_pr)
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
        from fysb.services import promos as _promos
        final_price = float(p["price"] or 0.0)
        promo_code, promo_pct = "", 0
        ap = context.user_data.get("active_promo") or {}
        if ap.get("code"):
            vp = await afn.run(_promos.validate_promo, ap["code"], uid)
            if vp.get("ok"):
                promo_code, promo_pct = vp["code"], int(vp["discount_pct"])
                final_price, _ = _promos.apply_discount(final_price, promo_pct)
        pay_req = await afn.run(payments_service.create_request, uid, code, final_price, method["title"] if method else "MANUAL")
        if promo_code and promo_pct:
            def _tag_pr2():
                with db.tx() as c:
                    c.execute("UPDATE payments SET promo_code=?, discount_pct=? WHERE id=?", (promo_code, promo_pct, pay_req))
            await afn.run(_tag_pr2)
        context.user_data["awaiting_receipt"] = pay_req
        body = [f"🧾 <b>طلب اشتراك #{pay_req}</b>",
                f"💎 الباقة: <b>{esc(p['title'])}</b>",
                f"💰 المبلغ: <b>{final_price:g} {esc(p['currency'])}</b>"
                + (f" (خصم {promo_pct}% · <code>{esc(promo_code)}</code>)" if promo_pct else "")]
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
            rows_kb = []
            if method and method.get("account_ref"):
                rows_kb.append([kb.copy_btn("📋 نسخ رقم الحساب / المحفظة", method["account_ref"], style="primary")])
            rows_kb.append([kb.btn("🧾 مدفوعاتي", "u:payments")])
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
        from fysb.services import auto_bridge as _ab
        br_visible = await afn.run(_ab.is_bridge_visible_to_user, uid)
        demo_rows = [
            [kb.btn("🚀 فتح صفقة تجريبية", "u:demopick"), kb.btn("📂 الصفقات المفتوحة", "u:demoopen_list")],
            [kb.btn("📜 سجل الصفقات", "u:demohistory"), kb.btn("🔄 تصفير الحساب", "u:demoreset")],
            [kb.btn("⚙️ إعدادات حسابي التجريبي", "u:demoset")],
        ]
        if br_visible:
            demo_rows.insert(0, [kb.btn("⚡ جسر النسخ للمنصات (cTrader / TradeLocker / Deriv / MT5)", "u:bridge", style="success")])
        if config.webapp_ready():
            demo_rows.insert(0, [kb.webapp_btn(
                "🎮 متابعة حسابي التجريبي على الشارت الحي",
                config.webapp_page_url("/", query={"tab": "demo"}),
                style="success",
            )])
        await render(q, "\n".join(body), kb.user_nav(demo_rows), bot)

    elif action.startswith("demosig:"):
        sid = _safe_int(action.split(":", 1)[1])
        from fysb.engines import demo as demo_engine
        from fysb.engines.tracker import seq_of
        res = await afn.run(demo_engine.open_signal_interactive, uid, sid, config.market_api_key())
        if not res.get("ok"):
            await render(q, f"⚠️ <b>تعذر تفعيل الصفقة التجريبية للإشارة #{sid}:</b>\n{esc(res.get('error') or 'خطأ غير متوقع')}",
                         kb.user_nav([[kb.btn("🧪 حسابي التجريبي", "u:demo", style="success")]]), bot)
            return
        pos = res["position"]
        sig = res["signal"]
        seq = await afn.run(seq_of, sid)
        sym = pos["symbol"]
        d_ar = "شراء 🟢" if pos["direction"] == "BUY" else "بيع 🔴"
        status_title = "الصفقة مفعّلة مسبقاً وتُتابع حياً" if res.get("already_open") else "تم تفعيل الإشارة على حسابك التجريبي الآن"
        card_body = (
            f"🎮 <b><u>{status_title} — #{seq}</u></b>\n"
            f"<blockquote>"
            f"▎🌐 الأداة: <b>{esc(sym)}</b> · {d_ar} · ⏱ {esc(tf_name(sig.get('timeframe') or 'M15'))}\n"
            f"▎📦 اللوت: <b>{float(pos['lots']):g}</b> · 🎯 الدخول: <code>{instruments.fmt_price(sym, float(pos['entry']))}</code>\n"
            f"▎🛑 الوقف: <code>{instruments.fmt_price(sym, float(pos.get('sl') or 0))}</code> · "
            f"🏁 الهدف: <code>{instruments.fmt_price(sym, float(pos.get('tp') or 0))}</code>\n"
            f"▎📡 الصفقة مرتبطة لحظياً بحسابك التجريبي وبالتطبيق التفاعلي معاً."
            f"</blockquote>"
        )
        kb_rows = []
        if config.webapp_ready():
            kb_rows.append([kb.webapp_btn(
                "📱 فتح ومتابعة الصفقة على الشارت الحي الآن",
                config.webapp_page_url("/", query={"demo_sig": str(sid)}),
                style="success",
            )])
        kb_rows.append([
            kb.btn("📂 صفقاتي المفتوحة", "u:demoopen_list", style="primary"),
            kb.btn("🧪 لوحة التجريبي", "u:demo", style="success"),
        ])
        await render(q, card_body, kb.user_nav(kb_rows), bot)

    elif action == "demoset":
        from fysb.engines import demo as demo_engine
        s = await afn.run(demo_engine.demo_settings, uid)
        lot_word = "تلقائي حسب المخاطرة ورأس المال" if s["lot_mode"] == "auto" else f"ثابت {s['fixed_lot']:g}"
        body = (
            "⚙️ <b><u>إعدادات حسابي التجريبي الذكي</u></b>\n"
            "<blockquote>"
            f"▎💰 الرصيد الحالي: <b>{s['balance']:,.2f} USD</b>\n"
            f"▎⚖️ المخاطرة لكل صفقة: <b>{s['risk_pct']:g}%</b>\n"
            f"▎📦 حجم اللوت: <b>{lot_word}</b>\n"
            f"▎🤖 النسخ التلقائي الذكي للإشارات: <b>{'مفعّل 🟢' if s['auto_trade'] else 'متوقف ⚪'}</b>\n"
            f"▎🛡 إغلاق 50% عند TP1 + نقل الوقف للدخول (BE): <b>{'مفعّل 🟢' if s.get('partial_tp_be', True) else 'متوقف ⚪'}</b>\n"
            f"</blockquote>\n"
            "<i>عند التفعيل: أي إشارة جديدة تُنسخ تلقائياً على حسابك التجريبي بحجم مخاطرتك، وعند تحقيق الهدف الأول "
            "يُغلق 50% تلقائياً وتُحجز الأرباح ويُؤمَّن الباقي على نقطة الدخول (BE).</i>")
        await render(q, body, kb.user_nav([
            [kb.btn("💰 تعديل الرصيد", "u:demobal"), kb.btn("⚖️ المخاطرة", "u:demorisk")],
            [kb.btn("📦 اللوت", "u:demolot"), kb.btn(f"🤖 النسخ التلقائي: {'إيقاف' if s['auto_trade'] else 'تفعيل'}", "u:demoauto")],
            [kb.btn(f"🛡 تأمين 50% TP1 + BE: {'إيقاف' if s.get('partial_tp_be', True) else 'تفعيل'}", "u:demopartialbe", style="primary")],
            [kb.btn("↩️ التجريبي", "u:demo")],
        ]), bot)

    elif action == "demopartialbe":
        from fysb.engines import demo as demo_engine
        s = await afn.run(demo_engine.demo_settings, uid)
        nxt = not s.get("partial_tp_be", True)
        await afn.run(demo_engine.set_partial_tp_be, uid, nxt)
        await render(
            q,
            f"🛡 <b>تأمين 50% عند TP1 + نقل الوقف للدخول (BE) أصبح الآن: {'مفعّل 🟢' if nxt else 'متوقف ⚪'}</b>",
            kb.user_nav([[kb.btn("⚙️ إعدادات التجريبي", "u:demoset"), kb.btn("🧪 التجريبي", "u:demo")]]),
            bot,
        )

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
        rows = await afn.run(_open_signals_rows, uid)
        rows_kb = [[kb.btn(f"#{r['seq']} {esc(r['symbol'])} {r['direction']} {r['score']}/100", f"u:demolots:{r['id']}")] for r in rows]
        rows_kb.append([kb.btn("↩️ التجريبي", "u:demo")])
        body_demo = (
            "🚀 <b>فتح صفقة تجريبية</b>\nاختر إشارة لإدخالها محاكاةً (سعر الدخول الحقيقي + سبريد وانزلاق):"
            if rows else
            "🚀 <b>فتح صفقة تجريبية</b>\n<i>لا توجد إشارات مفتوحة مرسلة لحسابك حالياً — ستظهر هنا فور استلامك إشارة جديدة.</i>"
        )
        await render(q, body_demo, kb.user_nav(rows_kb), bot)

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
        await _handle_support_page(q, uid, context, bot)

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

    elif action == "leaderboard" or action.startswith("lb:") or action in ("strength", "news"):
        # v7.4.0: Strategy Leaderboard, Currency Strength & News Guard are Admin-only analytics.
        # Regular users receive luxury economic news bulletins automatically when enabled by Admin.
        if uid != config.ADMIN_ID:
            await render(
                q,
                "🔒 <b><u>هذا القسم مخصّص لإدارة المنصة فقط</u></b>\n"
                "<blockquote>"
                "▎👑 أدوات التحليل الإداري (مصفوفة الاستراتيجيات، قوة العملات، ودرع الأخبار) متاحة للإدارة فقط.\n"
                "▎📰 تصلك نشرات الأخبار الاقتصادية القوية تلقائياً كتنبيهات ملكية فور تفعيلها من الإدارة."
                "</blockquote>",
                kb.user_nav([[kb.btn("📡 مركز الإشارات", "u:signals", style="success")]]),
                bot,
            )
            return
        if action == "strength":
            from fysb.engines import intelligence as _intel
            body = await afn.run(_intel.render_strength_card, None, force_refresh=True)
            await render(q, body, kb.admin_nav([[kb.btn("↩️ المحرك", "a:engine")]]), bot)
        elif action == "news":
            from fysb.engines import news_guard as _ng
            body = await afn.run(_ng.render_news_calendar_html, force_refresh=True)
            await render(q, body, kb.admin_nav([[kb.btn("↩️ درع الأخبار", "a:newsguard")]]), bot)
        else:
            period = action.split(":", 1)[1] if action.startswith("lb:") else "30d"
            from fysb.services import leaderboard as _lb
            body = await afn.run(_lb.render_leaderboard_card, period, False)
            await render(q, body, kb.admin_nav([[kb.btn("↩️ مصفوفة الاستراتيجيات", "a:leaderboard")]]), bot)

    # --- v6.26.0 Risk Profile, 1-Tap Lot Calculator, Daily DD Guard, Journal & Charts ---
    elif action == "risk" or action.startswith(("rbal:", "rrisk:", "rlev:", "rdd:")):
        from fysb.services import risk_journal as _rj
        if action.startswith("rbal:"):
            val = _safe_float(action.split(":", 1)[1])
            if val:
                await afn.run(_rj.update_risk_profile, uid, balance=val)
        elif action.startswith("rrisk:"):
            val = _safe_float(action.split(":", 1)[1])
            if val:
                await afn.run(_rj.update_risk_profile, uid, risk_pct=val)
        elif action.startswith("rlev:"):
            val = _safe_int(action.split(":", 1)[1])
            if val:
                await afn.run(_rj.update_risk_profile, uid, leverage=val)
        elif action.startswith("rdd:"):
            val = _safe_float(action.split(":", 1)[1])
            if val:
                await afn.run(_rj.update_risk_profile, uid, max_daily_dd_pct=val)
        prof = await afn.run(_rj.get_risk_profile, uid)
        body = await afn.run(_rj.render_risk_profile_card, uid)
        rows_kb = [
            [
                kb.btn(f"{'✅ ' if prof['balance'] == 250 else ''}$250", "u:rbal:250"),
                kb.btn(f"{'✅ ' if prof['balance'] == 1000 else ''}$1K", "u:rbal:1000"),
                kb.btn(f"{'✅ ' if prof['balance'] == 5000 else ''}$5K", "u:rbal:5000"),
                kb.btn(f"{'✅ ' if prof['balance'] == 10000 else ''}$10K", "u:rbal:10000"),
            ],
            [
                kb.btn(f"{'✅ ' if prof['risk_pct'] == 0.5 else ''}0.5%", "u:rrisk:0.5"),
                kb.btn(f"{'✅ ' if prof['risk_pct'] == 1.0 else ''}1%", "u:rrisk:1"),
                kb.btn(f"{'✅ ' if prof['risk_pct'] == 2.0 else ''}2%", "u:rrisk:2"),
                kb.btn(f"{'✅ ' if prof['risk_pct'] == 3.0 else ''}3%", "u:rrisk:3"),
            ],
            [
                kb.btn(f"{'✅ ' if prof['leverage'] == 50 else ''}1:50", "u:rlev:50"),
                kb.btn(f"{'✅ ' if prof['leverage'] == 100 else ''}1:100", "u:rlev:100"),
                kb.btn(f"{'✅ ' if prof['leverage'] == 200 else ''}1:200", "u:rlev:200"),
                kb.btn(f"{'✅ ' if prof['leverage'] == 500 else ''}1:500", "u:rlev:500"),
            ],
            [
                kb.btn(f"{'✅ ' if prof['max_daily_dd_pct'] == 2.0 else ''}سحب 2%", "u:rdd:2", style="danger"),
                kb.btn(f"{'✅ ' if prof['max_daily_dd_pct'] == 3.0 else ''}سحب 3%", "u:rdd:3", style="danger"),
                kb.btn(f"{'✅ ' if prof['max_daily_dd_pct'] == 5.0 else ''}سحب 5%", "u:rdd:5", style="danger"),
            ],
            [kb.btn("📓 مفكرة التداول التلقائية", "u:journal", style="success"), kb.btn("📡 الإشارات", "u:signals")],
        ]
        await render(q, body, kb.user_nav(rows_kb), bot)

    elif action == "journal" or action.startswith("journal:"):
        days = _safe_int(action.split(":", 1)[1]) if ":" in action else 30
        if days not in (7, 30, 90):
            days = 30
        from fysb.services import risk_journal as _rj
        body = await afn.run(_rj.render_journal_card, uid, days)
        rows_kb = [
            [
                kb.btn("📅 7 أيام" + (" ✓" if days == 7 else ""), "u:journal:7", style="success" if days == 7 else "primary"),
                kb.btn("📆 30 يوماً" + (" ✓" if days == 30 else ""), "u:journal:30", style="success" if days == 30 else "primary"),
                kb.btn("📚 90 يوماً" + (" ✓" if days == 90 else ""), "u:journal:90", style="success" if days == 90 else "primary"),
            ],
            [kb.btn("🛡 تعديل ملف المخاطرة", "u:risk", style="primary"), kb.btn("📡 الإشارات", "u:signals")],
        ]
        await render(q, body, kb.user_nav(rows_kb), bot)

    elif action.startswith("lotcalc:"):
        sid = _safe_int(action.split(":", 1)[1])
        sig = await afn.run(_signal_row, sid) if sid else None
        if not sig:
            await render(q, "الإشارة غير متاحة.", kb.user_nav(), bot)
            return
        from fysb.services import risk_journal as _rj
        mrow = await afn.run(_market_row, sig["symbol"])
        body = await afn.run(_rj.render_lot_calculator_card, uid, dict(sig), mrow)
        rows_kb = [
            [kb.btn("🛡 تعديل رصيدي ومخاطرتي", "u:risk", style="primary"),
             kb.btn("✅ دخلت الصفقة", f"u:jtake:{sid}", style="success")],
            [kb.btn("📊 شارت الإشارة", f"u:sigchart:{sid}", style="primary"),
             kb.btn("↩️ الإشارات", "u:signals")],
        ]
        await render(q, body, kb.user_nav(rows_kb), bot)

    elif action.startswith(("jtake:", "jskip:")):
        is_take = action.startswith("jtake:")
        sid = _safe_int(action.split(":", 1)[1])
        sig = await afn.run(_signal_row, sid) if sid else None
        if not sig:
            await render(q, "الإشارة غير متاحة.", kb.user_nav(), bot)
            return
        from fysb.services import risk_journal as _rj
        res = await afn.run(_rj.log_journal_action, uid, dict(sig), "TAKEN" if is_take else "SKIPPED")
        dd = res.get("drawdown") or {}
        if is_take:
            dd_warn = (
                f"\n▎🚨 <b>تحذير حارس السحب اليومي:</b> خسارة يومك {dd.get('today_loss_pct', 0):.2f}% "
                f"(تجاوزت حدك {dd.get('max_daily_dd_pct', 3):g}%)!"
                if dd.get("breached") else ""
            )
            body = (
                f"✅ <b><u>تم تسجيل دخولك الصفقة في مفكرة التداول</u></b>\n"
                f"<blockquote>"
                f"▎📌 الأداة: <b>{esc(sig['symbol'])}</b> {sig['direction']} · ⏱ {esc(sig['timeframe'])}\n"
                f"▎📦 اللوت المحسوب من ملف مخاطرتك: <code>{res['lots']:.2f} lot</code> (مخاطرة: <b>{res['risk_usd']:,.2f} $</b>)\n"
                f"▎🔄 ستُحدّث نتيجتك في المفكرة تلقائياً فور إغلاق الصفقة.{dd_warn}"
                f"</blockquote>"
            )
        else:
            body = (
                f"❌ <b>تم تسجيل تجاهل الصفقة في مفكرتك ({esc(sig['symbol'])} {sig['direction']})</b>\n"
                "<blockquote>▎يساعدك ذلك في قياس انضباطك ومقارنة الصفقات التي دخلتها بتلك التي تجاهلتها.</blockquote>"
            )
        rows_kb = [
            [kb.btn("📓 عرض مفكرة التداول", "u:journal", style="success"),
             kb.btn("🧮 حاسبة اللوت", f"u:lotcalc:{sid}", style="primary")],
            [kb.btn("📡 الإشارات", "u:signals")],
        ]
        await render(q, body, kb.user_nav(rows_kb), bot)

    elif action.startswith("sigchart:"):
        sid = _safe_int(action.split(":", 1)[1])
        sig = await afn.run(_signal_row, sid) if sid else None
        if not sig:
            await render(q, "الإشارة غير متاحة.", kb.user_nav(), bot)
            return
        from fysb.handlers.user_pages import build_signal_chart_bundle
        bundle = await asyncio.to_thread(build_signal_chart_bundle, dict(sig), False)
        png_bytes, is_proof = bundle["png"], bundle["is_proof"]
        if not png_bytes:
            await render(
                q,
                "⚠️ تعذر توليد الشارت اللحظي حالياً (بيانات الشموع غير متوفرة مؤقتاً) — يمكنك متابعة مستويات الإشارة النصية.",
                kb.user_nav([[kb.btn("↩️ الإشارات", "u:signals")]]),
                bot,
            )
            return
        cap = (
            f"{'🏆 شارت إثبات موثّق' if is_proof else '📊 شارت الإشارة الملكي'} — "
            f"<b>{esc(sig['symbol'])}</b> {sig['direction']} · {esc(sig['timeframe'])} · {esc(sig['strategy'])}\n"
            f"{bundle['pips_line']}"
        )
        try:
            import io as _io
            await bot.send_photo(
                chat_id=uid,
                photo=_io.BytesIO(png_bytes),
                caption=cap,
                parse_mode="HTML",
            )
        except Exception:
            await render(q, "⚠️ تعذر إرسال صورة الشارت حالياً.", kb.user_nav([[kb.btn("↩️ الإشارات", "u:signals")]]), bot)

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


def _safe_float(v):
    try:
        val = float(v)
        return val if val > 0 else None
    except (TypeError, ValueError):
        return None


def build_signal_chart_bundle(sig: dict, force_proof: bool = False) -> dict:
    """Fetch fresh real OHLC candles, compute REAL live/banked pips, and render PNG chart."""
    from fysb.engines import market_data
    from fysb.ui import charts
    sym = str(sig.get("symbol") or "XAUUSD")
    tf = str(sig.get("timeframe") or "H1")
    row = _market_row(sym)
    prov_sym = (row.get("provider_symbol") if row and row.get("provider_symbol") else sym)
    api_key = config.market_api_key()
    candles: list[dict] = []
    tried: list[str] = []
    for candidate_tf in (tf, "M15", "H1"):
        interval = market_data.parse_interval(candidate_tf)
        if interval in tried:
            continue
        tried.append(interval)
        try:
            payload = market_data.fetch_candles(prov_sym, interval, api_key=api_key, outputsize=72)
            vals = list((payload or {}).get("values") or [])
            if len(vals) >= 5:
                candles = vals
                break
        except Exception as exc:
            log.warning("chart fetch_candles failed sym=%s interval=%s: %s", prov_sym, interval, exc)
    metrics = charts.live_signal_metrics(sig, candles, row=row)
    st = str(sig.get("status") or "").upper()
    is_proof = force_proof or st in ("TP1_HIT", "TP2_HIT", "TP3_HIT", "SL_HIT", "CLOSED")
    png = charts.render_proof_chart(sig, candles, row=row) if is_proof else charts.render_signal_chart(sig, candles, row=row)
    px_txt = f" · السعر الآن: <code>{instruments.fmt_price(sym, metrics['live_px'], row)}</code>" if metrics["live_px"] > 0 else ""
    res_label = "الربح/النتيجة اللحظية" if metrics["is_floating"] else "النتيجة المحققة"
    pips_line = f"▎🎯 {res_label}: <b>{metrics['display_pips']:+.1f} pips</b> ({esc(st)}){px_txt}"
    return {
        "png": png,
        "is_proof": is_proof,
        "metrics": metrics,
        "pips": metrics["display_pips"],
        "live_px": metrics["live_px"],
        "is_floating": metrics["is_floating"],
        "pips_line": pips_line,
    }


def _build_signal_chart_png(sig: dict, force_proof: bool = False) -> tuple[bytes | None, bool]:
    """Backward-compatible wrapper returning (png_bytes, is_proof)."""
    b = build_signal_chart_bundle(sig, force_proof=force_proof)
    return b["png"], b["is_proof"]


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
    """(v6.20.1) Consolidated single-pass dashboard counters (3 queries instead of 5)."""
    day = db.day_start_utc()
    with db.conn() as c:
        srow = c.execute(
            "SELECT COUNT(*) total, "
            "COALESCE(SUM(CASE WHEN status IN ('ACTIVE','RUNNING','TP1_HIT','TP2_HIT') AND (closed_at IS NULL OR closed_at='') THEN 1 ELSE 0 END),0) active, "
            "COALESCE(SUM(CASE WHEN status='TP3_HIT' OR (status IN ('TP1_HIT','TP2_HIT') AND closed_at!='') THEN 1 ELSE 0 END),0) wins FROM signals"
        ).fetchone()
        delivered = c.execute("SELECT COUNT(*) n FROM signal_deliveries WHERE user_id=?", (uid,)).fetchone()["n"]
        anl_used = c.execute("SELECT COUNT(*) n FROM analyses WHERE user_id=? AND created_at>=?",
                             (uid, day)).fetchone()["n"]
    return int(srow["total"] or 0), int(srow["active"] or 0), int(srow["wins"] or 0), delivered, anl_used


def _dashboard_snapshot(uid: int, first_mkt: str) -> dict:
    """(v6.20.1) Full user dashboard data on ONE single DB connection (replaces 14 sequential RTs!)."""
    day = db.day_start_utc()
    with db.conn() as c:
        plan = subs.plan_for(uid, conn_obj=c)
        srow = c.execute(
            "SELECT COUNT(*) total, "
            "COALESCE(SUM(CASE WHEN status IN ('ACTIVE','RUNNING','TP1_HIT','TP2_HIT') AND (closed_at IS NULL OR closed_at='') THEN 1 ELSE 0 END),0) active, "
            "COALESCE(SUM(CASE WHEN status='TP3_HIT' OR (status IN ('TP1_HIT','TP2_HIT') AND closed_at!='') THEN 1 ELSE 0 END),0) wins FROM signals"
        ).fetchone()
        drow = c.execute(
            "SELECT COUNT(*) total, COALESCE(SUM(CASE WHEN delivered_at>=? THEN 1 ELSE 0 END),0) today "
            "FROM signal_deliveries WHERE user_id=?",
            (day, uid),
        ).fetchone()
        anl_used = c.execute("SELECT COUNT(*) n FROM analyses WHERE user_id=? AND created_at>=?",
                             (uid, day)).fetchone()["n"]
        mrow = c.execute("SELECT * FROM market_catalog WHERE symbol=?", (first_mkt,)).fetchone()
    used_today = int(drow["today"] or 0) if drow else 0
    delivered = int(drow["total"] or 0) if drow else 0
    quota = (max(int(plan["max_signals_per_day"] or 5), 5)
             if (plan and plan["code"] != "FREE")
             else max(1, db.get_setting_int("free_daily_signals", 2)))
    state = {"plan": plan["code"] if plan else "FREE", "quota": quota,
             "used": used_today, "remaining": max(0, quota - used_today), "day": day}
    return {
        "plan": plan,
        "state": state,
        "counts": (int(srow["total"] or 0), int(srow["active"] or 0), int(srow["wins"] or 0), delivered, anl_used),
        "market_row": dict(mrow) if mrow else None,
        "trading_mode": db.get_setting("trading_mode", "SIGNALS_ONLY"),
    }


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
                             AND (closed_at IS NULL OR closed_at='')
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


def _user_has_delivered_signal(uid: int, sid: int) -> bool:
    """(v6.23.0 thread-offload helper) True iff `sid` was actually delivered to `uid`."""
    with db.conn() as c:
        r = c.execute(
            "SELECT 1 FROM signal_deliveries WHERE user_id=? AND signal_id=? LIMIT 1",
            (int(uid), int(sid)),
        ).fetchone()
    return r is not None


def _open_signals_rows(uid: int | None = None):
    """(v6.11.0 / v6.23.0) latest open signals for the demo picker — scoped to
    signals actually delivered to `uid` (admin sees all open signals)."""
    with db.conn() as c:
        if uid is not None and int(uid) != config.ADMIN_ID:
            return c.execute(
                """SELECT s.*, (SELECT COUNT(*) FROM signals s2 WHERE s2.id <= s.id) AS seq
                   FROM signals s
                   JOIN signal_deliveries d ON d.signal_id=s.id
                   WHERE d.user_id=?
                     AND s.status IN ('ACTIVE','RUNNING','TP1_HIT')
                     AND (s.closed_at IS NULL OR s.closed_at='')
                   ORDER BY s.id DESC LIMIT 6""",
                (int(uid),),
            ).fetchall()
        return c.execute("""SELECT *, ROW_NUMBER() OVER (ORDER BY id) AS seq
                            FROM signals WHERE status IN ('ACTIVE','RUNNING','TP1_HIT')
                            AND (closed_at IS NULL OR closed_at='')
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


def _tg_or_web_url(raw: str) -> str | None:
    """Convert a Telegram handle (@name), t.me link, or https URL into a safe
    Telegram InlineKeyboardButton URL; return None if not a valid URL target."""
    import re
    s = (raw or "").strip()
    if not s or any(ch.isspace() for ch in s):
        return None
    low = s.lower()
    if low.startswith("https://") or low.startswith("http://"):
        return s
    if low.startswith("t.me/") or low.startswith("telegram.me/"):
        return "https://" + s
    handle = s.lstrip("@")
    if re.fullmatch(r"[A-Za-z][A-Za-z0-9_]{3,63}", handle):
        return f"https://t.me/{handle}"
    return None


def _support_contact_rows(cfg: dict) -> list[list]:
    """Build luxury colored 1-tap contact buttons (Telegram / WhatsApp / Copy /
    Channel / Group) instead of dumping raw usernames & numbers as plain text."""
    import re
    rows: list[list] = []

    username = (cfg.get("username") or "").strip()
    if username:
        tg_url = _tg_or_web_url(username)
        if tg_url:
            rows.append([kb.url_btn("✈️ تواصل مباشر عبر تلغرام", tg_url, style="primary")])
        else:
            rows.append([kb.copy_btn("✈️ نسخ معرّف الدعم", f"@{username.lstrip('@')}", style="primary")])

    phone = (cfg.get("phone") or "").strip()
    if phone:
        digits = re.sub(r"[^\d]", "", phone)
        if 7 <= len(digits) <= 15:
            rows.append([
                kb.url_btn("💬 تواصل عبر واتساب", f"https://wa.me/{digits}", style="success"),
                kb.copy_btn("📋 نسخ الرقم", phone, style="primary"),
            ])
        else:
            rows.append([kb.copy_btn("📞 نسخ رقم التواصل", phone, style="primary")])

    comm_row: list = []
    channel = (cfg.get("channel") or "").strip()
    if channel:
        ch_url = _tg_or_web_url(channel)
        comm_row.append(
            kb.url_btn("📢 القناة الرسمية", ch_url, style="primary")
            if ch_url else kb.copy_btn("📢 نسخ القناة", channel, style="primary")
        )
    group_chat = (cfg.get("group_chat") or "").strip()
    if group_chat:
        gr_url = _tg_or_web_url(group_chat)
        comm_row.append(
            kb.url_btn("👥 مجموعة المجتمع", gr_url, style="success")
            if gr_url else kb.copy_btn("👥 نسخ المجموعة", group_chat, style="success")
        )
    if comm_row:
        rows.append(comm_row)

    return rows


async def _handle_support_page(q, uid: int, context, bot):
    context.user_data["awaiting_ticket"] = True
    cfg = await afn.run(support_service.config_row) or {}
    contact_rows = _support_contact_rows(cfg)
    has_contacts = bool(contact_rows)

    body_lines = [
        "🎧 <b><u>مركز الدعم الفني والتواصل المباشر</u></b>",
        "━━━━━━━━━━━━━━━━━━",
        "<blockquote>",
    ]
    if has_contacts:
        body_lines.append("▎👑 <b>التواصل الفوري:</b>\nاختر وسيلة التواصل المناسبة لك مباشرة من الأزرار بالأسفل بضغطة واحدة.\n")
    body_lines.append(
        "▎🎫 <b>فتح تذكرة دعم داخل البوت:</b>\n"
        "أرسل رسالتك هنا كنص واحد، ويمكنك إرفاق صورة بعدها مباشرة وسيرد عليك فريق الدعم فوراً."
    )
    body_lines.append("</blockquote>")

    mine = await afn.run(support_service.user_tickets, uid)
    rows_kb = list(contact_rows)
    for t in mine:
        rows_kb.append([kb.btn(f"🎫 تذكرة #{t['id']} · {esc(t['subject'])} · {esc(t['status'])}", f"u:ticket:{t['id']}")])
    rows_kb.append([kb.btn("❓ مركز المساعدة", "u:help", style="primary")])
    await render(q, "\n".join(body_lines), kb.user_nav(rows_kb), bot)
