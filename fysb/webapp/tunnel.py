"""Zero-Config Automatic HTTPS Tunnel for Bot-Hosting / VPS / Local (v7.2.0).

Enables Telegram Mini App & Admin Web Panel WITHOUT owning any domain, website,
or external hosting — ideal for Bot-Hosting (Pterodactyl) containers!

How it works (100% Free, Zero Account, Zero Root):
  1. If `WEBAPP_URL` is already configured in `.env` (or cloud host env like
     RAILWAY_PUBLIC_DOMAIN / RENDER_EXTERNAL_URL), this module does nothing.
  2. Otherwise, when `WEBAPP_ENABLED=1` and DB setting `webapp_auto_tunnel=1`:
     - Uses existing `cloudflared` in PATH if installed, OR downloads the
       official Cloudflare static binary once from GitHub Releases into
       `BASE_DIR/.bin/cloudflared` (with `/tmp/fysb_cloudflared` fallback).
     - Spawns `cloudflared tunnel --no-autoupdate --url http://127.0.0.1:<port>`.
     - Captures the live `https://<random>.trycloudflare.com` URL within ~3s
       and injects it into `config.set_runtime_webapp_url(url)` immediately
       (no bot restart required!).
     - Automatically restarts the tunnel if it ever drops, and terminates
       cleanly on `post_shutdown()`.
"""
from __future__ import annotations

import asyncio
import os
import platform
import re
import shutil
import stat
import urllib.request
from pathlib import Path

from fysb import config
from fysb.config import log

_TRYCF_RE = re.compile(r"https://[a-zA-Z0-9-]+\.trycloudflare\.com")

_BIN_URLS = {
    ("linux", "x86_64"): "https://github.com/cloudflare/cloudflared/releases/latest/download/cloudflared-linux-amd64",
    ("linux", "amd64"): "https://github.com/cloudflare/cloudflared/releases/latest/download/cloudflared-linux-amd64",
    ("linux", "aarch64"): "https://github.com/cloudflare/cloudflared/releases/latest/download/cloudflared-linux-arm64",
    ("linux", "arm64"): "https://github.com/cloudflare/cloudflared/releases/latest/download/cloudflared-linux-arm64",
    ("linux", "armv7l"): "https://github.com/cloudflare/cloudflared/releases/latest/download/cloudflared-linux-arm",
    ("linux", "arm"): "https://github.com/cloudflare/cloudflared/releases/latest/download/cloudflared-linux-arm",
}

_STATE: dict = {
    "active": False,
    "url": "",
    "port": 0,
    "pid": 0,
    "binary": "",
    "last_error": "",
    "restarts": 0,
}

_PROC: asyncio.subprocess.Process | None = None
_READERS: list[asyncio.Task] = []
_LOCK: asyncio.Lock | None = None


def _get_lock() -> asyncio.Lock:
    global _LOCK
    if _LOCK is None:
        _LOCK = asyncio.Lock()
    return _LOCK


def tunnel_status() -> dict:
    """Return a safe copy of the current auto-tunnel runtime state."""
    return dict(_STATE)


def _download_url_for_host() -> str | None:
    sys_name = platform.system().lower()
    arch = platform.machine().lower()
    return _BIN_URLS.get((sys_name, arch))


def _is_executable(path: Path) -> bool:
    try:
        return path.is_file() and path.stat().st_size > 1_000_000 and os.access(path, os.X_OK)
    except OSError:
        return False


def ensure_cloudflared_binary() -> Path | None:
    """Locate or download the official Cloudflare static binary (no root required)."""
    existing = shutil.which("cloudflared")
    if existing:
        p = Path(existing)
        _STATE["binary"] = str(p)
        return p

    candidates = [
        config.BASE_DIR / ".bin" / "cloudflared",
        Path("/tmp/fysb_cloudflared"),
    ]
    for cand in candidates:
        if _is_executable(cand):
            _STATE["binary"] = str(cand)
            return cand

    url = _download_url_for_host()
    if not url:
        _STATE["last_error"] = f"منصة غير مدعومة للنفق التلقائي ({platform.system()}/{platform.machine()})"
        return None

    for target in candidates:
        try:
            target.parent.mkdir(parents=True, exist_ok=True)
            tmp = target.with_suffix(".tmp")
            log.info("WEBAPP TUNNEL: downloading cloudflared from %s -> %s", url, target)
            req = urllib.request.Request(url, headers={"User-Agent": f"ForexYemeni/{config.VERSION}"})
            with urllib.request.urlopen(req, timeout=90) as resp, open(tmp, "wb") as out:
                shutil.copyfileobj(resp, out, length=256 * 1024)
            tmp.chmod(tmp.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)
            tmp.replace(target)
            if _is_executable(target):
                _STATE["binary"] = str(target)
                _STATE["last_error"] = ""
                log.info("WEBAPP TUNNEL: cloudflared ready (%s, %d bytes)", target, target.stat().st_size)
                return target
        except Exception as exc:
            _STATE["last_error"] = f"فشل تحميل cloudflared: {type(exc).__name__}"
            log.warning("WEBAPP TUNNEL: download to %s failed: %s", target, exc)
    return None


