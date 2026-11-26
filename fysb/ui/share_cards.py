"""Branded Royal PNG PnL Share Cards & Executive Performance Digest Cards (v7.6.0).

Pure-Pillow high-DPI institutional dark-gold graphics (zero paid APIs):
  - render_pnl_share_card(payload)        -> 1200x680 PNG bytes (Signals & Demo Wins)
  - render_performance_digest_card(stats) -> 1200x760 PNG bytes (Weekly / Monthly Digest)

Uses crisp DejaVu Sans / Mono typography with layered obsidian panels, hairline
gold borders, subtle radial glows, and referral attribution strip.
"""
from __future__ import annotations

import hashlib
import io
from datetime import datetime, timezone

from fysb import config

try:
    from PIL import Image, ImageDraw, ImageFont
    PIL_AVAILABLE = True
except Exception:
    PIL_AVAILABLE = False

# Obsidian & Royal Gold Palette
BG_TOP = (7, 10, 16, 255)
BG_BOT = (13, 18, 29, 255)
CARD_BG = (17, 23, 36, 255)
CARD_ALT = (21, 29, 46, 255)
GOLD = (212, 175, 55, 255)
GOLD_BRIGHT = (247, 211, 106, 255)
GOLD_DIM = (118, 96, 36, 255)
EMERALD = (22, 199, 132, 255)
EMERALD_SOFT = (18, 62, 52, 255)
CRIMSON = (242, 54, 69, 255)
CRIMSON_SOFT = (74, 24, 34, 255)
CYAN = (56, 189, 248, 255)
TEXT_MAIN = (241, 245, 249, 255)
TEXT_MUTED = (148, 163, 184, 255)
LINE_SUB = (36, 47, 68, 255)

_BOLD_FONTS = (
    "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
    "/usr/share/fonts/truetype/liberation/LiberationSans-Bold.ttf",
    "/system/fonts/Roboto-Bold.ttf",
)
_REG_FONTS = (
    "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
    "/usr/share/fonts/truetype/liberation/LiberationSans-Regular.ttf",
    "/system/fonts/Roboto-Regular.ttf",
)
_MONO_FONTS = (
    "/usr/share/fonts/truetype/dejavu/DejaVuSansMono-Bold.ttf",
    "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
)


def _font(size: int, style: str = "bold"):
    cands = _BOLD_FONTS if style == "bold" else (_MONO_FONTS if style == "mono" else _REG_FONTS)
    for path in cands:
        try:
            return ImageFont.truetype(path, size)
        except Exception:
            continue
    return ImageFont.load_default()


def _vertical_gradient(w: int, h: int, top: tuple, bot: tuple) -> "Image.Image":
    img = Image.new("RGBA", (w, h), top)
    draw = ImageDraw.Draw(img)
    for y in range(h):
        t = y / max(1, h - 1)
        r = int(top[0] + (bot[0] - top[0]) * t)
        g = int(top[1] + (bot[1] - top[1]) * t)
        b = int(top[2] + (bot[2] - top[2]) * t)
        draw.line([(0, y), (w, y)], fill=(r, g, b, 255))
    return img


def _draw_glow(img: "Image.Image", cx: int, cy: int, radius: int, rgb: tuple[int, int, int], max_alpha: int = 44):
    overlay = Image.new("RGBA", img.size, (0, 0, 0, 0))
    d = ImageDraw.Draw(overlay)
    steps = 16
    for i in range(steps, 0, -1):
        rad = int(radius * (i / steps))
        alpha = int(max_alpha * ((1.0 - (i - 1) / steps) ** 1.8))
        d.ellipse([cx - rad, cy - rad, cx + rad, cy + rad], fill=(rgb[0], rgb[1], rgb[2], alpha))
    img.alpha_composite(overlay)


def _draw_grid(draw: "ImageDraw.ImageDraw", w: int, h: int):
    for x in range(48, w, 64):
        draw.line([(x, 0), (x, h)], fill=(255, 255, 255, 7), width=1)
    for y in range(48, h, 64):
        draw.line([(0, y), (w, y)], fill=(255, 255, 255, 7), width=1)


