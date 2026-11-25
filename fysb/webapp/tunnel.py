"""Zero-Config Verified Auto-HTTPS Tunnel for Bot-Hosting / VPS / Local (v7.2.1).

Guarantees ZERO Error 1033 broken links:
  1. Never activates or notifies a URL just because a tunnel printed it!
  2. For Cloudflare (`cloudflared`), waits for BOTH the `trycloudflare.com` URL
     AND `"Registered tunnel connection"` in stderr, trying IPv4 `http2` first
     and `quic` second.
  3. Performs a REAL end-to-end HTTPS self-test (`GET {url}/health` -> HTTP 200
     JSON `{"ok": true}`) proving traffic actually traverses the internet edge
     all the way back into our embedded Python HTTP server!
  4. If Bot-Hosting's firewall blocks Cloudflare's port 7844, automatically fails
     over to port-443 SSH HTTPS tunnels (`serveo.net` on TCP 443 -> `localhost.run`)
     when `ssh` is available, verifying each with `GET {url}/health`!
  5. Records real diagnostic log lines in `tunnel_status()["logs"]` so Admin
     always sees the exact reason if the host blocks all outbound tunnels.
"""
from __future__ import annotations

import asyncio
import json
import os
import platform
import re
import shutil
import stat
import urllib.error
import urllib.request
from collections import deque
from pathlib import Path

from fysb import config
from fysb.config import log

_TRYCF_RE = re.compile(r"https://[a-zA-Z0-9-]+\.trycloudflare\.com")
_SERVEO_RE = re.compile(r"https://[a-zA-Z0-9.-]+\.(?:serveo\.net|serveousercontent\.com)")
_LHR_RE = re.compile(r"https://[a-zA-Z0-9-]+\.lhr\.life")

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
    "verified": False,
    "provider": "",
    "url": "",
    "port": 0,
    "pid": 0,
    "binary": "",
    "last_error": "",
    "restarts": 0,
}
_LOGS: deque[str] = deque(maxlen=15)

_PROC: asyncio.subprocess.Process | None = None
_READERS: list[asyncio.Task] = []
_LOCK: asyncio.Lock | None = None


def _get_lock() -> asyncio.Lock:
    global _LOCK
    if _LOCK is None:
        _LOCK = asyncio.Lock()
    return _LOCK


def tunnel_status() -> dict:
    """Return a safe copy of the current auto-tunnel runtime state & recent logs."""
    out = dict(_STATE)
    out["logs"] = list(_LOGS)
    return out


def _download_url_for_host() -> str | None:
    return _BIN_URLS.get((platform.system().lower(), platform.machine().lower()))


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


