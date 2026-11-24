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
VERSION = "6.20.2"
BOT_SIGNATURE = "@ForexYemeni1_BOT"   # the bot identity carried on alerts
PAGE_SIZE = 8

# --- GitHub self-update (v6.9.0) --------------------------------------------
# The bot pulls the newest ForexYemeni1.zip from GitHub Releases by itself:
# at every boot (updater.boot_check, disable with AUTO_UPDATE=0) and via /update.
# The public repo needs NO token; if it ever turns private, set GITHUB_TOKEN in .env.
REPO_OWNER = os.getenv("REPO_OWNER", "ForexYemeni").strip()
REPO_NAME = os.getenv("REPO_NAME", "ForexYemeni_Bot").strip()
UPDATE_ASSET = os.getenv("UPDATE_ASSET", "ForexYemeni1.zip").strip()

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
