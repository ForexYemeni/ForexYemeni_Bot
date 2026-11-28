#!/usr/bin/env python3
"""FOREXYEMENI — Telegram trading-signals platform. Entry point.

Usage: python main.py
Environment: .env (BOT_TOKEN, ADMIN_ID, DB_PATH, LOG_LEVEL, TWELVEDATA_API_KEY)
"""
import os
import sys


def _bootstrap_self_update() -> None:
    """v6.9.0 — فحص GitHub Releases قبل تحميل أي كود من fysb.

    يجري فقط عند التشغيل المباشر (python main.py)، وليس عند الاستيراد.
    وجود إصدار أحدث ⇒ تثبيته ثم إعادة تشغيل فورية — لا يعود أبداً.
    أي فشل ⇒ سطر BOOT واضح ثم متابعة الإقلاع الطبيعي بالإصدار الحالي.
    تعطيل كامل: AUTO_UPDATE=0 في البيئة.
    """
    if os.getenv("AUTO_UPDATE", "1").strip().lower() in ("0", "false", "no", "off"):
        print("BOOT: UPDATE — معطل (AUTO_UPDATE=0)")
        return
    try:
        try:
            from dotenv import load_dotenv
            load_dotenv()          # لملء GITHUB_TOKEN إن كان المستودع خاصاً
        except Exception:
            pass
        import updater
        def _announce_verified_update(info):
            from fysb.services.update_notices import boot_notice
            boot_notice(info)
        updater.boot_check(before_install=_announce_verified_update)
    except SystemExit:
        raise
    except Exception as exc:      # فحص التحديث لا يحجب الإقلاع أبداً
        print(f"BOOT: UPDATE ✗ تم تجاهل الفحص ({type(exc).__name__}) — متابعة بالإصدار الحالي")


if __name__ == "__main__":
    _bootstrap_self_update()

from fysb import config, db
from fysb.config import log
from fysb.handlers.background import (SingleInstanceLock, post_init, post_shutdown)

TASKS_REF = []

# First line of every boot log — proves EXACTLY which build the container runs.
# (2026-11-23 bot-hosting incident: the panel kept crash-looping on v6.7.1 for
# hours while v6.7.2 sat on GitHub because the container never pulled it; a
# version banner before ANY database work ends all doubt in one glance.)
log.info("BOOT: %s v%s — this exact build is running", config.BRAND, config.VERSION)


def _fresh_page_on_boot() -> None:
    """v8.14.0 — the royal fresh page, EXECUTED once on the first boot after
    v8.14.0 (the owner's documented decision: «حذف جميع الإشارات السابقة
    ونبدا من الآن باشارات جديدة وكل شيء جديد حقيقي 100%» — no archive,
    mandatory notice to all holders, signals-scope only).

    Boot-time execution makes the wipe DETERMINISTIC on the production server
    (no button press required). Holders are captured BEFORE the wipe (the
    deliveries table is part of the scope) and stashed for the background
    loop, which sends the mandatory notice once Telegram is up. Idempotent:
    the `fresh_page_executed` stamp means it can never run twice."""
    import json as _json
    if db.get_setting("fresh_page_executed", "0") == "1":
        return
    try:
        from fysb.engines import tracker as _trk
        holders = _trk.fresh_page_holders()
        counts = _trk.fresh_page_wipe()
        db.set_setting("fresh_page_executed", "1")
        db.set_setting("fresh_page_notice_holders", _json.dumps(sorted(holders)))
        db.set_setting("fresh_page_notice_pending", "1")
        log.warning("BOOT: FRESH PAGE executed — signals wiped (%s); mandatory "
                    "notice queued for %s holders", counts.get("signals", 0), len(holders))
    except Exception as exc:
        # never block the bot boot on the wipe — but leave the stamp unset so
        # the next boot retries the owner's standing decision
        log.exception("BOOT: fresh page deferred: %s", type(exc).__name__)


def preflight() -> bool:
    """Validate environment + database before touching Telegram."""
    problems = config.validate_boot()
    if problems:
        for p in problems:
            log.error("BOOT: %s", p)
        return False
    db.init_db()
    ok, integrity = db.healthy()
    if not ok:
        log.error("BOOT: database integrity failed: %s", integrity)
        return False
    _fresh_page_on_boot()
    log.info("BOOT: database ok (%s)", integrity)
    log.info("BOOT: brand=%s build=%s | admin_id=%s | db=%s | market_key=%s",
             config.BRAND, config.BUILD_NAME, config.ADMIN_ID,
             config.DB_FILE.name, "configured" if config.has_market_api() else "missing")
    log.info("BOOT: live trading state=%s (must stay 0 unless a verified broker exists)",
             db.get_setting("live_enabled", "0"))
    return True


