"""Per-callback, best-effort feedback; never cancels the underlying action.

Fast pages need no extra messages. A slow route gets one temporary receipt,
not a success claim. Telegram/network timing remains outside our control.
"""
import asyncio
from contextvars import ContextVar
import time

from fysb.config import log

PROGRESS_DELAY_SECONDS = 0.75
FEEDBACK_TIMEOUT_SECONDS = 2.0
DELETE_TIMEOUT_SECONDS = 0.5

_current: ContextVar = ContextVar("callback_feedback", default=None)


class CallbackFeedback:
    def __init__(self, query, bot):
        self.query = query
        self.bot = bot
        self.started_at = time.monotonic()
        self.responded = asyncio.Event()
        self.first_response_ms = None
        self.notice = None
        self.task = None

    def start(self):
        self.task = asyncio.create_task(self._watch())

    def page_delivered(self):
        if not self.responded.is_set():
            self.first_response_ms = (time.monotonic() - self.started_at) * 1000
            log.info("BUTTON FIRST RESPONSE | %.0fms | data=%s",
                     self.first_response_ms, (self.query.data or "")[:32])
        self.responded.set()

    async def _send_notice(self):
        # Never attach business details before the route's authorization check.
        text = "⏳ تم استلام طلبك، جارٍ تنفيذه… ستظهر النتيجة عند اكتماله."
        message = getattr(self.query, "message", None)
        if message is not None and hasattr(message, "reply_text"):
            return await message.reply_text(text)
        chat = getattr(message, "chat", None) or getattr(self.query, "message_chat", None)
        if chat is not None and self.bot is not None:
            return await self.bot.send_message(chat.id, text)
        return None

    async def _watch(self):
        try:
            try:
                await asyncio.wait_for(self.responded.wait(), PROGRESS_DELAY_SECONDS)
                return
            except asyncio.TimeoutError:
                pass
            if self.responded.is_set():
                return
            self.notice = await asyncio.wait_for(self._send_notice(), FEEDBACK_TIMEOUT_SECONDS)
            if self.notice is not None:
                log.info("BUTTON PROGRESS | %.0fms | data=%s",
                         (time.monotonic() - self.started_at) * 1000,
                         (self.query.data or "")[:32])
            await self.responded.wait()
        except Exception as exc:
            # A failed progress request must never fail/retry the actual action.
            log.debug("callback feedback unavailable: %s", type(exc).__name__)
        finally:
            # Delete only our temporary message, never the original panel.
            original = getattr(self.query, "message", None)
            if self.notice is not None and self.notice is not original and hasattr(self.notice, "delete"):
                try:
                    await asyncio.wait_for(self.notice.delete(), DELETE_TIMEOUT_SECONDS)
                except Exception:
                    pass

    async def close(self):
        self.responded.set()
        if self.task is not None:
            await self.task


def bind(feedback):
    return _current.set(feedback)


def unbind(token):
    _current.reset(token)


def page_delivered(query):
    feedback = _current.get()
    if feedback is not None and feedback.query is query:
        feedback.page_delivered()
