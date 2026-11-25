"""Input state machine — guided, step-by-step text flows (luxury UX).

Every flow walks the admin field-by-field with inline buttons where a value is
choosable (currency, days, quotas, plans, presets). Each awaiting_* flag is
popped exactly once; invalid input re-prompts with the exact expectation.
All validation is server-side; errors never leak internals and secrets
(broker API keys) are never echoed back.
"""
import asyncio
import json
import re
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timezone

from telegram import Update
from telegram.ext import ContextTypes

from fysb import afn, config, db, instruments
from fysb.services import chain_verify
from fysb.handlers.common import safe
from fysb.services import payments as payments_service
from fysb.services import support as support_service
from fysb.services import users as users_service
from fysb.services import audit as audit_log
from fysb.theme import esc
from fysb.config import log

TIMEFRAMES = ["M1", "M5", "M15", "M30", "H1", "H4", "D1", "W1"]
STRATEGIES = ["ICT", "SMC", "PRICE_ACTION", "CRT", "AMD", "TURTLE_SOUP", "SILVER_BULLET",
              "WYCKOFF", "FIBONACCI", "TREND"]

# Payment-method presets — Yemeni wallets + global crypto + bank options
PM_PRESETS = {
    # Yemeni local wallets (local currency friendly)
    "JAWALI": ("محفظة جوالي", "اليمن"),
    "ONE_CASH": ("محفظة واحد كاش", "اليمن"),
    "CASH_WALLET": ("محفظة كاش", "اليمن"),
    "MTN_CASH": ("محفظة MTN كاش", "اليمن"),
    "YU_WALLET": ("محفظة يو (Yu)", "اليمن"),
    "YEMEN_BANK": ("حوالة بنكية يمنية", "اليمن"),
    # Digital / global
    "USDT_TRC20": ("USDT TRC20", "عالمي"),
    "USDT_BEP20": ("USDT BEP20", "عالمي"),
    "BINANCE_PAY": ("Binance Pay", "عالمي"),
    "PAYEER": ("Payeer", "عالمي"),
    "PERFECT_MONEY": ("Perfect Money", "عالمي"),
    "BANK_WIRE": ("تحويل بنكي دولي", "عالمي"),
    "WESTERN_UNION": ("ويسترن يونيون", "عالمي"),
}
PM_CATEGORIES = [
    ("yemen", "🇾🇪 محافظ يمنية", ["JAWALI", "ONE_CASH", "CASH_WALLET", "MTN_CASH", "YU_WALLET", "YEMEN_BANK"]),
    ("crypto", "🪙 عملات رقمية", ["USDT_TRC20", "USDT_BEP20", "BINANCE_PAY", "PAYEER", "PERFECT_MONEY"]),
    ("bank", "🏦 بنكي / حوالات", ["BANK_WIRE", "WESTERN_UNION"]),
]

# Broker adapter presets — real vendors, honest statuses
BROKER_PRESETS = {
    "METAAPI": {"title": "MetaAPI (MT4/MT5 Cloud)", "base_url": "https://mt-provisioning-api-v1.agiliumtrade.agiliumtrade.ai",
                "auth": "Bearer Token", "test": "auth"},
    "BINANCE": {"title": "Binance Spot REST", "base_url": "https://api.binance.com", "auth": "API Key + Secret", "test": "ping"},
    "BYBIT": {"title": "Bybit V5 REST", "base_url": "https://api.bybit.com", "auth": "API Key + Secret", "test": "ping"},
    "OKX": {"title": "OKX V5 REST", "base_url": "https://www.okx.com", "auth": "API Key + Secret + Passphrase", "test": "ping"},
    "CUSTOM": {"title": "REST مخصص", "base_url": "", "auth": "حسب الوسيط", "test": "url"},
}

DRAFT_KEYS = ("plan_draft", "pm_draft", "broker_draft", "awaiting_signal",
              "awaiting_market_add", "awaiting_user_search", "reject_payment_id",
              "awaiting_broadcast_text", "awaiting_support_config", "awaiting_support_field",
              "awaiting_restore", "awaiting_free_quota", "awaiting_demo_balance",
              "awaiting_expiry_hours", "awaiting_chan_add", "awaiting_usdt_wallet",
              "awaiting_plan_field", "awaiting_plan_custom",
              "awaiting_partner_add", "awaiting_partner_channel", "awaiting_partner_brand",
              "awaiting_partner_limit")

# v6.10.0 — single-shot text-awaiting flags: a BUTTON PRESS means the admin is
# navigating, not typing, so any stale one-shot flag left over from a prompt he
# never answered must die — otherwise it silently shadows the NEXT text flow
# (the live bug: a stale awaiting_usdt_wallet ate the payment-rejection reason
# and answered with the TRC20 validator). Multi-step DRAFTS (plan/pm/broker)
# are NOT cleared — their stages legitimately span many button presses.
CLEAR_ON_NAV = ("awaiting_signal", "awaiting_market_add", "awaiting_user_search",
                "reject_payment_id", "awaiting_broadcast_text", "awaiting_support_config",
                "awaiting_support_field", "awaiting_restore", "awaiting_free_quota",
                "awaiting_demo_balance", "awaiting_expiry_hours", "awaiting_chan_add",
                "awaiting_usdt_wallet", "awaiting_plan_field", "awaiting_plan_custom",
                "awaiting_broadcast_photo", "broadcast_draft", "awaiting_usdt_txid",
                "usdt_chain", "awaiting_demo_bal_user", "awaiting_receipt",
                "awaiting_partner_add", "awaiting_partner_channel", "awaiting_partner_brand",
                "awaiting_partner_limit",
                "awaiting_gift_code", "awaiting_aff_wallet",
                "awaiting_acad_grant", "awaiting_acad_price", "awaiting_acad_dur",
                "awaiting_acad_media_for", "awaiting_acad_edit_for", "awaiting_acad_quiz_for",
                "awaiting_gift_custom", "awaiting_gift_direct",
                "awaiting_exch_creds", "awaiting_ngrok_token", "awaiting_webapp_url",
                "awaiting_chart_channel_add")


def clear_flows(ud: dict) -> None:
    """Start a new flow cleanly — no cross-flow state leakage."""
    for k in DRAFT_KEYS:
        ud.pop(k, None)


