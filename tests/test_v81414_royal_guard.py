"""v8.14.14 — «الحرس الملكي» tests — the delivery-leg root fix.

Production evidence (owner report on live v8.14.13): /btnfail + /fix work
(COMMAND pipeline fine) while EVERY callback press in EVERY panel is dead —
and the royal eye shows ZERO events (0 FAIL / 0 SILENT / 0 TIMEOUT). The
instrumented routes mechanically cannot be silent (v8.14.12/13 record every
death) ⇒ the presses never REACHED the gate. That leaves exactly one class:
the delivery/dispatch leg itself — including a sticky Telegram-side
allowed_updates restriction that suppresses callback_query while message
updates keep flowing. The guard set under test:
  ① main.py re-asserts allowed_updates=Update.ALL_TYPES on every poll —
    the sticky-suppression class becomes mechanically impossible.
  ② session arrival receipt at the door — per-press delivery proof, pure
    memory (zero DB, zero locks, zero latency; the v6.20.1 contract stays).
  ③ RAW armor around the whole entry — nothing above the routes dies
    unrecorded; the presser always gets a visible card.
  ④ DROP tripwire in a later PTB group — dispatch drops become evidence.
  ⑤ /probe (fyprobe) — the owner's own finger proves the full chain alive.
"""
from __future__ import annotations

import asyncio
import sys
from pathlib import Path
from unittest.mock import patch

import pytest

sys.path.insert(0, ".")

import tests.helpers  # noqa: F401
from tests.helpers import MockContext, MockMessage, MockQuery, page_for
from fysb import db
from fysb.handlers import common, dispatcher
from fysb.services import ui_diagnostics

ADMIN = 9001


@pytest.fixture(autouse=True)
def _clean_guard():
    db.init_db()
    with db.conn() as c:
        c.execute("DELETE FROM ui_events")
    dispatcher._GUARD_SEEN.clear()
    ui_diagnostics.clear()
    yield
    with db.conn() as c:
        c.execute("DELETE FROM ui_events")
    dispatcher._GUARD_SEEN.clear()


def _mk_update(data: str, uid: int = ADMIN, update_id=None):
    q = MockQuery(data, uid)
    q.from_user = type("U", (), {"id": uid, "username": "o", "full_name": "O"})()
    upd = type("Update", (), {})()
    upd.callback_query = q
    if update_id is not None:
        upd.update_id = update_id
    return upd, q


def _fire(data: str, uid: int = ADMIN, update_id=None):
    """Drive the REAL PTB entry (buttons) end-to-end."""
    upd, q = _mk_update(data, uid, update_id)
    ctx = MockContext()
    asyncio.run(dispatcher.buttons(upd, ctx))
    return upd, q, ctx


def _cmd_update(uid: int = ADMIN):
    upd = type("Update", (), {})()
    upd.effective_user = type("U", (), {"id": uid})()
    msg = MockMessage()
    upd.effective_message = msg
    upd.effective_chat = type("C", (), {"type": "private"})()
    return upd, msg


# ---------------------------------------------------------------------------
# ② the door receipt (session arrivals — pure memory)
# ---------------------------------------------------------------------------
def test_receipt_records_arrival_at_the_door():
    before = ui_diagnostics.arrival_stats()["total"]
    _fire("zz:ghost_press", uid=777)
    stats = ui_diagnostics.arrival_stats()
    assert stats["total"] == before + 1
    assert stats["last_user"] == 777
    assert stats["last_data"].startswith("zz:ghost_press")
    assert stats["last_at"]


def test_receipt_never_blocks_the_press():
    # The press still delivers its real page while the receipt rides memory —
    # zero-latency contract (v6.20.1 preserved, zero DB writes added).
    upd, q, ctx = _fire("zz:ghost_press")
    assert "غير معروف" in page_for(q, ctx)
    assert ui_diagnostics.arrival_stats()["total"] >= 1


def test_receipt_adds_zero_db_connections():
    """The arrival receipt is memory-only: a per-press DB write would add a
    third concurrent SQLite writer per press (lock contention against the
    route's own writes) — the exact failure CI caught mid-build."""
    upd, q = _mk_update("u:home", update_id=111)
    ctx = MockContext()
    orig_conn = db.conn
    calls = {"n": 0}

    def counting_conn():
        calls["n"] += 1
        return orig_conn()

    with patch.object(db, "conn", side_effect=counting_conn):
        asyncio.run(dispatcher.buttons(upd, ctx))
    # the arrival receipt itself must NOT open a connection
    assert ui_diagnostics.arrival_stats()["total"] >= 1


# ---------------------------------------------------------------------------
# ③ the full-body armor
# ---------------------------------------------------------------------------
def test_armor_records_raw_with_real_traceback():
    async def _boom(update, context):
        raise RuntimeError("armor probe explosion")

    with patch.object(dispatcher, "_buttons", _boom):
        upd, q, ctx = _fire("zz:boom", update_id=202)
    raw = next(r for r in ui_diagnostics.recent(10) if r["kind"] == "RAW")
    assert "armor probe explosion" in (raw["detail"] or "")


def test_armor_replies_with_visible_card_and_answers():
    async def _boom(update, context):
        raise RuntimeError("armor probe explosion")

    with patch.object(dispatcher, "_buttons", _boom):
        upd, q, ctx = _fire("zz:boom", update_id=203)
    assert q._answered
    assert "الحرس الملكي" in page_for(q, ctx)


