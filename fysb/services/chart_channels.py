"""Market-Filtered Chart-Only Channels Service (v7.3.0).

Allows Admin to register dedicated Telegram channels/groups inside the Royal
Charts Center, pick specific market(s) (e.g. XAUUSD, BTCUSD, EURUSD, or ALL),
and route ONLY luxury PNG charts (never text-only signal cards or analysis
posts) to each channel whenever:
  * mode='signal'   — a new signal is born on a matching market
  * mode='tracking' — a live lifecycle milestone hits (RUNNING/HALFWAY/TP/SL/BE)
  * mode='schedule' — the scheduled periodic chart ticker runs (5/10/15/30/60m)
"""
from __future__ import annotations

import io
import re
from typing import Iterable

from fysb import config, db, instruments
from fysb.config import log
from fysb.theme import dir_badge, esc, strategy_name, tf_name


def normalize_chat_input(raw: str) -> str:
    """Normalize Telegram channel/group input (@user, t.me/user, -100..., 100...).

    Raises ValueError on empty input or unsupported private invite links (t.me/+...).
    """
    s = str(raw or "").strip()
    if not s:
        raise ValueError("يرجى إدخال معرف القناة (@channel أو -100...)")
    # Strip full Telegram URLs: https://t.me/channel, t.me/channel, telegram.me/channel
    m = re.match(r"^(?:https?://)?(?:t\.me|telegram\.me)/([^?\s#]+)", s, flags=re.IGNORECASE)
    if m:
        slug = m.group(1).strip("/")
        if slug.startswith("+") or slug.lower().startswith("joinchat/"):
            raise ValueError(
                "روابط الدعوة الخاصة (t.me/+...) لا تعمل مع Bot API — استخدم المعرّف الرقمي للقناة (-100...) أو @username."
            )
        # Handle t.me/c/1234567890/12 -> -1001234567890
        mc = re.match(r"^c/(\d+)", slug, flags=re.IGNORECASE)
        if mc:
            return f"-100{mc.group(1)}"
        slug = slug.split("/")[0].lstrip("@")
        if not slug:
            raise ValueError("رابط القناة غير صالح")
        return f"@{slug}"
    # Bare positive 100xxxxxxxxxx -> -100xxxxxxxxxx
    if s.isdigit() and s.startswith("100") and len(s) >= 11:
        return f"-{s}"
    if s.lstrip("-").isdigit():
        return s
    if s.startswith("@"):
        return s
    # Bare username like MyForexChannel -> @MyForexChannel
    if re.match(r"^[A-Za-z][A-Za-z0-9_]{3,64}$", s):
        return f"@{s}"
    return s


def _norm_sym(sym: str) -> str:
    return str(sym or "").strip().upper().replace("/", "").replace(" ", "")


def parse_markets_csv(markets_csv: str) -> list[str]:
    raw = [x.strip().upper() for x in str(markets_csv or "").split(",") if x.strip()]
    if not raw or "ALL" in raw:
        return ["ALL"]
    seen: list[str] = []
    for x in raw:
        if x not in seen:
            seen.append(x)
    return seen


def market_matches(markets_csv: str, symbol: str) -> bool:
    mkts = parse_markets_csv(markets_csv)
    if "ALL" in mkts:
        return True
    target = _norm_sym(symbol)
    if not target:
        return False
    return any(_norm_sym(m) == target for m in mkts)


def format_markets_label(markets_csv: str) -> str:
    mkts = parse_markets_csv(markets_csv)
    if "ALL" in mkts:
        return "🌐 جميع الأسواق (ALL)"
    return " · ".join(mkts)


def list_channels(only_enabled: bool = False) -> list[dict]:
    with db.conn() as c:
        if only_enabled:
            rows = c.execute(
                "SELECT * FROM chart_channels WHERE enabled=1 ORDER BY id DESC"
            ).fetchall()
        else:
            rows = c.execute(
                "SELECT * FROM chart_channels ORDER BY id DESC"
            ).fetchall()
    return [dict(r) for r in rows]


