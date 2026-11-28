"""FOREXYEMENI design system — unified luxury visual language for every message.

Palette semantics (rendered through structure & consistent markers, since
Telegram text does not support colors):
  Deep Royal Navy .... frame + header bands
  Luxury Gold ........ titles, brand, premium marks  (◆ / 👑 / 💎)
  Premium White ...... body text
  Emerald Green ...... positive states  (BUY, TP hit, APPROVED, active)
  Elegant Red ........ warnings & losses (SELL, SL hit, REJECTED, blocked)
  Royal Blue ......... informational metrics & stats (ℹ)

Telegram-native "cards": body sections are wrapped in <blockquote> blocks —
Telegram renders them as bordered cards with a leading accent bar, which is the
closest native luxury-card surface inside a chat. Bold is used aggressively for
labels and figures; italics only for notes; <code> only for numbers that must
be copyable (prices, IDs).

Rules enforced here:
  * one shared icon vocabulary — no random decoration
  * every card = header band + labelled sections + footer
  * numbers formatted via instruments-aware formatter (full precision in DB)
  * no secrets anywhere
"""
import html
from typing import Iterable

from fysb import config

# --- Icon vocabulary (closed set — do not invent new icons ad hoc) ----------
ICON = {
    "crown": "👑", "diamond": "💎", "chart": "📊", "signal": "📡",
    "brain": "🧠", "shield": "🛡", "gear": "⚙️", "users": "👥",
    "money": "💳", "wallet": "🧾", "globe": "🌐", "clock": "⏱",
    "rocket": "🚀", "test": "🧪", "headset": "🎧", "bell": "🔔",
    "book": "📖", "lock": "🔒", "key": "🔐", "disk": "💾",
    "megaphone": "📣", "fire": "🔥", "warn": "⚠️", "ban": "⛔",
    "up": "📈", "down": "📉", "check": "✅", "cross": "❌",
    "arrow_back": "↩️", "home": "🏠", "refresh": "🔄", "search": "🔎",
    "target": "🎯", "bullet": "•", "gold": "🥇", "engine": "🛠",
    "paper": "📄", "link": "🔗", "star": "⭐", "bank": "🏦",
    "gem_royal": "💠", "lab": "🔬", "card": "🗂", "spark": "✨",
}

# Status colorization (Emerald = positive, Red = negative, Blue = info)
STATUS_STYLE = {
    "ACTIVE": ("EMERALD", "ACTIVE"),
    "RUNNING": ("EMERALD", "RUNNING"),
    "TP1_HIT": ("EMERALD", "TP1 HIT"),
    "TP2_HIT": ("EMERALD", "TP2 HIT"),
    "TP3_HIT": ("EMERALD", "TP3 HIT"),
    "CLOSED_WIN": ("EMERALD", "COMPLETED"),
    "SL_HIT": ("RED", "SL HIT"),
    "CANCELLED": ("RED", "CANCELLED"),
    "EXPIRED": ("GREY", "EXPIRED"),
    "PENDING": ("BLUE", "PENDING"),
    "APPROVED": ("EMERALD", "APPROVED"),
    "REJECTED": ("RED", "REJECTED"),
    "OPEN": ("BLUE", "OPEN"),
    "ANSWERED": ("EMERALD", "ANSWERED"),
    "CLOSED": ("GREY", "CLOSED"),
    "OPEN_POS": ("BLUE", "OPEN"),
    "WIN": ("EMERALD", "WIN"),
    "LOSS": ("RED", "LOSS"),
}

STATUS_EMOJI = {
    "EMERALD": "🟢", "RED": "🔴", "BLUE": "🔵", "GREY": "⚪",
}

DIRECTION_EMOJI = {"BUY": "🟢", "SELL": "🔴"}

