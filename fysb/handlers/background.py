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


def _armed_scan_timeframes() -> list[str]:
    """v6.24.0 (#5) Multi-timeframe auto scanner list from `engine_scan_timeframes`
    intersected with enabled+verified rows in `timeframe_catalog`."""
    raw = (db.get_setting("engine_scan_timeframes", "M15,H1") or "M15,H1").strip()
    wanted = [x.strip().upper() for x in raw.split(",") if x.strip()]
    with db.conn() as c:
        enabled = {
            r["code"].upper()
            for r in c.execute("SELECT code FROM timeframe_catalog WHERE enabled=1 AND verified=1").fetchall()
        }
    tfs = [tf for tf in wanted if not enabled or tf in enabled]
    if not tfs:
        tfs = ["M15"] if ("M15" in enabled or not enabled) else [sorted(enabled)[0]]
    return tfs


def _pin_admin_delivery(signal_id: int, msg) -> None:
    """v6.24.0 (#4) Pin Admin's auto-signal message_id in signal_deliveries so
    tracker.py live-edits the Admin's exact signal card on TP1/TP2/TP3/SL."""
    if not config.ADMIN_ID or msg is None:
        return
    message_id = getattr(msg, "message_id", None)
    chat_id = getattr(msg, "chat_id", None)
    if chat_id is None:
        chat = getattr(msg, "chat", None)
        chat_id = getattr(chat, "id", None)
    if not message_id or chat_id is None:
        return
    with db.conn() as c:
        c.execute(
            """INSERT OR IGNORE INTO signal_deliveries(user_id, signal_id, delivered_at, chat_id, message_id)
               VALUES(?,?,?,?,?)""",
            (int(config.ADMIN_ID), int(signal_id), config.now(), str(chat_id), int(message_id)),
        )
        c.execute(
            "UPDATE signal_deliveries SET chat_id=?, message_id=? WHERE user_id=? AND signal_id=?",
            (str(chat_id), int(message_id), int(config.ADMIN_ID), int(signal_id)),
        )


def _build_free_trial_drip_payload(item: dict) -> tuple[str, list]:
    """v6.24.0 (#8) Build luxury FREE-trial drip card + 1-tap upgrade buttons."""
    from fysb.services import subscriptions as subs
    from fysb.ui import keyboards as kb
    from fysb.ui.cards import render_luxury_upgrade_card

    uid = int(item["user_id"])
    stage = str(item["stage"])
    hours_left = int(item.get("hours_left") or 0)
    trial_days = int(item.get("trial_days") or 7)
    target = subs.next_higher_plan("FREE")
    t_code = (target["code"] if target else "PRO").upper()
    t_title = target["title"] if target else "PRO"
    t_price = f"{float(target['price']):g}$" if target else "19$"

    if stage == "expired":
        paid_plans = sorted(
            [p for p in subs._plans_map().values() if str(p.get("code") or "").upper() != "FREE"],
            key=lambda p: float(p.get("price") or 0),
        )
        reason = f"انتهت فترتك المجانية ({trial_days} يوم) — قم بالترقية الآن للاستمرار في الإشارات الحية"
        text = render_luxury_upgrade_card(
            reason,
            current_plan=subs.plan_for(uid),
            paid_plans=paid_plans,
            recommended_plan=target,
            trial_expired=True,
        )
        rows = [
            [kb.btn(f"⚡ الترقية السريعة إلى {t_title} ({t_price})", f"u:plan:{t_code}", style="success")],
            [
                kb.btn("👑 VIP", "u:plan:VIP", style="primary"),
                kb.btn("🏆 ROYAL", "u:plan:ROYAL", style="primary"),
            ],
            [kb.btn("💎 مقارنة جميع الباقات", "u:plans")],
        ]
        return text, rows

    urgency = "🚨 تنبيه أخير" if stage == "24h" else "⏳ تذكير ملكي"
    text = (
        f"{urgency} — <b><u>فترتك التجريبية المجانية توشك على الانتهاء</u></b>\n\n"
        f"<blockquote>"
        f"▎⏱ الوقت المتبقي: <b>~{hours_left} ساعة</b> (من أصل {trial_days} أيام)\n"
        f"▎✨ رَقِّ حسابك الآن إلى <b>{t_title}</b> ({t_price}/شهرياً) لتحصل على:\n"
        f"▎   • <b>4 إشارات يومية + 4 تحليلات فورية</b>\n"
        f"▎   • أسواق <b>الذهب + العملات الرئيسية</b> على <b>M15/M30/H1/H4</b>\n"
        f"▎   • استراتيجيات <b>Price Action · Fibonacci · Ichimoku</b>\n"
        f"▎   • تتبع حي مستمر بدون انقطاع!</blockquote>"
    )
    rows = [
        [kb.btn(f"🚀 الترقية الآن إلى {t_title} ({t_price})", f"u:plan:{t_code}", style="success")],
        [kb.btn("💎 استعراض جميع الباقات", "u:plans", style="primary")],
    ]
    return text, rows


