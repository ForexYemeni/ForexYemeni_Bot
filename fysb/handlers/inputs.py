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

from fysb import config, db, instruments
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
              "awaiting_expiry_hours", "awaiting_chan_add")


def clear_flows(ud: dict) -> None:
    """Start a new flow cleanly — no cross-flow state leakage."""
    for k in DRAFT_KEYS:
        ud.pop(k, None)


async def on_text(update: Update, context: ContextTypes.DEFAULT_TYPE):
    uid = update.effective_user.id
    text = (update.effective_message.text or "").strip()
    ud = context.user_data

    # ---------- Admin flows ----------
    if uid == config.ADMIN_ID:
        handled = await _admin_text(update, context, uid, text)
        if handled:
            return

    # ---------- User: own demo balance ----------
    if ud.pop("awaiting_demo_bal_user", False):
        try:
            val = float(text.replace(",", ""))
            if not (10 <= val <= 10_000_000):
                raise ValueError("الرصيد يجب أن يكون بين 10 و 10,000,000")
            from fysb.engines import demo as demo_engine
            demo_engine.set_balance(uid, val)
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
        req = payments_service.get_request(pid)
        if not req or req["user_id"] != uid or req["status"] != "PENDING":
            await update.effective_message.reply_text("⚠️ الطلب غير متاح للتحقق (مُراجع مسبقاً أو غير موجود).")
            return
        chain = (ud.pop("usdt_chain", "") or (req.get("chain") if isinstance(req, dict) else "") or "TRC20").upper()
        used = payments_service.txid_used(text.strip())
        if used:
            await update.effective_message.reply_text(
                f"🔒 هذا TXID مُستخدم مسبقاً في الطلب #{used} — المعاملة محجوبة نهائياً ولا تُقبل مرتين.")
            return
        wallet = payments_service.chain_wallet(chain)
        if not wallet:
            await update.effective_message.reply_text(
                f"⛓ لم يُضبَط عنوان محفظة USDT على شبكة {chain} بعد — أرسل صورة الإيصال للمراجعة اليدوية أو تواصل مع الدعم.")
            return
        await update.effective_message.reply_text(f"⛓ جارٍ فحص المعاملة على شبكة {chain} (التأكيد + العنوان + المبلغ)…")
        res = await asyncio.to_thread(chain_verify.verify_usdt,
                                      text.strip(), wallet, float(req["amount"] or 0), chain)
        if not res.get("ok"):
            ud["awaiting_usdt_txid"] = pid
            ud["usdt_chain"] = chain
            await update.effective_message.reply_text(
                f"❌ <b>فشل التحقق البلوكيشن</b>\n{res.get('reason', '')}\n\n"
                "أعد إرسال TXID صحيحاً، أو أرسل صورة الإيصال للمراجعة اليدوية.",
                parse_mode="HTML")
            return
        bound = payments_service.attach_txid(pid, uid, text.strip(), chain)
        if not bound.get("ok"):
            ud.pop("awaiting_usdt_txid", None)
            await update.effective_message.reply_text("🔒 " + bound.get("error", ""))
            return
        appr = payments_service.approve_request(pid, actor=0)
        ud.pop("awaiting_usdt_txid", None)
        if not appr.get("ok"):
            await update.effective_message.reply_text("⚠️ المعاملة موثقة ومسجلة، لكن التفعيل أعاد: "
                                                      + safe(appr.get("error", "خطأ")) + " — تواصل مع الدعم.")
            return
        with db.conn() as c:
            plan = c.execute("SELECT * FROM plans WHERE code=?", (req["plan_code"],)).fetchone()
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
        tid = support_service.create_ticket(uid, subject, ttype, text)
        audit_log.log(uid, f"support_ticket:{tid}")
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
        t = support_service.get_ticket(tid)
        if t and t["user_id"] == uid and t["status"] in ("OPEN", "ANSWERED"):
            support_service.add_message(tid, "user", uid, text)
            support_service.set_status(tid, "OPEN")
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
                live_px = float((_md.fetch_price(sym) or {}).get("price") or 0.0)
            except Exception:
                live_px = 0.0
            b_status = _birth(direction, entry, live_px) if live_px > 0 else "ACTIVE"
            filled_at = config.now() if b_status == "RUNNING" else ""
            with db.conn() as c:
                dup = c.execute("""SELECT id FROM signals WHERE symbol=? AND direction=? AND timeframe=? AND created_at>=?""",
                                (sym, direction, tf, cutoff)).fetchone()
                if dup:
                    raise ValueError(f"توجد إشارة مطابقة حديثاً #{dup['id']} — منع التكرار")
                cur = c.execute("""INSERT INTO signals(symbol,direction,timeframe,entry,stop,tp1,tp2,tp3,targets,rr,score,
                                   strategy,reason,status,source,created_by,created_at,data_source,data_time,filled_at,live_px)
                                   VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,'MANUAL',?,?,?,'MANUAL','',?,?)""",
                                (sym, direction, tf, entry, sl, tp1, tp2, tp3,
                                 f"{tp1},{tp2},{tp3}", reward / risk, score, strategy.upper(), reason,
                                 b_status, uid, config.now(), filled_at, live_px))
                sid = cur.lastrowid
            audit_log.log(uid, f"signal_create:{sid}")
            with db.conn() as c:
                row = c.execute("SELECT * FROM signals WHERE id=?", (sid,)).fetchone()
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
            with db.conn() as c:
                c.execute("""INSERT INTO market_catalog(symbol,title,provider_symbol,enabled,verified,created_at,updated_at)
                             VALUES(?,?,?,0,0,?,?)
                             ON CONFLICT(symbol) DO UPDATE SET title=excluded.title,
                             provider_symbol=excluded.provider_symbol,enabled=0,verified=0,updated_at=excluded.updated_at""",
                          (symbol, title[:80], provider[:30], config.now(), config.now()))
            audit_log.log(uid, "market_added_unverified:" + symbol)
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
        cfg = support_service.config_row() or {}
        cfg[field] = val.lstrip("@") if field == "username" else val
        support_service.set_config(cfg.get("username", ""), cfg.get("phone", ""),
                                   cfg.get("channel", ""), cfg.get("group_chat", ""))
        ud.pop("awaiting_support_field", None)
        audit_log.log(uid, f"support_config_field:{field}")
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
            support_service.set_config(*vals)
            audit_log.log(uid, "support_config_updated")
            await update.effective_message.reply_text("✅ تم حفظ بيانات الدعم.")
        except Exception as e:
            ud["awaiting_support_config"] = True
            await update.effective_message.reply_text("تعذر الحفظ: " + safe(e))
        return True

    # ---------- User search ----------
    if ud.pop("awaiting_user_search", False):
        rows = users_service.search(text)
        rows = [r for r in rows if r["user_id"] != config.ADMIN_ID]
        if not rows:
            await update.effective_message.reply_text("لا نتائج مطابقة.")
        else:
            lines = "\n".join(f"👤 {esc(r['full_name'] or '—')} · @{esc(r['username'] or '—')} · <code>{r['user_id']}</code>"
                              for r in rows[:10])
            await update.effective_message.reply_text("نتائج البحث:\n" + lines, parse_mode="HTML")
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
        payments_service.set_chain_wallet(chain, val)
        audit_log.log(uid, f"usdt_wallet_set:{chain}" if val else f"usdt_wallet_cleared:{chain}")
        await update.effective_message.reply_text(
            (f"✅ تم ضبط محفظة USDT على شبكة <b>{chain}</b> للتحقق البلوكيشن الفوري:\n<code>{esc(val)}</code>\n"
             "كل دفعة تُتحقق على الشبكة (التأكيد + العقد + العنوان + المبلغ)، والـ TXID يُحجب بعد القبول."
             if val else f"✅ تم مسح عنوان محفظة {chain} — الشبكة تبقى مغلقة للمستخدمين حتى ضبطها."),
            parse_mode="HTML")
        return True

    # ---------- Payment rejection reason ----------
    pid = ud.pop("reject_payment_id", None)
    if pid:
        res = payments_service.reject_request(int(pid), text, uid)
        if res.get("ok"):
            audit_log.log(uid, f"payment_rejected:{pid}")
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
            from telegram import InlineKeyboardButton, InlineKeyboardMarkup
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
                    [InlineKeyboardButton("✅ تأكيد الإرسال", callback_data=f"a:bcgo:{bid}")],
                    [InlineKeyboardButton("🚫 إلغاء", callback_data=f"a:bccancel:{bid}")],
                    [InlineKeyboardButton("↩️ الإعلانات", "a:broadcast")],
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
            audit_log.log(uid, f"free_quota_set:{val}")
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
            audit_log.log(uid, f"demo_balance_set:{val:g}")
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
            audit_log.log(uid, f"signal_expiry_set:{val}")
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
            with db.conn() as c:
                if c.execute("SELECT 1 FROM plans WHERE code=?", (code,)).fetchone():
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
            from telegram import InlineKeyboardButton, InlineKeyboardMarkup
            kb = InlineKeyboardMarkup([[InlineKeyboardButton(c, callback_data=f"a:plancur:{c}") for c in ("USD", "YER")],
                                       [InlineKeyboardButton(c, callback_data=f"a:plancur:{c}") for c in ("SAR", "EUR")]])
            await update.effective_message.reply_text(f"✅ السعر: <b>{price:g}</b>\n💎 اختر <b>العملة</b>:", parse_mode="HTML", reply_markup=kb)
        else:
            ud.pop("plan_draft", None)
            await update.effective_message.reply_text("أعد إنشاء الباقة من قسم الباقات.")
    except ValueError as e:
        await update.effective_message.reply_text(f"❌ {safe(e)} — أعد الإرسال.")
    return True


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
        from telegram import InlineKeyboardButton, InlineKeyboardMarkup
        with db.conn() as c:
            plans = c.execute("SELECT code,title FROM plans ORDER BY price").fetchall()
        kb = [[InlineKeyboardButton(("✅ " if p["code"] in d.get("linked", []) else "") + p["title"],
                                    callback_data=f"a:pmplan:{p['code']}")] for p in plans]
        kb.append([InlineKeyboardButton("💾 حفظ الوسيلة", callback_data="a:pmsave"),
                   InlineKeyboardButton("🗑 إلغاء", callback_data="a:pmcancel")])
        await update.effective_message.reply_text(
            f"✅ التعليمات حُفظت.\n💎 اختر <b>الباقات المرتبطة</b> (اضغط للتبديل) ثم احفظ:",
            parse_mode="HTML", reply_markup=InlineKeyboardMarkup(kb))
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
        # Save (credentials stored only in config_json; never rendered)
        cfg = {"base_url": d.get("base_url", ""), "api_key": d["api_key"],
               "api_secret": d["api_secret"], "account_id": d["account_id"]}
        with db.conn() as c:
            cur = c.execute("""INSERT INTO broker_adapters(key,title,status,enabled,config_json,created_at,updated_at)
                               VALUES(?,?, 'CONFIGURED', 0, ?, ?, ?)""",
                            (d["type"], d["name"], json.dumps(cfg), config.now(), config.now()))
            bid = cur.lastrowid
        context.user_data.pop("broker_draft", None)
        audit_log.log(uid, f"broker_added:{d['type']}:{bid}")
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
    bot = context.bot

    async def send(uid: int, text: str):
        return await bot.send_message(uid, text, parse_mode="HTML")

    return send


