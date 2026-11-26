"""Branded pre-install notices for Telegram-initiated updates.

No user balance/permission/notification preference is modified. Delivery is
best-effort: blocked users and channels without posting permission are counted.
Automatic boot updates use a short-lived Telegram client. Offline rollback
does not use the remote-package notification hook.
"""
import asyncio
import io
from pathlib import Path
import threading

from telegram.error import BadRequest, Forbidden, NetworkError, RetryAfter

from fysb import afn, config, db
from fysb.config import log
from fysb.theme import esc

_INSTALL_LOCK = threading.Lock()
NOTICE_INTERVAL = 1 / 20
SEND_TIMEOUT = 8.0
LOGO_PATH = Path(__file__).resolve().parents[1] / "ui" / "assets" / "update_emblem.jpg"


def notice_card(source, target, *, cancelled=False):
    if cancelled:
        title = "⚠️ تعذّر إكمال تحديث البوت"
        body = "لم تكتمل عملية التحديث كما خُطط لها. تتابع الإدارة الحالة، وسيُعلن عن أي إجراء لاحق."
    else:
        title = "🛠 إشعار تحديث المنصة"
        body = "يجري تجهيز الانتقال للإصدار الموضح أدناه، تليه إعادة تشغيل قصيرة. قد يتأخر الرد مؤقتًا أثناء العملية."
    return (
        "👑 <b>FOREXYEMENI</b>\n"
        "━━━━━━━━━━━━━━━━━━━━\n"
        f"<b>{title}</b>\n\n"
        f"<blockquote>◆ من الإصدار: <b>{esc(source)}</b>\n"
        f"◆ إلى الإصدار: <b>{esc(target)}</b>\n"
        f"◆ {body}</blockquote>\n\n"
        "💎 شكرًا لثقتكم وصبركم — فريق FOREXYEMENI\n"
        f"{esc(config.BOT_SIGNATURE)}"
    )


def _targets():
    targets = []
    with db.conn() as c:
        targets.extend(r["user_id"] for r in c.execute(
            "SELECT user_id FROM users WHERE is_blocked=0 AND user_id!=?", (config.ADMIN_ID,)
        ).fetchall())
        for table in ("broadcast_channels", "chart_channels"):
            targets.extend(r["chat_id"] for r in c.execute(
                f"SELECT chat_id FROM {table} WHERE enabled=1"
            ).fetchall())
    unique = {}
    for value in targets:
        text = str(value or "").strip()
        if not text:
            continue
        try:
            value = int(text)
            key = str(value)
        except ValueError:
            value, key = text, text.lower()
        if key != str(config.ADMIN_ID):
            unique.setdefault(key, value)
    return list(unique.values())


async def _deliver(bot, chat_id, text, logo):
    """Use the logo when possible; text fallback never bypasses a 429 wait."""
    async def send(use_photo):
        # Pace attempts too (including media fallbacks), not just recipients.
        await asyncio.sleep(NOTICE_INTERVAL)
        if use_photo:
            photo = (logo.get("file_id") or io.BytesIO(logo["bytes"])) if isinstance(logo, dict) else io.BytesIO(logo)
            message = await asyncio.wait_for(bot.send_photo(
                chat_id=chat_id, photo=photo, caption=text, parse_mode="HTML"
            ), SEND_TIMEOUT)
            sizes = getattr(message, "photo", None)
            if isinstance(logo, dict) and sizes:
                logo["file_id"] = sizes[-1].file_id
            return message
        return await asyncio.wait_for(bot.send_message(chat_id=chat_id, text=text, parse_mode="HTML"), SEND_TIMEOUT)

    use_photo = bool(logo)
    for attempt in range(2):
        try:
            await send(use_photo)
            return True
        except RetryAfter as exc:
            delay = exc.retry_after
            delay = delay.total_seconds() if hasattr(delay, "total_seconds") else float(delay)
            if attempt or delay > 30:
                return False
            await asyncio.sleep(max(0, delay))
        except Forbidden:
            return False
        except BadRequest:
            if not use_photo:
                return False
            use_photo = False
        except (asyncio.TimeoutError, NetworkError):
            # Unknown delivery outcome: do not duplicate a possibly sent notice.
            return False
        except Exception:
            return False
    return False


async def announce(bot, info, *, targets=None, cancelled=False):
    if bot is None:
        return {"sent": 0, "failed": 0, "targets": []}
    targets = await afn.run(_targets) if targets is None else targets
    source = "v" + str(info.get("local") or config.VERSION).lstrip("vV")
    target = "v" + str(info.get("remote") or "?").lstrip("vV")
    text = notice_card(source, target, cancelled=cancelled)
    try:
        logo = await afn.run(LOGO_PATH.read_bytes) if not cancelled else None
    except OSError:
        logo = None
    media = {"bytes": logo} if logo else None
    sent, failed, reached = 0, 0, []
    for chat_id in targets:
        if await _deliver(bot, chat_id, text, media):
            sent += 1
            reached.append(chat_id)
        else:
            failed += 1
    log.info("UPDATE NOTICE | from=%s | to=%s | sent=%d | failed=%d | cancelled=%s",
             source, target, sent, failed, cancelled)
    return {"sent": sent, "failed": failed, "targets": reached}


async def install(bot, target_tag="", force=False):
    """Run the existing installer once; bridge its verified-package hook to Telegram.

    All notices finish (or fail per-recipient) before extraction. No success
    message is sent to users: only the restarted process can prove it is ready.
    """
    import updater
    loop = asyncio.get_running_loop()
    notified = {}

    def before_install(info):
        nonlocal notified
        try:
            notified = asyncio.run_coroutine_threadsafe(announce(bot, info), loop).result()
        except Exception as exc:
            log.warning("update announcement failed (%s); proceeding with verified update", type(exc).__name__)

    def worker():
        if not _INSTALL_LOCK.acquire(blocking=False):
            return {"status": "error", "detail": "يوجد تحديث قيد التنفيذ بالفعل؛ انتظر اكتماله."}
        try:
            # Preserve the legacy call surface for non-Telegram callers/tests.
            if bot is None:
                return updater.self_update(None, target_tag, force) if target_tag else updater.self_update()
            return updater.self_update(None, target_tag, force, before_install=before_install)
        finally:
            _INSTALL_LOCK.release()

    task = asyncio.create_task(asyncio.to_thread(worker))
    try:
        result = await asyncio.shield(task)
    except asyncio.CancelledError:
        # A caller cancellation must not release the lock/stop the event loop
        # while the installer thread is waiting for its Telegram hook.
        await task
        raise
    if notified:
        result["notice"] = {k: notified[k] for k in ("sent", "failed")}
        if result.get("status") != "updated" and notified.get("targets"):
            await announce(bot, result, targets=notified["targets"], cancelled=True)
    return result


def summary(result):
    notice = result.get("notice")
    if not notice:
        return ""
    return (f"📣 تنبيه التحديث: وصل إلى {int(notice.get('sent') or 0)} وجهة · "
            f"تعذّر إلى {int(notice.get('failed') or 0)}.\n")


def boot_notice(info):
    """Best-effort notice before an automatic verified boot-time update."""
    if not config.BOT_TOKEN:
        return

    async def send():
        from telegram import Bot
        async with Bot(config.BOT_TOKEN) as bot:
            return await announce(bot, info)

    try:
        asyncio.run(send())
    except Exception as exc:
        log.warning("boot update notice unavailable (%s)", type(exc).__name__)