async def on_text(update: Update, context: ContextTypes.DEFAULT_TYPE):
    uid = update.effective_user.id
    text = (update.effective_message.text or "").strip()
    ud = context.user_data

    # ---------- Phase 5 (v6.28.0) Free Crypto Exchange API Credentials ----------
    if ud.get("awaiting_exch_creds"):
        from fysb.handlers import crypto_api
        if await crypto_api.handle_crypto_text_input(update, context, uid, text):
            return

    # ---------- Phase 4 (v6.27.0) & v7.1.0 Royal Academy Studio ----------
    if any(ud.get(k) for k in ("awaiting_gift_code", "awaiting_aff_wallet",
                               "awaiting_acad_grant", "awaiting_acad_price", "awaiting_acad_dur",
                               "awaiting_acad_edit_for", "awaiting_acad_quiz_for", "acad_new_draft",
                               "awaiting_gift_custom", "awaiting_gift_direct")):
        from fysb.handlers import academy_growth
        if await academy_growth.handle_growth_text_input(update, context, uid, text):
            return

    # ---------- Admin flows ----------
    if uid == config.ADMIN_ID:
        handled = await _admin_text(update, context, uid, text)
        if handled:
            return

    # ---------- Partner private flows (v6.21.0) ----------
    if ud.get("awaiting_partner_channel"):
        from fysb.handlers import admin_partners
        if await admin_partners.handle_partner_channel_input(update, context, uid, text):
            return
    if ud.get("awaiting_partner_brand"):
        from fysb.handlers import admin_partners
        if await admin_partners.handle_partner_brand_input(update, context, uid, text):
            return

    # ---------- User: own demo balance ----------
    if ud.pop("awaiting_demo_bal_user", False):
        try:
            val = float(text.replace(",", ""))
            if not (10 <= val <= 10_000_000):
                raise ValueError("الرصيد يجب أن يكون بين 10 و 10,000,000")
            from fysb.engines import demo as demo_engine
            await afn.run(demo_engine.set_balance, uid, val)
            await update.effective_message.reply_text(
                f"✅ أصبح رصيد حسابك التجريبي: <b>{val:,.2f} USD</b> — وأُغلقت الصفقات المفتوحة ضمن إعادة الضبط.\n"
                f"🤖 <b>التنفيذ التلقائي مُسلّح الآن:</b> كل إشارة مؤهلة تُنفذ في حسابك فوراً بحجم يُحسب "
                f"من رصيدك ونسبة المخاطرة تلقائياً (بدون إدخال لوت) — يمكنك تعطيله من إعدادات التجريبي.",
                parse_mode="HTML")
        except ValueError as e:
            ud["awaiting_demo_bal_user"] = True
            await update.effective_message.reply_text(f"❌ {safe(e)} — أرسل رقماً فقط (مثال: 5000).")
        return

    # ---------- User: USDT on-chain TXID — instant blockchain activation ----------
    # v6.8.1: the chain follows the network the user PICKED (TRC20/BEP20/ERC20/
    # Polygon) — stored alongside the pending request, never guessed.
    tx_pid = ud.pop("awaiting_usdt_txid", None)
    if tx_pid:
        from fysb.services import chain_verify
        pid = int(tx_pid)
        req = await afn.run(payments_service.get_request, pid)
        if not req or req["user_id"] != uid or req["status"] != "PENDING":
            await update.effective_message.reply_text("⚠️ الطلب غير متاح للتحقق (مُراجع مسبقاً أو غير موجود).")
            return
        chain = (ud.pop("usdt_chain", "") or (req.get("chain") if isinstance(req, dict) else "") or "TRC20").upper()
        used = await afn.run(payments_service.txid_used, text.strip())
        if used:
            await update.effective_message.reply_text(
                f"🔒 هذا TXID مُستخدم مسبقاً في الطلب #{used} — المعاملة محجوبة نهائياً ولا تُقبل مرتين.")
            return
        wallet = await afn.run(payments_service.chain_wallet, chain)
        if not wallet:
            await update.effective_message.reply_text(
                f"⛓ لم يُضبَط عنوان محفظة USDT على شبكة {chain} بعد — أرسل صورة الإيصال للمراجعة اليدوية أو تواصل مع الدعم.")
            return
        await update.effective_message.reply_text(f"⛓ جارٍ فحص المعاملة على شبكة {chain} (التأكيد + العنوان + المبلغ)…")
        # Allow a 5-minute grace window before payment creation for users who transferred
        # just before tapping the button, while blocking historical TXID replay attacks.
        min_ts = max(0.0, float(req.get("created_at") or 0) - 300.0)
        res = await asyncio.to_thread(chain_verify.verify_usdt,
                                      text.strip(), wallet, float(req["amount"] or 0), chain,
                                      20.0, min_ts)
        if not res.get("ok"):
            ud["awaiting_usdt_txid"] = pid
            ud["usdt_chain"] = chain
            await update.effective_message.reply_text(
                f"❌ <b>فشل التحقق البلوكيشن</b>\n{res.get('reason', '')}\n\n"
                "أعد إرسال TXID صحيحاً، أو أرسل صورة الإيصال للمراجعة اليدوية.",
                parse_mode="HTML")
            return
        bound = await afn.run(payments_service.attach_txid, pid, uid, text.strip(), chain)
        if not bound.get("ok"):
            ud.pop("awaiting_usdt_txid", None)
            await update.effective_message.reply_text("🔒 " + bound.get("error", ""))
            return
        appr = await afn.run(payments_service.approve_request, pid, actor=0)
        ud.pop("awaiting_usdt_txid", None)
        if not appr.get("ok"):
            await update.effective_message.reply_text("⚠️ المعاملة موثقة ومسجلة، لكن التفعيل أعاد: "
                                                      + safe(appr.get("error", "خطأ")) + " — تواصل مع الدعم.")
            return
        plan = await afn.run(_fetch_plan_row, req["plan_code"])
        if str(req["plan_code"] or "").upper() == "ACADEMY":
            from fysb.services import academy as _acad_svc
            title = "🎓 أكاديمية ForexYemeni الاحترافية"
            days = await afn.run(_acad_svc.paid_duration_days)
        else:
            title = esc(plan["title"]) if plan else esc(req["plan_code"])
            days = plan["duration_days"] if plan else 0
        tx_short = f"{text.strip()[:12]}…{text.strip()[-8:]}"
        await update.effective_message.reply_text(
            "👑 <b><u>تم التحقق من دفعتك على البلوكيشن — باقتك مفتوحة الآن</u></b>\n"
            "<blockquote>"
            f"▎✅ المعاملة: <code>{tx_short}</code> · <b>{res.get('amount', 0):,.2f} USDT</b> موثقة على شبكة <b>{chain}</b>\n"
            f"▎💎 الباقة المفتوحة: <b>{title}</b>"
            + (f" · المدة: <b>{days} يوم</b>" if days else "")
            + "\n▎🔒 الـ TXID مُسجل في سجلك ومحجوب نهائياً من أي استخدام آخر\n"
            "▎🚀 كل مزايا الباقة تعمل الآن — افتح /menu وابدأ"
            "</blockquote>",
            parse_mode="HTML")
        try:
            from fysb.services import partners as partners_svc
            await partners_svc.notify_commission_and_vip(
                update.get_bot(), uid, req["plan_code"], appr.get("commission")
            )
        except Exception:
            pass
        if config.ADMIN_ID:
            try:
                await update.get_bot().send_message(
                    config.ADMIN_ID,
                    f"⛓ <b>دفعة USDT موثقة على البلوكيشن — تفعيل فوري</b>\n"
                    f"▎👤 المستخدم: <code>{uid}</code>\n"
                    f"▎🌐 الشبكة: <b>{chain}</b>\n"
                    f"▎🧾 الطلب: #{req['id']} · الباقة: {title} · {res.get('amount', 0):,.2f} USDT\n"
                    f"▎🔗 TXID: <code>{tx_short}</code>\n"
                    f"▎🔒 المعاملة محجوبة من إعادة الاستخدام",
                    parse_mode="HTML")
            except Exception:
                pass
        return

    # ---------- Support ticket (user) ----------
    if ud.pop("awaiting_ticket", False):
        ttype = ud.pop("ticket_type", "general")
        subject = text.split("\n")[0][:100] or "دعم عام"
        tid = await afn.run(support_service.create_ticket, uid, subject, ttype, text)
        await afn.run(audit_log.log, uid, f"support_ticket:{tid}")
        await update.effective_message.reply_text(
            f"✅ تم فتح تذكرة الدعم #{tid}. سيتم إشعارك عند رد الإدارة.")
        if config.ADMIN_ID:
            try:
                await context.bot.send_message(
                    config.ADMIN_ID,
                    f"🎫 <b>تذكرة دعم جديدة #{tid}</b>\n"
                    f"👤 المستخدم: <code>{uid}</code>\n"
                    f"🏷 النوع: {esc(ttype)}\n"
                    f"📄 الموضوع: {esc(subject)}\n\n{text[:2500]}",
                    parse_mode="HTML")
            except Exception:
                pass
        return

    # Reply inside an open ticket thread (attachment replies reuse ticket_reply_to)
    tid = ud.pop("ticket_reply_to", None)
    if tid:
        t = await afn.run(support_service.get_ticket, tid)
        if t and t["user_id"] == uid and t["status"] in ("OPEN", "ANSWERED"):
            await afn.run(support_service.add_message, tid, "user", uid, text)
            await afn.run(support_service.set_status, tid, "OPEN")
            if config.ADMIN_ID:
                try:
                    await context.bot.send_message(config.ADMIN_ID,
                                                   f"💬 رد على التذكرة #{tid} من المستخدم {uid}:\n{text[:2000]}")
                except Exception:
                    pass
            await update.effective_message.reply_text(f"✅ أُرسلت رسالتك في التذكرة #{tid}.")
            return

    await update.effective_message.reply_text("استخدم أزرار القائمة. أرسل /start لعرضها.")


