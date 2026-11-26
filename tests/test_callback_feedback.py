"""Offline callback feedback and UI-executor isolation contracts.

Tests intentionally control service/network waits. They cannot establish a
real Telegram end-to-end latency guarantee.
"""
import asyncio
from concurrent.futures import ThreadPoolExecutor
from contextvars import ContextVar
import threading
from types import SimpleNamespace

import pytest

import tests.helpers  # noqa: F401
from fysb import afn
from fysb.handlers import callback_feedback as feedback, common, dispatcher
from fysb.services import partners


class Message:
    def __init__(self, owner, text=""):
        self.owner = owner
        self.text = text
        self.deleted = False
        self.chat = SimpleNamespace(id=123)

    async def delete(self):
        self.deleted = True

    async def reply_text(self, text, **kwargs):
        message = Message(self.owner, text)
        self.owner.append(message)
        return message

    async def edit_text(self, text, **kwargs):
        self.text = text
        return self


def query(data="u:home"):
    messages = []
    q = SimpleNamespace(data=data, from_user=SimpleNamespace(id=1001), message=Message(messages))
    q.messages = messages
    q.acked = asyncio.Event()

    async def answer():
        q.acked.set()

    q.answer = answer
    return q


def context():
    return SimpleNamespace(bot=None, user_data={})


@pytest.fixture(autouse=True)
def fast_feedback(monkeypatch):
    # Short, deterministic test deadlines, not the production latency target.
    monkeypatch.setattr(feedback, "PROGRESS_DELAY_SECONDS", 0.005)
    monkeypatch.setattr(feedback, "FEEDBACK_TIMEOUT_SECONDS", 0.05)
    monkeypatch.setattr(feedback, "DELETE_TIMEOUT_SECONDS", 0.05)
    monkeypatch.setattr(common.db, "get_setting", lambda *a: "0")
    yield
    afn.shutdown()


def test_reserved_ui_pool_survives_a_fully_saturated_default_executor():
    async def run():
        loop = asyncio.get_running_loop()
        loop.set_default_executor(ThreadPoolExecutor(max_workers=1, thread_name_prefix="busy-background"))
        started = asyncio.Event()
        release = threading.Event()

        def block():
            loop.call_soon_threadsafe(started.set)
            assert release.wait(3)

        job = asyncio.create_task(afn.run(block))
        try:
            await asyncio.wait_for(started.wait(), 1)
            with afn.interactive():
                name = await afn.run(lambda: threading.current_thread().name)
            assert name.startswith("fysb-ui")
            assert not job.done()
        finally:
            release.set()
            await job

    asyncio.run(run())


def test_ui_pool_preserves_context_kwargs_results_and_exceptions():
    marker = ContextVar("test_marker", default="absent")

    async def run():
        marker.set("request")
        with afn.interactive():
            result = await afn.run(lambda *, value: (marker.get(), value), value=42)
            assert result == ("request", 42)

            def fail():
                raise ValueError("same exception")

            with pytest.raises(ValueError, match="same exception"):
                await afn.run(fail)

    asyncio.run(run())


def test_spawned_broadcast_tasks_do_not_inherit_ui_priority():
    async def run():
        asyncio.get_running_loop().set_default_executor(ThreadPoolExecutor(max_workers=1, thread_name_prefix="background"))
        with afn.interactive():
            direct = await afn.run(lambda: threading.current_thread().name)
            child = await asyncio.create_task(afn.run(lambda: threading.current_thread().name))
        after = await afn.run(lambda: threading.current_thread().name)
        assert direct.startswith("fysb-ui")
        assert child.startswith("background") and after.startswith("background")

    asyncio.run(run())


def test_nested_interactive_scope_restores_outer_owner():
    async def run():
        with afn.interactive():
            with afn.interactive():
                assert (await afn.run(lambda: threading.current_thread().name)).startswith("fysb-ui")
            assert (await afn.run(lambda: threading.current_thread().name)).startswith("fysb-ui")

    asyncio.run(run())


