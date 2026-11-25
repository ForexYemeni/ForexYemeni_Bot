"""Local Zero-Cost Royal Institutional Charts (v6.26.0 — 100% Free Forever).

Renders high-resolution dark-gold institutional PNG charts locally via Pillow:
  #13 render_signal_chart(sig, candles, row=None) -> bytes | None
      Live signal candlestick board with Risk/Reward zones, Entry, SL (-pips),
      TP1/TP2/TP3 dashed levels, strategy/score/RR header, and watermark.
  #14 render_proof_chart(sig, candles, row=None) -> bytes | None
      Verified 'BEFORE -> AFTER' victory proof chart upon TP2/TP3 close,
      ready for direct channel & social proof publishing.

Gracefully returns None if Pillow is unavailable — never crashes the bot.
"""
from __future__ import annotations

import io
from pathlib import Path

from fysb import config, db, instruments

try:
    from PIL import Image, ImageDraw, ImageFont
    PIL_AVAILABLE = True
except Exception:
    PIL_AVAILABLE = False

# Royal Dark-Gold Institutional Palette
BG_DARK = (11, 14, 20, 255)
PANEL_BG = (17, 22, 34, 255)
HEADER_BG = (15, 20, 31, 255)
GRID_COLOR = (30, 38, 56, 255)
BORDER_GOLD = (212, 175, 55, 255)
GOLD_SOFT = (245, 197, 66, 255)
TEXT_LIGHT = (238, 242, 248, 255)
TEXT_MUTED = (146, 160, 184, 255)
BULL_COLOR = (22, 199, 132, 255)
BEAR_COLOR = (234, 57, 67, 255)
ENTRY_COLOR = (245, 197, 66, 255)
SL_COLOR = (255, 77, 79, 255)
TP_COLOR = (0, 208, 132, 255)
TP2_COLOR = (46, 213, 155, 255)
TP3_COLOR = (212, 175, 55, 255)
RISK_FILL = (234, 57, 67, 34)
REWARD_FILL = (22, 199, 132, 28)
WATERMARK_COLOR = (212, 175, 55, 24)

_FONT_CANDIDATES_BOLD = (
    "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
    "/usr/share/fonts/truetype/liberation/LiberationSans-Bold.ttf",
    "/system/fonts/Roboto-Bold.ttf",
)
_FONT_CANDIDATES_REG = (
    "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
    "/usr/share/fonts/truetype/liberation/LiberationSans-Regular.ttf",
    "/system/fonts/Roboto-Regular.ttf",
)
_FONT_CANDIDATES_MONO = (
    "/usr/share/fonts/truetype/dejavu/DejaVuSansMono-Bold.ttf",
    "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
)


def charts_enabled() -> bool:
    return (db.get_setting("charts_enabled", "1") or "1") == "1"


def live_signal_metrics(sig: dict, candles: list[dict] | None = None, row=None) -> dict:
    """Compute honest live/realized pips & current market price for a signal.

    For open RUNNING/TP1_HIT/TP2_HIT signals whose `pips_result` is still 0 (or
    whose live market price has travelled further), calculates real floating pips
    from the latest real candle close (or `sig['live_px']`) — never misleadingly
    displaying `+0.0 pips` on a trade currently +100 pips in profit.
    """
    symbol = str(sig.get("symbol") or "XAUUSD").upper()
    direction = str(sig.get("direction") or "BUY").upper()
    entry = float(sig.get("entry") or 0.0)
    status = str(sig.get("status") or "ACTIVE").upper()
    stored_pips = float(sig.get("pips_result") or 0.0)
    closed_at = str(sig.get("closed_at") or "").strip()

    live_px = 0.0
    if candles:
        try:
            live_px = float(candles[-1].get("close") or 0.0)
        except (TypeError, ValueError):
            live_px = 0.0
    if live_px <= 0:
        try:
            live_px = float(sig.get("live_px") or 0.0)
        except (TypeError, ValueError):
            live_px = 0.0

    floating_pips = 0.0
    if live_px > 0 and entry > 0:
        floating_pips = float(instruments.signed_pips(symbol, direction, entry, live_px, row))

    is_closed = bool(closed_at) or status in ("TP3_HIT", "SL_HIT", "EXPIRED", "CANCELLED", "CLOSED")
    if is_closed and stored_pips != 0.0:
        display_pips = stored_pips
        is_floating = False
    elif status in ("TP1_HIT", "TP2_HIT") and not is_closed:
        # Show the live floating pips if currently above banked level, else banked pips
        display_pips = floating_pips if (live_px > 0 and floating_pips > stored_pips) else (stored_pips or floating_pips)
        is_floating = live_px > 0
    elif status == "RUNNING" and live_px > 0:
        display_pips = floating_pips
        is_floating = True
    else:
        display_pips = stored_pips if stored_pips != 0.0 else floating_pips
        is_floating = (not is_closed) and (live_px > 0)

    return {
        "live_px": live_px,
        "floating_pips": round(floating_pips, 1),
        "stored_pips": round(stored_pips, 1),
        "display_pips": round(display_pips, 1),
        "is_floating": is_floating,
        "is_closed": is_closed,
        "status": status,
    }