# =============================================================================
#                            ADMIN GUIDED FLOWS
# =============================================================================
async def _admin_text(update: Update, context: ContextTypes.DEFAULT_TYPE, uid: int, text: str) -> bool:
    ud = context.user_data

    # ---------- v7.3.0 Admin: Add Market-Filtered Chart-Only Channel ----------
    if ud.pop("awaiting_chart_channel_add", False):
        lines = [ln.strip() for ln in text.splitlines() if ln.strip()]
        raw_cid = lines[0] if lines else ""
        custom_title = lines[1] if len(lines) > 1 else ""
        if not raw_cid or (not raw_cid.startswith("@") and not raw_cid.lstrip("-").isdigit()):
            ud["awaiting_chart_channel_add"] = True
            await update.effective_message.reply_text(
                "❌ المعرّف غير صحيح — أرسل معرف القناة مثل <code>@MyCharts</code> أو <code>-1001234567890</code>.",
                parse_mode="HTML",
            )
            return True
        from fysb.services import chart_channels as _cch
        from fysb.ui import keyboards as kb_ui
        title = custom_title
        if not title:
            try:
                tgt = int(raw_cid) if raw_cid.lstrip("-").isdigit() else raw_cid
                chat_obj = await context.bot.get_chat(tgt)
                title = getattr(chat_obj, "title", "") or getattr(chat_obj, "username", "") or raw_cid
            except Exception:
                title = raw_cid
        cid = await afn.run(_cch.add_channel, raw_cid, title, "ALL")
        await afn.run(audit_log.log, uid, f"chart_channel_added:{cid}:{raw_cid}")
        all_mkts = await afn.run(_cch.available_markets)
        rows_kb = [[kb_ui.btn("✅ 🌐 جميع الأسواق (ALL)", f"a:chartch_mkt:{cid}:ALL", style="success")]]
        row_buf = []
        for sym in all_mkts:
            row_buf.append(kb_ui.btn(sym, f"a:chartch_mkt:{cid}:{sym}", style="primary"))
            if len(row_buf) == 3:
                rows_kb.append(row_buf)
                row_buf = []
        if row_buf:
            rows_kb.append(row_buf)
        rows_kb.append([kb_ui.btn("✔️ عرض إعدادات القناة", f"a:chartch:{cid}", style="success")])
        await update.effective_message.reply_text(
            f"✅ <b><u>تمت إضافة قناة الشارت بنجاح — {esc(title)}</u></b>\n"
            f"<blockquote>▎🆔 المعرّف: <code>{esc(raw_cid)}</code>\n"
            f"▎🌐 اختر الآن <b>السوق (أو الأسواق)</b> التي ستستقبل هذه القناة شارتاتها فقط:</blockquote>",
            reply_markup=kb_ui.admin_nav(rows_kb),
            parse_mode="HTML",
        )
        return True

    # ---------- v7.2.2 Admin: Ngrok-443 Authtoken & Custom HTTPS WebApp URL ----------
    if ud.pop("awaiting_ngrok_token", False) or ud.pop("awaiting_webapp_url", False):
        from fysb.handlers import background as _bg
        from fysb.ui import keyboards as kb_ui
        from fysb.webapp import server as _wsrv
        from fysb.webapp import tunnel as _tun

        parsed_in = _tun.detect_and_parse_tunnel_input(text)
        kind = parsed_in.get("kind")

        if kind == "clear":
            await afn.run(db.set_setting, "webapp_custom_url", "")
            await afn.run(db.set_setting, "webapp_gh_pages_enabled", "0")
            await afn.run(db.set_setting, "ngrok_authtoken", "")
            await afn.run(db.set_setting, "ngrok_domain", "")
            await afn.run(db.set_setting, "cf_tunnel_token", "")
            await afn.run(db.set_setting, "pinggy_token", "")
            await _tun.stop_tunnel()
            config.WEBAPP_GH_PAGES_ENABLED = False
            config.set_runtime_webapp_url("", source="")
            await update.effective_message.reply_text(
                "✅ تمت إزالة الرابط المخصص وجميع توكنات الأنفاق وإيقاف واجهة GitHub Pages والعودة للوضع التلقائي المباشر.",
                reply_markup=kb_ui.admin_nav([[kb_ui.btn("🌐 التطبيق المصغر", "a:webapp")]]),
            )
            return True

        if kind == "custom_url":
            u_txt = parsed_in["url"]
            is_frontdoor = await afn.run(_tun.is_proxy_frontdoor_url, u_txt)
            # ALWAYS save & activate the Admin's chosen HTTPS URL directly (never hijacked by stale GH Pages flag)!
            await afn.run(db.set_setting, "webapp_custom_url", u_txt)
            await afn.run(db.set_setting, "webapp_gh_pages_enabled", "0")
            config.WEBAPP_GH_PAGES_ENABLED = False
            if not is_frontdoor:
                await _tun.stop_tunnel()
                config.set_runtime_webapp_url(u_txt, source="db_custom")
            else:
                # Keep backend Ngrok / Cloudflare / Pinggy tunnel ALIVE behind Worker/Proxy!
                config.set_runtime_webapp_url(u_txt, source="worker_proxy")
            await afn.run(audit_log.log, uid, f"webapp_custom_url:{u_txt}")

            # Non-blocking honest live health check so Admin sees immediate status
            ok_live, diag_msg = await _tun.verify_https_health(u_txt, retries=1, delay=0.5)
            cur_origin = _tun.tunnel_status().get("url") or ""
            origin_note = f"\n▎🛰 النفق الخلفي النشط: <code>{esc(cur_origin)}</code>" if cur_origin.startswith("https://") else ""
            if ok_live:
                status_line = "▎🟢 الفحص الحي (/health): <b>HTTP 200 — جاهز ويعمل 100%!</b>"
            else:
                status_line = (
                    f"▎🟡 حالة الفحص الحي حالياً: <b>{esc(diag_msg)}</b>\n"
                    "▎💡 إذا كان هذا رابط Cloudflare Worker ولم تلصق كود البروكسي بعد، اضغط <b>«☁️ كود Cloudflare Worker»</b>."
                )
            await update.effective_message.reply_text(
                f"✅ <b><u>تم حفظ وتفعيل رابط التطبيق المصغّر بنجاح!</u></b>\n"
                f"<blockquote>▎🔗 <code>{esc(u_txt)}</code>{origin_note}\n{status_line}</blockquote>",
                reply_markup=kb_ui.admin_nav([
                    [kb_ui.webapp_btn("🖥 فتح لوحة الإدارة الويب", config.webapp_page_url("/admin", base_url=u_txt), style="success")],
                    [kb_ui.webapp_btn("📱 فتح التطبيق المصغر", config.webapp_page_url("/", base_url=u_txt), style="primary")],
                    [kb_ui.btn("🌐 إعدادات التطبيق", "a:webapp")],
                ]),
                parse_mode="HTML",
            )
            return True

        if kind in ("cloudflare_token", "ngrok_token", "pinggy_token"):
            if kind == "cloudflare_token":
                await afn.run(db.set_setting, "cf_tunnel_token", parsed_in["token"])
                if parsed_in.get("url"):
                    await afn.run(db.set_setting, "webapp_custom_url", parsed_in["url"])
                    config.set_runtime_webapp_url(parsed_in["url"], source="worker_proxy")
                lbl = "☁️ Cloudflare Zero Trust Tunnel"
                await afn.run(audit_log.log, uid, "cf_tunnel_token_set")
            elif kind == "ngrok_token":
                await afn.run(db.set_setting, "ngrok_authtoken", parsed_in["token"])
                if parsed_in.get("domain"):
                    await afn.run(db.set_setting, "ngrok_domain", parsed_in["domain"])
                lbl = "🔑 Ngrok-443" + (f" ({parsed_in['domain']})" if parsed_in.get("domain") else "")
                await afn.run(audit_log.log, uid, f"ngrok_authtoken_set:dom={parsed_in.get('domain') or 'auto'}")
            else:
                await afn.run(db.set_setting, "pinggy_token", parsed_in["token"])
                lbl = "⚡ Pinggy-443 Token"
                await afn.run(audit_log.log, uid, "pinggy_token_set")

            await afn.run(db.set_setting, "webapp_auto_tunnel", "1")
            # v7.5.1: Binding a fresh tunnel token always opens directly 100% (never hijacked by stale GH Pages flag)
            await afn.run(db.set_setting, "webapp_gh_pages_enabled", "0")
            config.WEBAPP_GH_PAGES_ENABLED = False
            await update.effective_message.reply_text(
                f"⏳ تم حفظ توكن <b>{esc(lbl)}</b> — جاري التشغيل والتحقق الحي (10-20 ثانية)...",
                parse_mode="HTML",
            )
            if getattr(_bg.post_init, "_webapp_srv", None) is None:
                _bg.post_init._webapp_srv = await _wsrv.start_webapp_server()
            await _tun.stop_tunnel()
            url = await _tun.start_tunnel(port=config.WEBAPP_PORT, timeout=35.0)
            show_url = config.WEBAPP_URL or (url if (url or "").startswith("https://") else "")
            if url:
                rows_ok = []
                if show_url:
                    rows_ok.append([kb_ui.webapp_btn("🖥 فتح لوحة الإدارة الويب", config.webapp_page_url("/admin", base_url=show_url), style="success")])
                    rows_ok.append([kb_ui.webapp_btn("📱 فتح التطبيق المصغر", config.webapp_page_url("/", base_url=show_url), style="primary")])
                rows_ok.append([kb_ui.btn("🌐 إعدادات التطبيق", "a:webapp")])
                await update.effective_message.reply_text(
                    f"🎉 <b><u>تم تشغيل {esc(lbl)} بنجاح!</u></b>\n"
                    f"<blockquote>▎🛰 النفق الخلفي: <code>{esc(url)}</code>\n"
                    f"▎🌐 الرابط الفعّال: <code>{esc(show_url or 'أضف رابط الدومين عبر 🔗 لصق أي رابط HTTPS')}</code></blockquote>",
                    reply_markup=kb_ui.admin_nav(rows_ok),
                    parse_mode="HTML",
                )
            else:
                err = _tun.tunnel_status().get("last_error") or "تعذر التشغيل الفوري"
                await update.effective_message.reply_text(
                    f"⚠️ تم حفظ توكن <b>{esc(lbl)}</b> لكن فشل البدء الفوري:\n<code>{esc(err)}</code>",
                    reply_markup=kb_ui.admin_nav([[kb_ui.btn("🌐 التطبيق المصغر", "a:webapp")]]),
                    parse_mode="HTML",
                )
            return True

        # Invalid / http:// input — keep prompt active with clear guidance
        ud["awaiting_webapp_url"] = True
        await update.effective_message.reply_text(
            f"❌ {esc(parsed_in.get('error') or 'صيغة غير صالحة')}\n"
            "<i>أرسل رابط <code>https://...</code> أو توكن Cloudflare / Ngrok / Pinggy، أو أرسل <code>clear</code> للإزالة.</i>",
            parse_mode="HTML",
        )
        return True

    # ---------- Restore .db file ----------
    if ud.pop("awaiting_restore", False):
        await update.effective_message.reply_text("♻️ أرسل ملف قاعدة البيانات كمستند (document) وليس كنص.")
        return True

    # ---------- Manual signal publish ----------
    if ud.pop("awaiting_signal", False):
        try:
            p = [x.strip() for x in text.split("|")]
            if len(p) != 11:
                raise ValueError("عدد الحقول يجب أن يكون 11")
            symbol, direction, tf, entry, sl, tp1, tp2, tp3, score, strategy, reason = p
            direction = direction.upper()
            tf = tf.upper()
            entry, sl, tp1, tp2, tp3 = map(float, (entry, sl, tp1, tp2, tp3))
            score = int(score)
            strategy = strategy.upper()
            if direction not in ("BUY", "SELL") or tf not in TIMEFRAMES or not (0 <= score <= 100):
                raise ValueError("قيمة غير صحيحة (الاتجاه/الإطار/الجودة)")
            risk = abs(entry - sl)
            reward = (tp1 - entry) if direction == "BUY" else (entry - tp1)
            if risk <= 0 or reward <= 0 or reward / risk < 2:
                raise ValueError("TP1 يجب أن يكون في اتجاه الصفقة وبنسبة لا تقل عن 2R")
            if direction == "BUY" and not (sl < entry < tp1 < tp2 < tp3):
                raise ValueError("ترتيب BUY يجب أن يكون SL < ENTRY < TP1 < TP2 < TP3")
            if direction == "SELL" and not (sl > entry > tp1 > tp2 > tp3):
                raise ValueError("ترتيب SELL يجب أن يكون SL > ENTRY > TP1 > TP2 > TP3")
            from datetime import datetime, timedelta, timezone
            cutoff = (datetime.now(timezone.utc) - timedelta(hours=4)).isoformat()
            sym = instruments.normalize(symbol)
            # Birth truth (Real-Entry protocol, v6.7.1): manual signals carry the
            # same honesty as auto ones — the birth quote is stamped and an
            # already-crossed entry is born RUNNING, never «بانتظار التنفيذ».
            from fysb.engines import market_data as _md
            from fysb.engines.entry_state import birth_status as _birth
            live_px = 0.0
            try:
                px_row = await afn.run(_md.fetch_price, sym)
                live_px = float((px_row or {}).get("price") or 0.0)
            except Exception:
                live_px = 0.0
            b_status = _birth(direction, entry, live_px) if live_px > 0 else "ACTIVE"
            filled_at = config.now() if b_status == "RUNNING" else ""
            sid = await afn.run(_insert_manual_signal, sym, direction, tf, entry, sl, tp1, tp2, tp3,
                                reward / risk, score, strategy.upper(), reason, b_status, uid,
                                filled_at, live_px, cutoff)
            await afn.run(audit_log.log, uid, f"signal_create:{sid}")
            row = await afn.run(_fetch_signal_row, sid)
            from fysb.engines.signal_engine import emit_to_users
            stats = await emit_to_users(dict(row), _bot_sender(context))
            await update.effective_message.reply_text(
                f"✅ حُفظت ونُشرت الإشارة #{sid}.\n"
                f"📊 التسليم: {stats['sent']} مستلم · تجاوز الحد: {stats['skipped_quota']} · فشل: {stats['failed']}")
        except Exception as e:
            ud["awaiting_signal"] = True
            await update.effective_message.reply_text(f"❌ لم تُحفظ الإشارة: {safe(e)}\nأعد الإرسال بالتنسيق:\n"
                                                      "<code>SYMBOL | BUY/SELL | TF | ENTRY | SL | TP1 | TP2 | TP3 | SCORE | STRATEGY | السبب</code>")
        return True

    # ---------- Market add ----------
    if ud.pop("awaiting_market_add", False):
        try:
            parts = [x.strip() for x in text.split("|")]
            if len(parts) < 3:
                raise ValueError("التنسيق: SYMBOL | PROVIDER_SYMBOL | الاسم | التصنيف(اختياري)")
            symbol, provider, title = parts[0], parts[1], parts[2]
            category = parts[3].lower() if len(parts) > 3 else "forex"
            symbol = instruments.normalize(symbol)
            if not re.fullmatch(r"[A-Z0-9]{3,15}", symbol):
                raise ValueError("رمز السوق غير صالح")
            if category not in ("forex", "metal", "crypto", "index", "commodity"):
                category = "forex"
            await afn.run(_insert_market_row, symbol, title[:80], provider[:30])
            await afn.run(audit_log.log, uid, "market_added_unverified:" + symbol)
            await update.effective_message.reply_text(
                f"✅ أُضيف السوق {symbol} مغلقًا وغير موثق. استخدم زر «تحقق» في صفحة الأسواق لاختبار المزود فعلياً.")
        except Exception as e:
            ud["awaiting_market_add"] = True
            await update.effective_message.reply_text("تعذر إضافة السوق: " + safe(e))
        return True

    # ---------- Guided PLAN creation ----------
    draft = ud.get("plan_draft")
    if draft:
        return await _plan_step(update, context, uid, draft, text)

    # ---------- Guided PAYMENT METHOD ----------
    pm = ud.get("pm_draft")
    if pm:
        return await _pm_step(update, context, uid, pm, text)

    # ---------- Guided BROKER ----------
    bk = ud.get("broker_draft")
    if bk:
        return await _broker_step(update, context, uid, bk, text)

    # ---------- Support config — one field per message ----------
    field = ud.get("awaiting_support_field")
    if field:
        val = "" if text in ("-", "—", "لا") else text
        cfg = await afn.run(support_service.config_row) or {}
        cfg[field] = val.lstrip("@") if field == "username" else val
        await afn.run(support_service.set_config, cfg.get("username", ""), cfg.get("phone", ""),
                      cfg.get("channel", ""), cfg.get("group_chat", ""))
        ud.pop("awaiting_support_field", None)
        await afn.run(audit_log.log, uid, f"support_config_field:{field}")
        await update.effective_message.reply_text(
            f"✅ حُفظ الحقل <b>{esc(field)}</b>.\n"
            f"القيم الحالية: @{esc(cfg.get('username') or '—')} · {esc(cfg.get('phone') or '—')} · "
            f"{esc(cfg.get('channel') or '—')} · {esc(cfg.get('group_chat') or '—')}",
            parse_mode="HTML")
        return True

    # Legacy pipe support-config (kept for compatibility)
    if ud.pop("awaiting_support_config", False):
        try:
            parts = [x.strip() for x in text.split("|")]
            if len(parts) != 4:
                raise ValueError("أدخل أربعة حقول مفصولة بـ |")
            username, phone, channel, group = parts
            vals = ["" if x == "-" else x for x in (username, phone, channel, group)]
            await afn.run(support_service.set_config, *vals)
            await afn.run(audit_log.log, uid, "support_config_updated")
            await update.effective_message.reply_text("✅ تم حفظ بيانات الدعم.")
        except Exception as e:
            ud["awaiting_support_config"] = True
            await update.effective_message.reply_text("تعذر الحفظ: " + safe(e))
        return True

    # ---------- User search ----------
    if ud.pop("awaiting_user_search", False):
        rows = await afn.run(users_service.search, text)
        rows = [r for r in rows if r["user_id"] != config.ADMIN_ID]
        if not rows:
            await update.effective_message.reply_text("لا نتائج مطابقة.")
        else:
            lines = "\n".join(f"👤 {esc(r['full_name'] or '—')} · @{esc(r['username'] or '—')} · <code>{r['user_id']}</code>"
                              for r in rows[:10])
            await update.effective_message.reply_text("نتائج البحث:\n" + lines, parse_mode="HTML")
        return True

    # ---------- Payment rejection reason (v6.10.0: BEFORE the wallet flow —
    # the most recent admin intent wins, and the dispatcher's CLEAR_ON_NAV
    # already killed any stale flag; this order is the second wall of defense)
    pid = ud.pop("reject_payment_id", None)
    if pid:
        res = await afn.run(payments_service.reject_request, int(pid), text, uid)
        if res.get("ok"):
            await afn.run(audit_log.log, uid, f"payment_rejected:{pid}")
            p = res["payment"]
            try:
                await context.bot.send_message(p["user_id"],
                                               f"⛔ تم رفض طلب الدفع #{pid}.\nالسبب: {esc(text)}")
            except Exception:
                pass
            await update.effective_message.reply_text("✅ سُجل سبب الرفض وأُبلغ المستخدم.")
        else:
            await update.effective_message.reply_text(safe(res.get("error", "خطأ")))
        return True

    # ---------- USDT per-network wallets (admin — blockchain verification) ----------
    wallet_flow = ud.pop("awaiting_usdt_wallet", None)
    if wallet_flow:
        chain = (wallet_flow.get("chain") if isinstance(wallet_flow, dict) else "TRC20") or "TRC20"
        val = "" if text in ("-", "—", "لا") else text.strip()
        if val and not chain_verify.valid_wallet(val, chain):
            ud["awaiting_usdt_wallet"] = {"chain": chain}
            hint = ("عنوان TRC20 صحيح يبدأ بحرف T وطوله 34 خانة (أرقام وحروف لاتينية)"
                    if chain == "TRC20"
                    else "عنوان EVM صحيح يبدأ بـ 0x يليه 40 خانة سداسية عشرية")
            await update.effective_message.reply_text(f"❌ {hint} — أعد الإرسال أو أرسل - للمسح.")
            return True
        await afn.run(payments_service.set_chain_wallet, chain, val)
        await afn.run(audit_log.log, uid, f"usdt_wallet_set:{chain}" if val else f"usdt_wallet_cleared:{chain}")
        await update.effective_message.reply_text(
            (f"✅ تم ضبط محفظة USDT على شبكة <b>{chain}</b> للتحقق البلوكيشن الفوري:\n<code>{esc(val)}</code>\n"
             "كل دفعة تُتحقق على الشبكة (التأكيد + العقد + العنوان + المبلغ)، والـ TXID يُحجب بعد القبول."
             if val else f"✅ تم مسح عنوان محفظة {chain} — الشبكة تبقى مغلقة للمستخدمين حتى ضبطها."),
            parse_mode="HTML")
        return True

    # ---------- Direct plan field edit (v6.10.0 luxury editor) ----------
    field_flag = ud.pop("awaiting_plan_field", None)
    if field_flag:
        try:
            code, field = (field_flag.split(":", 1) + [""])[:2]
            from fysb.handlers.admin_core import plan_field_apply
            msg = await afn.run(plan_field_apply, code, field, text)
            await afn.run(audit_log.log, uid, f"plan_field:{code}:{field}:{text[:20]}")
            await update.effective_message.reply_text(f"✅ {esc(msg)}", parse_mode="HTML")
        except ValueError as e:
            ud["awaiting_plan_field"] = field_flag
            await update.effective_message.reply_text(f"❌ {safe(e)} — أعد الإرسال.")
        return True

    # ---------- Custom guided-plan values (days / quota / analyses / score) ----------
    custom_flag = ud.pop("awaiting_plan_custom", None)
    if custom_flag:
        # custom_flag = "days"|"quota"|"analyses"|"score"
        limits = {"days": (0, 3650, "المدة بالأيام (0 = دائم)"),
                  "quota": (1, 1000, "حد الإشارات اليومي"),
                  "analyses": (0, 1000, "حد التحليلات اليومي"),
                  "score": (40, 100, "الجودة الدنيا (40-100)")}
        lo, hi, label = limits.get(custom_flag, (0, 3650, "القيمة"))
        try:
            val = int(float(text.replace(",", "")))
            if not (lo <= val <= hi):
                raise ValueError(f"{label}: بين {lo} و {hi}")
            d = ud.get("plan_draft")
            if not d:
                raise ValueError("انتهت جلسة الإنشاء — ابدأ من «إنشاء باقة جديدة»")
            key = {"days": "days", "quota": "quota", "analyses": "analyses", "score": "min_score"}[custom_flag]
            d[key] = val
            stage_after = {"days": "score", "score": "quota", "quota": "analyses", "analyses": "markets"}[custom_flag]
            d["stage"] = stage_after
            await update.effective_message.reply_text(f"✅ {label}: <b>{val}</b>", parse_mode="HTML")
        except ValueError as e:
            ud["awaiting_plan_custom"] = custom_flag
            await update.effective_message.reply_text(f"❌ {safe(e)} — أعد الإرسال رقماً.")
        return True

    # ---------- Broadcast text ----------
    if ud.pop("awaiting_broadcast_text", False):
        audience = ud.get("broadcast_audience", "all")
        plan = ud.get("broadcast_plan", "")
        try:
            from fysb.services import broadcast as broadcast_service
            bid = broadcast_service.create(uid, audience, plan, text)
            ud["broadcast_draft"] = bid
            ud["awaiting_broadcast_photo"] = True   # a photo may now follow
            targets = broadcast_service.audience_ids(audience, plan)
            from telegram import InlineKeyboardMarkup
            from fysb.ui import keyboards as kb
            # v6.8.1 — the preview IS the final look: the plain text is wrapped
            # in the royal announcement frame (never parsed as the admin's own
            # HTML), so what the admin confirms is exactly what users receive.
            await update.effective_message.reply_text(
                f"📋 <b>معاينة الإعلان #{bid}</b> (هذا شكله النهائي لدى المستخدمين)\n\n"
                + broadcast_service.announcement_text(text, limit=2600) + "\n\n"
                f"🎯 الجمهور: {esc(audience)}{' · ' + esc(plan) if plan else ''}\n"
                f"👥 عدد المستلمين: <b>{len(targets)}</b>\n\n"
                "🖼 أرسل صورة الآن لإرفاقها بالإعلان (اختيارية)، أو أكد الإرسال.",
                parse_mode="HTML",
                reply_markup=InlineKeyboardMarkup([
                    [kb.btn("✅ تأكيد الإرسال", f"a:bcgo:{bid}", style="success")],
                    [kb.btn("🚫 إلغاء", f"a:bccancel:{bid}", style="danger")],
                    [kb.btn("↩️ الإعلانات", "a:broadcast", style="primary")],
                ]))
        except Exception as e:
            # The broadcast flow must NEVER die with the generic error card —
            # the admin always gets the exact reason and the flow stays open.
            ud["awaiting_broadcast_text"] = True
            await update.effective_message.reply_text(
                f"❌ تعذر تجهيز الإعلان: {safe(e)}\nأعد إرسال النص (نص عادي — لا حاجة لأي وسوم).")
        return True

    # ---------- Free daily signals quota ----------
    if ud.pop("awaiting_free_quota", False):
        try:
            val = int(float(text))
            if not (1 <= val <= 10):
                raise ValueError("القيمة يجب أن تكون بين 1 و 10")
            db.set_setting("free_daily_signals", val)
            await afn.run(audit_log.log, uid, f"free_quota_set:{val}")
            await update.effective_message.reply_text(f"✅ أصبح الحد اليومي للباقة المجانية: <b>{val}</b> إشارة.", parse_mode="HTML")
        except ValueError as e:
            ud["awaiting_free_quota"] = True
            await update.effective_message.reply_text(f"❌ {safe(e)} — أرسل رقماً بين 1 و 10.")
        return True

    # ---------- Demo start balance (admin global default) ----------
    if ud.pop("awaiting_demo_balance", False):
        try:
            val = float(text.replace(",", ""))
            if not (10 <= val <= 10_000_000):
                raise ValueError("الرصيد يجب أن يكون بين 10 و 10,000,000")
            db.set_setting("demo_start_balance", val)
            await afn.run(audit_log.log, uid, f"demo_balance_set:{val:g}")
            await update.effective_message.reply_text(
                f"✅ أصبح الرصيد الابتدائي للحسابات التجريبية الجديدة: <b>{val:,.2f} USD</b>.\n"
                "<i>الحسابات القائمة تُعدل من حساب كل مستخدم أو زر تصفير.</i>", parse_mode="HTML")
        except ValueError as e:
            ud["awaiting_demo_balance"] = True
            await update.effective_message.reply_text(f"❌ {safe(e)} — أرسل رقماً (مثال: 10000).")
        return True

    # ---------- Signal expiry hours ----------
    if ud.pop("awaiting_expiry_hours", False):
        try:
            val = int(float(text))
            if not (2 <= val <= 720):
                raise ValueError("الصلاحية يجب أن تكون بين 2 و 720 ساعة")
            db.set_setting("signal_expiry_hours", val)
            await afn.run(audit_log.log, uid, f"signal_expiry_set:{val}")
            await update.effective_message.reply_text(f"✅ صلاحية الإشارة الجديدة: <b>{val} ساعة</b>.", parse_mode="HTML")
        except ValueError as e:
            ud["awaiting_expiry_hours"] = True
            await update.effective_message.reply_text(f"❌ {safe(e)} — أرسل رقماً بالساعات (مثال: 72).")
        return True

    # ---------- Broadcast-channel / group add ----------
    if ud.get("awaiting_chan_add"):
        from fysb.handlers import admin_channels
        await admin_channels.handle_add_input(update, context, uid, text)
        return True

    if ud.get("awaiting_partner_add"):
        from fysb.handlers import admin_partners
        await admin_partners.handle_partner_add_input(update, context, uid, text)
        return True

    if ud.get("awaiting_partner_limit"):
        from fysb.handlers import admin_partners
        await admin_partners.handle_partner_limit_input(update, context, uid, text)
        return True

    return False


