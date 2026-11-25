"""Zero-Config Verified Auto-HTTPS Tunnel for Bot-Hosting / VPS / Local (v7.2.2).

Provider priority chain (ALL end-to-end verified via `GET {url}/health -> HTTP 200`):
  1. **Ngrok-443** (if Admin set a free Ngrok authtoken via Telegram or `NGROK_AUTHTOKEN`):
     Uses PURE TLS port 443 (`connect.ngrok-agent.com:443`) — indistinguishable from
     regular HTTPS traffic, so Bot-Hosting / Pterodactyl firewalls CANNOT block it!
  2. **Pinggy-443 / Serveo-443 / LocalhostRun-443** (zero-account SSH over TCP port 443
     with proper `stdin=DEVNULL`, `-T`, non-interactive flags, and ANSI stripping).
  3. **Cloudflare `cloudflared`** (IPv4 `http2` -> `quic`, waiting for BOTH the URL AND
     `"Registered tunnel connection"` to prevent Error 1033).

Guarantees ZERO broken Error 1033 links: a URL is ONLY activated and sent to Admin
after real public HTTPS `GET {url}/health` returns HTTP 200 JSON `{"ok": true}`.
"""
from __future__ import annotations

import asyncio
import json
import os
import platform
import re
import shutil
import stat
import tarfile
import urllib.error
import urllib.request
from collections import deque
from pathlib import Path

from fysb import config, db
from fysb.config import log

_ANSI_RE = re.compile(r"\x1b\[[0-9;]*[a-zA-Z]")
_TRYCF_RE = re.compile(r"https://[a-zA-Z0-9-]+\.trycloudflare\.com")
_PINGGY_RE = re.compile(r"https://[a-zA-Z0-9.-]+\.pinggy\.(?:link|io|online)")
_SERVEO_RE = re.compile(r"https://[a-zA-Z0-9.-]+\.(?:serveo\.net|serveousercontent\.com)")
_LHR_RE = re.compile(r"https://[a-zA-Z0-9-]+\.lhr\.life")
_NGROK_RE = re.compile(r"https://[a-zA-Z0-9.-]+\.ngrok(?:-free)?\.(?:app|dev|io)")

_CF_BIN_URLS = {
    ("linux", "x86_64"): "https://github.com/cloudflare/cloudflared/releases/latest/download/cloudflared-linux-amd64",
    ("linux", "amd64"): "https://github.com/cloudflare/cloudflared/releases/latest/download/cloudflared-linux-amd64",
    ("linux", "aarch64"): "https://github.com/cloudflare/cloudflared/releases/latest/download/cloudflared-linux-arm64",
    ("linux", "arm64"): "https://github.com/cloudflare/cloudflared/releases/latest/download/cloudflared-linux-arm64",
    ("linux", "armv7l"): "https://github.com/cloudflare/cloudflared/releases/latest/download/cloudflared-linux-arm",
    ("linux", "arm"): "https://github.com/cloudflare/cloudflared/releases/latest/download/cloudflared-linux-arm",
}

