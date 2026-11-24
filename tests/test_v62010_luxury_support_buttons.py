"""v6.20.10 regression tests — Luxury 1-tap colored support contact buttons."""
from __future__ import annotations

import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock

from fysb.handlers import user_pages
from fysb.services import support as support_service


def test_support_page_renders_contacts_as_luxury_buttons_not_plain_text():
    support_service.set_config(
        username="@USDTtadawl",
        phone="+967773178684",
        channel="@ForexYemeniCh",
        group_chat="https://t.me/ForexYemeniGrp",
    )

    replies = []

    async def fake_reply(text=None, reply_markup=None, **kw):
        replies.append((text, reply_markup))
        return SimpleNamespace(chat=SimpleNamespace(id=5050), message_id=12)

    q = SimpleNamespace(
        message=SimpleNamespace(
            chat_id=5050,
            message_id=11,
            reply_text=AsyncMock(side_effect=fake_reply),
            edit_text=AsyncMock(side_effect=fake_reply),
        ),
        from_user=SimpleNamespace(id=5050, full_name="Trader"),
        answer=AsyncMock(),
    )
    bot = SimpleNamespace(send_message=AsyncMock())
    ctx = SimpleNamespace(user_data={}, bot=bot)

    asyncio.run(user_pages._handle_support_page(q, 5050, ctx, bot))

    assert replies
    text, markup = replies[-1]
    assert "مركز الدعم الفني والتواصل المباشر" in text
    assert "<blockquote>" in text
    # Raw contact handles/numbers must NOT be dumped as plain message lines
    assert "@USDTtadawl" not in text
    assert "+967773178684" not in text

    btns = [b for row in markup.inline_keyboard for b in row]
    urls = {b.url for b in btns if getattr(b, "url", None)}
    assert "https://t.me/USDTtadawl" in urls
    assert "https://wa.me/967773178684" in urls
    assert "https://t.me/ForexYemeniCh" in urls
    assert "https://t.me/ForexYemeniGrp" in urls

    copy_vals = [
        b.copy_text.text for b in btns if getattr(b, "copy_text", None) is not None
    ]
    assert "+967773178684" in copy_vals

    # Every button on the support page is colored in Bot API 9.4 native style
    assert all(b.to_dict().get("style") in ("primary", "success", "danger") for b in btns)


def test_support_contact_rows_safe_fallback_for_non_url_values():
    rows = user_pages._support_contact_rows({
        "username": "bad handle with spaces",
        "phone": "ext-12",
        "channel": "قناة غير رابط",
        "group_chat": "",
    })
    btns = [b for row in rows for b in row]
    assert len(btns) == 3
    # No invalid URL passed to Telegram
    assert all(getattr(b, "url", None) is None for b in btns)
    assert all(b.to_dict().get("style") in ("primary", "success", "danger") for b in btns)