# =============================================================================
# Guided PLAN creation — CODE → title → price → currency → days → score →
# quota → markets → timeframes → strategies → saved (all button-driven)
# =============================================================================
async def _plan_step(update: Update, context: ContextTypes.DEFAULT_TYPE, uid: int, d: dict, text: str) -> bool:
    step = d.get("step")
    try:
        if step == "code":
            code = text.upper().strip()
            if not re.fullmatch(r"[A-Z][A-Z0-9_]{1,15}", code):
                raise ValueError("رمز الباقة: حروف لاتينية كبيرة وأرقام فقط (مثال VIP)")
            if await afn.run(_plan_code_exists, code):
                raise ValueError(f"الرمز {code} موجود — استخدم المحرر لتعديله أو اختر رمزاً آخر")
            d["code"] = code
            d["step"] = "title"
            await update.effective_message.reply_text(f"✅ الرمز: <b>{esc(code)}</b>\n✍️ أرسل <b>عنوان</b> الباقة (مثال: VIP Plan):", parse_mode="HTML")
        elif step == "title":
            d["title"] = text[:60]
            d["step"] = "price"
            await update.effective_message.reply_text(f"✅ العنوان: <b>{esc(d['title'])}</b>\n✍️ أرسل <b>السعر</b> رقماً فقط (مثال: 99 أو 0 للمجانية):", parse_mode="HTML")
        elif step == "price":
            price = float(text.replace(",", "."))
            if not (0 <= price <= 100000):
                raise ValueError("السعر خارج النطاق")
            d["price"] = price
            d["step"] = "currency"
            from telegram import InlineKeyboardMarkup
            from fysb.ui import keyboards as kb_ui
            kb_mk = InlineKeyboardMarkup([[kb_ui.btn(c, f"a:plancur:{c}", style="primary") for c in ("USD", "YER")],
                                          [kb_ui.btn(c, f"a:plancur:{c}", style="primary") for c in ("SAR", "EUR")]])
            await update.effective_message.reply_text(f"✅ السعر: <b>{price:g}</b>\n💎 اختر <b>العملة</b>:", parse_mode="HTML", reply_markup=kb_mk)
        else:
            ud.pop("plan_draft", None)
            await update.effective_message.reply_text("أعد إنشاء الباقة من قسم الباقات.")
    except ValueError as e:
        await update.effective_message.reply_text(f"❌ {safe(e)} — أعد الإرسال.")
    return True