def get_channel(cid: int) -> dict | None:
    with db.conn() as c:
        r = c.execute("SELECT * FROM chart_channels WHERE id=?", (int(cid),)).fetchone()
    return dict(r) if r else None


def find_by_chat_id(chat_id: str) -> dict | None:
    target = str(chat_id or "").strip().lower()
    if not target:
        return None
    for row in list_channels(False):
        if str(row.get("chat_id") or "").strip().lower() == target:
            return row
    return None


def add_channel(chat_id: str, title: str = "", markets: str = "ALL") -> int:
    cid_str = normalize_chat_input(chat_id)
    mkts = ",".join(parse_markets_csv(markets))
    now_s = config.now()
    existing = find_by_chat_id(cid_str)
    if existing:
        new_title = (title or existing.get("title") or cid_str).strip()[:80]
        with db.tx() as c:
            c.execute(
                """UPDATE chart_channels
                   SET title=?, markets=?, enabled=1, updated_at=?
                   WHERE id=?""",
                (new_title, mkts, now_s, int(existing["id"])),
            )
        return int(existing["id"])
    with db.tx() as c:
        cur = c.execute(
            """INSERT INTO chart_channels(
                   chat_id, title, markets, enabled,
                   send_on_signal, send_on_tracking, send_on_schedule,
                   last_sent, created_at, updated_at
               ) VALUES (?,?,?,1,1,1,1,'',?,?)""",
            (cid_str, (title or cid_str).strip()[:80], mkts, now_s, now_s),
        )
        return int(cur.lastrowid)


def toggle_channel(cid: int) -> bool:
    row = get_channel(cid)
    if not row:
        return False
    nxt = 0 if int(row.get("enabled") or 0) == 1 else 1
    with db.tx() as c:
        c.execute(
            "UPDATE chart_channels SET enabled=?, updated_at=? WHERE id=?",
            (nxt, config.now(), int(cid)),
        )
    return nxt == 1


def toggle_flag(cid: int, field: str) -> bool:
    if field not in ("send_on_signal", "send_on_tracking", "send_on_schedule"):
        return False
    row = get_channel(cid)
    if not row:
        return False
    nxt = 0 if int(row.get(field) or 0) == 1 else 1
    with db.tx() as c:
        c.execute(
            f"UPDATE chart_channels SET {field}=?, updated_at=? WHERE id=?",
            (nxt, config.now(), int(cid)),
        )
    return nxt == 1


def set_markets(cid: int, markets_csv: str) -> str:
    mkts = ",".join(parse_markets_csv(markets_csv))
    with db.tx() as c:
        c.execute(
            "UPDATE chart_channels SET markets=?, updated_at=? WHERE id=?",
            (mkts, config.now(), int(cid)),
        )
    return mkts


def update_channel(
    cid: int,
    *,
    title: str | None = None,
    markets: str | None = None,
    send_on_signal: bool | None = None,
    send_on_tracking: bool | None = None,
    send_on_schedule: bool | None = None,
) -> dict | None:
    """Update an existing chart channel's title, instruments/markets, and delivery modes (v8.5.1)."""
    row = get_channel(cid)
    if not row:
        return None
    new_title = (str(title).strip()[:80] if title is not None and str(title).strip() else row.get("title") or row.get("chat_id") or "")
    new_mkts = ",".join(parse_markets_csv(markets)) if markets is not None else (row.get("markets") or "ALL")
    sig_f = int(bool(send_on_signal)) if send_on_signal is not None else int(row.get("send_on_signal") or 0)
    trk_f = int(bool(send_on_tracking)) if send_on_tracking is not None else int(row.get("send_on_tracking") or 0)
    sch_f = int(bool(send_on_schedule)) if send_on_schedule is not None else int(row.get("send_on_schedule") or 0)
    with db.tx() as c:
        c.execute(
            """UPDATE chart_channels
               SET title=?, markets=?, send_on_signal=?, send_on_tracking=?,
                   send_on_schedule=?, updated_at=?
               WHERE id=?""",
            (new_title, new_mkts, sig_f, trk_f, sch_f, config.now(), int(cid)),
        )
    return get_channel(cid)


