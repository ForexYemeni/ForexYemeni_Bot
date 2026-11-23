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

# --- Arabic display names (users NEVER see raw machine codes) ---------------
STRATEGY_AR = {
    "ICT": "آي سي تي المؤسسية",
    "SMC": "المال الذكي",
    "PRICE_ACTION": "حركة السعر",
    "CRT": "نظرية مدى الشمعة",
    "AMD": "قوة الثلاثة (تراكم/تلاعب/توزيع)",
    "TURTLE_SOUP": "اصطياد السيولة",
    "SILVER_BULLET": "الرصاصة الفضية",
    "WYCKOFF": "وايكوف",
    "FIBONACCI": "فيبوناتشي (الجيب الذهبي)",
    "TREND": "اتباع الاتجاه",
    "ICHIMOKU": "إيشيموكو (السحابة اليابانية)",
    # --- v6.8.1: أسماء الاستراتيجيات العالمية المضافة (كل مدرسة) ---
    "ELLIOTT_WAVE": "موجات إليوت",
    "HARMONIC": "الأنماط التوافقية (هارمونيك)",
    "VOLUME_PROFILE": "ملف الحجم (Volume Profile)",
    "VWAP": "السعر المرجح بالحجم (VWAP)",
    "BREAKOUT_RETEST": "الكسر وإعادة الاختبار",
    "SUPPORT_RESISTANCE": "الدعم والمقاومة",
    "RSI_DIVERGENCE": "دايفرجنس RSI",
    "MACD_STRAT": "زخم MACD",
    "BOLLINGER": "بولينجر باندز",
    "DONCHIAN": "قناة دونشيان (قواعد السلاحف)",
    "MA_CROSS": "تقاطع المتوسطات",
    "SUPERTREND": "السوبر تريند",
    "KELTNER": "قنوات كلتنر",
    "ORB": "كسر مدى الافتتاح (ORB)",
    "ASIA_RANGE": "مدى جلسة آسيا",
    "LONDON_BREAK": "كسر لندن",
    "PIVOT_POINTS": "نقاط المحور",
    "CPR": "مدى المحور المركزي (CPR)",
    "MARKET_PROFILE": "ملف السوق (TPO)",
    "ORDER_FLOW": "تدفق الأوامر والدلتا",
    "MEAN_REVERSION": "الارتداد للمتوسط",
    "GAP_TRADE": "تداول الفجوات",
    "HEIKIN_ASHI": "شموع هيكين آشي",
    "MOMENTUM": "اندفاع الزخم",
    "ATR_BREAKOUT": "كسر التقلب ATR",
    "MANUAL": "إشارة يدوية من الإدارة",
    "ALL": "جميع الاستراتيجيات",
}

TIMEFRAME_AR = {
    "M1": "1 دقيقة", "M5": "5 دقائق", "M15": "15 دقيقة", "M30": "30 دقيقة",
    "H1": "ساعة", "H4": "4 ساعات", "D1": "يومي", "W1": "أسبوعي",
}

# v6.8.0 — luxury market sections (owner instruction: المعادن/الفوركس/العملات/
# الأسهم as separated royal sections). Any unknown category falls back to its code.
CATEGORY_AR = {
    "metal": "المعادن",
    "forex": "الفوركس والعملات",
    "crypto": "العملات الرقمية",
    "index": "المؤشرات العالمية",
    "stock": "الأسهم الأمريكية",
    "commodity": "السلع",
}

CATEGORY_ICON = {
    "metal": "🥇", "forex": "💱", "crypto": "🪙", "index": "📊", "stock": "🏛", "commodity": "🛢",
}

STATUS_AR = {
    "ACTIVE": "نشطة — بانتظار التنفيذ",
    "RUNNING": "نشطة — قيد التنفيذ في السوق",
    "TP1_HIT": "تحقق الهدف الأول — مأمونة",
    "TP2_HIT": "تحقق الهدف الثاني",
    "TP3_HIT": "تحقق الهدف الثالث — مكتملة",
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
    """2026-11-21T16:00:34.144230+00:00 -> 2026-11-21 16:00 UTC"""
    if not iso:
        return "—"
    return esc(iso[:19].replace("T", " ")) + " UTC"


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
