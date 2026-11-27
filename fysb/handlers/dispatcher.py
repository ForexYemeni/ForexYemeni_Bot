"""Central callback dispatcher — the proven button-fix pattern, hardened.

Preserved behaviors (from the working V5 build, non-negotiable):
  1. start q.answer() on every callback without waiting for DB/routing
  2. pages render through the living-panel contract (see common.render)
  3. every action logged, every failure caught with a visible reply
  4. admin guard checked SERVER-SIDE in both the dispatcher and each page module

Added hardening:
  * unknown callbacks get an explicit "unknown button" reply (no dead buttons)
  * non-admin tapping admin callbacks is logged as a security probe
  * callback data length validated (Telegram 64-byte limit protection)
  * v6.8.2: user identity = ONE pooled round trip (touch_and_get) — WAN
    PostgreSQL used to cost 3-4 sequential queries per button press
  * v6.8.2: every button logs its total handling time (BUTTON DONE) so the
    owner can prove latency from the log alone
"""
import asyncio
import time

from telegram import Update
from telegram.ext import ContextTypes

from fysb import afn, config, db
from fysb.config import log
from fysb.handlers import admin_channels, admin_core, admin_partners, admin_referrals, admin_system, admin_trading, user_pages
from fysb.handlers import callback_feedback
from fysb.handlers.inputs import CLEAR_ON_NAV
from fysb.services import users as users_service
from fysb.services import audit as audit_log
from fysb.ui import keyboards as kb

BOT_REF = None  # set in post_init for background tasks
ACK_TIMEOUT_SECONDS = 2.0


async def _quiet_answer(q) -> None:
    try:
        await asyncio.wait_for(q.answer(), ACK_TIMEOUT_SECONDS)
    except Exception:
        pass  # stale/timeout answers must not block the action


async def buttons(update: Update, context: ContextTypes.DEFAULT_TYPE):
    # All u:/a:/p:/unknown routes share the same feedback and worker isolation.
    with afn.interactive():
        await _buttons(update, context)


async def _buttons(update: Update, context: ContextTypes.DEFAULT_TYPE):
    global BOT_REF
    q = update.callback_query
    if not q:
        return
    BOT_REF = context.bot
    uid = q.from_user.id
    data = q.data or ""
    t0 = time.monotonic()
    log.info("BUTTON RECEIVED | user_id=%s | data=%s", uid, data[:64])
    # v6.20.1 latency upgrade: acknowledge callback IN PARALLEL with DB/render
    # instead of stalling 150-400ms before even starting work!
    ans_task = asyncio.create_task(_quiet_answer(q))
    feedback = callback_feedback.CallbackFeedback(q, context.bot)
    feedback_token = callback_feedback.bind(feedback)
    feedback.start()

    try:
        # Give the receipt task a scheduling opportunity before route code runs.
        await asyncio.sleep(0)
        if data.startswith("u:"):
            await _route_user(q, context, uid, data[2:])
        elif data.startswith("a:"):
            await _route_admin(q, context, uid, data[2:])
        elif data.startswith("p:"):
            for k in CLEAR_ON_NAV:
                context.user_data.pop(k, None)
            await admin_partners.handle_partner_callback(q, uid, context, context.bot, data[2:])
        else:
            from fysb.services import partners as partners_svc
            active_p = (
                await afn.run(partners_svc.get_partner_by_user, uid)
                if uid != config.ADMIN_ID else None
            )
            if active_p and active_p.get("enabled"):
                await admin_partners.handle_partner_callback(q, uid, context, context.bot, "home")
            else:
                from fysb.handlers.common import render
                await render(
                    q,
                    "زر غير معروف. أرسل /start لتحديث القائمة.",
                    kb.admin_menu() if uid == config.ADMIN_ID else kb.user_menu(),
                    context.bot,
                )
            await afn.run(audit_log.log, uid, "unknown_callback:" + data[:40])
    except Exception as exc:
        log.exception("BUTTON ACTION FAILED | data=%s", data[:64])
        try:
            detail = f" ({type(exc).__name__})" if uid == config.ADMIN_ID else ""
            await q.message.reply_text(f"⚠️ حدث خطأ أثناء تنفيذ الزر{detail}. أعد المحاولة أو راجع سجل الإدارة.")
            feedback.page_delivered()
        except Exception:
            pass
    finally:
        # Stop the watchdog before waiting for a slow/failed acknowledgement.
        # Neither deadline cancels or retries a financial/business action.
        try:
            await feedback.close()
            await ans_task
        finally:
            if not ans_task.done():
                ans_task.cancel()
                await asyncio.gather(ans_task, return_exceptions=True)
            callback_feedback.unbind(feedback_token)
        log.info("BUTTON DONE | %.0fms | data=%s",
                 (time.monotonic() - t0) * 1000, data[:32])


