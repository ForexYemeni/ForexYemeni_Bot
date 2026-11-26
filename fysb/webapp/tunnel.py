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
import signal
import stat
import tarfile
import time
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


_TOKEN_MAP = {
    "ngrok_authtoken": "NGROK_AUTHTOKEN",
    "ngrok_domain": "NGROK_DOMAIN",
    "cf_tunnel_token": "CLOUDFLARE_TUNNEL_TOKEN",
    "pinggy_token": "PINGGY_TOKEN",
}


def _tokens_file_path() -> Path:
    """Path to local JSON backup for tunnel tokens (survives DB resets & /update).

    Isolated automatically to the DB parent directory when running inside
    temporary test folders (`tmp_path`) so tests never pollute the repo root.
    """
    try:
        base = Path(config.BASE_DIR).resolve()
        dbf = Path(getattr(config, "DB_FILE", base / "database" / "forexyemeni.db")).resolve()
        if not dbf.is_relative_to(base):
            return dbf.parent / "tunnel_tokens.json"
    except Exception:
        pass
    return Path(config.BASE_DIR) / "tunnel_tokens.json"


def _pid_file_path() -> Path:
    return _tokens_file_path().parent / ".tunnel.pid"


def _read_tokens_file() -> dict[str, str]:
    p = _tokens_file_path()
    try:
        if p.is_file():
            raw = json.loads(p.read_text(encoding="utf-8"))
            if isinstance(raw, dict):
                return {str(k): str(v).strip() for k, v in raw.items() if v is not None}
    except Exception:
        pass
    return {}


def _write_tokens_file(data: dict[str, str]) -> None:
    p = _tokens_file_path()
    try:
        p.parent.mkdir(parents=True, exist_ok=True)
        clean = {k: str(v).strip() for k, v in data.items() if str(v or "").strip()}
        tmp = p.with_suffix(".tmp")
        tmp.write_text(json.dumps(clean, ensure_ascii=False, indent=2), encoding="utf-8")
        try:
            os.chmod(tmp, 0o600)
        except OSError:
            pass
        tmp.replace(p)
    except Exception as exc:
        log.debug("tunnel_tokens.json write skipped: %s", exc)


def _sync_dotenv_key(env_key: str, value: str) -> None:
    """Safely upsert/clear `env_key` in `.env` so tokens survive /update & restarts 100%."""
    if not env_key or not re.fullmatch(r"[A-Z0-9_]+", env_key):
        return
    try:
        dotenv_p = Path(getattr(config, "_DOTENV_FILE", Path(config.BASE_DIR) / ".env")).resolve()
        base = Path(config.BASE_DIR).resolve()
        dbf = Path(getattr(config, "DB_FILE", base / "database" / "forexyemeni.db")).resolve()
        # Never mutate the repo's real .env when running isolated pytest fixtures in /tmp
        explicit_dotenv = bool(os.getenv("DOTENV_PATH", "").strip())
        if not explicit_dotenv and not getattr(config, "USE_PG", False) and not dbf.is_relative_to(base):
            return
        if not dotenv_p.is_file() and not explicit_dotenv:
            return
        lines = dotenv_p.read_text(encoding="utf-8").splitlines() if dotenv_p.is_file() else []
        clean_val = str(value or "").replace("\r", "").replace("\n", "").strip()
        prefix = f"{env_key}="
        out_lines: list[str] = []
        found = False
        for ln in lines:
            stripped = ln.lstrip()
            if stripped.startswith(prefix) or stripped.startswith(f"export {prefix}"):
                found = True
                if clean_val:
                    out_lines.append(f"{env_key}={clean_val}")
                continue
            out_lines.append(ln)
        if not found and clean_val:
            out_lines.append(f"{env_key}={clean_val}")
        dotenv_p.parent.mkdir(parents=True, exist_ok=True)
        tmp = dotenv_p.with_suffix(".env.tmp")
        tmp.write_text("\n".join(out_lines) + ("\n" if out_lines else ""), encoding="utf-8")
        try:
            os.chmod(tmp, 0o600)
        except OSError:
            pass
        tmp.replace(dotenv_p)
    except Exception as exc:
        log.debug(".env sync skipped for %s: %s", env_key, exc)