def _probe_health_once(url: str, timeout: float = 8.0) -> tuple[bool, str]:
    """Perform a single real public HTTPS GET {url}/health and verify JSON `ok: true`."""
    health_url = url.rstrip("/") + "/health"
    req = urllib.request.Request(
        health_url,
        headers={
            "User-Agent": f"ForexYemeni-HealthCheck/{config.VERSION}",
            "Accept": "application/json",
        },
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            code = int(getattr(resp, "status", 200))
            raw = resp.read(2048).decode("utf-8", errors="ignore")
            if code != 200:
                return False, f"HTTP {code}"
            try:
                data = json.loads(raw)
                if isinstance(data, dict) and data.get("ok") is True:
                    return True, "OK"
            except Exception:
                pass
            return False, "استجابة غير متوقعة (ليست JSON من الخادم)"
    except urllib.error.HTTPError as exc:
        body = ""
        try:
            body = exc.read(512).decode("utf-8", errors="ignore")
        except Exception:
            pass
        if "1033" in body:
            return False, "Cloudflare Error 1033 (النفق لم يسجّل اتصاله)"
        return False, f"HTTP {exc.code}"
    except Exception as exc:
        return False, f"{type(exc).__name__}: {str(exc)[:80]}"


async def verify_https_health(url: str, retries: int = 7, delay: float = 2.0) -> tuple[bool, str]:
    """Verify end-to-end public HTTPS reachability (allows DNS propagation time)."""
    last_err = ""
    for attempt in range(max(1, int(retries))):
        ok, msg = await asyncio.to_thread(_probe_health_once, url, 8.0)
        if ok:
            return True, "OK"
        last_err = msg
        if attempt + 1 < retries:
            await asyncio.sleep(delay)
    return False, last_err or "تعذر التحقق من رابط HTTPS"


async def stop_tunnel() -> None:
    """Cleanly stop any running tunnel subprocess and reader tasks."""
    global _PROC, _READERS
    proc = _PROC
    _PROC = None
    readers = list(_READERS)
    _READERS.clear()
    _STATE["active"] = False
    _STATE["verified"] = False
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
        _STATE["provider"] = ""


async def _drain_tunnel_stream(
    stream: asyncio.StreamReader,
    url_re: re.Pattern,
    url_future: asyncio.Future,
    reg_future: asyncio.Future | None = None,
) -> None:
    """Stream tunnel stdout/stderr, recording recent logs and resolving URL & registration."""
    try:
        while True:
            raw = await stream.readline()
            if not raw:
                break
            line = raw.decode("utf-8", errors="ignore").strip()
            if not line:
                continue
            _LOGS.append(line[:200])
            m = url_re.search(line)
            if m and not url_future.done():
                url_future.set_result(m.group(0))
            if reg_future is not None and not reg_future.done():
                low = line.lower()
                if "registered tunnel connection" in low:
                    reg_future.set_result(True)
    except asyncio.CancelledError:
        raise
    except Exception:
        pass


async def _try_spawn_provider(
    provider_name: str,
    cmd: list[str],
    url_re: re.Pattern,
    require_cf_register: bool,
    target_port: int,
    timeout: float,
    verify: bool = True,
) -> tuple[str | None, str]:
    """Spawn one tunnel provider, wait for URL (+ registration if Cloudflare), and
    end-to-end verify `GET {url}/health == 200` before accepting."""
    global _PROC
    await stop_tunnel()
    _LOGS.clear()

    try:
        proc = await asyncio.create_subprocess_exec(
            *cmd,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
    except Exception as exc:
        return None, f"{provider_name}: تعذر التشغيل ({type(exc).__name__})"

    _PROC = proc
    _STATE["pid"] = proc.pid or 0
    _STATE["port"] = target_port
    _STATE["provider"] = provider_name

    loop = asyncio.get_running_loop()
    url_future: asyncio.Future = loop.create_future()
    reg_future: asyncio.Future | None = loop.create_future() if require_cf_register else None
    readers = []
    if proc.stderr:
        readers.append(asyncio.create_task(_drain_tunnel_stream(proc.stderr, url_re, url_future, reg_future)))
    if proc.stdout:
        readers.append(asyncio.create_task(_drain_tunnel_stream(proc.stdout, url_re, url_future, reg_future)))
    _READERS.extend(readers)

    try:
        url = await asyncio.wait_for(url_future, timeout=min(timeout, 22.0))
    except asyncio.TimeoutError:
        await stop_tunnel()
        return None, f"{provider_name}: انتهت مهلة الحصول على الرابط"
    except Exception as exc:
        await stop_tunnel()
        return None, f"{provider_name}: {type(exc).__name__}"

    # CRITICAL FIX for Cloudflare Error 1033: wait for actual edge registration!
    if reg_future is not None:
        try:
            await asyncio.wait_for(reg_future, timeout=min(timeout, 20.0))
        except asyncio.TimeoutError:
            log.warning("WEBAPP TUNNEL (%s): got %s but edge registration timed out (port 7844 likely blocked)", provider_name, url)
            await stop_tunnel()
            return None, f"{provider_name}: فشل تسجيل اتصال الحافة (غالبًا بورت 7844 محجوب)"

    # Ensure process is still alive right after registration
    if proc.returncode is not None:
        await stop_tunnel()
        return None, f"{provider_name}: توقفت العملية فجأة (code={proc.returncode})"

    # End-to-End Self-Test: actually fetch GET {url}/health through the public internet!
    if verify:
        ok, vmsg = await verify_https_health(url, retries=7, delay=2.0)
        if not ok:
            log.warning("WEBAPP TUNNEL (%s): health check failed for %s (%s)", provider_name, url, vmsg)
            await stop_tunnel()
            return None, f"{provider_name}: فشل الفحص الحي ({vmsg})"

    _STATE["active"] = True
    _STATE["verified"] = True
    _STATE["url"] = url
    _STATE["provider"] = provider_name
    _STATE["last_error"] = ""
    config.set_runtime_webapp_url(url, source="auto_tunnel")
    log.info("WEBAPP TUNNEL VERIFIED & READY [%s]: %s -> http://127.0.0.1:%s", provider_name, url, target_port)
    return url, ""


async def start_tunnel(port: int | None = None, timeout: float = 35.0, verify: bool = True) -> str | None:
    """Start a verified free HTTPS tunnel pointing to `http://127.0.0.1:<port>`.
    Tries Cloudflare (http2 -> quic), then automatically fails over to port-443
    SSH HTTPS tunnels (`serveo.net` -> `localhost.run`) if Cloudflare's port 7844
    is blocked on Bot-Hosting."""
    async with _get_lock():
        target_port = int(port if port is not None else config.WEBAPP_PORT)
        if target_port <= 0:
            _STATE["last_error"] = "بورت الخادم المحلي غير متاح"
            return None

        if is_tunnel_alive() and _STATE.get("port") == target_port:
            return _STATE["url"]

        errors: list[str] = []

        # 1) Primary: Cloudflare cloudflared (IPv4 http2, then IPv4 quic)
        bin_path = await asyncio.to_thread(ensure_cloudflared_binary)
        if bin_path:
            for proto in ("http2", "quic"):
                cmd = [
                    str(bin_path),
                    "tunnel",
                    "--no-autoupdate",
                    "--edge-ip-version",
                    "4",
                    "--protocol",
                    proto,
                    "--url",
                    f"http://127.0.0.1:{target_port}",
                ]
                url, err = await _try_spawn_provider(
                    f"Cloudflare-{proto}",
                    cmd,
                    _TRYCF_RE,
                    require_cf_register=True,
                    target_port=target_port,
                    timeout=timeout,
                    verify=verify,
                )
                if url:
                    return url
                errors.append(err)
        else:
            if _STATE.get("last_error"):
                errors.append(_STATE["last_error"])

        # 2) Fallback for Bot-Hosting containers that block port 7844:
        #    SSH reverse HTTPS tunnels over standard TCP port 443 / 22!
        ssh_bin = shutil.which("ssh")
        if ssh_bin:
            ssh_base = [
                ssh_bin,
                "-o", "StrictHostKeyChecking=no",
                "-o", "UserKnownHostsFile=/dev/null",
                "-o", "ServerAliveInterval=30",
                "-o", "ServerAliveCountMax=3",
                "-o", "ConnectTimeout=12",
                "-o", "ExitOnForwardFailure=yes",
            ]
            # Serveo on TCP port 443 (bypasses port 7844 & port 22 firewalls!)
            url, err = await _try_spawn_provider(
                "Serveo-443",
                [*ssh_base, "-p", "443", "-R", f"80:127.0.0.1:{target_port}", "serveo.net"],
                _SERVEO_RE,
                require_cf_register=False,
                target_port=target_port,
                timeout=timeout,
                verify=verify,
            )
            if url:
                return url
            errors.append(err)

            # localhost.run fallback
            url, err = await _try_spawn_provider(
                "LocalhostRun",
                [*ssh_base, "-R", f"80:127.0.0.1:{target_port}", "nokey@localhost.run"],
                _LHR_RE,
                require_cf_register=False,
                target_port=target_port,
                timeout=timeout,
                verify=verify,
            )
            if url:
                return url
            errors.append(err)

        _STATE["last_error"] = " · ".join(e for e in errors if e)[:280] or "تعذر فتح نفق HTTPS تلقائي"
        return None


def is_tunnel_alive() -> bool:
    return bool(
        _PROC is not None
        and _PROC.returncode is None
        and _STATE.get("active")
        and _STATE.get("verified")
        and _STATE.get("url")
    )
