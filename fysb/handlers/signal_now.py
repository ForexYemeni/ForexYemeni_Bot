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

from fysb import afn, config, db
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


def _fetch_signal_by_id(sig_id: int):
    """(v6.11.0 thread-offload helper) full signal row by id."""
    with db.conn() as c:
        return c.execute("SELECT * FROM signals WHERE id=?", (sig_id,)).fetchone()


def _fetch_market_row(symbol: str):
    """(v6.11.0 thread-offload helper) market_catalog row for a symbol."""
    with db.conn() as c:
        r = c.execute("SELECT * FROM market_catalog WHERE symbol=?", (symbol,)).fetchone()
    return dict(r) if r else None


def _delivery_exists(uid: int, sig_id: int) -> bool:
    """(v6.11.0 thread-offload helper) delivery dedupe check."""
    with db.conn() as c:
        return c.execute("SELECT 1 FROM signal_deliveries WHERE user_id=? AND signal_id=?",
                         (uid, sig_id)).fetchone() is not None


def _enabled_timeframes() -> set:
    """(v6.11.0 thread-offload helper) enabled+verified timeframe codes."""
    with db.conn() as c:
        return {r["code"] for r in c.execute(
            "SELECT code FROM timeframe_catalog WHERE enabled=1 AND verified=1").fetchall()}


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


def _strat_label(code: str) -> str:
    """v6.17.0: luxury Arabic label for strategy code, including 'ALL'."""
    c = (code or "").strip().upper()
    return "جميع المنهجيات (أقوى فرصة)" if c == "ALL" else c


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
    ch = await afn.run(choices_for, uid, d.get("is_admin", False))
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
    plan = await afn.run(subs.plan_for, uid)
    p_tfs = [x.strip().upper() for x in (plan["timeframes"] or "").split(",") if x.strip()]
    enabled = await afn.run(_enabled_timeframes)
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
    strategies = (await afn.run(choices_for, uid, d.get("is_admin", False)))["strategies"]
    if not strategies:
        await _render(q, "⛔ لا توجد منهجيات مفعلة وموثقة متاحة — فعّل استراتيجيات من قسم الاستراتيجيات.",
                      kb.user_nav(), bot)
        return
    d["strategies"] = strategies
    context.user_data[DRAFT_KEY] = d
    rows = [[kb.btn(
        f"✨ جميع المنهجيات — أقوى فرصة تلقائياً ({len(strategies)})",
        f"{_prefix(d['is_admin'])}:signowstrat:ALL",
        style="success",
    )]]
    rows += [[kb.btn(f"🧠 {s}", f"{_prefix(d['is_admin'])}:signowstrat:{s}") for s in strategies[i:i + 3]]
             for i in range(0, len(strategies), 3)]
    body = (f"✅ <b>{esc(d.get('symbol', ''))}</b> · ⏱ <b>{tf_name(tf)}</b> · <b>{STYLE_AR.get(d.get('style', ''), '')}</b>\n"
            "🧠 <b><u>المرحلة 4/4 — اختر المنهجية (المفعلة والمختبرة فعلاً):</u></b>\n"
            "<blockquote>"
            "▎✨ <b>جميع المنهجيات:</b> يفحص المحرك كل المنهجيات المفعّلة والمسموحة في باقتك معاً ويختار أقوى فرصة بأعلى جودة تلقائياً.\n"
            "▎🎯 أو اختر منهجية محددة من الأسفل للفحص الدقيق عليها فقط."
            "</blockquote>")
    await _render(q, body, kb.user_nav(rows) if not d.get("is_admin") else kb.admin_nav(rows), bot)