def _draw_ref_matrix(draw: "ImageDraw.ImageDraw", x: int, y: int, size: int, seed_text: str):
    """Draw a crisp deterministic gold-on-obsidian geometric emblem / matrix badge."""
    cells = 7
    cell = size // cells
    digest = hashlib.sha256((seed_text or "FOREXYEMENI").encode("utf-8")).digest()
    draw.rounded_rectangle([x - 6, y - 6, x + cells * cell + 6, y + cells * cell + 6],
                           radius=10, fill=(10, 14, 22, 255), outline=GOLD, width=2)
    idx = 0
    for r in range(cells):
        for c in range(cells):
            # Symmetric institutional matrix pattern + corner finders
            is_corner = (r < 2 and c < 2) or (r < 2 and c >= cells - 2) or (r >= cells - 2 and c < 2)
            bit = (digest[(r * 4 + min(c, cells - 1 - c)) % len(digest)] >> (idx % 8)) & 1
            idx += 1
            if is_corner or bit:
                col = GOLD_BRIGHT if is_corner else GOLD
                px = x + c * cell + 1
                py = y + r * cell + 1
                draw.rounded_rectangle([px, py, px + cell - 2, py + cell - 2], radius=2, fill=col)


def _fmt_px(v) -> str:
    try:
        f = float(v or 0)
        if f == 0:
            return "—"
        if abs(f) >= 1000:
            return f"{f:,.2f}"
        if abs(f) >= 10:
            return f"{f:.3f}"
        return f"{f:.5f}"
    except Exception:
        return "—"


