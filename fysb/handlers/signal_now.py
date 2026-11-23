"""«إشارة الآن» — the royal staged signal-generation flow (admin + users).

Owner demand (v6.10.0, seventh report): a «Signal Now» feature identical to
the channels' one, available in the ADMIN account AND USER accounts, that
passes 100% REAL stages:
    1. trading style  (سكالبينج / يومي / سوينج)
    2. market         (per the user's PLAN — admin: every live market)
    3. timeframe      (per plan ∩ style)
    4. strategy       (per plan ∩ enabled+verified+IMPLEMENTED engines)
then the full pipeline runs for the CHOSEN strategy only. If a real setup
exists, the signal is stored, delivered to every eligible user, tracked from
birth to death, and auto-executed on the requester's demo account when his
auto-trade is on. The requester's own quota is consumed like any delivery.

If NO real setup exists: the honest rejection card appears WITH the royal
auto-search (watcher) buttons — a chosen interval (15m/30m/1h/3h/6h) keeps
the REAL engine checking that exact market+strategy until a signal is born,
then notifies and stops. Watchers auto-expire after 24h. Nothing is ever
fabricated: no setup → no signal, forever.
"""
import asyncio

from fysb import config, db
from fysb.config import log
from fysb.engines.signal_engine import generate_signal
from fysb.services import subscriptions as subs
from fysb.services import users as users_service
from fysb.theme import esc, tf_name
from fysb.ui import keyboards as kb

STYLE_STAGES = [
    ("SCALP", "⚡ سكالبينج — أطر سريعة (M1-M30)"),
    ("INTRADAY", "📈 يومي — أطر متوسطة (M15-H4)"),
    ("SWING", "🧭 سوينج — أطر كبرى (H1-W1)"),
]
STYLE_TFS = {
    "SCALP": ["M1", "M5", "M15", "M30"],
    "INTRADAY": ["M15", "M30", "H1", "H4"],
    "SWING": ["H1", "H4", "D1", "W1"],
}
STYLE_AR = {"SCALP": "سكالبينج", "INTRADAY": "يومي", "SWING": "سوينج"}
WATCH_PERIODS = [15, 30, 60, 180, 360]
WATCH_MAX_HOURS = 24

DRAFT_KEY = "signow_draft"


# ---------------------------------------------------------------------------
# Watchers (the honest auto-search) — stored in signal_watchers
# ---------------------------------------------------------------------------
def start_watcher(uid: int, symbol: str, tf: str, strategy: str, style: str,
                  interval_minutes: int) -> int:
    from datetime import datetime, timedelta, timezone
    now = datetime.now(timezone.utc)
    with db.conn() as c:
        c.execute("DELETE FROM signal_watchers WHERE user_id=? AND status='ACTIVE'", (uid,))
        cur = c.execute("""INSERT INTO signal_watchers(user_id,symbol,timeframe,strategy,style,
                           interval_minutes,status,next_run,created_at,expires_at)
                           VALUES(?,?,?,?,?,?,'ACTIVE',?,?,?)""",
                        (uid, symbol, tf, strategy, style, max(5, int(interval_minutes)),
                         (now + timedelta(minutes=max(5, int(interval_minutes)))).isoformat(),
                         config.now(),
                         (now + timedelta(hours=WATCH_MAX_HOURS)).isoformat()))
        return cur.lastrowid


def cancel_watchers(uid: int) -> int:
    with db.conn() as c:
        cur = c.execute("UPDATE signal_watchers SET status='CANCELLED' WHERE user_id=? AND status='ACTIVE'",
                        (uid,))
        return cur.rowcount


def active_watcher(uid: int) -> dict | None:
    with db.conn() as c:
        r = c.execute("SELECT * FROM signal_watchers WHERE user_id=? AND status='ACTIVE' ORDER BY id DESC LIMIT 1",
                      (uid,)).fetchone()
    return dict(r) if r else None