async def _deliver_private_signal(bot, uid: int, sig: dict) -> bool:
    """Deliver a private SIGNAL_NOW signal ONLY to its requester (uid), consume
    their daily quota, pin delivery refs for live tracker edits, and run demo
    auto-execute if armed."""
    from fysb.engines.entry_state import birth_pulse_line
    from fysb.engines.signal_engine import record_delivery_refs
    from fysb.services import user_trade
    from fysb.ui.cards import render_signal_card

    got = await afn.run(_delivery_exists, uid, sig["id"])
    if not got:
        if not await afn.run(subs.try_consume_signal, uid, sig):
            return False
    row = await afn.run(_fetch_market_row, sig["symbol"])
    text = render_signal_card(sig, user_view=True, row=row)
    pulse = birth_pulse_line(sig, row)
    if pulse:
        text += "\n\n<blockquote>" + pulse + "</blockquote>"
    personal = await afn.run(user_trade.personal_block, sig, uid)
    if personal:
        text += "\n\n<blockquote>" + personal + "</blockquote>"
    try:
        msg = await bot.send_message(uid, text, parse_mode="HTML")
        await afn.run(record_delivery_refs, uid, sig["id"], msg)
        exec_res = await afn.run(user_trade.auto_execute, sig, uid)
        if exec_res and exec_res.get("ok"):
            await bot.send_message(uid, user_trade.execution_card(exec_res, sig), parse_mode="HTML")
        return True
    except Exception:
        if not got:
            await afn.run(subs.release_signal, uid, sig["id"])
        return False


