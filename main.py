#!/usr/bin/env python3
"""FOREXYEMENI — Telegram trading-signals platform. Entry point.

Usage: python main.py
Environment: .env (BOT_TOKEN, ADMIN_ID, DB_PATH, LOG_LEVEL, TWELVEDATA_API_KEY)
"""
import sys

from fysb import config, db
from fysb.config import log
from fysb.handlers.background import (SingleInstanceLock, post_init, post_shutdown)

TASKS_REF = []


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
        from telegram.ext import Application, CallbackQueryHandler, CommandHandler, MessageHandler, filters

        from fysb.handlers import inputs
        from fysb.handlers import common
        from fysb.handlers.common import (admin_command, backup_command, error_handler,
                                          menu_command, start)
        from fysb.handlers.dispatcher import buttons

        app = (Application.builder()
               .token(config.BOT_TOKEN)
               .post_init(post_init)
               .post_shutdown(post_shutdown)
               .build())
        app.add_handler(CommandHandler("start", start))
        app.add_handler(CommandHandler("menu", menu_command))
        app.add_handler(CommandHandler("admin", admin_command))
        app.add_handler(CommandHandler("backup", backup_command))
        app.add_handler(CallbackQueryHandler(buttons))
        app.add_handler(MessageHandler(filters.PHOTO, inputs.on_photo))
        app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, inputs.on_text))
        app.add_error_handler(error_handler)
        log.info("%s | %s | %s — starting (handlers registered: /start /menu /admin /backup + callbacks + text/photo)",
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