def toggle_market(cid: int, symbol: str) -> str:
    row = get_channel(cid)
    if not row:
        return "ALL"
    sym = str(symbol or "").strip().upper()
    if sym == "ALL":
        return set_markets(cid, "ALL")
    cur = parse_markets_csv(row.get("markets") or "ALL")
    if cur == ["ALL"]:
        return set_markets(cid, sym)
    if sym in cur:
        cur = [m for m in cur if m != sym]
        if not cur:
            return set_markets(cid, "ALL")
        return set_markets(cid, ",".join(cur))
    cur.append(sym)
    return set_markets(cid, ",".join(cur))


def delete_channel(cid: int) -> bool:
    with db.tx() as c:
        cur = c.execute("DELETE FROM chart_channels WHERE id=?", (int(cid),))
        return cur.rowcount > 0


def available_markets() -> list[str]:
    with db.conn() as c:
        rows = c.execute(
            "SELECT symbol FROM market_catalog WHERE enabled=1 ORDER BY symbol"
        ).fetchall()
    syms = [str(r["symbol"]).upper() for r in rows if r["symbol"]]
    return syms or ["XAUUSD", "BTCUSD", "EURUSD", "GBPUSD", "ETHUSD"]


def matching_channels(symbol: str, *, mode: str = "schedule") -> list[dict]:
    """Return enabled chart-only channels configured for `symbol` and `mode`."""
    flag_map = {
        "signal": "send_on_signal",
        "tracking": "send_on_tracking",
        "schedule": "send_on_schedule",
    }
    flag_col = flag_map.get(mode, "send_on_schedule")
    out: list[dict] = []
    for ch in list_channels(only_enabled=True):
        if int(ch.get(flag_col) or 0) != 1:
            continue
        if market_matches(ch.get("markets") or "ALL", symbol):
            out.append(ch)
    return out


def _norm_chat_target(chat_id: str):
    s = str(chat_id or "").strip()
    if s.lstrip("-").isdigit():
        return int(s)
    return s


def touch_last_sent(channel_ids: Iterable[int]) -> None:
    ids = [int(x) for x in channel_ids if x]
    if not ids:
        return
    now_s = config.now()
    with db.tx() as c:
        for cid in ids:
            c.execute("UPDATE chart_channels SET last_sent=? WHERE id=?", (now_s, cid))


