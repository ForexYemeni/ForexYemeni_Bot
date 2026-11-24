"""Keyboard builder — Bot API 9.4 native styled buttons & clean Hub navigation (v6.13.0).

Upgrades over v6.x:
  * Real Telegram Bot API 9.4 colored buttons via `style="primary"|"success"|"danger"`
    (supported natively by python-telegram-bot 22.8 — replaces double-emoji dots).
  * Native one-tap `CopyTextButton` helper (`copy_btn`) for wallet addresses,
    account numbers, and referral links.
  * Uncluttered User Home (6 clean rows) and Categorized Admin Command Center
    (4 clean rows with 5 focused sub-hubs instead of 28 buttons dumped on one screen).

IMPORTANT (root-cause fix preserved): callback tuples already carry their
prefix (u:/a:) — the builder NEVER adds a second prefix.
"""
import inspect
from telegram import InlineKeyboardButton, InlineKeyboardMarkup

try:
    from telegram import CopyTextButton
except ImportError:  # pragma: no cover — PTB < 21.6 compatibility
    CopyTextButton = None

from fysb.theme import ICON

# Runtime capability detection so the bot NEVER crashes with TypeError if a
# host still runs python-telegram-bot < 22.7 (which predates Bot API 9.4 `style`).
_BTN_PARAMS = inspect.signature(InlineKeyboardButton.__init__).parameters
_HAS_STYLE = "style" in _BTN_PARAMS
_HAS_COPY_TEXT = (CopyTextButton is not None) and ("copy_text" in _BTN_PARAMS)

# Native Bot API 9.4 button styles:
#   "primary" -> Royal Sapphire Blue (main hubs, analytics, navigation, pickers)
#   "success" -> Royal Emerald Green (BUY, live signals, approve, add, enable, VIP, profit)
#   "danger"  -> Royal Ruby Red (SELL, reject, delete, stop/disable, block, risk, kill switch)
VALID_STYLES = ("primary", "success", "danger")

_LUX_TO_STYLE: dict[str, str] = {
    "emerald": "success",
    "ruby": "danger",
    "amber": "danger",
    "sapphire": "primary",
    "royal": "primary",
    "gold": "success",
    "pearl": "primary",
    "onyx": "primary",
}

# Kept for backward-compat imports; visual styling now uses native Bot API 9.4 `style`.
LUX = {
    "emerald": "🟢", "gold": "🟡", "ruby": "🔴", "sapphire": "🔵",
    "royal": "🟣", "amber": "🟠", "pearl": "⚪", "onyx": "⚫",
}

_NAV_MARKERS = ("↩️", "🏠", "⬅️", "➡️", "◀️", "القائمة الرئيسية", "مركز الإدارة", "السابق", "التالي", "كل القنوات")

_DANGER_TEXT_MARKERS = (
    "🔴", "🗑", "⛔", "🚫", "⏹", "🛑", "❌", "🔒", "⚠️", "📉", "🧹", "🧯", "🔐",
    "حذف", "إزالة", "رفض", "مرفوض", "حظر", "إيقاف", "تعطيل", "إغلاق", "بيع", "sell",
    "مخاطر", "طوارئ", "تصفير", "إلغاء", "تنظيف", "مسح", "استعادة",
    "تجميد", "طرد", "قفل", "الأمان", "التداول الحقيقي", "حالة الطوارئ",
)
_DANGER_DATA_MARKERS = (
    "del", "reject", "ban", "block", "kill", "stop", "close", "reset",
    "cancel", "sell", "risk", "clean", "lock", "disable", "security", "live",
    "revoke", "purge", "clear", "wipe",
)

