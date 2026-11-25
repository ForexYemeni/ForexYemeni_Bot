#!/usr/bin/env python3
"""Generate static GitHub Pages Mini App & Admin Panel HTML files into `docs/`.

GitHub Pages serves `https://forexyemeni.github.io/ForexYemeni_Bot/` (User/Partner Mini App)
and `https://forexyemeni.github.io/ForexYemeni_Bot/admin/` (Admin Control Panel) for free
forever, with global Fastly CDN, zero ISP DNS blocks, and zero browser warning screens.
Every API request dynamically targets `?api=<live_https_backend>` with
`ngrok-skip-browser-warning: 1` and Telegram HMAC-SHA256 `X-Telegram-Init-Data`.
"""
from __future__ import annotations

import pathlib
import sys

ROOT = pathlib.Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from fysb.webapp import templates  # noqa: E402


def build_pages(out_dir: pathlib.Path | None = None) -> tuple[pathlib.Path, pathlib.Path]:
    docs = out_dir or (ROOT / "docs")
    admin_dir = docs / "admin"
    admin_dir.mkdir(parents=True, exist_ok=True)
    (docs / ".nojekyll").write_text("", encoding="utf-8")

    user_file = docs / "index.html"
    admin_file = admin_dir / "index.html"
    user_file.write_text(templates.render_user_miniapp_html(), encoding="utf-8")
    admin_file.write_text(templates.render_admin_panel_html(), encoding="utf-8")
    return user_file, admin_file


if __name__ == "__main__":
    u, a = build_pages()
    print(f"Built GitHub Pages Mini App: {u} ({u.stat().st_size} B), {a} ({a.stat().st_size} B)")