def build_luxury_signal_chart_caption(sig: dict, metrics: dict | None = None, *, seq: int | None = None) -> str:
    """Luxury royal Arabic caption attached to newly-born signal charts."""
    sym = str(sig.get("symbol") or "XAUUSD").upper()
    direction = str(sig.get("direction") or "BUY").upper()
    tf = str(sig.get("timeframe") or "M15").upper()
    strat = strategy_name(sig.get("strategy") or "")
    score = int(sig.get("score") or 0)
    rr = float(sig.get("rr") or 0.0)
    entry = float(sig.get("entry") or 0.0)
    sl = float(sig.get("stop") or 0.0)
    tp1 = float(sig.get("tp1") or 0.0)
    tp2 = float(sig.get("tp2") or 0.0)
    tp3 = float(sig.get("tp3") or 0.0)
    num_part = f" #{seq}" if seq else ""
    live_line = ""
    if metrics and float(metrics.get("live_px") or 0) > 0:
        lp = float(metrics["live_px"])
        dp = float(metrics.get("display_pips") or 0.0)
        live_line = (
            f"▎💹 السعر اللحظي: <code>{instruments.fmt_price(sym, lp)}</code>"
            f" · <b>{dp:+.1f} pips</b>\n"
        )
    return (
        f"👑 <b><u>الشارت الملكي للإشارة{num_part} — {esc(sym)} {dir_badge(direction)}</u></b>\n"
        f"<blockquote>"
        f"▎⏱ الفريم: <b>{tf_name(tf)}</b> · 🧠 المنهجية: <b>{esc(strat)}</b>\n"
        f"▎⭐ الجودة: <b>{score}/100</b> · ⚖️ العائد للمخاطرة: <b>1:{rr:.2f}</b>\n"
        f"▎🟡 الدخول: <code>{instruments.fmt_price(sym, entry)}</code> · 🔴 الوقف: <code>{instruments.fmt_price(sym, sl)}</code>\n"
        f"▎🎯 الأهداف: <code>{instruments.fmt_price(sym, tp1)}</code> · "
        f"<code>{instruments.fmt_price(sym, tp2)}</code> · "
        f"<code>{instruments.fmt_price(sym, tp3)}</code>\n"
        f"{live_line}"
        f"</blockquote>\n"
        f"<i>📊 شارت شموع حقيقي 100% من محرك {config.BRAND} · {config.BOT_SIGNATURE}</i>"
    )


def build_luxury_tracking_chart_caption(
    sig: dict,
    event: str,
    *,
    live_px: float = 0.0,
    pips: float = 0.0,
    seq: int | None = None,
) -> str:
    """Ultra-luxury Arabic caption for live tracking chart updates."""
    sym = str(sig.get("symbol") or "XAUUSD").upper()
    direction = str(sig.get("direction") or "BUY").upper()
    tf = str(sig.get("timeframe") or "M15").upper()
    strat = strategy_name(sig.get("strategy") or "")
    entry = float(sig.get("entry") or 0.0)
    ev = str(event or "").upper()
    num_part = f" #{seq}" if seq else ""
    usd_001 = instruments.usd_move(sym, pips, 0.01, px=live_px or entry)

    headers = {
        "RUNNING": ("🚀", "تفعيل الدخول الحي — الصفقة الآن في السوق"),
        "HALFWAY": ("💧", "تحديث التتبع الفاخر — 50% نحو الهدف الأول (أمّن صفقتك)"),
        "TP1_HIT": ("🎯", "تحقق الهدف الأول TP1 — ربح موثّق بالشارت"),
        "TP2_HIT": ("🏆", "تحقق الهدف الثاني TP2 — أداء مؤسسي فاخر"),
        "TP3_HIT": ("👑", "اكتملت الأهداف بالكامل TP3 — نصر ملكي كامل"),
        "TP1_BE": ("🛡", "إغلاق مؤمّن على ربح TP1 عند نقطة الدخول"),
        "TP2_BE": ("🛡", "إغلاق مؤمّن على ربح TP2 عند نقطة الدخول"),
        "SL_HIT": ("🛡", "تفعيل وقف الخسارة — حماية رأس المال"),
    }
    icon, title = headers.get(ev, ("📊", "تحديث شارت التتبع المباشر"))
    advice_map = {
        "RUNNING": "▎📡 الصفقة قيد المتابعة اللحظية — ستصلك شارتات التتبع تلقائياً عند كل هدف.",
        "HALFWAY": f"▎🛡 <b>تأمين ملكي:</b> انقل الوقف لنقطة الدخول <code>{instruments.fmt_price(sym, entry)}</code> واحجز 50% من الربح.",
        "TP1_HIT": f"▎🛡 <b>إدارة محترفة:</b> انقل الوقف إلى الدخول <code>{instruments.fmt_price(sym, entry)}</code> ودع الباقي يطارد TP2 و TP3.",
        "TP2_HIT": "▎💎 <b>زخم قوي:</b> أمّن الأرباح عند TP1 واترك الجزء المتبقي نحو الهدف الملكي TP3.",
        "TP3_HIT": "▎👑 <b>جميع الأهداف تحققت بالكامل</b> — موثّق بشموع السوق الحقيقية.",
        "TP1_BE": "▎✅ أُغلق الجزء المتبقي دون أي خسارة مع الاحتفاظ بربح الهدف الأول.",
        "TP2_BE": "▎✅ أُغلق الجزء المتبقي دون أي خسارة مع الاحتفاظ بربح الهدف الثاني.",
        "SL_HIT": "▎⚖️ التزام تام بإدارة المخاطر — الاستعداد للفرصة التالية.",
    }
    px_part = f" · السعر الآن: <code>{instruments.fmt_price(sym, live_px)}</code>" if live_px > 0 else ""
    return (
        f"{icon} <b><u>{title}{num_part}</u></b>\n"
        f"<b>{esc(sym)}</b> {dir_badge(direction)} · ⏱ {tf_name(tf)} · 🧠 {esc(strat)}\n"
        f"<blockquote>"
        f"▎📈 النتيجة الموثقة: <b>{pips:+.1f} pips</b> · 💵 <b>{usd_001:+,.2f}$</b> (0.01 لوت){px_part}\n"
        f"{advice_map.get(ev, '▎📡 متابعة حية من محرك ForexYemeni Royal.')}\n"
        f"</blockquote>\n"
        f"<i>✨ {config.BRAND} Royal Live Tracking · {config.BOT_SIGNATURE}</i>"
    )


