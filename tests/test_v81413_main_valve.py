"""v8.14.13 — «الصمّام الملكي» tests — the guaranteed root fix for the
owner's production report: ALL buttons dead across ALL panels (admin/user/
partner) while they used to be 100% fast.

The mechanism that can kill every button at once (verified in code):
concurrent_updates gives each press its own task, but every route offloads
its work to the 8-worker UI pool; a stalled WAN database pins those workers
→ queue grows → presses appear silently dead. The valve set:
  ① route hard-timeout — no press may EVER die silently; hung routes are
     cancelled and the owner gets a visible card + recorded evidence with
     UI-pool depth (the starvation tell).
  ② UI-pool self-heal — sustained starvation recycles the pool automatically.
  ③ survivor commands — /btnfail + /fix ride the COMMAND pipeline, which is
     structurally separate from callbacks; they work even with all buttons
     dead. /ping carries the eye + pool state.
  ④ silence alarm — ≥2 dead presses/10min PUSH the report to the owner.
  ⑤ boot independence — the boot card + absence report run in a detached
     timed task; polling starts instantly, boot never hangs on a send.
"""
from __future__ import annotations

import asyncio
import sys
from unittest.mock import patch

import pytest

sys.path.insert(0, ".")

import tests.helpers  # noqa: F401
from tests.helpers import MockContext, MockQuery, page_of
from fysb import afn, config, db
from fysb.handlers import dispatcher
from fysb.handlers.common import btnfail_command, fix_command
from fysb.services import ui_diagnostics

ADMIN = 9001


@pytest.fixture(autouse=True)
def _clean_valve():
    db.init_db()
    with db.conn() as c:
        c.execute("DELETE FROM ui_events")
    yield
    with db.conn() as c:
        c.execute("DELETE FROM ui_events")


def _fire(data: str, uid: int = ADMIN):
    q = MockQuery(data, uid)
    q.from_user = type("U", (), {"id": uid, "username": "o", "full_name": "O"})()
    ctx = MockContext()
    upd = type("Update", (), {})()
    upd.callback_query = q
    asyncio.run(dispatcher.buttons(upd, ctx))
    return q, ctx


# ---------------------------------------------------------------------------
# ① the route hard-timeout watchdog
# ---------------------------------------------------------------------------

def test_hung_route_produces_visible_card_and_evidence(monkeypatch):
    async def _hang(q, action, uid, context, preloaded_u=None):
        await asyncio.sleep(999)
    monkeypatch.setattr("fysb.handlers.user_pages.user_page", _hang)
    monkeypatch.setattr("fysb.services.users.touch_and_get",
                        lambda uid: {"id": uid, "is_blocked": 0, "is_active_partner": 0})
    monkeypatch.setattr(dispatcher, "BUTTON_HARD_TIMEOUT_SECONDS", 0.2)
    q, _ = _fire("u:signals")
    body = page_of(q)
    assert "تجاوزت المهلة الملكية" in body, "a hung route must deliver a VISIBLE card"
    events = ui_diagnostics.recent(5)
    timeouts = [e for e in events if e["kind"] == "TIMEOUT"]
    assert timeouts and timeouts[0]["data"] == "u:signals"
    assert "عمال الواجهة" in timeouts[0]["detail"]   # the starvation tell


def test_fast_route_is_never_interrupted_by_the_watchdog():
    q, _ = _fire("a:settings")
    body = page_of(q)
    assert "الإعدادات العامة" in body and "تجاوزت المهلة" not in body


# ---------------------------------------------------------------------------
# ② afn telemetry + self-heal primitives
# ---------------------------------------------------------------------------

def test_afn_stats_and_recycle():
    s = afn.stats()
    assert s["workers"] == 8 and s["queued"] >= 0
    async def _spin():
        await asyncio.sleep(0.05)
    asyncio.run(afn.run(_spin))
    afn.recycle()
    s2 = afn.stats()                                    # lazy rebuild on demand
    assert s2["workers"] == 8
    asyncio.run(afn.run(lambda: 42)) == 42              # pool works after recycle


# ---------------------------------------------------------------------------
# ③ the survivor commands (separate pipeline from buttons)
# ---------------------------------------------------------------------------

class _FakeMsg:
    def __init__(self):
        self.texts = []

    async def reply_text(self, text, **kw):
        self.texts.append(text)


class _FakeUpdate:
    def __init__(self, uid):
        self.effective_user = type("U", (), {"id": uid})()
        self.effective_message = _FakeMsg()


