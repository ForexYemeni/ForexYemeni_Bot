"""Central configuration — reads .env once, exposes typed constants.

Secrets are read here and never logged, never rendered to users, never embedded
in reports. Only boolean/presence checks are exposed (e.g. has_market_api()).
"""
import os
import logging
from datetime import datetime, timezone
from pathlib import Path

from dotenv import load_dotenv


def now() -> str:
    """UTC ISO timestamp — single source of time for the whole platform."""
    return datetime.now(timezone.utc).isoformat()


load_dotenv()

# --- Environment -----------------------------------------------------------
BOT_TOKEN = os.getenv("BOT_TOKEN", "").strip()
ADMIN_ID = int(os.getenv("ADMIN_ID", "0") or 0)
DB_PATH = os.getenv("DB_PATH", "database/forexyemeni.db").strip()
LOG_LEVEL = os.getenv("LOG_LEVEL", "INFO").upper()
_MARKET_KEY_RAW = os.getenv("TWELVEDATA_API_KEY", "").strip()

# Engine scopes (env-tunable, catalog-driven at runtime)
MARKET_SYMBOLS_ENV = os.getenv("MARKET_SYMBOLS", "XAU/USD")
MARKET_TIMEFRAME_ENV = os.getenv("MARKET_TIMEFRAME", "15min")
APP_TIMEFRAME_LABEL = os.getenv("APP_TIMEFRAME_LABEL", "M15").upper()
MARKET_SYMBOL_DELAY_SECONDS = float(os.getenv("MARKET_SYMBOL_DELAY_SECONDS", "6"))

# --- Brand / design system -------------------------------------------------
BRAND = "FOREXYEMENI"
BRAND_SHORT = "FYB"
BUILD_NAME = ""                # optional eyebrow next to the brand — ELITE naming removed
VERSION = "6.4.0"
BOT_SIGNATURE = "@ForexYemeni1_BOT"   # the bot identity carried on alerts
PAGE_SIZE = 8

# --- Paths -----------------------------------------------------------------
BASE_DIR = Path(__file__).resolve().parent.parent
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