def due_watchers() -> list[dict]:
    """Watchers whose next_run has arrived (and not expired)."""
    now = config.now()
    with db.conn() as c:
        rows = [dict(r) for r in c.execute(
            "SELECT * FROM signal_watchers WHERE status='ACTIVE' AND next_run<=?", (now,)).fetchall()]
    out = []
    for w in rows:
        if w["expires_at"] and w["expires_at"] <= now:
            with db.conn() as c:
                c.execute("UPDATE signal_watchers SET status='EXPIRED' WHERE id=?", (w["id"],))
            w["_expired"] = True
        out.append(w)
    return out


def reschedule_watcher(wid: int, interval_minutes: int) -> None:
    from datetime import datetime, timedelta, timezone
    nxt = (datetime.now(timezone.utc) + timedelta(minutes=max(5, int(interval_minutes)))).isoformat()
    with db.conn() as c:
        c.execute("UPDATE signal_watchers SET next_run=? WHERE id=?", (nxt, wid))


def finish_watcher(wid: int, status: str) -> None:
    with db.conn() as c:
        c.execute("UPDATE signal_watchers SET status=? WHERE id=?", (status, wid))


# ---------------------------------------------------------------------------
# Eligible-choice computation (plan ∩ live catalog ∩ style)
# ---------------------------------------------------------------------------
def _live_markets() -> list[str]:
    with db.conn() as c:
        return [r["symbol"] for r in c.execute(
            "SELECT symbol FROM market_catalog WHERE enabled=1 AND verified=1 ORDER BY symbol").fetchall()]


def _live_strategies() -> list[str]:
    from fysb.engines.strategies import REGISTRY
    with db.conn() as c:
        rows = c.execute("SELECT code FROM strategy_catalog WHERE enabled=1 AND verified=1").fetchall()
    from fysb.services import strategy_stats
    return strategy_stats.ordered_codes([r["code"] for r in rows if REGISTRY.get(r["code"]) is not None])


def choices_for(uid: int, is_admin: bool) -> dict:
    plan = subs.plan_for(uid)
    p_markets = [x.strip().upper() for x in (plan["markets"] or "").split(",") if x.strip()]
    p_tfs = [x.strip().upper() for x in (plan["timeframes"] or "").split(",") if x.strip()]
    p_strats = [x.strip().upper() for x in (plan["strategies"] or "").split(",") if x.strip()]
    markets = _live_markets()
    strategies = _live_strategies()
    if not is_admin:
        markets = [m for m in markets if m in p_markets] or [m for m in p_markets if m in _live_markets()]
        strategies = [s for s in strategies if s in p_strats]
    return {"markets": markets, "strategies": strategies, "plan": plan}


# ---------------------------------------------------------------------------
# Staged flow (callback-driven; the draft lives in context.user_data)
# ---------------------------------------------------------------------------
async def open_flow(q, uid: int, context, bot, is_admin: bool) -> None:
    context.user_data[DRAFT_KEY] = {"stage": "style", "is_admin": is_admin}
    rows = [[kb.btn(label, f"{'a' if is_admin else 'u'}:signowstyle:{code}")] for code, label in STYLE_STAGES]
    body = ("🚀 <b><u>إشارة الآن — التدفق الملكي بمراحل حقيقية 100%</u></b>\n"
            "<blockquote>"
            "▎🎯 سيولّد المحرك إشارة حقيقية 100% على المنهجية التي تختارها — بلا أي إشارات مصطنعة.\n"
            "▎📋 المراحل: نوع التداول ← السوق ← الإطار الزمني ← المنهجية ← التوليد.\n"
            "▎🔍 إن لم تتوفر فرصة حقيقية: يُرفض بصدق ويُعرض زر البحث التلقائي حتى تصدر الإشارة.\n"
            + ("▎👑 وضع الإدارة: كل الأسواق والمنهجيات المفعلة متاحة أمامك."
               if is_admin else "▎💎 الخيارات مقيّدة بباقتك — نفس بوابات التسليم تماماً.")
            + "</blockquote>\n"
            "⏱ <b>المرحلة 1/4 — اختر نوع التداول:</b>")
    await _render(q, body, kb.user_nav(rows) if not is_admin else kb.admin_nav(rows), bot)


