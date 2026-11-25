"""Free Live Crypto WebSocket Feed (v6.24.0) — Zero Extra Dependencies.

Streams sub-second real spot ticks for major crypto instruments using pure
stdlib `asyncio` + `ssl` RFC-6455 WebSocket framing:
  * Primary : Binance public combined `miniTicker` stream (keyless, free 24/7).
  * Fallback: Bybit public v5 spot `tickers` stream (keyless, free 24/7).

If outbound WebSocket connections are blocked or `CRYPTO_WS_ENABLED=0`, the
feed backs off gracefully and `market_data.fetch_price()` seamlessly continues
with its REST failover chain — zero errors, zero impact.
"""
from __future__ import annotations

import asyncio
import base64
import json
import os
import secrets
import ssl
import struct
import threading
import time

from fysb.config import log

# Venue ticker -> internal normalized symbol
STREAM_SYMBOLS: dict[str, str] = {
    "BTCUSDT": "BTCUSD",
    "ETHUSDT": "ETHUSD",
    "SOLUSDT": "SOLUSD",
    "XRPUSDT": "XRPUSD",
    "ADAUSDT": "ADAUSD",
    "DOGEUSDT": "DOGEUSD",
    "BNBUSDT": "BNBUSD",
    "AVAXUSDT": "AVAXUSD",
}

_LOCK = threading.Lock()
_TICKS: dict[str, tuple[float, float, str]] = {}  # internal -> (price, ts, venue)
_META = {
    "connected": False,
    "venue": "",
    "messages": 0,
    "reconnects": 0,
    "last_tick_at": 0.0,
    "last_error": "",
}


def is_enabled() -> bool:
    return os.getenv("CRYPTO_WS_ENABLED", "1").strip() not in ("0", "false", "False", "no")


def reset() -> None:
    """Reset tick store (used in unit tests)."""
    with _LOCK:
        _TICKS.clear()
        _META.update(connected=False, venue="", messages=0, reconnects=0, last_tick_at=0.0, last_error="")


def update_tick(internal: str, price: float, venue: str = "binance-ws", now: float | None = None) -> None:
    if not internal or not price or price <= 0:
        return
    ts = time.time() if now is None else float(now)
    with _LOCK:
        _TICKS[internal.upper()] = (float(price), ts, venue)
        _META["messages"] += 1
        _META["last_tick_at"] = ts


def get_live_price(internal: str, max_age: float = 12.0, now: float | None = None) -> float | None:
    """Return latest live WebSocket tick if fresher than `max_age` seconds."""
    if not internal:
        return None
    t = time.time() if now is None else float(now)
    with _LOCK:
        hit = _TICKS.get(internal.upper())
    if not hit:
        return None
    price, ts, _venue = hit
    if (t - ts) <= max_age and price > 0:
        return price
    return None


def ws_stats(now: float | None = None) -> dict:
    t = time.time() if now is None else float(now)
    with _LOCK:
        fresh = sum(1 for _p, ts, _v in _TICKS.values() if (t - ts) <= 15.0)
        return {
            "enabled": is_enabled(),
            "connected": _META["connected"],
            "venue": _META["venue"],
            "fresh_symbols": fresh,
            "total_symbols": len(_TICKS),
            "messages": _META["messages"],
            "reconnects": _META["reconnects"],
            "last_error": _META["last_error"],
        }


def parse_binance_message(raw: str, now: float | None = None) -> bool:
    """Parse one Binance combined miniTicker JSON message into the tick store."""
    try:
        obj = json.loads(raw)
        data = obj.get("data", obj) if isinstance(obj, dict) else {}
        sym = str(data.get("s") or "").upper()
        px = float(data.get("c") or 0.0)
        internal = STREAM_SYMBOLS.get(sym)
        if internal and px > 0:
            update_tick(internal, px, venue="binance-ws", now=now)
            return True
    except Exception:
        pass
    return False


def parse_bybit_message(raw: str, now: float | None = None) -> bool:
    """Parse one Bybit v5 spot ticker JSON message into the tick store."""
    try:
        obj = json.loads(raw)
        data = obj.get("data") if isinstance(obj, dict) else None
        if not isinstance(data, dict):
            return False
        sym = str(data.get("symbol") or "").upper()
        px = float(data.get("lastPrice") or 0.0)
        internal = STREAM_SYMBOLS.get(sym)
        if internal and px > 0:
            update_tick(internal, px, venue="bybit-ws", now=now)
            return True
    except Exception:
        pass
    return False


# ---------------------------------------------------------------------------
# Minimal RFC-6455 client over stdlib asyncio SSL (zero third-party deps)
# ---------------------------------------------------------------------------

def _encode_client_frame(opcode: int, payload: bytes) -> bytes:
    """Build one masked RFC-6455 client frame (FIN=1)."""
    mask = secrets.token_bytes(4)
    n = len(payload)
    header = bytearray([0x80 | (opcode & 0x0F)])
    if n < 126:
        header.append(0x80 | n)
    elif n < 65536:
        header.append(0x80 | 126)
        header.extend(struct.pack("!H", n))
    else:
        header.append(0x80 | 127)
        header.extend(struct.pack("!Q", n))
    header.extend(mask)
    masked = bytes(b ^ mask[i & 3] for i, b in enumerate(payload))
    return bytes(header) + masked