async def market_engine_loop(app):
    """Signal-only scanner — real data, honest rejections, never places orders.

    Works WITHOUT any API key: the hidden real-time provider chain (gold/crypto)
    serves the engine; keyed providers add forex coverage.
    v6.24.0: Multi-timeframe scanner + live-editable Admin card + FREE tier companion pick.
    """
    from fysb.engines.signal_engine import (
        admin_accepts_market,
        emit_to_users,
        generate_signal,
    )

    async def bot_send(uid: int, text: str, reply_markup=None):
        # Return the sent Message — emit_to_users pins its chat/message ids so
        # the tracker can edit this exact original card live later.
        if reply_markup is not None:
            try:
                return await app.bot.send_message(uid, text, reply_markup=reply_markup, parse_mode="HTML")
            except Exception:
                from fysb.ui.keyboards import strip_button_styles
                return await app.bot.send_message(
                    uid, text, reply_markup=strip_button_styles(reply_markup), parse_mode="HTML",
                )
        return await app.bot.send_message(uid, text, parse_mode="HTML")

    bot_send._bot = app.bot

    while True:
        try:
            # v6.25.0 (#2): 30-min pre-news warning to Admin for High-Impact events
            if config.ADMIN_ID:
                try:
                    from fysb.engines import news_guard as _ng
                    for ev in await afn.run(_ng.due_pre_news_alerts, 30):
                        await _safe_send(
                            app.bot,
                            config.ADMIN_ID,
                            f"📰 <b><u>تنبيه خبر اقتصادي قوي بعد ~{int(ev.get('minutes_delta') or 30)} دقيقة</u></b>\n"
                            f"<blockquote>▎🔴 العملة: <b>{ev.get('currency')}</b> — <b>{ev.get('title')}</b>\n"
                            f"▎🛡 درع الأخبار سيحمي الإشارات الجديدة تلقائياً حول وقت الخبر.</blockquote>",
                        )
                except Exception:
                    pass
            if await afn.run(db.get_setting, "engine_enabled", "0") == "1" \
                    and not await afn.run(db.kill_switch_on) \
                    and await afn.run(db.get_setting, "signal_push_enabled", "1") == "1":
                markets = await afn.run(_armed_market_rows)
                scan_tfs = await afn.run(_armed_scan_timeframes)
                for m in markets:
                    for tf in scan_tfs:
                        try:
                            result = await asyncio.to_thread(generate_signal, m["symbol"], tf)
                            if result["status"] == "SIGNAL_CREATED":
                                sig = await afn.run(_signal_row_by_id, result["signal_id"])
                                if sig:
                                    # emit_to_users already awaits opposite_close_advisory cleanly
                                    stats = await emit_to_users(dict(sig), bot_send, bot=app.bot, include_channels=True)
                                    log.info("auto signal #%s (%s %s) emitted: %s",
                                             result["signal_id"], m["symbol"], tf, stats)
                                    if await afn.run(admin_accepts_market, m["symbol"]):
                                        try:
                                            from fysb.engines.tracker import seq_of
                                            from fysb.ui.cards import render_signal_card
                                            mrow = await afn.run(_market_row, m["symbol"])
                                            seq = await afn.run(seq_of, result["signal_id"])
                                            card = render_signal_card(
                                                dict(sig), user_view=False,
                                                row=dict(mrow) if mrow else None)
                                            card += (f"\n\n<blockquote>📡 <b>إشارة آلية جديدة #{seq}"
                                                     f" ({m['symbol']} · {tf}) — جودة {result.get('score')}/100</b>\n"
                                                     f"▎📤 التسليم: {stats['sent']} مستلم"
                                                     f" · قنوات: {stats.get('channels_sent', 0)}"
                                                     f" · تجاوز الحد: {stats.get('skipped_quota', 0)}"
                                                     f" · فشل: {stats.get('failed', 0)}</blockquote>")
                                            admin_msg = await app.bot.send_message(config.ADMIN_ID, card, parse_mode="HTML")
                                            # v6.24.0 (#4): pin Admin message_id for live tracker edits!
                                            await afn.run(_pin_admin_delivery, result["signal_id"], admin_msg)
                                            # v6.26.0 (#13): auto zero-cost institutional signal chart PNG
                                            from fysb.ui import charts as _charts
                                            if await afn.run(_charts.charts_enabled):
                                                from fysb.handlers.user_pages import _build_signal_chart_png
                                                png_b, _ = await asyncio.to_thread(_build_signal_chart_png, dict(sig), False)
                                                if png_b:
                                                    import io as _io
                                                    await app.bot.send_photo(
                                                        chat_id=config.ADMIN_ID,
                                                        photo=_io.BytesIO(png_b),
                                                        caption=f"📊 شارت الإشارة الآلية #{seq} · <b>{m['symbol']}</b> {sig['direction']} ({tf})",
                                                        parse_mode="HTML",
                                                    )
                                        except Exception:
                                            pass
                                # v6.24.0 (#6): deliver companion FREE-tier pick if one was created
                                free_sid = result.get("free_fallback_signal_id")
                                if free_sid:
                                    free_sig = await afn.run(_signal_row_by_id, free_sid)
                                    if free_sig:
                                        f_stats = await emit_to_users(
                                            dict(free_sig), bot_send, bot=app.bot, include_channels=False
                                        )
                                        log.info("free companion signal #%s (%s %s): %s",
                                                 free_sid, m["symbol"], tf, f_stats)
                        except Exception as e:
                            log.warning("engine scan failed for %s %s: %s", m["symbol"], tf, type(e).__name__)
                        await asyncio.sleep(config.MARKET_SYMBOL_DELAY_SECONDS)
        except asyncio.CancelledError:
            raise
        except Exception:
            log.exception("market engine loop iteration failed")
        interval = await afn.run(db.get_setting_int, "engine_interval_seconds", 300)
        await asyncio.sleep(max(60, interval))