@pytest.mark.parametrize("data", ["u:home", "a:home", "p:home", "unknown"])
def test_slow_routes_get_one_receipt_and_keep_running(monkeypatch, data):
    async def run():
        q = query(data)
        release = asyncio.Event()
        received = asyncio.Event()
        original_send = feedback.CallbackFeedback._send_notice
        actions = []

        async def notice(self):
            message = await original_send(self)
            received.set()
            return message

        async def slow_route(*args):
            await release.wait()
            actions.append("executed once")
            await common.render(q, "Final result")

        monkeypatch.setattr(feedback.CallbackFeedback, "_send_notice", notice)
        monkeypatch.setattr(dispatcher, "_route_user", slow_route)
        monkeypatch.setattr(dispatcher, "_route_admin", slow_route)
        monkeypatch.setattr(dispatcher.admin_partners, "handle_partner_callback", slow_route)
        monkeypatch.setattr(partners, "get_partner_by_user", lambda *a: {"enabled": True})
        task = asyncio.create_task(dispatcher.buttons(SimpleNamespace(callback_query=q), context()))
        try:
            await asyncio.wait_for(received.wait(), 1)
            assert q.acked.is_set()
            assert not task.done()
            assert len(q.messages) == 1
            assert "تم استلام طلبك" in q.messages[0].text
            assert "نجاح" not in q.messages[0].text
        finally:
            release.set()
            await task
        assert actions == ["executed once"]
        assert q.messages[0].deleted
        assert q.messages[-1].text == "Final result" and not q.messages[-1].deleted

    asyncio.run(run())


def test_fast_page_does_not_get_a_progress_message(monkeypatch):
    async def run():
        q = query()

        async def route(*args):
            await common.render(q, "Fast page")

        monkeypatch.setattr(feedback, "PROGRESS_DELAY_SECONDS", 1)
        monkeypatch.setattr(dispatcher, "_route_user", route)
        await dispatcher.buttons(SimpleNamespace(callback_query=q), context())
        assert q.acked.is_set()
        assert [m.text for m in q.messages] == ["Fast page"]
        assert not q.messages[0].deleted

    asyncio.run(run())


def test_existing_loading_page_suppresses_duplicate_progress(monkeypatch):
    async def run():
        q = query()
        receipt = feedback.CallbackFeedback(q, None)
        token = feedback.bind(receipt)
        receipt.start()
        try:
            await common.render(q, "Analysis is in progress")
            assert receipt.responded.is_set()
            # Complete the watchdog while the original operation is still live.
            await asyncio.wait_for(receipt.task, 1)
            assert receipt.notice is None
            assert [m.text for m in q.messages] == ["Analysis is in progress"]
        finally:
            await receipt.close()
            feedback.unbind(token)

    asyncio.run(run())


@pytest.mark.parametrize("failure", ["exception", "timeout"])
def test_feedback_failure_never_cancels_or_retries_business_action(monkeypatch, failure):
    async def run():
        q = query()
        notice_started = asyncio.Event()
        release = asyncio.Event()
        actions = []

        async def notice(self):
            notice_started.set()
            if failure == "exception":
                raise RuntimeError("Telegram unavailable")
            await asyncio.Event().wait()

        async def route(*args):
            await notice_started.wait()
            await release.wait()
            actions.append("done")

        monkeypatch.setattr(feedback.CallbackFeedback, "_send_notice", notice)
        monkeypatch.setattr(dispatcher, "_route_user", route)
        task = asyncio.create_task(dispatcher.buttons(SimpleNamespace(callback_query=q), context()))
        try:
            await asyncio.wait_for(notice_started.wait(), 1)
        finally:
            release.set()
            await asyncio.wait_for(task, 1)
        assert actions == ["done"]
        assert not q.messages

    asyncio.run(run())


