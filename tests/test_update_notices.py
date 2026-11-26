"""Offline contracts for branded maintenance notices and the complete archive."""
import asyncio
import html
import sqlite3
from contextlib import contextmanager
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock, patch

import pytest
from tests.helpers import MockMessage  # config isolation before app imports
from tests.test_updater import build_package
from fysb import config
from fysb.handlers import admin_system
from fysb.services import update_notices as notices
from telegram.error import BadRequest, Forbidden, NetworkError, RetryAfter, TimedOut
import updater


def run(coro):
    return asyncio.run(coro)


@pytest.mark.parametrize('problem', [None, 'package', 'checksum', 'download', 'equal'])
def test_hook_only_after_verified_package_before_extraction(tmp_path, problem):
    pkg = tmp_path / 'package.zip'
    build_package(pkg)
    dest = tmp_path / 'live'
    dest.mkdir()
    (dest / 'main.py').write_text('original')
    def check_original(info):
        assert info['remote'] == 'v99.0.0'
        assert (dest / 'main.py').read_text() == 'original'
    hook = Mock(side_effect=check_original)
    rel = {'tag': 'v99.0.0', 'url': 'https://example.invalid/pkg'}
    if problem == 'equal':
        rel['tag'] = 'v' + config.VERSION
    if problem == 'checksum':
        rel['sha256'] = '0' * 64

    def download(url, target):
        if problem == 'download':
            raise OSError('offline')
        target.write_bytes(b'broken' if problem == 'package' else pkg.read_bytes())

    with patch.object(updater, 'latest_release', return_value=rel), patch.object(updater, '_download', side_effect=download):
        result = updater.self_update(dest, before_install=hook)
    assert hook.call_count == (0 if problem else 1)
    if not problem:
        assert result['status'] == 'updated'
        assert (dest / 'main.py').read_text() != 'original'
    else:
        assert (dest / 'main.py').read_text() == 'original'


def test_targets_filter_and_deduplicate():
    c = sqlite3.connect(':memory:')
    c.row_factory = sqlite3.Row
    c.executescript('''CREATE TABLE users(user_id INTEGER, is_blocked INTEGER);
        CREATE TABLE broadcast_channels(chat_id TEXT, enabled INTEGER);
        CREATE TABLE chart_channels(chat_id TEXT, enabled INTEGER);''')
    c.executemany('INSERT INTO users VALUES (?,?)', [(config.ADMIN_ID, 0), (123, 0), (456, 1)])
    c.executemany('INSERT INTO broadcast_channels VALUES (?,?)', [('-1001', 1), ('-1002', 0), ('@MyChannel', 1)])
    c.executemany('INSERT INTO chart_channels VALUES (?,?)', [('-1001', 1), ('@mychannel', 1), ('-1003', 1)])
    @contextmanager
    def conn():
        yield c
    with patch.object(notices.db, 'conn', conn):
        assert notices._targets() == [123, -1001, '@MyChannel', -1003]
    c.close()


@pytest.mark.parametrize('error', [Forbidden('blocked'), TimedOut(), NetworkError('unknown outcome'), asyncio.TimeoutError()])
def test_unknown_or_forbidden_delivery_never_duplicates(error):
    bot = SimpleNamespace(send_photo=AsyncMock(side_effect=error), send_message=AsyncMock())
    assert run(notices._deliver(bot, 1, 'text', b'logo')) is False
    bot.send_photo.assert_awaited_once()
    bot.send_message.assert_not_awaited()


def test_invalid_photo_text_fallback():
    bot = SimpleNamespace(send_photo=AsyncMock(side_effect=BadRequest('invalid photo')), send_message=AsyncMock())
    assert run(notices._deliver(bot, 1, 'text', b'logo'))
    bot.send_message.assert_awaited_once()


@pytest.mark.parametrize('seconds,success,calls', [(2, True, 2), (31, False, 1)])
def test_retry_after_respected(seconds, success, calls):
    bot = SimpleNamespace(send_photo=AsyncMock(side_effect=[RetryAfter(seconds), SimpleNamespace(photo=[])]), send_message=AsyncMock())
    with patch.object(notices.asyncio, 'sleep', new_callable=AsyncMock) as sleep:
        assert run(notices._deliver(bot, 1, 'text', b'logo')) is success
    assert bot.send_photo.await_count == calls
    if success:
        sleep.assert_any_await(seconds)
    bot.send_message.assert_not_awaited()


def test_announcement_reuses_file_id_and_counts_failures():
    bot = SimpleNamespace(send_photo=AsyncMock(side_effect=[SimpleNamespace(photo=[SimpleNamespace(file_id='cached')]), Forbidden('no permission')]), send_message=AsyncMock())
    with patch.object(notices.asyncio, 'sleep', new_callable=AsyncMock):
        result = run(notices.announce(bot, {'local': '8.9.2', 'remote': 'v8.9.3'}, targets=[1, 2]))
    assert result == {'sent': 1, 'failed': 1, 'targets': [1]}
    assert bot.send_photo.await_args_list[1].kwargs['photo'] == 'cached'
    text = bot.send_photo.await_args_list[0].kwargs['caption']
    assert 'v8.9.2' in text and 'v8.9.3' in text and 'FOREXYEMENI' in text
    assert len(text.encode('utf-16-le')) // 2 < 1024