def _norm_chat_id(chat_id):
    """Normalize numeric string chat IDs ('-100123...') to int for PTB."""
    if isinstance(chat_id, str):
        s = chat_id.strip()
        if s.lstrip("-").isdigit():
            return int(s)
        return s
    return chat_id


async def _safe_send(bot, chat_id, text: str) -> bool:
    """Send an HTML Telegram message (user DM or linked channel) with RetryAfter protection."""
    from telegram.error import RetryAfter
    cid = _norm_chat_id(chat_id)
    for _ in range(2):
        try:
            await bot.send_message(cid, text, parse_mode="HTML")
            await asyncio.sleep(0.05)
            return True
        except RetryAfter as exc:
            wait = min(float(getattr(exc, "retry_after", 2.0) or 2.0) + 0.5, 30.0)
            await asyncio.sleep(wait)
        except Exception as exc:
            log.warning("_safe_send failed chat_id=%s: %s", chat_id, exc)
            return False
    return False


async def _safe_edit(bot, chat_id, message_id: int, text: str) -> bool:
    """Edit an HTML Telegram message in place with FloodWait / RetryAfter protection."""
    from telegram.error import RetryAfter
    cid = _norm_chat_id(chat_id)
    for _ in range(2):
        try:
            await bot.edit_message_text(text, chat_id=cid, message_id=int(message_id), parse_mode="HTML")
            await asyncio.sleep(0.05)
            return True
        except RetryAfter as exc:
            wait = min(float(getattr(exc, "retry_after", 2.0) or 2.0) + 0.5, 30.0)
            await asyncio.sleep(wait)
        except Exception as exc:
            if "message is not modified" not in str(exc).lower():
                log.warning("_safe_edit failed chat_id=%s mid=%s: %s", chat_id, message_id, exc)
            return False
    return False


