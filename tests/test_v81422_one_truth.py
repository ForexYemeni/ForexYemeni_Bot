"""v8.14.22 — «الحقيقة الواحدة» tests — the owner's decree:

The owner compared THREE surfaces for the SAME day and got THREE different
truths: the signals center (17 · +596.2 mixed pips), the interactive report
(6 · 3W-3L · +7.79$), and the executive PNG (4 · 1 closed · 0% WR · -2.69$).
That contradiction is executed:

① The digest engine (the PNG's data) NEVER classifies LOCKED_50 as open —
   accounting.is_closed / theme.is_signal_closed recognize it as settled and
   the ledger rules it a win. Every LOCKED_50 is a win in EVERY surface.
② The interactive report's closed-set mirrors the ledger EXACTLY
   (TP1_BE/TP2_BE/WIN/LOSS included).
③ The owner's slice PNG counts EXACTLY the report's population — every
   source included (include_private=True), zero silent exclusions. The
   weekly channel digest keeps its official-only policy (by design).
④ The signals center summary is LEDGER truth: a touched stop is a WIN at
   the threshold (rescued, disclosed), LOCKED_50 a win — and the net is
   honest USD at 0.01 lot per trade. The mixed '+596.2 pips' line is dead:
   pips are NEVER summed across assets on any surface.
⑤ The card is honest: footer "SIGNALS: n (CLOSED m)", period "LAST 24 HOURS"
   for days=1 (never "LAST 1 DAYS"), no emoji tofu in the period pill.
"""
from __future__ import annotations

import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

sys.path.insert(0, ".")

import tests.helpers  # noqa: F401
from fysb import db, instruments
from fysb.engines.accounting import is_closed
from fysb.engines.strategies import REGISTRY  # noqa: F401
from fysb.services import digest, reports, signal_archive
from fysb.theme import is_signal_closed

ADMIN = 9001

_SEQ = iter(range(1, 900))


@pytest.fixture(autouse=True)
def _clean():
    db.init_db()
    with db.conn() as c:
        c.execute("DELETE FROM signals")
        c.execute("DELETE FROM broadcast_channels")
    yield
    with db.conn() as c:
        c.execute("DELETE FROM signals")
        c.execute("DELETE FROM broadcast_channels")


def _iso(age_days: float = 0.0) -> str:
    return (datetime.now(timezone.utc) - timedelta(days=age_days)).isoformat()


def _ins(sym, strat, status, pips, tf="M5", age_hours=0.0, touched50=0, source="AUTO",
         score=93, stop=25.0, entry=3500.0):
    closed_at = "" if status in ("ACTIVE", "RUNNING", "PENDING") else _iso(age_hours / 24.0)
    with db.conn() as c:
        c.execute("INSERT INTO signals(symbol,timeframe,status,direction,entry,stop,tp1,tp2,tp3,"
                  "rr,score,strategy,pips_result,profit_50_hit,closed_at,created_at,source)"
                  " VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                  (sym, tf, status, "BUY", entry, entry - stop, entry + 50, entry + 80, entry + 120,
                   2.0, score, strat, pips, touched50, closed_at, _iso(age_hours / 24.0), source))


def _owner_slice_rows():
    """The owner's exact scenario: BTCUSD last 24h — 3 honest SLs + 3 LOCKED_50,
    two of them born from SIGNAL_NOW (the old digest silently dropped 2 of 6)."""
    _ins("BTCUSD", "ICHIMOKU", "LOCKED_50", 300.0, age_hours=5.0, touched50=1)
    _ins("BTCUSD", "SMC", "LOCKED_50", 150.0, age_hours=7.0, touched50=1, source="SIGNAL_NOW")
    _ins("BTCUSD", "ICT", "LOCKED_50", 79.3, age_hours=8.0, touched50=1)
    _ins("BTCUSD", "ICHIMOKU", "SL_HIT", -225.0, age_hours=10.0)
    _ins("BTCUSD", "SMC", "SL_HIT", -269.0, age_hours=12.0, source="SIGNAL_NOW")
    _ins("BTCUSD", "ICT", "SL_HIT", -135.0, age_hours=14.0)


# ---------------------------------------------------------------------------
# ① LOCKED_50 is settled everywhere — never "open" in any aggregate
# ---------------------------------------------------------------------------
def test_locked50_is_settled_in_accounting_and_theme():
    row = {"status": "LOCKED_50", "closed_at": ""}
    assert is_closed(row) is True
    assert is_signal_closed(row) is True


def test_digest_never_counts_locked50_as_open():
    _ins("BTCUSD", "ICHIMOKU", "LOCKED_50", 300.0, age_hours=5.0)
    _ins("BTCUSD", "SMC", "SL_HIT", -269.0, age_hours=6.0)
    d = digest.build_digest_data(days=1, market="BTCUSD", include_private=True)
    assert d["total"] == 2 and d["open"] == 0          # never "OPEN:3" again
    assert d["wins"] == 1 and d["losses"] == 1


# ---------------------------------------------------------------------------
# ② the report's closed-set == the ledger's closed-set
# ---------------------------------------------------------------------------
def test_report_closed_set_mirrors_the_ledger_including_be_rows():
    _ins("XAUUSD", "TREND", "TP1_BE", 40.0, age_hours=5.0, tf="M15")
    _ins("XAUUSD", "TREND", "WIN", 55.0, age_hours=6.0, tf="M15")
    txt = reports.filtered_signals_report("XAUUSD", None, None, None)
    assert "رابحة <b>2</b>" in txt  # TP1_BE + WIN count exactly like the digest does


