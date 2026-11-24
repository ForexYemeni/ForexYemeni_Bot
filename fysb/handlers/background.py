"""Background loops — market engine, lifecycle tracker, subscription maintenance.

Single-instance protected (lock file), graceful shutdown, per-loop error
isolation: one failing loop never kills the bot.
"""
import asyncio
import os
from pathlib import Path

from fysb import afn, config, db
from fysb.config import log

LOCK_FILE = config.BASE_DIR / "fysb.lock"
_held_lock_paths: set[str] = set()


def _lock_key() -> str:
    try:
        return str(LOCK_FILE.resolve())
    except Exception:
        return str(LOCK_FILE)


def release_lock_file() -> bool:
    """Release fysb.lock if owned by the current PID (safe before os.execve).

    Prevents the Linux os.execve self-deadlock during /update where the newly
    exec'd main.py inherits the exact same PID and /proc start_tick.
    """
    _held_lock_paths.discard(_lock_key())
    try:
        if not LOCK_FILE.exists():
            return True
        text = LOCK_FILE.read_text().strip()
        pid_text = text.split(":")[0] if text else ""
        if not pid_text.isdigit() or int(pid_text) == os.getpid():
            LOCK_FILE.unlink(missing_ok=True)
            return True
    except OSError:
        pass
    return False


def _proc_start_tick(pid: int) -> str:
    """Linux: field 22 of /proc/<pid>/stat — jiffies at process start.

    Identifies a *specific process incarnation*, immune to PID reuse. In
    short-lived containers (bot-hosting.net, Pterodactyl) a restarted process
    frequently gets the SAME low PID as the dead one — a bare PID check then
    locks the bot out of its own stale lock. The start tick disambiguates.
    """
    try:
        raw = Path(f"/proc/{pid}/stat").read_bytes().decode("utf-8", "replace")
        tail = raw[raw.rfind(")") + 2:].split()
        return tail[19] if len(tail) > 19 else ""
    except Exception:
        return ""


def _is_genuine_second_instance(pid: int) -> bool:
    """True only when `pid` is a live python run of main.py FROM this folder.

    Used for legacy locks (plain PID, no start tick): a reused PID belonging
    to any other program (pip, daemon shell, container init) must NOT block.
    """
    if Path("/proc").is_dir():
        try:
            cwd = Path(f"/proc/{pid}/cwd").resolve()
            if cwd != Path(config.BASE_DIR).resolve():
                return False
            cmd = Path(f"/proc/{pid}/cmdline").read_bytes().decode("utf-8", "replace")
            return "main.py" in cmd
        except Exception:
            return False  # unreadable → cannot prove a genuine twin → take over
    return True  # non-Linux (no /proc): keep the conservative legacy behaviour


def _lock_identity_alive(pid: int, boot_tick: str) -> bool:
    """Is the lock held by a genuinely running process incarnation?

    Within one boot, /proc start ticks grow monotonically — so a matching tick
    proves the SAME incarnation is alive; any mismatch proves the lock belongs
    to a dead predecessor whose PID slot got reused (the bot-hosting bug).

    For our OWN pid: only alive if THIS Python interpreter actually acquired the
    lock (blocks accidental double-acquire in the same process, while allowing a
    freshly os.execve()'d process from /update to cleanly take over a leftover
    lock even when upgrading from pre-v6.12.0).
    """
    my_tick = _proc_start_tick(os.getpid())
    if boot_tick:
        if pid == os.getpid():
            return (_lock_key() in _held_lock_paths) and bool(my_tick) and boot_tick == my_tick
        return _pid_alive(pid) and _proc_start_tick(pid) == boot_tick
    # Legacy plain-PID lock (pre-6.6.3): the new format always carries a tick,
    # so a tick-less lock naming OUR pid can only be a dead predecessor.
    if pid == os.getpid():
        return False
    if not _pid_alive(pid):
        return False
    return _is_genuine_second_instance(pid)