async def tracker_loop(app):
    """Signal lifecycle updates from real prices + expiry sweep + half-TP1 alerts
    + LIVE-CARD EDITS: the user's ORIGINAL signal message is re-rendered on
    every transition and meaningful price move — the first card is the board.

    Tracking runs UNCONDITIONALLY (every 60s): open signals are obligations the
    platform already issued — the engine toggle governs generating NEW signals
    only. A paused engine must never leave issued signals unattended.
    """
    from fysb.engines import tracker
    while True:
        try:
            result = await asyncio.to_thread(tracker.check_open_signals, config.market_api_key())
            if result["stats"]["updated"]:
                log.info("tracker updated %s signals", result["stats"]["updated"])
            for uid, text in result["notifications"]:
                await _safe_send(app.bot, uid, text)
            for uid, chat_id, message_id, text in result.get("edits", []):
                await _safe_edit(app.bot, chat_id, message_id, text)
            # v6.26.0 (#14): Auto Before/After Verified Proof Chart on TP2_HIT / TP3_HIT
            for ps in result.get("proof_signals", []):
                try:
                    from fysb.ui import charts as _charts
                    from fysb.ui import keyboards as _kb
                    if config.ADMIN_ID and await afn.run(_charts.charts_enabled):
                        png = await asyncio.to_thread(_charts.render_proof_chart, ps, ps.get("candles") or [])
                        if png:
                            import io as _io
                            sid = int(ps["id"])
                            pips = float(ps.get("pips_result") or 0.0)
                            cap = (
                                f"🏆 <b>شارت إثبات صفقة رابحة #{sid}</b> — "
                                f"<b>{ps['symbol']}</b> {ps['direction']} · <b>{pips:+.1f} pips</b> ({ps['status']})\n"
                                "<i>اضغط أدناه لنشر الإثبات في جميع قنواتك المفعّلة بضغطة واحدة.</i>"
                            )
                            pub_kb = _kb.InlineKeyboardMarkup([[
                                _kb.btn("🏆 نشر الإثبات للقنوات الآن", f"a:sigproof:{sid}", style="success"),
                            ]])
                            await app.bot.send_photo(
                                chat_id=config.ADMIN_ID,
                                photo=_io.BytesIO(png),
                                caption=cap,
                                reply_markup=pub_kb,
                                parse_mode="HTML",
                            )
                except Exception:
                    pass
            expired, expiry_edits, expiry_notes = await afn.run(tracker.expire_with_alerts)
            for uid, chat_id, message_id, text in expiry_edits:
                await _safe_edit(app.bot, chat_id, message_id, text)
            for uid, text in expiry_notes:
                await _safe_send(app.bot, uid, text)
            if expired:
                log.info("expired %s stale signals", expired)
        except asyncio.CancelledError:
            raise
        except Exception:
            log.exception("tracker loop failed")
        await asyncio.sleep(60)


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
            # v6.24.0 (#8): FREE Trial luxury drip reminders (48h / 24h / expired)
            from telegram import InlineKeyboardMarkup
            from fysb.ui.keyboards import strip_button_styles
            for item in await afn.run(subs.free_trial_due_reminders):
                try:
                    drip_txt, drip_rows = await afn.run(_build_free_trial_drip_payload, item)
                    markup = InlineKeyboardMarkup(drip_rows)
                    try:
                        await app.bot.send_message(item["user_id"], drip_txt, reply_markup=markup, parse_mode="HTML")
                    except Exception:
                        await app.bot.send_message(
                            item["user_id"], drip_txt,
                            reply_markup=strip_button_styles(markup), parse_mode="HTML",
                        )
                    await afn.run(subs.mark_free_trial_reminded, item["user_id"], item["stage"])
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
    """Scheduled auto-analysis & auto-signals to linked channels (Mecca time)
    + VIP Gatekeeper periodic expiry sweep (v6.21.0)."""
    from fysb.handlers import admin_channels
    from fysb.services import partners as partners_svc
    tick = 0
    while True:
        try:
            for ch in await afn.run(admin_channels.due_channels):
                try:
                    partner = await afn.run(partners_svc.partner_for_channel, ch)
                    is_partner_ch = int(ch.get("owner_partner_id") or 0) > 0
                    if is_partner_ch and (not partner or not partner.get("enabled")):
                        continue
                    if ch["auto_analysis"] and (not is_partner_ch or partner.get("perm_auto_analysis")):
                        await admin_channels.post_analysis(ch["id"], app.bot, ch)
                    if ch["auto_signals"] and (not is_partner_ch or partner.get("perm_auto_signals")):
                        await admin_channels.post_signal(ch["id"], app.bot, ch)
                except Exception:
                    log.exception("channel auto-post failed cid=%s", ch["id"])
            # VIP Gatekeeper expiry sweep every ~10 minutes (every 2 loop ticks)
            tick += 1
            if tick % 2 == 1:
                try:
                    sw = await partners_svc.sweep_expired_vip_members(app.bot)
                    if sw.get("removed"):
                        log.info("VIP gatekeeper sweep removed %s expired member(s)", sw["removed"])
                except Exception:
                    log.exception("VIP gatekeeper sweep failed")
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