def save_tunnel_token(db_key: str, value: str) -> None:
    """v8.0.2: Persist tunnel token/domain in ALL 3 layers (DB + tunnel_tokens.json + .env + env).

    Guarantees Ngrok/Cloudflare/Pinggy tokens NEVER vanish across `/update`,
    container restarts, DB restores, or SQLite <-> PostgreSQL switches.
    """
    val = str(value or "").strip()
    try:
        db.set_setting(db_key, val)
    except Exception as exc:
        log.warning("save_tunnel_token DB write failed (%s): %s", db_key, exc)
    data = _read_tokens_file()
    if val:
        data[db_key] = val
    else:
        data.pop(db_key, None)
    _write_tokens_file(data)
    env_key = _TOKEN_MAP.get(db_key, "")
    if env_key:
        if val:
            os.environ[env_key] = val
        else:
            os.environ.pop(env_key, None)
        _sync_dotenv_key(env_key, val)


def clear_tunnel_tokens() -> None:
    """Clear all saved tunnel tokens from DB, tunnel_tokens.json, os.environ, and .env."""
    for db_key in _TOKEN_MAP:
        save_tunnel_token(db_key, "")


def _read_persisted_token(db_key: str, *env_keys: str) -> str:
    """Read token with 3-tier fallback & self-heal: DB -> env -> tunnel_tokens.json."""
    # 1) Active environment (.env / host env)
    for ek in env_keys:
        v = (os.getenv(ek, "") or "").strip()
        if v:
            # Self-heal DB if empty so Admin panel & backups stay synchronized
            try:
                if not (db.get_setting(db_key, "") or "").strip():
                    db.set_setting(db_key, v)
            except Exception:
                pass
            return v
    # 2) Database settings
    try:
        dv = (db.get_setting(db_key, "") or "").strip()
        if dv:
            # Mirror into local backup file once if missing (upgrades old DB-only tokens automatically!)
            local_map = _read_tokens_file()
            if local_map.get(db_key) != dv:
                local_map[db_key] = dv
                _write_tokens_file(local_map)
                ek0 = _TOKEN_MAP.get(db_key, "")
                if ek0:
                    _sync_dotenv_key(ek0, dv)
            return dv
    except Exception:
        pass
    # 3) Preserved local tunnel_tokens.json fallback (survives DB resets / migrations)
    fv = (_read_tokens_file().get(db_key) or "").strip()
    if fv:
        try:
            db.set_setting(db_key, fv)
        except Exception:
            pass
        ek0 = _TOKEN_MAP.get(db_key, "")
        if ek0:
            os.environ[ek0] = fv
    return fv


def _record_tunnel_pid(pid: int) -> None:
    p = _pid_file_path()
    try:
        if pid and pid > 0:
            p.parent.mkdir(parents=True, exist_ok=True)
            p.write_text(str(int(pid)), encoding="utf-8")
        else:
            p.unlink(missing_ok=True)
    except Exception:
        pass


def _kill_pid_if_tunnel(pid: int, wait_sec: float = 2.5) -> None:
    """Safely terminate an old ngrok/cloudflared/ssh child PID and wait for it to release its session."""
    if not pid or pid <= 1 or pid == os.getpid():
        return
    # On Linux, confirm the PID actually belongs to a tunnel binary before signaling
    cmdline_p = Path(f"/proc/{pid}/cmdline")
    if Path("/proc").is_dir():
        try:
            if not cmdline_p.exists():
                return
            cmd_txt = cmdline_p.read_bytes().decode("utf-8", "replace").lower()
            if not any(k in cmd_txt for k in ("ngrok", "cloudflared", "ssh")):
                return
        except Exception:
            return
    try:
        os.kill(pid, signal.SIGTERM)
    except ProcessLookupError:
        return
    except Exception:
        return
    deadline = time.monotonic() + max(0.2, float(wait_sec))
    while time.monotonic() < deadline:
        try:
            os.kill(pid, 0)
        except ProcessLookupError:
            return
        except Exception:
            return
        time.sleep(0.1)
    try:
        os.kill(pid, signal.SIGKILL)
    except Exception:
        pass