async def deliver_chart_to_matching_channels(
    bot,
    sig: dict,
    png_bytes: bytes,
    caption: str,
    *,
    mode: str = "schedule",
    skip_private: bool = True,
) -> int:
    """Send ONLY the luxury PNG chart to enabled `chart_channels` matching `sig['symbol']`.

    Private user SIGNAL_NOW signals are NEVER leaked to public chart channels
    when `skip_private=True`.
    """
    if not bot or not png_bytes or not sig:
        return 0
    if skip_private and str(sig.get("source") or "").upper() == "SIGNAL_NOW":
        return 0
    sym = str(sig.get("symbol") or "").upper()
    channels = matching_channels(sym, mode=mode)
    if not channels:
        return 0
    sent = 0
    sent_ids: list[int] = []
    for ch in channels:
        try:
            target = _norm_chat_target(ch["chat_id"])
            await bot.send_photo(
                chat_id=target,
                photo=io.BytesIO(png_bytes),
                caption=caption,
                parse_mode="HTML",
            )
            sent += 1
            sent_ids.append(int(ch["id"]))
        except Exception as exc:
            log.warning("chart_channel send failed id=%s chat=%s: %s", ch.get("id"), ch.get("chat_id"), exc)
    if sent_ids:
        touch_last_sent(sent_ids)
    return sent


