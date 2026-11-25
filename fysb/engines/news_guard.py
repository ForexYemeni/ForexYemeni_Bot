"""Live Economic Calendar & News Guard (v6.25.0 — 100% Free, Keyless).

Protects traders from high-impact news whipsaws (NFP, CPI, FOMC, ECB, BOE, BOJ)
using the public ForexFactory weekly JSON feed (`ff_calendar_thisweek.json`)
with a 30-minute local cache and graceful offline degradation:
  * If `news_guard_enabled == "1"`, signals on affected instruments are blocked
    within `±news_guard_minutes` (default 30 min) of any HIGH-impact release.
  * Admin can toggle News Guard ON/OFF, adjust the buffer window (15/30/45/60m),
    and view the live High-Impact calendar directly from the bot.
  * Never blocks or crashes the engine if the remote calendar feed is offline.
"""
from __future__ import annotations

import json
import threading
import time
import urllib.request
from datetime import datetime, timedelta, timezone

from fysb import config, db
from fysb.config import log
from fysb.theme import esc

FEED_URL = "https://nfs.faireconomy.media/ff_calendar_thisweek.json"
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


def currencies_for_symbol(symbol: str) -> set[str]:
    """Map an instrument symbol to the macro currencies that move it."""
    s = (symbol or "").upper().replace("/", "").replace("-", "").strip()
    if not s:
        return set()
    if s in ("XAUUSD", "XAGUSD", "XPTUSD", "US30", "NAS100", "SPX500", "USOIL", "WTI"):
        return {"USD"}
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
    """Return cached weekly economic events (keyless free feed; safe offline)."""
    now_t = time.time()
    with _LOCK:
        if not force_refresh and _CACHE["ts"] > 0 and (now_t - _CACHE["ts"]) < CACHE_TTL_SECONDS:
            return list(_CACHE["events"])
    try:
        req = urllib.request.Request(
            FEED_URL,
            headers={"User-Agent": f"FOREXYEMENI/{config.VERSION}", "Accept": "application/json"},
        )
        with urllib.request.urlopen(req, timeout=8) as resp:
            raw = json.loads(resp.read().decode("utf-8"))
        events = _parse_raw_feed(raw if isinstance(raw, list) else [])
        with _LOCK:
            _CACHE.update(ts=now_t, events=events, error="")
        return list(events)
    except Exception as exc:
        log.debug("news_guard feed unavailable (graceful skip): %s", exc)
        with _LOCK:
            _CACHE["error"] = f"{type(exc).__name__}"
            # Keep stale events if any, throttle retries for 5 min
            if _CACHE["ts"] <= 0:
                _CACHE["ts"] = now_t - CACHE_TTL_SECONDS + 300
            return list(_CACHE["events"])


def is_enabled() -> bool:
    return (db.get_setting("news_guard_enabled", "1") or "1") == "1"


def window_minutes() -> int:
    return max(5, min(120, db.get_setting_int("news_guard_minutes", 30)))


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


def upcoming_high_impact(within_hours: int = 24, now_utc: datetime | None = None) -> list[dict]:
    """Return upcoming High-impact macro events within the next `within_hours`."""
    now = now_utc or datetime.now(timezone.utc)
    if now.tzinfo is None:
        now = now.replace(tzinfo=timezone.utc)
    end = now + timedelta(hours=max(1, within_hours))
    out: list[dict] = []
    for ev in fetch_events():
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
    if not is_enabled():
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


def render_news_calendar_html(events: list[dict] | None = None, enabled: bool | None = None, buf_min: int | None = None) -> str:
    """Luxury Arabic Economic Calendar & News Guard status card."""
    on = is_enabled() if enabled is None else enabled
    wmin = window_minutes() if buf_min is None else buf_min
    evs = upcoming_high_impact(48) if events is None else events
    lines = [
        "📰 <b><u>درع الأخبار الاقتصادية الحي (News Guard)</u></b>",
        "<blockquote>"
        f"▎🛡 الحالة: <b>{'🟢 مفعّل — حماية تلقائية نشطة' if on else '⚪ متوقف'}</b>\n"
        f"▎⏱ نافذة الحظر حول الخبر القوي: <b>±{wmin} دقيقة</b>\n"
        "▎🎯 يمنع صدور إشارات جديدة وقت أخبار CPI / NFP / FOMC عالية التأثير.</blockquote>",
        "",
    ]
    if not evs:
        lines.append("<i>✨ لا توجد أخبار اقتصادية عالية التأثير مجدولة خلال الـ 48 ساعة القادمة (أو جاري تحديث التقويم).</i>")
    else:
        lines.append("🔴 <b>أهم الأخبار عالية التأثير القادمة:</b>")
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