def _cleanup_stale_tunnel_pid() -> None:
    p = _pid_file_path()
    try:
        if p.is_file():
            raw = p.read_text(encoding="utf-8").strip()
            if raw.isdigit():
                _kill_pid_if_tunnel(int(raw), wait_sec=2.5)
            p.unlink(missing_ok=True)
    except Exception:
        pass


def terminate_tunnel_sync(wait_sec: float = 2.5) -> None:
    """v8.0.2: Synchronous clean shutdown for updater.restart() BEFORE os.execve().

    Fixes the v7 bug where updater looked for non-existent `_STATE['proc']`,
    leaving orphan `ngrok` agents holding the free 1-session slot (ERR_NGROK_108).
    """
    global _PROC
    pids: list[int] = []
    proc = _PROC
    _PROC = None
    if proc is not None:
        if getattr(proc, "pid", None):
            pids.append(int(proc.pid))
        if getattr(proc, "returncode", None) is None:
            try:
                proc.terminate()
            except Exception:
                pass
    st_pid = int(_STATE.get("pid") or 0)
    if st_pid and st_pid not in pids:
        pids.append(st_pid)
    _STATE["active"] = False
    _STATE["verified"] = False
    _STATE["pid"] = 0
    for pid in pids:
        _kill_pid_if_tunnel(pid, wait_sec=wait_sec)
    _cleanup_stale_tunnel_pid()


def get_ngrok_token() -> str:
    return _read_persisted_token("ngrok_authtoken", "NGROK_AUTHTOKEN")


def get_ngrok_domain() -> str:
    """Optional Ngrok free static domain (e.g. `my-bot.ngrok-free.app`)."""
    raw = _read_persisted_token("ngrok_domain", "NGROK_DOMAIN")
    raw = raw.replace("https://", "").replace("http://", "").split("/", 1)[0].strip()
    return raw


def get_cf_tunnel_token() -> str:
    """Cloudflare Zero Trust Named Tunnel token (`eyJhIjo...`)."""
    return _read_persisted_token("cf_tunnel_token", "CLOUDFLARE_TUNNEL_TOKEN", "CF_TUNNEL_TOKEN")


def get_pinggy_token() -> str:
    """Optional Pinggy token (`<token>@a.pinggy.io`)."""
    return _read_persisted_token("pinggy_token", "PINGGY_TOKEN")


_URL_EXTRACT_RE = re.compile(r"https://[^\s\)\]\>\"'<>]+", re.IGNORECASE)
_CF_TOKEN_RE = re.compile(r"\beyJ[A-Za-z0-9_-]{30,}\b")


def extract_https_url(raw: str) -> str:
    """Extract a clean `https://...` URL even if pasted with markdown brackets or surrounding text."""
    m = _URL_EXTRACT_RE.search(raw or "")
    if not m:
        return ""
    return m.group(0).rstrip(".,;:/)")