async def on_photo(update: Update, context: ContextTypes.DEFAULT_TYPE):
    uid = update.effective_user.id
    ud = context.user_data
    photo = update.effective_message.photo[-1]
    caption = (update.effective_message.caption or "").strip()

    # Admin: broadcast photo
    if uid == config.ADMIN_ID and ud.get("awaiting_broadcast_photo"):
        bid = ud.get("broadcast_draft", 0)
        ud["awaiting_broadcast_photo"] = False
        from fysb.services import broadcast as broadcast_service
        b = broadcast_service.get(bid) if bid else None
        if b:
            with db.conn() as c:
                c.execute("UPDATE broadcasts SET file_id=?, has_photo=1 WHERE id=?", (photo.file_id, bid))
            targets = broadcast_service.audience_ids(b["audience"], b["plan_code"])
            from telegram import InlineKeyboardButton, InlineKeyboardMarkup
            await update.effective_message.reply_text(
                f"✅ أُرفقت الصورة بالإعلان #{bid}.\nعدد المستلمين: {len(targets)}",
                reply_markup=InlineKeyboardMarkup([
                    [InlineKeyboardButton("🚀 إرسال الآن", callback_data=f"a:bcgo:{bid}")],
                    [InlineKeyboardButton("🚫 إلغاء", callback_data=f"a:bccancel:{bid}")],
                    [InlineKeyboardButton("↩️ الإعلانات", "a:broadcast")],
                ]))
        else:
            await update.effective_message.reply_text("⚠️ لا يوجد مسودة إعلان — ابدأ من قسم الإعلانات.")
        return

    # User: payment receipt
    pid = ud.pop("awaiting_receipt", None)
    if pid:
        ok = payments_service.attach_receipt(int(pid), uid, photo.file_id)
        if ok:
            await update.effective_message.reply_text(f"✅ استلمنا إيصال الطلب #{pid}. سيظهر للإدارة للمراجعة.")
            if config.ADMIN_ID:
                try:
                    await context.bot.send_photo(config.ADMIN_ID, photo.file_id,
                                                 caption=f"🧾 إيصال دفع جديد #{pid}\nالمستخدم: {uid}\n"
                                                         f"الباقة: {payments_service.get_request(pid)['plan_code']}")
                except Exception:
                    pass
        else:
            await update.effective_message.reply_text("⚠️ الطلب غير موجود أو تمت مراجعته مسبقاً.")
        return

    # User: attachment to an open ticket reply
    tid = ud.get("ticket_reply_to")
    if tid:
        support_service.add_message(int(tid), "user", uid, caption or "📎 مرفق", file_id=photo.file_id)
        if config.ADMIN_ID:
            try:
                await context.bot.send_photo(config.ADMIN_ID, photo.file_id,
                                             caption=f"📎 مرفق في التذكرة #{tid} من المستخدم {uid}")
            except Exception:
                pass
        await update.effective_message.reply_text(f"✅ أُضيف المرفق للتذكرة #{tid}.")
        return
