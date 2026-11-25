"""Live Economic Calendar, News Guard & Luxury Auto-Broadcast (v7.4.0 — 100% Free, Keyless).

Protects traders from high-impact news whipsaws (NFP, CPI, FOMC, ECB, BOE, BOJ)
using dual-mirror ForexFactory weekly JSON feeds with a 30-minute local cache,
honest feed-health reporting, full global index/commodity/FX currency mapping,
AND Admin-controlled ultra-luxury automated news broadcasts to Users, Partners,
and Channels.
"""
from __future__ import annotations

import asyncio
import json
import threading
import time
import urllib.request
from datetime import datetime, timedelta, timezone

from fysb import config, db
from fysb.config import log
from fysb.theme import esc

FEED_URL = "https://nfs.faireconomy.media/ff_calendar_thisweek.json"
FEED_MIRRORS = (
    FEED_URL,
    "https://cdn-nfs.faireconomy.media/ff_calendar_thisweek.json",
)
CACHE_TTL_SECONDS = 1800  # 30 min

_LOCK = threading.Lock()
_CACHE: dict = {"ts": 0.0, "events": [], "error": ""}
_ALERTED_KEYS: set[str] = set()

_IMPACT_AR = {
    "High": "🔴 عالي التأثير",
    "Medium": "🟠 متوسط التأثير",
    "Low": "🟡 منخفض",
}

_CURRENCY_FLAGS = {
    "USD": "🇺🇸", "EUR": "🇪🇺", "GBP": "🇬🇧", "JPY": "🇯🇵",
    "CHF": "🇨🇭", "CAD": "🇨🇦", "AUD": "🇦🇺", "NZD": "🇳🇿",
    "CNY": "🇨🇳",
}

_CURRENCY_NAMES_AR = {
    "USD": "الدولار الأمريكي",
    "EUR": "اليورو الأوروبي",
    "GBP": "الجنيه الإسترليني",
    "JPY": "الين الياباني",
    "CHF": "الفرنك السويسري",
    "CAD": "الدولار الكندي",
    "AUD": "الدولار الأسترالي",
    "NZD": "الدولار النيوزيلندي",
    "CNY": "اليوان الصيني",
}

_CURRENCY_IMPACT_MARKETS = {
    "USD": ("XAUUSD", "EURUSD", "GBPUSD", "USDJPY", "US30", "NAS100", "BTCUSD"),
    "EUR": ("EURUSD", "EURGBP", "EURJPY", "GER40"),
    "GBP": ("GBPUSD", "EURGBP", "GBPJPY", "UK100"),
    "JPY": ("USDJPY", "EURJPY", "GBPJPY", "JP225"),
    "CHF": ("USDCHF", "EURCHF"),
    "CAD": ("USDCAD", "USOIL"),
    "AUD": ("AUDUSD", "AUDJPY"),
    "NZD": ("NZDUSD", "NZDJPY"),
    "CNY": ("XAUUSD", "AUDUSD", "USOIL"),
}


def reset_cache() -> None:
    """Clear calendar cache & alert dedup set (used in unit tests)."""
    with _LOCK:
        _CACHE.update(ts=0.0, events=[], error="")
        _ALERTED_KEYS.clear()


def set_cached_events(events: list[dict], ts: float | None = None) -> None:
    """Inject parsed events directly into cache (used for tests / warm cache)."""
    with _LOCK:
        _CACHE["events"] = list(events or [])
        _CACHE["ts"] = time.time() if ts is None else float(ts)
        _CACHE["error"] = ""


def last_feed_error() -> str:
    with _LOCK:
        return str(_CACHE.get("error") or "")


def currencies_for_symbol(symbol: str) -> set[str]:
    """Map any FX / metal / index / commodity / crypto symbol to macro currencies."""
    s = (symbol or "").upper().replace("/", "").replace("-", "").replace("_", "").strip()
    if not s:
        return set()
    if s in (
        "XAUUSD", "XAGUSD", "XPTUSD", "XPDUSD", "GOLD", "SILVER",
        "US30", "DJ30", "DJI", "NAS100", "NDX", "USTEC", "SPX500", "SP500", "US500",
        "USOIL", "WTI", "UKOIL", "BRENT", "DXY",
    ):
        return {"USD"}
    if s in ("GER40", "GER30", "DE40", "DAX", "EU50", "STOXX50", "FRA40", "CAC40"):
        return {"EUR"}
    if s in ("UK100", "FTSE", "FTSE100"):
        return {"GBP"}
    if s in ("JP225", "NIKKEI", "NI225"):
        return {"JPY"}
    if s.endswith("USDT") or s.endswith("USD"):
        base = s[:-4] if s.endswith("USDT") else s[:-3]
        if base in _CURRENCY_FLAGS:
            return {base, "USD"}
        return {"USD"}
    if len(s) == 6:
        c1, c2 = s[:3], s[3:]
        out = set()
        if c1 in _CURRENCY_FLAGS:
            out.add(c1)
        if c2 in _CURRENCY_FLAGS:
            out.add(c2)
        return out
    return set()