# ---------------------------------------------------------------------------
# ③ the owner's PNG population == the report population (the headline fix)
# ---------------------------------------------------------------------------
def test_owner_slice_png_counts_exactly_what_the_report_counts():
    _owner_slice_rows()
    txt = reports.filtered_signals_report("BTCUSD", None, None, 1)
    assert "رابحة <b>3</b>" in txt and "خاسرة <b>3</b>" in txt  # 3 LOCKED_50 + 3 honest SLs
    d = digest.build_digest_data(days=1, market="BTCUSD", include_private=True)
    assert d["total"] == 6 and d["decided"] == 6        # NOT 4/1 — the silent drop is dead
    assert d["wins"] == 3 and d["losses"] == 3 and d["open"] == 0
    exp = (instruments.usd_move("BTCUSD", 300.0 + 150.0 + 79.3, 0.01)
           + instruments.usd_move("BTCUSD", -225.0 - 269.0 - 135.0, 0.01))
    assert float(d["net_usd_001"]) == pytest.approx(exp, abs=0.02)


def test_weekly_channel_digest_keeps_its_official_only_policy():
    _owner_slice_rows()
    d_official = digest.build_digest_data(days=1, market="BTCUSD")  # default: official-only
    assert d_official["total"] == 4  # the 2 SIGNAL_NOW rows are excluded BY DESIGN there
    assert d_official["wins"] == 2 and d_official["losses"] == 2 and d_official["open"] == 0
    # and even there, the LOCKED_50 rows are WINS — never open ghosts


def test_interactive_png_builder_uses_the_report_population():
    from fysb.handlers.admin_reports_ui import _build_slice_png
    _owner_slice_rows()
    f = {"market": "BTCUSD", "strategy": None, "timeframe": None, "days": 1}
    png, d = _build_slice_png(f)
    assert d["total"] == 6 and d["wins"] == 3 and d["losses"] == 3
    assert png and png[:8] == b"\x89PNG\r\n\x1a\n"


# ---------------------------------------------------------------------------
# ④ the signals center: ledger truth + honest USD net (mixed pips executed)
# ---------------------------------------------------------------------------
def test_center_summary_is_ledger_truth_with_usd_net():
    _ins("XAUUSD", "TREND", "LOCKED_50", 50.0, age_hours=4.0, touched50=1, tf="M15", stop=50.0)
    _ins("XAUUSD", "TREND", "SL_HIT", -50.0, age_hours=5.0, touched50=1, tf="M15", stop=50.0)  # rescued 🏅
    _ins("XAUUSD", "TREND", "SL_HIT", -60.0, age_hours=6.0, tf="M15", stop=60.0)               # honest loss
    _ins("BTCUSD", "TREND", "RUNNING", 0.0, age_hours=2.0, tf="M5")
    rows = [dict(r) for r in db.conn().execute("SELECT * FROM signals").fetchall()]
    s = signal_archive.summarize_rows(rows)
    assert s["total"] == 4 and s["open"] == 1
    assert s["wins"] == 2 and s["losses"] == 1
    assert s["rescued"] == 1                     # the decree is DISCLOSED on the center
    exp = (instruments.usd_move("XAUUSD", 50.0, 0.01) + instruments.usd_move("XAUUSD", 50.0, 0.01)
           + instruments.usd_move("XAUUSD", -60.0, 0.01))
    assert float(s["net_usd"]) == pytest.approx(exp, abs=0.02)


def test_center_header_renders_honest_usd_and_decree_line_never_mixed_pips():
    summary = {"total": 3, "open": 0, "wins": 2, "losses": 1, "rescued": 1,
               "net_pips": 40.0, "net_usd": 4.0}
    head = signal_archive.luxury_day_header("مركز إدارة الإشارات الملكي", "اليوم", summary, 1, 1)
    assert "الصافي الموثوق (دفتر الحقيقة)" in head and "+4.00$" in head
    assert "0.01 لوت لكل صفقة" in head
    assert "قانون الخمسين نقطة" in head
    assert "صافي النقاط" not in head and "pips" not in head  # the mixed unit is EXECUTED


def test_open_tp1_runner_is_open_not_a_decided_row():
    _ins("XAUUSD", "TREND", "TP1_HIT", 40.0, age_hours=3.0, tf="M15", stop=50.0)
    with db.conn() as c:  # the runner is still live: no closed_at stamp yet
        c.execute("UPDATE signals SET closed_at=''")
    rows = [dict(r) for r in db.conn().execute("SELECT * FROM signals").fetchall()]
    s = signal_archive.summarize_rows(rows)
    assert s["open"] == 1 and s["wins"] == 0 and s["losses"] == 0  # still running to TP2


# ---------------------------------------------------------------------------
# ⑤ the card's honest labels
# ---------------------------------------------------------------------------
def test_card_labels_are_honest_source_level():
    src = Path("fysb/ui/share_cards.py").read_text(encoding="utf-8")
    assert "SIGNALS: {total}" in src            # never "OFFICIAL:" for the owner's slice
    assert '"LAST 24 HOURS"' in src             # never "LAST 1 DAYS"
    assert 'f"🏆  {period_en}"' not in src      # no tofu emoji in the period pill


def test_card_renders_with_24h_period_and_slice():
    from fysb.ui import share_cards
    _owner_slice_rows()
    d = digest.build_digest_data(days=1, market="BTCUSD", include_private=True)
    d["slice_ar"] = "BTCUSD"
    png = share_cards.render_performance_digest_card(d)
    assert png and png[:8] == b"\x89PNG\r\n\x1a\n"
