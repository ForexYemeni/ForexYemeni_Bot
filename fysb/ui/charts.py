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
    return PIL_AVAILABLE and (db.get_setting("charts_enabled", "1") or "1") == "1"


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
    if not PIL_AVAILABLE or not sig:
        return None
    bars = list(candles or [])[-48:]
    if len(bars) < 5:
        return None

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

    # Header Right: Proof Victory Ribbon or Live Status Badge
    if proof_mode:
        usd_001 = instruments.usd_move(symbol, pips_res, 0.01, px=tp3 or tp2 or tp1 or entry, row=row)
        ribbon_txt = f"VERIFIED WIN  {pips_res:+.1f} PIPS  ({usd_001:+,.2f}$ / 0.01)"
        _draw_pill(draw, W - 445, 28, ribbon_txt, BORDER_GOLD, BG_DARK, f_badge, 14, 9)
    else:
        badge_txt = f"STATUS: {status}"
        _draw_pill(draw, W - 245, 30, badge_txt, (28, 38, 58, 255), GOLD_SOFT, f_badge, 12, 8)

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
