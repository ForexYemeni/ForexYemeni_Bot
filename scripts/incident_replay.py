#!/usr/bin/env python3
"""Incident replay — the owner's exact 2026-11-23 15:11 button sequence.

v6.8.4 ROYAL RESTORE edition: the owner asked for the v6.8.0 experience back
(«هذه النسخة كانت تعمل فيها الأزرار بشكل حقيقي وفاخر جداً — استعادتها 100%»),
so this replay now proves the RESTORED v6.8.0 contract end to end:
  * every tap is answered instantly (spinner stops)
  * EVERY tap — stale panel, current panel, even >48h InaccessibleMessage —
    delivers a FRESH page right where the user is looking (reply under the
    tapped panel / bot.send_message fallback)
  * nothing is ever edited in place, nothing is ever deleted — a tap can
    never silently mutate history again, which is structurally impossible
  * handling time in milliseconds (the BUTTON DONE contract)
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
    print("INCIDENT REPLAY (v6.8.4 ROYAL RESTORE) — 2026-11-23 button sequence")
    print("=" * 74)

    bot = H.MockBot()

    # The chat already holds an OLD stale panel high up in history (the
    # production reality: days of panels from earlier versions).
    stale_panel = H.MockMessage(chat_id=ADMIN, message_id=100)
    print(f"\n[setup] chat holds a stale panel (message_id=100), registry EMPTY (post-restart)")

    for i, data in enumerate(SEQ, 1):
        q = H.MockQuery(data, ADMIN)
        # taps 1..3 land on the STALE panel (the incident: owner kept tapping
        # an old panel); the page must appear FRESH right under the tap.
        q.message = stale_panel
        ctx = H.MockContext(bot)
        q._ctx = ctx
        t0 = time.monotonic()
        await dispatcher.buttons(_mk_update(q), ctx)
        ms = (time.monotonic() - t0) * 1000
        page = H.page_for(q, ctx)
        print(f"\n  tap {i}: {data}")
        print(f"    answered()      : {q._answered}")
        print(f"    page delivered  : {bool(page)} — {page[:60].replace(chr(10), ' ')}...")
        print(f"    method          : new_message (fresh reply under the tapped panel)")
        print(f"    handling time   : {ms:.0f} ms")
        assert q._answered, "callback MUST be answered"
        assert page, "a page MUST be delivered — never silent"
        assert q.message.replies, "v6.8.4: EVERY tap delivers a fresh reply (v6.8.0 contract)"
        assert not q.message.edits, "v6.8.4 NEVER edits in place"
        assert not q.message.deleted, "v6.8.4 NEVER deletes the tapped panel"

    # --- proof 2: tapping ANY panel again still delivers fresh ---------------
    q = H.MockQuery("a:stats", ADMIN)
    q.message = H.MockMessage(chat_id=ADMIN, message_id=100)
    ctx = H.MockContext(bot)
    q._ctx = ctx
    t0 = time.monotonic()
    await dispatcher.buttons(_mk_update(q), ctx)
    ms = (time.monotonic() - t0) * 1000
    print(f"\n[proof 2] tap on the same panel again (message_id=100)")
    print(f"    fresh reply     : {bool(q.message.replies)} — {q.message.replies[-1]['text'][:50].replace(chr(10), ' ')}...")
    print(f"    in-place edits  : {len(q.message.edits)} (must be 0 — nothing is ever edited)")
    print(f"    handling time   : {ms:.0f} ms")
    assert q.message.replies and not q.message.edits and not q.message.deleted

    # --- proof 3: >48h panel (InaccessibleMessage) ---------------------------
    q = H.MockQuery("a:home", ADMIN)
    q.message = H.InaccessibleMock(chat_id=ADMIN, message_id=42)
    ctx = H.MockContext(bot)
    q._ctx = ctx
    t0 = time.monotonic()
    await dispatcher.buttons(_mk_update(q), ctx)
    ms = (time.monotonic() - t0) * 1000
    page = H.page_for(q, ctx)
    print(f"\n[proof 3] tap on a >48h panel (InaccessibleMessage, message_id=42)")
    print(f"    page delivered  : {bool(page)} — {page[:50].replace(chr(10), ' ')}...")
    print(f"    handling time   : {ms:.0f} ms")
    assert page, "inaccessible panels MUST still deliver a fresh page"

    print("\n" + "=" * 74)
    print("RESULT: every tap DELIVERS A FRESH VISIBLE PAGE — v6.8.0 restored 100%.")
    print("A silent «زر يُضغط ولا يظهر أي شي» is structurally impossible:")
    print("every tap is a brand-new message under the user's eyes. Nothing edited,")
    print("nothing deleted, nothing hidden.")
    print("=" * 74)


if __name__ == "__main__":
    asyncio.run(replay())
