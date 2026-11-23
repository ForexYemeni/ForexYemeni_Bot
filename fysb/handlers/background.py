"""Background loops — market engine, lifecycle tracker, subscription maintenance.

Single-instance protected (lock file), graceful shutdown, per-loop error
isolation: one failing loop never kills the bot.
"""
import asyncio

from fysb import config, db
from fysb.config import log

LOCK_FILE = config.BASE_DIR / "fysb.lock"


class SingleInstanceLock:
    """Cross-platform single-instance guard (Termux-safe, no flock dependency)."""

    def __init__(self):
        self.acquired = False

    def acquire(self) -> bool:
        import os
        try:
            if LOCK_FILE.exists():
                pid_text = LOCK_FILE.read_text().strip()
                if pid_text.isdigit() and _pid_alive(int(pid_text)):
                    return False
                # stale lock from a crashed run — take over
                LOCK_FILE.unlink()
            fd = os.open(str(LOCK_FILE), os.O_CREAT | os.O_EXCL | os.O_WRONLY)
            os.write(fd, str(os.getpid()).encode())
            os.close(fd)
            self.acquired = True
            return True
        except FileExistsError:
            return False
        except OSError:
            return True  # cannot enforce — proceed but log
        except Exception:
            return True

    def release(self):
        try:
            if self.acquired:
                LOCK_FILE.unlink(missing_ok=True)
        except OSError:
            pass


def _pid_alive(pid: int) -> bool:
    try:
        import os
        os.kill(pid, 0)
        return True
    except (ProcessLookupError, ValueError):
        return False
    except PermissionError:
        return True  # exists but owned by another user


async def market_engine_loop(app):
    """Signal-only scanner — real data, honest rejections, never places orders.

    Works WITHOUT any API key: the hidden real-time provider chain (gold/crypto)
    serves the engine; keyed providers add forex coverage.
    """
    from fysb.engines.signal_engine import generate_signal

    async def bot_send(uid: int, text: str):
        await app.bot.send_message(uid, text, parse_mode="HTML")

    while True:
        try:
            if db.get_setting("engine_enabled", "0") == "1" \
                    and not db.kill_switch_on() \
                    and db.get_setting("signal_push_enabled", "1") == "1":
                with db.conn() as c:
                    markets = [dict(r) for r in c.execute(
                        "SELECT symbol FROM market_catalog WHERE enabled=1 AND verified=1").fetchall()]
                for m in markets:
                    try:
                        result = await asyncio.to_thread(generate_signal, m["symbol"])
                        if result["status"] == "SIGNAL_CREATED":
                            with db.conn() as c:
                                sig = c.execute("SELECT * FROM signals WHERE id=?", (result["signal_id"],)).fetchone()
                            if sig:
                                from fysb.engines.signal_engine import emit_to_users
                                stats = await emit_to_users(dict(sig), bot_send)
                                log.info("auto signal #%s emitted: %s", result["signal_id"], stats)
                                try:
                                    await app.bot.send_message(
                                        config.ADMIN_ID,
                                        f"📡 إشارة آلية جديدة #{result['signal_id']} ({m['symbol']}) — جودة {result.get('score')}/100")
                                except Exception:
                                    pass
                    except Exception as e:
                        log.warning("engine scan failed for %s: %s", m["symbol"], type(e).__name__)
                    await asyncio.sleep(config.MARKET_SYMBOL_DELAY_SECONDS)
        except asyncio.CancelledError:
            raise
        except Exception:
            log.exception("market engine loop iteration failed")
        await asyncio.sleep(max(60, db.get_setting_int("engine_interval_seconds", 300)))


async def tracker_loop(app):
    """Signal lifecycle updates from real prices + expiry sweep + half-TP1 alerts.

    Tracking runs UNCONDITIONALLY: open signals are obligations the platform
    already issued — the engine toggle governs generating NEW signals only.
    A paused engine must never leave issued signals unattended.
    """
    from fysb.engines import tracker
    while True:
        try:
            result = await asyncio.to_thread(tracker.check_open_signals, config.market_api_key())
            if result["stats"]["updated"]:
                log.info("tracker updated %s signals", result["stats"]["updated"])
            for uid, text in result["notifications"]:
                try:
                    await app.bot.send_message(uid, text, parse_mode="HTML")
                except Exception:
                    pass
            expired = tracker.expire_stale_signals()
            if expired:
                log.info("expired %s stale signals", expired)
        except asyncio.CancelledError:
            raise
        except Exception:
            log.exception("tracker loop failed")
        await asyncio.sleep(120)