async def _render(q, text, markup, bot):
    from fysb.handlers.common import render
    await render(q, text, markup, bot)


def _prefix(is_admin: bool) -> str:
    return "a" if is_admin else "u"


async def pick_style(q, uid: int, context, bot, style: str) -> None:
    d = context.user_data.get(DRAFT_KEY) or {}
    if not d:
        return await open_flow(q, uid, context, bot, uid == config.ADMIN_ID)
    style = style.upper()
    if style not in STYLE_TFS:
        style = "INTRADAY"
    d["style"] = style
    d["stage"] = "market"
    context.user_data[DRAFT_KEY] = d
    ch = choices_for(uid, d.get("is_admin", False))
    mkts = ch["markets"]
    if not mkts:
        await _render(q, "⛔ لا توجد أسواق مفعلة وموثقة متاحة لباقتك بعد — تواصل مع الإدارة.",
                      kb.user_nav(), bot)
        return
    rows = [[kb.btn(("🌐 " + m), f"{_prefix(d['is_admin'])}:signowsym:{m}") for m in mkts[i:i + 3]]
            for i in range(0, len(mkts), 3)]
    body = (f"✅ نوع التداول: <b>{STYLE_AR[style]}</b>\n"
            f"🌐 <b>المرحلة 2/4 — اختر السوق/الأداة:</b>\n"
            f"<i>{'كل الأسواق المفعلة والموثقة (وضع الإدارة).' if d.get('is_admin') else 'مقيّد بالأسواق المفعلة داخل باقتك.'}</i>")
    await _render(q, body, kb.user_nav(rows) if not d.get("is_admin") else kb.admin_nav(rows), bot)


async def pick_symbol(q, uid: int, context, bot, symbol: str) -> None:
    d = context.user_data.get(DRAFT_KEY) or {}
    if not d or d.get("stage") != "market":
        return await open_flow(q, uid, context, bot, uid == config.ADMIN_ID)
    symbol = symbol.upper()
    d["symbol"] = symbol
    d["stage"] = "tf"
    plan = subs.plan_for(uid)
    p_tfs = [x.strip().upper() for x in (plan["timeframes"] or "").split(",") if x.strip()]
    with db.conn() as c:
        enabled = {r["code"] for r in c.execute(
            "SELECT code FROM timeframe_catalog WHERE enabled=1 AND verified=1").fetchall()}
    usable = [t for t in STYLE_TFS.get(d.get("style", "INTRADAY"), []) if t in enabled]
    if not d.get("is_admin", False):
        usable = [t for t in usable if t in p_tfs]
    if not usable:
        await _render(q, "⛔ لا توجد أطر زمنية متاحة لهذا النمط داخل باقتك — اختر نمطاً آخر أو رقّ باقتك.",
                      kb.user_nav(), bot)
        return
    d["tfs"] = usable
    context.user_data[DRAFT_KEY] = d
    rows = [[kb.btn(f"⏱ {tf_name(t)}", f"{_prefix(d['is_admin'])}:signowtf:{t}") for t in usable[i:i + 4]]
            for i in range(0, len(usable), 4)]
    body = (f"✅ السوق: <b>{esc(symbol)}</b> · النمط: <b>{STYLE_AR.get(d.get('style', ''), '')}</b>\n"
            f"⏱ <b>المرحلة 3/4 — اختر الإطار الزمني:</b>")
    await _render(q, body, kb.user_nav(rows) if not d.get("is_admin") else kb.admin_nav(rows), bot)