async def verify_telegram_channel(bot, raw_chat_id: str) -> dict:
    """Verify via real Telegram Bot API that the channel exists and the bot can post to it."""
    try:
        norm = normalize_chat_input(raw_chat_id)
    except ValueError as exc:
        return {"ok": False, "error": str(exc)}
    if bot is None or not hasattr(bot, "get_chat"):
        return {
            "ok": True,
            "verified": False,
            "chat_id": norm,
            "title": norm,
            "username": norm.lstrip("@") if norm.startswith("@") else "",
            "chat_type": "channel",
        }
    target = _norm_chat_target(norm)
    try:
        chat = await bot.get_chat(target)
    except Exception as exc:
        msg = str(exc)
        low = msg.lower()
        if "chat not found" in low or "not found" in low:
            return {
                "ok": False,
                "error": (
                    "تعذر العثور على القناة في تيليجرام — تأكد من المعرّف (@channel أو -100...) "
                    "وأن البوت مضاف بالفعل كمشرف داخل القناة."
                ),
            }
        if "kicked" in low or "forbidden" in low:
            return {"ok": False, "error": "البوت محظور أو مطرود من هذه القناة — أعد إضافته كمشرف أولاً."}
        return {"ok": False, "error": f"تعذر التحقق من القناة عبر تيليجرام: {msg[:160]}"}

    resolved_id = str(getattr(chat, "id", norm))
    resolved_title = str(getattr(chat, "title", "") or getattr(chat, "full_name", "") or norm).strip()
    resolved_user = str(getattr(chat, "username", "") or "").strip()
    chat_type = str(getattr(chat, "type", "channel") or "channel").lower()

    if hasattr(bot, "get_me") and hasattr(bot, "get_chat_member"):
        try:
            me = await bot.get_me()
            member = await bot.get_chat_member(chat.id, me.id)
            status = str(getattr(member, "status", "") or "").lower()
            if chat_type == "channel":
                if status not in ("administrator", "creator"):
                    return {
                        "ok": False,
                        "error": (
                            f"البوت ليس مشرفاً في القناة «{resolved_title}» (الحالة: {status or 'member'}) — "
                            "قم بترقية البوت إلى مشرف مع صلاحية نشر الرسائل (Post Messages)."
                        ),
                    }
                if status == "administrator" and getattr(member, "can_post_messages", True) is False:
                    return {
                        "ok": False,
                        "error": (
                            f"البوت مشرف في «{resolved_title}» لكن تنقصه صلاحية «نشر الرسائل / Post Messages» — "
                            "فعّل الصلاحية من إعدادات مشرفي القناة ثم أعد المحاولة."
                        ),
                    }
            elif chat_type in ("group", "supergroup"):
                if status in ("left", "kicked"):
                    return {"ok": False, "error": f"البوت غير موجود في المجموعة «{resolved_title}»."}
                if status == "restricted" and getattr(member, "can_send_messages", True) is False:
                    return {"ok": False, "error": f"البوت مقيّد من الإرسال في المجموعة «{resolved_title}»."}
        except Exception as exc:
            low = str(exc).lower()
            if "not enough rights" in low or "forbidden" in low or "member list is inaccessible" in low:
                return {
                    "ok": False,
                    "error": (
                        f"تعذر تأكيد صلاحيات الإشراف في «{resolved_title}» — "
                        "تأكد أن البوت مشرف بصلاحية نشر الرسائل ثم أعد المحاولة."
                    ),
                }
            log.debug("verify_telegram_channel member check warning: %s", exc)

    return {
        "ok": True,
        "verified": True,
        "chat_id": resolved_id,
        "title": resolved_title or resolved_id,
        "username": resolved_user,
        "chat_type": chat_type,
    }