def affected_markets_for_currency(ccy: str) -> list[str]:
    """Return active catalog markets impacted by `ccy`, falling back to canonical majors."""
    c_u = (ccy or "").strip().upper()
    try:
        with db.conn() as c:
            rows = c.execute("SELECT symbol FROM market_catalog WHERE enabled=1 ORDER BY symbol").fetchall()
        matched = [str(r["symbol"]).upper() for r in rows if c_u in currencies_for_symbol(r["symbol"])]
        if matched:
            return matched[:6]
    except Exception:
        pass
    return list(_CURRENCY_IMPACT_MARKETS.get(c_u, ()))[:6]


def _parse_event_dt(raw_date: str) -> datetime | None:
    if not raw_date:
        return None
    try:
        dt = datetime.fromisoformat(str(raw_date).replace("Z", "+00:00"))
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt.astimezone(timezone.utc)
    except Exception:
        return None


def _parse_raw_feed(items: list[dict]) -> list[dict]:
    parsed: list[dict] = []
    for it in items or []:
        if not isinstance(it, dict):
            continue
        dt = _parse_event_dt(it.get("date") or "")
        if dt is None:
            continue
        impact = str(it.get("impact") or "").strip().title()
        ccy = str(it.get("country") or it.get("currency") or "").strip().upper()
        title = str(it.get("title") or "").strip()
        if not title or not ccy:
            continue
        parsed.append({
            "title": title,
            "currency": ccy,
            "impact": impact,
            "dt_utc": dt.isoformat(),
            "forecast": str(it.get("forecast") or "").strip(),
            "previous": str(it.get("previous") or "").strip(),
        })
    parsed.sort(key=lambda e: e["dt_utc"])
    return parsed


def fetch_events(force_refresh: bool = False) -> list[dict]:
    """Return cached weekly economic events using dual-mirror ForexFactory failover."""
    now_t = time.time()
    with _LOCK:
        if not force_refresh and _CACHE["ts"] > 0 and (now_t - _CACHE["ts"]) < CACHE_TTL_SECONDS:
            return list(_CACHE["events"])
    last_err = ""
    for mirror in FEED_MIRRORS:
        try:
            req = urllib.request.Request(
                mirror,
                headers={
                    "User-Agent": f"Mozilla/5.0 (compatible; FOREXYEMENI/{config.VERSION})",
                    "Accept": "application/json",
                },
            )
            with urllib.request.urlopen(req, timeout=8) as resp:
                raw = json.loads(resp.read().decode("utf-8"))
            events = _parse_raw_feed(raw if isinstance(raw, list) else [])
            with _LOCK:
                _CACHE.update(ts=now_t, events=events, error="")
            return list(events)
        except Exception as exc:
            last_err = f"{type(exc).__name__}: {str(exc)[:60]}"
            log.debug("news_guard mirror %s unavailable: %s", mirror, exc)
    with _LOCK:
        _CACHE["error"] = last_err or "FeedUnavailable"
        if _CACHE["ts"] <= 0:
            _CACHE["ts"] = now_t - CACHE_TTL_SECONDS + 300
        return list(_CACHE["events"])


def is_enabled() -> bool:
    return (db.get_setting("news_guard_enabled", "1") or "1") == "1"


def window_minutes() -> int:
    return max(5, min(120, db.get_setting_int("news_guard_minutes", 30)))


def auto_broadcast_enabled() -> bool:
    return (db.get_setting("news_auto_broadcast", "0") or "0") == "1"


def lead_minutes() -> int:
    return max(10, min(120, db.get_setting_int("news_lead_minutes", 30)))


def broadcast_destinations() -> dict[str, bool]:
    return {
        "users": (db.get_setting("news_broadcast_users", "1") or "1") == "1",
        "partners": (db.get_setting("news_broadcast_partners", "1") or "1") == "1",
        "channels": (db.get_setting("news_broadcast_channels", "1") or "1") == "1",
    }