# v6.10.0 — OWNER RULE: strategy names are the SHORT ROYAL CODES everywhere
# (ICT, SMC, CRT, SK ...) — never long Arabic writings. The old Arabic map is
# kept only as STRATEGY_DESC for catalog prose needs.
STRATEGY_AR = {
    "ICT": "ICT",
    "SMC": "SMC",
    "PRICE_ACTION": "PRICE_ACTION",
    "CRT": "CRT",
    "AMD": "AMD",
    "TURTLE_SOUP": "TURTLE_SOUP",
    "SILVER_BULLET": "SILVER_BULLET",
    "WYCKOFF": "WYCKOFF",
    "FIBONACCI": "FIBONACCI",
    "TREND": "TREND",
    "ICHIMOKU": "ICHIMOKU",
    "CLASSIC": "CLASSIC",
    "SK": "SK",
    "HARMONIC": "HARMONIC",
    "VWAP_RECLAIM": "VWAP_RECLAIM",
    "LONDON_BREAKOUT": "LONDON_BREAKOUT",
    "DIVERGENCE": "DIVERGENCE",
    "SQUEEZE": "SQUEEZE",
    "MANUAL": "MANUAL",
    "ALL": "ALL",
}

TIMEFRAME_AR = {
    "M1": "1 دقيقة", "M5": "5 دقائق", "M15": "15 دقيقة", "M30": "30 دقيقة",
    "H1": "ساعة", "H4": "4 ساعات", "D1": "يومي", "W1": "أسبوعي",
}

# v6.8.0 — luxury market sections (owner instruction: المعادن/الفوركس/العملات/
# الأسهم as separated royal sections). Any unknown category falls back to its code.
CATEGORY_AR = {
    "metal": "المعادن",
    "forex": "الفوركس",
    "crypto": "العملات الرقمية",
    "stock": "الأسهم",
    "index": "المؤشرات العالمية",
    "commodity": "السلع والطاقة",
}

CATEGORY_ICON = {
    "metal": "🥇", "forex": "💱", "crypto": "🪙", "stock": "🏛", "index": "📊", "commodity": "🛢",
}

CATEGORY_ORDER = ["metal", "forex", "crypto", "stock", "index", "commodity"]

STATUS_AR = {
    "ACTIVE": "نشطة — بانتظار التنفيذ",
    "RUNNING": "نشطة — قيد التنفيذ في السوق",
    "TP1_HIT": "تحقق الهدف الأول — مأمونة",
    "TP2_HIT": "تحقق الهدف الثاني",
    "TP3_HIT": "تحقق الهدف الثالث — مكتملة",
    "LOCKED_50": "🏅 مؤمّنة عند +50 — القفل الملكي حفظ الربح",
    "SL_HIT": "ضرب وقف الخسارة",
    "CANCELLED": "ملغاة",
    "EXPIRED": "منتهية الصلاحية",
    "PENDING": "معلقة",
    "APPROVED": "مقبولة",
    "REJECTED": "مرفوضة",
    "OPEN": "مفتوحة",
    "ANSWERED": "تم الرد",
    "CLOSED": "مغلقة",
    "WIN": "رابحة",
    "LOSS": "خاسرة",
    "PARTIAL": "مُغلق جزئياً",
}

PATTERN_AR = {
    "ENGULFING_BULL": "ابتلاع صاعد",
    "ENGULFING_BEAR": "ابتلاع هابط",
    "PIN_BAR_BULL": "دبوس شرائي (مطرقة)",
    "PIN_BAR_BEAR": "دبوس بيعي (نجمة طائرة)",
    "DOJI": "دوجي (تردد)",
}

STRUCTURE_AR = {
    "HH": "قمة أعلى", "HL": "قاع أعلى", "LH": "قمة أدنى", "LL": "قاع أدنى",
}

KILLZONE_AR = {"LONDON": "جلسة لندن", "NEW_YORK": "جلسة نيويورك"}


def strategy_name(code: str) -> str:
    return STRATEGY_AR.get((code or "").upper(), esc(code) if code else "—")


def tf_name(code: str) -> str:
    return TIMEFRAME_AR.get((code or "").upper(), esc(code) if code else "—")


def status_ar(status: str) -> str:
    return STATUS_AR.get((status or "").upper(), esc(status) if status else "—")