def _fetch_plan_row(plan_code: str):
    """(v6.11.0 thread-offload helper) plan row for the on-chain success card."""
    with db.conn() as c:
        return c.execute("SELECT * FROM plans WHERE code=?", (plan_code,)).fetchone()


def _insert_manual_signal(sym, direction, tf, entry, sl, tp1, tp2, tp3, rr, score,
                          strategy, reason, b_status, uid, filled_at, live_px, cutoff) -> int:
    """(v6.11.0 thread-offload helper) duplicate-guarded manual signal insert."""
    with db.conn() as c:
        dup = c.execute("""SELECT id FROM signals WHERE symbol=? AND direction=? AND timeframe=? AND created_at>=?""",
                        (sym, direction, tf, cutoff)).fetchone()
        if dup:
            raise ValueError(f"توجد إشارة مطابقة حديثاً #{dup['id']} — منع التكرار")
        cur = c.execute("""INSERT INTO signals(symbol,direction,timeframe,entry,stop,tp1,tp2,tp3,targets,rr,score,
                           strategy,reason,status,source,created_by,created_at,data_source,data_time,filled_at,live_px)
                           VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,'MANUAL',?,?,?,'MANUAL','',?,?)""",
                        (sym, direction, tf, entry, sl, tp1, tp2, tp3,
                         f"{tp1},{tp2},{tp3}", rr, score, strategy, reason,
                         b_status, uid, config.now(), filled_at, live_px))
        return cur.lastrowid