async def pick_tf(q, uid: int, context, bot, tf: str) -> None:
    d = context.user_data.get(DRAFT_KEY) or {}
    if not d or d.get("stage") != "tf" or tf.upper() not in (d.get("tfs") or []):
        return await open_flow(q, uid, context, bot, uid == config.ADMIN_ID)
    tf = tf.upper()
    d["tf"] = tf
    d["stage"] = "strategy"
    strategies = choices_for(uid, d.get("is_admin", False))["strategies"]
    if not strategies:
        await _render(q, "⛔ لا توجد منهجيات مفعلة وموثقة متاحة — فعّل استراتيجيات من قسم الاستراتيجيات.",
                      kb.user_nav(), bot)
        return
    d["strategies"] = strategies
    context.user_data[DRAFT_KEY] = d
    rows = [[kb.btn(f"🧠 {s}", f"{_prefix(d['is_admin'])}:signowstrat:{s}") for s in strategies[i:i + 3]]
            for i in range(0, len(strategies), 3)]
    body = (f"✅ {esc(d.get('symbol', ''))} · ⏱ {tf_name(tf)} · {STYLE_AR.get(d.get('style', ''), '')}\n"
            f"🧠 <b>المرحلة 4/4 — اختر المنهجية (المفعلة والمختبرة فعلاً):</b>")
    await _render(q, body, kb.user_nav(rows) if not d.get("is_admin") else kb.admin_nav(rows), bot)


async def pick_strategy(q, uid: int, context, bot, strategy: str) -> None:
    d = context.user_data.get(DRAFT_KEY) or {}
    if not d or d.get("stage") != "strategy" or strategy.upper() not in (d.get("strategies") or []):
        return await open_flow(q, uid, context, bot, uid == config.ADMIN_ID)
    strategy = strategy.upper()
    is_admin = bool(d.get("is_admin"))
    symbol, tf = d["symbol"], d["tf"]
    style = d.get("style", "")
    context.user_data.pop(DRAFT_KEY, None)
    await _render(q, "🚀 <b>جارٍ تشغيل المحرك الحقيقي على اختيارك…</b>\n"
                     "<i>السوق والمنهجية والإطار تُفحص فعلياً — لا إشارات مصطنعة أبداً.</i>", None, bot)
    try:
        res = await asyncio.to_thread(generate_signal, symbol, tf, False, [strategy])
    except Exception as e:
        log.warning("signow generate failed: %s", type(e).__name__)
        res = {"status": "ERROR", "reason": type(e).__name__}
    if res.get("status") == "SIGNAL_CREATED":
        from fysb.engines.signal_engine import emit_to_users
        from fysb.engines.tracker import seq_of
        with db.conn() as c:
            sig = c.execute("SELECT * FROM signals WHERE id=?", (res["signal_id"],)).fetchone()
        sig = dict(sig) if sig else {}
        stats = {"sent": 0, "skipped_quota": 0, "failed": 0}
        if sig:
            from fysb.handlers.inputs import bot_sender_from
            stats = await emit_to_users(sig, bot_sender_from(bot))
            # The requester MUST hold his own copy too (quota consumed like any
            # delivery) — even if his notifications toggle or plan gate skipped him.
            with db.conn() as c:
                got = c.execute("SELECT 1 FROM signal_deliveries WHERE user_id=? AND signal_id=?",
                                (uid, sig["id"])).fetchone()
            if not got:
                from fysb.services import user_trade
                from fysb.ui.cards import render_signal_card
                if subs.try_consume_signal(uid, sig):
                    row = None
                    with db.conn() as c:
                        r = c.execute("SELECT * FROM market_catalog WHERE symbol=?", (sig["symbol"],)).fetchone()
                        row = dict(r) if r else None
                    text = render_signal_card(sig, user_view=True, row=row)
                    personal = user_trade.personal_block(sig, uid)
                    if personal:
                        text += "\n\n<blockquote>" + personal + "</blockquote>"
                    try:
                        msg = await bot.send_message(uid, text, parse_mode="HTML")
                        from fysb.engines.signal_engine import record_delivery_refs
                        record_delivery_refs(uid, sig["id"], msg)
                        exec_res = user_trade.auto_execute(sig, uid)
                        if exec_res and exec_res.get("ok"):
                            await bot.send_message(uid, user_trade.execution_card(exec_res, sig),
                                                   parse_mode="HTML")
                    except Exception:
                        pass
        note = (f"✅ <b><u>ولدت إشارة حقيقية 100% — الإشارة #{seq_of(sig['id'])}</u></b>\n"
                "<blockquote>"
                f"▎🌐 {esc(symbol)} · ⏱ {tf_name(tf)} · 🧠 {esc(strategy)} · {STYLE_AR.get(style, '')}\n"
                f"▎⭐ الجودة: <b>{res.get('score', 0)}/100</b>\n"
                f"▎📤 وصلت لـ <b>{stats['sent']}</b> مستلم مؤهل (حدك اليومي استُهلك كأي إشارة)\n"
                "▎📡 التتبع اللحظي بدأ من الآن حتى الأهداف أو الوقف — أول بطاقة وصلتك تُحدَّث حية"
                "</blockquote>")
        rows = [[kb.btn("📡 الإشارات المباشرة", "u:signals" if not is_admin else "a:signals")]]
        await _render(q, note, kb.user_nav(rows) if not is_admin else kb.admin_nav(rows), bot)
        return
    if res.get("status") == "NO_SETUP":
        rows = [[kb.btn(f"⏰ بحث تلقائي كل {p} دقيقة", f"{_prefix(is_admin)}:signowwatch:{p}")]
                for p in WATCH_PERIODS]
        body = ("⛔ <b>لا توجد إشارة مؤهلة الآن — ظروف السوق لم تحقق " + esc(strategy) + "</b>\n"
                "<blockquote>"
                "▎🚫 المحرك يرفض تصنيع إشارات — هذه أمينة المنصة 100%.\n"
                "▎🔔 <b>البحث التلقائي الملكي:</b> اختر الفترة بالأسفل وسيبقى المحرك يفحص "
                f"<b>{esc(symbol)} · {tf_name(tf)} · {esc(strategy)}</b> كل مرة حتى تصدر إشارة حقيقية، "
                "ثم يوقف البحث ويرسلها لك مباشرة.\n"
                "▎⌛ البحث التلقائي يعمل حتى 24 ساعة كحد أقصى ثم يتوقف بأدب، ويمكنك إلغاؤه في أي لحظة."
                "</blockquote>")
        await _render(q, body, kb.user_nav(rows) if not is_admin else kb.admin_nav(rows), bot)
        return
    reason = res.get("reason") or res.get("status", "")
    await _render(q, f"⛔ لم تُولد إشارة: <b>{esc(reason)}</b>\n<i>(سوق مغلق / بيانات غير متاحة / حد وقف محرك المخاطر)</i>",
                  kb.user_nav() if not is_admin else kb.admin_nav(), bot)