def test_stalled_ack_does_not_prevent_a_final_page(monkeypatch):
    async def run():
        q = query()
        delivered = asyncio.Event()

        async def answer():
            await asyncio.Event().wait()

        async def route(*args):
            await common.render(q, "Result")
            delivered.set()

        q.answer = answer
        monkeypatch.setattr(dispatcher, "ACK_TIMEOUT_SECONDS", 0.03)
        monkeypatch.setattr(dispatcher, "_route_user", route)
        task = asyncio.create_task(dispatcher.buttons(SimpleNamespace(callback_query=q), context()))
        await asyncio.wait_for(delivered.wait(), 1)
        await asyncio.wait_for(task, 1)
        assert [m.text for m in q.messages] == ["Result"]

    asyncio.run(run())


def test_cancellation_removes_feedback_and_preserves_cancellation(monkeypatch):
    async def run():
        q = query()
        sent = asyncio.Event()
        original_send = feedback.CallbackFeedback._send_notice

        async def notice(self):
            result = await original_send(self)
            sent.set()
            return result

        async def route(*args):
            await asyncio.Event().wait()

        monkeypatch.setattr(feedback.CallbackFeedback, "_send_notice", notice)
        monkeypatch.setattr(dispatcher, "_route_user", route)
        task = asyncio.create_task(dispatcher.buttons(SimpleNamespace(callback_query=q), context()))
        await asyncio.wait_for(sent.wait(), 1)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
        assert q.messages[0].deleted
        assert feedback._current.get() is None
        assert not [t for t in asyncio.all_tasks() if t is not asyncio.current_task() and not t.done()]

    asyncio.run(run())


def test_progress_send_racing_final_page_is_cleaned_up():
    async def run():
        q = query()
        receipt = feedback.CallbackFeedback(q, None)
        sending = asyncio.Event()
        release = asyncio.Event()

        async def notice():
            sending.set()
            await release.wait()
            return await q.message.reply_text("Temporary receipt")

        receipt._send_notice = notice
        receipt.start()
        await asyncio.wait_for(sending.wait(), 1)
        receipt.page_delivered()
        release.set()
        await asyncio.wait_for(receipt.close(), 1)
        assert receipt.notice.deleted

    asyncio.run(run())


def test_inaccessible_old_panel_uses_chat_fallback():
    async def run():
        q = query()
        q.message = SimpleNamespace(chat=SimpleNamespace(id=456))
        sends = []
        sent = asyncio.Event()

        async def send(chat_id, text):
            sends.append((chat_id, text))
            sent.set()
            return Message([])

        receipt = feedback.CallbackFeedback(q, SimpleNamespace(send_message=send))
        receipt.start()
        await asyncio.wait_for(sent.wait(), 1)
        await receipt.close()
        assert len(sends) == 1 and sends[0][0] == 456
        assert receipt.notice.deleted

    asyncio.run(run())


def test_progress_never_deletes_original_panel_returned_by_legacy_mock():
    async def run():
        q = query()
        sent = asyncio.Event()

        async def legacy_reply(text):
            sent.set()
            return q.message

        q.message.reply_text = legacy_reply
        receipt = feedback.CallbackFeedback(q, None)
        receipt.start()
        await asyncio.wait_for(sent.wait(), 1)
        await receipt.close()
        assert not q.message.deleted

    asyncio.run(run())


def test_two_callback_scopes_cannot_mark_each_other_delivered():
    async def run():
        first, second = query("u:home"), query("a:home")
        one, two = feedback.CallbackFeedback(first, None), feedback.CallbackFeedback(second, None)
        token_one = feedback.bind(one)
        try:
            token_two = feedback.bind(two)
            try:
                feedback.page_delivered(first)
                assert not one.responded.is_set() and not two.responded.is_set()
                feedback.page_delivered(second)
                assert two.responded.is_set()
            finally:
                feedback.unbind(token_two)
            feedback.page_delivered(first)
            assert one.responded.is_set()
        finally:
            feedback.unbind(token_one)

    asyncio.run(run())