def detect_and_parse_tunnel_input(raw: str) -> dict:
    """Smart universal parser for ANY link or tunnel token an Admin pastes:
      - `clear` / `off` / `مسح`
      - Cloudflare Zero Trust Tunnel Token (`eyJ...`, or full `cloudflared ... --token eyJ...`)
      - Pinggy Token (`pinggy:<tok>` or `<tok>@a.pinggy.io`)
      - Ngrok Authtoken (`ngrok config add-authtoken ...` or raw token + optional static domain)
      - Any custom `https://...` URL (including `*.workers.dev`, custom domain, VPS, etc.)
    """
    txt = (raw or "").strip()
    if not txt:
        return {"kind": "invalid", "error": "إدخال فارغ"}
    if txt.lower() in ("clear", "off", "delete", "مسح", "حذف"):
        return {"kind": "clear"}

    # 1) Cloudflare Zero Trust Named Tunnel JWT Token (even if pasted inside full `cloudflared` command!)
    m_cf = _CF_TOKEN_RE.search(txt)
    if m_cf:
        url_extra = extract_https_url(txt)
        return {"kind": "cloudflare_token", "token": m_cf.group(0), "url": url_extra}

    # 2) Explicit Pinggy token (`pinggy:TOKEN` or `TOKEN@a.pinggy.io`)
    if txt.lower().startswith("pinggy:"):
        tok = txt.split(":", 1)[1].strip().split()[0]
        if tok:
            return {"kind": "pinggy_token", "token": tok}
    m_pg = re.search(r"([A-Za-z0-9_-]{6,})@a\.pinggy\.io", txt)
    if m_pg:
        return {"kind": "pinggy_token", "token": m_pg.group(1)}

    # 3) Custom HTTPS URL (unless it's a Ngrok token + static https://...ngrok-free.app pair)
    url_found = extract_https_url(txt)
    # Strip known ngrok CLI prefixes if user pasted the full command from dashboard
    cleaned = re.sub(r"^\s*ngrok\s+config\s+add-authtoken\s+", "", txt, flags=re.IGNORECASE).strip()
    parts = [p.strip() for p in cleaned.replace("|", " ").split() if p.strip()]

    # If the ENTIRE input is just an HTTPS URL (or markdown link), treat as custom_url
    if url_found and (
        txt.lower().startswith("https://")
        or txt.startswith("[https://")
        or len(parts) == 1
    ):
        return {"kind": "custom_url", "url": url_found}

    # 4) Ngrok authtoken (+ optional static domain as 2nd token)
    if parts and len(parts[0]) >= 20 and not parts[0].lower().startswith(("http://", "https://")):
        tok = parts[0]
        dom = ""
        if len(parts) >= 2:
            dom = parts[1].replace("https://", "").replace("http://", "").split("/", 1)[0].strip()
        return {"kind": "ngrok_token", "token": tok, "domain": dom}

    # 5) Short standalone token (e.g. Pinggy token without prefix)
    if len(parts) == 1 and 8 <= len(parts[0]) < 20 and re.fullmatch(r"[A-Za-z0-9_-]+", parts[0]):
        return {"kind": "pinggy_token", "token": parts[0]}

    if url_found:
        return {"kind": "custom_url", "url": url_found}

    if "http://" in txt.lower():
        return {"kind": "http_not_https", "error": "تيليجرام يقبل روابط https:// فقط (http:// مرفوض رسمياً من تيليجرام)"}

    return {"kind": "invalid", "error": "لم يتم التعرف على الصيغة — أرسل رابط https:// أو توكن Cloudflare / Ngrok / Pinggy"}


def is_proxy_frontdoor_url(url: str) -> bool:
    """True when custom URL needs the local tunnel kept alive behind it (e.g. GitHub Pages / Cloudflare Worker / Named Tunnel / Ngrok)."""
    u = (url or "").strip().lower()
    if not u.startswith("https://"):
        return False
    if ".workers.dev" in u or ".pages.dev" in u or ".github.io" in u or ".gitlab.io" in u:
        return True
    return bool(get_cf_tunnel_token() or get_ngrok_token() or get_pinggy_token())


