"""Phase 7 UI/UX & Telegram Bot API 9.4 Tests (v6.13.0):
  * Native InlineKeyboardButton.style ('primary'|'success'|'danger')
  * Native CopyTextButton on wallet addresses, local payment accounts & referral links
  * Clean categorized Admin Command Center (4 rows + 5 focused sub-hubs, zero dead callbacks)
  * Clean uncluttered User Home menu (6 rows, no double-emoji dot clutter)
  * Smart in-place edit_text navigation with BadRequest fallback + live Settings toggle
"""
import asyncio
from telegram.error import BadRequest

import pytest

from fysb import config, db
from fysb.handlers import common, dispatcher
from fysb.services import payments as payments_service
from fysb.services import referrals as ref_service
from fysb.services import users as users_service
from fysb.ui import keyboards as kb
from tests.helpers import FakeUser, MockBot, MockContext, MockMessage, MockQuery, page_for, page_markup_for

ADMIN = 9001
USER = 555707


@pytest.fixture
def ui_env(tmp_path, monkeypatch):
    db_file = tmp_path / "phase7_ui.db"
    monkeypatch.setattr(config, "DB_FILE", db_file)
    monkeypatch.setattr(config, "USE_PG", False)
    monkeypatch.setattr(config, "ADMIN_ID", ADMIN)
    db._settings_cache.clear()
    db.init_db(snapshot_before_structural=False)
    users_service.ensure_user(FakeUser(USER))
    yield
    db._settings_cache.clear()


def _fire(data: str, uid: int, msg=None):
    q = MockQuery(data, uid)
    if msg is not None:
        q.message = msg
    ctx = MockContext(MockBot())
    upd = type("Update", (), {})()
    upd.callback_query = q
    asyncio.run(dispatcher.buttons(upd, ctx))
    return q, ctx


def test_bot_api_94_native_button_styles_and_copy_helper():
    b_p = kb.btn("Primary", "u:home", style="primary")
    b_s = kb.btn("Success", "u:signals", style="success")
    b_d = kb.btn("Danger", "a:killswitch", style="danger")
    b_c = kb.copy_btn("Copy Wallet", "TABC123", style="primary")
    b_u = kb.url_btn("Share", "https://t.me/share/url?url=x", style="success")

    assert b_p.to_dict()["style"] == "primary"
    assert b_s.to_dict()["style"] == "success"
    assert b_d.to_dict()["style"] == "danger"
    assert b_c.to_dict()["copy_text"] == {"text": "TABC123"}
    assert b_c.to_dict()["style"] == "primary"
    assert b_u.to_dict()["style"] == "success"


def test_user_menu_clean_layout_and_native_styles():
    markup = kb.user_menu()
    rows = markup.inline_keyboard
    assert len(rows) <= 6, "user home must stay compact (<= 6 rows)"
    buttons = [b for row in rows for b in row]
    styles = {b.to_dict().get("style") for b in buttons}
    assert "primary" in styles and "success" in styles
    for b in buttons:
        # No ugly double-icon emoji dot prefixes like '🟢 📊 ...'
        assert not b.text.startswith(("🟢 ", "🟡 ", "🔵 ", "🟣 ", "🔴 ", "🟠 "))


def test_admin_command_center_compact_hubs_and_all_routes_alive(ui_env):
    home_markup = kb.admin_menu()
    assert len(home_markup.inline_keyboard) == 4, "admin home must be compact (4 clean rows, not 16)"

    # Every hub button on admin_menu routes to a real hub page
    for hub_key in ("trading", "finance", "comms", "execution", "system"):
        q, ctx = _fire(f"a:hub:{hub_key}", ADMIN)
        text = page_for(q, ctx)
        assert text and "غير معروف" not in text
        hub_markup = page_markup_for(q, ctx)
        assert hub_markup is not None
        # Every button inside every hub must route to a real section (no dead callbacks!)
        for row in hub_markup.inline_keyboard:
            for b in row:
                if not b.callback_data:
                    continue
                q2, ctx2 = _fire(b.callback_data, ADMIN)
                t2 = page_for(q2, ctx2)
                assert t2, f"hub {hub_key} button {b.callback_data} rendered nothing"
                assert "غير معروف" not in t2, f"dead button in hub {hub_key}: {b.callback_data}"
                assert "غير متاح" not in t2, f"unreachable button in hub {hub_key}: {b.callback_data}"