async def send_test_chart(bot, ch: dict) -> dict:
    """Send an immediate live test chart/confirmation to a registered chart channel."""
    if not ch:
        return {"ok": False, "error": "القناة غير موجودة"}
    if bot is None:
        return {"ok": False, "error": "البوت غير متصل حالياً لإرسال رسالة الاختبار"}
    target = _norm_chat_target(ch["chat_id"])
    mkts = parse_markets_csv(ch.get("markets") or "ALL")
    sample_sym = "XAUUSD" if "ALL" in mkts else mkts[0]
    title = str(ch.get("title") or ch.get("chat_id") or "")
    mkts_lbl = format_markets_label(ch.get("markets") or "ALL")

    # Try rendering a real ForexYemeni luxury analysis chart first!
    png_bytes = b""
    caption = ""
    try:
        from fysb.services.analytics import run_analysis
        from fysb.ui import charts as _charts
        if _charts.charts_enabled():
            res = run_analysis(0, sample_sym, "M15", "", "", "ALL", enforce_plan=False)
            if res.get("ok") and res.get("analysis"):
                an = res["analysis"]
                png_bytes = _charts.render_analysis_chart(an) or b""
                if png_bytes:
                    caption = (
                        f"✅ <b><u>تم ربط واعتماد قناة الشارت الفاخرة بنجاح</u></b>\n"
                        f"<blockquote>"
                        f"▎📺 القناة: <b>{esc(title)}</b>\n"
                        f"▎🌐 الأسواق المفعّلة: <b>{esc(mkts_lbl)}</b>\n"
                        f"▎📊 عيّنة فورية: <b>{esc(sample_sym)}</b> · شارت شموع حقيقي من محرك <b>{config.BRAND}</b>\n"
                        f"</blockquote>\n"
                        f"<i>👑 {config.BRAND} Luxury Charts Engine · {config.BOT_SIGNATURE}</i>"
                    )
    except Exception as exc:
        log.debug("send_test_chart luxury preview fallback (%s): %s", sample_sym, exc)

    try:
        if png_bytes and hasattr(bot, "send_photo"):
            await bot.send_photo(
                chat_id=target,
                photo=io.BytesIO(png_bytes),
                caption=caption,
                parse_mode="HTML",
            )
            touch_last_sent([int(ch["id"])])
            return {"ok": True, "kind": "chart", "symbol": sample_sym}
        if hasattr(bot, "send_message"):
            msg = (
                f"✅ <b><u>تم ربط واعتماد قناة الشارت الفاخرة بنجاح</u></b>\n"
                f"<blockquote>"
                f"▎📺 القناة: <b>{esc(title)}</b>\n"
                f"▎🌐 الأسواق المفعّلة: <b>{esc(mkts_lbl)}</b>\n"
                f"▎📡 ستستلم هذه القناة الشارتات الفاخرة تلقائياً فور صدور الإشارات والتحديثات والتحليلات.\n"
                f"</blockquote>\n"
                f"<i>👑 {config.BRAND} Luxury Charts Engine · {config.BOT_SIGNATURE}</i>"
            )
            await bot.send_message(chat_id=target, text=msg, parse_mode="HTML")
            touch_last_sent([int(ch["id"])])
            return {"ok": True, "kind": "message", "symbol": sample_sym}
        return {"ok": False, "error": "كائن البوت لا يدعم الإرسال"}
    except Exception as exc:
        low = str(exc).lower()
        if "not enough rights" in low or "can't send" in low or "forbidden" in low:
            return {"ok": False, "error": "فشل الإرسال: البوت لا يملك صلاحية نشر الصور/الرسائل في القناة."}
        if "chat not found" in low:
            return {"ok": False, "error": "فشل الإرسال: لم يتم العثور على القناة — تحقق من المعرف."}
        return {"ok": False, "error": f"فشل الإرسال للقناة: {str(exc)[:160]}"}


async def deliver_analysis_chart_to_matching_channels(
    bot,
    symbol: str,
    png_bytes: bytes,
    caption: str,
    *,
    exclude_chat_ids: set[str] | None = None,
) -> int:
    """Deliver v8.1.0 luxury analysis charts to matching enabled chart_channels."""
    if not bot or not png_bytes or not symbol:
        return 0
    excluded = {str(x).strip().lower() for x in (exclude_chat_ids or set()) if x}
    sym = str(symbol).upper()
    sent = 0
    sent_ids: list[int] = []
    for ch in list_channels(only_enabled=True):
        if not market_matches(ch.get("markets") or "ALL", sym):
            continue
        cid_key = str(ch.get("chat_id") or "").strip().lower()
        if cid_key in excluded:
            continue
        try:
            target = _norm_chat_target(ch["chat_id"])
            await bot.send_photo(
                chat_id=target,
                photo=io.BytesIO(png_bytes),
                caption=caption[:1024],
                parse_mode="HTML",
            )
            sent += 1
            sent_ids.append(int(ch["id"]))
        except Exception as exc:
            log.warning("chart_channel analysis send failed id=%s chat=%s: %s", ch.get("id"), ch.get("chat_id"), exc)
    if sent_ids:
        touch_last_sent(sent_ids)
    return sent