_SUCCESS_TEXT_MARKERS = (
    "🟢", "✅", "➕", "🚀", "⚡", "▶️", "🎯", "💰", "💵", "💳", "💎", "🏆",
    "📈", "🧪", "🤝", "💾", "📡", "🟢",
    "تفعيل", "تشغيل", "قبول", "اعتماد", "موافقة", "إضافة", "إنشاء", "جديد",
    "حفظ", "تأكيد", "شراء", "buy", "نشر", "إرسال", "بث", "الآن", "فوري",
    "مباشر", "ربح", "أرباح", "مدفوعات", "دفع", "اشتراك", "باقة", "ترقية",
    "دعوة", "إحالة", "فحص", "تجريبي", "اختبار", "مفتوحة", "تربح", "أداء",
    "تحديث الآن", "نسخ احتياطي الآن", "حالة النظام",
)
_SUCCESS_DATA_MARKERS = (
    "approve", "add", "new", "create", "enable", "start", "run", "now",
    "buy", "pay", "plan", "profit", "demo", "ref", "track", "post",
    "btrun", "chanall", "save", "confirm", "bcgo", "sigpl", "signow",
    "backtest", "backupnow", "update", "health", "support",
)


def infer_luxury_style(text: str, data: str = "") -> str:
    """Assign a harmonious Bot API 9.4 native color ('primary'|'success'|'danger')
    to EVERY button so zero dull gray unstyled buttons remain anywhere in the bot."""
    t_raw = str(text or "")
    t = t_raw.lower()
    d = str(data or "").lower()

    # 1. Navigation / back / pagination buttons always stay crisp Royal Sapphire Blue
    if any(m in t_raw for m in _NAV_MARKERS) or d in ("u:home", "a:home", "a:plannoop"):
        return "primary"

    # 2. Destructive / stop / sell / risk / lock actions -> Royal Ruby Red
    if any(m in t for m in _DANGER_TEXT_MARKERS):
        return "danger"
    if any(tok in d for tok in _DANGER_DATA_MARKERS) and not any(
        ok in t for ok in ("✅", "🟢", "▶️", "تفعيل", "تشغيل", "قبول", "حفظ")
    ):
        return "danger"

    # 3. Positive / active / live / financial / launch actions -> Royal Emerald Green
    if any(m in t for m in _SUCCESS_TEXT_MARKERS):
        return "success"
    if any(tok in d for tok in _SUCCESS_DATA_MARKERS):
        return "success"

    # 4. All remaining hubs, analytics, settings, pickers & info -> Royal Sapphire Blue
    return "primary"


def btn(text: str, data: str, style: str | None = None) -> InlineKeyboardButton:
    """Native callback button with full Bot API 9.4 luxury color styling (PTB-safe).

    When `style` is omitted (None), automatically applies `infer_luxury_style()`
    so every button across the entire bot renders in rich Royal Sapphire / Emerald / Ruby.
    """
    resolved = style if style in VALID_STYLES else ("" if style == "" else infer_luxury_style(text, data))
    if resolved and _HAS_STYLE:
        return InlineKeyboardButton(text, callback_data=data, style=resolved)
    return InlineKeyboardButton(text, callback_data=data)


def copy_btn(text: str, copy_value: str, style: str | None = "primary") -> InlineKeyboardButton:
    """Native one-tap CopyTextButton (falls back safely on older PTB)."""
    val = str(copy_value or "")
    resolved = style if style in VALID_STYLES else infer_luxury_style(text)
    if _HAS_COPY_TEXT:
        if resolved and _HAS_STYLE:
            return InlineKeyboardButton(text, copy_text=CopyTextButton(text=val), style=resolved)
        return InlineKeyboardButton(text, copy_text=CopyTextButton(text=val))
    return btn(text, "a:plannoop", style=resolved)


def url_btn(text: str, url: str, style: str | None = None) -> InlineKeyboardButton:
    """Native URL button with Bot API 9.4 luxury color style (PTB-safe)."""
    resolved = style if style in VALID_STYLES else infer_luxury_style(text, url)
    if resolved and _HAS_STYLE:
        return InlineKeyboardButton(text, url=url, style=resolved)
    return InlineKeyboardButton(text, url=url)