def _load_font(size: int, kind: str = "bold"):
    if not PIL_AVAILABLE:
        return None
    cands = {
        "bold": _FONT_CANDIDATES_BOLD,
        "reg": _FONT_CANDIDATES_REG,
        "mono": _FONT_CANDIDATES_MONO,
    }.get(kind, _FONT_CANDIDATES_BOLD)
    for p in cands:
        if Path(p).exists():
            try:
                return ImageFont.truetype(p, size)
            except Exception:
                continue
    return ImageFont.load_default()


def _draw_dashed_line(draw, x0: int, x1: int, y: int, color, dash: int = 10, gap: int = 6, width: int = 2):
    x = x0
    while x < x1:
        nx = min(x + dash, x1)
        draw.line([(x, y), (nx, y)], fill=color, width=width)
        x += dash + gap


def _draw_pill(draw, x: int, y: int, text: str, bg, fg, font, pad_x: int = 10, pad_y: int = 5):
    bbox = draw.textbbox((0, 0), text, font=font)
    tw = bbox[2] - bbox[0]
    th = bbox[3] - bbox[1]
    draw.rounded_rectangle([x, y, x + tw + pad_x * 2, y + th + pad_y * 2], radius=7, fill=bg)
    draw.text((x + pad_x, y + pad_y - 1), text, fill=fg, font=font)
    return tw + pad_x * 2