# --- v8.11.1 (luxury diagnostics): engine-run verdict & reason Arabic layer ---
# The engine stores terse English codes/details for log honesty; the DISPLAY
# layer presents them in the royal Arabic voice everywhere users/admins read.
RESULT_AR = {
    "SIGNAL_CREATED": ("✅", "إشارة ولدت وسُلّمت"),
    "NO_SETUP": ("🧭", "لا فرصة مؤهلة — شروط الاستراتيجيات غير مكتملة فعلاً"),
    "DUPLICATE": ("♻️", "إشارة مطابقة حديثة — منع التكرار"),
    "MARKET_CLOSED": ("🔒", "السوق مغلق فعلاً 100% وفق تقويم الجلسات"),
    "STALE_DATA": ("⏳", "بيانات أقدم من الحد المسموح — رفض صادق"),
    "FEED_FROZEN": ("🧊", "التغذية مجمّدة — نفس اللقطة تتكرر والسوق مفتوح؛ راجع اتصال السيرفر والمفتاح"),
    "DATA_ERROR": ("📡", "تعذر جلب بيانات لحظية — لا إشارة من بيانات مشكّلة"),
    "NEWS_BLOCKED": ("📰", "خبر قوي قريب — درع الأخبار يحمي الدخول"),
    "KILL_SWITCH": ("🛡", "مفتاح القتل مشغّل — لا إصدار إطلاقاً"),
    "INSUFFICIENT_DATA": ("📉", "شموع غير كافية للتحقق المؤسسي"),
    "QUALITY_REJECTED": ("⭐", "جودة دون الحد المؤسسي — امتناع حماية"),
    "RISK_REJECTED": ("🛡", "بوابة المخاطر رفضت — سقف/حد وقائي"),
    "STALE_ENTRY": ("🎯", "السعر ابتعد عن منطقة الدخول قبل النشر"),
    "CONFLICT_REJECTED": ("⚔️", "تعارض اتجاهي بين الاستراتيجيات — رفض حماية"),
    "CONFLICT_RESOLVED": ("⚖️", "تعارض اتجاهي حُلّ بأوزان الذكاء"),
    "CANDIDATE_FALLBACK": ("🔁", "اختيار أفضل مرشح بعد رفض الأعلى درجة"),
    "STRATEGY_NOT_LIVE": ("🧠", "المنهجية المطلوبة غير مفعلة أو غير موثقة"),
    "PROXY_REJECTED": ("💠", "بيانات بديلة للعرض فقط — ليست دليل تنفيذ"),
    "REJECTED": ("⛔", "مرفوض ببوابة حماية"),
    "ERROR": ("⚠️", "خطأ تشغيلي في المحاولة"),
}

_REASON_SUBS = (
    # (lowercase needle OR regex, Arabic replacement) — first match wins
    ("session calendar says market is closed; no signal manufactured", ""),
    ("no strategy conditions met; no signal manufactured", ""),
    ("market closed", "السوق مغلق الآن وفق تقويم الجلسات — التزام صادق"),
    ("kill switch engaged", "مفتاح القتل مشغّل — لا إصدار إطلاقاً"),
    ("global kill switch engaged", "مفتاح القتل مشغّل — لا إصدار إطلاقاً"),
    ("proxy candles are indicative only, not execution evidence",
     "بيانات بديلة معايرة للعرض فقط — ليست دليل تنفيذ"),
    ("no qualifying setup", "لا فرصة مؤهلة — شروط الاستراتيجيات غير مكتملة فعلاً"),
    ("recent duplicate", "إشارة مطابقة حديثة — منع التكرار"),
    ("strategy disabled or unverified", "الاستراتيجية غير مفعلة أو غير موثقة"),
    ("news status unknown", "حالة الأخبار غير معروفة — لا توجد بيانات تقويم صالحة"),
)


def ar_result(code: str) -> str:
    """Engine-run verdict code -> 'emoji Arabic label' (code passthrough if unknown)."""
    c = (code or "").strip().upper()
    icon, label = RESULT_AR.get(c, ("•", c))
    return f"{icon} {label}" if label else c


