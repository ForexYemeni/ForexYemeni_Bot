"""v6.20.8 regression tests — 100% luxury Bot API 9.4 native button coloring."""
from __future__ import annotations

import glob

from fysb.ui import keyboards as kb


def test_every_flagship_menu_and_hub_button_is_colored():
    menus = [
        kb.user_menu(),
        kb.user_nav([[kb.btn("إجراء تجريبي", "u:demo")]]),
        kb.admin_menu(),
        kb.admin_hub_trading(),
        kb.admin_hub_finance(),
        kb.admin_hub_comms(),
        kb.admin_hub_execution(),
        kb.admin_hub_system(),
    ]
    for mk in menus:
        for row in mk.inline_keyboard:
            for b in row:
                st = b.to_dict().get("style")
                assert st in kb.VALID_STYLES, f"Button {b.text!r} missing native Bot API 9.4 style"


def test_auto_styler_assigns_semantic_luxury_colors_without_false_positives():
    # Navigation -> primary (Royal Sapphire Blue)
    assert kb.btn("↩️ مركز الإدارة", "a:home").to_dict()["style"] == "primary"
    assert kb.btn("🏠 القائمة الرئيسية", "u:home").to_dict()["style"] == "primary"
    assert kb.btn("⬅️ السابق", "a:sigday:today:all:0").to_dict()["style"] == "primary"

    # Positive / Live / Profit / Launch -> success (Royal Emerald Green)
    assert kb.btn("💰 محاسبة الربح والخسارة", "a:sigpl").to_dict()["style"] == "success"
    assert kb.btn("📈 إحصائيات مفصلة", "a:statistics").to_dict()["style"] == "success"
    assert kb.btn("➕ ربط قناة / مجموعة", "a:chanadd").to_dict()["style"] == "success"
    assert kb.btn("🚀 دورة فورية الآن", "a:enginenow").to_dict()["style"] == "success"
    assert kb.btn("✅ XAUUSD", "a:chanfeatset:1:markets:XAUUSD").to_dict()["style"] == "success"

    # Destructive / Stop / Reject / Risk -> danger (Royal Ruby Red)
    assert kb.btn("🗑 إلغاء", "a:chancancel").to_dict()["style"] == "danger"
    assert kb.btn("🔴 إزالة الربط", "a:chandel:1").to_dict()["style"] == "danger"
    assert kb.btn("⏹ إيقاف المحرك", "a:enginetoggle").to_dict()["style"] == "danger"
    assert kb.btn("المرفوضة", "a:paymentsstatus:REJECTED").to_dict()["style"] == "danger"
    assert kb.btn("🔄 تصفير الحساب", "u:demoreset").to_dict()["style"] == "danger"

    # Unselected picker / generic info -> primary (never plain gray!)
    assert kb.btn("▫️ XAUUSD", "a:chanfeatset:1:markets:XAUUSD").to_dict()["style"] == "primary"
    assert kb.btn("🌐 الأسواق", "a:markets").to_dict()["style"] == "primary"
    assert kb.url_btn("🔗 فتح القناة", "https://t.me/test").to_dict()["style"] == "primary"
    assert kb.copy_btn("📋 نسخ العنوان", "T123").to_dict()["style"] == "primary"


def test_zero_raw_unstyled_inline_keyboard_buttons_outside_keyboards_module():
    offenders = []
    for path in sorted(glob.glob("fysb/**/*.py", recursive=True)):
        if path == "fysb/ui/keyboards.py":
            continue
        with open(path, encoding="utf-8") as fh:
            for lineno, line in enumerate(fh, 1):
                if "InlineKeyboardButton(" in line:
                    offenders.append(f"{path}:{lineno}")
    assert not offenders, f"Found raw InlineKeyboardButton bypassing kb.btn(): {offenders}"