def main() -> int:
    lock = SingleInstanceLock()
    if not lock.acquire():
        print("⛔ يوجد نسخة أخرى من البوت تعمل حالياً (fysb.lock). أوقفها أولاً.")
        return 2
    try:
        if not preflight():
            return 1
        from telegram.ext import (
            Application, CallbackQueryHandler, CommandHandler, MessageHandler,
            PreCheckoutQueryHandler, filters,
        )

        from fysb.handlers import inputs, admin_partners
        from fysb.handlers import common
        from fysb.handlers.common import (admin_command, backup_command, btnfail_command,
                                          error_handler, fix_command, menu_command,
                                          ping_command, start, update_command)
        from fysb.handlers.dispatcher import buttons

        # v6.11.0 — ZERO-WAIT BUTTONS (owner demand: «أزرار تستجيب بشكل أسرع
        # دون الانتظار للحظة واحدة»), three structural switches at once:
        #  1. concurrent_updates: every update runs in its OWN task — a slow
        #     page for one user can never queue the next user's button behind
        #     it (previously updates were processed strictly one-by-one).
        #  2. connection_pool_size(64): PTB's default HTTPX pool serializes
        #     Telegram API calls (pool_size=1); with parallel handlers the
        #     pool must fit the parallelism or sends would wait in line.
        #  3. handlers never block the loop: every DB/service/API call on a
        #     handler path is shipped to the worker-thread pool via fysb.afn
        #     — the loop keeps answering buttons while threads wait on WAN.
        app = (Application.builder()
               .token(config.BOT_TOKEN)
               .concurrent_updates(True)
               .connection_pool_size(64)
               .get_updates_connection_pool_size(8)
               .post_init(post_init)
               .post_shutdown(post_shutdown)
               .build())
        _PRIV = filters.ChatType.PRIVATE & ~filters.UpdateType.EDITED_MESSAGE
        app.add_handler(CommandHandler("start", start, filters=_PRIV))
        app.add_handler(CommandHandler("menu", menu_command, filters=_PRIV))
        app.add_handler(CommandHandler("ping", ping_command, filters=_PRIV))
        app.add_handler(CommandHandler("update", update_command, filters=_PRIV))
        app.add_handler(CommandHandler("admin", admin_command, filters=_PRIV))
        app.add_handler(CommandHandler("partner", admin_partners.partner_command, filters=_PRIV))
        app.add_handler(CommandHandler("backup", backup_command, filters=_PRIV))
        app.add_handler(CommandHandler("btnfail", btnfail_command, filters=_PRIV))
        app.add_handler(CommandHandler("fix", fix_command, filters=_PRIV))
        app.add_handler(CallbackQueryHandler(buttons))
        app.add_handler(PreCheckoutQueryHandler(inputs.on_pre_checkout))
        app.add_handler(MessageHandler(_PRIV & filters.SUCCESSFUL_PAYMENT, inputs.on_successful_payment))
        app.add_handler(MessageHandler(_PRIV & filters.PHOTO, inputs.on_photo))
        app.add_handler(MessageHandler(_PRIV & (filters.VIDEO | filters.AUDIO | filters.VOICE | filters.Document.ALL), inputs.on_media))
        app.add_handler(MessageHandler(_PRIV & filters.TEXT & ~filters.COMMAND, inputs.on_text))
        app.add_error_handler(error_handler)
        log.info("%s | %s | %s — starting (handlers registered: /start /menu /ping /update /admin /backup /btnfail /fix + callbacks + text/photo)",
                 config.BRAND, config.BUILD_NAME, config.VERSION)
        app.run_polling(drop_pending_updates=False)
        if common.conflict_detected:
            # Another instance is polling with this token — installer maps exit 3 to the remedy.
            return 3
        return 0
    except KeyboardInterrupt:
        log.info("shutdown requested (keyboard interrupt)")
        return 0
    finally:
        db.wal_checkpoint()
        lock.release()


if __name__ == "__main__":
    sys.exit(main())
