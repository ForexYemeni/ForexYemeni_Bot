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


def _render_board(sig: dict, candles: list[dict], row=None, proof_mode: bool = False, tracking_event: str = "") -> bytes | None:
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
    f_sub = _load_font(14, "reg")
    f_mono = _load_font(14, "mono")
    f_badge = _load_font(15, "bold")
    f_hud = _load_font(13, "bold")
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
    hit_tp = int(sig.get("hit_tp") or 0)
    ev_u = str(tracking_event or "").upper()

    # Outer luxury double-accent gold frame + subtle top metallic sheen
    draw.rounded_rectangle([6, 6, W - 7, H - 7], radius=18, outline=(46, 56, 80, 255), width=2)
    draw.rounded_rectangle([10, 10, W - 11, H - 11], radius=15, outline=(212, 175, 55, 70), width=1)
    draw.rectangle([12, 12, W - 13, 94], fill=HEADER_BG)
    # Subtle royal gold top-left / top-right corner accent bars
    draw.line([(12, 12), (260, 12)], fill=BORDER_GOLD, width=3)
    draw.line([(W - 260, 12), (W - 13, 12)], fill=BORDER_GOLD, width=3)
    draw.line([(12, 94), (W - 13, 94)], fill=BORDER_GOLD, width=2)

    # Header Left: Brand + Symbol + Direction Pill
    draw.text((28, 18), "FOREXYEMENI  ROYAL", fill=BORDER_GOLD, font=f_brand)
    draw.text((28, 49), f"{symbol}  ·  {tf}", fill=TEXT_LIGHT, font=f_title)

    dir_bg = BULL_COLOR if direction == "BUY" else BEAR_COLOR
    px_cursor = 290
    px_cursor += _draw_pill(draw, px_cursor, 49, f"{direction}", dir_bg, BG_DARK, f_badge, 12, 4) + 10
    px_cursor += _draw_pill(draw, px_cursor, 49, f"{strat}", (36, 46, 70, 255), GOLD_SOFT, f_badge, 10, 4) + 10
    if score > 0:
        px_cursor += _draw_pill(draw, px_cursor, 49, f"SCORE {score}/100", (30, 42, 62, 255), TEXT_LIGHT, f_badge, 10, 4) + 10
    if rr > 0:
        _draw_pill(draw, px_cursor, 49, f"R:R 1:{rr:.2f}", (30, 42, 62, 255), TP_COLOR, f_badge, 10, 4)

    # Header Right: Event / Proof Victory Ribbon or Live Status Badge
    metrics = live_signal_metrics(sig, bars, row=row)
    eff_pips = metrics["display_pips"]
    live_px = float(metrics["live_px"] or 0.0)
    usd_001 = instruments.usd_move(symbol, eff_pips, 0.01, px=live_px or tp3 or tp2 or tp1 or entry, row=row)

    event_labels = {
        "RUNNING": ("LIVE ENTRY ACTIVE", (56, 189, 248, 255), BG_DARK),
        "HALFWAY": ("50% SECURE PROFIT", GOLD_SOFT, BG_DARK),
        "TP1_HIT": ("TP1 SMASHED", TP_COLOR, BG_DARK),
        "TP2_HIT": ("TP2 VERIFIED WIN", BORDER_GOLD, BG_DARK),
        "TP3_HIT": ("TP3 FULL ROYAL WIN", BORDER_GOLD, BG_DARK),
        "TP1_BE": ("TP1 BREAKEVEN LOCKED", GOLD_SOFT, BG_DARK),
        "TP2_BE": ("TP2 BREAKEVEN LOCKED", GOLD_SOFT, BG_DARK),
        "SL_HIT": ("SL RISK PROTECTED", BEAR_COLOR, TEXT_LIGHT),
    }
    if ev_u in event_labels:
        ev_lbl, r_bg, r_fg = event_labels[ev_u]
        ribbon_txt = f"{ev_lbl}  {eff_pips:+.1f}P  ({usd_001:+,.2f}$)"
        _draw_pill(draw, W - 465, 28, ribbon_txt, r_bg, r_fg, f_badge, 14, 9)
    elif proof_mode:
        tag_lbl = "LIVE PROFIT" if (metrics["is_floating"] and eff_pips >= 0) else (
            "LIVE P&L" if metrics["is_floating"] else ("VERIFIED WIN" if eff_pips >= 0 else "CLOSED")
        )
        ribbon_bg = BORDER_GOLD if eff_pips >= 0 else BEAR_COLOR
        ribbon_txt = f"{tag_lbl}  {eff_pips:+.1f} PIPS  ({usd_001:+,.2f}$ / 0.01)"
        _draw_pill(draw, W - 455, 28, ribbon_txt, ribbon_bg, BG_DARK, f_badge, 14, 9)
    else:
        pip_part = f"  {eff_pips:+.1f}P" if (live_px > 0 or eff_pips != 0.0) else ""
        badge_txt = f"{status}{pip_part}"
        _draw_pill(draw, W - 295, 30, badge_txt, (28, 38, 58, 255), TP_COLOR if eff_pips > 0 else GOLD_SOFT, f_badge, 12, 8)

    # Luxury Live Tracking Milestone HUD Strip (between header and chart plot)
    hud_l, hud_t, hud_r, hud_b = 34, 104, W - 24, 134
    draw.rounded_rectangle([hud_l, hud_t, hud_r, hud_b], radius=8, fill=(18, 24, 38, 255), outline=(40, 52, 76, 255), width=1)
    # Milestone pills: ENTRY -> TP1 -> TP2 -> TP3
    is_live = status in ("RUNNING", "TP1_HIT", "TP2_HIT", "TP3_HIT") or ev_u in ("RUNNING", "HALFWAY", "TP1_HIT", "TP2_HIT", "TP3_HIT", "TP1_BE", "TP2_BE")
    eff_hit = max(hit_tp, {"TP1_HIT": 1, "TP1_BE": 1, "TP2_HIT": 2, "TP2_BE": 2, "TP3_HIT": 3}.get(ev_u, 0))
    ms_items = [
        ("ENTRY", is_live, GOLD_SOFT),
        ("TP1", eff_hit >= 1, TP_COLOR),
        ("TP2", eff_hit >= 2, TP2_COLOR),
        ("TP3", eff_hit >= 3, BORDER_GOLD),
    ]
    mx = hud_l + 14
    for idx_m, (m_lbl, reached, m_col) in enumerate(ms_items):
        dot_col = m_col if reached else (58, 70, 96, 255)
        draw.ellipse([mx, hud_t + 9, mx + 12, hud_t + 21], fill=dot_col)
        draw.text((mx + 18, hud_t + 7), m_lbl, fill=m_col if reached else TEXT_MUTED, font=f_hud)
        mx += 78
        if idx_m < len(ms_items) - 1:
            line_col = m_col if reached else (42, 54, 78, 255)
            draw.line([(mx - 16, hud_t + 15), (mx - 4, hud_t + 15)], fill=line_col, width=2)
    # Right side of HUD: Live Price + Real-Time P&L Telemetry
    now_px_str = instruments.fmt_price(symbol, live_px, row) if live_px > 0 else "—"
    prog_txt = ""
    if entry > 0 and tp1 > 0 and live_px > 0:
        tp1_d = abs(tp1 - entry)
        if tp1_d > 0:
            mv = (live_px - entry) if direction == "BUY" else (entry - live_px)
            pct = max(0, min(100, int(round((mv / tp1_d) * 100))))
            prog_txt = f"  |  TP1: {pct}%"
    hud_telemetry = f"LIVE: {now_px_str}  |  P&L: {eff_pips:+.1f} PIPS ({usd_001:+,.2f}$){prog_txt}"
    draw.text((hud_l + 370, hud_t + 7), hud_telemetry, fill=TP_COLOR if eff_pips >= 0 else SL_COLOR, font=f_hud)

    # Chart Plot Area
    plot_l, plot_t = 34, 144
    plot_r, plot_b = W - 215, H - 52
    plot_w = plot_r - plot_l
    plot_h = plot_b - plot_t
    draw.rounded_rectangle([plot_l, plot_t, plot_r, plot_b], radius=10, fill=PANEL_BG, outline=(42, 54, 78, 255), width=1)

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
    for j in range(1, 6):
        gx = int(plot_l + j * plot_w / 6)
        draw.line([(gx, plot_t + 4), (gx, plot_b - 4)], fill=(24, 31, 46, 255), width=1)

    # Risk & Reward shaded translucent zones (on overlay, alpha-composited)
    zone_x0 = plot_l + int(plot_w * 0.56) if not (proof_mode or ev_u) else plot_l + int(plot_w * 0.18)
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
    last_cx = plot_r - 20
    for idx, c in enumerate(bars):
        o = float(c["open"])
        h = float(c["high"])
        l = float(c["low"])
        cl = float(c["close"])
        cx = int(plot_l + 15 + idx * step + step * 0.5)
        last_cx = cx
        col = BULL_COLOR if cl >= o else BEAR_COLOR
        draw.line([(cx, y_of(h)), (cx, y_of(l))], fill=col, width=2)
        y_o, y_c = y_of(o), y_of(cl)
        top_b, bot_b = min(y_o, y_c), max(y_o, y_c)
        if bot_b - top_b < 2:
            bot_b = top_b + 2
        draw.rectangle([cx - body_w // 2, top_b, cx + body_w // 2, bot_b], fill=col)

    # Live Market Price line & glowing pulse dot on latest candle
    LIVE_CYAN = (56, 189, 248, 255)
    used_y: list[int] = []
    if live_px > 0 and p_lo <= live_px <= p_hi:
        ly_now = y_of(live_px)
        _draw_dashed_line(draw, plot_l + 4, plot_r - 2, ly_now, LIVE_CYAN, dash=5, gap=4, width=1)
        draw.ellipse([last_cx - 6, ly_now - 6, last_cx + 6, ly_now + 6], outline=LIVE_CYAN, width=2)
        draw.ellipse([last_cx - 3, ly_now - 3, last_cx + 3, ly_now + 3], fill=GOLD_SOFT)

    # Draw Trade Levels (Entry, SL, TP1, TP2, TP3) with right-axis tags
    levels = [
        ("SL", sl, SL_COLOR, instruments.signed_pips(symbol, direction, entry, sl, row) if sl else 0.0),
        ("ENTRY", entry, ENTRY_COLOR, 0.0),
        ("TP1", tp1, TP_COLOR, instruments.signed_pips(symbol, direction, entry, tp1, row) if tp1 else 0.0),
        ("TP2", tp2, TP2_COLOR, instruments.signed_pips(symbol, direction, entry, tp2, row) if tp2 else 0.0),
        ("TP3", tp3, TP3_COLOR, instruments.signed_pips(symbol, direction, entry, tp3, row) if tp3 else 0.0),
    ]
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

    # Right-axis NOW price pill (de-collided)
    if live_px > 0 and p_lo <= live_px <= p_hi:
        ly_now = y_of(live_px)
        lbl_y = ly_now - 11
        for prev_y in used_y:
            if abs(lbl_y - prev_y) < 22:
                lbl_y = prev_y + 22
        lbl_y = max(plot_t + 2, min(plot_b - 22, lbl_y))
        used_y.append(lbl_y)
        now_txt = f"NOW {instruments.fmt_price(symbol, live_px, row)}"
        draw.rounded_rectangle([plot_r + 6, lbl_y, W - 18, lbl_y + 21], radius=5, fill=LIVE_CYAN)
        draw.text((plot_r + 12, lbl_y + 3), now_txt[:22], fill=BG_DARK, font=f_mono)

    # Footer bar
    last_dt = str(bars[-1].get("datetime") or sig.get("data_time") or "")[:16]
    foot_l = f"REAL MARKET OHLC  ·  LAST CANDLE: {last_dt} UTC  ·  {config.BOT_SIGNATURE}"
    draw.text((34, H - 36), foot_l, fill=TEXT_MUTED, font=f_sub)
    mode_tag = (
        f"ROYAL LIVE TRACKING · {ev_u}" if ev_u else
        ("VERIFIED BEFORE/AFTER PROOF" if proof_mode else "INSTITUTIONAL ROYAL CHART")
    )
    draw.text((W - 320, H - 36), mode_tag, fill=BORDER_GOLD, font=f_sub)

    buf = io.BytesIO()
    img.convert("RGB").save(buf, format="PNG", optimize=True)
    return buf.getvalue()


def render_signal_chart(sig: dict, candles: list[dict], row=None) -> bytes | None:
    """#13 Render institutional live signal candlestick chart PNG."""
    return _render_board(sig, candles, row=row, proof_mode=False)


def render_proof_chart(sig: dict, candles: list[dict], row=None) -> bytes | None:
    """#14 Render verified BEFORE -> AFTER TP2/TP3 victory proof chart PNG."""
    return _render_board(sig, candles, row=row, proof_mode=True)


def render_tracking_chart(sig: dict, candles: list[dict], event: str = "", row=None) -> bytes | None:
    """v7.3.0 Render ultra-luxury live tracking candlestick chart PNG for lifecycle milestones."""
    ev_u = str(event or "").upper()
    is_proof = ev_u in ("TP1_HIT", "TP2_HIT", "TP3_HIT", "TP1_BE", "TP2_BE", "SL_HIT")
    return _render_board(sig, candles, row=row, proof_mode=is_proof, tracking_event=ev_u)


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


# ---------------------------------------------------------------------------
# v8.1.0 Ultra-Luxury Institutional Market Analysis Chart (1600x1000)
# Automatic for Users, Admin, Partners & Channels — ForexYemeni Branded Crest
# ---------------------------------------------------------------------------

def _ema_series(values: list[float], period: int) -> list[float]:
    if not values:
        return []
    k = 2.0 / (period + 1)
    out = [float(values[0])]
    for v in values[1:]:
        out.append(float(v) * k + out[-1] * (1.0 - k))
    return out


def _rsi_series(values: list[float], period: int = 14) -> list[float]:
    if len(values) < 2:
        return [50.0] * len(values)
    out = [50.0]
    gains: list[float] = []
    losses: list[float] = []
    avg_g = 0.0
    avg_l = 0.0
    for i in range(1, len(values)):
        diff = float(values[i]) - float(values[i - 1])
        g = max(0.0, diff)
        l = max(0.0, -diff)
        if i <= period:
            gains.append(g)
            losses.append(l)
            avg_g = sum(gains) / max(1, len(gains))
            avg_l = sum(losses) / max(1, len(losses))
        else:
            avg_g = (avg_g * (period - 1) + g) / period
            avg_l = (avg_l * (period - 1) + l) / period
        if avg_l <= 1e-12:
            out.append(100.0 if avg_g > 0 else 50.0)
        else:
            rs = avg_g / avg_l
            out.append(100.0 - (100.0 / (1.0 + rs)))
    return out


def _draw_forexyemeni_emblem(draw, cx: int, cy: int, size: int = 58) -> None:
    """Draw the bespoke Royal Gold ForexYemeni Shield + Crown + Bullish Spark emblem."""
    r = size // 2
    # Outer metallic gold shield polygon
    shield_outer = [
        (cx, cy - r),
        (cx + int(r * 0.88), cy - int(r * 0.56)),
        (cx + int(r * 0.78), cy + int(r * 0.32)),
        (cx, cy + r),
        (cx - int(r * 0.78), cy + int(r * 0.32)),
        (cx - int(r * 0.88), cy - int(r * 0.56)),
    ]
    shield_inner = [
        (cx, cy - int(r * 0.84)),
        (cx + int(r * 0.73), cy - int(r * 0.46)),
        (cx + int(r * 0.64), cy + int(r * 0.25)),
        (cx, cy + int(r * 0.83)),
        (cx - int(r * 0.64), cy + int(r * 0.25)),
        (cx - int(r * 0.73), cy - int(r * 0.46)),
    ]
    draw.polygon(shield_outer, fill=(212, 175, 55, 255), outline=(255, 224, 120, 255))
    draw.polygon(shield_inner, fill=(13, 18, 30, 255), outline=(170, 134, 34, 255))

    # Royal 3-peak gold crown at top inside shield
    cr_y = cy - int(r * 0.16)
    crown = [
        (cx - int(r * 0.42), cr_y),
        (cx - int(r * 0.48), cr_y - int(r * 0.34)),
        (cx - int(r * 0.18), cr_y - int(r * 0.14)),
        (cx, cr_y - int(r * 0.46)),
        (cx + int(r * 0.18), cr_y - int(r * 0.14)),
        (cx + int(r * 0.48), cr_y - int(r * 0.34)),
        (cx + int(r * 0.42), cr_y),
    ]
    draw.polygon(crown, fill=(245, 197, 66, 255), outline=(255, 232, 148, 255))
    # Crown base bar
    draw.rectangle(
        [cx - int(r * 0.42), cr_y + 1, cx + int(r * 0.42), cr_y + max(3, int(r * 0.10))],
        fill=(212, 175, 55, 255),
    )

    # Golden bullish spark / candlestick lightning below crown
    spark = [
        (cx - int(r * 0.38), cy + int(r * 0.42)),
        (cx - int(r * 0.10), cy + int(r * 0.18)),
        (cx + int(r * 0.08), cy + int(r * 0.32)),
        (cx + int(r * 0.38), cy + int(r * 0.04)),
    ]
    draw.line(spark, fill=(0, 220, 140, 255), width=max(3, int(r * 0.11)))
    # Tip dot
    ex, ey = spark[-1]
    draw.ellipse([ex - 3, ey - 3, ex + 3, ey + 3], fill=(245, 197, 66, 255))


def render_analysis_caption(an: dict, market_row=None) -> str:
    """Short ultra-luxury Arabic caption (<= 950 chars) for Telegram photo delivery."""
    from html import escape as _esc
    sym = str(an.get("symbol") or "XAUUSD").upper()
    tf = str(an.get("timeframe") or "M15")
    direction = str(an.get("direction") or "NEUTRAL").upper()
    quality = int(an.get("quality") or 0)
    price = float(an.get("price") or 0.0)
    px_str = instruments.fmt_price(sym, price, market_row) if price > 0 else "—"
    dir_badge = {
        "BUY": "🟢 انحياز صاعد (شراء)",
        "SELL": "🔴 انحياز هابط (بيع)",
    }.get(direction, "🟡 حيادي / انتظار تأكيد")
    cd = an.get("chart_data") or {}
    rsi_v = float(cd.get("rsi") or 50.0)
    kz_raw = str(cd.get("kz") or "").upper()
    kz_map = {"LONDON": "لندن", "NEW_YORK": "نيويورك", "ASIAN": "آسيا"}
    kz_txt = f" · 🕒 جلسة {kz_map.get(kz_raw, kz_raw)}" if kz_raw else ""
    filled = max(0, min(10, round(quality / 10)))
    qbar = "🟩" * filled + "⬜" * (10 - filled)
    setup = cd.get("setup") or {}
    setup_line = ""
    if setup and float(setup.get("entry") or 0) > 0 and float(setup.get("stop") or 0) > 0:
        e_s = instruments.fmt_price(sym, float(setup["entry"]), market_row)
        sl_s = instruments.fmt_price(sym, float(setup["stop"]), market_row)
        tp1_s = instruments.fmt_price(sym, float(setup.get("tp1") or 0), market_row)
        rr_v = float(setup.get("rr") or 0.0)
        setup_line = (
            f"\n▎🎯 خطة <b>{_esc(setup.get('code') or an.get('strategy') or 'PRO')}</b>: "
            f"دخول <code>{e_s}</code> · وقف <code>{sl_s}</code> · هدف1 <code>{tp1_s}</code> (<b>1:{rr_v:.1f}</b>)"
        )
    conf = an.get("confirmation") or {}
    conf_line = ""
    if isinstance(conf, dict) and conf.get("stability_tf"):
        st_tf = _esc(str(conf["stability_tf"]))
        cf_tf = _esc(str(conf.get("confirm_tf") or st_tf))
        st_ico = "🟢 مؤكّد فعلياً الآن" if conf.get("activated") else "⏳ بانتظار إغلاق جسم حقيقي"
        conf_line = f"\n▎🔐 الثبات والتأكيد: إغلاق <b>{st_tf}</b> + تأكيد <b>{cf_tf}</b> ({st_ico})"
    def _build(include_setup: bool, include_conf: bool) -> str:
        s_ln = setup_line if include_setup else ""
        c_ln = conf_line if include_conf else ""
        return (
            f"👑 <b>{config.BRAND} · الشارت التحليلي المؤسسي الفاخر</b>\n"
            f"<blockquote>"
            f"▎📊 السوق: <b>{_esc(sym)}</b> · الإطار: <b>{_esc(tf)}</b>\n"
            f"▎💵 السعر الحي: <code>{px_str}</code>{kz_txt}\n"
            f"▎🧭 الاتجاه: <b>{dir_badge}</b>\n"
            f"▎⭐ جودة السياق: <b>{quality}/100</b> {qbar}\n"
            f"▎📈 مؤشر RSI(14): <b>{rsi_v:.1f}</b> · المنهجية: <b>{_esc(str(an.get('strategy_name') or 'مؤسسي شامل')[:48])}</b>"
            f"{c_ln}"
            f"{s_ln}"
            f"</blockquote>\n"
            f"<i>⬇️ التقرير التفصيلي الكامل بالمناطق والأرقام أسفل الشارت مباشرة · {config.BOT_SIGNATURE}</i>"
        )

    # Guarantee valid closed HTML tags <= 1020 chars (NEVER slice mid-tag!)
    for inc_s, inc_c in ((True, True), (False, True), (False, False)):
        cap = _build(inc_s, inc_c)
        if len(cap) <= 1020:
            return cap
    return (
        f"👑 <b>{config.BRAND} · الشارت التحليلي</b>\n"
        f"▎📊 <b>{_esc(sym)}</b> ({_esc(tf)}) · <code>{px_str}</code> · <b>{dir_badge}</b> · {quality}/100"
    )


async def send_analysis_chart_photo(
    bot,
    chat_id,
    an: dict,
    *,
    market_row=None,
    reply_markup=None,
    brand_footer: str = "",
) -> tuple[bytes | None, str]:
    """v8.5.2: Guaranteed resilient delivery of Ultra-Luxury 1600x1000 Analysis Chart PNG.

    Independent of global periodic-chart toggles (just like v7.5.0 signal charts):
    analysis requests in Channels, Partners, Users, and True Analysis ALWAYS send
    the luxury chart PNG, with multi-step fallback (strip button styles -> plain
    caption -> minimal caption) so Telegram NEVER silently drops the chart!
    Returns `(png_bytes_or_None, final_caption)`.
    """
    import asyncio
    import re
    from fysb.ui import keyboards as _kb

    if bot is None or not hasattr(bot, "send_photo") or not an:
        return None, ""
    try:
        png = await asyncio.to_thread(render_analysis_chart, an, market_row)
    except Exception as exc:
        log.warning("render_analysis_chart failed for %s: %s", an.get("symbol"), exc)
        return None, ""
    if not png:
        log.warning("render_analysis_chart returned empty PNG for %s", an.get("symbol"))
        return None, ""

    cap = render_analysis_caption(an, market_row=market_row)
    if brand_footer and (len(cap) + len(brand_footer) <= 1020):
        cap = cap + brand_footer

    kw_base = {"chat_id": chat_id, "caption": cap, "parse_mode": "HTML"}
    if reply_markup is not None:
        kw_base["reply_markup"] = reply_markup

    # Attempt 1: Full luxury HTML caption + styled buttons
    try:
        await bot.send_photo(photo=io.BytesIO(png), **kw_base)
        return png, cap
    except Exception as e1:
        log.warning("analysis chart send_photo attempt 1 failed (%s -> %s): %s", an.get("symbol"), chat_id, e1)

    # Attempt 2: Strip custom Telegram button styles (older Bot API clients / groups)
    if reply_markup is not None:
        try:
            await bot.send_photo(
                chat_id=chat_id,
                photo=io.BytesIO(png),
                caption=cap,
                parse_mode="HTML",
                reply_markup=_kb.strip_button_styles(reply_markup),
            )
            return png, cap
        except Exception as e2:
            log.warning("analysis chart send_photo attempt 2 failed (%s -> %s): %s", an.get("symbol"), chat_id, e2)

    # Attempt 3: Plain-text caption (guards against any malformed HTML entity in brand footer)
    plain_cap = re.sub(r"<[^>]+>", "", cap)[:980]
    try:
        kw_plain = {"chat_id": chat_id, "photo": io.BytesIO(png), "caption": plain_cap}
        if reply_markup is not None:
            kw_plain["reply_markup"] = _kb.strip_button_styles(reply_markup)
        await bot.send_photo(**kw_plain)
        return png, plain_cap
    except Exception as e3:
        log.warning("analysis chart send_photo attempt 3 failed (%s -> %s): %s", an.get("symbol"), chat_id, e3)

    # Attempt 4: Minimal guaranteed delivery
    try:
        short_cap = f"👑 {config.BRAND} · {an.get('symbol', '')} ({an.get('timeframe', '')})"
        await bot.send_photo(chat_id=chat_id, photo=io.BytesIO(png), caption=short_cap)
        return png, short_cap
    except Exception as e4:
        log.warning("analysis chart send_photo final attempt failed (%s -> %s): %s", an.get("symbol"), chat_id, e4)
        return None, ""


def render_analysis_chart(an: dict, market_row=None) -> bytes | None:
    """v8.1.0 Ultra-Luxury 1600x1000 Institutional Analysis Chart PNG with ForexYemeni Crest.

    Renders real confirmed candlesticks + EMA 21/50 + Order Blocks + FVG + Premium/Discount EQ 50%
    + Support/Resistance + BOS/CHOCH + Liquidity Sweeps + Activation/Invalidation (or full
    Entry/SL/TP1-TP3 R:R plan) + dedicated RSI(14) sub-panel + 5 institutional KPI summary cards.
    """
    if not an:
        return None
    cd = an.get("chart_data") or {}
    bars = list(cd.get("candles") or [])[-85:]
    if len(bars) < 10:
        return None
    if not PIL_AVAILABLE:
        # Clean stdlib fallback reusing existing lightweight board renderer
        synth_sig = {
            "symbol": an.get("symbol", "XAUUSD"),
            "direction": an.get("direction", "BUY") if an.get("direction") in ("BUY", "SELL") else "BUY",
            "timeframe": cd.get("tf") or an.get("timeframe") or "M15",
            "strategy": an.get("strategy") or "INSTITUTIONAL",
            "score": int(an.get("quality") or 0),
            "rr": float((cd.get("setup") or {}).get("rr") or 2.0),
            "entry": float((cd.get("setup") or {}).get("entry") or an.get("price") or bars[-1]["close"]),
            "stop": float((cd.get("setup") or {}).get("stop") or cd.get("invalidation_px") or 0.0),
            "tp1": float((cd.get("setup") or {}).get("tp1") or cd.get("activation_px") or 0.0),
            "tp2": float((cd.get("setup") or {}).get("tp2") or 0.0),
            "tp3": float((cd.get("setup") or {}).get("tp3") or 0.0),
        }
        return _render_board_stdlib(synth_sig, bars[-48:], row=market_row)

    W, H = 1600, 1000
    OBSIDIAN = (9, 12, 18, 255)
    CARD_BG = (15, 20, 31, 255)
    CARD_BORDER = (38, 48, 70, 255)
    CYAN_EMA = (56, 189, 248, 235)
    PURPLE_SWEEP = (192, 132, 252, 240)
    AMBER_FVG = (251, 191, 36, 255)

    img = Image.new("RGBA", (W, H), OBSIDIAN)
    overlay = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    draw = ImageDraw.Draw(img)
    odraw = ImageDraw.Draw(overlay)

    f_logo = _load_font(26, "bold")
    f_sub_logo = _load_font(12, "reg")
    f_sym = _load_font(30, "bold")
    f_price_big = _load_font(28, "mono")
    f_badge = _load_font(16, "bold")
    f_small_b = _load_font(13, "bold")
    f_mono = _load_font(14, "mono")
    f_mono_sm = _load_font(12, "mono")
    f_kpi_lbl = _load_font(12, "bold")
    f_kpi_val = _load_font(18, "bold")
    f_kpi_sub = _load_font(12, "reg")
    f_wm = _load_font(76, "bold")

    symbol = str(an.get("symbol") or "XAUUSD").upper()
    tf_raw = str(cd.get("tf") or an.get("timeframe") or "M15").upper()
    tf_disp = "MULTI-TF" if cd.get("multi_tf") else tf_raw
    direction = str(an.get("direction") or "NEUTRAL").upper()
    quality = max(0, min(100, int(an.get("quality") or 0)))
    strat_code = str(an.get("strategy") or "INSTITUTIONAL").upper()
    if strat_code == "ALL":
        strat_code = "INSTITUTIONAL ALL"
    live_px = float(an.get("price") or bars[-1]["close"])
    price_str = instruments.fmt_price(symbol, live_px, market_row)
    kz_raw = str(cd.get("kz") or "").upper()
    sr = cd.get("sr") or {}
    obs = cd.get("obs") or []
    fvgs = cd.get("fvgs") or []
    pd_zone = cd.get("pd") or {}
    sweep = cd.get("sweep") or {}
    struct = cd.get("struct") or {}
    setup = cd.get("setup") or {}
    act_px = float(cd.get("activation_px") or 0.0)
    inv_px = float(cd.get("invalidation_px") or 0.0)
    rsi_val = float(cd.get("rsi") or 50.0)
    atr_val = float(cd.get("atr") or 0.0)
    atr_pct = float(cd.get("atr_pct") or 0.0)
    e9 = float(cd.get("e9") or 0.0)
    e21_v = float(cd.get("e21") or 0.0)
    e50_v = float(cd.get("e50") or 0.0)

    # ---- Outer Luxury Double-Bevel Gold Frame ----
    draw.rounded_rectangle([8, 8, W - 9, H - 9], radius=22, outline=(46, 58, 84, 255), width=2)
    draw.rounded_rectangle([13, 13, W - 14, H - 14], radius=18, outline=(212, 175, 55, 95), width=1)
    # Top royal metallic sheen header
    draw.rounded_rectangle([16, 16, W - 17, 118], radius=14, fill=HEADER_BG)
    draw.line([(24, 16), (420, 16)], fill=BORDER_GOLD, width=3)
    draw.line([(W - 420, 16), (W - 24, 16)], fill=BORDER_GOLD, width=3)
    draw.line([(16, 118), (W - 17, 118)], fill=BORDER_GOLD, width=2)

    # ---- Header Left: ForexYemeni Crest Emblem + Brand ----
    _draw_forexyemeni_emblem(draw, 58, 67, size=62)
    draw.text((100, 32), "FOREXYEMENI", fill=BORDER_GOLD, font=f_logo)
    draw.text((101, 64), "ROYAL INSTITUTIONAL ANALYSIS DESK", fill=TEXT_MUTED, font=f_sub_logo)
    draw.text((101, 82), f"{config.BOT_SIGNATURE}  ·  v{config.VERSION}", fill=GOLD_SOFT, font=f_sub_logo)

    # Vertical gold divider
    draw.line([(396, 28), (396, 106)], fill=(212, 175, 55, 80), width=1)

    # ---- Header Center: Symbol · TF + Big Live Price + Bias Pill ----
    draw.text((418, 28), f"{symbol}  ·  {tf_disp}", fill=TEXT_LIGHT, font=f_sym)
    draw.text((418, 68), f"LIVE  {price_str}", fill=GOLD_SOFT, font=f_price_big)

    dir_cfg = {
        "BUY": ("▲ BULLISH BIAS", BULL_COLOR, OBSIDIAN),
        "SELL": ("▼ BEARISH BIAS", BEAR_COLOR, TEXT_LIGHT),
    }.get(direction, ("◆ NEUTRAL / WAIT", BORDER_GOLD, OBSIDIAN))
    px_cur = 770
    w_dir = _draw_pill(draw, px_cur, 30, dir_cfg[0], dir_cfg[1], dir_cfg[2], f_badge, pad_x=14, pad_y=6)
    px_cur += w_dir + 10
    w_st = _draw_pill(draw, px_cur, 30, strat_code[:20], (30, 41, 64, 255), GOLD_SOFT, f_badge, pad_x=12, pad_y=6)
    px_cur += w_st + 10
    if kz_raw:
        _draw_pill(draw, px_cur, 30, f"{kz_raw} KZ", (44, 34, 68, 255), (216, 180, 254, 255), f_small_b, pad_x=10, pad_y=7)

    # Second row under pills: Multi-TF alignment or Institutional tag
    tf_votes = cd.get("tf_votes") or []
    sub_x = 770
    if len(tf_votes) > 1:
        for t_code, t_dir in tf_votes[:4]:
            t_col = BULL_COLOR if t_dir == "BUY" else (BEAR_COLOR if t_dir == "SELL" else TEXT_MUTED)
            sym_arrow = "▲" if t_dir == "BUY" else ("▼" if t_dir == "SELL" else "◆")
            tw = _draw_pill(draw, sub_x, 74, f"{t_code} {sym_arrow}", (22, 29, 44, 255), t_col, f_small_b, pad_x=9, pad_y=4)
            sub_x += tw + 8
    else:
        tr_lbl = {"up": "UPTREND", "down": "DOWNTREND", "range": "CONSOLIDATION"}.get(str(struct.get("trend") or "range"), "NEUTRAL")
        tr_col = BULL_COLOR if tr_lbl == "UPTREND" else (BEAR_COLOR if tr_lbl == "DOWNTREND" else GOLD_SOFT)
        w1 = _draw_pill(draw, sub_x, 74, f"STRUCTURE: {tr_lbl}", (22, 29, 44, 255), tr_col, f_small_b, pad_x=10, pad_y=4)
        sub_x += w1 + 8
        if struct.get("bos"):
            w2 = _draw_pill(draw, sub_x, 74, "BOS CONFIRMED", (18, 52, 42, 255), BULL_COLOR, f_small_b, pad_x=10, pad_y=4)
            sub_x += w2 + 8
        if struct.get("choch"):
            _draw_pill(draw, sub_x, 74, "CHOCH SHIFT", (60, 44, 18, 255), GOLD_SOFT, f_small_b, pad_x=10, pad_y=4)

    # ---- Header Far-Right: Quality Score Gauge ----
    gx0, gy0, gx1, gy1 = W - 265, 26, W - 28, 108
    draw.rounded_rectangle([gx0, gy0, gx1, gy1], radius=12, fill=(21, 28, 43, 255), outline=(212, 175, 55, 110), width=1)
    q_col = BULL_COLOR if quality >= 75 else (GOLD_SOFT if quality >= 55 else BEAR_COLOR)
    draw.text((gx0 + 14, gy0 + 10), "CONTEXT QUALITY", fill=TEXT_MUTED, font=f_kpi_lbl)
    draw.text((gx0 + 14, gy0 + 28), f"{quality} / 100", fill=q_col, font=f_sym)
    # Progress bar inside gauge
    bx0, by0, bx1, by1 = gx0 + 14, gy1 - 16, gx1 - 14, gy1 - 8
    draw.rounded_rectangle([bx0, by0, bx1, by1], radius=4, fill=(32, 40, 58, 255))
    fill_w = max(8, int((bx1 - bx0) * (quality / 100.0)))
    draw.rounded_rectangle([bx0, by0, bx0 + fill_w, by1], radius=4, fill=q_col)

    # ---- Main Candlestick Chart Panel ----
    plot_l, plot_t, plot_r, plot_b = 30, 134, W - 195, 694
    draw.rounded_rectangle([plot_l, plot_t, plot_r, plot_b], radius=14, fill=PANEL_BG, outline=CARD_BORDER, width=1)
    # Right price axis background strip
    draw.rounded_rectangle([plot_r + 6, plot_t, W - 24, plot_b], radius=12, fill=(13, 18, 28, 255), outline=(30, 38, 56, 255), width=1)

    highs = [float(b["high"]) for b in bars]
    lows = [float(b["low"]) for b in bars]
    closes = [float(b["close"]) for b in bars]

    # Collect price range including candles + near key levels (never squash candles!)
    c_min, c_max = min(lows), max(highs)
    c_span = max(c_max - c_min, max(abs(live_px) * 0.001, 1e-6))
    ref_prices = list(highs) + list(lows)
    for lv in (
        sr.get("support"), sr.get("resistance"),
        setup.get("entry"), setup.get("stop"), setup.get("tp1"), setup.get("tp2"),
        act_px, inv_px, sweep.get("level"),
    ):
        try:
            v = float(lv or 0.0)
            if v > 0 and (c_min - c_span * 1.15) <= v <= (c_max + c_span * 1.15):
                ref_prices.append(v)
        except (TypeError, ValueError):
            pass
    p_lo = min(ref_prices)
    p_hi = max(ref_prices)
    pad = max((p_hi - p_lo) * 0.11, c_span * 0.08)
    p_lo -= pad
    p_hi += pad
    p_span = max(p_hi - p_lo, 1e-9)

    def y_of(price: float) -> int:
        frac = (price - p_lo) / p_span
        return int(plot_b - 18 - frac * (plot_b - plot_t - 36))

    # Horizontal grid + right-axis tick labels
    for i in range(7):
        gy = int(plot_t + 18 + i * (plot_b - plot_t - 36) / 6)
        draw.line([(plot_l + 4, gy), (plot_r - 4, gy)], fill=GRID_COLOR, width=1)
        gp = p_hi - i * (p_hi - p_lo) / 6
        draw.text((plot_r + 16, gy - 7), instruments.fmt_price(symbol, gp, market_row), fill=TEXT_MUTED, font=f_mono_sm)

    # Vertical grid lines
    for i in range(1, 7):
        gx = int(plot_l + i * (plot_r - plot_l) / 7)
        draw.line([(gx, plot_t + 4), (gx, plot_b - 4)], fill=(23, 30, 45, 255), width=1)

    # Grand subtle center ForexYemeni Watermark
    wm_txt = "FOREXYEMENI"
    wb = odraw.textbbox((0, 0), wm_txt, font=f_wm)
    w_cx = (plot_l + plot_r) // 2 - (wb[2] - wb[0]) // 2
    w_cy = (plot_t + plot_b) // 2 - (wb[3] - wb[1]) // 2
    odraw.text((w_cx, w_cy), wm_txt, fill=(212, 175, 55, 20), font=f_wm)

    # ---- Overlay 1: Premium / Discount Equilibrium 50% ----
    pd_hi = float(pd_zone.get("high") or 0.0)
    pd_lo = float(pd_zone.get("low") or 0.0)
    if pd_hi > pd_lo > 0:
        eq = (pd_hi + pd_lo) / 2.0
        if p_lo <= eq <= p_hi:
            yeq = y_of(eq)
            _draw_dashed_line(odraw, plot_l + 10, plot_r - 10, yeq, (212, 175, 55, 110), dash=8, gap=8, width=1)
            odraw.text((plot_l + 18, yeq - 16), f"EQ 50%  {instruments.fmt_price(symbol, eq, market_row)}", fill=(212, 175, 55, 165), font=f_mono_sm)

    # ---- Overlay 2: Order Blocks (Institutional Demand / Supply Zones) ----
    for ob in obs[:2]:
        ob_lo, ob_hi = float(ob.get("lo") or 0.0), float(ob.get("hi") or 0.0)
        if ob_hi <= ob_lo or ob_hi < p_lo or ob_lo > p_hi:
            continue
        is_bull = str(ob.get("kind") or "") == "bullish"
        y1 = max(plot_t + 6, y_of(min(ob_hi, p_hi)))
        y2 = min(plot_b - 6, y_of(max(ob_lo, p_lo)))
        if y2 - y1 < 8:
            y2 = min(plot_b - 6, y1 + 8)
        fill_c = (22, 199, 132, 34) if is_bull else (234, 57, 67, 34)
        out_c = (22, 199, 132, 150) if is_bull else (234, 57, 67, 150)
        ox0 = plot_l + int((plot_r - plot_l) * 0.42)
        odraw.rectangle([ox0, y1, plot_r - 4, y2], fill=fill_c, outline=out_c, width=1)
        lbl = "BULL OB" if is_bull else "BEAR OB"
        lb_x = plot_r - 78
        odraw.rounded_rectangle([lb_x - 4, y1 + 2, plot_r - 8, y1 + 18], radius=4, fill=(10, 14, 22, 210))
        odraw.text((lb_x, y1 + 3), lbl, fill=BULL_COLOR if is_bull else BEAR_COLOR, font=f_mono_sm)

    # ---- Overlay 3: Fair Value Gaps (FVG) ----
    for fvg in fvgs[:2]:
        f_lo, f_hi = float(fvg.get("lo") or 0.0), float(fvg.get("hi") or 0.0)
        if f_hi <= f_lo or f_hi < p_lo or f_lo > p_hi:
            continue
        is_bull = str(fvg.get("kind") or "") == "bullish"
        y1 = max(plot_t + 6, y_of(min(f_hi, p_hi)))
        y2 = min(plot_b - 6, y_of(max(f_lo, p_lo)))
        if y2 - y1 < 6:
            y2 = min(plot_b - 6, y1 + 6)
        fill_c = (56, 189, 248, 28) if is_bull else (251, 191, 36, 26)
        out_c = (56, 189, 248, 130) if is_bull else (251, 191, 36, 130)
        fx0 = plot_l + int((plot_r - plot_l) * 0.58)
        odraw.rectangle([fx0, y1, plot_r - 4, y2], fill=fill_c, outline=out_c, width=1)
        flbl = "FVG +" if is_bull else "FVG -"
        fb_x = plot_r - 66
        fb_y = max(plot_t + 6, y1 + 2)
        odraw.rounded_rectangle([fb_x - 4, fb_y, plot_r - 8, fb_y + 16], radius=4, fill=(10, 14, 22, 210))
        odraw.text((fb_x, fb_y + 1), flbl, fill=CYAN_EMA if is_bull else AMBER_FVG, font=f_mono_sm)

    # ---- Overlay 4: Risk/Reward Shaded Box (if Strategy Lens found a full Setup!) ----
    s_entry = float(setup.get("entry") or 0.0)
    s_stop = float(setup.get("stop") or 0.0)
    s_tp1 = float(setup.get("tp1") or 0.0)
    s_tp2 = float(setup.get("tp2") or 0.0)
    if s_entry > 0 and s_stop > 0 and s_tp1 > 0 and p_lo <= s_entry <= p_hi:
        bx_l = plot_l + int((plot_r - plot_l) * 0.72)
        ye = y_of(s_entry)
        ys = y_of(max(p_lo, min(p_hi, s_stop)))
        yt = y_of(max(p_lo, min(p_hi, s_tp1)))
        odraw.rectangle([bx_l, min(ye, ys), plot_r - 4, max(ye, ys)], fill=(234, 57, 67, 42))
        odraw.rectangle([bx_l, min(ye, yt), plot_r - 4, max(ye, yt)], fill=(22, 199, 132, 36))

    # Composite translucent overlays before drawing sharp EMAs & Candlesticks
    img = Image.alpha_composite(img, overlay)
    draw = ImageDraw.Draw(img)

    # ---- EMA 21 & EMA 50 Curves ----
    n = len(bars)
    usable_w = plot_r - plot_l - 36
    step = usable_w / max(n, 1)
    xs = [int(plot_l + 18 + i * step + step / 2) for i in range(n)]

    ema21_pts = _ema_series(closes, 21)
    ema50_pts = _ema_series(closes, 50)
    if len(xs) >= 2:
        pts21 = [(xs[i], max(plot_t + 6, min(plot_b - 6, y_of(ema21_pts[i])))) for i in range(n)]
        pts50 = [(xs[i], max(plot_t + 6, min(plot_b - 6, y_of(ema50_pts[i])))) for i in range(n)]
        draw.line(pts50, fill=CYAN_EMA, width=2)
        draw.line(pts21, fill=GOLD_SOFT, width=2)

    # ---- Candlesticks ----
    cw = max(5, min(14, int(step * 0.64)))
    for i, b in enumerate(bars):
        o, h, l, c = float(b["open"]), float(b["high"]), float(b["low"]), float(b["close"])
        cx = xs[i]
        col = BULL_COLOR if c >= o else BEAR_COLOR
        draw.line([(cx, y_of(h)), (cx, y_of(l))], fill=col, width=2)
        yt = min(y_of(o), y_of(c))
        yb = max(y_of(o), y_of(c))
        if yb - yt < 2:
            yb = yt + 2
        draw.rectangle([cx - cw // 2, yt, cx + cw // 2, yb], fill=col, outline=col)

    # ---- Chart Top-Left Legend (EMA 21 / EMA 50 / Zones) ----
    lx, ly = plot_l + 16, plot_t + 12
    draw.rounded_rectangle([lx, ly, lx + 356, ly + 28], radius=7, fill=(11, 15, 24, 215), outline=(42, 54, 78, 255), width=1)
    draw.line([(lx + 10, ly + 14), (lx + 28, ly + 14)], fill=GOLD_SOFT, width=3)
    draw.text((lx + 34, ly + 7), "EMA 21", fill=GOLD_SOFT, font=f_mono_sm)
    draw.line([(lx + 98, ly + 14), (lx + 116, ly + 14)], fill=CYAN_EMA, width=3)
    draw.text((lx + 122, ly + 7), "EMA 50", fill=CYAN_EMA, font=f_mono_sm)
    draw.rectangle([lx + 186, ly + 8, lx + 198, ly + 20], fill=(22, 199, 132, 140))
    draw.text((lx + 204, ly + 7), "OB", fill=TEXT_LIGHT, font=f_mono_sm)
    draw.rectangle([lx + 236, ly + 8, lx + 248, ly + 20], fill=(56, 189, 248, 140))
    draw.text((lx + 254, ly + 7), "FVG", fill=TEXT_LIGHT, font=f_mono_sm)
    draw.text((lx + 292, ly + 7), "EQ 50%", fill=BORDER_GOLD, font=f_mono_sm)

    # ---- Horizontal Key Price Levels + Anti-Overlap Right-Axis Tags ----
    level_items: list[tuple[str, float, tuple, bool]] = []
    if s_entry > 0 and s_stop > 0 and s_tp1 > 0:
        level_items.append(("ENTRY", s_entry, ENTRY_COLOR, True))
        level_items.append(("SL", s_stop, SL_COLOR, False))
        level_items.append(("TP1", s_tp1, TP_COLOR, False))
        if s_tp2 > 0:
            level_items.append(("TP2", s_tp2, TP2_COLOR, False))
    else:
        if sr.get("resistance"):
            level_items.append(("RES", float(sr["resistance"]), BEAR_COLOR, False))
        if sr.get("support"):
            level_items.append(("SUP", float(sr["support"]), BULL_COLOR, False))
        if act_px > 0 and all(abs(act_px - x[1]) > p_span * 0.015 for x in level_items):
            level_items.append(("ACT", act_px, GOLD_SOFT, False))
        if inv_px > 0 and all(abs(inv_px - x[1]) > p_span * 0.015 for x in level_items):
            level_items.append(("INV", inv_px, SL_COLOR, False))
    if sweep.get("level"):
        sw_lv = float(sweep["level"])
        sw_tag = "BSL" if sweep.get("buy_side") else "SSL"
        level_items.append((sw_tag, sw_lv, PURPLE_SWEEP, False))
    # Live Price tag always visible
    level_items.append(("LIVE", live_px, (255, 255, 255, 255), True))

    used_tag_y: list[int] = []
    for tag, lv, col, solid in level_items:
        if lv <= 0 or not (p_lo <= lv <= p_hi):
            continue
        ly_px = y_of(lv)
        if tag == "LIVE":
            _draw_dashed_line(draw, plot_l + 6, plot_r - 4, ly_px, (245, 197, 66, 200), dash=4, gap=4, width=1)
        elif solid:
            draw.line([(plot_l + 6, ly_px), (plot_r - 4, ly_px)], fill=col, width=2)
        else:
            _draw_dashed_line(draw, plot_l + 6, plot_r - 4, ly_px, col, dash=10, gap=6, width=2)

        tag_y = ly_px - 12
        for prev_y in used_tag_y:
            if abs(tag_y - prev_y) < 26:
                tag_y = prev_y + 26
        tag_y = max(plot_t + 6, min(plot_b - 26, tag_y))
        used_tag_y.append(tag_y)

        fg_col = OBSIDIAN if tag in ("LIVE", "ENTRY", "ACT", "TP1", "TP2", "SUP", "BSL", "SSL") else TEXT_LIGHT
        pill_bg = GOLD_SOFT if tag == "LIVE" else col
        draw.rounded_rectangle([plot_r + 9, tag_y - 1, W - 27, tag_y + 24], radius=6, fill=pill_bg, outline=OBSIDIAN, width=1)
        p_txt = instruments.fmt_price(symbol, lv, market_row)
        draw.text((plot_r + 16, tag_y + 4), f"{tag} {p_txt}"[:17], fill=fg_col, font=f_mono_sm)

    # ---- RSI (14) Dedicated Institutional Sub-Panel ----
    rsi_l, rsi_t, rsi_r, rsi_b = 30, 708, W - 24, 838
    draw.rounded_rectangle([rsi_l, rsi_t, rsi_r, rsi_b], radius=12, fill=PANEL_BG, outline=CARD_BORDER, width=1)

    def y_rsi(val: float) -> int:
        v = max(0.0, min(100.0, float(val)))
        return int(rsi_b - 14 - (v / 100.0) * (rsi_b - rsi_t - 28))

    # Overbought/Oversold subtle bands
    rsi_ov = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    rod = ImageDraw.Draw(rsi_ov)
    rod.rectangle([rsi_l + 4, rsi_t + 4, plot_r - 4, y_rsi(70)], fill=(234, 57, 67, 22))
    rod.rectangle([rsi_l + 4, y_rsi(30), plot_r - 4, rsi_b - 4], fill=(22, 199, 132, 22))
    img = Image.alpha_composite(img, rsi_ov)
    draw = ImageDraw.Draw(img)

    for lvl, lcol in ((70, (234, 57, 67, 130)), (50, (100, 116, 139, 110)), (30, (22, 199, 132, 130))):
        yr = y_rsi(lvl)
        _draw_dashed_line(draw, rsi_l + 8, plot_r - 8, yr, lcol, dash=6, gap=6, width=1)
        draw.text((plot_r + 16, yr - 7), str(lvl), fill=TEXT_MUTED, font=f_mono_sm)

    rsi_pts = _rsi_series(closes, 14)
    start_r = min(14, max(0, n - 5))
    if len(xs) - start_r >= 2:
        r_line = [(xs[i], y_rsi(rsi_pts[i])) for i in range(start_r, n)]
        draw.line(r_line, fill=(168, 85, 247, 255), width=2)
        lx_r, ly_r = r_line[-1]
        draw.ellipse([lx_r - 4, ly_r - 4, lx_r + 4, ly_r + 4], fill=GOLD_SOFT, outline=OBSIDIAN)

    rsi_state = "OVERBOUGHT" if rsi_val >= 70 else ("OVERSOLD" if rsi_val <= 30 else "BALANCED")
    rsi_col = BEAR_COLOR if rsi_val >= 70 else (BULL_COLOR if rsi_val <= 30 else GOLD_SOFT)
    draw.rounded_rectangle([rsi_l + 12, rsi_t + 6, rsi_l + 245, rsi_t + 28], radius=6, fill=(11, 15, 24, 230), outline=(42, 54, 78, 255), width=1)
    draw.text((rsi_l + 20, rsi_t + 10), f"RSI (14): {rsi_val:.1f} · {rsi_state}", fill=rsi_col, font=f_small_b)
    # Right side RSI big value pill
    draw.rounded_rectangle([plot_r + 52, rsi_t + 36, W - 36, rsi_b - 36], radius=10, fill=(22, 29, 44, 255), outline=rsi_col, width=1)
    draw.text((plot_r + 64, rsi_t + 44), "RSI 14", fill=TEXT_MUTED, font=f_kpi_lbl)
    draw.text((plot_r + 64, rsi_t + 62), f"{rsi_val:.1f}", fill=rsi_col, font=f_kpi_val)

    # ---- 5 Institutional KPI Summary Cards Footer ----
    ema_order = "BULL ALIGNED" if (e9 > e21_v > e50_v and e50_v > 0) else ("BEAR ALIGNED" if (e9 < e21_v < e50_v and e50_v > 0) else "MIXED / RANGE")
    ema_col = BULL_COLOR if ema_order.startswith("BULL") else (BEAR_COLOR if ema_order.startswith("BEAR") else GOLD_SOFT)
    st_trend = {"up": "BULLISH", "down": "BEARISH", "range": "RANGE"}.get(str(struct.get("trend") or "range"), "RANGE")
    st_tags = " · ".join(t for t, flg in (("BOS", struct.get("bos")), ("CHOCH", struct.get("choch"))) if flg) or "SWING STRUCTURE"
    st_col = BULL_COLOR if st_trend == "BULLISH" else (BEAR_COLOR if st_trend == "BEARISH" else GOLD_SOFT)
    vol_lbl = "HIGH VOL" if atr_pct > 3.0 else ("NORMAL" if atr_pct > 0.5 else "CALM")
    if s_entry > 0 and float(setup.get("rr") or 0) > 0:
        k5_lbl = "STRATEGY PLAN"
        k5_val = f"R:R  1 : {float(setup['rr']):.2f}"
        k5_sub = f"TP1 {instruments.fmt_price(symbol, s_tp1, market_row)}"
        k5_col = BULL_COLOR
    else:
        k5_lbl = "KEY TRIGGER"
        k5_val = instruments.fmt_price(symbol, act_px, market_row) if act_px > 0 else "WAIT BREAK"
        k5_sub = f"INV {instruments.fmt_price(symbol, inv_px, market_row)}" if inv_px > 0 else "NO FORCED ENTRY"
        k5_col = GOLD_SOFT

    kpis = [
        ("MARKET STRUCTURE", st_trend, st_tags, st_col),
        ("EMA 9 / 21 / 50", ema_order, f"E21 {instruments.fmt_price(symbol, e21_v, market_row)}" if e21_v > 0 else "MOVING AVGS", ema_col),
        ("MOMENTUM (RSI)", f"{rsi_val:.1f}  ·  {rsi_state}", "14-PERIOD WILDER RSI", rsi_col),
        ("VOLATILITY (ATR)", f"{atr_val:.4g}  ({atr_pct:.2f}%)", f"REGIME: {vol_lbl}", CYAN_EMA),
        (k5_lbl, k5_val, k5_sub, k5_col),
    ]
    card_gap = 14
    card_w = (W - 54 - card_gap * 4) // 5
    cy0, cy1 = 852, 954
    for idx, (kl, kv, ks, kc) in enumerate(kpis):
        cx0 = 30 + idx * (card_w + card_gap)
        cx1 = cx0 + card_w
        draw.rounded_rectangle([cx0, cy0, cx1, cy1], radius=12, fill=CARD_BG, outline=CARD_BORDER, width=1)
        draw.line([(cx0 + 14, cy0), (cx0 + 90, cy0)], fill=kc, width=3)
        draw.text((cx0 + 16, cy0 + 14), kl, fill=TEXT_MUTED, font=f_kpi_lbl)
        draw.text((cx0 + 16, cy0 + 38), kv[:22], fill=kc, font=f_kpi_val)
        draw.text((cx0 + 16, cy0 + 70), ks[:28], fill=TEXT_MUTED, font=f_kpi_sub)

    # ---- Bottom Institutional Signature Line ----
    ts_str = str(an.get("price_time") or bars[-1].get("datetime") or "")[:19]
    draw.text((34, H - 36), f"FOREXYEMENI INSTITUTIONAL DESK  ·  REAL OHLC DATA  ·  {config.BOT_SIGNATURE}", fill=BORDER_GOLD, font=f_sub_logo)
    draw.text((W - 290, H - 36), f"CANDLE UTC: {ts_str}", fill=TEXT_MUTED, font=f_mono_sm)

    out = io.BytesIO()
    img.convert("RGB").save(out, format="PNG", optimize=True)
    return out.getvalue()