async def stop_tunnel() -> None:
    """Cleanly stop any running cloudflared subprocess and reader tasks."""
    global _PROC, _READERS
    proc = _PROC
    _PROC = None
    readers = list(_READERS)
    _READERS.clear()
    _STATE["active"] = False
    _STATE["pid"] = 0
    if proc is not None and proc.returncode is None:
        try:
            proc.terminate()
            try:
                await asyncio.wait_for(proc.wait(), timeout=5.0)
            except asyncio.TimeoutError:
                proc.kill()
                await proc.wait()
        except Exception:
            pass
    for t in readers:
        if not t.done():
            t.cancel()
    if readers:
        await asyncio.gather(*readers, return_exceptions=True)
    if config.WEBAPP_TUNNEL_SOURCE == "auto_tunnel":
        config.set_runtime_webapp_url("", source="")
        _STATE["url"] = ""


async def _drain_stream(stream: asyncio.StreamReader, url_future: asyncio.Future) -> None:
    """Read cloudflared stderr/stdout until EOF, resolving `url_future` on first trycloudflare URL."""
    try:
        while True:
            raw = await stream.readline()
            if not raw:
                break
            line = raw.decode("utf-8", errors="ignore").strip()
            if not line:
                continue
            m = _TRYCF_RE.search(line)
            if m and not url_future.done():
                url_future.set_result(m.group(0))
    except asyncio.CancelledError:
        raise
    except Exception:
        pass


async def start_tunnel(port: int | None = None, timeout: float = 35.0) -> str | None:
    """Start Cloudflare Quick Tunnel pointing to `http://127.0.0.1:<port>`.
    Returns the live `https://...trycloudflare.com` URL on success, or None."""
    global _PROC
    async with _get_lock():
        target_port = int(port if port is not None else config.WEBAPP_PORT)
        if target_port <= 0:
            _STATE["last_error"] = "بورت الخادم المحلي غير متاح"
            return None

        # Reuse already healthy tunnel on same port
        if _PROC is not None and _PROC.returncode is None and _STATE.get("url") and _STATE.get("port") == target_port:
            return _STATE["url"]

        await stop_tunnel()

        bin_path = await asyncio.to_thread(ensure_cloudflared_binary)
        if not bin_path:
            return None

        cmd = [
            str(bin_path),
            "tunnel",
            "--no-autoupdate",
            "--protocol",
            "http2",
            "--url",
            f"http://127.0.0.1:{target_port}",
        ]
        try:
            proc = await asyncio.create_subprocess_exec(
                *cmd,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
        except Exception as exc:
            _STATE["last_error"] = f"تعذر تشغيل cloudflared: {type(exc).__name__}"
            log.warning("WEBAPP TUNNEL: failed to launch %s: %s", bin_path, exc)
            return None

        _PROC = proc
        _STATE["pid"] = proc.pid or 0
        _STATE["port"] = target_port

        loop = asyncio.get_running_loop()
        url_future: asyncio.Future = loop.create_future()
        readers = []
        if proc.stderr:
            readers.append(asyncio.create_task(_drain_stream(proc.stderr, url_future)))
        if proc.stdout:
            readers.append(asyncio.create_task(_drain_stream(proc.stdout, url_future)))
        _READERS.extend(readers)

        try:
            url = await asyncio.wait_for(url_future, timeout=timeout)
        except asyncio.TimeoutError:
            _STATE["last_error"] = "انتهت مهلة الحصول على رابط HTTPS من Cloudflare"
            log.warning("WEBAPP TUNNEL: timed out waiting for trycloudflare URL")
            for t in readers:
                t.cancel()
            await stop_tunnel()
            return None
        except Exception as exc:
            _STATE["last_error"] = f"خطأ أثناء إنشاء النفق: {type(exc).__name__}"
            for t in readers:
                t.cancel()
            await stop_tunnel()
            return None

        _STATE["active"] = True
        _STATE["url"] = url
        _STATE["last_error"] = ""
        config.set_runtime_webapp_url(url, source="auto_tunnel")
        log.info("WEBAPP TUNNEL READY: %s -> http://127.0.0.1:%s", url, target_port)
        return url


def is_tunnel_alive() -> bool:
    return bool(_PROC is not None and _PROC.returncode is None and _STATE.get("active") and _STATE.get("url"))