def check_news_block(symbol: str, now_utc: datetime | None = None) -> tuple[bool, dict | None]:
    """Return `(True, event)` if `symbol` is inside a High-Impact news buffer."""
    if not is_enabled():
        return False, None
    ccys = currencies_for_symbol(symbol)
    if not ccys:
        return False, None
    now = now_utc or datetime.now(timezone.utc)
    if now.tzinfo is None:
        now = now.replace(tzinfo=timezone.utc)
    buf = timedelta(minutes=window_minutes())
    for ev in fetch_events():
        if ev.get("impact") != "High":
            continue
        if ev.get("currency") not in ccys:
            continue
        ev_dt = _parse_event_dt(ev.get("dt_utc") or "")
        if ev_dt is None:
            continue
        if abs(ev_dt - now) <= buf:
            mins = int(round((ev_dt - now).total_seconds() / 60.0))
            return True, {**ev, "minutes_delta": mins}
    return False, None


def upcoming_high_impact(
    within_hours: int = 24,
    now_utc: datetime | None = None,
    *,
    force_refresh: bool = False,
) -> list[dict]:
    """Return upcoming High-impact macro events within the next `within_hours`."""
    now = now_utc or datetime.now(timezone.utc)
    if now.tzinfo is None:
        now = now.replace(tzinfo=timezone.utc)
    end = now + timedelta(hours=max(1, within_hours))
    out: list[dict] = []
    for ev in fetch_events(force_refresh=force_refresh):
        if ev.get("impact") != "High":
            continue
        ev_dt = _parse_event_dt(ev.get("dt_utc") or "")
        if ev_dt is None or not (now - timedelta(minutes=15) <= ev_dt <= end):
            continue
        mins = int(round((ev_dt - now).total_seconds() / 60.0))
        out.append({**ev, "minutes_delta": mins, "time_utc": ev_dt.strftime("%m-%d %H:%M UTC")})
    return out


def due_pre_news_alerts(lead_minutes: int = 30, now_utc: datetime | None = None) -> list[dict]:
    """Return High-Impact events starting in `<= lead_minutes` that haven't been alerted yet."""
    if not is_enabled() and not auto_broadcast_enabled():
        return []
    now = now_utc or datetime.now(timezone.utc)
    if now.tzinfo is None:
        now = now.replace(tzinfo=timezone.utc)
    due: list[dict] = []
    for ev in upcoming_high_impact(within_hours=2, now_utc=now):
        mins = int(ev.get("minutes_delta") or 999)
        if 0 < mins <= lead_minutes:
            key = f"{ev['currency']}|{ev['dt_utc']}|{ev['title'][:32]}"
            with _LOCK:
                if key in _ALERTED_KEYS:
                    continue
                _ALERTED_KEYS.add(key)
            due.append(ev)
    return due


def render_luxury_news_alert_card(ev: dict, *, buf_min: int | None = None) -> str:
    """Ultra-luxury Arabic pre-news alert card for Users, Partners, and Channels."""
    ccy = str(ev.get("currency") or "USD").upper()
    flag = _CURRENCY_FLAGS.get(ccy, "🌐")
    ccy_ar = _CURRENCY_NAMES_AR.get(ccy, ccy)
    title = str(ev.get("title") or "خبر اقتصادي عالي التأثير")
    mins = int(ev.get("minutes_delta") or 30)
    t_utc = str(ev.get("time_utc") or ev.get("dt_utc", "")[:16])
    wmin = window_minutes() if buf_min is None else buf_min
    affected = affected_markets_for_currency(ccy)
    mkt_line = " · ".join(f"<code>{esc(m)}</code>" for m in affected) if affected else f"<code>{esc(ccy)}</code>"

    data_bits = []
    if ev.get("forecast"):
        data_bits.append(f"المتوقع: <b>{esc(ev['forecast'])}</b>")
    if ev.get("previous"):
        data_bits.append(f"السابق: <b>{esc(ev['previous'])}</b>")
    data_line = f"▎📊 الأرقام التقديرية: {' · '.join(data_bits)}\n" if data_bits else ""

    when_txt = f"خلال <b>{mins} دقيقة</b>" if mins > 0 else "<b>الآن / للتو</b>"
    return (
        f"🚨 <b><u>نشرة الأخبار الاقتصادية الملكية — خبر عالي التأثير</u></b>\n"
        f"━━━━━━━━━━━━━━━━━━\n"
        f"<blockquote>"
        f"▎{flag} العملة المتأثرة: <b>{esc(ccy)} ({esc(ccy_ar)})</b>\n"
        f"▎🔴 الحدث الاقتصادي: <b>{esc(title)}</b>\n"
        f"▎⏰ الموعد: <code>{esc(t_utc)}</code> ({when_txt})\n"
        f"{data_line}"
        f"▎🌐 الأسواق الأكثر تأثراً: {mkt_line}\n"
        f"▎🛡 <b>بروتوكول الحماية الملكي:</b> يُتوقّع تذبذب حاد واتساع في السبريد — "
        f"أمّن صفقاتك المفتوحة وتجنّب المخاطرة الزائدة حول الخبر (±{wmin} دقيقة)."
        f"</blockquote>\n"
        f"<i>👑 تنبيه مؤسسي فوري من {config.BRAND} · {config.BOT_SIGNATURE}</i>"
    )


