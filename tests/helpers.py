"""Shared test helpers — synthetic candle builders + Telegram mocks.

The test suite ALWAYS runs against a temporary database (never the production
DB) and fake credentials, using a dedicated temporary .env file pointed to via
DOTENV_PATH — config.py loads THAT file with override=True, so no inherited
shell value or ancestor .env can ever leak into tests (or vice versa).
"""
import os
import sys
import tempfile
from pathlib import Path

# Isolate the test environment BEFORE fysb is imported anywhere.
_TEST_DIR = Path(tempfile.mkdtemp(prefix="fysb_test_"))
_DOTENV = _TEST_DIR / ".env"
_DOTENV.write_text(
    "BOT_TOKEN=TEST:TOKEN_NOT_REAL\n"
    "ADMIN_ID=9001\n"
    f"DB_PATH={_TEST_DIR / 'test.db'}\n"
    "TWELVEDATA_API_KEY=\n"
    "DATABASE_URL=\n"
    "LOG_LEVEL=INFO\n",
    encoding="utf-8")
os.environ["DOTENV_PATH"] = str(_DOTENV)

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))


def make_candles(values: list[tuple[float, float, float, float]], start_hour: int = 8) -> list[dict]:
    """Build OHLC candles with plausible timestamps: (open, high, low, close)."""
    from datetime import datetime, timedelta
    out = []
    t0 = datetime(2026, 11, 18, start_hour, 0)
    for i, (o, h, l, c) in enumerate(values):
        out.append({"datetime": (t0 + timedelta(minutes=15 * i)).strftime("%Y-%m-%d %H:%M:%S"),
                    "open": o, "high": h, "low": l, "close": c, "volume": 1000 + i})
    return out


def uptrend_candles(n: int = 80, base: float = 100.0, step: float = 0.35) -> list[dict]:
    vals = []
    px = base
    for i in range(n):
        o = px
        c = px + step
        h = c + step * 0.4
        l = o - step * 0.25
        vals.append((o, h, l, c))
        px = c
    return make_candles(vals)


def downtrend_candles(n: int = 80, base: float = 200.0, step: float = 0.35) -> list[dict]:
    vals = []
    px = base
    for i in range(n):
        o = px
        c = px - step
        h = o + step * 0.25
        l = c - step * 0.4
        vals.append((o, h, l, c))
        px = c
    return make_candles(vals)


def ict_bullish_sweep_setup(base: float = 3500.0) -> list[dict]:
    """Range with a sell-side sweep then displacement up leaving a bullish FVG."""
    vals = []
    px = base
    # 30 bars of a mild range
    for i in range(30):
        o = px
        c = px + (0.3 if i % 2 == 0 else -0.3)
        vals.append((o, max(o, c) + 0.2, min(o, c) - 0.2, c))
        px = c
    # build a prior low
    low_level = px - 6.0
    vals.append((px, px + 0.5, low_level + 1.0, low_level + 1.5))
    px = low_level + 1.5
    # sweep: pierce below the low, close back above (sell-side liquidity taken)
    vals.append((px, px + 0.5, low_level - 1.2, low_level + 2.5))
    px = low_level + 2.5
    # displacement: strong bullish candle creating an FVG
    vals.append((px, px + 8.0, px - 0.3, px + 7.5))
    px += 7.5
    # gap candle pair (FVG between candle A high and candle C low)
    vals.append((px, px + 1.0, px - 0.2, px + 0.8))
    px += 0.8
    # a few continuation bars
    for i in range(6):
        vals.append((px, px + 0.8, px - 0.3, px + 0.5))
        px += 0.5
    return make_candles(vals)


def smc_bos_setup(base: float = 1.0800) -> list[dict]:
    """Downtrend then a CHOCH/BOS upward with HH/HL labels."""
    vals = []
    px = base + 0.0040
    for i in range(40):  # mild downtrend
        o = px
        c = px - 0.0008
        vals.append((o, o + 0.0004, c - 0.0004, c))
        px = c
    # higher low then break of structure up
    hl = px + 0.0010
    vals.append((px, px + 0.0012, px - 0.0006, hl))
    px = hl
    swing = px - 0.0020
    vals.append((px, px + 0.0012, swing, swing + 0.0030))
    px = swing + 0.0030
    vals.append((px, px + 0.0040, px - 0.0005, px + 0.0035))  # BOS close above prior high
    px += 0.0035
    for i in range(10):
        vals.append((px, px + 0.0008, px - 0.0004, px + 0.0004))
        px += 0.0004
    return make_candles(vals)


class MockBot:
    def __init__(self):
        self.sent = []
        self.photos = []
        self.docs = []
        self.fail_for = set()

    async def send_message(self, chat_id, text, **kwargs):
        if chat_id in self.fail_for:
            raise RuntimeError("blocked by user")
        self.sent.append({"chat_id": chat_id, "text": text, **kwargs})

    async def send_photo(self, chat_id, photo, caption="", **kwargs):
        self.photos.append({"chat_id": chat_id, "caption": caption})
        return True

    async def send_document(self, chat_id, doc, filename="", caption="", **kwargs):
        self.docs.append({"chat_id": chat_id, "filename": filename})
        return True


class MockMessage:
    def __init__(self, chat_id=777):
        self.chat_id = chat_id
        self.replies = []

    async def reply_text(self, text, reply_markup=None, parse_mode=None, **kw):
        self.replies.append({"text": text, "markup": reply_markup})
        return True


class MockQuery:
    def __init__(self, data: str, user_id: int):
        self.data = data
        self.from_user = type("U", (), {"id": user_id})()
        self.message = MockMessage()
        self._answered = False

    async def answer(self, *a, **kw):
        self._answered = True
        return True


class FakeUser:
    def __init__(self, uid, username="tester", full_name="Tester"):
        self.id = uid
        self.username = username
        self.full_name = full_name
        self.first_name = full_name


class MockContext:
    def __init__(self, bot=None):
        self.bot = bot or MockBot()
        self.user_data = {}
