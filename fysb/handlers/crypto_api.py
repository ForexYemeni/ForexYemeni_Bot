"""Telegram UI handlers for Phase 5 (#31 — Free Crypto Exchange API Integration)."""
from __future__ import annotations

import asyncio

from fysb import afn, config, db
from fysb.handlers.common import render
from fysb.services import audit as audit_log
from fysb.services import crypto_exchange as cex
from fysb.theme import esc
from fysb.ui import keyboards as kb


def _signal_by_id(sid: int) -> dict | None:
    with db.conn() as c:
        r = c.execute("SELECT * FROM signals WHERE id=?", (int(sid),)).fetchone()
    return dict(r) if r else None


async def handle_user_crypto_api(q, uid: int, action: str, context, bot) -> bool:
    if action == "exchange":
        if not await afn.run(cex.is_enabled):
            await render(
                q,
                "🔗 <b><u>ربط منصات الكريبتو</u></b>\n"
                "<blockquote>▎🔴 هذه الميزة متوقفة مؤقتاً من الإدارة.</blockquote>",
                kb.user_nav(),
                bot,
            )
            return True
        body = await afn.run(cex.render_user_exchange_card, uid)
        keys = await afn.run(cex.list_user_keys, uid)
        rows_kb = [
            [
                kb.btn("🟡 ربط Binance", "u:exchadd:BINANCE", style="primary"),
                kb.btn("🟠 ربط Bybit", "u:exchadd:BYBIT", style="primary"),
                kb.btn("⚫ ربط OKX", "u:exchadd:OKX", style="primary"),
            ]
        ]
        for k in keys:
            ex = k["exchange"]
            rows_kb.append([
                kb.btn(f"🧪 فحص {ex}", f"u:exchtest:{ex}", style="success"),
                kb.btn("⚡ تحويل LIVE" if k["testnet"] else "🧪 عودة Testnet", f"u:exchtoglive:{ex}",
                       style="danger" if k["testnet"] else "primary"),
                kb.btn(f"🗑 حذف {ex}", f"u:exchdel:{ex}", style="danger"),
            ])
        rows_kb.append([kb.btn("📜 سجل أوامري", "u:exchorders", style="primary"), kb.btn("📡 الإشارات", "u:signals")])
        await render(q, body, kb.user_nav(rows_kb), bot)
        return True

    if action.startswith("exchadd:"):
        ex = action.split(":", 1)[1].strip().upper()
        if ex not in cex.SUPPORTED_EXCHANGES:
            return True
        context.user_data["awaiting_exch_creds"] = ex
        needs_pp = cex.EXCHANGE_META[ex]["needs_passphrase"]
        fmt = "<code>API_KEY API_SECRET PASSPHRASE</code>" if needs_pp else "<code>API_KEY API_SECRET</code>"
        await render(
            q,
            f"🔐 <b><u>ربط منصة {cex.EXCHANGE_META[ex]['title']} (وضع Testnet الآمن أولاً)</u></b>\n"
            f"<blockquote>"
            f"▎1) أنشئ مفتاح API للتداول فقط <b>بدون صلاحية السحب (No Withdraw)</b>.\n"
            f"▎2) أرسل المفاتيح الآن في سطر واحد بالتنسيق:\n{fmt}\n"
            f"▎🛡 ستُحذف رسالتك تلقائياً وتُشفّر المفاتيح بتشفير ENC1 فوراً."
            f"</blockquote>",
            kb.user_nav([[kb.btn("❌ إلغاء", "u:exchange", style="danger")]]),
            bot,
        )
        return True

    if action.startswith("exchtest:"):
        ex = action.split(":", 1)[1].strip().upper()
        res = await asyncio.to_thread(cex.verify_connection, uid, ex)
        if res.get("ok"):
            mode = "🧪 Testnet" if res["testnet"] else "⚡ LIVE"
            body = (
                f"✅ <b><u>نجح الاتصال بمنصة {esc(ex)} ({mode})!</u></b>\n"
                f"<blockquote>▎💰 رصيد USDT المتاح: <b>{res['usdt_balance']:,.2f} USDT</b>\n"
                f"▎🛡 فحص سياسة منع السحب (No-Withdraw): <b>اجتاز بنجاح</b></blockquote>"
            )
        else:
            body = f"❌ <b>فشل فحص الاتصال بـ {esc(ex)}:</b>\n<blockquote>{esc(res.get('error', 'خطأ غير معروف'))}</blockquote>"
        await render(q, body, kb.user_nav([[kb.btn("↩️ منصاتي", "u:exchange")]]), bot)
        return True

    if action.startswith("exchtoglive:"):
        ex = action.split(":", 1)[1].strip().upper()
        keys = {k["exchange"]: k for k in await afn.run(cex.list_user_keys, uid)}
        cur = keys.get(ex)
        if not cur:
            return True
        nxt_testnet = not bool(cur["testnet"])
        await afn.run(cex.set_key_testnet, uid, ex, nxt_testnet)
        return await handle_user_crypto_api(q, uid, "exchange", context, bot)

    if action.startswith("exchdel:"):
        ex = action.split(":", 1)[1].strip().upper()
        await afn.run(cex.delete_exchange_key, uid, ex)
        return await handle_user_crypto_api(q, uid, "exchange", context, bot)

    if action == "exchorders":
        orders = await afn.run(cex.recent_orders, uid, 10)
        lines = ["📜 <b><u>سجل أوامر منصات الكريبتو</u></b>"]
        if orders:
            for o in orders:
                ic = "✅" if o["status"] == "FILLED" else "❌"
                mode = "🧪" if o["testnet"] else "⚡"
                lines.append(
                    f"  {ic} {mode} <b>{esc(o['exchange'])}</b> · {esc(o['symbol'])} {o['side']} · "
                    f"<code>{o['qty']:.5f}</code> · {esc(o['status'])}"
                )
        else:
            lines.append("<blockquote>▎لا توجد أوامر منفّذة بعد.</blockquote>")
        await render(q, "\n".join(lines), kb.user_nav([[kb.btn("↩️ منصاتي", "u:exchange")]]), bot)
        return True

    if action.startswith("exchexec:"):
        sid = int(action.split(":", 1)[1] or 0)
        sig = await afn.run(_signal_by_id, sid)
        if not sig or not cex.is_crypto_symbol(sig["symbol"]):
            await render(q, "⚠️ هذه الإشارة ليست عملة رقمية مدعومة في منصات الكريبتو.", kb.user_nav(), bot)
            return True
        keys = await afn.run(cex.list_user_keys, uid)
        if not keys:
            await render(
                q,
                "🔗 اربط منصة Binance أو Bybit أو OKX أولاً من قسم منصات الكريبتو.",
                kb.user_nav([[kb.btn("🔗 ربط منصة الآن", "u:exchange", style="success")]]),
                bot,
            )
            return True
        ex = keys[0]["exchange"]
        res = await asyncio.to_thread(cex.execute_crypto_signal, uid, ex, sig)
        if res.get("ok"):
            mode = "🧪 Testnet" if res["testnet"] else "⚡ LIVE"
            body = (
                f"✅ <b><u>تم تنفيذ الأمر على {esc(ex)} ({mode})</u></b>\n"
                f"<blockquote>▎📌 {esc(res['symbol'])} <b>{res['side']}</b> · القيمة: <b>{res['trade_usd']:,.2f} USDT</b>\n"
                f"▎🧾 رقم الأمر: <code>{esc(res['order_id'])}</code></blockquote>"
            )
        else:
            body = f"❌ <b>تعذر تنفيذ الأمر:</b>\n<blockquote>{esc(res.get('error', 'خطأ'))}</blockquote>"
        await render(q, body, kb.user_nav([[kb.btn("📜 سجل أوامري", "u:exchorders"), kb.btn("📡 الإشارات", "u:signals")]]), bot)
        return True

    return False


