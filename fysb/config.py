"""Central configuration — reads .env once, exposes typed constants.

Secrets are read here and never logged, never rendered to users, never embedded
in reports. Only boolean/presence checks are exposed (e.g. has_market_api()).

.env resolution (pinned, deterministic):
  * DOTENV_PATH env var (used by the test suite for airtight isolation), else
  * the project's own .env next to this package.
The chosen file wins over inherited shell values (override=True) so a stale
DATABASE_URL in the login environment can never silently hijack the engine.
"""
import os
import logging
from datetime import datetime, timezone
from pathlib import Path

from dotenv import load_dotenv


def now() -> str:
    """UTC ISO timestamp — single source of time for the whole platform."""
    return datetime.now(timezone.utc).isoformat()


# --- Paths (needed before .env loading) -------------------------------------
BASE_DIR = Path(__file__).resolve().parent.parent

_DOTENV_FILE = Path(os.getenv("DOTENV_PATH", "")).expanduser() \
    if os.getenv("DOTENV_PATH", "").strip() else (BASE_DIR / ".env")
load_dotenv(_DOTENV_FILE, override=True)

# --- Environment -----------------------------------------------------------
BOT_TOKEN = os.getenv("BOT_TOKEN", "").strip()
ADMIN_ID = int(os.getenv("ADMIN_ID", "0") or 0)
DB_PATH = os.getenv("DB_PATH", "database/forexyemeni.db").strip()
# PostgreSQL (Supabase) — when DATABASE_URL is set, the whole platform runs on
# real PostgreSQL; without it the classic SQLite engine is used. The URL holds
# credentials and is NEVER logged, never rendered, never included in reports.
DATABASE_URL = os.getenv("DATABASE_URL", "").strip()
if DATABASE_URL.lower().startswith("file:"):
    # Not a PostgreSQL URL — inherited environment noise; ignore it entirely.
    DATABASE_URL = ""
USE_PG = bool(DATABASE_URL)
LOG_LEVEL = os.getenv("LOG_LEVEL", "INFO").upper()
_MARKET_KEY_RAW = os.getenv("TWELVEDATA_API_KEY", "").strip()

# --- Price provider controls (env-tunable, REAL failover preserved) --------
# PRICE_PROVIDER: preferred provider tried FIRST when it serves the symbol
# class (values: BINANCE | BYBIT | YAHOO | GOLD_API | TWELVEDATA | empty=auto).
# The hidden failover chain always stays intact — preference reorders, it never
# removes a safety net. Unknown values are ignored silently (auto mode).
PRICE_PROVIDER = os.getenv("PRICE_PROVIDER", "").strip().upper()
# Base-URL overrides (geo/network workarounds) — empty => official endpoints.
BINANCE_BASE_URL = os.getenv("BINANCE_BASE_URL", "https://api.binance.com").strip().rstrip("/")
BYBIT_BASE_URL = os.getenv("BYBIT_BASE_URL", "https://api.bybit.com").strip().rstrip("/")

# Engine scopes (env-tunable, catalog-driven at runtime)
MARKET_SYMBOLS_ENV = os.getenv("MARKET_SYMBOLS", "XAU/USD")
MARKET_TIMEFRAME_ENV = os.getenv("MARKET_TIMEFRAME", "15min")
APP_TIMEFRAME_LABEL = os.getenv("APP_TIMEFRAME_LABEL", "M15").upper()
MARKET_SYMBOL_DELAY_SECONDS = float(os.getenv("MARKET_SYMBOL_DELAY_SECONDS", "6"))

# --- Brand / design system -------------------------------------------------
BRAND = "FOREXYEMENI"
BRAND_SHORT = "FYB"
BUILD_NAME = ""                # optional eyebrow next to the brand — ELITE naming removed
VERSION = "8.5.0"
BOT_SIGNATURE = "@ForexYemeni1_BOT"   # the bot identity carried on alerts
PAGE_SIZE = 8

# --- GitHub self-update (v6.9.0) --------------------------------------------
# The bot pulls the newest ForexYemeni1.zip from GitHub Releases by itself:
# at every boot (updater.boot_check, disable with AUTO_UPDATE=0) and via /update.
# The public repo needs NO token; if it ever turns private, set GITHUB_TOKEN in .env.
REPO_OWNER = os.getenv("REPO_OWNER", "ForexYemeni").strip()
REPO_NAME = os.getenv("REPO_NAME", "ForexYemeni_Bot").strip()
UPDATE_ASSET = os.getenv("UPDATE_ASSET", "ForexYemeni1.zip").strip()

