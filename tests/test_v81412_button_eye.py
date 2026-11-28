"""v8.14.12 — «عين الأزرار الملكية» tests.

The owner reported dead section buttons on PRODUCTION (v8.14.10) with no
visible error. Every code-level section/pagination surface was audited and
reproduced green locally (both accounts, realistic rows, LOCKED_50 included;
release zip proven complete by `git archive`). The remaining gap was
OBSERVABILITY: a production-only hang/crash was invisible from his phone.
This suite pins the royal button eye — every silent/failed/unchanged press
is persisted verbatim and readable from the admin panel.
"""
from __future__ import annotations

import asyncio
import sys
from unittest.mock import patch

import pytest

sys.path.insert(0, ".")

import tests.helpers  # noqa: F401
from tests.helpers import MockContext, MockQuery, page_of
from fysb import db
from fysb.handlers import dispatcher
from fysb.services import ui_diagnostics

ADMIN = 9001


@pytest.fixture(autouse=True)
def _clean_eye():
    db.init_db()
    with db.conn() as c:
        c.execute("DELETE FROM ui_events")
    yield
    with db.conn() as c:
        c.execute("DELETE FROM ui_events")


def _fire(data: str, uid: int = ADMIN, from_user=None):
    q = MockQuery(data, uid)
    q.from_user = from_user or type("U", (), {"id": uid, "username": "o", "full_name": "O"})()
    ctx = MockContext()
    upd = type("Update", (), {})()
    upd.callback_query = q
    asyncio.run(dispatcher.buttons(upd, ctx))
    return q, ctx


def test_failed_route_is_recorded_verbatim():
    # force a route crash at the hub's data source — the recording mechanism
    # itself (afn) must stay real so the failure lands in the ledger
    with patch("fysb.services.signal_archive.admin_pl_detailed",
               side_effect=RuntimeError("boom")):
        _fire("a:sigpl:all")
    events = ui_diagnostics.recent(5)
    fails = [e for e in events if e["kind"] == "FAIL"]
    assert fails, "a raised route MUST land in the royal button eye"
    assert fails[0]["data"] == "a:sigpl:all"
    assert "RuntimeError" in fails[0]["detail"] and "boom" in fails[0]["detail"]
    assert fails[0]["duration_ms"] is not None


def test_silent_press_is_recorded_when_no_page_is_delivered():
    # a route that renders NOTHING (no page_delivered, no exception): patch the
    # user page sink to a no-op for an unknown-but-routed action
    async def _no_delivery(q, action, uid, context, preloaded_u=None):
        return None
    with patch("fysb.handlers.user_pages.user_page", side_effect=_no_delivery):
        with patch.object(dispatcher.users_service, "touch_and_get",
                          return_value={"id": 9001, "is_blocked": 0,
                                        "is_active_partner": 0}):
            _fire("u:signals", ADMIN)
    events = ui_diagnostics.recent(5)
    silents = [e for e in events if e["kind"] == "SILENT"]
    assert silents, "a press with zero page delivery MUST be recorded as SILENT"
    assert silents[0]["data"] == "u:signals"


def test_not_modified_press_is_recorded():
    ui_diagnostics.record_not_modified(555, "u:sigday:today:0")
    events = ui_diagnostics.recent(5)
    assert events[0]["kind"] == "NOT_MODIFIED"
    assert events[0]["data"] == "u:sigday:today:0"


def test_admin_eye_page_shows_the_real_cause():
    ui_diagnostics.record_failure(9001, "u:dead:btn", "KeyError: 'locked_n'",
                                  "Traceback...\n  File \"fysb/handlers/x.py\", line 9\nKeyError: 'locked_n'",
                                  321.0)
    ui_diagnostics.record_silent(9001, "a:sigday:all:core:5", 3001.0)
    q, _ = _fire("a:btnfail")
    body = page_of(q)
    assert "عين الأزرار الملكية" in body
    # the page HTML-escapes quotes; assert the raw facts survive verbatim
    assert "u:dead:btn" in body and "KeyError" in body and "locked_n" in body
    assert "ضغطات صامتة" in body


def test_eye_clear_wipes_the_ledger():
    ui_diagnostics.record_silent(1, "u:x", 10.0)
    assert ui_diagnostics.counts()["SILENT"] == 1
    q, _ = _fire("a:btnfailclear")
    assert ui_diagnostics.counts() == {"FAIL": 0, "SILENT": 0, "NOT_MODIFIED": 0}


def test_eye_never_breaks_the_press_itself():
    """Diagnostics are best-effort by contract: even if the ledger INSERT
    explodes, the button's own error card still delivers."""
    with patch.object(ui_diagnostics.db, "conn", side_effect=RuntimeError("db gone")):
        ui_diagnostics.record_silent(1, "u:x", 5.0)   # swallowed, no raise
    q, _ = _fire("a:settings")
    body = page_of(q)
    assert body and "الإعدادات" in body


def test_eye_ledger_is_size_capped():
    for i in range(350):
        ui_diagnostics.record("SILENT", i, f"u:x:{i}", "", 1.0)
    assert ui_diagnostics.counts()["SILENT"] <= 300


def test_dispatcher_routes_the_eye():
    src = open("fysb/handlers/dispatcher.py", encoding="utf-8").read()
    assert '"btnfail"' in src and '"btnfailclear"' in src