async def scheduled_charts_loop(app):
    """v7.0.2: Periodic live candlestick chart updates for open RUNNING/TP1/TP2 trades
    every N minutes (5 / 10 / 15 / 30 / 60 min) as configured by Admin."""
    import io as _io
    import time as _time
    from fysb.handlers.admin_trading import _active_channel_chat_ids, _open_running_signals_sync
    from fysb.handlers.user_pages import build_signal_chart_bundle
    from fysb.theme import esc
    from fysb.ui import charts as _charts
    from fysb.ui import keyboards as _kb

    last_sent_ts = 0.0
    while True:
        try:
            sched_min = await afn.run(db.get_setting_int, "chart_schedule_minutes", 0)
            enabled = await afn.run(_charts.charts_enabled)
            now_m = _time.monotonic()
            if enabled and sched_min >= 5 and (now_m - last_sent_ts) >= (sched_min * 60):
                last_sent_ts = now_m
                sigs = await afn.run(_open_running_signals_sync, 3)
                if sigs and config.ADMIN_ID:
                    to_ch = (await afn.run(db.get_setting, "chart_schedule_channels", "0") or "0") == "1"
                    chat_ids = await afn.run(_active_channel_chat_ids) if to_ch else []
                    for sig in sigs:
                        try:
                            b = await asyncio.to_thread(build_signal_chart_bundle, sig, False)
                            if not b["png"]:
                                continue
                            # Auto-upgrade to proof banner if live floating profit >= +20 pips
                            if b["pips"] >= 20.0 and not b["is_proof"]:
                                b = await asyncio.to_thread(build_signal_chart_bundle, sig, True)
                            cap = (
                                f"📊 <b><u>تحديث شارت دوري (كل {sched_min}د) — {esc(sig['symbol'])} {sig['direction']}</u></b>\n"
                                f"▎⏱ {esc(sig['timeframe'])} · 🧠 {esc(sig['strategy'])}\n"
                                f"{b['pips_line']}\n"
                                f"▎🛡 متابعة حية تلقائية من محرك ForexYemeni Royal."
                            )
                            pub_kb = _kb.InlineKeyboardMarkup([[
                                _kb.btn("🏆 نشر الإثبات للقنوات الآن", f"a:sigproof:{sig['id']}", style="success"),
                            ]])
                            await app.bot.send_photo(
                                chat_id=config.ADMIN_ID,
                                photo=_io.BytesIO(b["png"]),
                                caption=cap,
                                reply_markup=pub_kb,
                                parse_mode="HTML",
                            )
                            for cid in chat_ids:
                                try:
                                    target = int(cid) if cid.lstrip("-").isdigit() else cid
                                    await app.bot.send_photo(
                                        chat_id=target,
                                        photo=_io.BytesIO(b["png"]),
                                        caption=cap,
                                        parse_mode="HTML",
                                    )
                                except Exception:
                                    pass
                        except Exception:
                            log.exception("scheduled chart send failed for signal #%s", sig.get("id"))
        except asyncio.CancelledError:
            raise
        except Exception:
            log.exception("scheduled_charts_loop failed")
        await asyncio.sleep(30)