def render_luxury_news_digest_card(
    events: list[dict] | None = None,
    *,
    buf_min: int | None = None,
    force_refresh: bool = False,
) -> str:
    """Ultra-luxury Arabic 48-hour High-Impact Economic Bulletin for broadcast."""
    evs = upcoming_high_impact(48, force_refresh=force_refresh) if events is None else events
    wmin = window_minutes() if buf_min is None else buf_min
    now_s = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    if not evs:
        return (
            f"📰 <b><u>النشرة الاقتصادية الملكية — {config.BRAND}</u></b>\n"
            f"━━━━━━━━━━━━━━━━━━\n"
            f"<blockquote>"
            f"▎🟢 الأجواء الاقتصادية هادئة خلال الـ 48 ساعة القادمة — لا توجد أخبار حمراء عالية التأثير مجدولة.\n"
            f"▎📡 التداول الفني مستقر ضمن نطاق السيولة الطبيعي."
            f"</blockquote>\n"
            f"<i>👑 {config.BRAND} Royal Calendar · {now_s} · {config.BOT_SIGNATURE}</i>"
        )
    lines = [
        f"📰 <b><u>النشرة الاقتصادية الملكية — أهم أخبار 48 ساعة</u></b>",
        "━━━━━━━━━━━━━━━━━━",
        "<blockquote>",
    ]
    for ev in evs[:10]:
        ccy = str(ev.get("currency") or "").upper()
        flag = _CURRENCY_FLAGS.get(ccy, "🌐")
        mins = int(ev.get("minutes_delta") or 0)
        if mins < 0:
            when = f"منذ {abs(mins)}د"
        elif mins < 60:
            when = f"بعد {mins}د"
        else:
            when = f"بعد {mins // 60}س {mins % 60}د"
        fp = ""
        if ev.get("forecast"):
            fp += f" · المتوقع: <code>{esc(ev['forecast'])}</code>"
        if ev.get("previous"):
            fp += f" · السابق: <code>{esc(ev['previous'])}</code>"
        lines.append(
            f"▎🔴 {flag} <b>{esc(ccy)}</b> — <b>{esc(ev['title'])}</b>\n"
            f"   ⏱ <code>{esc(ev.get('time_utc', ''))}</code> ({when}){fp}"
        )
    lines.append("</blockquote>")
    lines.append(
        f"<blockquote>▎🛡 <b>نصيحة إدارة المخاطر:</b> راقب توقيت الأخبار أعلاه وأمّن صفقاتك قبل صدورها بـ <b>{wmin} دقيقة</b>.</blockquote>\n"
        f"<i>👑 {config.BRAND} Royal Economic Desk · {now_s} · {config.BOT_SIGNATURE}</i>"
    )
    return "\n".join(lines)


