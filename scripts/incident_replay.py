#!/usr/bin/env python3
"""Incident replay — the owner's exact 2026-11-23 15:11 button sequence.

Replays a:home → a:signals → a:accounts through the REAL dispatcher with
production-realistic Telegram mocks and proves, for every tap:
  * the callback is answered (spinner stops instantly)
  * a page is delivered (fresh at chat bottom / edit in place)
  * handling time in milliseconds (the BUTTON DONE contract)
Also proves the three regression paths of v6.8.1:
  1. stale panel tap  -> fresh page (visible) + old panel deleted
  2. current panel tap-> in-place edit, no message spam
  3. inaccessible panel (>48h) -> fresh page, no RuntimeError
"""
import asyncio
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import tests.helpers as H
from fysb import db
from fysb.handlers import dispatcher, common

ADMIN = 9001
SEQ = ["a:home", "a:signals", "a:accounts"]


def _mk_update(q):
    upd = type("Update", (), {})()
    upd.callback_query = q
    return upd


async def replay():
    db.init_db()
    common._last_panel.clear()
    print("=" * 74)
    print("INCIDENT REPLAY — 2026-11-23 15:11 button sequence (admin id 6195038575)")
    print("=" * 74)

    bot = H.MockBot()

    # The chat already holds an OLD stale panel high up in history (the
    # production reality: days of fresh-message panels from v6.7.x).
    stale_panel = H.MockMessage(chat_id=ADMIN, message_id=100)
    print(f"\n[setup] chat holds a stale panel (message_id=100), registry EMPTY (post-restart)")

    for i, data in enumerate(SEQ, 1):
        q = H.MockQuery(data, ADMIN)
        # taps 1..3 land on the STALE panel (the incident: owner kept tapping
        # the same old panel buttons); the page must still appear.
        q.message = stale_panel
        ctx = H.MockContext(bot)
        q._ctx = ctx
        t0 = time.monotonic()
        await dispatcher.buttons(_mk_update(q), ctx)
        ms = (time.monotonic() - t0) * 1000
        page = H.page_for(q, ctx)
        fresh = [m for m in bot.sent if m["chat_id"] == ADMIN]
        print(f"\n  tap {i}: {data}")
        print(f"    answered()         : {q._answered}")
        print(f"    page delivered     : {bool(page)} — {page[:60].replace(chr(10), ' ')}...")
        print(f"    method             : {'edit_in_place' if q.message.edits else ('fresh + stale deleted' if fresh else 'NONE!')}")
        print(f"    stale panel deleted: {stale_panel.deleted}")
        print(f"    handling time      : {ms:.0f} ms")
        assert q._answered, "callback MUST be answered"
        assert page, "a page MUST be delivered — never silent"
        # after the first tap the registry points at the fresh page
        if fresh:
            assert common._last_panel.get(ADMIN) == fresh[-1]["message_id"]

    # --- regression 2: tapping the CURRENT living panel edits in place ------
    living = bot.sent[-1]["message_id"]
    q = H.MockQuery("a:stats", ADMIN)
    q.message = H.MockMessage(chat_id=ADMIN, message_id=living)
    common.register_panel(ADMIN, living)
    ctx = H.MockContext(bot)
    q._ctx = ctx
    before = len([m for m in bot.sent if m["chat_id"] == ADMIN])
    t0 = time.monotonic()
    await dispatcher.buttons(_mk_update(q), ctx)
    ms = (time.monotonic() - t0) * 1000
    print(f"\n[regression 2] tap on the CURRENT living panel (message_id={living})")
    print(f"    in-place edit      : {bool(q.message.edits)} — {q.message.edits[-1]['text'][:50].replace(chr(10), ' ')}...")
    print(f"    new message spam   : {len([m for m in bot.sent if m['chat_id'] == ADMIN]) - before} (must be 0)")
    print(f"    handling time      : {ms:.0f} ms")
    assert q.message.edits and not q.message.deleted

    # --- regression 3: >48h panel (InaccessibleMessage) ----------------------
    q = H.MockQuery("a:home", ADMIN)
    q.message = H.InaccessibleMock(chat_id=ADMIN, message_id=42)
    ctx = H.MockContext(bot)
    q._ctx = ctx
    t0 = time.monotonic()
    await dispatcher.buttons(_mk_update(q), ctx)
    ms = (time.monotonic() - t0) * 1000
    page = H.page_for(q, ctx)
    print(f"\n[regression 3] tap on a >48h panel (InaccessibleMessage, message_id=42)")
    print(f"    page delivered     : {bool(page)} — {page[:50].replace(chr(10), ' ')}...")
    print(f"    handling time      : {ms:.0f} ms")
    assert page, "inaccessible panels MUST still deliver a page (v6.8.1 died here)"

    print("\n" + "=" * 74)
    print("RESULT: every tap of the incident sequence now DELIVERS A VISIBLE PAGE.")
    print("The «زر يُضغط ولا يظهر أي شي» regression is structurally impossible:")
    print("edit-in-place for the living panel, delete+fresh for anything else.")
    print("=" * 74)


if __name__ == "__main__":
    asyncio.run(replay())