def _fetch_signal_row(sid: int):
    """(v6.11.0 thread-offload helper) full signal row after insert."""
    with db.conn() as c:
        return c.execute("SELECT * FROM signals WHERE id=?", (sid,)).fetchone()


def _insert_market_row(symbol: str, title: str, provider: str) -> None:
    """(v6.11.0 thread-offload helper) unverified market-catalog upsert."""
    with db.conn() as c:
        c.execute("""INSERT INTO market_catalog(symbol,title,provider_symbol,enabled,verified,created_at,updated_at)
                     VALUES(?,?,?,0,0,?,?)
                     ON CONFLICT(symbol) DO UPDATE SET title=excluded.title,
                     provider_symbol=excluded.provider_symbol,enabled=0,verified=0,updated_at=excluded.updated_at""",
                  (symbol, title, provider, config.now(), config.now()))


def _plan_code_exists(code: str) -> bool:
    """(v6.11.0 thread-offload helper) guided-plan uniqueness check."""
    with db.conn() as c:
        return c.execute("SELECT 1 FROM plans WHERE code=?", (code,)).fetchone() is not None


def _fetch_plans_list():
    """(v6.11.0 thread-offload helper) plan picker rows for PM linking."""
    with db.conn() as c:
        return c.execute("SELECT code,title FROM plans ORDER BY price").fetchall()


def _insert_broker(adapter_key: str, title: str, config_json: str) -> int:
    """(v6.11.0 thread-offload helper) CONFIGURED broker adapter insert."""
    with db.conn() as c:
        cur = c.execute("""INSERT INTO broker_adapters(key,title,status,enabled,config_json,created_at,updated_at)
                           VALUES(?,?, 'CONFIGURED', 0, ?, ?, ?)""",
                        (adapter_key, title, config_json, config.now(), config.now()))
        return cur.lastrowid


def _broadcast_set_photo(bid: int, file_id: str) -> None:
    """(v6.11.0 thread-offload helper) attach photo file_id to broadcast."""
    with db.conn() as c:
        c.execute("UPDATE broadcasts SET file_id=?, has_photo=1 WHERE id=?", (file_id, bid))


def plan_persist(d: dict) -> None:
    with db.conn() as c:
        c.execute("""INSERT INTO plans(code,title,price,currency,duration_days,min_score,enabled,
                     max_signals_per_day,markets,timeframes,strategies,max_analyses_per_day)
                     VALUES(?,?,?,?,?,?,1,?,?,?,?,?)
                     ON CONFLICT(code) DO UPDATE SET title=excluded.title,price=excluded.price,
                     currency=excluded.currency,duration_days=excluded.duration_days,
                     min_score=excluded.min_score,max_signals_per_day=excluded.max_signals_per_day,
                     markets=excluded.markets,timeframes=excluded.timeframes,strategies=excluded.strategies""",
                  (d["code"], d["title"], d["price"], d.get("currency", "USD"), int(d.get("days", 30)),
                   int(d.get("min_score", 70)), int(d.get("quota", 10)),
                   d.get("markets", "XAUUSD"), d.get("timeframes", "M15,H1"),
                   d.get("strategies", "ICT"), int(d.get("analyses", 3))))