def build_cloudflare_worker_code(origin_url: str = "") -> str:
    """Return copy-paste Cloudflare Worker JS that hides Ngrok/Pinggy warning screens permanently."""
    orig = (origin_url or "https://YOUR-STATIC-DOMAIN.ngrok-free.app").strip().rstrip("/")
    return (
        f'const ORIGIN = "{orig}";\n'
        "export default {\n"
        "  async fetch(request) {\n"
        "    const u = new URL(request.url);\n"
        "    const headers = new Headers(request.headers);\n"
        '    headers.set("ngrok-skip-browser-warning", "1");\n'
        '    headers.set("X-Pinggy-No-Screen", "1");\n'
        '    headers.delete("host");\n'
        '    const hasBody = request.method !== "GET" && request.method !== "HEAD";\n'
        "    const body = hasBody ? await request.arrayBuffer() : undefined;\n"
        "    const r = await fetch(ORIGIN + u.pathname + u.search, {\n"
        '      method: request.method, headers, body, redirect: "manual"\n'
        "    });\n"
        "    const rh = new Headers(r.headers);\n"
        '    rh.set("Cache-Control", "no-store");\n'
        "    return new Response(r.body, { status: r.status, headers: rh });\n"
        "  }\n"
        "};"
    )


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
            if "hello world" in raw.lower():
                return False, "الرابط يعيد صفحة Hello World الافتراضية من Cloudflare Worker — لم يتم لصق كود البروكسي بعد"
            return False, "استجابة غير متوقعة (ليست JSON من خادم التطبيق)"
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
    await asyncio.to_thread(_cleanup_stale_tunnel_pid)
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
    _record_tunnel_pid(proc.pid or 0)

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
    # Preserve Cloudflare Worker front-door URL when configured (tunnel runs cleanly as its backend!)
    try:
        custom_db = (db.get_setting("webapp_custom_url", "") or "").strip().rstrip("/")
    except Exception:
        custom_db = ""
    if custom_db.lower().startswith("https://") and not config.WEBAPP_URL_STATIC:
        config.set_runtime_webapp_url(custom_db, source="worker_proxy")
    else:
        config.set_runtime_webapp_url(url, source="auto_tunnel")
    log.info("WEBAPP TUNNEL VERIFIED & READY [%s]: %s -> http://127.0.0.1:%s", provider_name, url, target_port)
    return url, ""