async def _read_frame(reader: asyncio.StreamReader, timeout: float = 35.0) -> tuple[int, bytes]:
    hdr = await asyncio.wait_for(reader.readexactly(2), timeout=timeout)
    opcode = hdr[0] & 0x0F
    masked = (hdr[1] & 0x80) != 0
    length = hdr[1] & 0x7F
    if length == 126:
        length = struct.unpack("!H", await asyncio.wait_for(reader.readexactly(2), timeout=timeout))[0]
    elif length == 127:
        length = struct.unpack("!Q", await asyncio.wait_for(reader.readexactly(8), timeout=timeout))[0]
    mask = await asyncio.wait_for(reader.readexactly(4), timeout=timeout) if masked else b""
    payload = await asyncio.wait_for(reader.readexactly(length), timeout=timeout) if length else b""
    if masked:
        payload = bytes(b ^ mask[i & 3] for i, b in enumerate(payload))
    return opcode, payload


async def _ws_handshake(host: str, port: int, path: str) -> tuple[asyncio.StreamReader, asyncio.StreamWriter]:
    ctx = ssl.create_default_context()
    reader, writer = await asyncio.wait_for(
        asyncio.open_connection(host, port, ssl=ctx, server_hostname=host),
        timeout=12.0,
    )
    ws_key = base64.b64encode(secrets.token_bytes(16)).decode("ascii")
    req = (
        f"GET {path} HTTP/1.1\r\n"
        f"Host: {host}:{port}\r\n"
        "Upgrade: websocket\r\n"
        "Connection: Upgrade\r\n"
        f"Sec-WebSocket-Key: {ws_key}\r\n"
        "Sec-WebSocket-Version: 13\r\n"
        "User-Agent: FOREXYEMENI-WS/1.0\r\n\r\n"
    )
    writer.write(req.encode("ascii"))
    await writer.drain()
    status_line = await asyncio.wait_for(reader.readline(), timeout=10.0)
    if b"101" not in status_line:
        writer.close()
        raise ConnectionError(f"WS handshake refused: {status_line.decode('latin1', 'ignore').strip()[:80]}")
    while True:
        line = await asyncio.wait_for(reader.readline(), timeout=10.0)
        if line in (b"\r\n", b"\n", b""):
            break
    return reader, writer


async def _stream_binance() -> None:
    streams = "/".join(f"{s.lower()}@miniTicker" for s in STREAM_SYMBOLS)
    reader, writer = await _ws_handshake("stream.binance.com", 9443, f"/stream?streams={streams}")
    with _LOCK:
        _META["connected"] = True
        _META["venue"] = "binance-ws"
        _META["last_error"] = ""
    try:
        while True:
            opcode, payload = await _read_frame(reader, timeout=40.0)
            if opcode == 0x1:  # text
                parse_binance_message(payload.decode("utf-8", "ignore"))
            elif opcode == 0x9:  # ping -> pong
                writer.write(_encode_client_frame(0xA, payload))
                await writer.drain()
            elif opcode == 0x8:  # close
                break
    finally:
        with _LOCK:
            _META["connected"] = False
        try:
            writer.close()
        except Exception:
            pass


async def _stream_bybit() -> None:
    reader, writer = await _ws_handshake("stream.bybit.com", 443, "/v5/public/spot")
    sub = json.dumps({"op": "subscribe", "args": [f"tickers.{s}" for s in STREAM_SYMBOLS]}).encode("utf-8")
    writer.write(_encode_client_frame(0x1, sub))
    await writer.drain()
    with _LOCK:
        _META["connected"] = True
        _META["venue"] = "bybit-ws"
        _META["last_error"] = ""
    last_ping = time.time()
    try:
        while True:
            if time.time() - last_ping >= 18.0:
                writer.write(_encode_client_frame(0x1, b'{"op":"ping"}'))
                await writer.drain()
                last_ping = time.time()
            opcode, payload = await _read_frame(reader, timeout=25.0)
            if opcode == 0x1:
                parse_bybit_message(payload.decode("utf-8", "ignore"))
            elif opcode == 0x9:
                writer.write(_encode_client_frame(0xA, payload))
                await writer.drain()
            elif opcode == 0x8:
                break
    finally:
        with _LOCK:
            _META["connected"] = False
        try:
            writer.close()
        except Exception:
            pass


async def crypto_ws_loop(app=None) -> None:
    """Background task keeping live crypto ticks warm (auto-failover Binance -> Bybit)."""
    if not is_enabled():
        return
    backoff = 5.0
    venues = (_stream_binance, _stream_bybit)
    idx = 0
    while True:
        try:
            await venues[idx % len(venues)]()
            backoff = 5.0
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            with _LOCK:
                _META["connected"] = False
                _META["reconnects"] += 1
                _META["last_error"] = f"{type(exc).__name__}: {str(exc)[:80]}"
            log.debug("crypto_ws reconnect (%s): %s", venues[idx % len(venues)].__name__, exc)
            idx += 1
            await asyncio.sleep(backoff)
            backoff = min(backoff * 1.8, 120.0)