def test_copy_text_buttons_on_usdt_local_payment_and_referrals(ui_env):
    # 1. USDT on-chain payment screen carries native CopyTextButton for wallet
    wallet = "T" + "7" * 33
    payments_service.set_chain_wallet("TRC20", wallet)
    q, ctx = _fire("u:usdtpay:PRO:TRC20", USER)
    mk = page_markup_for(q, ctx)
    copy_vals = [b.copy_text.text for row in mk.inline_keyboard for b in row if b.copy_text]
    assert wallet in copy_vals

    # 2. Local payment method screen carries native CopyTextButton for account_ref
    mid = payments_service.add_method("الكريمي", "حول للرقم", "777123456", "اليمن", "PRO")
    q2, ctx2 = _fire(f"u:requestplan:PRO:{mid}", USER)
    mk2 = page_markup_for(q2, ctx2)
    copy_vals2 = [b.copy_text.text for row in mk2.inline_keyboard for b in row if b.copy_text]
    assert "777123456" in copy_vals2

    # 3. User referrals screen carries native CopyTextButton for invite link
    q3, ctx3 = _fire("u:referrals", USER)
    mk3 = page_markup_for(q3, ctx3)
    copy_vals3 = [b.copy_text.text for row in mk3.inline_keyboard for b in row if b.copy_text]
    assert any("t.me/" in v and "start=ref" in v for v in copy_vals3)

    # 4. Admin referral view carries native CopyTextButton for campaign link
    camp = ref_service.ensure_campaign("ROYAL", "الحملة الملكية")
    q4, ctx4 = _fire(f"a:refview:{camp['id']}", ADMIN)
    mk4 = page_markup_for(q4, ctx4)
    copy_vals4 = [b.copy_text.text for row in mk4.inline_keyboard for b in row if b.copy_text]
    assert any("start=refROYAL" in v for v in copy_vals4)


def test_signal_buttons_colored_buy_green_sell_red():
    mk = kb.user_menu_signal_buttons([
        {"id": 1, "symbol": "XAUUSD", "direction": "BUY", "score": 88},
        {"id": 2, "symbol": "EURUSD", "direction": "SELL", "score": 84},
    ])
    assert mk.inline_keyboard[0][0].to_dict()["style"] == "success"
    assert mk.inline_keyboard[1][0].to_dict()["style"] == "danger"


def test_smart_render_fresh_by_default_and_badrequest_fallback(ui_env):
    # 1. Default mode is "0" (proven fresh reply_text page under every tap — never hidden!)
    assert db.get_setting("ui_edit_in_place", "0") == "0"
    q, ctx = _fire("a:settings", ADMIN)
    assert q.message.replies and not q.message.edits
    # v8.14.16 — the In-Place Edit toggle is EXECUTED: no line, no button, no route.
    page_txt = page_for(q, ctx)
    assert "وضع التنقل" not in page_txt
    assert "التعديل المكاني" not in page_txt
    mk = page_markup_for(q, ctx)
    assert not any(
        getattr(b, "callback_data", None) == "a:toggleuimode"
        for row in mk.inline_keyboard for b in row
    )

    # 2. If Telegram ever rejects styled buttons with BadRequest, _send_guarded
    # automatically strips styles and retries cleanly so NO button can ever die!
    class StyleRejectMsg(MockMessage):
        async def reply_text(self, text, reply_markup=None, parse_mode=None, **kw):
            if reply_markup and any(
                b.to_dict().get("style") for row in reply_markup.inline_keyboard for b in row
            ):
                raise BadRequest("Unsupported button style")
            return await super().reply_text(text, reply_markup=reply_markup, parse_mode=parse_mode, **kw)

    sr_msg = StyleRejectMsg()
    q_sr, ctx_sr = _fire("a:home", ADMIN, msg=sr_msg)
    assert sr_msg.replies, "style rejection must automatically fall back to plain buttons"
    assert "SUPER ADMIN" in sr_msg.replies[-1]["text"]

    # 3. v8.14.16 — fresh delivery is THE ONLY mode: even a legacy DB still
    # carrying ui_edit_in_place='1' from the deleted toggle NEVER edits again.
    db.set_setting("ui_edit_in_place", "1")
    try:
        q_f, ctx_f = _fire("a:home", ADMIN)
        assert q_f.message.replies and not q_f.message.edits
        assert "SUPER ADMIN" in page_for(q_f, ctx_f)
    finally:
        db.set_setting("ui_edit_in_place", "0")

    # 4. v8.14.16 — the toggle route is dead: a ghost press hits the
    # unknown-action reply contract and the setting stays untouched.
    q_g, ctx_g = _fire("a:toggleuimode", ADMIN)
    assert q_g.message.replies, "ghost press must still get a page — never die silently"
    assert db.get_setting("ui_edit_in_place") == "0"