# =============================================================================
# Guided PAYMENT METHOD — preset → account → instructions → plans
# (country is auto-filled from the preset — no redundant typing step)
# =============================================================================
async def _pm_step(update: Update, context: ContextTypes.DEFAULT_TYPE, uid: int, d: dict, text: str) -> bool:
    step = d.get("step")
    if step == "name":
        d["title"] = text[:60]
        d.setdefault("country", "عالمي")
        d["step"] = "account"
        await update.effective_message.reply_text(
            f"✅ الاسم: <b>{esc(d['title'])}</b>\n✍️ أرسل <b>بيانات الحساب</b> (رقم المحفظة / عنوان المحفظة / IBAN):",
            parse_mode="HTML")
    elif step == "account":
        d["account_ref"] = text[:120]
        d["step"] = "instructions"
        await update.effective_message.reply_text(
            f"✅ الحساب: <code>{esc(d['account_ref'])}</code>\n✍️ أرسل <b>تعليمات الدفع</b> للمستخدم:",
            parse_mode="HTML")
    elif step == "instructions":
        d["instructions"] = text[:300]
        d["step"] = "plans"
        from telegram import InlineKeyboardMarkup
        from fysb.ui import keyboards as kb_ui
        plans = await afn.run(_fetch_plans_list)
        kb_rows = [[kb_ui.btn(("✅ " if p["code"] in d.get("linked", []) else "▫️ ") + p["title"],
                              f"a:pmplan:{p['code']}",
                              style="success" if p["code"] in d.get("linked", []) else "primary")] for p in plans]
        kb_rows.append([kb_ui.btn("💾 حفظ الوسيلة", "a:pmsave", style="success"),
                        kb_ui.btn("🗑 إلغاء", "a:pmcancel", style="danger")])
        await update.effective_message.reply_text(
            f"✅ التعليمات حُفظت.\n💎 اختر <b>الباقات المرتبطة</b> (اضغط للتبديل) ثم احفظ:",
            parse_mode="HTML", reply_markup=InlineKeyboardMarkup(kb_rows))
    else:
        context.user_data.pop("pm_draft", None)
        await update.effective_message.reply_text("أعد إضافة الوسيلة من قسم وسائل الدفع.")
    return True


# =============================================================================
# Guided BROKER — preset → name → api key → secret → account id (masked)
# =============================================================================
async def _broker_step(update: Update, context: ContextTypes.DEFAULT_TYPE, uid: int, d: dict, text: str) -> bool:
    step = d.get("step")
    if step == "name":
        d["name"] = text[:60]
        d["step"] = "api_key"
        await update.effective_message.reply_text(
            f"✅ الاسم: <b>{esc(d['name'])}</b>\n🔑 أرسل <b>API Key / Token</b>:\n<i>(لن يُعرض مرة أخرى ولا يُطبع في أي سجل)</i>",
            parse_mode="HTML")
    elif step == "api_key":
        d["api_key"] = text[:200]
        d["step"] = "api_secret"
        await update.effective_message.reply_text(
            "🔐 أرسل <b>API Secret</b> — أو أرسل <code>-</code> إن كان غير مطلوب:", parse_mode="HTML")
    elif step == "api_secret":
        d["api_secret"] = "" if text == "-" else text[:200]
        d["step"] = "account_id"
        await update.effective_message.reply_text(
            "🆔 أرسل <b>معرّف الحساب</b> (Account ID / Login) — أو <code>-</code> للتخطي:", parse_mode="HTML")
    elif step == "account_id":
        d["account_id"] = "" if text == "-" else text[:60]
        # Save encrypted at rest (ENC1 Encrypt-then-MAC; never rendered)
        from fysb.services import secrets as secrets_service
        cfg = {"base_url": d.get("base_url", ""), "api_key": d["api_key"],
               "api_secret": d["api_secret"], "account_id": d["account_id"]}
        bid = await afn.run(_insert_broker, d["type"], d["name"], secrets_service.encrypt_json(cfg))
        context.user_data.pop("broker_draft", None)
        await afn.run(audit_log.log, uid, f"broker_added:{d['type']}:{bid}")
        await update.effective_message.reply_text(
            f"🏦 <b>سُجل الوسيط #{bid}</b> — {esc(d['name'])} ({esc(d['type'])})\n"
            f"🔑 المفاتيح محفوظة مشفرة في قاعدة البيانات ولا تعرض أبداً.\n"
            f"▶️ الخطوة التالية: زر «فحص اتصال حقيقي» ثم «اعتماد» من قسم الوسطاء.",
            parse_mode="HTML")
    else:
        context.user_data.pop("broker_draft", None)
        await update.effective_message.reply_text("أعد إضافة الوسيط من قسم الوسطاء.")
    return True


def broker_test_connectivity(cfg: dict, adapter_key: str) -> tuple[bool, str]:
    """REAL authenticated connectivity test — no fake PASS.

    The public ping trick is gone: fake API keys can no longer score HTTP 200.
    Every preset type now verifies its OWN credentials against its OWN
    authenticated endpoint (signed requests for exchanges, Bearer for MetaAPI,
    strict URL validation for custom adapters). Sanitized Arabic messages only;
    credentials are never echoed back.
    """
    import hashlib
    import hmac as hmac_mod
    import time as _t
    import base64

    preset = BROKER_PRESETS.get(adapter_key, {"base_url": cfg.get("base_url", ""), "test": "url"})
    base = (cfg.get("base_url") or preset.get("base_url") or "").rstrip("/")
    api_key = (cfg.get("api_key") or "").strip()
    api_secret = (cfg.get("api_secret") or "").strip()
    account_id = (cfg.get("account_id") or "").strip()

    if not api_key:
        return False, "لا يوجد API Key محفوظ — أعد إضافة الوسيط بالمفاتيح الحقيقية"
    if adapter_key in ("BINANCE", "BYBIT", "OKX") and not api_secret:
        return False, "API Secret مفقود — المصادقة الموقّعة تتطلب المفتاح والسر معاً"

    try:
        if adapter_key == "BINANCE":
            # Signed /api/v3/account — a fake key/secret gets HTTP 401, never a pass.
            query = f"timestamp={int(_t.time() * 1000)}&recvWindow=10000"
            sig = hmac_mod.new(api_secret.encode(), query.encode(), hashlib.sha256).hexdigest()
            url = f"{base}/api/v3/account?{query}&signature={sig}"
            req = urllib.request.Request(url, headers={"X-MBX-APIKEY": api_key,
                                                       "User-Agent": f"FYSB/{config.VERSION}"})
            with urllib.request.urlopen(req, timeout=15) as r:
                body = json.loads(r.read().decode())
                can_trade = bool(body.get("canTrade"))
                n_assets = len(body.get("balances") or [])
                return True, ("المصادقة الموقّعة سليمة — الحساب مرتبط فعلياً"
                              + (" وقابل للتداول" if can_trade else " (قراءة فقط)"))

        if adapter_key == "BYBIT":
            # Signed /v5/account/wallet-balance — invalid keys return retCode 10003/401.
            ts = str(int(_t.time() * 1000))
            recv = "10000"
            query = "accountType=UNIFIED"
            sign_payload = ts + api_key + recv + query
            sig = hmac_mod.new(api_secret.encode(), sign_payload.encode(), hashlib.sha256).hexdigest()
            url = f"{base}/v5/account/wallet-balance?{query}"
            req = urllib.request.Request(url, headers={
                "X-BAPI-API-KEY": api_key, "X-BAPI-TIMESTAMP": ts,
                "X-BAPI-RECV-WINDOW": recv, "X-BAPI-SIGN": sig,
                "User-Agent": f"FYSB/{config.VERSION}"})
            with urllib.request.urlopen(req, timeout=15) as r:
                body = json.loads(r.read().decode())
                code = int(body.get("retCode", -1))
                if code == 0:
                    return True, "المصادقة الموقّعة سليمة — جُلب رصيد الحساب فعلياً من الوسيط"
                return False, f"رفض الوسيط المصادقة (رمز {code}) — تحقق من المفاتيح وصلاحيتها"

        if adapter_key == "OKX":
            ts = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%f")[:-3] + "Z"
            path = "/api/v5/account/balance"
            what = ts + "GET" + path
            sig = base64.b64encode(hmac_mod.new(api_secret.encode(), what.encode(), hashlib.sha256).digest()).decode()
            req = urllib.request.Request(base + path, headers={
                "OK-ACCESS-KEY": api_key, "OK-ACCESS-SIGN": sig,
                "OK-ACCESS-TIMESTAMP": ts, "OK-ACCESS-PASSPHRASE": cfg.get("passphrase", ""),
                "User-Agent": f"FYSB/{config.VERSION}"})
            with urllib.request.urlopen(req, timeout=15) as r:
                body = json.loads(r.read().decode())
                if str(body.get("code")) == "0":
                    return True, "المصادقة الموقّعة سليمة — جُلب رصيد الحساب فعلياً من الوسيط"
                return False, "رفض الوسيط المصادقة — تحقق من المفاتيح وكلمة المرور الثانية"

        if preset.get("test") == "auth" and base:
            # MetaAPI-style Bearer probe against the REAL authenticated endpoint
            req = urllib.request.Request(base + "/users/current",
                                         headers={"Authorization": "Bearer " + api_key,
                                                  "User-Agent": f"FYSB/{config.VERSION}"})
            with urllib.request.urlopen(req, timeout=15) as r:
                return True, "المصادقة سليمة (Bearer token صالح ومقبول من الخادم)"

        # CUSTOM adapter: strict validation, honest result
        if not base:
            return False, "لا يوجد عنوان أساس — أضف رابط الوسيط (https://...) ثم أعد الفحص"
        if not base.startswith("https://"):
            return False, "العنوان يجب أن يبدأ بـ https:// — اتصال غير مشفر مرفوض"
        host = urllib.parse.urlparse(base).hostname or ""
        if not host or "." not in host:
            return False, "عنوان غير صالح — أدخل رابط خادم وسيط حقيقي (مثال: https://api.broker.com)"
        if not account_id:
            return False, "معرّف الحساب مفقود — لا يمكن توثيق وسيط بلا رقم حساب"
        req = urllib.request.Request(base, method="GET",
                                     headers={"User-Agent": f"FYSB/{config.VERSION}"})
        with urllib.request.urlopen(req, timeout=15) as r:
            return True, f"الخادم يستجيب (HTTP {r.status}) — التحقق الأساسي للعنوان سليم (بيانات الاعتماد تُختبر وقت التنفيذ)"
    except urllib.error.HTTPError as e:
        if e.code in (401, 403):
            return False, "المفاتيح مرفوضة من الوسيط (HTTP 401/403) — بيانات وهمية أو منتهية لا تُعتمد"
        if e.code in (404, 410):
            return False, "نقطة المصادقة غير موجودة على هذا الخادم (HTTP 404) — تحقق من نوع الوسيط والعنوان"
        return False, f"رفض الخادم (HTTP {e.code}) — راجع العنوان والمفاتيح"
    except Exception as e:
        return False, "فشل الاتصال بالوسيط: " + type(e).__name__