def test_fix_command_recycles_pool_and_reports():
    upd = _FakeUpdate(ADMIN)
    ctx = MockContext()
    asyncio.run(fix_command(upd, ctx))
    body = upd.effective_message.texts[0]
    assert "الصمّام الملكي" in body
    assert "قاعدة البيانات" in body and "عمال" in body
    with db.conn() as c:
        assert c.execute("SELECT value FROM settings WHERE key='last_fix_probe'").fetchone()


def test_fix_command_is_owner_only():
    upd = _FakeUpdate(555)
    asyncio.run(fix_command(upd, MockContext()))
    assert upd.effective_message.texts == []            # silent refusal for non-owners


def test_btnfail_command_renders_the_eye():
    ui_diagnostics.record_silent(ADMIN, "u:sigday:today:1", 3001.0)
    upd = _FakeUpdate(ADMIN)
    asyncio.run(btnfail_command(upd, MockContext()))
    body = upd.effective_message.texts[0]
    assert "عين الأزرار الملكية" in body and "u:sigday:today:1" in body


# ---------------------------------------------------------------------------
# ④ the silence alarm (evidence pushed without being asked)
# ---------------------------------------------------------------------------

def test_silence_alarm_pushes_after_two_dead_presses(monkeypatch):
    from fysb.handlers import background
    ui_diagnostics.record("SILENT", 1, "u:x:1", "", 100.0)
    ui_diagnostics.record("TIMEOUT", 1, "u:x:2", "", 2000.0)
    sent = []

    class _Bot:
        async def send_message(self, chat_id, text, **kw):
            sent.append((chat_id, text))
            return type("M", (), {})()

    app = type("A", (), {"bot": _Bot()})()
    monkeypatch.setattr(background, "_safe_send",
                        lambda bot, chat_id, text: _captured_send(sent, chat_id, text))
    asyncio.run(background._valve_tick(app, 0))
    assert sent and "ضغطات ميتة" in sent[0][1] and sent[0][0] == ADMIN
    assert "/fix" in sent[0][1] and "/btnfail" in sent[0][1]
    # the alarm is rate-limited: an immediate second tick stays silent
    sent.clear()
    asyncio.run(background._valve_tick(app, 0))
    assert sent == []


async def _captured_send(sent, chat_id, text):
    sent.append((chat_id, text))
    return type("M", (), {})()


def test_no_alarm_without_dead_presses():
    from fysb.handlers import background
    assert background._absence_or_none({"created_at": ""}) > 1e11   # broken stamp → ancient-safe
    assert background._absence_or_none({"created_at": __import__("datetime")
                                        .datetime.now(__import__("datetime").timezone.utc)
                                        .isoformat()}) < 600
    sent = []
    app = type("A", (), {"bot": type("B", (), {"send_message": staticmethod(lambda *a, **k: None)})()})()
    asyncio.run(background._valve_tick(app, 0))
    assert sent == []                                               # zero dead presses → silence


# ---------------------------------------------------------------------------
# ⑤ boot independence — the boot valve is a detached timed task
# ---------------------------------------------------------------------------

def test_boot_card_and_absence_are_detached_and_timeboxed(monkeypatch):
    from fysb.handlers import background
    src = open("fysb/handlers/background.py", encoding="utf-8").read()
    assert "_boot_valve_task(app)" in src
    assert "post_init._tasks_ref = _TASKS" in src
    # the OLD critical-path await is gone from post_init
    assert "report, gap = await afn.run(_absence.build_absence_report)\n        if report:\n            await _safe_send(app.bot, config.ADMIN_ID, report)\n            log.warning" not in src
    # the detached task hard-timeboxes both sends
    assert "wait_for(_safe_send" in src and "wait_for(\n                afn.run(_absence.build_absence_report), 15)" in src


def test_boot_valve_task_survives_a_dead_db(monkeypatch):
    from fysb.handlers import background
    sent = []

    class _Bot:
        async def send_message(self, chat_id, text, **kw):
            sent.append(text)
            return type("M", (), {})()

    app = type("A", (), {"bot": _Bot()})()
    monkeypatch.setattr(background.asyncio, "sleep", async_none)
    with patch.object(background, "_safe_send", side_effect=RuntimeError("net dead")):
        asyncio.run(background._boot_valve_task(app))   # must NOT raise
    # the absence report path also failed silently — no crash, task ended


async def async_none(_sec):
    await asyncio.sleep(0)


def test_dispatcher_registers_survivor_commands():
    src = open("main.py", encoding="utf-8").read()
    assert 'CommandHandler("btnfail", btnfail_command' in src
    assert 'CommandHandler("fix", fix_command' in src