def ar_reason(reason: str) -> str:
    """Translate the common English engine reasons to the royal Arabic voice.

    Arabic reasons (quality gate, risk engine, news guard) pass through
    untouched; unknown English is preserved honestly rather than dropped.
    """
    s = (reason or "").strip()
    if not s:
        return ""
    import re as _re
    low = s.lower()
    for needle, ar in _REASON_SUBS:
        if needle in low:
            return ar
    m = _re.search(r"settlement pause (\d{2}:\d{2})-(\d{2}:\d{2})", low)
    if m:
        return (f"🌙 استراحة التسوية اليومية للأسواق ({m.group(1)}–{m.group(2)} UTC / 17:00–18:00 نيويورك) — "
                "السوق يلتفت أنفاسه دقائق محدودة كل يوم؛ التدفق والإشارات يعودان فور انتهائها بأول شمعة صالحة")
    m = _re.search(r"data stale \(age (\d+)min\)", low)
    if m:
        # v8.14.6 — user surface carries the REAL story, no counters (the raw
        # age stays in engine_runs for admin diagnostics only)
        return ("📡 مصدر البيانات يعيد لقطة أقدم من السوق الحي — رفض حماية صادق: "
                "لا نولّد إشارة على أسعار غير حية؛ أعد المحاولة بعد لحظات")
    m = _re.search(r"data frozen \(repeats (\d+), age (\d+)min\)", low)
    if m:
        return ("🧊 مصدر البيانات متجمّد — يعيد نفس اللقطة رغم تحرك السوق؛ "
                "لا إشارة من شموع جامدة أبداً: أعد المحاولة بعد لحظات")
    m = _re.search(r"insufficient candles \((\d+)\)", low)
    if m:
        return "📉 تاريخ الشموع المتاح أقصر من الحد الأدنى للتحليل المؤسسي — لا إشارة من عينة ناقصة"
    if "stale entry" in low:
        return s  # already mixed Arabic — keep as authored
    return s


def _has_arabic(s: str) -> bool:
    return any("\u0600" <= ch <= "\u06FF" for ch in (s or ""))


def ar_run_line(result: str, detail: str = "") -> str:
    """One engine_runs entry rendered in Arabic; only Arabic-bearing detail is
    appended — raw English log leftovers never reach the display layer."""
    line = ar_result(result)
    ar_d = ar_reason(detail)
    if ar_d and _has_arabic(ar_d) and ar_d not in line:
        line += f" — {ar_d}"
    return line


def is_signal_closed(sig) -> bool:
    """True when a signal is finished (terminal status OR closed_at stamped, e.g. TP1/TP2 Breakeven)."""
    if sig is None:
        return True
    try:
        st = (sig["status"] or "").upper()
    except Exception:
        st = ""
    # v8.14.22 — LOCKED_50 is settled (the lock banked the threshold) — never "open".
    if st in ("TP3_HIT", "SL_HIT", "EXPIRED", "CANCELLED", "LOCKED_50"):
        return True
    try:
        closed_at = sig["closed_at"]
    except (KeyError, IndexError, TypeError):
        closed_at = ""
    return bool((closed_at or "").strip())


def signal_status_ar(sig) -> str:
    """Honest lifecycle label that distinguishes OPEN TP1/TP2 runners from CLOSED profit trades."""
    st = (sig["status"] or "").upper()
    if is_signal_closed(sig):
        if st == "TP1_HIT":
            return "مغلقة على ربح — تحقق الهدف الأول وأُغلقت عند التأمين"
        if st == "TP2_HIT":
            return "مغلقة على ربح — تحقق الهدف الثاني وأُغلقت عند التأمين"
    return status_ar(st)


def signal_status_badge(sig) -> str:
    """Rich emoji + bold status line for signal cards (distinguishes closed TP1/TP2 from active)."""
    st = (sig["status"] or "").upper()
    if is_signal_closed(sig) and st in ("TP1_HIT", "TP2_HIT"):
        return f"🏁 <b>{signal_status_ar(sig)} ✅</b>"
    return status_badge(st)


def market_state_ar(open_now) -> str:
    if open_now is True:
        return "🟢 مفتوح"
    if open_now is False:
        return "🔴 مغلق"
    return "🟡 غير محدد"


def esc(s) -> str:
    """HTML-escape any user/provider data before it enters a template."""
    return html.escape(str(s if s is not None else ""))


def status_badge(status: str) -> str:
    style, _label = STATUS_STYLE.get(status, ("BLUE", status))
    return f"{STATUS_EMOJI.get(style, '🔵')} <b>{status_ar(status)}</b>"


def dir_badge(direction: str) -> str:
    ar = {"BUY": "شراء", "SELL": "بيع"}.get((direction or "").upper(), direction)
    return f"{DIRECTION_EMOJI.get(direction, '🔵')} <b>{esc(ar)}</b>"


# --- Frame builders ---------------------------------------------------------
GOLD = "◆"
DIV = "➖" * 8            # thin luxury divider
THICK = "━━━━━━━━━━━━━━━━━━━━"


def card(body: str) -> str:
    """Wrap a section in a native Telegram card (blockquote surface)."""
    return f"<blockquote>{body}</blockquote>"


