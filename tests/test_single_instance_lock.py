"""SingleInstanceLock — restart-proof stale-lock takeover.

Regression root: on bot-hosting.net the container restart reuses the same low
PID numbers, so the new process saw the DEAD run's PID as «alive» and locked
itself out of its own stale fysb.lock («يوجد نسخة أخرى من البوت تعمل حالياً»).

Contract under test:
  take over silently when  — PID is ours · PID is dead · start-tick mismatch
  (PID reused) · legacy plain-PID lock not held by a main.py run of this
  folder · garbage content · unreadable content;
  block ONLY when a genuinely live second incarnation holds the lock
  (same PID + same /proc start tick).
"""
import os
import subprocess
import sys
import time
from pathlib import Path

import pytest

from fysb.handlers import background
from fysb.handlers.background import SingleInstanceLock

LINUX = os.path.isdir("/proc")


def _write_lock(tmp_path: Path, content: str) -> None:
    background.LOCK_FILE.write_text(content)


@pytest.fixture(autouse=True)
def _isolate_lock_file(tmp_path, monkeypatch):
    """Redirect the lock to a temp file — never touch the project root."""
    monkeypatch.setattr(background, "LOCK_FILE", tmp_path / "fysb.lock")
    yield


def _dead_pid() -> int:
    """A PID that provably belonged to a real, now-finished process."""
    p = subprocess.Popen([sys.executable, "-c", "pass"])
    p.wait()
    return p.pid


def test_fresh_acquire_and_release(tmp_path):
    lock = SingleInstanceLock()
    assert lock.acquire() is True
    raw = background.LOCK_FILE.read_text().strip()
    pid_text, _, tick = raw.partition(":")
    assert pid_text == str(os.getpid())
    if LINUX:
        assert tick and tick.isdigit()          # incarnation marker recorded
    lock.release()
    assert not background.LOCK_FILE.exists()
    assert lock.acquired is False


def test_release_idempotent(tmp_path):
    lock = SingleInstanceLock()
    assert lock.acquire() is True
    lock.release()
    lock.release()  # must not raise
    assert not background.LOCK_FILE.exists()


def test_dead_pid_lock_taken_over(tmp_path):
    _write_lock(tmp_path, f"{_dead_pid()}:424242")
    assert SingleInstanceLock().acquire() is True


def test_own_pid_lock_taken_over(tmp_path):
    """THE bot-hosting bug: the restarted bot reuses its own PID slot."""
    _write_lock(tmp_path, f"{os.getpid()}:anything")
    assert SingleInstanceLock().acquire() is True


def test_legacy_own_pid_plain_lock_taken_over(tmp_path):
    """Old-format lock (v6.6.2 wrote a bare PID) held by our own PID."""
    _write_lock(tmp_path, str(os.getpid()))
    assert SingleInstanceLock().acquire() is True


@pytest.mark.skipif(not LINUX, reason="/proc identity check is Linux-only")
def test_pid_reused_by_unrelated_process_taken_over(tmp_path, tmp_path_factory):
    """Live PID but NOT a main.py run of this folder (legacy no-tick lock)."""
    workdir = tmp_path_factory.mktemp("unrelated")
    p = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(30)"],
                         cwd=str(workdir))
    try:
        time.sleep(0.2)
        _write_lock(tmp_path, str(p.pid))       # legacy format, no start tick
        assert SingleInstanceLock().acquire() is True
    finally:
        p.terminate()
        p.wait()


@pytest.mark.skipif(not LINUX, reason="/proc start tick is Linux-only")
def test_genuine_live_incarnation_blocks(tmp_path):
    """Same PID + same start tick = the real owner is alive → must block."""
    p = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(30)"],
                         cwd=str(os.path.expanduser("~")))
    try:
        time.sleep(0.2)
        tick = background._proc_start_tick(p.pid)
        assert tick, "start tick must be readable for a live process"
        _write_lock(tmp_path, f"{p.pid}:{tick}")
        assert SingleInstanceLock().acquire() is False
    finally:
        p.terminate()
        p.wait()


@pytest.mark.skipif(not LINUX, reason="/proc start tick is Linux-only")
def test_boot_tick_mismatch_means_reused_pid(tmp_path):
    """Live PID whose current start tick differs from the stored one."""
    p = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(30)"],
                         cwd=str(os.path.expanduser("~")))
    try:
        time.sleep(0.2)
        real = background._proc_start_tick(p.pid)
        forged = "99999999" if real != "99999999" else "88888888"
        _write_lock(tmp_path, f"{p.pid}:{forged}")
        assert SingleInstanceLock().acquire() is True   # PID reuse → stale
    finally:
        p.terminate()
        p.wait()


def test_garbage_lock_taken_over(tmp_path):
    _write_lock(tmp_path, "not-a-pid at all")
    assert SingleInstanceLock().acquire() is True


def test_empty_lock_taken_over(tmp_path):
    _write_lock(tmp_path, "")
    assert SingleInstanceLock().acquire() is True


def test_unreadable_lock_taken_over(tmp_path):
    lf = background.LOCK_FILE
    lf.write_text(f"{os.getpid()}:1")
    os.chmod(lf, 0o000)
    try:
        assert SingleInstanceLock().acquire() is True
    finally:
        os.chmod(lf, 0o644)


def test_second_acquire_in_same_process_blocked_then_released(tmp_path):
    """A held lock blocks a second acquire; release frees it again."""
    first = SingleInstanceLock()
    assert first.acquire() is True
    second = SingleInstanceLock()
    assert second.acquire() is False
    first.release()
    assert second.acquire() is True
    second.release()