def _bot_sender(context):
    """Adapter matching signal_engine.emit_to_users signature.
    Returns the sent Message so delivery refs (chat_id/message_id) get pinned —
    the tracker then edits this exact original card live."""
    return bot_sender_from(context.bot)


def bot_sender_from(bot):
    """Bot-object variant of _bot_sender (used by signal_now + watchers).
    Carries `_bot` so emit_to_users can broadcast to enabled channels too."""

    async def send(uid: int, text: str):
        return await bot.send_message(uid, text, parse_mode="HTML")

    send._bot = bot
    return send


async def on_photo(update: Update, context: ContextTypes.DEFAULT_TYPE):
    uid = update.effective_user.id
    ud = context.user_data
    photo = update.effective_message.photo[-1]
    caption = (update.effective_message.caption or "").strip()

    # v7.1.0 Admin: Academy lesson photo attachment
    if uid == config.ADMIN_ID and (
        ud.get("awaiting_acad_media_for")
        or (ud.get("acad_new_draft") or {}).get("step") == "media"
    ):
        from fysb.handlers import academy_growth
        if await academy_growth.handle_growth_media_input(update, context, uid):
            return

    # Admin: broadcast photo
    if uid == config.ADMIN_ID and ud.get("awaiting_broadcast_photo"):
        bid = ud.get("broadcast_draft", 0)
        ud["awaiting_broadcast_photo"] = False
        from fysb.services import broadcast as broadcast_service
        b = await afn.run(broadcast_service.get, bid) if bid else None
        if b:
            await afn.run(_broadcast_set_photo, bid, photo.file_id)
            targets = await afn.run(broadcast_service.audience_ids, b["audience"], b["plan_code"])
            from telegram import InlineKeyboardMarkup
            from fysb.ui import keyboards as kb_ui
            await update.effective_message.reply_text(
                f"✅ أُرفقت الصورة بالإعلان #{bid}.\nعدد المستلمين: {len(targets)}",
                reply_markup=InlineKeyboardMarkup([
                    [kb_ui.btn("🚀 إرسال الآن", f"a:bcgo:{bid}", style="success")],
                    [kb_ui.btn("🚫 إلغاء", f"a:bccancel:{bid}", style="danger")],
                    [kb_ui.btn("↩️ الإعلانات", "a:broadcast", style="primary")],
                ]))
        else:
            await update.effective_message.reply_text("⚠️ لا يوجد مسودة إعلان — ابدأ من قسم الإعلانات.")
        return

    # User: payment receipt
    pid = ud.pop("awaiting_receipt", None)
    if pid:
        ok = await afn.run(payments_service.attach_receipt, int(pid), uid, photo.file_id)
        if ok:
            preq = await afn.run(payments_service.get_request, int(pid))
            plan_code = (preq["plan_code"] if preq else "") or "—"
            plan_row = await afn.run(_fetch_plan_row, plan_code) if plan_code not in ("—", "ACADEMY") else None
            plan_title = (
                "🎓 أكاديمية ForexYemeni الاحترافية"
                if plan_code.upper() == "ACADEMY"
                else (esc(plan_row["title"]) if plan_row else esc(plan_code))
            )
            plan_cur = esc(plan_row["currency"]) if plan_row else "USD"
            amt = float(preq["amount"] or 0) if preq else 0.0
            method_txt = esc((preq["method"] if preq else "") or "MANUAL")
            tg_u = update.effective_user
            u_name = esc((tg_u.full_name or tg_u.username or f"User {uid}") if tg_u else f"User {uid}")
            u_handle = f" (@{esc(tg_u.username)})" if (tg_u and tg_u.username) else ""

            await update.effective_message.reply_text(
                f"✅ <b><u>تم استلام إيصال الطلب #{pid} بنجاح!</u></b>\n"
                "━━━━━━━━━━━━━━━━━━\n"
                "<blockquote>"
                f"▎💎 الباقة: <b>{plan_title}</b>\n"
                f"▎💰 المبلغ: <b>{amt:g} {plan_cur}</b> · الوسيلة: <b>{method_txt}</b>\n"
                "▎⏳ تم إرسال الإيصال فوراً إلى الإدارة للمراجعة — سيصلك إشعار لحظي فور الاعتماد والتفعيل."
                "</blockquote>",
                parse_mode="HTML",
            )
            if config.ADMIN_ID:
                from telegram import InlineKeyboardMarkup
                from fysb.ui import keyboards as kb_ui
                adm_caption = (
                    f"🧾 <b><u>إيصال دفع جديد بانتظار الاعتماد — طلب #{pid}</u></b>\n"
                    "━━━━━━━━━━━━━━━━━━\n"
                    "<blockquote>"
                    f"▎👤 المستخدم: <b>{u_name}</b>{u_handle}\n"
                    f"▎🆔 المعرّف: <code>{uid}</code>\n"
                    f"▎💎 الباقة: <b>{plan_title}</b> (<code>{esc(plan_code)}</code>)\n"
                    f"▎💰 المبلغ: <b>{amt:g} {plan_cur}</b> · الوسيلة: <b>{method_txt}</b>\n"
                    "▎📌 الحالة: <b>⏳ بانتظار قرارك الفوري</b>"
                    "</blockquote>"
                )
                adm_markup = InlineKeyboardMarkup([
                    [
                        kb_ui.btn("✅ قبول وتفعيل الباقة فوراً", f"a:approvepay:{pid}", style="success"),
                        kb_ui.btn("❌ رفض الطلب", f"a:rejectpay:{pid}", style="danger"),
                    ],
                    [
                        kb_ui.btn(f"🧾 تفاصيل الطلب #{pid}", f"a:paymentdetail:{pid}", style="primary"),
                        kb_ui.btn("👤 ملف المستخدم", f"a:userdetail:{uid}", style="primary"),
                    ],
                ])
                try:
                    await context.bot.send_photo(
                        config.ADMIN_ID,
                        photo.file_id,
                        caption=adm_caption,
                        parse_mode="HTML",
                        reply_markup=adm_markup,
                    )
                except Exception:
                    try:
                        await context.bot.send_photo(
                            config.ADMIN_ID,
                            photo.file_id,
                            caption=adm_caption,
                            parse_mode="HTML",
                            reply_markup=kb_ui.strip_button_styles(adm_markup),
                        )
                    except Exception:
                        pass
        else:
            await update.effective_message.reply_text("⚠️ الطلب غير موجود أو تمت مراجعته مسبقاً.")
        return

    # User: attachment to an open ticket reply
    tid = ud.get("ticket_reply_to")
    if tid:
        await afn.run(support_service.add_message, int(tid), "user", uid, caption or "📎 مرفق", file_id=photo.file_id)
        if config.ADMIN_ID:
            try:
                await context.bot.send_photo(config.ADMIN_ID, photo.file_id,
                                             caption=f"📎 مرفق في التذكرة #{tid} من المستخدم {uid}")
            except Exception:
                pass
        await update.effective_message.reply_text(f"✅ أُضيف المرفق للتذكرة #{tid}.")
        return


async def on_media(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """v7.1.0 — Handle Admin Video / Audio / Voice / PDF (Document) uploads for Academy lessons."""
    uid = update.effective_user.id
    if uid == config.ADMIN_ID:
        from fysb.handlers import academy_growth
        if await academy_growth.handle_growth_media_input(update, context, uid):
            return