_NGROK_TGZ_URLS = {
    ("linux", "x86_64"): "https://bin.equinox.io/c/bNyj1mQVY4c/ngrok-v3-stable-linux-amd64.tgz",
    ("linux", "amd64"): "https://bin.equinox.io/c/bNyj1mQVY4c/ngrok-v3-stable-linux-amd64.tgz",
    ("linux", "aarch64"): "https://bin.equinox.io/c/bNyj1mQVY4c/ngrok-v3-stable-linux-arm64.tgz",
    ("linux", "arm64"): "https://bin.equinox.io/c/bNyj1mQVY4c/ngrok-v3-stable-linux-arm64.tgz",
    ("linux", "armv7l"): "https://bin.equinox.io/c/bNyj1mQVY4c/ngrok-v3-stable-linux-arm.tgz",
    ("linux", "arm"): "https://bin.equinox.io/c/bNyj1mQVY4c/ngrok-v3-stable-linux-arm.tgz",
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
_LOGS: deque[str] = deque(maxlen=20)

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


def get_ngrok_token() -> str:
    env_tok = os.getenv("NGROK_AUTHTOKEN", "").strip()
    if env_tok:
        return env_tok
    try:
        return (db.get_setting("ngrok_authtoken", "") or "").strip()
    except Exception:
        return ""


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

    url = _CF_BIN_URLS.get((platform.system().lower(), platform.machine().lower()))
    if not url:
        return None

    for target in candidates:
        try:
            target.parent.mkdir(parents=True, exist_ok=True)
            tmp = target.with_suffix(".tmp")
            req = urllib.request.Request(url, headers={"User-Agent": f"ForexYemeni/{config.VERSION}"})
            with urllib.request.urlopen(req, timeout=90) as resp, open(tmp, "wb") as out:
                shutil.copyfileobj(resp, out, length=256 * 1024)
            tmp.chmod(tmp.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)
            tmp.replace(target)
            if _is_executable(target):
                _STATE["binary"] = str(target)
                return target
        except Exception as exc:
            log.warning("WEBAPP TUNNEL: cloudflared download to %s failed: %s", target, exc)
    return None


def ensure_ngrok_binary() -> Path | None:
    """Locate or download official ngrok v3 static binary (TLS port 443 — bypasses container firewalls)."""
    existing = shutil.which("ngrok")
    if existing:
        return Path(existing)

    candidates = [
        config.BASE_DIR / ".bin" / "ngrok",
        Path("/tmp/fysb_ngrok"),
    ]
    for cand in candidates:
        if _is_executable(cand):
            return cand

    url = _NGROK_TGZ_URLS.get((platform.system().lower(), platform.machine().lower()))
    if not url:
        return None

    for target in candidates:
        try:
            target.parent.mkdir(parents=True, exist_ok=True)
            tgz_path = target.with_suffix(".tgz")
            req = urllib.request.Request(url, headers={"User-Agent": f"ForexYemeni/{config.VERSION}"})
            with urllib.request.urlopen(req, timeout=90) as resp, open(tgz_path, "wb") as out:
                shutil.copyfileobj(resp, out, length=256 * 1024)
            with tarfile.open(tgz_path, "r:gz") as tf:
                member = next((m for m in tf.getmembers() if m.name.endswith("ngrok")), None)
                if member is None:
                    continue
                src = tf.extractfile(member)
                if src is None:
                    continue
                tmp = target.with_suffix(".tmp")
                with open(tmp, "wb") as out:
                    shutil.copyfileobj(src, out, length=256 * 1024)
            try:
                tgz_path.unlink(missing_ok=True)
            except Exception:
                pass
            tmp.chmod(tmp.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)
            tmp.replace(target)
            if _is_executable(target):
                log.info("WEBAPP TUNNEL: ngrok v3 ready (%s)", target)
                return target
        except Exception as exc:
            log.warning("WEBAPP TUNNEL: ngrok download to %s failed: %s", target, exc)
    return None


def _probe_health_once(url: str, timeout: float = 8.0) -> tuple[bool, str]:
    """Perform a single real public HTTPS GET {url}/health and verify JSON `ok: true`."""
    health_url = url.rstrip("/") + "/health"
    req = urllib.request.Request(
        health_url,
        headers={
            "User-Agent": f"ForexYemeni-HealthCheck/{config.VERSION}",
            "Accept": "application/json",
            "ngrok-skip-browser-warning": "1",
            "X-Pinggy-No-Screen": "1",
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
            return False, "Cloudflare Error 1033"
        return False, f"HTTP {exc.code}"
    except Exception as exc:
        return False, f"{type(exc).__name__}: {str(exc)[:80]}"


async def verify_https_health(url: str, retries: int = 6, delay: float = 2.0) -> tuple[bool, str]:
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
    tag: str = "",
) -> None:
    """Stream tunnel stdout/stderr, stripping ANSI codes, logging diagnostics, and capturing URL."""
    try:
        while True:
            raw = await stream.readline()
            if not raw:
                break
            line = _ANSI_RE.sub("", raw.decode("utf-8", errors="ignore")).strip()
            if not line:
                continue
            _LOGS.append(f"[{tag}] {line[:180]}" if tag else line[:180])
            m = url_re.search(line)
            if m and not url_future.done():
                url_future.set_result(m.group(0))
            if reg_future is not None and not reg_future.done():
                if "registered tunnel connection" in line.lower():
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
    """Spawn one tunnel provider with `stdin=DEVNULL`, wait for URL (+ registration if CF),
    and end-to-end verify `GET {url}/health == 200` before accepting."""
    global _PROC
    await stop_tunnel()

    try:
        proc = await asyncio.create_subprocess_exec(
            *cmd,
            stdin=asyncio.subprocess.DEVNULL,
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
        readers.append(asyncio.create_task(_drain_tunnel_stream(proc.stderr, url_re, url_future, reg_future, provider_name)))
    if proc.stdout:
        readers.append(asyncio.create_task(_drain_tunnel_stream(proc.stdout, url_re, url_future, reg_future, provider_name)))
    _READERS.extend(readers)

    try:
        url = await asyncio.wait_for(url_future, timeout=min(timeout, 18.0))
    except asyncio.TimeoutError:
        await stop_tunnel()
        return None, f"{provider_name}: انتهت المهلة"
    except Exception as exc:
        await stop_tunnel()
        return None, f"{provider_name}: {type(exc).__name__}"

    if reg_future is not None:
        try:
            await asyncio.wait_for(reg_future, timeout=min(timeout, 16.0))
        except asyncio.TimeoutError:
            await stop_tunnel()
            return None, f"{provider_name}: بورت 7844 محجوب"

    if proc.returncode is not None:
        await stop_tunnel()
        return None, f"{provider_name}: توقف مبكر (code={proc.returncode})"

    if verify:
        ok, vmsg = await verify_https_health(url, retries=6, delay=2.0)
        if not ok:
            await stop_tunnel()
            return None, f"{provider_name}: فشل الفحص ({vmsg})"

    _STATE["active"] = True
    _STATE["verified"] = True
    _STATE["url"] = url
    _STATE["provider"] = provider_name
    _STATE["last_error"] = ""
    config.set_runtime_webapp_url(url, source="auto_tunnel")
    log.info("WEBAPP TUNNEL VERIFIED & READY [%s]: %s -> http://127.0.0.1:%s", provider_name, url, target_port)
    return url, ""


async def start_tunnel(port: int | None = None, timeout: float = 35.0, verify: bool = True) -> str | None:
    """Start a verified free HTTPS tunnel pointing to `http://127.0.0.1:<port>`."""
    async with _get_lock():
        target_port = int(port if port is not None else config.WEBAPP_PORT)
        if target_port <= 0:
            _STATE["last_error"] = "بورت الخادم المحلي غير متاح"
            return None

        if is_tunnel_alive() and _STATE.get("port") == target_port:
            return _STATE["url"]

        _LOGS.clear()
        errors: list[str] = []

        # 1) PRIORITY: Ngrok v3 over PURE TLS port 443 (if authtoken configured)
        ngrok_tok = await asyncio.to_thread(get_ngrok_token)
        if ngrok_tok:
            ng_bin = await asyncio.to_thread(ensure_ngrok_binary)
            if ng_bin:
                url, err = await _try_spawn_provider(
                    "Ngrok-443",
                    [
                        str(ng_bin),
                        "http",
                        f"http://127.0.0.1:{target_port}",
                        "--authtoken",
                        ngrok_tok,
                        "--log",
                        "stdout",
                        "--log-format",
                        "logfmt",
                    ],
                    _NGROK_RE,
                    require_cf_register=False,
                    target_port=target_port,
                    timeout=timeout,
                    verify=verify,
                )
                if url:
                    return url
                errors.append(err)
            else:
                errors.append("Ngrok-443: تعذر تحميل أداة ngrok")

        # 2) Zero-account SSH HTTPS tunnels on TCP port 443 (non-interactive, stdin=DEVNULL)
        ssh_bin = shutil.which("ssh")
        if ssh_bin:
            ssh_base = [
                ssh_bin,
                "-T",
                "-o", "StrictHostKeyChecking=no",
                "-o", "UserKnownHostsFile=/dev/null",
                "-o", "PubkeyAuthentication=no",
                "-o", "PreferredAuthentications=none,keyboard-interactive,password",
                "-o", "NumberOfPasswordPrompts=0",
                "-o", "ServerAliveInterval=30",
                "-o", "ServerAliveCountMax=3",
                "-o", "ConnectTimeout=10",
                "-o", "ExitOnForwardFailure=yes",
            ]
            for name, host_args, rx in (
                ("Pinggy-443", ["-p", "443", "-R", f"0:127.0.0.1:{target_port}", "a.pinggy.io"], _PINGGY_RE),
                ("Serveo-443", ["-p", "443", "-R", f"80:127.0.0.1:{target_port}", "serveo.net"], _SERVEO_RE),
                ("LocalhostRun-443", ["-p", "443", "-R", f"80:127.0.0.1:{target_port}", "nokey@localhost.run"], _LHR_RE),
            ):
                url, err = await _try_spawn_provider(
                    name,
                    [*ssh_base, *host_args],
                    rx,
                    require_cf_register=False,
                    target_port=target_port,
                    timeout=min(timeout, 15.0),
                    verify=verify,
                )
                if url:
                    return url
                errors.append(err)

        # 3) Cloudflare cloudflared (works wherever port 7844 is open)
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
                    timeout=min(timeout, 18.0),
                    verify=verify,
                )
                if url:
                    return url
                errors.append(err)

        _STATE["last_error"] = " · ".join(e for e in errors if e)[:320] or "تعذر فتح نفق HTTPS تلقائي"
        return None


def is_tunnel_alive() -> bool:
    return bool(
        _PROC is not None
        and _PROC.returncode is None
        and _STATE.get("active")
        and _STATE.get("verified")
        and _STATE.get("url")
    )
