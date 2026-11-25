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
from typing import Iterable

from fysb import config, db, instruments
from fysb.config import log
from fysb.theme import dir_badge, esc, strategy_name, tf_name


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


def add_channel(chat_id: str, title: str = "", markets: str = "ALL") -> int:
    cid_str = str(chat_id or "").strip()
    if not cid_str:
        raise ValueError("chat_id is required")
    mkts = ",".join(parse_markets_csv(markets))
    now_s = config.now()
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