# ---------------------------------------------------------------- user routing
async def _route_user(q, context, uid: int, action: str):
    # ONE pooled round trip: throttled last_seen write + profile read.
    # v6.20.1: pass preloaded_u straight into user_page() so the user row is
    # NEVER fetched twice on a single button tap!
    u = await afn.run(users_service.touch_and_get, uid)
    if u is None:
        try:
            await afn.run(users_service.ensure_user, q.from_user)
            u = await afn.run(users_service.touch_and_get, uid)
        except PermissionError:
            from fysb.handlers.common import render
            await render(q, "⛔ التسجيل مغلق مؤقتاً.", None, context.bot)
            return
    if u and u["is_blocked"]:
        from fysb.handlers.common import render
        await render(q, "⛔ حسابك موقوف. تواصل مع الدعم الفني.", None, context.bot)
        return
    if await afn.run(db.maintenance_on) and uid != config.ADMIN_ID:
        from fysb.handlers.common import render
        await render(q, "🔧 المنصة في وضع الصيانة مؤقتاً. حاول بعد قليل.", None, context.bot)
        return
    # v6.21.2 Strict Partner-Only Mode: active partners cannot access regular
    # user menus (`u:*`) — EXCEPT their granted Royal True Analysis studio
    # (`true_analysis`, `ta_anl`, `ta_cat:`, `ta_sym:`, `ta_tf:`, `ta_run:`,
    # `tamoncancel:`, and viewing their own True Analysis signal chart via `sigchart:`).
    # Any other stale user button redirects straight to their Partner Command Panel.
    if uid != config.ADMIN_ID and u and u.get("is_active_partner"):
        if action in ("true_analysis", "ta_anl") or action.startswith(
            ("ta_cat:", "ta_sym:", "ta_tf:", "ta_run:", "tamoncancel:")
        ):
            from fysb.handlers import true_analysis_ui
            if await true_analysis_ui.handle_user_true_analysis(q, uid, action, context, context.bot):
                return
        elif action.startswith("sigchart:"):
            await user_pages.user_page(q, action, uid, context, preloaded_u=u)
            return
        await admin_partners.handle_partner_callback(q, uid, context, context.bot, "home")
        return
    # Ticket replies: route the text flow (handled in inputs module)
    if action.startswith("ticketreply:"):
        context.user_data["ticket_reply_to"] = int(action.split(":", 1)[1])
        from fysb.handlers.common import render
        await render(q, "💬 أرسل رسالتك الآن — ستُسجل في التذكرة.", None, context.bot)
        return
    # Phase 4 (v6.27.0) & v7.1.0 Royal Academy + v8.7.0 Unified Affiliates & Plan-Unlock
    if action in ("academy", "acadbuy", "redeem", "affiliate", "affpayout", "affunlock") or action.startswith(
        ("acadtrack:", "acadles:", "acadquiz:", "acadcert:", "acadmedia:", "acadusdt:", "acadpay:", "affunlockgo:")
    ):
        from fysb.handlers import academy_growth
        if await academy_growth.handle_user_growth(q, uid, action, context, context.bot):
            return
    # Phase 5 (v6.28.0): Free Crypto Exchange API Integration (Binance/Bybit/OKX)
    if action in ("exchange", "exchorders") or action.startswith(
        ("exchadd:", "exchtest:", "exchtoglive:", "exchdel:", "exchexec:")
    ):
        from fysb.handlers import crypto_api
        if await crypto_api.handle_user_crypto_api(q, uid, action, context, context.bot):
            return
    # v8.4.0 / v8.5.2: Royal True Analysis Radar Subscription + Dedicated Separate Wizard
    if action in ("true_analysis", "tatrial", "tabuy", "ta_anl") or action.startswith(
        ("tausdt:", "tapay:", "tamoncancel:", "ta_cat:", "ta_sym:", "ta_tf:", "ta_run:")
    ):
        from fysb.handlers import true_analysis_ui
        if await true_analysis_ui.handle_user_true_analysis(q, uid, action, context, context.bot):
            return
    # v7.7.2: Native Telegram Royal Auto-Bridge (cTrader / TradeLocker / Deriv / MT5 EA)
    if action == "bridge" or action.startswith("br_"):
        from fysb.handlers import auto_bridge_bot
        if await auto_bridge_bot.handle_user_bridge(q, uid, action, context, context.bot):
            return
    await user_pages.user_page(q, action, uid, context, preloaded_u=u)


