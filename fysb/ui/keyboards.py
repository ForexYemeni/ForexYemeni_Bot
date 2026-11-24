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
#   "primary" -> Blue (main CTAs, analytics, navigation hubs)
#   "success" -> Green (BUY, approve, live signals, confirm, share)
#   "danger"  -> Red (SELL, reject, delete, block, kill switch, live lock)
#   None      -> Neutral (secondary navigation, info, back)
_LUX_TO_STYLE: dict[str, str | None] = {
    "emerald": "success",
    "ruby": "danger",
    "sapphire": "primary",
    "royal": "primary",
    "gold": "primary",
    "amber": None,
    "pearl": None,
    "onyx": None,
}

# Kept for backward-compat imports; visual styling now uses native Bot API 9.4 `style`.
LUX = {
    "emerald": "🟢", "gold": "🟡", "ruby": "🔴", "sapphire": "🔵",
    "royal": "🟣", "amber": "🟠", "pearl": "⚪", "onyx": "⚫",
}


def btn(text: str, data: str, style: str | None = None) -> InlineKeyboardButton:
    """Native callback button with optional Bot API 9.4 color style (PTB-safe)."""
    if style and _HAS_STYLE:
        return InlineKeyboardButton(text, callback_data=data, style=style)
    return InlineKeyboardButton(text, callback_data=data)


def copy_btn(text: str, copy_value: str, style: str | None = "primary") -> InlineKeyboardButton:
    """Native one-tap CopyTextButton (falls back safely on older PTB)."""
    val = str(copy_value or "")
    if _HAS_COPY_TEXT:
        if style and _HAS_STYLE:
            return InlineKeyboardButton(text, copy_text=CopyTextButton(text=val), style=style)
        return InlineKeyboardButton(text, copy_text=CopyTextButton(text=val))
    return InlineKeyboardButton(text, callback_data="a:plannoop")


def url_btn(text: str, url: str, style: str | None = None) -> InlineKeyboardButton:
    """Native URL button with optional Bot API 9.4 color style (PTB-safe)."""
    if style and _HAS_STYLE:
        return InlineKeyboardButton(text, url=url, style=style)
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
    return btn(label, data, style=_LUX_TO_STYLE.get(color))


# --- User menus (6 clean rows, single icon per button, native colors) --------
def user_menu() -> InlineKeyboardMarkup:
    rows = [
        [btn("📡 الإشارات المباشرة", "u:signals", style="success"),
         btn("🔬 التحليلات الاحترافية", "u:analytics", style="primary")],
        [btn("📊 لوحة التحكم", "u:dashboard", style="primary"),
         btn("🧪 التداول التجريبي", "u:demo", style="success")],
        [btn("💎 الباقات والاشتراك", "u:plans", style="primary"),
         btn("🧾 مدفوعاتي", "u:payments")],
        [btn("🎛 تخصيص الإشارات", "u:preferences"),
         btn("👤 ملفي وإحصائياتي", "u:profile")],
        [btn("🔔 الإشعارات", "u:notifications"),
         btn("🤝 دعوة صديق", "u:referrals", style="success")],
        [btn("🎧 الدعم الفني", "u:support"),
         btn("❓ مركز المساعدة", "u:help")],
    ]
    return InlineKeyboardMarkup(rows)


def user_nav(extra_rows: list | None = None) -> InlineKeyboardMarkup:
    rows = list(extra_rows or [])
    rows.append([btn(f"{ICON['home']} القائمة الرئيسية", "u:home")])
    return InlineKeyboardMarkup(rows)


def user_menu_signal_buttons(rows) -> InlineKeyboardMarkup:
    kb = [[btn(
        f"{'🟢' if r['direction'] == 'BUY' else '🔴'} #{r['id']} {r['symbol']} {r['direction']} · {r['score']}/100",
        f"u:signalview:{r['id']}",
        style="success" if r["direction"] == "BUY" else "danger",
    )] for r in rows]
    return user_nav(kb)


# --- Admin Command Center (clean 4-row Home + 5 focused sub-hubs) ------------
def admin_menu() -> InlineKeyboardMarkup:
    """Clean, high-speed Admin Command Center — 4 crisp rows instead of 28 dumped buttons."""
    rows = [
        [btn("📊 لوحة المعلومات", "a:stats", style="primary"),
         btn("📡 الإشارات والمحرك", "a:hub:trading", style="success")],
        [btn("👥 المستخدمون والمالية", "a:hub:finance", style="primary"),
         btn("📺 القنوات والتواصل", "a:hub:comms", style="primary")],
        [btn("🏦 الوسطاء والتنفيذ", "a:hub:execution"),
         btn("🛡 النظام والأمان", "a:hub:system")],
        [btn("💳 المدفوعات", "a:payments", style="success"),
         btn("🖥 حالة النظام", "a:health")],
    ]
    return InlineKeyboardMarkup(rows)


def admin_hub_trading() -> InlineKeyboardMarkup:
    return admin_nav([
        [btn("📡 إدارة الإشارات", "a:signals", style="success"),
         btn("🧠 محرك السوق", "a:engine", style="primary")],
        [btn("🧩 الاستراتيجيات (13)", "a:strategies", style="primary"),
         btn("🛡 إدارة المخاطر", "a:risk", style="danger")],
        [btn("🌐 الأسواق", "a:markets"),
         btn("⏱ الأطر الزمنية", "a:timeframes")],
        [btn("🔬 إدارة التحليلات", "a:analytics"),
         btn("⚙️ أوضاع التشغيل", "a:modes")],
    ])


def admin_hub_finance() -> InlineKeyboardMarkup:
    return admin_nav([
        [btn("👥 المستخدمون", "a:users", style="primary"),
         btn("💎 الاشتراكات", "a:subscriptions", style="primary")],
        [btn("💳 المدفوعات", "a:payments", style="success"),
         btn("💠 الباقات", "a:plans")],
        [btn("🏦 وسائل الدفع", "a:paymethods"),
         btn("🤝 نظام الإحالات", "a:refs")],
        [btn("📈 الإحصائيات", "a:statistics"),
         btn("📋 التقارير", "a:reports")],
    ])


def admin_hub_comms() -> InlineKeyboardMarkup:
    return admin_nav([
        [btn("📺 القنوات والمجموعات", "a:channels", style="primary"),
         btn("📣 الإعلانات", "a:broadcast", style="primary")],
        [btn("🎧 الدعم الفني", "a:support", style="success"),
         btn("🔔 الإشعارات", "a:notifications")],
    ])


def admin_hub_execution() -> InlineKeyboardMarkup:
    return admin_nav([
        [btn("🏦 الوسطاء", "a:brokers", style="primary"),
         btn("🔗 حسابات التداول", "a:accounts")],
        [btn("🧪 التداول التجريبي", "a:demo", style="success"),
         btn("🔒 التداول الحقيقي", "a:live", style="danger")],
    ])


def admin_hub_system() -> InlineKeyboardMarkup:
    return admin_nav([
        [btn("🔐 الأمان", "a:security", style="danger"),
         btn("💾 النسخ الاحتياطي", "a:backup", style="primary")],
        [btn("⚙️ الإعدادات", "a:settings"),
         btn("🧾 سجل الإدارة", "a:audit")],
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
    rows.append([btn("↩️ مركز الإدارة", "a:home")])
    return InlineKeyboardMarkup(rows)