async def pick_strategy(q, uid: int, context, bot, strategy: str) -> None:
    d = context.user_data.get(DRAFT_KEY) or {}
    allowed_strats = list(d.get("strategies") or [])
    strat_u = (strategy or "").strip().upper()
    if not d or d.get("stage") != "strategy" or (strat_u != "ALL" and strat_u not in allowed_strats):
        return await open_flow(q, uid, context, bot, uid == config.ADMIN_ID)
    strategy = strat_u
    is_admin = bool(d.get("is_admin"))
    symbol, tf = d["symbol"], d["tf"]
    style = d.get("style", "")
    # v6.17.0: 'ALL' scans ALL enabled+verified methodologies allowed by the
    # user's plan (or all enabled+verified for admin) and picks the strongest.
    only_strats = allowed_strats if strategy == "ALL" else [strategy]
    # v6.14.0 FIX: preserve DRAFT_KEY with the chosen strategy so that when
    # NO_SETUP occurs, tapping «بحث تلقائي» immediately creates the watcher!
    d["strategy"] = strategy
    d["stage"] = "ready_watch"
    context.user_data[DRAFT_KEY] = d
    scan_desc = (
        f"جميع المنهجيات ({len(only_strats)} منهجية) لاختيار أقوى فرصة…"
        if strategy == "ALL" else f"منهجية {esc(strategy)}…"
    )
    await _render(q, f"🚀 <b>جارٍ تشغيل المحرك الحقيقي على {scan_desc}</b>\n"
                     "<i>السوق والمنهجية والإطار تُفحص فعلياً — لا إشارات مصطنعة أبداً.</i>", None, bot)
    sig_source = "ADMIN_SIGNAL_NOW" if is_admin else "SIGNAL_NOW"
    try:
        res = await asyncio.to_thread(
            generate_signal, symbol, tf, False, only_strats, sig_source, uid
        )
    except Exception as e:
        log.warning("signow generate failed: %s", type(e).__name__)
        res = {"status": "ERROR", "reason": type(e).__name__}
    if res.get("status") == "SIGNAL_CREATED":
        context.user_data.pop(DRAFT_KEY, None)
        from fysb.engines.signal_engine import emit_to_users
        from fysb.engines.tracker import seq_of
        sig = await afn.run(_fetch_signal_by_id, res["signal_id"])
        sig = dict(sig) if sig else {}
        if is_admin:
            stats = {"sent": 0, "channels_sent": 0, "skipped_quota": 0, "failed": 0}
            if sig:
                from fysb.engines.entry_state import birth_pulse_line
                from fysb.engines.signal_engine import record_delivery_refs
                from fysb.handlers.inputs import bot_sender_from
                from fysb.ui.cards import render_signal_card
                stats = await emit_to_users(sig, bot_sender_from(bot), bot=bot, include_channels=True)
                # v6.20.0: Always deliver the FULL live signal card to Admin and pin
                # its (uid, sid, chat_id, message_id) so tracker updates edit it live!
                if not await afn.run(_delivery_exists, uid, sig["id"]):
                    try:
                        row = await afn.run(_fetch_market_row, sig["symbol"])
                        card = render_signal_card(sig, user_view=True, row=row)
                        pulse = birth_pulse_line(sig, row)
                        if pulse:
                            card += "\n\n<blockquote>" + pulse + "</blockquote>"
                        msg = await bot.send_message(uid, card, parse_mode="HTML")
                        await afn.run(record_delivery_refs, uid, sig["id"], msg)
                    except Exception:
                        pass
            delivery_line = (
                f"▎📤 وصلت لـ <b>{stats['sent']}</b> مستلم مؤهل"
                + (f" · قنوات: <b>{stats.get('channels_sent', 0)}</b>" if stats.get("channels_sent") else "")
            )
        else:
            # v6.14.0 PRIVACY: regular user's Signal-Now is delivered PRIVATELY
            # to the requester only — never broadcast publicly to other users/channels.
            delivered = await _deliver_private_signal(bot, uid, sig) if sig else False
            delivery_line = (
                "▎🔒 إشارة خاصة لك وحدك — وصلتك أعلاه (استُهلكت من حدك اليومي)"
                if delivered else
                "▎⚠️ تعذر تسليم الإشارة (تأكد من توفر رصيد في حدك اليومي)"
            )
        win_strat = sig.get("strategy") or strategy
        strat_disp = (
            f"جميع المنهجيات ← الفائزة: {esc(win_strat)}"
            if strategy == "ALL" else esc(win_strat)
        )
        note = (f"✅ <b><u>ولدت إشارة حقيقية 100% — الإشارة #{seq_of(sig['id'])}</u></b>\n"
                "<blockquote>"
                f"▎🌐 {esc(symbol)} · ⏱ {tf_name(tf)} · 🧠 {strat_disp} · {STYLE_AR.get(style, '')}\n"
                f"▎⭐ الجودة: <b>{res.get('score', 0)}/100</b>\n"
                f"{delivery_line}\n"
                "▎📡 التتبع اللحظي بدأ من الآن حتى الأهداف أو الوقف — أول بطاقة وصلتك تُحدَّث حية"
                "</blockquote>")
        rows = [[kb.btn("📡 الإشارات المباشرة", "u:signals" if not is_admin else "a:signals", style="success")]]
        await _render(q, note, kb.user_nav(rows) if not is_admin else kb.admin_nav(rows), bot)
        return
    if res.get("status") == "NO_SETUP":
        rows = [[kb.btn(f"⏰ بحث تلقائي كل {p} دقيقة", f"{_prefix(is_admin)}:signowwatch:{p}", style="primary")]
                for p in WATCH_PERIODS]
        strat_ar = esc(_strat_label(strategy))
        body = (f"⛔ <b>لا توجد إشارة مؤهلة الآن — ظروف السوق لم تحقق {strat_ar}</b>\n"
                "<blockquote>"
                "▎🚫 المحرك يرفض تصنيع إشارات — هذه أمينة المنصة 100%.\n"
                "▎🔔 <b>البحث التلقائي الملكي:</b> اختر الفترة بالأسفل وسيبقى المحرك يفحص "
                f"<b>{esc(symbol)} · {tf_name(tf)} · {strat_ar}</b> كل مرة حتى تصدر إشارة حقيقية، "
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
    wid = await afn.run(start_watcher, uid, symbol, tf, strategy, style, minutes)
    w = await afn.run(active_watcher, uid)
    body = ("⏰ <b><u>البحث التلقائي الملكي مفعّل</u></b>\n"
            "<blockquote>"
            f"▎🎯 الهدف: إشارة حقيقية على <b>{esc(symbol)} · {tf_name(tf)} · {esc(_strat_label(strategy))}</b>\n"
            f"▎⏲ الفترة: كل <b>{minutes} دقيقة</b> يفحص المحرك السوق فعلياً\n"
            f"▎⌛ يتوقف تلقائياً بعد {WATCH_MAX_HOURS} ساعة أو فور صدور الإشارة\n"
            f"▎🚫 لا تصنيع — إن لم تتحقق الشروط يبقى الصمت أمانة"
            "</blockquote>")
    rows = [[kb.btn("🔴 إلغاء البحث التلقائي", f"{'a' if is_admin else 'u'}:watchoff:{wid}")],
            [kb.btn("📡 الإشارات المباشرة", "u:signals" if not is_admin else "a:signals")]]
    await _render(q, body, kb.user_nav(rows) if not is_admin else kb.admin_nav(rows), bot)


async def stop_watch(q, uid: int, context, bot, wid: int) -> None:
    await afn.run(cancel_watchers, uid)
    await _render(q, "🔴 أُلغي البحث التلقائي بالكامل — لا يوجد أي فحص مجدول باسمك الآن.",
                  kb.user_nav([[kb.btn("🚀 إشارة الآن", "u:signow")]]), bot)


# ---------------------------------------------------------------------------
# Background pass — called from the watchers loop every minute
# ---------------------------------------------------------------------------
async def run_due_watchers(app) -> None:
    from fysb.ui.cards import render_signal_card
    for w in await afn.run(due_watchers):
        uid = w["user_id"]
        if w.get("_expired"):
            try:
                await app.bot.send_message(uid,
                    "⌛ انتهت مدة البحث التلقائي (24 ساعة) دون فرصة مؤهلة — أُوقف بأدب. "
                    "فعّله من جديد من «إشارة الآن» متى أردت.", parse_mode="HTML")
            except Exception:
                pass
            continue
        is_admin_w = (uid == config.ADMIN_ID)
        sig_source = "ADMIN_SIGNAL_NOW" if is_admin_w else "SIGNAL_NOW"
        w_strat = (w["strategy"] or "").strip().upper()
        if w_strat == "ALL":
            only_strats = (await afn.run(choices_for, uid, is_admin_w))["strategies"]
            if not only_strats:
                await afn.run(reschedule_watcher, w["id"], w["interval_minutes"])
                continue
        else:
            only_strats = [w_strat]
        try:
            res = await asyncio.to_thread(
                generate_signal, w["symbol"], w["timeframe"], False, only_strats, sig_source, uid
            )
        except Exception as e:
            log.warning("watcher %s generation failed: %s", w["id"], type(e).__name__)
            res = {"status": "ERROR"}
        if res.get("status") == "SIGNAL_CREATED":
            await afn.run(finish_watcher, w["id"], "FOUND")
            sig = await afn.run(_fetch_signal_by_id, res["signal_id"])
            sig = dict(sig) if sig else {}
            if not sig:
                continue
            from fysb.engines.signal_engine import emit_to_users
            from fysb.engines.tracker import seq_of
            from fysb.handlers.inputs import bot_sender_from
            if is_admin_w:
                stats = await emit_to_users(sig, bot_sender_from(app.bot), bot=app.bot, include_channels=True)
                sent_n = stats.get("sent", 0)
            else:
                ok_priv = await _deliver_private_signal(app.bot, uid, sig)
                sent_n = 1 if ok_priv else 0
            try:
                await app.bot.send_message(uid,
                    f"🔔 <b>البحث التلقائي أثمر — صدرت الإشارة الحقيقية #{seq_of(sig['id'])}</b> "
                    f"({esc(sig['symbol'])} · {esc(sig['strategy'])}) وجاءتك أعلاه، وتوقف البحث.",
                    parse_mode="HTML")
            except Exception:
                pass
            log.info("watcher %s FOUND signal #%s (sent=%s)", w["id"], sig["id"], sent_n)
        elif res.get("status") in ("NO_SETUP", "REJECTED", "DUPLICATE", "ERROR"):
            await afn.run(reschedule_watcher, w["id"], w["interval_minutes"])