def render_pnl_share_card(payload: dict) -> bytes | None:
    """Render a 1200x680 branded Royal PNG PnL Share Card for a Signal or Demo Trade.

    Expected payload keys (all gracefully defaulted):
      kind: 'signal' | 'demo'
      symbol: e.g. 'XAUUSD'
      direction: 'BUY' | 'SELL'
      strategy: e.g. 'SMC'
      timeframe: e.g. 'M15'
      entry: float
      exit_price: float (or current/hit price)
      tp_label: str (e.g. 'TP3 HIT' / 'WIN' / '+4.2%')
      pips: float
      pnl_usd: float (optional — shown prominently for demo trades)
      pnl_pct: float (optional)
      rr: float (optional)
      score: int (optional)
      ref_link: str (e.g. 't.me/ForexYemeniBot?start=ref_12345')
      ref_label: str (optional tag)
      timestamp: str (optional ISO)
    """
    if not PIL_AVAILABLE:
        return None
    W, H = 1200, 680
    img = _vertical_gradient(W, H, BG_TOP, BG_BOT)
    is_win = (float(payload.get("pips") or 0) >= 0) and (float(payload.get("pnl_usd") or 0) >= 0)
    accent = EMERALD if is_win else CRIMSON
    accent_rgb = (22, 199, 132) if is_win else (242, 54, 69)

    _draw_glow(img, 960, 140, 320, (212, 175, 55), max_alpha=36)
    _draw_glow(img, 240, 480, 340, accent_rgb, max_alpha=34)

    draw = ImageDraw.Draw(img, "RGBA")
    _draw_grid(draw, W, H)

    # Outer double royal gold frame
    draw.rounded_rectangle([18, 18, W - 18, H - 18], radius=26, outline=GOLD, width=2)
    draw.rounded_rectangle([26, 26, W - 26, H - 26], radius=22, outline=(212, 175, 55, 65), width=1)

    # Top header strip
    f_brand = _font(28, "bold")
    f_sub = _font(16, "reg")
    f_pill = _font(17, "bold")
    f_sym = _font(64, "bold")
    f_hero = _font(78, "mono")
    f_hero_sub = _font(26, "bold")
    f_k_lbl = _font(15, "bold")
    f_k_val = _font(26, "mono")
    f_foot = _font(18, "mono")

    brand = str(config.BRAND or "FOREXYEMENI").upper()
    kind = str(payload.get("kind") or "signal").lower()
    badge_txt = "VERIFIED SIGNAL RESULT" if kind == "signal" else "VERIFIED DEMO TRADE"
    ts_str = config.mecca_chart_dt_12h(payload.get("timestamp") or None)

    draw.text((54, 46), f"{brand}  ROYAL", font=f_brand, fill=GOLD_BRIGHT)
    draw.text((54, 82), f"INSTITUTIONAL TRADING INTELLIGENCE  •  v{config.VERSION}", font=f_sub, fill=TEXT_MUTED)

    # Top-right verified pill
    draw.rounded_rectangle([W - 350, 46, W - 54, 88], radius=21, fill=(22, 199, 132, 32), outline=EMERALD, width=2)
    draw.text((W - 324, 57), f"★  {badge_txt}", font=f_pill, fill=EMERALD)
    draw.text((W - 230, 96), ts_str, font=f_sub, fill=TEXT_MUTED)

    # Divider line
    draw.line([(54, 126), (W - 54, 126)], fill=(212, 175, 55, 90), width=1)

    # Center Hero Left: Symbol + Direction + Strategy
    sym = str(payload.get("symbol") or "XAUUSD").upper()
    direction = str(payload.get("direction") or "BUY").upper()
    is_buy = direction in ("BUY", "LONG")
    dir_col = EMERALD if is_buy else CRIMSON
    dir_bg = EMERALD_SOFT if is_buy else CRIMSON_SOFT
    strat = str(payload.get("strategy") or "ROYAL").upper()
    tf = str(payload.get("timeframe") or "M15").upper()

    draw.text((56, 152), sym, font=f_sym, fill=TEXT_MAIN)

    # Direction & strategy pills under symbol
    pill_y = 236
    draw.rounded_rectangle([56, pill_y, 232, pill_y + 42], radius=12, fill=dir_bg, outline=dir_col, width=2)
    draw.text((84, pill_y + 10), "▲ BUY LONG" if is_buy else "▼ SELL SHORT", font=f_pill, fill=dir_col)

    draw.rounded_rectangle([246, pill_y, 495, pill_y + 42], radius=12, fill=CARD_ALT, outline=(212, 175, 55, 110), width=1)
    draw.text((266, pill_y + 10), f"{strat}  •  {tf}", font=f_pill, fill=GOLD_BRIGHT)

    # Center Hero Right: Big Luminous Profit Metric
    pips = float(payload.get("pips") or 0.0)
    pnl_usd = payload.get("pnl_usd")
    pnl_pct = payload.get("pnl_pct")

    if kind == "demo" and pnl_usd is not None:
        v_usd = float(pnl_usd)
        sign = "+" if v_usd >= 0 else ""
        main_num = f"{sign}${v_usd:,.2f}"
        sub_num = f"{'+' if float(pnl_pct or 0) >= 0 else ''}{float(pnl_pct or 0):.2f}% ACCOUNT RETURN"
    else:
        sign = "+" if pips >= 0 else ""
        main_num = f"{sign}{pips:.1f} PIPS"
        rr_val = float(payload.get("rr") or 0)
        sc_val = int(payload.get("score") or 0)
        sub_num = f"SCORE {sc_val}/100  •  R:R 1:{rr_val:.1f}" if rr_val > 0 else f"ROYAL VERIFIED SCORE {sc_val}/100"

    # Right-aligned luminous box
    draw.rounded_rectangle([560, 148, W - 54, 304], radius=22, fill=(14, 21, 34, 235), outline=accent, width=2)
    draw.text((592, 168), main_num, font=f_hero, fill=accent)
    draw.text((594, 256), sub_num, font=f_hero_sub, fill=GOLD_BRIGHT)

    # 4 Glass Metric Cards Row
    entry_str = _fmt_px(payload.get("entry"))
    exit_str = _fmt_px(payload.get("exit_price"))
    target_str = _fmt_px(payload.get("tp3") or payload.get("tp1") or payload.get("sl"))
    status_str = str(payload.get("tp_label") or ("WIN" if is_win else "CLOSED")).upper()[:14]

    cards_data = [
        ("ENTRY PRICE", entry_str, GOLD_BRIGHT),
        ("EXIT / HIT PRICE", exit_str, accent),
        ("TARGET / LEVEL", target_str, CYAN),
        ("OUTCOME STATUS", status_str, accent),
    ]
    cw = 258
    gap = 18
    start_x = 54
    cy_top = 342
    for idx, (lbl, val, col) in enumerate(cards_data):
        x0 = start_x + idx * (cw + gap)
        x1 = x0 + cw
        draw.rounded_rectangle([x0, cy_top, x1, cy_top + 118], radius=18, fill=CARD_BG, outline=LINE_SUB, width=1)
        draw.line([(x0 + 18, cy_top + 2), (x1 - 18, cy_top + 2)], fill=(col[0], col[1], col[2], 150), width=2)
        draw.text((x0 + 20, cy_top + 20), lbl, font=f_k_lbl, fill=TEXT_MUTED)
        draw.text((x0 + 20, cy_top + 56), val, font=f_k_val, fill=col)

    # Bottom Royal Attribution & Referral Strip
    fy0 = 498
    draw.rounded_rectangle([54, fy0, W - 54, H - 46], radius=20, fill=(12, 17, 28, 240), outline=(212, 175, 55, 130), width=1)
    ref_link = str(payload.get("ref_link") or f"t.me/{brand}Bot").strip()
    ref_label = str(payload.get("ref_label") or "JOIN THE ROYAL INSTITUTIONAL TRADING DESK").strip()

    _draw_ref_matrix(draw, 80, fy0 + 24, 88, ref_link)
    draw.text((196, fy0 + 26), ref_label, font=f_pill, fill=GOLD_BRIGHT)
    draw.text((196, fy0 + 58), ref_link, font=f_foot, fill=TEXT_MAIN)
    draw.text((196, fy0 + 94), "Real-Time Market Engine  •  Verified Price Tracking  •  Zero Fake Signals", font=f_sub, fill=TEXT_MUTED)

    buf = io.BytesIO()
    img.convert("RGB").save(buf, format="PNG", optimize=True)
    return buf.getvalue()


