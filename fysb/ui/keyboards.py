"""Keyboard builder — interactive-first navigation (buttons over typing).

IMPORTANT (root-cause fix preserved): callback tuples already carry their
prefix (u:/a:) — the builder must NOT add a second prefix. The previous
double-prefix bug ("u:u:dashboard") made every user section fall into the
generic "section unavailable" fallback.
"""
from telegram import InlineKeyboardButton, InlineKeyboardMarkup

from fysb.theme import ICON

# --- Luxury button palette ----------------------------------------------------
# Telegram colors buttons only through markers — the royal language is a
# colored dot before the icon, semantically consistent everywhere:
#   Emerald 🟢 = live trading actions      Gold 🟡 = premium / money / invites
#   Ruby 🔴   = destructive actions        Sapphire 🔵 = navigation & info
#   Royal 🟣  = elite institutional tools  Amber 🟠 = pending / caution
LUX = {
    "emerald": "🟢", "gold": "🟡", "ruby": "🔴", "sapphire": "🔵",
    "royal": "🟣", "amber": "🟠", "pearl": "⚪", "onyx": "⚫",
}


def btn(text: str, data: str) -> InlineKeyboardButton:
    return InlineKeyboardButton(text, callback_data=data)


def lux(label: str, data: str, color: str = "sapphire") -> InlineKeyboardButton:
    """Luxury colored button — dot + label, same callback contract as btn()."""
    return InlineKeyboardButton(f"{LUX.get(color, '🔵')} {label}", callback_data=data)


# --- User menus --------------------------------------------------------------
def user_menu() -> InlineKeyboardMarkup:
    groups = [
        [("🟢 📊 لوحة التحكم", "u:dashboard"), ("🟢 📡 الإشارات المباشرة", "u:signals")],
        [("🟣 🔬 التحليلات الاحترافية", "u:analytics"), ("🔵 👤 ملفي الشخصي", "u:profile")],
        [("🟡 💎 الباقات والاشتراك", "u:plans"), ("🟡 🧾 مدفوعاتي", "u:payments")],
        [("🟢 🧪 التداول التجريبي", "u:demo"), ("🔵 🎛 تخصيص الإشارات", "u:preferences")],
        [("🔵 📈 إحصائياتي", "u:my_stats"), ("🔵 🔔 الإشعارات", "u:notifications")],
        [("🟡 🤝 دعوة صديق", "u:referrals"), ("🔵 🎧 الدعم الفني", "u:support")],
        [("🔵 ❓ مركز المساعدة", "u:help")],
    ]
    rows = [[btn(t, a) for t, a in row] for row in groups]
    return InlineKeyboardMarkup(rows)


def user_nav(extra_rows: list | None = None) -> InlineKeyboardMarkup:
    rows = extra_rows or []
    rows.append([btn(f"{ICON['home']} القائمة الرئيسية", "u:home")])
    return InlineKeyboardMarkup(rows)


def user_menu_signal_buttons(rows) -> InlineKeyboardMarkup:
    kb = [[btn(f"{'🟢' if r['direction'] == 'BUY' else '🔴'} #{r['id']} {r['symbol']} {r['direction']} · {r['score']}/100",
               f"u:signalview:{r['id']}")] for r in rows]
    return user_nav(kb)


# --- Admin menus (28 sections, grouped visually + royal colors) --------------
def admin_menu() -> InlineKeyboardMarkup:
    groups = [
        # Core operations
        [("🔵 📊 لوحة المعلومات", "a:stats"), ("🔵 📈 الإحصائيات", "a:statistics")],
        [("🟡 👥 المستخدمون", "a:users"), ("🟡 💎 الاشتراكات", "a:subscriptions")],
        [("🟡 💠 الباقات", "a:plans"), ("🟡 💳 المدفوعات", "a:payments")],
        [("🟡 🏦 وسائل الدفع", "a:paymethods"), ("🟢 📡 الإشارات", "a:signals")],
        # Trading
        [("🟣 🧠 محرك السوق", "a:engine"), ("🟣 🧩 الاستراتيجيات", "a:strategies")],
        [("🟣 ⚙️ أوضاع التشغيل", "a:modes"), ("🟣 🛡 إدارة المخاطر", "a:risk")],
        [("🟣 🌐 الأسواق", "a:markets"), ("🟣 ⏱ الأطر الزمنية", "a:timeframes")],
        # Accounts & trading modes
        [("🟢 🏦 الوسطاء", "a:brokers"), ("🟢 🔗 حسابات التداول", "a:accounts")],
        [("🟢 🧪 التداول التجريبي", "a:demo"), ("🔴 🔒 التداول الحقيقي", "a:live")],
        # Analytics governance + royal referrals
        [("🟣 🔬 إدارة التحليلات", "a:analytics")],
        [("🟡 🤝 نظام الإحالات", "a:refs")],
        # Communication
        [("🔵 📈 التقارير", "a:reports"), ("🔵 🔔 الإشعارات", "a:notifications")],
        [("🟠 📣 الإعلانات", "a:broadcast"), ("🔵 🎧 الدعم الفني", "a:support")],
        # Channels & groups (scheduled auto-posts, Mecca time)
        [("🟣 📺 القنوات والمجموعات", "a:channels")],
        # System
        [("🔴 🔐 الأمان", "a:security"), ("🔵 💾 النسخ الاحتياطي", "a:backup")],
        [("🔵 ⚙️ الإعدادات", "a:settings"), ("🔵 🧾 سجل الإدارة", "a:audit")],
        [("🟢 🖥 حالة النظام", "a:health")],
    ]
    rows = [[btn(t, a) for t, a in row] for row in groups]
    return InlineKeyboardMarkup(rows)


def admin_nav(extra_rows: list | None = None) -> InlineKeyboardMarkup:
    rows = list(extra_rows or [])
    rows.append([btn("↩️ مركز الإدارة", "a:home")])
    return InlineKeyboardMarkup(rows)