def _render_board(sig: dict, candles: list[dict], row=None, proof_mode: bool = False) -> bytes | None:
    if not sig:
        return None
    bars = list(candles or [])[-48:]
    if len(bars) < 5:
        return None
    if not PIL_AVAILABLE:
        return _render_board_stdlib(sig, bars, row=row, proof_mode=proof_mode)

    W, H = 1200, 680
    img = Image.new("RGBA", (W, H), BG_DARK)
    overlay = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    draw = ImageDraw.Draw(img)
    odraw = ImageDraw.Draw(overlay)

    f_brand = _load_font(22, "bold")
    f_title = _load_font(26, "bold")
    f_sub = _load_font(15, "reg")
    f_mono = _load_font(14, "mono")
    f_badge = _load_font(15, "bold")
    f_wm = _load_font(54, "bold")

    symbol = str(sig.get("symbol") or "XAUUSD").upper()
    direction = str(sig.get("direction") or "BUY").upper()
    tf = str(sig.get("timeframe") or "M15").upper()
    strat = str(sig.get("strategy") or "INSTITUTIONAL").upper()
    score = int(sig.get("score") or 0)
    rr = float(sig.get("rr") or 0.0)
    entry = float(sig.get("entry") or 0.0)
    sl = float(sig.get("stop") or 0.0)
    tp1 = float(sig.get("tp1") or 0.0)
    tp2 = float(sig.get("tp2") or 0.0)
    tp3 = float(sig.get("tp3") or 0.0)
    status = str(sig.get("status") or "ACTIVE").upper()
    pips_res = float(sig.get("pips_result") or 0.0)

    # Outer luxury gold frame
    draw.rounded_rectangle([8, 8, W - 9, H - 9], radius=16, outline=(52, 62, 86, 255), width=2)
    draw.rectangle([10, 10, W - 11, 92], fill=HEADER_BG)
    draw.line([(10, 92), (W - 11, 92)], fill=BORDER_GOLD, width=2)

    # Header Left: Brand + Symbol + Direction Pill
    draw.text((28, 18), "FOREXYEMENI", fill=BORDER_GOLD, font=f_brand)
    draw.text((28, 48), f"{symbol}  ·  {tf}", fill=TEXT_LIGHT, font=f_title)

    dir_bg = BULL_COLOR if direction == "BUY" else BEAR_COLOR
    px_cursor = 290
    px_cursor += _draw_pill(draw, px_cursor, 48, f"{direction}", dir_bg, BG_DARK, f_badge, 12, 4) + 10
    px_cursor += _draw_pill(draw, px_cursor, 48, f"{strat}", (36, 46, 70, 255), GOLD_SOFT, f_badge, 10, 4) + 10
    if score > 0:
        px_cursor += _draw_pill(draw, px_cursor, 48, f"SCORE {score}/100", (30, 42, 62, 255), TEXT_LIGHT, f_badge, 10, 4) + 10
    if rr > 0:
        _draw_pill(draw, px_cursor, 48, f"R:R 1:{rr:.2f}", (30, 42, 62, 255), TP_COLOR, f_badge, 10, 4)

    # Header Right: Proof Victory Ribbon or Live Status Badge (uses REAL live floating pips on RUNNING trades!)
    metrics = live_signal_metrics(sig, bars, row=row)
    eff_pips = metrics["display_pips"]
    if proof_mode:
        usd_001 = instruments.usd_move(symbol, eff_pips, 0.01, px=metrics["live_px"] or tp3 or tp2 or tp1 or entry, row=row)
        tag_lbl = "LIVE PROFIT" if (metrics["is_floating"] and eff_pips >= 0) else (
            "LIVE P&L" if metrics["is_floating"] else ("VERIFIED WIN" if eff_pips >= 0 else "CLOSED")
        )
        ribbon_bg = BORDER_GOLD if eff_pips >= 0 else BEAR_COLOR
        ribbon_txt = f"{tag_lbl}  {eff_pips:+.1f} PIPS  ({usd_001:+,.2f}$ / 0.01)"
        _draw_pill(draw, W - 445, 28, ribbon_txt, ribbon_bg, BG_DARK, f_badge, 14, 9)
    else:
        pip_part = f"  {eff_pips:+.1f}P" if (metrics["live_px"] > 0 or eff_pips != 0.0) else ""
        badge_txt = f"{status}{pip_part}"
        _draw_pill(draw, W - 285, 30, badge_txt, (28, 38, 58, 255), TP_COLOR if eff_pips > 0 else GOLD_SOFT, f_badge, 12, 8)

    # Chart Plot Area
    plot_l, plot_t = 34, 114
    plot_r, plot_b = W - 215, H - 56
    plot_w = plot_r - plot_l
    plot_h = plot_b - plot_t
    draw.rounded_rectangle([plot_l, plot_t, plot_r, plot_b], radius=10, fill=PANEL_BG, outline=GRID_COLOR, width=1)

    # Subtle Center Watermark
    odraw.text((plot_l + plot_w // 2 - 220, plot_t + plot_h // 2 - 30), "FOREXYEMENI", fill=WATERMARK_COLOR, font=f_wm)

    # Price bounds covering candles + Entry + SL + TP1..TP2 (and TP3 if not distorting scale)
    highs = [float(c["high"]) for c in bars if c.get("high")]
    lows = [float(c["low"]) for c in bars if c.get("low")]
    key_levels = [lvl for lvl in (entry, sl, tp1, tp2) if lvl > 0]
    p_hi = max(highs + key_levels)
    p_lo = min(lows + key_levels)
    if tp3 > 0:
        span_core = max(p_hi - p_lo, 1e-9)
        if abs(tp3 - entry) <= span_core * 1.8:
            p_hi = max(p_hi, tp3)
            p_lo = min(p_lo, tp3)
    pad = max((p_hi - p_lo) * 0.10, 1e-6)
    p_hi += pad
    p_lo -= pad

    def y_of(price: float) -> int:
        ratio = (p_hi - float(price)) / max(p_hi - p_lo, 1e-9)
        return int(round(plot_t + 10 + ratio * (plot_h - 20)))

    # Horizontal & Vertical Grid
    for i in range(6):
        gy = int(plot_t + 10 + i * (plot_h - 20) / 5)
        draw.line([(plot_l + 2, gy), (plot_r - 2, gy)], fill=GRID_COLOR, width=1)
        g_price = p_hi - i * (p_hi - p_lo) / 5
        draw.text((plot_r + 10, gy - 8), instruments.fmt_price(symbol, g_price, row), fill=TEXT_MUTED, font=f_mono)

    # Risk & Reward shaded translucent zones (on overlay, alpha-composited)
    zone_x0 = plot_l + int(plot_w * 0.58) if not proof_mode else plot_l + int(plot_w * 0.18)
    zone_x1 = plot_r - 4
    if entry > 0 and sl > 0:
        y_e, y_s = y_of(entry), y_of(sl)
        odraw.rectangle([zone_x0, min(y_e, y_s), zone_x1, max(y_e, y_s)], fill=RISK_FILL)
    top_tp = tp3 if (tp3 > 0 and p_lo <= tp3 <= p_hi) else (tp2 or tp1)
    if entry > 0 and top_tp > 0:
        y_e, y_t = y_of(entry), y_of(top_tp)
        odraw.rectangle([zone_x0, min(y_e, y_t), zone_x1, max(y_e, y_t)], fill=REWARD_FILL)

    img = Image.alpha_composite(img, overlay)
    draw = ImageDraw.Draw(img)

    # Draw real OHLC Candlesticks
    n = len(bars)
    step = (plot_w - 30) / max(n, 1)
    body_w = max(4, min(14, int(step * 0.62)))
    for idx, c in enumerate(bars):
        o = float(c["open"])
        h = float(c["high"])
        l = float(c["low"])
        cl = float(c["close"])
        cx = int(plot_l + 15 + idx * step + step * 0.5)
        col = BULL_COLOR if cl >= o else BEAR_COLOR
        draw.line([(cx, y_of(h)), (cx, y_of(l))], fill=col, width=2)
        y_o, y_c = y_of(o), y_of(cl)
        top_b, bot_b = min(y_o, y_c), max(y_o, y_c)
        if bot_b - top_b < 2:
            bot_b = top_b + 2
        draw.rectangle([cx - body_w // 2, top_b, cx + body_w // 2, bot_b], fill=col)

    # Draw Trade Levels (Entry, SL, TP1, TP2, TP3) with right-axis tags
    levels = [
        ("SL", sl, SL_COLOR, instruments.signed_pips(symbol, direction, entry, sl, row) if sl else 0.0),
        ("ENTRY", entry, ENTRY_COLOR, 0.0),
        ("TP1", tp1, TP_COLOR, instruments.signed_pips(symbol, direction, entry, tp1, row) if tp1 else 0.0),
        ("TP2", tp2, TP2_COLOR, instruments.signed_pips(symbol, direction, entry, tp2, row) if tp2 else 0.0),
        ("TP3", tp3, TP3_COLOR, instruments.signed_pips(symbol, direction, entry, tp3, row) if tp3 else 0.0),
    ]
    used_y: list[int] = []
    for tag, lvl, col, spips in levels:
        if lvl <= 0 or not (p_lo <= lvl <= p_hi):
            continue
        ly = y_of(lvl)
        if tag == "ENTRY":
            draw.line([(plot_l + 4, ly), (plot_r - 2, ly)], fill=col, width=2)
        else:
            _draw_dashed_line(draw, plot_l + 4, plot_r - 2, ly, col, dash=9, gap=6, width=2)
        # De-collide right-axis tag labels
        lbl_y = ly - 11
        for prev_y in used_y:
            if abs(lbl_y - prev_y) < 22:
                lbl_y = prev_y + 22
        lbl_y = max(plot_t + 2, min(plot_b - 22, lbl_y))
        used_y.append(lbl_y)
        px_str = instruments.fmt_price(symbol, lvl, row)
        pip_part = f" ({spips:+.0f}p)" if tag != "ENTRY" else ""
        tag_txt = f"{tag} {px_str}{pip_part}"
        draw.rounded_rectangle([plot_r + 6, lbl_y, W - 18, lbl_y + 21], radius=5, fill=col)
        draw.text((plot_r + 12, lbl_y + 3), tag_txt[:22], fill=BG_DARK, font=f_mono)

    # Footer bar
    last_dt = str(bars[-1].get("datetime") or sig.get("data_time") or "")[:16]
    foot_l = f"REAL MARKET OHLC  ·  LAST CANDLE: {last_dt} UTC  ·  {config.BOT_SIGNATURE}"
    draw.text((34, H - 38), foot_l, fill=TEXT_MUTED, font=f_sub)
    mode_tag = "VERIFIED BEFORE/AFTER PROOF" if proof_mode else "INSTITUTIONAL LIVE CHART"
    draw.text((W - 285, H - 38), mode_tag, fill=BORDER_GOLD, font=f_sub)

    buf = io.BytesIO()
    img.convert("RGB").save(buf, format="PNG", optimize=True)
    return buf.getvalue()


def render_signal_chart(sig: dict, candles: list[dict], row=None) -> bytes | None:
    """#13 Render institutional live signal candlestick chart PNG."""
    return _render_board(sig, candles, row=row, proof_mode=False)


def render_proof_chart(sig: dict, candles: list[dict], row=None) -> bytes | None:
    """#14 Render verified BEFORE -> AFTER TP2/TP3 victory proof chart PNG."""
    return _render_board(sig, candles, row=row, proof_mode=True)


# --- Zero-Dependency Pure-Python Stdlib PNG Fallback (works even without Pillow) ---
_FONT5X7: dict[str, tuple[str, ...]] = {
    "A": ("01110", "10001", "10001", "11111", "10001", "10001", "10001"),
    "B": ("11110", "10001", "10001", "11110", "10001", "10001", "11110"),
    "C": ("01110", "10001", "10000", "10000", "10000", "10001", "01110"),
    "D": ("11100", "10010", "10001", "10001", "10001", "10010", "11100"),
    "E": ("11111", "10000", "10000", "11110", "10000", "10000", "11111"),
    "F": ("11111", "10000", "10000", "11110", "10000", "10000", "10000"),
    "G": ("01110", "10001", "10000", "10111", "10001", "10001", "01110"),
    "H": ("10001", "10001", "10001", "11111", "10001", "10001", "10001"),
    "I": ("01110", "00100", "00100", "00100", "00100", "00100", "01110"),
    "J": ("00111", "00010", "00010", "00010", "10010", "10010", "01100"),
    "K": ("10001", "10010", "10100", "11000", "10100", "10010", "10001"),
    "L": ("10000", "10000", "10000", "10000", "10000", "10000", "11111"),
    "M": ("10001", "11011", "10101", "10101", "10001", "10001", "10001"),
    "N": ("10001", "11001", "10101", "10011", "10001", "10001", "10001"),
    "O": ("01110", "10001", "10001", "10001", "10001", "10001", "01110"),
    "P": ("11110", "10001", "10001", "11110", "10000", "10000", "10000"),
    "Q": ("01110", "10001", "10001", "10001", "10101", "10010", "01101"),
    "R": ("11110", "10001", "10001", "11110", "10100", "10010", "10001"),
    "S": ("01111", "10000", "10000", "01110", "00001", "00001", "11110"),
    "T": ("11111", "00100", "00100", "00100", "00100", "00100", "00100"),
    "U": ("10001", "10001", "10001", "10001", "10001", "10001", "01110"),
    "V": ("10001", "10001", "10001", "10001", "10001", "01010", "00100"),
    "W": ("10001", "10001", "10001", "10101", "10101", "11011", "10001"),
    "X": ("10001", "10001", "01010", "00100", "01010", "10001", "10001"),
    "Y": ("10001", "10001", "01010", "00100", "00100", "00100", "00100"),
    "Z": ("11111", "00001", "00010", "00100", "01000", "10000", "11111"),
    "0": ("01110", "10001", "10011", "10101", "11001", "10001", "01110"),
    "1": ("00100", "01100", "00100", "00100", "00100", "00100", "01110"),
    "2": ("01110", "10001", "00001", "00010", "00100", "01000", "11111"),
    "3": ("11110", "00001", "00001", "01110", "00001", "00001", "11110"),
    "4": ("00010", "00110", "01010", "10010", "11111", "00010", "00010"),
    "5": ("11111", "10000", "11110", "00001", "00001", "10001", "01110"),
    "6": ("01110", "10000", "11110", "10001", "10001", "10001", "01110"),
    "7": ("11111", "00001", "00010", "00100", "01000", "01000", "01000"),
    "8": ("01110", "10001", "10001", "01110", "10001", "10001", "01110"),
    "9": ("01110", "10001", "10001", "01111", "00001", "00001", "01110"),
    ".": ("00000", "00000", "00000", "00000", "00000", "01100", "01100"),
    ":": ("00000", "01100", "01100", "00000", "01100", "01100", "00000"),
    "-": ("00000", "00000", "00000", "11111", "00000", "00000", "00000"),
    "+": ("00000", "00100", "00100", "11111", "00100", "00100", "00000"),
    "/": ("00001", "00010", "00010", "00100", "01000", "01000", "10000"),
    "|": ("00100", "00100", "00100", "00100", "00100", "00100", "00100"),
    " ": ("00000", "00000", "00000", "00000", "00000", "00000", "00000"),
}


def _encode_png_rgb(w: int, h: int, buf: bytearray) -> bytes:
    import struct
    import zlib

    def _chunk(tag: bytes, data: bytes) -> bytes:
        return struct.pack(">I", len(data)) + tag + data + struct.pack(">I", zlib.crc32(tag + data) & 0xFFFFFFFF)

    stride = w * 3
    raw = bytearray((stride + 1) * h)
    for y in range(h):
        dst = y * (stride + 1)
        raw[dst] = 0
        src = y * stride
        raw[dst + 1:dst + 1 + stride] = buf[src:src + stride]
    ihdr = struct.pack(">IIBBBBB", w, h, 8, 2, 0, 0, 0)
    return b"\x89PNG\r\n\x1a\n" + _chunk(b"IHDR", ihdr) + _chunk(b"IDAT", zlib.compress(bytes(raw), 6)) + _chunk(b"IEND", b"")


def _render_board_stdlib(sig: dict, bars: list[dict], row=None, proof_mode: bool = False) -> bytes:
    """Pure-Python dark-gold candlestick PNG renderer (zero external deps)."""
    W, H = 960, 560
    buf = bytearray(W * H * 3)

    def _fill_rect(x0: int, y0: int, x1: int, y1: int, rgb: tuple[int, int, int]) -> None:
        r, g, b = rgb
        xa, xb = max(0, x0), min(W, x1)
        ya, yb = max(0, y0), min(H, y1)
        if xa >= xb or ya >= yb:
            return
        row_bytes = bytes((r, g, b)) * (xb - xa)
        for y in range(ya, yb):
            off = (y * W + xa) * 3
            buf[off:off + len(row_bytes)] = row_bytes

    def _draw_text(x: int, y: int, txt: str, rgb: tuple[int, int, int], scale: int = 2) -> None:
        cx = x
        for ch in txt.upper():
            glyph = _FONT5X7.get(ch, _FONT5X7[" "])
            for gy, line in enumerate(glyph):
                for gx, bit in enumerate(line):
                    if bit == "1":
                        _fill_rect(cx + gx * scale, y + gy * scale, cx + (gx + 1) * scale, y + (gy + 1) * scale, rgb)
            cx += 6 * scale

    _fill_rect(0, 0, W, H, BG_DARK[:3])
    _fill_rect(8, 8, W - 8, 74, HEADER_BG[:3])
    _fill_rect(8, 72, W - 8, 74, BORDER_GOLD[:3])

    plot_l, plot_t, plot_r, plot_b = 26, 92, W - 210, H - 44
    _fill_rect(plot_l, plot_t, plot_r, plot_b, PANEL_BG[:3])

    symbol = str(sig.get("symbol") or "XAUUSD").upper()
    direction = str(sig.get("direction") or "BUY").upper()
    tf = str(sig.get("timeframe") or "M15").upper()
    strat = str(sig.get("strategy") or "INSTITUTIONAL").upper()
    score = int(sig.get("score") or 0)
    entry = float(sig.get("entry") or 0.0)
    sl = float(sig.get("stop") or 0.0)
    tp1 = float(sig.get("tp1") or 0.0)
    tp2 = float(sig.get("tp2") or 0.0)
    tp3 = float(sig.get("tp3") or 0.0)

    metrics = live_signal_metrics(sig, bars, row=row)
    eff_pips = metrics["display_pips"]
    hdr = f"FOREXYEMENI | {symbol} {direction} {tf} | {strat} {score}/100"
    _draw_text(22, 26, hdr[:52], GOLD_SOFT[:3], scale=2)
    if proof_mode:
        lbl = "LIVE" if metrics["is_floating"] else "PROOF"
        _draw_text(22, 50, f"{lbl} | {sig.get('status','')} {eff_pips:+.1f}P", TP_COLOR[:3] if eff_pips >= 0 else SL_COLOR[:3], scale=2)

    highs = [float(b["high"]) for b in bars]
    lows = [float(b["low"]) for b in bars]
    for lv in (entry, sl, tp1, tp2, tp3):
        if lv > 0:
            highs.append(lv)
            lows.append(lv)
    p_hi, p_lo = max(highs), min(lows)
    pad = max((p_hi - p_lo) * 0.08, 1e-6)
    p_hi += pad
    p_lo -= pad

    def y_of(price: float) -> int:
        ratio = (p_hi - price) / (p_hi - p_lo)
        return int(plot_t + ratio * (plot_b - plot_t))

    # Grid lines
    for i in range(1, 5):
        gy = plot_t + int((plot_b - plot_t) * i / 5)
        _fill_rect(plot_l, gy, plot_r, gy + 1, GRID_COLOR[:3])

    # Candlesticks
    n = len(bars)
    step = (plot_r - plot_l - 16) / max(n, 1)
    cw = max(3, int(step * 0.6))
    for idx, b in enumerate(bars):
        o, h, l, c = float(b["open"]), float(b["high"]), float(b["low"]), float(b["close"])
        cx = int(plot_l + 8 + idx * step + step / 2)
        col = BULL_COLOR[:3] if c >= o else BEAR_COLOR[:3]
        _fill_rect(cx, y_of(h), cx + 1, y_of(l) + 1, col)
        yt, yb = min(y_of(o), y_of(c)), max(y_of(o), y_of(c))
        if yb - yt < 2:
            yb = yt + 2
        _fill_rect(cx - cw // 2, yt, cx + cw // 2 + 1, yb, col)

    # Levels & right-axis labels
    used_y: list[int] = []
    for tag, lv, col in (
        ("ENTRY", entry, ENTRY_COLOR[:3]),
        ("SL", sl, SL_COLOR[:3]),
        ("TP1", tp1, TP_COLOR[:3]),
        ("TP2", tp2, TP2_COLOR[:3]),
        ("TP3", tp3, TP3_COLOR[:3]),
    ):
        if lv <= 0 or not (p_lo <= lv <= p_hi):
            continue
        ly = y_of(lv)
        if tag == "ENTRY":
            _fill_rect(plot_l + 2, ly, plot_r - 2, ly + 2, col)
        else:
            for dx in range(plot_l + 2, plot_r - 2, 14):
                _fill_rect(dx, ly, min(dx + 8, plot_r - 2), ly + 2, col)
        lbl_y = ly - 10
        for py in used_y:
            if abs(lbl_y - py) < 22:
                lbl_y = py + 22
        lbl_y = max(plot_t + 2, min(plot_b - 20, lbl_y))
        used_y.append(lbl_y)
        _fill_rect(plot_r + 6, lbl_y, W - 12, lbl_y + 20, col)
        px_str = instruments.fmt_price(symbol, lv, row)
        _draw_text(plot_r + 12, lbl_y + 3, f"{tag} {px_str}"[:16], BG_DARK[:3], scale=2)

    _draw_text(26, H - 28, f"REAL OHLC | FOREXYEMENI ROYAL V{config.VERSION}", TEXT_MUTED[:3], scale=2)
    return _encode_png_rgb(W, H, buf)