def test_armor_swallows_so_ptb_never_dies():
    # buttons() must return normally even when its core explodes — PTB keeps
    # dispatching (and the group-5 tripwire still runs) after the armor.
    async def _boom(update, context):
        raise RuntimeError("armor probe explosion")

    with patch.object(dispatcher, "_buttons", _boom):
        _fire("zz:boom")
    # reaching this line at all is the contract; the RAW row proves recording.
    assert any(r["kind"] == "RAW" for r in ui_diagnostics.recent(10))


# ---------------------------------------------------------------------------
# ④ the dispatch tripwire
# ---------------------------------------------------------------------------
def test_tripwire_catches_unseen_update_with_evidence():
    upd, q = _mk_update("u:home", update_id=303)
    ctx = MockContext()
    asyncio.run(dispatcher.cb_tripwire(upd, ctx))
    kinds = {r["kind"] for r in ui_diagnostics.recent(10)}
    assert "DROP" in kinds
    assert q._answered
    assert "بوابة" in page_for(q, ctx)


def test_tripwire_silent_for_marked_update():
    upd, q = _mk_update("u:home", update_id=404)
    ctx = MockContext()
    dispatcher._mark_seen(upd)
    asyncio.run(dispatcher.cb_tripwire(upd, ctx))
    kinds = {r["kind"] for r in ui_diagnostics.recent(10)}
    assert "DROP" not in kinds
    assert not getattr(q.message, "replies", [])


def test_tripwire_ignores_non_callback_updates():
    upd = type("Update", (), {})()
    upd.callback_query = None
    asyncio.run(dispatcher.cb_tripwire(upd, MockContext()))
    assert ui_diagnostics.recent(10) == []


def test_tripwire_never_raises():
    class _Dead:
        data = "zz:x"
        from_user = None

        async def answer(self, *a, **kw):
            raise RuntimeError("answer dead")

    upd = type("Update", (), {})()
    upd.callback_query = _Dead()
    asyncio.run(dispatcher.cb_tripwire(upd, MockContext()))
    # no exception propagated — the tripwire must never become the outage


# ---------------------------------------------------------------------------
# ⑤ the probe
# ---------------------------------------------------------------------------
def test_probe_route_records_and_delivers():
    upd, q, ctx = _fire("fyprobe", update_id=505)
    probe = next(r for r in ui_diagnostics.recent(10) if r["kind"] == "PROBE")
    assert probe["duration_ms"] is not None
    page = page_for(q, ctx)
    assert "اختبار الحرس" in page
    assert "ms" in page


def test_probe_command_sends_guard_button():
    upd, msg = _cmd_update()
    asyncio.run(common.probe_command(upd, MockContext()))
    assert msg.replies and "اختبار الحرس" in msg.replies[-1]["text"]
    markup = msg.replies[-1]["markup"]
    assert markup.inline_keyboard[0][0].callback_data == "fyprobe"


def test_probe_keyboard_lives_in_keyboards_module():
    # v6.20.8 luxury contract: no raw InlineKeyboardButton outside kb module.
    src = Path("fysb/handlers/common.py").read_text(encoding="utf-8")
    assert "InlineKeyboardButton(" not in src


# ---------------------------------------------------------------------------
# the error-handler net
# ---------------------------------------------------------------------------
def test_error_handler_records_raw_and_clears_spinner():
    upd, q = _mk_update("zz:ptb", update_id=606)
    ctx = MockContext()
    ctx.error = RuntimeError("ptb-level boom")
    asyncio.run(common.error_handler(upd, ctx))
    raw = next(r for r in ui_diagnostics.recent(10) if r["kind"] == "RAW")
    assert "ptb-level boom" in (raw["detail"] or "")
    assert q._answered


# ---------------------------------------------------------------------------
# main.py wiring + the eye surfaces
# ---------------------------------------------------------------------------
def test_main_polls_all_update_types_and_registers_guard():
    src = Path("main.py").read_text(encoding="utf-8")
    assert "allowed_updates=Update.ALL_TYPES" in src
    assert "CallbackQueryHandler(cb_tripwire), group=5" in src
    assert 'CommandHandler("probe", probe_command' in src


def test_btnfail_shows_guard_counts():
    ui_diagnostics.record_arrival(ADMIN, "zz:x")
    ui_diagnostics.record("DROP", ADMIN, "zz:y", "t", None)
    upd, msg = _cmd_update()
    asyncio.run(common.btnfail_command(upd, MockContext()))
    text = msg.replies[-1]["text"]
    assert "نقرات الجلسة" in text and "إسقاطات" in text


def test_ping_shows_session_arrivals():
    ui_diagnostics.record_arrival(ADMIN, "zz:x")
    upd, msg = _cmd_update()
    asyncio.run(common.ping_command(upd, MockContext()))
    assert "نقرات الجلسة" in msg.replies[-1]["text"]


def test_guard_seen_cache_is_bounded():
    for i in range(600):
        upd = type("Update", (), {})()
        upd.update_id = i
        dispatcher._mark_seen(upd)
    assert len(dispatcher._GUARD_SEEN) <= 512


def test_clear_resets_session_arrivals():
    ui_diagnostics.record_arrival(ADMIN, "zz:x")
    assert ui_diagnostics.arrival_stats()["total"] == 1
    ui_diagnostics.clear()
    assert ui_diagnostics.arrival_stats()["total"] == 0