def test_card_escapes_input():
    assert '&lt;script&gt;' in notices.notice_card('<script>', 'A&B')
    assert 'A&amp;B' in notices.notice_card('<script>', 'A&B')


@pytest.mark.parametrize('status', ['updated', 'error', 'no_update'])
def test_install_hook_delivery_order_and_cancellation_notice(status):
    events = []
    async def announce(bot, info, **kw):
        events.append('cancel' if kw.get('cancelled') else 'notice')
        return {'sent': 1, 'failed': 0, 'targets': [1]}
    def install(*args, before_install):
        if status != 'no_update':
            before_install({'local': '1', 'remote': '2'})
        events.append('extract' if status == 'updated' else status)
        return {'status': status, 'local': '1', 'remote': '2'}
    with patch.object(updater, 'self_update', side_effect=install), patch.object(notices, 'announce', side_effect=announce):
        result = run(notices.install(object()))
    expected = {'updated': ['notice', 'extract'], 'error': ['notice', 'error', 'cancel'], 'no_update': ['no_update']}[status]
    assert events == expected
    assert bool(result.get('notice')) == (status != 'no_update')


def test_simultaneous_install_rejected():
    notices._INSTALL_LOCK.acquire()
    try:
        with patch.object(updater, 'self_update') as install:
            assert run(notices.install(object()))['status'] == 'error'
        install.assert_not_called()
    finally:
        notices._INSTALL_LOCK.release()


def test_note_pages_escape_without_loss_or_broken_entities():
    text = ('<hello> & 😀 العربية\n' * 900)
    pages = admin_system._release_note_pages(text)
    assert len(pages) > 6
    assert ''.join(map(html.unescape, pages)) == text
    assert all(len(p.encode('utf-16-le')) // 2 <= 2200 for p in pages)
    assert all('<hello>' not in p for p in pages)


def test_archive_second_page_and_notes_without_zip():
    data = [{'tag_name': 'v1.2.3', 'name': 'old', 'body': 'fixed <x>', 'assets': []}]
    with patch.object(updater, '_get_json', return_value=data) as get:
        rows = updater.list_releases(6, page=2, include_uninstallable=True)
    assert 'page=2' in get.call_args.args[0]
    assert rows[0]['notes'] == 'fixed <x>' and not rows[0]['url']
    with patch.object(updater, '_get_json', side_effect=OSError()), patch.object(updater, 'tags_from_feed') as feed:
        assert updater.list_releases(6, page=2) == []
    feed.assert_not_called()
    with patch.object(updater, '_get_json', return_value=data[0]):
        assert updater.release_details('v1.2.3')['notes'] == 'fixed <x>'


def test_archive_permissions():
    ctx = SimpleNamespace(user_data={})
    with patch.object(admin_system, 'render', new_callable=AsyncMock) as render, patch.object(updater, 'list_releases') as listing:
        run(admin_system.admin_updates(None, config.ADMIN_ID + 1, ctx, None))
        run(admin_system.admin_release_notes(None, config.ADMIN_ID + 1, ctx, None, 'v1.2.3'))
    assert render.await_count == 2
    listing.assert_not_called()


def test_boot_forwards_optional_hook():
    hook = Mock()
    with patch.object(updater, 'latest_release', return_value=None), patch.object(updater, 'self_update', return_value={'status': 'no_update', 'local': config.VERSION, 'remote': 'v' + config.VERSION}) as install:
        updater.boot_check(before_install=hook)
    install.assert_called_once_with(before_install=hook)


def test_dispatcher_archive_and_note_page_routes():
    from fysb import db
    db.init_db()
    from tests.test_phase6_perf_and_versioned_update import _fire
    from tests.helpers import page_of
    rows = [{'tag': f'v1.2.{n}', 'title': 'Historical', 'notes': '<fix>\n' * 900, 'url': ''} for n in range(6)]
    with patch.object(updater, 'list_releases', return_value=rows) as listing, patch.object(updater, 'list_rollback_snapshots', return_value=[]):
        query, ctx = _fire('a:updates:2')
    listing.assert_called_once_with(6, page=2, include_uninstallable=True)
    assert 'الصفحة <b>2' in page_of(query)
    with patch.object(updater, 'release_details', return_value=rows[0]):
        query, _ = _fire('a:updnotes:v1.2.0:2')
    assert 'الجزء 2/' in page_of(query)
    assert '&lt;fix&gt;' in page_of(query)


def test_install_cancellation_waits_for_worker():
    async def exercise():
        entered = asyncio.Event()
        release = asyncio.Event()
        async def announce(bot, info, **kw):
            entered.set()
            await release.wait()
            return {'sent': 0, 'failed': 0, 'targets': []}
        def install(*args, before_install):
            before_install({'local': '1', 'remote': '2'})
            return {'status': 'updated'}
        with patch.object(updater, 'self_update', side_effect=install), patch.object(notices, 'announce', side_effect=announce):
            task = asyncio.create_task(notices.install(object()))
            await asyncio.wait_for(entered.wait(), 2)
            task.cancel()
            await asyncio.sleep(0)
            assert notices._INSTALL_LOCK.locked()
            release.set()
            with pytest.raises(asyncio.CancelledError):
                await asyncio.wait_for(task, 2)
        assert not notices._INSTALL_LOCK.locked()
    run(exercise())