def test_full_dispatcher_renders_while_default_pool_is_busy(monkeypatch):
    async def run():
        loop = asyncio.get_running_loop()
        loop.set_default_executor(ThreadPoolExecutor(max_workers=1))
        started = asyncio.Event()
        release = threading.Event()

        def block():
            loop.call_soon_threadsafe(started.set)
            assert release.wait(3)

        async def route(q, *args):
            # Models the identity/snapshot reads used by real page handlers.
            value = await afn.run(lambda: "Ready page")
            await common.render(q, value)

        monkeypatch.setattr(dispatcher, "_route_user", route)
        monkeypatch.setattr(feedback, "PROGRESS_DELAY_SECONDS", 1)
        background = asyncio.create_task(afn.run(block))
        try:
            await asyncio.wait_for(started.wait(), 1)
            q = query()
            await asyncio.wait_for(dispatcher.buttons(SimpleNamespace(callback_query=q), context()), 1)
            assert [m.text for m in q.messages] == ["Ready page"]
            assert not background.done()
        finally:
            release.set()
            await background

    asyncio.run(run())


def test_error_response_finishes_watchdog_without_repeating_action(monkeypatch):
    async def run():
        q = query()
        actions = []

        async def route(*args):
            actions.append(1)
            raise ValueError("business error")

        monkeypatch.setattr(dispatcher, "_route_user", route)
        await dispatcher.buttons(SimpleNamespace(callback_query=q), context())
        assert actions == [1]
        assert len(q.messages) == 1 and "حدث خطأ" in q.messages[0].text
        assert not q.messages[0].deleted

    asyncio.run(run())


def test_admin_guard_is_not_bypassed_by_feedback(monkeypatch):
    async def run():
        q = query("a:home")
        monkeypatch.setattr(dispatcher.config, "ADMIN_ID", 999999)
        audit = []
        monkeypatch.setattr(dispatcher.audit_log, "log", lambda *a: audit.append(a))
        monkeypatch.setattr(feedback, "PROGRESS_DELAY_SECONDS", 1)
        await dispatcher.buttons(SimpleNamespace(callback_query=q), context())
        assert len(audit) == 1 and "admin_probe" in audit[0][1]
        assert len(q.messages) == 1 and "غير مصرح" in q.messages[0].text

    asyncio.run(run())


def test_slow_notice_delete_does_not_hold_action_forever(monkeypatch):
    async def run():
        q = query()
        receipt = feedback.CallbackFeedback(q, None)
        sent = asyncio.Event()

        async def never_delete():
            await asyncio.Event().wait()

        async def notice():
            message = Message([])
            message.delete = never_delete
            sent.set()
            return message

        receipt._send_notice = notice
        receipt.start()
        await asyncio.wait_for(sent.wait(), 1)
        await asyncio.wait_for(receipt.close(), 1)
        assert receipt.task.done()

    asyncio.run(run())


def test_async_handlers_do_not_open_database_connections_inline():
    """Structural guard, NOT a substitute for profiling indirect service calls."""
    import ast
    from pathlib import Path

    violations = []

    class InlineDatabase(ast.NodeVisitor):
        def visit_FunctionDef(self, node):
            # Sync helpers are deliberately executed through afn.run/to_thread.
            pass

        def visit_Lambda(self, node):
            pass

        def visit_AsyncFunctionDef(self, node):
            for child in node.body:
                self.visit(child)

        def visit_Call(self, node):
            func = node.func
            if (isinstance(func, ast.Attribute) and isinstance(func.value, ast.Name)
                    and func.value.id == "db" and func.attr in ("conn", "tx")):
                violations.append((path.name, node.lineno))
            self.generic_visit(node)

    root = Path(dispatcher.__file__).parent
    for path in root.glob("*.py"):
        InlineDatabase().visit(ast.parse(path.read_text(encoding="utf-8")))
    assert not violations, violations