# --- Telegram Mini App & Admin Web Panel (v7.0.0 / v7.2.0 Auto-HTTPS) -------
# Telegram requires a valid HTTPS URL (SSL) for Mini Apps.
# On Bot-Hosting / VPS without a domain, Auto-HTTPS Tunnel (`fysb/webapp/tunnel.py`)
# obtains a free Cloudflare HTTPS URL automatically and injects it at runtime!
def _detect_static_webapp_url() -> str:
    direct = os.getenv("WEBAPP_URL", "").strip().rstrip("/")
    if direct:
        return direct
    for env_k in ("RENDER_EXTERNAL_URL", "RAILWAY_PUBLIC_DOMAIN", "KOYEB_PUBLIC_DOMAIN"):
        val = os.getenv(env_k, "").strip().rstrip("/")
        if val:
            return val if val.startswith(("http://", "https://")) else f"https://{val}"
    return ""


WEBAPP_URL_STATIC = _detect_static_webapp_url()
WEBAPP_URL = WEBAPP_URL_STATIC
WEBAPP_TUNNEL_SOURCE = "env" if WEBAPP_URL_STATIC else ""
WEBAPP_HOST = os.getenv("WEBAPP_HOST", "0.0.0.0").strip()
try:
    _raw_port = (
        os.getenv("WEBAPP_PORT", "").strip()
        or os.getenv("SERVER_PORT", "").strip()
        or os.getenv("PORT", "").strip()
        or "8080"
    )
    WEBAPP_PORT = int(_raw_port)
except ValueError:
    WEBAPP_PORT = 8080
WEBAPP_ENABLED = os.getenv("WEBAPP_ENABLED", "1").strip() == "1"
WEBAPP_AUTO_TUNNEL = os.getenv("WEBAPP_AUTO_TUNNEL", "1").strip() == "1"
# v7.4.3: Built-in Royal GitHub Pages Front-Door — 100% free forever, zero extra accounts,
# works in Yemen & all Arab ISPs without VPN/DNS blocks, and bypasses Ngrok/Pinggy warning screens
# by passing the live HTTPS tunnel backend via ?api=<origin> + ngrok-skip-browser-warning header!
WEBAPP_GH_PAGES_URL = (
    os.getenv("WEBAPP_FRONTDOOR_URL", "").strip().rstrip("/")
    or f"https://{REPO_OWNER.lower()}.github.io/{REPO_NAME}"
)
# v7.4.5: Strictly OPT-IN (default OFF) so direct Ngrok / Pinggy / custom HTTPS URLs
# ALWAYS open directly as configured and are NEVER silently redirected to github.io!
WEBAPP_GH_PAGES_ENABLED = os.getenv("WEBAPP_GH_PAGES", "0").strip() == "1"

_INTERSTITIAL_HOSTS = (
    ".ngrok-free.dev",
    ".ngrok-free.app",
    ".ngrok.app",
    ".ngrok.io",
    ".pinggy.link",
    ".pinggy.online",
)


def needs_frontdoor_host(url: str) -> bool:
    """True when `url` is a free tunnel host (Ngrok/Pinggy) that shows a browser warning screen."""
    try:
        from urllib.parse import urlparse
        host = (urlparse((url or "").strip()).hostname or "").lower()
    except Exception:
        return False
    return bool(host and any(host.endswith(suf) for suf in _INTERSTITIAL_HOSTS))


def is_static_pages_url(url: str) -> bool:
    """True when `url` is a static GitHub/GitLab Pages host needing `?api=<backend>`."""
    try:
        from urllib.parse import urlparse
        host = (urlparse((url or "").strip()).hostname or "").lower()
    except Exception:
        return False
    return bool(host and (host.endswith(".github.io") or host.endswith(".gitlab.io")))


def set_runtime_webapp_url(url: str, source: str = "auto_tunnel") -> None:
    """Update live HTTPS Mini App URL in memory (used by Bot-Hosting auto-tunnel)."""
    global WEBAPP_URL, WEBAPP_TUNNEL_SOURCE
    clean = (url or "").strip().rstrip("/")
    if clean:
        WEBAPP_URL = clean
        WEBAPP_TUNNEL_SOURCE = source
    elif WEBAPP_URL_STATIC:
        WEBAPP_URL = WEBAPP_URL_STATIC
        WEBAPP_TUNNEL_SOURCE = "env"
    else:
        WEBAPP_URL = ""
        WEBAPP_TUNNEL_SOURCE = ""