async def start_watch(q, uid: int, context, bot, minutes: int) -> None:
    minutes = minutes if minutes in WATCH_PERIODS else 30
    d = context.user_data.get(DRAFT_KEY) or {}
    symbol, tf, strategy, style = d.get("symbol"), d.get("tf"), d.get("strategy"), d.get("style", "")
    is_admin = bool(d.get("is_admin"))
    context.user_data.pop(DRAFT_KEY, None)
    if not (symbol and tf and strategy):
        await _render(q, "ابدأ من «إشارة الآن» أولاً ثم فعّل البحث التلقائي.", kb.user_nav(), bot)
        return
    wid = start_watcher(uid, symbol, tf, strategy, style, minutes)
    w = active_watcher(uid)
    body = ("⏰ <b><u>البحث التلقائي الملكي مفعّل</u></b>\n"
            "<blockquote>"
            f"▎🎯 الهدف: إشارة حقيقية على <b>{esc(symbol)} · {tf_name(tf)} · {esc(strategy)}</b>\n"
            f"▎⏲ الفترة: كل <b>{minutes} دقيقة</b> يفحص المحرك السوق فعلياً\n"
            f"▎⌛ يتوقف تلقائياً بعد {WATCH_MAX_HOURS} ساعة أو فور صدور الإشارة\n"
            f"▎🚫 لا تصنيع — إن لم تتحقق الشروط يبقى الصمت أمانة"
            "</blockquote>")
    rows = [[kb.btn("🔴 إلغاء البحث التلقائي", f"{'a' if is_admin else 'u'}:watchoff:{wid}")],
            [kb.btn("📡 الإشارات المباشرة", "u:signals" if not is_admin else "a:signals")]]
    await _render(q, body, kb.user_nav(rows) if not is_admin else kb.admin_nav(rows), bot)