def render_news_calendar_html(
    events: list[dict] | None = None,
    enabled: bool | None = None,
    buf_min: int | None = None,
    *,
    force_refresh: bool = False,
) -> str:
    """Luxury Arabic Economic Calendar & News Guard status card (Admin Panel)."""
    on = is_enabled() if enabled is None else enabled
    wmin = window_minutes() if buf_min is None else buf_min
    evs = upcoming_high_impact(48, force_refresh=force_refresh) if events is None else events
    auto_b = auto_broadcast_enabled()
    dests = broadcast_destinations()
    feed_err = last_feed_error() if events is None else ""
    dest_parts = []
    if dests["users"]:
        dest_parts.append("👥 المستخدمون")
    if dests["partners"]:
        dest_parts.append("🤝 الشركاء")
    if dests["channels"]:
        dest_parts.append("📺 القنوات")
    dest_txt = " + ".join(dest_parts) if dest_parts else "⚪ لا توجد وجهة مفعّلة"

    lines = [
        "📰 <b><u>درع الأخبار الاقتصادية الحي والبث التلقائي (News Guard)</u></b>",
        "<blockquote>"
        f"▎🛡 درع حماية المحرك: <b>{'🟢 مفعّل — حماية تلقائية نشطة' if on else '⚪ متوقف'}</b>\n"
        f"▎⏱ نافذة الحظر حول الخبر القوي: <b>±{wmin} دقيقة</b>\n"
        f"▎📢 البث التلقائي للأخبار الفاخرة: <b>{'🟢 مفعّل تلقائياً' if auto_b else '⚪ متوقف'}</b>\n"
        f"▎📤 وجهات البث المفعّلة: <b>{dest_txt}</b>"
        "</blockquote>",
        "",
    ]
    if feed_err and not evs:
        lines.append(
            f"⚠️ <b>تعذر الاتصال بمزود التقويم الاقتصادي حالياً</b> (<code>{esc(feed_err)}</code>)\n"
            "<i>اضغط «🔄 تحديث التقويم» لإعادة المحاولة — لن يتوقف المحرك أثناء انقطاع المزود.</i>"
        )
    elif not evs:
        lines.append("<i>✨ لا توجد أخبار اقتصادية عالية التأثير مجدولة خلال الـ 48 ساعة القادمة.</i>")
    else:
        lines.append("🔴 <b>أهم الأخبار عالية التأثير القادمة (48 ساعة):</b>")
        for ev in evs[:10]:
            flag = _CURRENCY_FLAGS.get(ev.get("currency", ""), "🌐")
            mins = int(ev.get("minutes_delta") or 0)
            if mins < 0:
                when = f"منذ {abs(mins)} د"
            elif mins < 60:
                when = f"بعد {mins} د"
            else:
                when = f"بعد {mins // 60}س {mins % 60}د"
            extra = ""
            if ev.get("forecast"):
                extra += f" · المتوقع: <code>{esc(ev['forecast'])}</code>"
            if ev.get("previous"):
                extra += f" · السابق: <code>{esc(ev['previous'])}</code>"
            lines.append(
                f"  • {flag} <b>{esc(ev['currency'])}</b> — {esc(ev['title'])}\n"
                f"     ⏱ <code>{esc(ev.get('time_utc', ev.get('dt_utc', '')[:16]))}</code> ({when}){extra}"
            )
    return "\n".join(lines)


def _collect_news_targets(
    *,
    include_admin: bool = True,
    to_users: bool = True,
    to_partners: bool = True,
    to_channels: bool = True,
) -> dict[str, list]:
    """Build deduplicated recipient groups for news broadcast."""
    seen: set[str] = set()
    out: dict[str, list] = {"admin": [], "partners": [], "users": [], "channels": []}

    if include_admin and config.ADMIN_ID:
        seen.add(str(config.ADMIN_ID))
        out["admin"].append(int(config.ADMIN_ID))

    with db.conn() as c:
        if to_partners:
            for r in c.execute("SELECT user_id FROM channel_partners WHERE enabled=1").fetchall():
                uid = int(r["user_id"] or 0)
                if uid and str(uid) not in seen:
                    seen.add(str(uid))
                    out["partners"].append(uid)
        if to_users:
            for r in c.execute("SELECT user_id FROM users WHERE notifications=1 AND is_blocked=0").fetchall():
                uid = int(r["user_id"] or 0)
                if uid and str(uid) not in seen:
                    seen.add(str(uid))
                    out["users"].append(uid)
        if to_channels:
            for r in c.execute("SELECT chat_id FROM broadcast_channels WHERE enabled=1").fetchall():
                cid = str(r["chat_id"] or "").strip()
                if cid and cid not in seen:
                    seen.add(cid)
                    out["channels"].append(int(cid) if cid.lstrip("-").isdigit() else cid)
    return out


async def broadcast_news_to_targets(
    bot,
    text: str,
    *,
    include_admin: bool = True,
    to_users: bool | None = None,
    to_partners: bool | None = None,
    to_channels: bool | None = None,
) -> dict:
    """Deliver a luxury HTML news bulletin to Admin, Users, Partners, and Channels."""
    dests = broadcast_destinations()
    u_on = dests["users"] if to_users is None else bool(to_users)
    p_on = dests["partners"] if to_partners is None else bool(to_partners)
    c_on = dests["channels"] if to_channels is None else bool(to_channels)

    groups = _collect_news_targets(
        include_admin=include_admin,
        to_users=u_on,
        to_partners=p_on,
        to_channels=c_on,
    )
    stats = {"admin": 0, "users": 0, "partners": 0, "channels": 0, "failed": 0}
    if not bot or not text:
        return stats

    for bucket in ("admin", "partners", "users", "channels"):
        for target in groups[bucket]:
            try:
                await bot.send_message(target, text, parse_mode="HTML")
                stats[bucket] += 1
                await asyncio.sleep(0.03)
            except Exception as exc:
                stats["failed"] += 1
                log.debug("news broadcast failed bucket=%s target=%s: %s", bucket, target, exc)
    return stats