def render_performance_digest_card(digest: dict) -> bytes | None:
    """Render a 1200x760 Executive Performance Digest PNG (Weekly 7D / Monthly 30D)."""
    if not PIL_AVAILABLE:
        return None
    W, H = 1200, 760
    img = _vertical_gradient(W, H, BG_TOP, BG_BOT)
    _draw_glow(img, 1020, 120, 340, (212, 175, 55), max_alpha=38)
    _draw_glow(img, 180, 620, 320, (22, 199, 132), max_alpha=30)

    draw = ImageDraw.Draw(img, "RGBA")
    _draw_grid(draw, W, H)

    draw.rounded_rectangle([18, 18, W - 18, H - 18], radius=26, outline=GOLD, width=2)
    draw.rounded_rectangle([26, 26, W - 26, H - 26], radius=22, outline=(212, 175, 55, 60), width=1)

    f_title = _font(32, "bold")
    f_sub = _font(16, "reg")
    f_pill = _font(17, "bold")
    f_kpi_lbl = _font(15, "bold")
    f_kpi_val = _font(40, "mono")
    f_sec = _font(21, "bold")
    f_row = _font(19, "bold")
    f_mono = _font(18, "mono")

    days = int(digest.get("days") or 7)
    period_en = f"WEEKLY REPORT • LAST {days} DAYS" if days <= 10 else f"MONTHLY REPORT • LAST {days} DAYS"
    brand = str(config.BRAND or "FOREXYEMENI").upper()
    ts = config.mecca_chart_dt_12h(digest.get("generated_at") or None)

    draw.text((54, 44), f"{brand}  EXECUTIVE DIGEST", font=f_title, fill=GOLD_BRIGHT)
    draw.text((54, 86), f"VERIFIED SIGNAL PERFORMANCE AUDIT  •  {ts}", font=f_sub, fill=TEXT_MUTED)
    draw.rounded_rectangle([W - 380, 46, W - 54, 90], radius=22, fill=(212, 175, 55, 32), outline=GOLD, width=2)
    draw.text((W - 354, 58), f"🏆  {period_en}", font=f_pill, fill=GOLD_BRIGHT)

    draw.line([(54, 126), (W - 54, 126)], fill=(212, 175, 55, 90), width=1)

    total = int(digest.get("total") or 0)
    wr = float(digest.get("win_rate") or 0.0)
    net_pips = float(digest.get("net_pips") or 0.0)
    best_st = str(digest.get("best_strategy") or "—").upper()[:12]

    kpis = [
        ("TOTAL SIGNALS", f"{total}", TEXT_MAIN),
        ("WIN RATE", f"{wr:.1f}%", EMERALD if wr >= 55 else GOLD_BRIGHT),
        ("NET PIPS", f"{'+' if net_pips >= 0 else ''}{net_pips:.1f}", EMERALD if net_pips >= 0 else CRIMSON),
        ("TOP STRATEGY", best_st, GOLD_BRIGHT),
    ]
    cw, gap, sx, ky = 258, 18, 54, 148
    for i, (lbl, val, col) in enumerate(kpis):
        x0 = sx + i * (cw + gap)
        x1 = x0 + cw
        draw.rounded_rectangle([x0, ky, x1, ky + 124], radius=18, fill=CARD_BG, outline=LINE_SUB, width=1)
        draw.line([(x0 + 18, ky + 2), (x1 - 18, ky + 2)], fill=(col[0], col[1], col[2], 170), width=3)
        draw.text((x0 + 20, ky + 20), lbl, font=f_kpi_lbl, fill=TEXT_MUTED)
        draw.text((x0 + 20, ky + 56), val, font=f_kpi_val, fill=col)

    # Left panel: Strategy Win-Rate Bars
    lx0, ly0, lx1, ly1 = 54, 302, 740, 650
    draw.rounded_rectangle([lx0, ly0, lx1, ly1], radius=20, fill=CARD_BG, outline=LINE_SUB, width=1)
    draw.text((lx0 + 24, ly0 + 20), "STRATEGY PERFORMANCE MATRIX", font=f_sec, fill=GOLD_BRIGHT)

    strats = list(digest.get("top_strategies") or [])[:5]
    if not strats:
        draw.text((lx0 + 24, ly0 + 90), "No closed signals recorded in this period yet.", font=f_row, fill=TEXT_MUTED)
    else:
        for idx, s in enumerate(strats):
            ry = ly0 + 68 + idx * 54
            name = str(s.get("strategy") or "MANUAL").upper()[:14]
            swr = float(s.get("win_rate") or 0.0)
            spips = float(s.get("pips") or 0.0)
            stotal = int(s.get("total") or 0)
            draw.text((lx0 + 24, ry), f"{idx + 1}. {name}", font=f_row, fill=TEXT_MAIN)
            draw.text((lx0 + 470, ry), f"{swr:.0f}%  ({'+' if spips >= 0 else ''}{spips:.0f}p · {stotal})", font=f_mono, fill=EMERALD if spips >= 0 else CRIMSON)
            # Progress track
            bx0, bx1, by0, by1 = lx0 + 24, lx1 - 24, ry + 30, ry + 40
            draw.rounded_rectangle([bx0, by0, bx1, by1], radius=5, fill=(28, 37, 54, 255))
            fw = max(8, int((bx1 - bx0) * min(100.0, max(0.0, swr)) / 100.0))
            draw.rounded_rectangle([bx0, by0, bx0 + fw, by1], radius=5, fill=EMERALD if swr >= 55 else GOLD)

    # Right panel: Top Symbols + Breakdown
    rx0, ry0, rx1, ry1 = 762, 302, W - 54, 650
    draw.rounded_rectangle([rx0, ry0, rx1, ry1], radius=20, fill=CARD_BG, outline=LINE_SUB, width=1)
    draw.text((rx0 + 24, ry0 + 20), "TOP PERFORMING ASSETS", font=f_sec, fill=CYAN)

    syms = list(digest.get("top_symbols") or [])[:4]
    if not syms:
        draw.text((rx0 + 24, ry0 + 90), "Awaiting active symbol data.", font=f_row, fill=TEXT_MUTED)
    else:
        for idx, sym_row in enumerate(syms):
            sy = ry0 + 68 + idx * 64
            sname = str(sym_row.get("symbol") or "XAUUSD").upper()
            sp = float(sym_row.get("pips") or 0.0)
            scnt = int(sym_row.get("total") or 0)
            draw.rounded_rectangle([rx0 + 20, sy, rx1 - 20, sy + 52], radius=12, fill=CARD_ALT, outline=LINE_SUB, width=1)
            draw.text((rx0 + 36, sy + 14), f"{sname}", font=f_row, fill=TEXT_MAIN)
            draw.text((rx0 + 195, sy + 15), f"{'+' if sp >= 0 else ''}{sp:.1f}p  ({scnt})", font=f_mono, fill=EMERALD if sp >= 0 else CRIMSON)

    # Bottom footer
    draw.rounded_rectangle([54, 672, W - 54, H - 36], radius=16, fill=(12, 17, 28, 240), outline=(212, 175, 55, 110), width=1)
    wins = int(digest.get("wins") or 0)
    losses = int(digest.get("losses") or 0)
    open_c = int(digest.get("open") or 0)
    draw.text((78, 690), f"WINS: {wins}   •   LOSSES: {losses}   •   RUNNING: {open_c}   •   100% VERIFIED DATABASE AUDIT", font=f_mono, fill=GOLD_BRIGHT)

    buf = io.BytesIO()
    img.convert("RGB").save(buf, format="PNG", optimize=True)
    return buf.getvalue()