def strip_button_styles(markup: InlineKeyboardMarkup | None) -> InlineKeyboardMarkup | None:
    """Return an equivalent keyboard with Bot API 9.4 `style` removed (fallback
    if any Telegram server/client rejects styled buttons with BadRequest)."""
    if markup is None or not hasattr(markup, "inline_keyboard"):
        return markup
    new_rows = []
    for row in markup.inline_keyboard:
        new_row = []
        for b in row:
            if getattr(b, "callback_data", None) is not None:
                new_row.append(InlineKeyboardButton(b.text, callback_data=b.callback_data))
            elif getattr(b, "url", None) is not None:
                new_row.append(InlineKeyboardButton(b.text, url=b.url))
            elif getattr(b, "copy_text", None) is not None and _HAS_COPY_TEXT:
                new_row.append(InlineKeyboardButton(b.text, copy_text=b.copy_text))
            else:
                new_row.append(InlineKeyboardButton(b.text, callback_data="u:home"))
        new_rows.append(new_row)
    return InlineKeyboardMarkup(new_rows)


def lux(label: str, data: str, color: str = "sapphire") -> InlineKeyboardButton:
    """Luxury button mapped to native Bot API 9.4 color styles (no dot clutter)."""
    return btn(label, data, style=_LUX_TO_STYLE.get(color, "primary"))


# --- User menus (6 luxury rows, 100% native Bot API 9.4 colored buttons) -----
def user_menu() -> InlineKeyboardMarkup:
    """Luxury 6-row user menu (100% Bot API 9.4 native styled buttons, v6.20.8):
    Row 1 puts Signal Now & Analysis Now front and center for instant 1-tap access."""
    rows = [
        [btn("⚡ إشارة الآن", "u:signow", style="success"),
         btn("🔬 تحليل الآن", "u:analytics", style="primary")],
        [btn("📡 الإشارات المباشرة", "u:signals", style="primary"),
         btn("🧪 التداول التجريبي", "u:demo", style="success")],
        [btn("📊 لوحة التحكم", "u:dashboard", style="primary"),
         btn("🌐 أسواقي وتخصيصي", "u:preferences", style="primary")],
        [btn("💎 الباقات والاشتراك", "u:plans", style="success"),
         btn("🧾 مدفوعاتي", "u:payments", style="primary")],
        [btn("👤 ملفي وإحصائياتي", "u:profile", style="primary"),
         btn("🤝 دعوة صديق", "u:referrals", style="success")],
        [btn("🔔 الإشعارات", "u:notifications", style="primary"),
         btn("🎧 الدعم والمساعدة", "u:support", style="primary")],
    ]
    return InlineKeyboardMarkup(rows)


def user_nav(extra_rows: list | None = None) -> InlineKeyboardMarkup:
    rows = list(extra_rows or [])
    rows.append([btn(f"{ICON['home']} القائمة الرئيسية", "u:home", style="primary")])
    return InlineKeyboardMarkup(rows)


def user_menu_signal_buttons(rows) -> InlineKeyboardMarkup:
    kb = [[btn(
        f"{'🟢' if r['direction'] == 'BUY' else '🔴'} #{r['id']} {r['symbol']} {r['direction']} · {r['score']}/100",
        f"u:signalview:{r['id']}",
        style="success" if r["direction"] == "BUY" else "danger",
    )] for r in rows]
    return user_nav(kb)


# --- Admin Command Center (clean 4-row Home + 5 focused sub-hubs, 100% styled)
def admin_menu() -> InlineKeyboardMarkup:
    """Luxury Admin Command Center — 4 crisp rows, 100% native Bot API 9.4 colors."""
    rows = [
        [btn("📊 لوحة المعلومات", "a:stats", style="primary"),
         btn("📡 الإشارات والمحرك", "a:hub:trading", style="success")],
        [btn("👥 المستخدمون والمالية", "a:hub:finance", style="primary"),
         btn("📺 القنوات والتواصل", "a:hub:comms", style="primary")],
        [btn("🏦 الوسطاء والتنفيذ", "a:hub:execution", style="primary"),
         btn("🛡 النظام والأمان", "a:hub:system", style="danger")],
        [btn("💳 المدفوعات", "a:payments", style="success"),
         btn("🖥 حالة النظام", "a:health", style="success")],
    ]
    return InlineKeyboardMarkup(rows)