# --------------------------------------------------------------- admin routing
async def _route_admin(q, context, uid: int, action: str):
    if uid != config.ADMIN_ID:
        await afn.run(audit_log.log, uid, f"admin_probe:{action[:40]}")
        from fysb.handlers.common import render
        await render(q, "⛔ غير مصرح لك بالوصول إلى لوحة الإدارة.", kb.user_menu(), context.bot)
        return
    bot = context.bot
    # v6.10.0 (owner report 3) — a button press means NAVIGATION: kill any
    # stale one-shot text-awaiting flag so an unanswered old prompt can never
    # shadow the next text flow (the live TRC20-vs-rejection-reason bug).
    for k in CLEAR_ON_NAV:
        context.user_data.pop(k, None)
    parts = action.split(":", 1)
    act = parts[0]
    arg = parts[1] if len(parts) > 1 else ""
    arg2 = ""
    if ":" in arg:
        arg, arg2 = arg.split(":", 1)

    if act == "home":
        from fysb.handlers.common import build_admin_home_card, render
        await render(q, await build_admin_home_card(), kb.admin_menu(), bot)
    elif act in ("bridge", "promos", "promo_new", "digest_now") or act.startswith("br_"):
        from fysb.handlers import auto_bridge_bot
        await auto_bridge_bot.handle_admin_bridge(q, uid, act, arg, arg2, context, bot)
    elif act in ("true_analysis", "tatoggle_global", "taset", "talist", "utamenu", "utagrant", "utarevoke"):
        from fysb.handlers import true_analysis_ui
        await true_analysis_ui.handle_admin_true_analysis(q, uid, act, arg, arg2, context, bot)
    elif act == "hub":
        from fysb.handlers.common import build_admin_home_card, render
        hub = kb.ADMIN_HUBS.get(arg)
        if hub:
            title, builder = hub
            now_ksa = config.mecca_time_12h()
            await render(q, f"{title}\n🕒 <code>{now_ksa}</code>", builder(), bot)
        else:
            await render(q, await build_admin_home_card(), kb.admin_menu(), bot)
    # --- core ---
    elif act == "stats":
        await admin_core.admin_stats(q, uid, context, bot)
    elif act == "statistics":
        await admin_core.admin_statistics(q, uid, context, bot, int(arg or 0))
    elif act == "users":
        await admin_core.admin_users(q, uid, context, bot)
    elif act == "userspage":
        await admin_core.admin_users(q, uid, context, bot, int(arg or 0))
    elif act == "usersearch":
        await admin_core.admin_user_search(q, uid, context, bot)
    elif act == "userdetail":
        await admin_core.admin_user_detail(q, uid, context, bot, int(arg))
    elif act == "toggleuser":
        await admin_core.admin_toggle_user(q, uid, context, bot, int(arg))
    elif act == "togglenotify":
        await admin_core.admin_toggle_notify(q, uid, context, bot, int(arg))
    elif act == "userpayments":
        await admin_core.admin_user_payments(q, uid, context, bot, int(arg))
    elif act == "userdeliveries":
        await admin_core.admin_user_deliveries(q, uid, context, bot, int(arg))
    elif act == "userdelete":
        await admin_core.admin_user_delete_confirm(q, uid, context, bot, int(arg))
    elif act == "userdeletego":
        await admin_core.admin_user_delete_go(q, uid, context, bot, int(arg))
    elif act == "subedit":
        await admin_core.admin_sub_edit(q, uid, context, bot, int(arg))
    elif act == "subset":
        await admin_core.admin_sub_set(q, uid, context, bot, int(arg), arg2)
    elif act == "subextend":
        await admin_core.admin_sub_extend(q, uid, context, bot, int(arg), int(arg2 or 30))
    elif act == "subcancel":
        await admin_core.admin_sub_cancel(q, uid, context, bot, int(arg))
    elif act == "demoacct":
        await admin_core.admin_demo_acct(q, uid, context, bot, int(arg))
    elif act == "demoreset_admin":
        await admin_core.admin_demo_reset(q, uid, context, bot, int(arg))
    elif act == "subscriptions":
        await admin_core.admin_subscriptions(q, uid, context, bot)
    elif act == "plans":
        await admin_core.admin_plans(q, uid, context, bot)
    elif act == "plannew":
        await admin_core.admin_plan_new(q, uid, context, bot)
    elif act == "plancur":
        await admin_core.admin_plan_cb(q, uid, context, bot, "cur", arg)
    elif act == "plandays":
        await admin_core.admin_plan_cb(q, uid, context, bot, "days", arg)
    elif act == "planscore":
        await admin_core.admin_plan_cb(q, uid, context, bot, "score", arg)
    elif act == "planquota":
        await admin_core.admin_plan_cb(q, uid, context, bot, "quota", arg)
    elif act == "plananal":
        await admin_core.admin_plan_cb(q, uid, context, bot, "anal", arg)
    elif act == "planmkt":
        await admin_core.admin_plan_cb(q, uid, context, bot, "mkt", arg)
    elif act == "plantf":
        await admin_core.admin_plan_cb(q, uid, context, bot, "tf", arg)
    elif act == "planstrat":
        await admin_core.admin_plan_cb(q, uid, context, bot, "strat", arg)
    elif act == "planstage":
        await admin_core.admin_plan_cb(q, uid, context, bot, "stage", arg)
    elif act == "planconfirm":
        await admin_core.admin_plan_cb(q, uid, context, bot, "confirm", "")
    elif act == "plancancel":
        await admin_core.admin_plan_cb(q, uid, context, bot, "cancel", "")
    elif act == "plancustom":
        await admin_core.admin_plan_cb(q, uid, context, bot, "custom", arg)
    elif act == "planfield":
        code, field = arg, arg2
        await admin_core.admin_plan_field(q, uid, context, bot, code, field)
    elif act == "plantrial":
        await admin_core.admin_plan_days_preset(q, uid, context, bot, arg, int(arg2 or 0))
    elif act == "planedit":
        await admin_core.admin_plan_edit(q, uid, context, bot, arg)
    elif act == "planadj":
        await admin_core.admin_plan_adjust(q, uid, context, bot, arg, arg2.rsplit(":", 1)[0], float(arg2.rsplit(":", 1)[1]))
    elif act == "toggleplan":
        await admin_core.admin_plan_toggle(q, uid, context, bot, arg)
    elif act == "plandel":
        await admin_core.admin_plan_delete(q, uid, context, bot, arg)
    elif act == "planfeatpage":
        await admin_core.admin_plan_feat_page(q, uid, context, bot, arg, arg2)
    elif act == "planfeat":
        code, kind, item = arg, arg2.split(":", 1)[0], arg2.split(":", 1)[1] if ":" in arg2 else ""
        await admin_core.admin_plan_feat_toggle(q, uid, context, bot, code, kind, item)
    elif act == "channels":
        await admin_channels.admin_channels(q, uid, context, bot)
    # --- referrals (royal invite → subscribe funnel) ---
    elif act == "refs":
        await admin_referrals.admin_refs(q, uid, context, bot)
    elif act == "refnew":
        await admin_referrals.admin_ref_new(q, uid, context, bot)
    elif act == "refview":
        await admin_referrals.admin_ref_view(q, uid, context, bot, int(arg))
    elif act == "reftoggle":
        await admin_referrals.admin_ref_toggle(q, uid, context, bot, int(arg))
    elif act == "refdel":
        await admin_referrals.admin_ref_delete(q, uid, context, bot, int(arg))
    elif act == "reftgti":
        await admin_referrals.admin_ref_targets(q, uid, context, bot, int(arg), "invited")
    elif act == "reftgtc":
        await admin_referrals.admin_ref_targets(q, uid, context, bot, int(arg), "converted")
    elif act == "reftgtset":
        await admin_referrals.admin_ref_targets_set(q, uid, context, bot, int(arg), arg2.split(":", 1)[0], int(arg2.split(":", 1)[1]))
    elif act == "reflist":
        await admin_referrals.admin_ref_list(q, uid, context, bot, int(arg), int(arg2 or 0))
    elif act == "refnoop":
        await admin_core.admin_noop(q, uid, context, bot)
    elif act == "chanadd":
        await admin_channels.admin_chan_add(q, uid, context, bot)
    elif act == "chancancel":
        await admin_channels.admin_chan_cancel(q, uid, context, bot)
    elif act == "chanview":
        await admin_channels.admin_chan_view(q, uid, context, bot, int(arg))
    elif act == "chanall":
        await admin_channels.admin_chan_enable_all(q, uid, context, bot, int(arg))
    elif act == "chanvip":
        await admin_channels.admin_chan_toggle_vip(q, uid, context, bot, int(arg))
    elif act == "partners":
        await admin_partners.admin_partners(q, uid, context, bot)
    elif act == "partadd":
        await admin_partners.admin_partner_add(q, uid, context, bot)
    elif act == "partcancel":
        await admin_partners.admin_partner_cancel(q, uid, context, bot)
    elif act == "partview":
        await admin_partners.admin_partner_view(q, uid, context, bot, int(arg))
    elif act == "parttoggle":
        await admin_partners.admin_partner_toggle(q, uid, context, bot, int(arg))
    elif act == "partpct":
        await admin_partners.admin_partner_pct(q, uid, context, bot, int(arg), int(arg2))
    elif act == "partmode":
        await admin_partners.admin_partner_mode(q, uid, context, bot, int(arg), arg2)
    elif act == "partperm":
        await admin_partners.admin_partner_perm(q, uid, context, bot, int(arg), arg2)
    elif act == "partlim":
        await admin_partners.admin_partner_limits(q, uid, context, bot, int(arg))
    elif act == "partlimset":
        kind, val = (arg2.split(":", 1) + ["6"])[:2]
        await admin_partners.admin_partner_limit_set(q, uid, context, bot, int(arg), kind, int(val))
    elif act == "partlimask":
        await admin_partners.admin_partner_limit_ask(q, uid, context, bot, int(arg), arg2)
    elif act == "partlimreset":
        await admin_partners.admin_partner_limit_reset(q, uid, context, bot, int(arg))
    elif act == "partsettle":
        await admin_partners.admin_partner_settle(q, uid, context, bot, int(arg))
    elif act == "partdel":
        await admin_partners.admin_partner_delete(q, uid, context, bot, int(arg))
    elif act == "vipsweep":
        await admin_partners.admin_vip_sweep(q, uid, context, bot)
    elif act == "chantoggle":
        await admin_channels.admin_chan_toggle(q, uid, context, bot, int(arg))
    elif act == "chanmode":
        await admin_channels.admin_chan_mode(q, uid, context, bot, int(arg), arg2)
    elif act == "chanfeat":
        await admin_channels.admin_chan_feat(q, uid, context, bot, int(arg), arg2)
    elif act == "chanfeatset":
        cid, kind, item = arg, arg2.split(":", 1)[0], arg2.split(":", 1)[1] if ":" in arg2 else ""
        await admin_channels.admin_chan_feat_set(q, uid, context, bot, int(cid), kind, item)
    elif act == "chaninterval":
        await admin_channels.admin_chan_interval(q, uid, context, bot, int(arg))
    elif act == "chansetinterval":
        await admin_channels.admin_chan_set_interval(q, uid, context, bot, int(arg), int(arg2))
    elif act == "chanpost":
        await admin_channels.admin_chan_post(q, uid, context, bot, int(arg), arg2)
    elif act == "chandel":
        await admin_channels.admin_chan_delete(q, uid, context, bot, int(arg))
    elif act == "plannoop":
        await admin_core.admin_noop(q, uid, context, bot)
    elif act == "payments":
        await admin_core.admin_payments(q, uid, context, bot)
    elif act == "paymentsstatus":
        await admin_core.admin_payments(q, uid, context, bot, arg)
    elif act == "paymentdetail":
        await admin_core.admin_payment_detail(q, uid, context, bot, int(arg))
    elif act == "approvepay":
        await admin_core.admin_payment_approve(q, uid, context, bot, int(arg))
    elif act == "rejectpay":
        await admin_core.admin_payment_reject(q, uid, context, bot, int(arg))
    elif act == "viewreceipt":
        await admin_core.admin_view_receipt(q, uid, context, bot, int(arg))
    elif act == "paymethods":
        await admin_core.admin_paymethods(q, uid, context, bot)
    elif act == "pmadd":
        await admin_core.admin_pm_add(q, uid, context, bot)
    elif act == "pmcat":
        await admin_core.admin_pm_cat(q, uid, context, bot, arg)
    elif act == "pmitem":
        await admin_core.admin_pm_item(q, uid, context, bot, arg)
    elif act == "pmplan":
        await admin_core.admin_pm_toggle_plan(q, uid, context, bot, arg)
    elif act == "pmsave":
        await admin_core.admin_pm_save(q, uid, context, bot)
    elif act == "pmcancel":
        await admin_core.admin_pm_cancel(q, uid, context, bot)
    elif act == "pmview":
        await admin_core.admin_pm_view(q, uid, context, bot, int(arg))
    elif act == "pmtoggle":
        await admin_core.admin_pm_toggle(q, uid, context, bot, int(arg))
    elif act == "pmdel":
        await admin_core.admin_pm_delete(q, uid, context, bot, int(arg))
    elif act == "usdtwallet":
        await admin_core.admin_usdt_wallet(q, uid, context, bot, arg or "TRC20")
    # --- trading ---
    elif act == "signals":
        await admin_trading.admin_signals(q, uid, context, bot)
    elif act == "signalspage":
        await admin_trading.admin_signals(q, uid, context, bot, int(arg or 0))
    elif act == "sigcat":
        await admin_trading.admin_signals_cat(q, uid, context, bot, arg, int(arg2 or 0))
    elif act == "sigday":
        # a:sigday:<scope>:<cat>:<page>
        day_cat = "all"
        day_page = 0
        if arg2:
            sub = arg2.split(":", 1)
            day_cat = sub[0] or "all"
            if len(sub) > 1 and sub[1].isdigit():
                day_page = int(sub[1])
        await admin_trading.admin_signals_day(q, uid, context, bot, arg or "today", day_cat, day_page)
    elif act == "sigdates":
        await admin_trading.admin_signals_dates(q, uid, context, bot, int(arg or 0))
    elif act == "sigpl":
        await admin_trading.admin_signal_pl(q, uid, context, bot, arg or "all")
    elif act == "sigplcat":
        await admin_trading.admin_signal_pl_sector(q, uid, context, bot, arg or "metal", arg2 or "all")
    elif act == "sigplsym":
        await admin_trading.admin_signal_pl_symbol(q, uid, context, bot, arg, arg2 or "all")
    elif act == "signow":
        from fysb.handlers import signal_now
        await signal_now.open_flow(q, uid, context, bot, is_admin=True)
    elif act == "signowstyle":
        from fysb.handlers import signal_now
        await signal_now.pick_style(q, uid, context, bot, arg)
    elif act == "signowcat":
        from fysb.handlers import signal_now
        await signal_now.pick_category(q, uid, context, bot, arg, int(arg2 or 1))
    elif act == "signowsym":
        from fysb.handlers import signal_now
        await signal_now.pick_symbol(q, uid, context, bot, arg)
    elif act == "signowtf":
        from fysb.handlers import signal_now
        await signal_now.pick_tf(q, uid, context, bot, arg)
    elif act == "signowstrat":
        from fysb.handlers import signal_now
        await signal_now.pick_strategy(q, uid, context, bot, arg)
    elif act == "signowwatch":
        from fysb.handlers import signal_now
        await signal_now.start_watch(q, uid, context, bot, int(arg or 30))
    elif act == "watchoff":
        from fysb.handlers import signal_now
        await signal_now.stop_watch(q, uid, context, bot, int(arg or 0))
    elif act == "signalview":
        await admin_trading.admin_signal_view(q, uid, context, bot, int(arg))
    elif act == "signalrecipients":
        await admin_trading.admin_signal_recipients(q, uid, context, bot, int(arg))
    elif act == "cancelsignal":
        await admin_trading.admin_cancel_signal(q, uid, context, bot, int(arg))
    elif act == "delsignal":
        await admin_trading.admin_delete_signal_confirm(q, uid, context, bot, int(arg))
    elif act == "delsignalgo":
        await admin_trading.admin_delete_signal_go(q, uid, context, bot, int(arg))
    elif act == "newsignal":
        await admin_trading.admin_new_signal(q, uid, context, bot)
    elif act == "tracknow":
        await admin_trading.admin_track_now(q, uid, context, bot)
    elif act == "signalpurge":
        await admin_trading.admin_signal_purge(q, uid, context, bot)
    elif act == "signalpurgego":
        await admin_trading.admin_signal_purge_go(q, uid, context, bot)
    elif act == "engine":
        await admin_trading.admin_engine(q, uid, context, bot)
    elif act == "enginetoggle":
        await admin_trading.admin_engine_toggle(q, uid, context, bot)
    elif act == "engineadj":
        await admin_trading.admin_engine_adjust(q, uid, context, bot, arg, float(arg2))
    elif act == "enginenow":
        await admin_trading.admin_engine_now(q, uid, context, bot)
    elif act == "engineclean":
        await admin_trading.admin_engine_clean(q, uid, context, bot)
    elif act == "scantfs":
        await admin_trading.admin_scan_tfs(q, uid, context, bot)
    elif act == "togglescantf":
        await admin_trading.admin_scan_tf_toggle(q, uid, context, bot, arg)
    elif act == "newsguard":
        await admin_trading.admin_news_guard(q, uid, context, bot)
    elif act == "newsrefresh":
        await admin_trading.admin_news_guard(q, uid, context, bot, force_refresh=True, notice="🔄 تم تحديث التقويم الاقتصادي من المصدر الحي.")
    elif act == "newsguardtoggle":
        await admin_trading.admin_news_guard_toggle(q, uid, context, bot)
    elif act == "newsguardwin":
        await admin_trading.admin_news_guard_win(q, uid, context, bot, int(arg or 30))
    elif act == "newsauto":
        await admin_trading.admin_news_auto_toggle(q, uid, context, bot)
    elif act == "newsdest":
        await admin_trading.admin_news_dest_toggle(q, uid, context, bot, arg)
    elif act == "newsfailopen":
        await admin_trading.admin_news_fail_open(q, uid, context, bot)
    elif act == "newscachehrs":
        await admin_trading.admin_news_cache_hours(q, uid, context, bot)
    elif act == "newscachehrsset":
        await admin_trading.admin_news_cache_hours_set(q, uid, context, bot, int(arg or 48))
    elif act == "enginediag":
        await admin_trading.admin_engine_diagnostics(q, uid, context, bot)
    elif act == "enginediagprobe":
        await admin_trading.admin_engine_diag_probe(q, uid, context, bot)
    elif act == "enginediagmarkets":
        await admin_trading.admin_engine_diag_markets(q, uid, context, bot, int(arg or 0))
    elif act == "enginediagruns":
        await admin_trading.admin_engine_diag_runs(q, uid, context, bot, int(arg or 0))
    elif act == "enginediagceiling":
        await admin_trading.admin_engine_diag_ceiling(q, uid, context, bot)
    elif act == "newssendnow":
        await admin_trading.admin_news_send_now(q, uid, context, bot)
    elif act == "leaderboard":
        await admin_trading.admin_leaderboard(q, uid, context, bot, "30d")
    elif act == "lb":
        await admin_trading.admin_leaderboard(q, uid, context, bot, arg or "30d")
    elif act == "lbpublish":
        await admin_trading.admin_leaderboard_publish(q, uid, context, bot, arg or "30d")
    elif act == "strength":
        await admin_trading.admin_strength(q, uid, context, bot)
    elif act == "strengthrefresh":
        await admin_trading.admin_strength(q, uid, context, bot, force_refresh=True)
    elif act == "chartstoggle":
        await admin_trading.admin_charts_toggle(q, uid, context, bot)
    elif act == "chartsmenu":
        await admin_trading.admin_charts_menu(q, uid, context, bot)
    elif act == "chartsched":
        await admin_trading.admin_chart_sched_set(q, uid, context, bot, int(arg or 0))
    elif act == "chartschedch":
        await admin_trading.admin_chart_sched_ch_toggle(q, uid, context, bot)
    elif act == "chartsendnow":
        await admin_trading.admin_chart_send_now(q, uid, context, bot)
    elif act == "chartchs":
        await admin_trading.admin_chart_channels_menu(q, uid, context, bot)
    elif act == "chartch_add":
        await admin_trading.admin_chart_channel_add_prompt(q, uid, context, bot)
    elif act == "chartch":
        await admin_trading.admin_chart_channel_view(q, uid, context, bot, int(arg or 0))
    elif act == "chartch_tog":
        await admin_trading.admin_chart_channel_toggle(q, uid, context, bot, int(arg or 0))
    elif act == "chartch_flg":
        await admin_trading.admin_chart_channel_flag_toggle(q, uid, context, bot, int(arg or 0), arg2)
    elif act == "chartch_mkts":
        await admin_trading.admin_chart_channel_markets(q, uid, context, bot, int(arg or 0))
    elif act == "chartch_mkt":
        await admin_trading.admin_chart_channel_market_toggle(q, uid, context, bot, int(arg or 0), arg2)
    elif act == "chartch_test":
        await admin_trading.admin_chart_channel_test(q, uid, context, bot, int(arg or 0))
    elif act == "chartch_del":
        await admin_trading.admin_chart_channel_delete(q, uid, context, bot, int(arg or 0))
    elif act == "sigchart":
        await admin_trading.admin_signal_chart(q, uid, context, bot, int(arg or 0))
    elif act == "sigproof":
        await admin_trading.admin_signal_proof_publish(q, uid, context, bot, int(arg or 0))
    elif act == "backtest":
        await admin_trading.admin_backtest(q, uid, context, bot, int(arg or 0))
    elif act == "btrun":
        bt_tf, bt_scope = (arg2.split(":", 1) + ["enabled"])[:2] if arg2 else ("M15", "enabled")
        await admin_trading.admin_backtest_run(q, uid, context, bot, arg, bt_tf, bt_scope)
    elif act == "strategies":
        await admin_trading.admin_strategies(q, uid, context, bot, int(arg or 0))
    elif act == "strategyinfo":
        await admin_trading.admin_strategy_info(q, uid, context, bot, arg, int(arg2 or 0))
    elif act == "strategy_toggle":
        await admin_trading.admin_strategy_toggle(q, uid, context, bot, arg, int(arg2 or 0))
    elif act == "strategy_verify":
        await admin_trading.admin_strategy_verify(q, uid, context, bot, arg, int(arg2 or 0))
    elif act == "modes":
        await admin_trading.admin_modes(q, uid, context, bot)
    elif act == "mode_set":
        await admin_trading.admin_mode_set(q, uid, context, bot, arg)
    elif act == "risk":
        await admin_trading.admin_risk(q, uid, context, bot)
    elif act == "riskadj":
        await admin_trading.admin_risk_adjust(q, uid, context, bot, arg, float(arg2))
    elif act == "killswitch":
        await admin_trading.admin_kill_switch(q, uid, context, bot)
    elif act == "volfilter":
        await admin_trading.admin_vol_filter(q, uid, context, bot)
    elif act == "eodclosenow":
        await admin_trading.admin_eod_close_now(q, uid, context, bot)
    elif act == "freshpage":
        await admin_trading.admin_fresh_page(q, uid, context, bot)
    elif act == "freshpagego":
        await admin_trading.admin_fresh_page_go(q, uid, context, bot)
    elif act == "markets":
        await admin_trading.admin_markets(q, uid, context, bot)
    elif act == "mktcat":
        await admin_trading.admin_markets_category(q, uid, context, bot, arg, int(arg2 or 1))
    elif act == "mktbulk":
        await admin_trading.admin_markets_bulk(q, uid, context, bot, arg, arg2)
    elif act == "market_toggle":
        await admin_trading.admin_market_toggle(q, uid, context, bot, arg, arg2)
    elif act == "market_verify":
        await admin_trading.admin_market_verify(q, uid, context, bot, arg)
    elif act == "adminmkts":
        await admin_trading.admin_signal_markets(q, uid, context, bot)
    elif act == "adminmktstog":
        await admin_trading.admin_signal_markets_toggle(q, uid, context, bot, arg)
    elif act == "adminmktsonly":
        await admin_trading.admin_signal_markets_only(q, uid, context, bot, arg)
    elif act == "adminmktsall":
        await admin_trading.admin_signal_markets_all(q, uid, context, bot)
    elif act == "market_add":
        await admin_trading.admin_market_add(q, uid, context, bot)
    elif act == "timeframes":
        await admin_trading.admin_timeframes(q, uid, context, bot)
    elif act == "tf_toggle":
        await admin_trading.admin_tf_toggle(q, uid, context, bot, arg)
    elif act == "tf_verify":
        await admin_trading.admin_tf_verify(q, uid, context, bot, arg)
    elif act == "brokers":
        await admin_trading.admin_brokers(q, uid, context, bot)
    elif act == "brokeradd":
        await admin_trading.admin_broker_add(q, uid, context, bot)
    elif act == "brokertype":
        await admin_trading.admin_broker_type(q, uid, context, bot, arg)
    elif act == "brokercancel":
        await admin_trading.admin_broker_cancel(q, uid, context, bot)
    elif act == "brokertest":
        await admin_trading.admin_broker_test(q, uid, context, bot, int(arg))
    elif act == "brokerapprove":
        await admin_trading.admin_broker_approve(q, uid, context, bot, int(arg))
    elif act == "brokerdel":
        await admin_trading.admin_broker_delete(q, uid, context, bot, int(arg))
    elif act == "accounts":
        await admin_trading.admin_accounts(q, uid, context, bot)
    elif act == "demo":
        await admin_trading.admin_demo(q, uid, context, bot)
    elif act == "live":
        await admin_trading.admin_live(q, uid, context, bot)
    # --- system ---
    elif act == "reports":
        await admin_system.admin_reports(q, uid, context, bot)
    elif act == "repusers":
        await admin_system.admin_rep_users(q, uid, context, bot)
    elif act == "repsignals":
        await admin_system.admin_rep_signals(q, uid, context, bot)
    elif act == "reppayments":
        await admin_system.admin_rep_payments(q, uid, context, bot)
    elif act == "repsubs":
        await admin_system.admin_rep_subs(q, uid, context, bot)
    elif act == "repengine":
        await admin_system.admin_rep_engine(q, uid, context, bot)
    elif act == "notifications":
        await admin_system.admin_notifications(q, uid, context, bot)
    elif act == "togglepush":
        await admin_system.admin_toggle_push(q, uid, context, bot)
    elif act == "reminddays":
        await admin_system.admin_remind_days(q, uid, context, bot, int(arg))
    elif act == "broadcast":
        await admin_system.admin_broadcast(q, uid, context, bot)
    elif act == "bcaud":
        await admin_system.admin_bc_audience(q, uid, context, bot, arg)
    elif act == "bcplan":
        await admin_system.admin_bc_plan(q, uid, context, bot)
    elif act == "bcpick":
        await admin_system.admin_bc_pick(q, uid, context, bot, arg)
    elif act == "bcgo":
        await admin_system.admin_bc_go(q, uid, context, bot, int(arg))
    elif act == "bcabort":
        await admin_system.admin_bc_abort(q, uid, context, bot, int(arg))
    elif act == "bccancel":
        await admin_system.admin_bc_cancel(q, uid, context, bot, int(arg))
    elif act == "support":
        await admin_system.admin_support(q, uid, context, bot)
    elif act == "supportconfig":
        await admin_system.admin_support_config(q, uid, context, bot)
    elif act == "supfield":
        await admin_system.admin_support_field(q, uid, context, bot, arg)
    elif act == "ticketview":
        await admin_system.admin_ticket_view(q, uid, context, bot, int(arg))
    elif act == "ticketreply":
        await admin_system.admin_ticket_reply(q, uid, context, bot, int(arg))
    elif act == "ticketclose":
        await admin_system.admin_ticket_close(q, uid, context, bot, int(arg))
    elif act == "ticketreopen_admin":
        await admin_system.admin_ticket_reopen(q, uid, context, bot, int(arg))
    elif act == "security":
        await admin_system.admin_security(q, uid, context, bot)
    elif act == "sys_tdkey":
        await admin_system.admin_tdkey(q, uid, context, bot)
    elif act == "sys_tdset":
        await admin_system.admin_tdkey_set(q, uid, context, bot)
    elif act == "sys_tdclear":
        await admin_system.admin_tdkey_clear(q, uid, context, bot)
    elif act == "togglemaintenance":
        await admin_system.admin_toggle_maintenance(q, uid, context, bot)
    elif act == "toggleblocknew":
        await admin_system.admin_toggle_blocknew(q, uid, context, bot)
    elif act == "backup":
        await admin_system.admin_backup(q, uid, context, bot)
    elif act == "bknow":
        await admin_system.admin_backup_now(q, uid, context, bot)
    elif act == "bkfull":
        await admin_system.admin_backup_full(q, uid, context, bot)
    elif act == "bksettings":
        await admin_system.admin_backup_settings(q, uid, context, bot)
    elif act == "bksend":
        await admin_system.admin_backup_send(q, uid, context, bot, arg)
    elif act == "bkrestore":
        await admin_system.admin_backup_restore(q, uid, context, bot)
    elif act == "settings":
        await admin_system.admin_settings(q, uid, context, bot)
    elif act == "toggleuimode":
        await admin_system.admin_toggle_ui_mode(q, uid, context, bot)
    elif act == "freeset":
        await admin_system.admin_free_set(q, uid, context, bot)
    elif act == "demobalset":
        await admin_system.admin_demo_bal_set(q, uid, context, bot)
    elif act == "expiryset":
        await admin_system.admin_expiry_set(q, uid, context, bot)
    elif act == "audit":
        await admin_system.admin_audit(q, uid, context, bot)
    elif act == "auditpage":
        await admin_system.admin_audit(q, uid, context, bot, int(arg or 0))
    elif act == "health":
        await admin_system.admin_health(q, uid, context, bot)
    elif act == "webapp":
        await admin_system.admin_webapp_info(q, uid, context, bot)
    elif act == "webapptunnel_toggle":
        await admin_system.admin_webapp_tunnel_toggle(q, uid, context, bot)
    elif act == "webapptunnel_restart":
        await admin_system.admin_webapp_tunnel_restart(q, uid, context, bot)
    elif act == "webapp_setngrok":
        await admin_system.admin_webapp_set_ngrok(q, uid, context, bot)
    elif act == "webapp_worker":
        await admin_system.admin_webapp_worker_guide(q, uid, context, bot)
    elif act == "webapp_ghpages":
        await admin_system.admin_webapp_enable_ghpages(q, uid, context, bot)
    elif act == "webapp_seturl":
        await admin_system.admin_webapp_set_url(q, uid, context, bot)
    elif act == "updates":
        await admin_system.admin_updates(q, uid, context, bot, max(1, user_pages._safe_int(arg) or 1))
    elif act == "updnotes":
        await admin_system.admin_release_notes(q, uid, context, bot, arg, max(1, user_pages._safe_int(arg2) or 1))
    elif act == "updlatest":
        await admin_system.admin_update_latest(q, uid, context, bot)
    elif act == "updver":
        await admin_system.admin_update_ver_confirm(q, uid, context, bot, arg)
    elif act == "updvergo":
        await admin_system.admin_update_ver_go(q, uid, context, bot, arg)
    elif act == "updrollback":
        await admin_system.admin_update_rollback_confirm(q, uid, context, bot)
    elif act == "updrollbackgo":
        await admin_system.admin_update_rollback_go(q, uid, context, bot)
    elif act == "analytics":
        await admin_system.admin_analytics(q, uid, context, bot)
    elif act == "anladj":
        await admin_system.admin_anl_adjust(q, uid, context, bot, arg, float(arg2))
    elif act == "anlglobal":
        await admin_system.admin_anl_global(q, uid, context, bot)
    elif act == "repanal":
        await admin_system.admin_rep_anal(q, uid, context, bot)
    elif act in ("academy", "acadtoggle", "acadprice", "acadprice_custom", "acaddur", "acaddur_custom",
                 "acadlessons", "acadles_view", "acadles_preview", "acadles_media", "acadles_edit",
                 "acadles_quiz", "acadles_track", "acadles_tog", "acadles_del", "acadles_delgo",
                 "acadnew", "acadnew_tr", "acadnew_mt", "acadnew_save", "acadgrant",
                 "gifts", "giftgen", "giftcustom", "giftdirect",
                 "affiliates", "affcfg", "affset", "affapprove", "affreject"):
        from fysb.handlers import academy_growth
        await academy_growth.handle_admin_growth(q, uid, act, arg, arg2, context, bot)
    elif act in ("cryptoapi", "cryptoapitoggle"):
        from fysb.handlers import crypto_api
        await crypto_api.handle_admin_crypto_api(q, uid, act, arg, context, bot)
    else:
        from fysb.handlers.common import render
        await render(q, "إجراء إداري غير معروف. حدّث القائمة عبر /start.", kb.admin_menu(), bot)