async def _try_spawn_cf_named_tunnel(
    bin_path: Path,
    cf_token: str,
    target_port: int,
    timeout: float,
) -> tuple[str | None, str]:
    """Start a Cloudflare Zero Trust permanent Named Tunnel (`cloudflared tunnel run --token ...`)."""
    global _PROC
    await stop_tunnel()
    cmd = [
        str(bin_path),
        "tunnel",
        "--no-autoupdate",
        "--edge-ip-version",
        "4",
        "--protocol",
        "http2",
        "run",
        "--token",
        cf_token,
    ]
    try:
        proc = await asyncio.create_subprocess_exec(
            *cmd,
            stdin=asyncio.subprocess.DEVNULL,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
    except Exception as exc:
        return None, f"Cloudflare-Token: تعذر التشغيل ({type(exc).__name__})"

    _PROC = proc
    _STATE["pid"] = proc.pid or 0
    _STATE["port"] = target_port
    _STATE["provider"] = "Cloudflare-Token"
    _record_tunnel_pid(proc.pid or 0)

    loop = asyncio.get_running_loop()
    dummy_url_fut: asyncio.Future = loop.create_future()
    reg_fut: asyncio.Future = loop.create_future()
    readers = []
    if proc.stderr:
        readers.append(asyncio.create_task(_drain_tunnel_stream(proc.stderr, _TRYCF_RE, dummy_url_fut, reg_fut, "CF-Token")))
    if proc.stdout:
        readers.append(asyncio.create_task(_drain_tunnel_stream(proc.stdout, _TRYCF_RE, dummy_url_fut, reg_fut, "CF-Token")))
    _READERS.extend(readers)

    try:
        await asyncio.wait_for(reg_fut, timeout=min(timeout, 20.0))
    except asyncio.TimeoutError:
        await stop_tunnel()
        return None, "Cloudflare-Token: انتهت المهلة (قد يكون بورت 7844 محجوباً على الاستضافة)"

    if proc.returncode is not None:
        await stop_tunnel()
        return None, f"Cloudflare-Token: توقف مبكر (code={proc.returncode})"

    try:
        custom_db = (db.get_setting("webapp_custom_url", "") or "").strip().rstrip("/")
    except Exception:
        custom_db = ""
    live_url = custom_db if custom_db.lower().startswith("https://") else (config.WEBAPP_URL or "")
    _STATE["active"] = True
    _STATE["verified"] = True
    _STATE["url"] = live_url or "cloudflare-named-tunnel"
    _STATE["provider"] = "Cloudflare-Token"
    _STATE["last_error"] = ""
    if live_url:
        config.set_runtime_webapp_url(live_url, source="worker_proxy")
    log.info("WEBAPP TUNNEL READY [Cloudflare-Token]: %s -> http://127.0.0.1:%s", _STATE["url"], target_port)
    return _STATE["url"], ""


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

        # 0) Cloudflare Zero Trust Named Tunnel Token (if configured — 100% free forever, zero warnings!)
        cf_tok = await asyncio.to_thread(get_cf_tunnel_token)
        if cf_tok:
            cf_bin = await asyncio.to_thread(ensure_cloudflared_binary)
            if cf_bin:
                url, err = await _try_spawn_cf_named_tunnel(cf_bin, cf_tok, target_port, timeout)
                if url:
                    return url
                errors.append(err)
            else:
                errors.append("Cloudflare-Token: تعذر تحميل cloudflared")

        # 1) PRIORITY: Ngrok v3 over PURE TLS port 443 (with optional free permanent --domain)
        ngrok_tok = await asyncio.to_thread(get_ngrok_token)
        if ngrok_tok:
            ng_bin = await asyncio.to_thread(ensure_ngrok_binary)
            if ng_bin:
                ng_dom = await asyncio.to_thread(get_ngrok_domain)
                ng_cmd = [
                    str(ng_bin),
                    "http",
                    f"http://127.0.0.1:{target_port}",
                    "--authtoken",
                    ngrok_tok,
                    "--log",
                    "stdout",
                    "--log-format",
                    "logfmt",
                ]
                if ng_dom:
                    ng_cmd.extend(["--domain", ng_dom])
                url, err = await _try_spawn_provider(
                    "Ngrok-443",
                    ng_cmd,
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

        # 2) Authenticated Pinggy Token on TCP port 443 (if configured)
        ssh_bin = shutil.which("ssh")
        pinggy_tok = await asyncio.to_thread(get_pinggy_token)
        if ssh_bin and pinggy_tok:
            ssh_tok_cmd = [
                ssh_bin, "-T",
                "-o", "StrictHostKeyChecking=no",
                "-o", "UserKnownHostsFile=/dev/null",
                "-o", "PubkeyAuthentication=no",
                "-o", "PreferredAuthentications=none,keyboard-interactive,password",
                "-o", "NumberOfPasswordPrompts=0",
                "-o", "ServerAliveInterval=30",
                "-o", "ServerAliveCountMax=3",
                "-o", "ConnectTimeout=10",
                "-o", "ExitOnForwardFailure=yes",
                "-p", "443",
                "-R", f"0:127.0.0.1:{target_port}",
                f"{pinggy_tok}@a.pinggy.io",
            ]
            url, err = await _try_spawn_provider(
                "Pinggy-Token-443",
                ssh_tok_cmd,
                _PINGGY_RE,
                require_cf_register=False,
                target_port=target_port,
                timeout=min(timeout, 18.0),
                verify=verify,
            )
            if url:
                return url
            errors.append(err)

        # 3) Zero-account SSH HTTPS tunnels on TCP port 443 (Serveo & LocalhostRun FIRST — NO warning screen!)
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
                ("Serveo-443", ["-p", "443", "-R", f"80:127.0.0.1:{target_port}", "serveo.net"], _SERVEO_RE),
                ("LocalhostRun-443", ["-p", "443", "-R", f"80:127.0.0.1:{target_port}", "nokey@localhost.run"], _LHR_RE),
                ("Pinggy-443", ["-p", "443", "-R", f"0:127.0.0.1:{target_port}", "a.pinggy.io"], _PINGGY_RE),
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