def admin_hub_trading() -> InlineKeyboardMarkup:
    return admin_nav([
        [btn("📡 إدارة الإشارات", "a:signals", style="success"),
         btn("🧠 محرك السوق", "a:engine", style="primary")],
        [btn("⚡ إشارة الآن", "a:signow", style="success"),
         btn("🎯 أسواق إشاراتي", "a:adminmkts", style="primary")],
        [btn("🧩 الاستراتيجيات (13)", "a:strategies", style="primary"),
         btn("🛡 إدارة المخاطر", "a:risk", style="danger")],
        [btn("🌐 الأسواق", "a:markets", style="primary"),
         btn("⏱ الأطر الزمنية", "a:timeframes", style="primary")],
        [btn("🔬 إدارة التحليلات", "a:analytics", style="primary"),
         btn("⚙️ أوضاع التشغيل", "a:modes", style="primary")],
    ])


def admin_hub_finance() -> InlineKeyboardMarkup:
    return admin_nav([
        [btn("👥 المستخدمون", "a:users", style="primary"),
         btn("💎 الاشتراكات", "a:subscriptions", style="success")],
        [btn("💳 المدفوعات", "a:payments", style="success"),
         btn("💠 الباقات", "a:plans", style="primary")],
        [btn("🏦 وسائل الدفع", "a:paymethods", style="primary"),
         btn("🤝 نظام الإحالات", "a:refs", style="success")],
        [btn("👑 شركاء القنوات و VIP", "a:partners", style="success")],
        [btn("📈 الإحصائيات", "a:statistics", style="primary"),
         btn("📋 التقارير", "a:reports", style="primary")],
    ])


def admin_hub_comms() -> InlineKeyboardMarkup:
    return admin_nav([
        [btn("📺 القنوات والمجموعات", "a:channels", style="primary"),
         btn("👑 شركاء القنوات و VIP", "a:partners", style="success")],
        [btn("📣 الإعلانات", "a:broadcast", style="success"),
         btn("🎧 الدعم الفني", "a:support", style="success")],
        [btn("🔔 الإشعارات", "a:notifications", style="primary")],
    ])


def admin_hub_execution() -> InlineKeyboardMarkup:
    return admin_nav([
        [btn("🏦 الوسطاء", "a:brokers", style="primary"),
         btn("🔗 حسابات التداول", "a:accounts", style="primary")],
        [btn("🧪 التداول التجريبي", "a:demo", style="success"),
         btn("🔒 التداول الحقيقي", "a:live", style="danger")],
    ])


def admin_hub_system() -> InlineKeyboardMarkup:
    return admin_nav([
        [btn("🔐 الأمان", "a:security", style="danger"),
         btn("💾 النسخ الاحتياطي", "a:backup", style="primary")],
        [btn("⚙️ الإعدادات", "a:settings", style="primary"),
         btn("🧾 سجل الإدارة", "a:audit", style="primary")],
        [btn("🔄 إدارة التحديثات", "a:updates", style="primary"),
         btn("🖥 حالة النظام", "a:health", style="success")],
    ])


ADMIN_HUBS = {
    "trading": ("📡 <b><u>مركز التداول والإشارات</u></b>\n▎إدارة الإشارات، محرك السوق، الـ 13 استراتيجية الحقيقية، الأسواق والمخاطر:", admin_hub_trading),
    "finance": ("👥 <b><u>مركز المستخدمين والمالية</u></b>\n▎دليل المستخدمين، الاشتراكات، الباقات، المدفوعات، الإحالات والتقارير:", admin_hub_finance),
    "comms": ("📺 <b><u>مركز القنوات والتواصل</u></b>\n▎القنوات والمجموعات، الإعلانات المجدولة، الإشعارات وتذاكر الدعم:", admin_hub_comms),
    "execution": ("🏦 <b><u>مركز الوسطاء والتنفيذ</u></b>\n▎إدارة الوسطاء المشفرين، حسابات التداول، التجريبي وقفل التداول الحقيقي:", admin_hub_execution),
    "system": ("🛡 <b><u>مركز النظام والأمان</u></b>\n▎الأمان، النسخ الاحتياطي، الإعدادات العامة، سجل الإدارة وصحة النظام:", admin_hub_system),
}


def admin_nav(extra_rows: list | None = None) -> InlineKeyboardMarkup:
    rows = list(extra_rows or [])
    rows.append([btn("↩️ مركز الإدارة", "a:home", style="primary")])
    return InlineKeyboardMarkup(rows)