async def handle_admin_crypto_api(q, uid: int, act: str, arg: str, context, bot) -> bool:
    if act == "cryptoapi":
        body = await afn.run(cex.render_admin_crypto_api_card)
        on = await afn.run(cex.is_enabled)
        rows_kb = [
            [kb.btn(
                "🔴 إيقاف ربط منصات الكريبتو" if on else "🟢 تفعيل ربط منصات الكريبتو",
                "a:cryptoapitoggle",
                style="danger" if on else "success",
            )],
            [kb.btn("↩️ الوسطاء والتنفيذ", "a:hub:execution")],
        ]
        await render(q, body, kb.admin_nav(rows_kb), bot)
        return True

    if act == "cryptoapitoggle":
        cur = await afn.run(cex.is_enabled)
        nxt = await afn.run(cex.set_enabled, not cur)
        await afn.run(audit_log.log, uid, f"crypto_api_enabled:{int(nxt)}")
        return await handle_admin_crypto_api(q, uid, "cryptoapi", "", context, bot)

    return False


async def handle_crypto_text_input(update, context, uid: int, text: str) -> bool:
    ex = context.user_data.pop("awaiting_exch_creds", None)
    if not ex:
        return False
    # Best-effort delete user's raw message containing API credentials immediately
    try:
        await update.effective_message.delete()
    except Exception:
        pass
    parts = text.strip().split()
    try:
        ak = parts[0]
        asec = parts[1]
        pp = parts[2] if len(parts) > 2 else ""
        res = await afn.run(cex.save_exchange_key, uid, ex, ak, asec, passphrase=pp, testnet=True)
    except Exception as exc:
        await context.bot.send_message(
            uid,
            f"❌ <b>تعذر حفظ المفاتيح:</b> {esc(str(exc))}",
            reply_markup=kb.user_nav([[kb.btn("🔄 إعادة المحاولة", f"u:exchadd:{ex}", style="primary")]]),
            parse_mode="HTML",
        )
        return True
    await context.bot.send_message(
        uid,
        f"🔐 <b><u>تم حفظ وتشفير مفاتيح {esc(ex)} بنجاح (وضع Testnet الآمن)</u></b>\n"
        f"<blockquote>▎🔑 المفتاح: <code>{esc(res['masked_key'])}</code>\n"
        f"▎🧪 الوضع: <b>Testnet</b> · الحد الأقصى للصفقة: <b>{res['max_trade_usd']:g}$</b>\n"
        f"▎اضغط «فحص الاتصال» الآن للتحقق من الرصيد وسياسة منع السحب (No-Withdraw).</blockquote>",
        reply_markup=kb.user_nav([
            [kb.btn(f"🧪 فحص اتصال {ex} الآن", f"u:exchtest:{ex}", style="success")],
            [kb.btn("🔗 إدارة منصاتي", "u:exchange", style="primary")],
        ]),
        parse_mode="HTML",
    )
    return True
