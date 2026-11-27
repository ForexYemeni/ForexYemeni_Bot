"""v6.20.9 regression tests — Ultra-luxury Royal Institutional No-Signal / Protection Card."""
from __future__ import annotations

import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock

from fysb import config
from fysb.ui.cards import diagnose_no_signal, render_no_signal_card


def test_diagnose_no_signal_covers_all_institutional_shields():
    ic, title, *_ = diagnose_no_signal("REJECTED", "معاكس لاتجاه الفريم الأعلى (HTF=DOWN, قوة 1.54 ATR)")
    assert ic == "🛡" and "الفريم الأعلى" not in title or "الاتجاه المؤسسي" in title

    ic2, title2, *_ = diagnose_no_signal("REJECTED", "سوق متذبذب عرضي (ER=0.14)")
    assert ic2 == "🌊" and "تذبذب" in title2

    ic3, title3, *_ = diagnose_no_signal("DUPLICATE", "توجد إشارة مفتوحة")
    assert ic3 == "🔁"

    ic4, title4, *_ = diagnose_no_signal("REJECTED", "جودة الإشارة أقل من الحد الأدنى 72")
    assert ic4 == "⚖️"

    ic5, title5, *_ = diagnose_no_signal("ERROR", "السوق مغلق حالياً")
    assert ic5 == "🌙"

    ic6, title6, *_ = diagnose_no_signal("NO_SETUP", "")
    assert ic6 == "👑"


def test_render_no_signal_card_is_luxurious_and_never_uses_old_plain_fallback():
    html = render_no_signal_card(
        symbol="XAUUSD",
        timeframe="M15",
        strategy_label="جميع المنهجيات (18)",
        style_label="مضاربة يومية",
        status="REJECTED",
        reason="معاكس لاتجاه الفريم الأعلى (HTF=DOWN, قوة 1.54 ATR)",
        show_watch_hint=True,
    )
    assert "تقرير الفحص المؤسسي — حماية رأس المال" in html
    assert "درع حماية الاتجاه المؤسسي" in html
    assert "XAUUSD" in html
    assert "معاكس لاتجاه الفريم الأعلى (HTF=DOWN, قوة 1.54 ATR)" in html
    assert "<blockquote>" in html and "</blockquote>" in html
    assert "سوق مغلق / بيانات غير متاحة / حد وقف محرك المخاطر" not in html


def test_signal_now_rejected_renders_luxury_card_with_colored_watch_buttons(monkeypatch):
    from fysb.handlers import signal_now

    monkeypatch.setattr(
        signal_now,
        "generate_signal",
        lambda *a, **kw: {
            "status": "REJECTED",
            "reason": "معاكس لاتجاه الفريم الأعلى (HTF=DOWN, قوة 1.54 ATR)",
        },
    )

    replies = []

    async def fake_reply(text=None, reply_markup=None, **kw):
        replies.append((text, reply_markup))
        return SimpleNamespace(chat=SimpleNamespace(id=config.ADMIN_ID), message_id=101)

    q = SimpleNamespace(
        message=SimpleNamespace(
            chat_id=config.ADMIN_ID, message_id=99,
            reply_text=AsyncMock(side_effect=fake_reply),
            edit_text=AsyncMock(side_effect=fake_reply),
        ),
        from_user=SimpleNamespace(id=config.ADMIN_ID, full_name="Admin"),
        answer=AsyncMock(),
    )
    bot = SimpleNamespace(send_message=AsyncMock())
    ctx = SimpleNamespace(
        user_data={
            signal_now.DRAFT_KEY: {
                "stage": "strategy",
                "style": "INTRADAY",
                "symbol": "XAUUSD",
                "tf": "M15",
                "is_admin": True,
            }
        },
        bot=bot,
    )

    asyncio.run(signal_now.pick_strategy(q, config.ADMIN_ID, ctx, bot, "ALL"))

    assert replies, "expected luxury rejection card to be rendered"
    text, markup = replies[-1]
    assert "تقرير الفحص المؤسسي — حماية رأس المال" in text
    assert "درع حماية الاتجاه المؤسسي" in text
    assert "HTF=DOWN" in text
    assert "حد وقف محرك المخاطر" not in text
    # All buttons on the card are colored AND include Auto-Watch options!
    btns = [b for row in markup.inline_keyboard for b in row]
    datas = [b.callback_data for b in btns]
    assert "a:signowwatch:15" in datas
    assert "a:signowwatch:30" in datas
    assert "a:signowwatch:60" in datas
    assert all(b.to_dict().get("style") in ("primary", "success", "danger") for b in btns)