def expandable(body: str) -> str:
    """Native Telegram expandable card — for long reasons/logs."""
    return f"<blockquote expandable>{body}</blockquote>"


def header(title: str, subtitle: str = "") -> str:
    """Royal header band: crown + brand eyebrow + bold title + gold divider."""
    out = f"{ICON['crown']} <b>{config.BRAND}</b> {GOLD}"
    if config.BUILD_NAME:
        out += f" <i>{config.BUILD_NAME}</i>"
    out += (f"\n"
           f"{THICK}\n"
           f"{ICON['spark']} <b><u>{esc(title)}</u></b>")
    if subtitle:
        out += f"\n<i>{esc(subtitle)}</i>"
    return out


# Tags engine-generated text may legitimately carry (our own markup only).
_SAFE_TAGS = ("b", "code", "i", "u", "blockquote")


def rich(s) -> str:
    """Render engine-generated text that carries intentional <b>/<code> markup.

    Everything is escaped first, then ONLY the whitelisted presentation tags
    are re-opened. Any other angle-bracket content (user input, stray symbols,
    provider noise) stays visibly escaped — so programming code can never leak
    into a Telegram message.
    """
    escaped = esc(s)
    for tag in _SAFE_TAGS:
        escaped = escaped.replace(f"&lt;{tag}&gt;", f"<{tag}>").replace(f"&lt;/{tag}&gt;", f"</{tag}>")
    return escaped


def kv(label: str, value: str, color: str = "WHITE") -> str:
    """Labelled field line — the atomic unit of every card."""
    return f"▎<b>{esc(label)}:</b> {value}"


def kv2(label: str, value: str, label2: str, value2: str) -> str:
    """Two labelled fields on one luxury line."""
    return f"▎<b>{esc(label)}:</b> {value}  ·  <b>{esc(label2)}:</b> {value2}"


def section(title: str) -> str:
    return f"\n{DIV}\n{GOLD} <b>{esc(title).upper()}</b> {GOLD}\n{DIV}"


def footer(note: str = "") -> str:
    if note:
        return f"\n<i>{esc(note)}</i>"
    return ""


def disclaimer() -> str:
    return "<i>⚠️ محتوى تحليلي لأغراض المعلومات فقط — ليس نصيحة استثمارية ولا ضماناً للربح.</i>"


def page(title: str, subtitle: str, body: str, note: str = "") -> str:
    out = header(title, subtitle)
    if body:
        out += "\n" + body
    out += footer(note)
    return out


def bullets(items: Iterable[str], marker: str = ICON["bullet"]) -> str:
    return "\n".join(f"{marker} {it}" for it in items)


def bar(current: int, total: int, width: int = 10) -> str:
    """Tiny progress bar ▰▰▰▱▱ — luxury stat chip."""
    if total <= 0:
        filled = 0
    else:
        filled = max(0, min(width, round(current / total * width)))
    return "▰" * filled + "▱" * (width - filled)


def pct(v: float) -> str:
    return f"{v:.1f}%"


def money(v: float, currency: str = "USD") -> str:
    return f"{v:,.2f} {esc(currency)}"


def human_dt(iso: str) -> str:
    """Convert any UTC/ISO timestamp -> 12-hour Mecca time:
    2026-11-24T06:12:00+00:00 -> 2026-11-24 · 09:12am بتوقيت مكة"""
    if not iso:
        return "—"
    from fysb import config
    rendered = config.mecca_dt_12h(iso)
    if rendered and rendered != "—":
        return esc(rendered)
    return esc(str(iso)[:19].replace("T", " "))


def ago(iso: str, now_iso: str) -> str:
    """Rough humanized delta between two ISO timestamps."""
    try:
        from datetime import datetime
        a = datetime.fromisoformat(iso)
        b = datetime.fromisoformat(now_iso)
        d = (b - a).total_seconds()
    except Exception:
        return "—"
    if d < 0:
        return "—"
    if d < 60:
        return "الآن"
    if d < 3600:
        return f"قبل {int(d // 60)} دقيقة"
    if d < 86400:
        return f"قبل {int(d // 3600)} ساعة"
    return f"قبل {int(d // 86400)} يوم"


def masked_id(admin_id: int) -> str:
    """Show only the tail of an ID — never a full credential-ish value."""
    s = str(admin_id)
    if len(s) <= 4:
        return "*" * len(s)
    return "*" * (len(s) - 3) + s[-3:]