def webapp_ready() -> bool:
    """True only when a valid HTTPS WEBAPP_URL is configured or auto-tunneled."""
    return bool(WEBAPP_ENABLED and WEBAPP_URL.lower().startswith("https://"))


def webapp_page_url(
    path: str = "/",
    query: dict[str, str] | None = None,
    *,
    base_url: str | None = None,
    backend_url: str | None = None,
    use_gh_pages: bool | None = None,
) -> str:
    """Build the optimal HTTPS Telegram Mini App URL.

    * When the active backend is an interstitial tunnel (Ngrok / Pinggy) and GitHub Pages
      Front-Door is enabled, automatically serves the static UI from GitHub Pages
      (`https://<owner>.github.io/<repo>/...`) with `?api=<backend>` — zero browser warning
      screen, zero ISP DNS blocks (`workers.dev`), zero extra accounts, free forever.
    * When a clean custom HTTPS domain (or non-interstitial tunnel) is active, returns it directly.
    """
    from urllib.parse import urlencode

    active = (base_url if base_url is not None else WEBAPP_URL).strip().rstrip("/")
    if not active:
        return ""
    p = "/" + (path or "/").lstrip("/")
    params: dict[str, str] = dict(query or {})

    gh_on = WEBAPP_GH_PAGES_ENABLED if use_gh_pages is None else bool(use_gh_pages)
    frontdoor = WEBAPP_GH_PAGES_URL.strip().rstrip("/")

    if gh_on and frontdoor.lower().startswith("https://") and needs_frontdoor_host(active):
        # Serve static UI from GitHub Pages with trailing slash on /admin/ to avoid 301 redirects
        gh_path = "/admin/" if p.rstrip("/") == "/admin" else "/"
        params["api"] = active
        return f"{frontdoor}{gh_path}?{urlencode(params)}"

    if is_static_pages_url(active):
        api_origin = (backend_url or "").strip().rstrip("/")
        if not api_origin:
            try:
                from fysb.webapp import tunnel as _tun
                api_origin = str(_tun.tunnel_status().get("url") or "").strip().rstrip("/")
            except Exception:
                api_origin = ""
        gh_path = "/admin/" if p.rstrip("/") == "/admin" else "/"
        if api_origin.lower().startswith("https://"):
            params["api"] = api_origin
        qs = f"?{urlencode(params)}" if params else ""
        return f"{active}{gh_path}{qs}"

    qs = f"?{urlencode(params)}" if params else ""
    return f"{active}{p}{qs}"

BACKUP_DIR = BASE_DIR / "backups"
LOG_DIR = BASE_DIR / "logs"
DB_FILE = Path(DB_PATH) if Path(DB_PATH).is_absolute() else (BASE_DIR / DB_PATH)

for _d in (BACKUP_DIR, LOG_DIR, DB_FILE.parent):
    _d.mkdir(parents=True, exist_ok=True)

# --- Logging (never prints env values) --------------------------------------
logging.basicConfig(
    level=getattr(logging, LOG_LEVEL, logging.INFO),
    format="%(asctime)s | %(levelname)s | %(name)s | %(message)s",
    handlers=[
        logging.StreamHandler(),
        logging.FileHandler(LOG_DIR / "fysb.log", encoding="utf-8"),
    ],
)
for _noisy in ("httpx", "httpcore", "telegram.ext.Updater", "telegram.Bot"):
    logging.getLogger(_noisy).setLevel(logging.WARNING)
log = logging.getLogger("FOREXYEMENI")


def has_market_api() -> bool:
    """Presence check only — the key itself is never exposed."""
    return bool(_MARKET_KEY_RAW)


def market_api_key() -> str:
    return _MARKET_KEY_RAW


def validate_boot() -> list[str]:
    """Return a list of boot-blocking problems (human readable, no secrets)."""
    problems = []
    if not BOT_TOKEN:
        problems.append("BOT_TOKEN missing from .env")
    if not ADMIN_ID:
        problems.append("ADMIN_ID missing from .env")
    return problems