class SingleInstanceLock:
    """Cross-platform single-instance guard, restart-proof.

    Lock content: `<pid>:<start-tick>` (tick empty where /proc is absent).
    On acquire, ANY of these means "stale — take over silently":
      PID equals ours · PID dead · start-tick mismatch (PID reused) ·
      legacy lock whose PID is not a main.py run from this folder.
    Only a genuine live second instance from the SAME folder blocks.
    """

    def __init__(self):
        self.acquired = False

    def acquire(self) -> bool:
        try:
            if LOCK_FILE.exists():
                if not self._holder_is_live():
                    LOCK_FILE.unlink(missing_ok=True)
                else:
                    return False
            fd = os.open(str(LOCK_FILE), os.O_CREAT | os.O_EXCL | os.O_WRONLY)
            os.write(fd, f"{os.getpid()}:{_proc_start_tick(os.getpid())}".encode())
            os.close(fd)
            self.acquired = True
            _held_lock_paths.add(_lock_key())
            return True
        except FileExistsError:
            return False
        except OSError:
            return True  # cannot enforce — proceed but log
        except Exception:
            return True

    def _holder_is_live(self) -> bool:
        try:
            text = LOCK_FILE.read_text().strip()
        except OSError:
            return False  # unreadable lock — treat as stale
        parts = text.split(":")
        pid_text = parts[0] if parts else ""
        if not pid_text.isdigit():
            return False  # garbage lock — stale
        boot_tick = parts[1] if len(parts) > 1 else ""
        return _lock_identity_alive(int(pid_text), boot_tick)

    def release(self):
        try:
            if self.acquired:
                _held_lock_paths.discard(_lock_key())
                LOCK_FILE.unlink(missing_ok=True)
        except OSError:
            pass
        finally:
            self.acquired = False


def _pid_alive(pid: int) -> bool:
    try:
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
        # Return the sent Message — emit_to_users pins its chat/message ids so
        # the tracker can edit this exact original card live later.
        return await app.bot.send_message(uid, text, parse_mode="HTML")

    bot_send._bot = app.bot

    while True:
        try:
            if await afn.run(db.get_setting, "engine_enabled", "0") == "1" \
                    and not await afn.run(db.kill_switch_on) \
                    and await afn.run(db.get_setting, "signal_push_enabled", "1") == "1":
                markets = await afn.run(_armed_market_rows)
                for m in markets:
                    try:
                        result = await asyncio.to_thread(generate_signal, m["symbol"])
                        if result["status"] == "SIGNAL_CREATED":
                            sig = await afn.run(_signal_row_by_id, result["signal_id"])
                            if sig:
                                from fysb.engines.signal_engine import emit_to_users
                                stats = await emit_to_users(dict(sig), bot_send)
                                log.info("auto signal #%s emitted: %s", result["signal_id"], stats)
                                try:
                                    # v6.8.0: the admin receives the REAL full card —
                                    # the same royal board the users received, plus
                                    # delivery counters. v6.8.1: the number shown is
                                    # the REAL sequential number (same as the
                                    # signals center), never the internal id.
                                    from fysb.engines.tracker import seq_of
                                    from fysb.ui.cards import render_signal_card
                                    mrow = await afn.run(_market_row, m["symbol"])
                                    seq = await afn.run(seq_of, result["signal_id"])
                                    card = render_signal_card(
                                        dict(sig), user_view=False,
                                        row=dict(mrow) if mrow else None)
                                    card += (f"\n\n<blockquote>📡 <b>إشارة آلية جديدة #{seq}"
                                             f" ({m['symbol']}) — جودة {result.get('score')}/100</b>\n"
                                             f"▎📤 التسليم: {stats['sent']} مستلم"
                                             f" · قنوات: {stats.get('channels_sent', 0)}"
                                             f" · تجاوز الحد: {stats.get('skipped_quota', 0)}"
                                             f" · فشل: {stats.get('failed', 0)}</blockquote>")
                                    await app.bot.send_message(config.ADMIN_ID, card, parse_mode="HTML")
                                except Exception:
                                    pass
                    except Exception as e:
                        log.warning("engine scan failed for %s: %s", m["symbol"], type(e).__name__)
                    await asyncio.sleep(config.MARKET_SYMBOL_DELAY_SECONDS)
        except asyncio.CancelledError:
            raise
        except Exception:
            log.exception("market engine loop iteration failed")
        interval = await afn.run(db.get_setting_int, "engine_interval_seconds", 300)
        await asyncio.sleep(max(60, interval))


