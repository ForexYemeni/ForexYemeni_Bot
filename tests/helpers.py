"""Shared test helpers — synthetic candle builders + Telegram mocks.

The test suite ALWAYS runs against a temporary database (never the production
DB) and fake credentials, using a dedicated temporary .env file pointed to via
DOTENV_PATH — config.py loads THAT file with override=True, so no inherited
shell value or ancestor .env can ever leak into tests (or vice versa).
"""
import datetime as _dtmod
import itertools
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


class FrozenFridayDT(_dtmod.datetime):
    """v8.14.10 — the time-sensitive mechanics tests must NEVER depend on the
    real calendar. staleness_limit widens on weekends (honest for production —
    Friday data IS fresh on Saturday) but poisons relative-timestamp tests the
    moment CI crosses the Fri→Sat boundary (the v8.14.9→v8.14.10 CI lesson:
    green Fri 23:50 UTC, red Sat 01:15 UTC, zero code difference). Everything
    in the time-pinned suites runs on FRIDAY NOON UTC — mid-session, 3 days
    from any weekend widening. Only `now()` is frozen; every other classmethod
    (fromisoformat/fromtimestamp/…) stays the real datetime machinery."""

    @classmethod
    def now(cls, tz=None):
        return _dtmod.datetime(2026, 11, 27, 12, 0, 0, tzinfo=tz or _dtmod.timezone.utc)


def frozen_market_clock():
    """Patch market_data's module clock to the frozen Friday. staleness_limit,
    fair_age and market_state all read `datetime.now(timezone.utc)` from that
    module — one pin, every verdict deterministic forever."""
    from unittest.mock import patch
    from fysb.engines import market_data as md
    return patch.object(md, "datetime", FrozenFridayDT)


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


def trend_pullback_setup(base: float = 2500.0, step: float = 2.0) -> list[dict]:
    """TREND fixture: EMA stack up -> pullback to the EMA21 value zone -> strong resumption."""
    vals = []
    px = base
    for i in range(50):                       # establish the uptrend stack
        o, c = px, px + step
        vals.append((o, c + step * 0.4, o - step * 0.25, c))
        px = c
    # pullback: 5 candles drifting down INTO the value zone (no structure break)
    for i in range(5):
        o = px
        c = px - step * 1.1
        vals.append((o, o + step * 0.2, c - step * 0.5, c))
        px = c
    # resumption: a strong bullish candle reclaiming the trend
    vals.append((px, px + step * 2.4, px - step * 0.2, px + step * 2.2))
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
    vals.append((px, px + 1.2, low_level - 1.2, low_level + 2.5))
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


_MSG_SEQ = itertools.count(555000)


class MockChat:
    def __init__(self, chat_id=777):
        self.id = chat_id


class MockBot:
    """Faithful enough: send_message returns a message object with message_id
    (the living-panel registry consumes it), records every call."""

    def __init__(self):
        self.sent = []
        self.photos = []
        self.docs = []
        self.fail_for = set()

    async def send_message(self, chat_id, text, **kwargs):
        if chat_id in self.fail_for:
            raise RuntimeError("blocked by user")
        mid = next(_MSG_SEQ)
        self.sent.append({"chat_id": chat_id, "text": text,
                          "message_id": mid, **kwargs})
        return type("SentMsg", (), {"message_id": mid, "chat": MockChat(chat_id)})()

    async def send_photo(self, chat_id, photo, caption="", **kwargs):
        self.photos.append({"chat_id": chat_id, "caption": caption})
        return True

    async def send_document(self, chat_id, doc, filename="", caption="", **kwargs):
        self.docs.append({"chat_id": chat_id, "filename": filename})
        return True


class MockMessage:
    """Production-realistic message: has chat/message_id/edit_text/delete —
    exactly the surface the living-panel render() contract relies on.
    (The v6.8.1 incident hid here: reply-only mocks never exercised the
    edit-in-place path the bot actually runs in production.)"""

    def __init__(self, chat_id=777, message_id=None):
        self.chat_id = chat_id
        self.chat = MockChat(chat_id)
        self.message_id = message_id if message_id is not None else next(_MSG_SEQ)
        self.replies = []   # reply_text records (backward compat)
        self.edits = []     # edit_text records (living panel)
        self.deleted = False

    async def reply_text(self, text, reply_markup=None, parse_mode=None, **kw):
        self.replies.append({"text": text, "markup": reply_markup})
        return self

    async def edit_text(self, text, reply_markup=None, parse_mode=None, **kw):
        self.edits.append({"text": text, "markup": reply_markup})
        return self

    async def delete(self):
        self.deleted = True
        return True


class InaccessibleMock:
    """PTB v22 reality for panels older than 48h: chat + message_id ONLY —
    no edit_text, no reply_text, no delete. (v6.8.1 died on these.)"""

    def __init__(self, chat_id=777, message_id=None):
        self.chat = MockChat(chat_id)
        self.message_id = message_id if message_id is not None else next(_MSG_SEQ)


class MockQuery:
    def __init__(self, data: str, user_id: int):
        self.data = data
        self.from_user = type("U", (), {"id": user_id})()
        self.message = MockMessage()
        self._answered = False

    async def answer(self, *a, **kw):
        self._answered = True
        return True


def page_for(q, ctx) -> str:
    """The page the user actually SEES after the callback (living-panel
    contract): the last fresh page delivered to the tapped chat, or an
    in-place edit, or a legacy reply — never a silent no-op."""
    try:
        cid = q.message.chat.id
    except Exception:
        cid = None
    sent = getattr(getattr(ctx, "bot", None), "sent", None) or []
    for m in reversed(sent):
        if m.get("chat_id") == cid:
            return str(m.get("text", ""))
    if getattr(q.message, "edits", None):
        return str(q.message.edits[-1]["text"])
    if getattr(q.message, "replies", None):
        return str(q.message.replies[-1]["text"])
    return ""


def page_markup_for(q, ctx):
    """The inline keyboard of the page the user sees (living-panel contract)."""
    try:
        cid = q.message.chat.id
    except Exception:
        cid = None
    sent = getattr(getattr(ctx, "bot", None), "sent", None) or []
    for m in reversed(sent):
        if m.get("chat_id") == cid:
            return m.get("reply_markup")
    if getattr(q.message, "edits", None):
        return q.message.edits[-1]["markup"]
    if getattr(q.message, "replies", None):
        return q.message.replies[-1]["markup"]
    return None


def page_of(q) -> str:
    """page_for() for fire()-style tests: fire() attaches the context on q."""
    return page_for(q, getattr(q, "_ctx", None))


def page_markup_of(q):
    """page_markup_for() for fire()-style tests: context attached on q."""
    return page_markup_for(q, getattr(q, "_ctx", None))


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