async def stop_watch(q, uid: int, context, bot, wid: int) -> None:
    cancel_watchers(uid)
    await _render(q, "🔴 أُلغي البحث التلقائي بالكامل — لا يوجد أي فحص مجدول باسمك الآن.",
                  kb.user_nav([[kb.btn("🚀 إشارة الآن", "u:signow")]]), bot)


# ---------------------------------------------------------------------------
# Background pass — called from the watchers loop every minute
# ---------------------------------------------------------------------------
async def run_due_watchers(app) -> None:
    from fysb.ui.cards import render_signal_card
    for w in due_watchers():
        uid = w["user_id"]
        if w.get("_expired"):
            try:
                await app.bot.send_message(uid,
                    "⌛ انتهت مدة البحث التلقائي (24 ساعة) دون فرصة مؤهلة — أُوقف بأدب. "
                    "فعّله من جديد من «إشارة الآن» متى أردت.", parse_mode="HTML")
            except Exception:
                pass
            continue
        try:
            res = await asyncio.to_thread(generate_signal, w["symbol"], w["timeframe"], False, [w["strategy"]])
        except Exception as e:
            log.warning("watcher %s generation failed: %s", w["id"], type(e).__name__)
            res = {"status": "ERROR"}
        if res.get("status") == "SIGNAL_CREATED":
            finish_watcher(w["id"], "FOUND")
            with db.conn() as c:
                sig = c.execute("SELECT * FROM signals WHERE id=?", (res["signal_id"],)).fetchone()
            sig = dict(sig) if sig else {}
            if not sig:
                continue
            from fysb.engines.signal_engine import emit_to_users, record_delivery_refs
            from fysb.engines.tracker import seq_of
            from fysb.handlers.inputs import bot_sender_from
            stats = await emit_to_users(sig, bot_sender_from(app.bot))
            with db.conn() as c:
                got = c.execute("SELECT 1 FROM signal_deliveries WHERE user_id=? AND signal_id=?",
                                (uid, sig["id"])).fetchone()
            if not got and subs.try_consume_signal(uid, sig):
                row = None
                with db.conn() as c:
                    r = c.execute("SELECT * FROM market_catalog WHERE symbol=?", (sig["symbol"],)).fetchone()
                    row = dict(r) if r else None
                from fysb.services import user_trade
                text = render_signal_card(sig, user_view=True, row=row)
                personal = user_trade.personal_block(sig, uid)
                if personal:
                    text += "\n\n<blockquote>" + personal + "</blockquote>"
                try:
                    msg = await app.bot.send_message(uid, text, parse_mode="HTML")
                    record_delivery_refs(uid, sig["id"], msg)
                    exec_res = user_trade.auto_execute(sig, uid)
                    if exec_res and exec_res.get("ok"):
                        await app.bot.send_message(uid, user_trade.execution_card(exec_res, sig),
                                                   parse_mode="HTML")
                except Exception:
                    pass
            try:
                await app.bot.send_message(uid,
                    f"🔔 <b>البحث التلقائي أثمر — صدرت الإشارة الحقيقية #{seq_of(sig['id'])}</b> "
                    f"({esc(sig['symbol'])} · {esc(sig['strategy'])}) وجاءتك أعلاه، وتوقف البحث.",
                    parse_mode="HTML")
            except Exception:
                pass
            log.info("watcher %s FOUND signal #%s (sent=%s)", w["id"], sig["id"], stats.get("sent"))
        elif res.get("status") in ("NO_SETUP", "REJECTED", "DUPLICATE", "ERROR"):
            reschedule_watcher(w["id"], w["interval_minutes"])
