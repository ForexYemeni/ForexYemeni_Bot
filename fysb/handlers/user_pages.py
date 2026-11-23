"""User-facing pages — luxury Arabic UI, every tile functional, quota enforced."""
import asyncio
from datetime import timedelta

from telegram import InlineKeyboardButton, Update
from telegram.ext import ContextTypes

from fysb import config, db, instruments
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
    from fysb.handlers.user_pages import subs as _subs  # already imported
    plan = _subs.plan_for(uid)
    with db.conn() as c:
        plans = c.execute("SELECT * FROM plans WHERE enabled=1 AND price>0 ORDER BY price").fetchall()
    body = (f"{ICON['lock']} <b><u>محتوى ملكي — يتطلب باقة أعلى</u></b>\n"
            f"<blockquote>"
            f"▎⛔ {esc(why)}.\n"
            f"▎💎 باقتك الحالية: <b>{esc(plan['title'])}</b>\n"
            f"▎✨ رقّ باقتك لفتح أسواق وأطر ومنهجيات أكثر وحدوداً يومية أعلى.\n"
            f"</blockquote>")
    rows_kb = [[kb.btn(f"💎 {esc(p['title'])} · {p['price']:g} {p['currency']}", f"u:planview:{p['code']}")] for p in plans]
    rows_kb.append([kb.btn("↩️ رجوع", "u:preferences")])
    await render(q, body, kb.user_nav(rows_kb), bot)