async def tracker_loop(app):
    """Signal lifecycle updates from real prices + expiry sweep + half-TP1 alerts
    + LIVE-CARD EDITS: the user's ORIGINAL signal message is re-rendered on
    every transition and meaningful price move — the first card is the board.

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
            for uid, chat_id, message_id, text in result.get("edits", []):
                try:
                    await app.bot.edit_message_text(text, chat_id=int(chat_id),
                                                    message_id=int(message_id), parse_mode="HTML")
                except Exception:
                    pass  # deleted message / identical text / blocked user — never fatal
            expired, expiry_edits = await afn.run(tracker.expire_with_card_edits)
            for uid, chat_id, message_id, text in expiry_edits:
                try:
                    await app.bot.edit_message_text(text, chat_id=int(chat_id),
                                                    message_id=int(message_id), parse_mode="HTML")
                except Exception:
                    pass
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
            expired_targets = await afn.run(subs.mark_expired_and_notify_targets)
            for t in expired_targets:
                try:
                    await app.bot.send_message(t["user_id"],
                                               f"⌛ انتهت صلاحية اشتراكك في باقة <b>{t['plan']}</b>. جدّد من قسم الباقات.",
                                               parse_mode="HTML")
                except Exception:
                    pass
            for s in await afn.run(subs.due_reminders):
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
                    await afn.run(subs.mark_reminded, s["id"], s["stage"])
                except Exception:
                    pass
            day = config.now()[:10]
            if day != last_digest_day and config.ADMIN_ID:
                pending = db.get_setting and await afn.run(_pending_payments_count)
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
            uids = await afn.run(_open_position_uids)
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
            for ch in await afn.run(admin_channels.due_channels):
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


async def watchers_loop(app):
    """v6.10.0 — «إشارة الآن» auto-search (signal_watchers): every minute the
    DUE watchers re-run the REAL engine for their exact market+strategy. A
    found setup is emitted like any engine signal and the watcher stops; a
    quiet market just reschedules. Nothing is ever manufactured."""
    from fysb.handlers import signal_now
    while True:
        try:
            await signal_now.run_due_watchers(app)
        except asyncio.CancelledError:
            raise
        except Exception:
            log.exception("watchers loop failed")
        await asyncio.sleep(60)


async def post_init(app):
    global _TASKS
    # v6.8.2: pre-warm the PostgreSQL pool in a worker thread so the FIRST
    # button press after a boot never pays the cold WAN connect cost
    # (connect_timeout=15s worst case on Supabase). SQLite: no-op.
    if config.USE_PG:
        try:
            await asyncio.to_thread(_warm_pg)
        except Exception:
            log.warning("pg pool warm-up skipped (will self-heal on demand)")
    _TASKS = [
        asyncio.create_task(market_engine_loop(app), name="fysb-market-engine"),
        asyncio.create_task(tracker_loop(app), name="fysb-tracker"),
        asyncio.create_task(subscriptions_loop(app), name="fysb-subs"),
        asyncio.create_task(demo_loop(app), name="fysb-demo"),
        asyncio.create_task(channels_loop(app), name="fysb-channels"),
        asyncio.create_task(watchers_loop(app), name="fysb-watchers"),
    ]
    post_init._tasks_ref = _TASKS
    log.info("background loops started (6)")


def _warm_pg() -> None:
    """Warm the PG pool + prove connectivity with one cheap statement."""
    from fysb import pglayer
    # v6.11.0: four pre-opened keepalive sessions — with parallel worker-thread
    # DB calls the first buttons never pay a WAN connect, even under bursts.
    pglayer.warm(4)
    c = pglayer._acquire()
    try:
        cur = c.cursor()
        cur.execute("SELECT 1")
        cur.fetchall()
        cur.close()
    finally:
        pglayer._release(c)


async def post_shutdown(app):
    for t in getattr(post_init, "_tasks_ref", []) or []:
        t.cancel()
    for t in getattr(post_init, "_tasks_ref", []) or []:
        try:
            await t
        except asyncio.CancelledError:
            pass
    log.info("background loops stopped")


# --- v6.11.0 thread-offload helpers ---
def _armed_market_rows() -> list[dict]:
    """(v6.11.0 thread-offload helper) enabled+verified market rows for the engine."""
    with db.conn() as c:
        return [dict(r) for r in c.execute(
            "SELECT symbol FROM market_catalog WHERE enabled=1 AND verified=1").fetchall()]


def _signal_row_by_id(signal_id: int):
    """(v6.11.0 thread-offload helper) one full signal row by id."""
    with db.conn() as c:
        return c.execute("SELECT * FROM signals WHERE id=?", (signal_id,)).fetchone()


def _market_row(symbol: str):
    """(v6.11.0 thread-offload helper) market_catalog row for card rendering."""
    with db.conn() as c:
        return c.execute("SELECT * FROM market_catalog WHERE symbol=?", (symbol,)).fetchone()


def _open_position_uids() -> list[int]:
    """(v6.11.0 thread-offload helper) users with OPEN demo positions."""
    with db.conn() as c:
        return [r["user_id"] for r in c.execute(
            "SELECT DISTINCT user_id FROM demo_positions WHERE status='OPEN'").fetchall()]