async def subscriptions_loop(app):
    """Expiry marking, reminders, payment-pending digest to admin (daily-ish)."""
    from fysb.services import subscriptions as subs
    last_digest_day = ""
    while True:
        try:
            expired_targets = subs.mark_expired_and_notify_targets()
            for t in expired_targets:
                try:
                    await app.bot.send_message(t["user_id"],
                                               f"⌛ انتهت صلاحية اشتراكك في باقة <b>{t['plan']}</b>. جدّد من قسم الباقات.",
                                               parse_mode="HTML")
                except Exception:
                    pass
            for s in subs.due_reminders():
                hours_left = max(1, 24 * 3)
                try:
                    from datetime import datetime
                    ends = datetime.fromisoformat(s["ends_at"])
                    hours_left = max(1, int((ends - datetime.now(ends.tzinfo)).total_seconds() // 3600))
                except Exception:
                    pass
                try:
                    await app.bot.send_message(s["user_id"],
                                               f"⏳ تنبيه: ينتهي اشتراكك ({s['plan_code']}) بعد <b>~{hours_left} ساعة</b>. جدّد مبكراً لتفادي التوقف.",
                                               parse_mode="HTML")
                    subs.mark_reminded(s["id"], s["stage"])
                except Exception:
                    pass
            day = config.now()[:10]
            if day != last_digest_day and config.ADMIN_ID:
                pending = db.get_setting and _pending_payments_count()
                if pending:
                    try:
                        await app.bot.send_message(config.ADMIN_ID,
                                                   f"💳 تذكير يومي: <b>{pending}</b> طلب دفع معلق بانتظار المراجعة.",
                                                   parse_mode="HTML")
                    except Exception:
                        pass
                last_digest_day = day
        except asyncio.CancelledError:
            raise
        except Exception:
            log.exception("subscriptions loop failed")
        await asyncio.sleep(1800)


def _pending_payments_count() -> int:
    with db.conn() as c:
        return c.execute("SELECT COUNT(*) n FROM payments WHERE status='PENDING'").fetchone()["n"]


async def demo_loop(app):
    """Demo maintenance: auto SL/TP closes + half-TP1 secure-profit partial
    closes with luxury notifications."""
    from fysb.engines import demo
    from fysb import instruments as inst
    from fysb.theme import esc
    while True:
        try:
            with db.conn() as c:
                uids = [r["user_id"] for r in c.execute(
                    "SELECT DISTINCT user_id FROM demo_positions WHERE status='OPEN'").fetchall()]
            for uid in uids:
                events = await asyncio.to_thread(demo.sync_positions_with_real_prices, uid, config.market_api_key())
                for e in events:
                    try:
                        if e["kind"] == "SL":
                            await app.bot.send_message(uid,
                                f"🛑 <b>أُغلقت صفقتك التجريبية #{e['position_id']} ({esc(e['symbol'])}) عند وقف الخسارة</b>\n"
                                f"النتيجة: <b>{e['pnl']:+,.2f} USD</b>\n"
                                f"<i>الخسارة كانت محدودة سلفاً بخطة الوقف — الالتزام بالإدارة يحمي رأس المال.</i>",
                                parse_mode="HTML")
                        elif e["kind"] == "TP":
                            await app.bot.send_message(uid,
                                f"🎯 <b>أُغلقت صفقتك التجريبية #{e['position_id']} ({esc(e['symbol'])}) على الهدف</b>\n"
                                f"النتيجة: <b>{e['pnl']:+,.2f} USD</b> ✨\n"
                                f"<i>أُضيفت للرصيد فوراً — تابع لوحة حسابك التجريبي.</i>",
                                parse_mode="HTML")
                        elif e["kind"] == "HALF_TP1":
                            await app.bot.send_message(uid,
                                f"💧 <b>تنبيه تأمين الربح — وصل السعر نصف الطريق نحو الهدف الأول</b>\n"
                                f"▎🌐 {esc(e['symbol'])} · {'شراء' if e.get('direction') == 'BUY' else 'بيع'}\n"
                                f"▎✅ أُغلق <b>50%</b> من الصفقة تلقائياً ({e['closed_lots']:g} lot) وحقق: <b>{e['pnl']:+,.2f} USD</b>\n"
                                f"▎🛡 نقلنا وقف الخسارة إلى نقطة الدخول — الصفقة المتبقية ({e['remaining_lots']:g} lot) تعمل بلا مخاطرة نحو الأهداف التالية\n"
                                f"▎💰 سعر اللحظة: <code>{e.get('price', 0):,.2f}</code>",
                                parse_mode="HTML")
                        elif e["kind"] == "PARTIAL_DONE":
                            await app.bot.send_message(uid,
                                f"💧 أُغلق الجزء المتبقي من صفقتك #{e['position_id']} بالكامل ({e['pnl']:+,.2f} USD).",
                                parse_mode="HTML")
                    except Exception:
                        pass
        except asyncio.CancelledError:
            raise
        except Exception:
            log.exception("demo loop failed")
        await asyncio.sleep(120)


async def channels_loop(app):
    """Scheduled auto-analysis & auto-signals to linked channels (Mecca time)."""
    from fysb.handlers import admin_channels
    while True:
        try:
            for ch in admin_channels.due_channels():
                try:
                    if ch["auto_analysis"]:
                        await admin_channels.post_analysis(ch["id"], app.bot, ch)
                    if ch["auto_signals"]:
                        await admin_channels.post_signal(ch["id"], app.bot, ch)
                except Exception:
                    log.exception("channel auto-post failed cid=%s", ch["id"])
        except asyncio.CancelledError:
            raise
        except Exception:
            log.exception("channels loop failed")
        await asyncio.sleep(120)


async def post_init(app):
    global _TASKS
    _TASKS = [
        asyncio.create_task(market_engine_loop(app), name="fysb-market-engine"),
        asyncio.create_task(tracker_loop(app), name="fysb-tracker"),
        asyncio.create_task(subscriptions_loop(app), name="fysb-subs"),
        asyncio.create_task(demo_loop(app), name="fysb-demo"),
        asyncio.create_task(channels_loop(app), name="fysb-channels"),
    ]
    post_init._tasks_ref = _TASKS
    log.info("background loops started (5)")


async def post_shutdown(app):
    for t in getattr(post_init, "_tasks_ref", []) or []:
        t.cancel()
    for t in getattr(post_init, "_tasks_ref", []) or []:
        try:
            await t
        except asyncio.CancelledError:
            pass
    log.info("background loops stopped")