async def webapp_tunnel_loop(app):
    """v7.2.0: Zero-config Auto-HTTPS Cloudflare Tunnel for Bot-Hosting / VPS.
    Automatically brings Telegram Mini App & Admin Web Panel online without any
    website, domain, or manual terminal commands whenever WEBAPP_URL is unset."""
    from fysb.webapp import server as _wsrv
    from fysb.webapp import tunnel as _tun
    from fysb.ui import keyboards as _kb

    notified_url = ""
    health_ticks = 0
    while True:
        try:
            auto_db = (await afn.run(db.get_setting, "webapp_auto_tunnel", "1") or "1") == "1"
            should_run = bool(
                config.WEBAPP_ENABLED
                and config.WEBAPP_AUTO_TUNNEL
                and auto_db
                and not config.WEBAPP_URL_STATIC
            )
            if should_run:
                # Ensure embedded HTTP server is running before opening tunnel
                if getattr(post_init, "_webapp_srv", None) is None:
                    post_init._webapp_srv = await _wsrv.start_webapp_server()
                if getattr(post_init, "_webapp_srv", None) is not None:
                    # Periodic live health verification every ~3 min to catch stale 1033 tunnels
                    if _tun.is_tunnel_alive():
                        health_ticks += 1
                        if health_ticks >= 9:
                            health_ticks = 0
                            ok, _ = await _tun.verify_https_health(_tun.tunnel_status()["url"], retries=2, delay=1.5)
                            if not ok:
                                log.warning("WEBAPP TUNNEL: live health probe failed — restarting tunnel")
                                await _tun.stop_tunnel()
                    if not _tun.is_tunnel_alive():
                        health_ticks = 0
                        url = await _tun.start_tunnel(port=config.WEBAPP_PORT)
                        if url and url != notified_url and config.ADMIN_ID:
                            notified_url = url
                            prov = _tun.tunnel_status().get("provider") or "HTTPS"
                            try:
                                await app.bot.send_message(
                                    config.ADMIN_ID,
                                    "🌐 <b><u>التطبيق المصغّر الملكي جاهز ومفحوص 100% (HTTP 200)!</u></b>\n"
                                    "<blockquote>"
                                    f"▎🟢 الرابط المباشر (HTTPS): <code>{url}</code>\n"
                                    f"▎🛰 المزوّد النشط: <b>{prov}</b> (تم التحقق الحي قبل الإرسال)\n"
                                    "▎📱 أصبح زر التطبيق ظاهراً الآن في القائمة الرئيسية للمستخدمين ولك!"
                                    "</blockquote>",
                                    reply_markup=_kb.InlineKeyboardMarkup([
                                        [_kb.webapp_btn("🖥 فتح لوحة الإدارة الويب", f"{url}/admin", style="success")],
                                        [_kb.webapp_btn("📱 فتح التطبيق المصغر (Mini App)", f"{url}/", style="primary")],
                                    ]),
                                    parse_mode="HTML",
                                )
                            except Exception:
                                pass
            elif _tun.is_tunnel_alive():
                await _tun.stop_tunnel()
                notified_url = ""
        except asyncio.CancelledError:
            raise
        except Exception:
            log.exception("webapp_tunnel_loop failed")
        await asyncio.sleep(20)


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
    from fysb.engines.crypto_ws import crypto_ws_loop
    _TASKS = [
        asyncio.create_task(market_engine_loop(app), name="fysb-market-engine"),
        asyncio.create_task(tracker_loop(app), name="fysb-tracker"),
        asyncio.create_task(subscriptions_loop(app), name="fysb-subs"),
        asyncio.create_task(demo_loop(app), name="fysb-demo"),
        asyncio.create_task(channels_loop(app), name="fysb-channels"),
        asyncio.create_task(watchers_loop(app), name="fysb-watchers"),
        asyncio.create_task(crypto_ws_loop(app), name="fysb-crypto-ws"),
        asyncio.create_task(scheduled_charts_loop(app), name="fysb-sched-charts"),
    ]
    # v7.0.0 / v7.2.0: start embedded Mini App & Admin Web Panel HTTP server
    # whenever WEBAPP_ENABLED=1 and either a static WEBAPP_URL or Auto-HTTPS
    # Tunnel (Bot-Hosting) is active.
    post_init._webapp_srv = None
    auto_tun_db = (await afn.run(db.get_setting, "webapp_auto_tunnel", "1") or "1") == "1"
    want_webapp = bool(
        config.WEBAPP_ENABLED
        and (config.WEBAPP_URL or (config.WEBAPP_AUTO_TUNNEL and auto_tun_db))
    )
    if want_webapp:
        try:
            from fysb.webapp.server import start_webapp_server
            post_init._webapp_srv = await start_webapp_server()
        except Exception:
            log.warning("webapp server startup skipped")
    if config.WEBAPP_ENABLED and config.WEBAPP_AUTO_TUNNEL and not config.WEBAPP_URL_STATIC:
        _TASKS.append(asyncio.create_task(webapp_tunnel_loop(app), name="fysb-webapp-tunnel"))
    post_init._tasks_ref = _TASKS
    log.info("background loops started (%d)", len(_TASKS))


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
    try:
        from fysb.webapp import tunnel as _tun
        await _tun.stop_tunnel()
    except Exception:
        pass
    srv = getattr(post_init, "_webapp_srv", None)
    if srv is not None:
        try:
            srv.close()
            await srv.wait_closed()
        except Exception:
            pass
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