async def user_page(q, action: str, uid: int, context) -> None:
    bot = context.bot
    users_service.touch(uid)
    u = users_service.get_user(uid)
    if not u:
        await render(q, "لم يتم العثور على حسابك. أرسل /start أولاً.", kb.user_menu(), bot)
        return

    # ---------- Home / profile ----------
    if action == "home":
        await render(q, f"👑 <b>{config.BRAND}</b>\nالقائمة الرئيسية:", kb.user_menu(), bot)

    elif action == "profile":
        profile = users_service.profile_summary(uid)
        await render(q, render_user_card(u, profile),
                     kb.user_nav([
                         [kb.btn("🎛 تخصيص الإشارات", "u:preferences"), kb.btn("💎 الاشتراك", "u:plans")],
                         [kb.btn("🧾 مدفوعاتي", "u:payments"), kb.btn("🎧 الدعم", "u:support")],
                     ]), bot)

    elif action == "dashboard":
        p = subs.plan_for(uid)
        state = subs.quota_state(uid)
        with db.conn() as c:
            total = c.execute("SELECT COUNT(*) n FROM signals").fetchone()["n"]
            active = c.execute("SELECT COUNT(*) n FROM signals WHERE status IN ('ACTIVE','RUNNING','TP1_HIT','TP2_HIT')").fetchone()["n"]
            wins = c.execute("SELECT COUNT(*) n FROM signals WHERE status='TP3_HIT'").fetchone()["n"]
            delivered = c.execute("SELECT COUNT(*) n FROM signal_deliveries WHERE user_id=?", (uid,)).fetchone()["n"]
            anl_used = c.execute("SELECT COUNT(*) n FROM analyses WHERE user_id=? AND created_at>=?",
                                 (uid, db.day_start_utc())).fetchone()["n"]
        plan_title = esc(p["title"] if p else "Free")
        anl_quota = int(p["max_analyses_per_day"] or 0) if p else 0
        mrow = _market_row(u["market"])
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
                f"🛡 وضع المنصة: <b>{esc(db.get_setting('trading_mode', 'SIGNALS_ONLY'))}</b>\n"
                f"🌍 أفضلك ({esc(u['market'])}): <b>{mstate_txt}</b>\n"
                f"📈 إجمالي الإشارات المسجلة: <b>{total}</b> (نشطة: {active} · مكتملة: {wins})")
        await render(q, body, kb.user_nav([
            [kb.btn("📡 فتح الإشارات", "u:signals"), kb.btn("🔬 تحليل جديد", "u:analytics")],
            [kb.btn("🎛 تخصيص التنبيهات", "u:preferences")],
            [kb.btn("💎 الباقات", "u:plans"), kb.btn("📈 إحصائياتي", "u:my_stats")],
        ]), bot)

    # ---------- Analytics (composite institutional engine, plan-gated) ----------
    elif action == "analytics":
        from fysb.services import analytics as anl
        if not anl.analytics_enabled():
            await render(q, "🔬 قسم التحليلات معطل حالياً من الإدارة.", kb.user_nav(), bot)
            return
        state = anl.quota_state(uid)
        plan = subs.plan_for(uid)
        p_markets = [x.strip().upper() for x in (plan["markets"] or "").split(",") if x.strip()]
        with db.conn() as c:
            rows = c.execute("SELECT * FROM market_catalog WHERE enabled=1 AND verified=1 ORDER BY symbol").fetchall()
        if not rows:
            # LEGACY-DEAD CATALOG FALLBACK (picker-empty bug 2026-11-22): the
            # picker must never dead-end — offer the plan's own markets (the
            # catalog self-heals on the next boot anyway).
            from fysb.db import SEED_MARKETS
            titles = {m: t for m, t, *_rest in SEED_MARKETS}
            rows = [{"symbol": m, "title": titles.get(m, m), "enabled": 1, "verified": 1}
                    for m in p_markets]
        allowed_rows = [r for r in rows if r["symbol"].upper() in p_markets]
        locked_rows = [r for r in rows if r["symbol"].upper() not in p_markets]
        if not allowed_rows and not locked_rows:
            await render(q, "لا توجد أسواق مفعلة بعد — تواصل مع الإدارة.", kb.user_nav(), bot)
            return
        # markets available for the user's plan (luxury toggle list)
        mkt_lines = "\n".join(f"▎✅ <b>{esc(r['symbol'])}</b> — {esc(r['title'])}" for r in allowed_rows) or "▎لا أسواق في باقتك الحالية"
        lock_lines = "\n".join(f"▎🔒 {esc(r['symbol'])} — {esc(r['title'])}" for r in locked_rows[:4])
        body = (f"🔬 <b><u>التحليلات الاحترافية المؤسسية</u></b>\n"
                f"<blockquote>"
                f"▎حصة اليوم: <b>{state['used']}/{state['quota']}</b> {bar(state['used'], state['quota'])}\n"
                f"▎💎 باقتك: <b>{esc(plan['title'])}</b>\n"
                f"</blockquote>\n"
                f"{ICON['globe']} <b>أسواق باقتك — اختر السوق للتحليل:</b>\n"
                f"<blockquote>{mkt_lines}</blockquote>")
        if lock_lines:
            body += (f"\n{ICON['lock']} <b>أسواق تنتظرك في الباقات الأعلى:</b>\n"
                     f"<blockquote>{lock_lines}</blockquote>")
        mkt_rows = [[kb.btn(("🔬 " if r["symbol"].upper() in p_markets else "🔒 ") + esc(r["symbol"]),
                            f"u:anlsym:{r['symbol']}" if r["symbol"].upper() in p_markets else "u:upgrade")
                     for r in rows[i:i + 3]] for i in range(0, len(rows), 3)]
        await render(q, body, kb.user_nav(mkt_rows), bot)

    elif action == "upgrade":
        with db.conn() as c:
            plans = c.execute("SELECT * FROM plans WHERE enabled=1 AND price>0 ORDER BY price").fetchall()
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
        plan = subs.plan_for(uid)
        p_markets = [x.strip().upper() for x in (plan["markets"] or "").split(",") if x.strip()]
        if symbol.upper() not in p_markets:
            await _upsell(q, uid, bot, "هذا السوق غير مشمول في باقتك")
            return
        draft = {"symbol": symbol}
        context.user_data["anl_draft"] = draft
        p_tfs = [x.strip().upper() for x in (plan["timeframes"] or "").split(",") if x.strip()]
        with db.conn() as c:
            enabled = {r["code"] for r in c.execute("SELECT code FROM timeframe_catalog WHERE enabled=1 AND verified=1")}
        # PICKER CAN NEVER BE EMPTY (user bug report 2026-11-22): plan codes are
        # the access authority — if the legacy catalog filter empties them, we
        # fall back to the plan's own timeframes. Boot self-heal fixes the root.
        usable = [t for t in p_tfs if t in enabled] or p_tfs
        if not usable:
            await _upsell(q, uid, bot, "باقتك الحالية لا تتضمن أطراً زمنية — رقّ باقتك للمتابعة")
            return
        body = (f"🌐 <b>السوق:</b> <b>{esc(symbol)}</b>\n"
                f"⏱ <b>اختر الإطار الزمني</b> — الأطر المسموحة لباقتك ({esc(plan['title'])}):")
        rows = [[kb.btn(f"⏱ {tf_name(t)}", f"u:anltf:{t}") for t in usable[i:i + 4]] for i in range(0, len(usable), 4)]
        if len(usable) > 1:
            rows.insert(0, [kb.btn("👑 تحليل متعدد الأطباق (الكل)", "u:anltf:ALL")])
        else:
            body += "\n▎إطار واحد متاح لباقتك حالياً — اختره للمتابعة."
        await render(q, body, kb.user_nav(rows), bot)

    elif action.startswith("anltf:"):
        from fysb.services import analytics as anl
        draft = context.user_data.get("anl_draft") or {}
        if not draft.get("symbol"):
            await render(q, "ابدأ من قسم التحليلات أولاً.", kb.user_nav([[kb.btn("🔬 التحليلات", "u:analytics")]]), bot)
            return
        tf = action.split(":", 1)[1]
        plan = subs.plan_for(uid)
        p_tfs = [x.strip().upper() for x in (plan["timeframes"] or "").split(",") if x.strip()]
        if tf.upper() != "ALL" and tf.upper() not in p_tfs:
            await _upsell(q, uid, bot, "هذا الإطار الزمني غير مشمول في باقتك")
            return
        draft["tf"] = tf.upper()
        context.user_data["anl_draft"] = draft
        p_strats = [x.strip().upper() for x in (plan["strategies"] or "").split(",") if x.strip()]
        with db.conn() as c:
            enabled = {r["code"] for r in c.execute("SELECT code FROM strategy_catalog WHERE enabled=1 AND verified=1")}
        # PICKER CAN NEVER BE EMPTY: fall back to the plan's own methodologies
        # (implemented ones first) — a legacy-dead catalog can never blank this page.
        usable = [s for s in p_strats if s in enabled and s != "ALL"]
        if not usable:
            from fysb.db import IMPLEMENTED_STRATEGIES
            usable = [s for s in p_strats if s != "ALL" and s in IMPLEMENTED_STRATEGIES] \
                or [s for s in p_strats if s != "ALL"]
        if not usable:
            await _upsell(q, uid, bot, "باقتك الحالية لا تتضمن منهجيات — رقّ باقتك للمتابعة")
            return
        tf_txt = "تحليل متعدد الأطباق" if draft["tf"] == "ALL" else tf
        body = (f"⏱ <b>الإطار:</b> <b>{esc(tf_txt)}</b>\n"
                f"🧠 <b>اختر المنهجية</b> — الاستراتيجيات المسموحة لباقتك ({esc(plan['title'])}):")
        rows = [[kb.btn(strategy_name(s), f"u:anlstrat:{s}") for s in usable[i:i + 2]]
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
        plan = subs.plan_for(uid)
        p_strats = [x.strip().upper() for x in (plan["strategies"] or "").split(",") if x.strip()]
        if strategy.upper() != "ALL" and strategy.upper() not in p_strats:
            await _upsell(q, uid, bot, "هذه المنهجية غير مشمولة في باقتك")
            return
        draft["strategy"] = strategy.upper()
        state = anl.quota_state(uid)
        tf_txt = "تحليل متعدد الأطباق (الأدنى ← الأعلى)" if draft["tf"] == "ALL" else tf_name(draft["tf"])
        await render(q, f"🔬 <b>جارٍ توليد التحليل المؤسسي المركب</b>\n▎السوق: <b>{esc(draft['symbol'])}</b> · الإطار: <b>{esc(tf_txt)}</b>\n▎حصتك قبل التوليد: <b>{state['used']}/{state['quota']}</b>", None)
        res = await asyncio.to_thread(anl.run_analysis, uid, draft["symbol"], draft["tf"],
                                      "", "", draft["strategy"])
        context.user_data.pop("anl_draft", None)
        if not res.get("ok"):
            await render(q, f"⛔ {esc(res.get('error', 'تعذر التوليد'))}",
                         kb.user_nav([[kb.btn("↩️ التحليلات", "u:analytics")]]), bot)
            return
        an = res["analysis"]
        q_state = res.get("quota") or anl.quota_state(uid)
        row = _market_row(an["symbol"])
        card_text = render_analysis_card(an, market_row=row)
        card_text += f"\n\n📊 <b>حصتك الآن:</b> <b>{q_state['used']}/{q_state['quota']}</b> {bar(q_state['used'], q_state['quota'])}"
        await render(q, card_text,
                     kb.user_nav([[kb.btn("🔬 تحليل جديد", "u:analytics"),
                                   kb.btn("📡 الإشارات", "u:signals")]]), bot)

    # ---------- Signals (quota enforced server-side) ----------
    elif action == "signals":
        state = subs.quota_state(uid)
        plan = subs.plan_for(uid)
        markets = [instruments.normalize(x) for x in (plan["markets"] or "XAUUSD").split(",") if x.strip()]
        timeframes = [x.strip() for x in (plan["timeframes"] or "M15").split(",") if x.strip()]
        strategies = [x.strip() for x in (plan["strategies"] or "ICT").split(",") if x.strip()]
        min_score = int(plan["min_score"] or 60)
        marks_m = ",".join("?" for _ in markets)
        marks_t = ",".join("?" for _ in timeframes)
        marks_s = ",".join("?" for _ in strategies)
        args = markets + timeframes + strategies + [min_score]
        with db.conn() as c:
            rows = c.execute(f"""SELECT * FROM signals WHERE status IN ('ACTIVE','RUNNING','TP1_HIT','TP2_HIT')
                                 AND REPLACE(symbol,'/','') IN ({marks_m}) AND timeframe IN ({marks_t})
                                 AND strategy IN ({marks_s}) AND score>=?
                                 ORDER BY id DESC LIMIT 12""", args).fetchall()
        # Which of these are still viewable under the remaining quota?
        fresh = [r for r in rows if _already_delivered(uid, r["id"]) or state["remaining"] > 0]
        lines = [f"💎 <b>الباقة:</b> {esc(plan['title'])}",
                 f"📊 الحد اليومي: <b>{state['used']}/{state['quota']}</b> (المتبقي: <b>{state['remaining']}</b>)",
                 "اختر إشارة لعرض بطاقة الدخول الكاملة:"]
        rows_kb = [[kb.btn(f"{'🟢' if r['direction'] == 'BUY' else '🔴'} #{r['id']} {esc(r['symbol'])} {r['direction']} · {r['score']}/100",
                           f"u:signalview:{r['id']}")] for r in fresh[:8]]
        if not rows_kb:
            rows_kb = [[kb.btn("🔄 تحديث الإشارات", "u:signals")]]
        await render(q, "\n".join(lines), kb.user_nav(rows_kb), bot)

    elif action.startswith("signalview:"):
        sid = _safe_int(action.split(":", 1)[1])
        if sid is None:
            await render(q, "معرّف غير صالح.", kb.user_nav(), bot)
            return
        with db.conn() as c:
            sig = c.execute("SELECT * FROM signals WHERE id=?", (sid,)).fetchone()
        if not sig or sig["status"] in ("CANCELLED",):
            await render(q, "الإشارة غير متاحة.", kb.user_nav(), bot)
            return
        # Server-side quota consumption (anti-bypass: re-check on every callback)
        consumption = subs.consume_on_view(uid, sid)
        if not consumption["ok"] and not consumption.get("already"):
            plan = subs.plan_for(uid)
            await render(q,
                         f"⛔ <b>انتهى حد الإشارات اليومي</b>\n"
                         f"الباقة: {esc(plan['title'])} · المستهلك: {consumption['used']}/{consumption['quota']}\n"
                         f"الحد يتجدد يومياً على السيرفر ولا يمكن تجاوزه من الجهاز.",
                         kb.user_nav([[kb.btn("💎 ترقية الباقة", "u:plans"), kb.btn("🔄 لاحقاً", "u:signals")]]), bot)
            return
        row = _market_row(sig["symbol"])
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
        personal = user_trade.personal_block(dict(sig), uid)
        if personal:
            card_text += "\n\n<blockquote>" + personal + "</blockquote>"
        await render(q, card_text,
                     kb.user_nav([[kb.btn("🧪 متابعة تجريبية", f"u:demoopen:{sid}"),
                                   kb.btn("↩️ كل الإشارات", "u:signals")]]), bot)

    # ---------- Preferences ----------
    elif action == "preferences":
        plan = subs.plan_for(uid)
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
        plan = subs.plan_for(uid)
        allowed = [x.strip().upper() for x in (plan["markets"] or "").split(",") if x.strip()]
        with db.conn() as c:
            rows = c.execute("SELECT * FROM market_catalog WHERE enabled=1 AND verified=1").fetchall()
        available = [r["symbol"] for r in rows if r["symbol"].upper() in allowed] or allowed
        rows_kb = [[kb.btn("🌍 الكل (حسب باقتك)", "u:setmarket:ALL")]]
        rows_kb += [[kb.btn(("✅ " if s == u["market"] else "") + s, f"u:setmarket:{s}") for s in available[i:i + 3]]
                    for i in range(0, len(available), 3)]
        rows_kb.append([kb.btn("↩️ التفضيلات", "u:preferences")])
        await render(q, "🌐 <b>اختر السوق المفضل</b>\nمقيد بالأسواق المتاحة في باقتك والموثقة لدى المزود:",
                     kb.user_nav(rows_kb), bot)

    elif action.startswith("setmarket:"):
        symbol = action.split(":", 1)[1]
        plan = subs.plan_for(uid)
        allowed = [x.strip().upper() for x in (plan["markets"] or "").split(",") if x.strip()]
        if symbol == "ALL":
            users_service.set_preference(uid, "market", "", {"market"})
            await render(q, "✅ سيصلك <b>كل ما تسمح به باقتك</b> من الأسواق مباشرة.", kb.user_nav([[kb.btn("↩️ التفضيلات", "u:preferences")]]), bot)
            return
        if symbol.upper() not in allowed:
            await _upsell(q, uid, bot, "هذا السوق غير متاح في باقتك")
            return
        users_service.set_preference(uid, "market", symbol, {"market"})
        await render(q, f"✅ تم حفظ السوق المفضل: <b>{esc(symbol)}</b>", kb.user_nav([[kb.btn("↩️ التفضيلات", "u:preferences")]]), bot)

    elif action == "timeframes":
        plan = subs.plan_for(uid)
        allowed = [x.strip().upper() for x in (plan["timeframes"] or "").split(",") if x.strip()]
        with db.conn() as c:
            enabled = {r["code"] for r in c.execute("SELECT code FROM timeframe_catalog WHERE enabled=1 AND verified=1")}
        usable = [t for t in allowed if t in enabled] or allowed
        rows_kb = [[kb.btn("🌍 الكل (حسب باقتك)", "u:settf:ALL")]]
        rows_kb += [[kb.btn(("✅ " if t == (u["timeframe"] or "").upper() else "") + f"{t} — {tf_name(t)}", f"u:settf:{t}") for t in usable[i:i + 3]]
                    for i in range(0, len(usable), 3)]
        rows_kb.append([kb.btn("↩️ التفضيلات", "u:preferences")])
        await render(q, "⏱ <b>اختر الإطار الزمني المفضل</b>", kb.user_nav(rows_kb), bot)

    elif action.startswith("settf:"):
        tf = action.split(":", 1)[1]
        plan = subs.plan_for(uid)
        allowed = [x.strip().upper() for x in (plan["timeframes"] or "").split(",") if x.strip()]
        if tf == "ALL":
            users_service.set_preference(uid, "timeframe", "", {"timeframe"})
            await render(q, "✅ سيصلك <b>كل ما تسمح به باقتك</b> من الأطر الزمنية.", kb.user_nav([[kb.btn("↩️ التفضيلات", "u:preferences")]]), bot)
            return
        if tf.upper() not in allowed:
            await _upsell(q, uid, bot, "هذا الإطار غير متاح في باقتك")
            return
        users_service.set_preference(uid, "timeframe", tf, {"timeframe"})
        await render(q, f"✅ تم حفظ الإطار: <b>{esc(tf_name(tf))}</b>", kb.user_nav([[kb.btn("↩️ التفضيلات", "u:preferences")]]), bot)

    elif action == "strategies":
        plan = subs.plan_for(uid)
        allowed = [x.strip().upper() for x in (plan["strategies"] or "").split(",") if x.strip()]
        with db.conn() as c:
            enabled = {r["code"] for r in c.execute("SELECT code FROM strategy_catalog WHERE enabled=1 AND verified=1")}
        usable = [s for s in allowed if s in enabled and s != "ALL"] or [s for s in allowed if s != "ALL"]
        rows_kb = [[kb.btn("👑 الكل (حسب باقتك)", "u:setstrategy:ALL")]]
        rows_kb += [[kb.btn(("✅ " if s == (u["strategy"] or "").upper() else "") + strategy_name(s), f"u:setstrategy:{s}") for s in usable[i:i + 2]]
                    for i in range(0, len(usable), 2)]
        rows_kb.append([kb.btn("↩️ التفضيلات", "u:preferences")])
        await render(q, "🧠 <b>اختر الاستراتيجية المفضلة</b>", kb.user_nav(rows_kb), bot)

    elif action.startswith("setstrategy:"):
        st = action.split(":", 1)[1]
        plan = subs.plan_for(uid)
        allowed = [x.strip().upper() for x in (plan["strategies"] or "").split(",") if x.strip()]
        if st == "ALL":
            users_service.set_preference(uid, "strategy", "", {"strategy"})
            await render(q, "✅ سيصلك <b>كل ما تسمح به باقتك</b> من المنهجيات.", kb.user_nav([[kb.btn("↩️ التفضيلات", "u:preferences")]]), bot)
            return
        if st.upper() not in allowed:
            await _upsell(q, uid, bot, "هذه الاستراتيجية غير متاحة في باقتك")
            return
        users_service.set_preference(uid, "strategy", st, {"strategy"})
        await render(q, f"✅ تم حفظ الاستراتيجية: <b>{esc(strategy_name(st))}</b>", kb.user_nav([[kb.btn("↩️ التفضيلات", "u:preferences")]]), bot)

    elif action == "quality":
        plan = subs.plan_for(uid)
        floor = int(plan["min_score"] or 60)
        options = [n for n in (60, 65, 70, 75, 80, 85, 90) if n >= floor] or [floor]
        rows_kb = [[kb.btn(f"{n} / 100", f"u:setquality:{n}")] for n in options]
        rows_kb.append([kb.btn("↩️ التفضيلات", "u:preferences")])
        await render(q, f"⭐ <b>الحد الأدنى لجودة الإشارة</b>\n(الأدنى المسموح بباقتك: {floor})", kb.user_nav(rows_kb), bot)

    elif action.startswith("setquality:"):
        n = _safe_int(action.split(":", 1)[1])
        plan = subs.plan_for(uid)
        floor = int(plan["min_score"] or 60)
        if n is None or n < floor or n > 100:
            await render(q, "⛔ قيمة أقل من حد باقتك.", kb.user_nav(), bot)
            return
        users_service.set_preference(uid, "min_score", n, {"min_score"})
        await render(q, f"✅ الجودة الدنيا الآن: <b>{n}</b>", kb.user_nav([[kb.btn("↩️ التفضيلات", "u:preferences")]]), bot)

    elif action == "notifications":
        new = 0 if u["notifications"] else 1
        users_service.set_notifications(uid, bool(new))
        await render(q, f"🔔 الإشعارات الآن <b>{'مفعلة' if new else 'متوقفة'}</b>.",
                     kb.user_nav([[kb.btn("↩️ التفضيلات", "u:preferences")]]), bot)

    # ---------- Royal referrals (invite → open → subscribe) ----------
    elif action == "referrals":
        from urllib.parse import quote as _urlquote
        from fysb.services import referrals as ref
        st = ref.user_stats(uid)
        share_link = ("https://t.me/share/url?url=" + _urlquote(st["link"])
                      + "&text=" + _urlquote("👑 انضم إلى FOREXYEMENI — تحليلات وإشارات تداول ملكية لحظية عبر @ForexYemeni1_BOT"))
        body = [
            "🤝 <b><u>دعوة الأصدقاء — بطاقة إحالات ملكية</u></b>",
            "",
            "<blockquote>"
            "▎🔗 رابط الدعوة الخاص بك — انسخه وشاركه مع أصدقائك:\n"
            f"▎<code>{esc(st['link'])}</code>"
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
            [InlineKeyboardButton("🟢 📤 شارك رابط الدعوة", url=share_link)],
            [kb.btn("🟡 💎 الباقات", "u:plans")],
        ]
        await render(q, "\n".join(body), kb.user_nav(rows_kb), bot)

    # ---------- Plans & payments ----------
    elif action == "plans":
        with db.conn() as c:
            rows = c.execute("SELECT * FROM plans WHERE enabled=1 ORDER BY price").fetchall()
        current = subs.plan_for(uid)
        rows_kb = [[kb.btn(f"💎 {esc(r['title'])} · {r['price']:g} {r['currency']} · {'مجاني' if not r['duration_days'] else str(r['duration_days']) + ' يوم'}",
                           f"u:planview:{r['code']}")] for r in rows]
        rows_kb.append([kb.btn("🧾 مدفوعاتي", "u:payments")])
        await render(q, f"💎 <b>الاشتراكات المميزة</b>\nباقتك الحالية: <b>{esc(current['title'])}</b>\nاختر باقة لعرض مزاياها:",
                     kb.user_nav(rows_kb), bot)

    elif action.startswith("planview:"):
        code = action.split(":", 1)[1]
        with db.conn() as c:
            p = c.execute("SELECT * FROM plans WHERE code=? AND enabled=1", (code,)).fetchone()
        if not p:
            await render(q, "هذه الباقة غير متاحة حالياً.", kb.user_nav([[kb.btn("↩️ الباقات", "u:plans")]]), bot)
            return
        methods = payments_service.list_methods(enabled_only=True)
        rows_kb = []
        if code != "FREE":
            if methods:
                for m in methods:
                    rows_kb.append([kb.btn(f"🧾 اشتراك عبر {esc(m['title'])}", f"u:requestplan:{code}:{m['id']}")])
            else:
                rows_kb.append([kb.btn("🧾 طلب اشتراك (دفع يدوي)", f"u:requestplan:{code}:0")])
        rows_kb.append([kb.btn("↩️ كل الباقات", "u:plans")])
        await render(q, render_plan_card(dict(p)), kb.user_nav(rows_kb), bot)

    elif action.startswith("requestplan:"):
        try:
            _, code, mid = action.split(":", 2)
            mid = int(mid)
        except ValueError:
            await render(q, "طلب غير صالح.", kb.user_nav(), bot)
            return
        with db.conn() as c:
            p = c.execute("SELECT * FROM plans WHERE code=? AND enabled=1", (code,)).fetchone()
        if not p:
            await render(q, "الباقة غير متاحة.", kb.user_nav(), bot)
            return
        method = payments_service.get_method(mid) if mid else None
        pay_req = payments_service.create_request(uid, code, p["price"], method["title"] if method else "MANUAL")
        context.user_data["awaiting_receipt"] = pay_req
        body = [f"🧾 <b>طلب اشتراك #{pay_req}</b>",
                f"💎 الباقة: <b>{esc(p['title'])}</b>",
                f"💰 المبلغ: <b>{p['price']:g} {esc(p['currency'])}</b>"]
        if method:
            body.append("\n💳 <b>وسيلة الدفع:</b> " + esc(method["title"]))
            if method["country"]:
                body.append("🌍 الدولة: " + esc(method["country"]))
            if method["account_ref"]:
                body.append("🔢 بيانات الحساب: <code>" + esc(method["account_ref"]) + "</code>")
            if method["instructions"]:
                body.append("📝 التعليمات: " + esc(method["instructions"]))
        body.append("\n📸 <b>أرسل صورة الإيصال هنا</b> — يبقى الاشتراك معلقاً حتى مراجعة الإدارة.")
        await render(q, "\n".join(body), kb.user_nav([[kb.btn("🧾 مدفوعاتي", "u:payments")]]), bot)

    elif action == "payments":
        with db.conn() as c:
            rows = c.execute("SELECT * FROM payments WHERE user_id=? ORDER BY id DESC LIMIT 8", (uid,)).fetchall()
        rows_kb = [[kb.btn(f"🧾 #{r['id']} · {esc(r['plan_code'])} · {r['amount']:g} · {esc(r['status'])}",
                           f"u:paymentview:{r['id']}")] for r in rows]
        if not rows_kb:
            rows_kb = [[kb.btn("💎 استعراض الباقات", "u:plans")]]
        await render(q, "🧾 <b>محفظة المدفوعات</b>", kb.user_nav(rows_kb), bot)

    elif action.startswith("paymentview:"):
        pid = _safe_int(action.split(":", 1)[1])
        r = payments_service.get_request(pid) if pid else None
        if not r or r["user_id"] != uid:
            await render(q, "طلب الدفع غير موجود.", kb.user_nav(), bot)
            return
        with db.conn() as c:
            plan_title = c.execute("SELECT title FROM plans WHERE code=?", (r["plan_code"],)).fetchone()
        from fysb.ui.cards import render_payment_card
        await render(q, render_payment_card(r, plan_title["title"] if plan_title else ""),
                     kb.user_nav([[kb.btn("↩️ مدفوعاتي", "u:payments"), kb.btn("💎 الباقات", "u:plans")]]), bot)

    # ---------- Demo trading ----------
    elif action == "demo":
        from fysb.engines import demo as demo_engine
        api_key = config.market_api_key()
        overview = demo_engine.account_overview(uid, api_key)
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
        s = demo_engine.demo_settings(uid)
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
        demo_engine.set_risk_pct(uid, v)
        await render(q, f"✅ أصبحت مخاطرتك لكل صفقة: <b>{v:g}%</b> — سيُحسب حجم اللوت تلقائياً منه (في وضع اللوت التلقائي).",
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
        demo_engine.set_lot_mode(uid, mode, float(lot) if float(lot) > 0 else None)
        word = "تلقائي حسب المخاطرة ورأس المال" if mode == "auto" else f"ثابت {float(lot):g}"
        await render(q, f"✅ حجم اللوت الآن: <b>{word}</b>", kb.user_nav([[kb.btn("↩️ الإعدادات", "u:demoset")]]), bot)

    elif action == "demoauto":
        from fysb.engines import demo as demo_engine
        s = demo_engine.demo_settings(uid)
        demo_engine.set_auto_trade(uid, not s["auto_trade"])
        state = "مفعل 🤖 — ستنفذ كل إشارة مؤهلة تلقائياً في حسابك بحجمك ومخاطرتك" if not s["auto_trade"] else "متوقف"
        await render(q, f"🤖 التنفيذ التلقائي الآن: <b>{state}</b>.",
                     kb.user_nav([[kb.btn("↩️ الإعدادات", "u:demoset")]]), bot)

    elif action == "demopick":
        with db.conn() as c:
            rows = c.execute("""SELECT * FROM signals WHERE status IN ('ACTIVE','RUNNING','TP1_HIT')
                                ORDER BY id DESC LIMIT 6""").fetchall()
        rows_kb = [[kb.btn(f"#{r['id']} {esc(r['symbol'])} {r['direction']} {r['score']}/100", f"u:demolots:{r['id']}")] for r in rows]
        rows_kb.append([kb.btn("↩️ التجريبي", "u:demo")])
        await render(q, "🚀 <b>فتح صفقة تجريبية</b>\nاختر إشارة لإدخالها محاكاةً (سعر الدخول الحقيقي + سبريد وانزلاق):",
                     kb.user_nav(rows_kb), bot)

    elif action.startswith("demolots:"):
        sid = _safe_int(action.split(":", 1)[1])
        with db.conn() as c:
            sig = c.execute("SELECT * FROM signals WHERE id=?", (sid,)).fetchone()
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
            with db.conn() as c:
                sig = c.execute("SELECT * FROM signals WHERE id=?", (sid,)).fetchone()
            sig_row = dict(sig) if sig else {}
        if not sig_row:
            await render(q, "الإشارة غير متاحة.", kb.user_nav(), bot)
            return
        from fysb.engines import demo as demo_engine
        res = demo_engine.open_position(uid, sig_row["symbol"], sig_row["direction"], lots,
                                        sl=sig_row.get("sl") or 0, tp=sig_row.get("tp1") or 0,
                                        api_key=config.market_api_key())
        if not res.get("ok"):
            await render(q, f"⛔ تعذر فتح الصفقة التجريبية:\n{esc(res.get('error', ''))}", kb.user_nav(), bot)
            return
        sym = sig_row["symbol"]
        await render(q,
                     f"✅ <b>تم فتح صفقة تجريبية #{res['position_id']}</b>\n\n"
                     f"🌐 {esc(sym)} · {esc(sig_row['direction'])} · 📦 {lots:g} lots\n"
                     f"🎯 سعر الدخول (مع السبريد): <code>{instruments.fmt_price(sym, res['entry'], row=_market_row(sym))}</code>\n"
                     f"🕐 وقت السعر: {human_dt(res['price_time'])}\n\n"
                     f"<i>يُغلق تلقائياً عند SL/TP بيانات حقيقية، أو الإغلاق اليدوي من الصفقات المفتوحة.</i>",
                     kb.user_nav([[kb.btn("📂 الصفقات المفتوحة", "u:demoopen_list")]]), bot)

    elif action == "demoopen_list":
        from fysb.engines import demo as demo_engine
        api_key = config.market_api_key()
        demo_engine.auto_close_on_targets(uid, api_key)
        overview = demo_engine.account_overview(uid, api_key)
        if not overview["open"]:
            await render(q, "📂 لا توجد صفقات مفتوحة.", kb.user_nav([[kb.btn("🚀 فتح صفقة", "u:demopick")]]), bot)
            return
        rows_kb = []
        for p in overview["open"]:
            cur = p.get("current")
            cur_txt = instruments.fmt_price(p["symbol"], cur, _market_row(p["symbol"])) if cur else "—"
            rows_kb.append([kb.btn(f"#{p['id']} {esc(p['symbol'])} {p['direction']} {p['lots']:g} · الآن {cur_txt}",
                                   f"u:democlose:{p['id']}")])
        await render(q, "📂 <b>الصفقات المفتوحة</b>\nاضغط على صفقة لإغلاقها بسعر السوق الحالي:",
                     kb.user_nav(rows_kb), bot)

    elif action.startswith("democlose:"):
        pid = _safe_int(action.split(":", 1)[1])
        from fysb.engines import demo as demo_engine
        res = demo_engine.close_position(uid, pid, config.market_api_key())
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
        overview = demo_engine.account_overview(uid)
        lines = ["📜 <b>سجل الصفقات التجريبية</b>"]
        if not overview["closed"]:
            lines.append("لا توجد صفقات مغلقة بعد.")
        for p in overview["closed"]:
            emoji = "📈" if (p["pnl"] or 0) >= 0 else "📉"
            lines.append(f"{emoji} #{p['id']} {esc(p['symbol'])} {p['direction']} · {p['pnl']:+.2f} · {esc(p['reason'])} · {human_dt(p['closed_at'])}")
        await render(q, "\n".join(lines), kb.user_nav(), bot)

    elif action == "demoreset":
        from fysb.engines import demo as demo_engine
        demo_engine.reset_account(uid)
        await render(q, "🔄 <b>تم تصفير الحساب التجريبي</b> وإعادة الرصيد الابتدائي. الصفقات المفتوحة أُغلقت إدارياً.",
                     kb.user_nav([[kb.btn("🧪 التجريبي", "u:demo")]]), bot)

    # ---------- Stats / support / help ----------
    elif action == "my_stats":
        profile = users_service.profile_summary(uid)
        with db.conn() as c:
            wins = c.execute("""SELECT COUNT(*) n FROM signal_deliveries d JOIN signals s ON s.id=d.signal_id
                                WHERE d.user_id=? AND s.status='TP3_HIT'""", (uid,)).fetchone()["n"]
            sls = c.execute("""SELECT COUNT(*) n FROM signal_deliveries d JOIN signals s ON s.id=d.signal_id
                               WHERE d.user_id=? AND s.status='SL_HIT'""", (uid,)).fetchone()["n"]
        body = (f"📈 <b>إحصائياتي</b>\n\n"
                f"📡 إشارات استلمتها: <b>{profile['consumed_total']}</b>\n"
                f"🟢 مكتملة TP3: <b>{wins}</b>\n🔴 ضربت SL: <b>{sls}</b>\n"
                f"🧪 صفقات تجريبية: انظر قسم التداول التجريبي\n")
        await render(q, body, kb.user_nav([[kb.btn("🧪 التداول التجريبي", "u:demo"), kb.btn("📡 الإشارات", "u:signals")]]), bot)

    elif action == "support":
        context.user_data["awaiting_ticket"] = True
        cfg = support_service.config_row()
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
        mine = support_service.user_tickets(uid)
        rows_kb = [[kb.btn(f"🎫 تذكرة #{t['id']} · {esc(t['subject'])} · {esc(t['status'])}", f"u:ticket:{t['id']}")] for t in mine]
        await render(q, "\n".join(body), kb.user_nav(rows_kb), bot)

    elif action.startswith("ticket:"):
        tid = _safe_int(action.split(":", 1)[1])
        t = support_service.get_ticket(tid) if tid else None
        if not t or t["user_id"] != uid:
            await render(q, "التذكرة غير موجودة.", kb.user_nav(), bot)
            return
        msgs = support_service.ticket_messages(tid)
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
        t = support_service.get_ticket(tid) if tid else None
        if t and t["user_id"] == uid:
            support_service.set_status(tid, "OPEN")
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
