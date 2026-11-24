"""v6.20.4 Regression Tests — PostgreSQL-Safe P/L Accounting + Luxury Date-Grouped
Signals Archive (Today / Yesterday / By Date) for BOTH Admin and User."""
import asyncio
import os
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock

from fysb import config, db
from fysb.handlers import dispatcher
from fysb.services import reports, signal_archive, users

ADMIN = 999001
USER_A = 77001
USER_B = 77002


def _make_query(data: str, uid: int):
    sent = []

    async def _send(chat_id=None, text="", **kw):
        m = SimpleNamespace(message_id=len(sent) + 100, chat_id=chat_id or uid, text=text, reply_markup=kw.get("reply_markup"))
        sent.append(m)
        return m

    async def _reply(text="", **kw):
        return await _send(uid, text, **kw)

    bot = SimpleNamespace(
        send_message=AsyncMock(side_effect=_send),
        edit_message_text=AsyncMock(side_effect=_send),
        delete_message=AsyncMock(),
    )
    msg = SimpleNamespace(
        chat_id=uid,
        message_id=10,
        edit_text=AsyncMock(side_effect=_reply),
        reply_text=AsyncMock(side_effect=_reply),
        delete=AsyncMock(),
    )
    q = SimpleNamespace(
        data=data,
        from_user=SimpleNamespace(id=uid, username=f"u{uid}", first_name=f"User{uid}", full_name=f"User {uid}"),
        message=msg,
        answer=AsyncMock(),
    )
    ctx = SimpleNamespace(bot=bot, user_data={}, _sent=sent)
    return q, ctx


def _fire(data: str, uid: int):
    q, ctx = _make_query(data, uid)
    upd = SimpleNamespace(callback_query=q, effective_user=q.from_user)
    asyncio.run(dispatcher.buttons(upd, ctx))
    last = ctx._sent[-1] if ctx._sent else SimpleNamespace(text="", reply_markup=None)
    datas = [b.callback_data for row in (last.reply_markup.inline_keyboard if last.reply_markup else []) for b in row]
    return last.text or "", datas


_SIG_SEQ = 0


def _insert_sig(day_iso: str, symbol="XAUUSD", status="TP1_HIT", pips=120.0,
                closed=True, source="AUTO", created_by=0, tf="M15", strat="ICT"):
    global _SIG_SEQ
    _SIG_SEQ += 1
    ts = f"{day_iso}T11:30:{_SIG_SEQ % 60:02d}+00:00"
    closed_at = f"{day_iso}T13:00:{_SIG_SEQ % 60:02d}+00:00" if closed else ""
    entry = 3600.0 + _SIG_SEQ * 0.5
    with db.conn() as c:
        return c.execute(
            """INSERT INTO signals(symbol,timeframe,direction,entry,stop,tp1,tp2,tp3,
               targets,rr,score,strategy,reason,status,source,created_by,hit_tp,pips_result,
               created_at,filled_at,closed_at)
               VALUES(?,?, 'BUY', ?, 3585.0, 3630.0, 3645.0, 3660.0,
               '3630,3645,3660', 2.0, 90, ?, 'test', ?, ?, ?, ?, ?, ?, ?, ?)""",
            (symbol, tf, entry, strat, status, source, created_by,
             1 if status in ("TP1_HIT", "TP2_HIT", "TP3_HIT") else 0,
             pips, ts, ts, closed_at),
        ).lastrowid


class TestV6204PgPlAndLuxuryDateArchive(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self._old_db = config.DB_FILE
        self._old_admin = config.ADMIN_ID
        config.DB_FILE = os.path.join(self._tmp.name, "v6204.db")
        config.ADMIN_ID = ADMIN
        db.init_db()
        users.ensure_user(SimpleNamespace(id=USER_A, username="usera", first_name="User A", full_name="User A"))
        users.ensure_user(SimpleNamespace(id=USER_B, username="userb", first_name="User B", full_name="User B"))
        self.d_today = signal_archive.today_iso()
        self.d_yest = signal_archive.yesterday_iso()
        self.d_old = (datetime.now(timezone.utc) - timedelta(days=4)).strftime("%Y-%m-%d")

    def tearDown(self):
        config.DB_FILE = self._old_db
        config.ADMIN_ID = self._old_admin
        self._tmp.cleanup()

    def test_pl_accounting_and_reports_work_across_periods(self):
        sid_xau_w = _insert_sig(self.d_today, "XAUUSD", "TP3_HIT", 300.0, closed=True)
        _insert_sig(self.d_today, "EURUSD", "SL_HIT", -40.0, closed=True)
        _insert_sig(self.d_today, "BTCUSD", "SL_HIT", -236.1, closed=True)
        _insert_sig(self.d_yest, "XAUUSD", "TP1_HIT", 180.0, closed=True)

        # Full P/L accounting grouped by market sector (Metals / Forex / Crypto)
        text_all, datas_all = _fire("a:sigpl", ADMIN)
        self.assertIn("محاسبة الربح والخسارة", text_all)
        self.assertIn("الملخص التنفيذي العام", text_all)
        self.assertIn("سوق المعادن والسلع", text_all)
        self.assertIn("سوق الفوركس والعملات", text_all)
        self.assertIn("سوق العملات الرقمية", text_all)
        self.assertIn("Profit Factor", text_all)
        self.assertIn("XAUUSD", text_all)
        self.assertIn("EURUSD", text_all)
        self.assertIn("BTCUSD", text_all)
        self.assertIn("+480.0 pips", text_all)
        self.assertIn("a:sigpl:today", datas_all)
        self.assertIn("a:sigpl:yesterday", datas_all)
        self.assertIn("a:sigplcat:metal:all", datas_all)
        self.assertIn("a:sigplcat:forex:all", datas_all)
        self.assertIn("a:sigplcat:crypto:all", datas_all)
        self.assertIn("a:sigplsym:XAUUSD:all", datas_all)

        # Sector drill-down: Metals ONLY (must NOT include EURUSD or BTCUSD)
        sec_txt, sec_datas = _fire("a:sigplcat:metal:all", ADMIN)
        self.assertIn("محاسبة سوق المعادن والسلع", sec_txt)
        self.assertIn("XAUUSD", sec_txt)
        self.assertNotIn("EURUSD", sec_txt)
        self.assertNotIn("BTCUSD", sec_txt)
        self.assertIn("a:sigplsym:XAUUSD:all", sec_datas)

        # Instrument deep ledger: XAUUSD with lot-scale preview & clickable trades
        sym_txt, sym_datas = _fire("a:sigplsym:XAUUSD:all", ADMIN)
        self.assertIn("كشف المحاسبة التفصيلي — XAUUSD", sym_txt)
        self.assertIn("مقياس اللوت", sym_txt)
        self.assertIn("أفضل صفقة", sym_txt)
        self.assertIn(f"a:signalview:{sid_xau_w}", sym_datas)

        # Today-only P/L accounting
        text_today, _ = _fire("a:sigpl:today", ADMIN)
        self.assertIn("+300.0 pips", text_today)
        self.assertIn("-40.0 pips", text_today)

        # Yesterday-only P/L accounting
        text_yest, _ = _fire("a:sigpl:yesterday", ADMIN)
        self.assertIn("XAUUSD", text_yest)
        self.assertIn("+180.0 pips", text_yest)
        self.assertNotIn("EURUSD", text_yest)

        # Reports module (signals_report & payments_report) also PostgreSQL-safe
        sr = reports.signals_report()
        self.assertIn("تقرير الإشارات", sr)
        pr = reports.payments_report()
        self.assertIn("تقرير المدفوعات", pr)

    def test_admin_luxury_date_separation_today_yesterday_and_by_date(self):
        sid_t = _insert_sig(self.d_today, "XAUUSD", "RUNNING", 0.0, closed=False)
        sid_y = _insert_sig(self.d_yest, "GBPUSD", "TP1_HIT", 110.0, closed=True)
        sid_o = _insert_sig(self.d_old, "BTCUSD", "TP3_HIT", 250.0, closed=True)

        # Default a:signals -> Today
        t_txt, t_datas = _fire("a:signals", ADMIN)
        self.assertIn("إشارات اليوم", t_txt)
        self.assertIn(f"a:signalview:{sid_t}", t_datas)
        self.assertNotIn(f"a:signalview:{sid_y}", t_datas)
        self.assertIn("a:sigday:yesterday:all:0", t_datas)
        self.assertIn("a:sigdates:0", t_datas)

        # Yesterday tab
        y_txt, y_datas = _fire("a:sigday:yesterday:all:0", ADMIN)
        self.assertIn("إشارات أمس", y_txt)
        self.assertIn(f"a:signalview:{sid_y}", y_datas)
        self.assertNotIn(f"a:signalview:{sid_t}", y_datas)

        # Calendar journal (By Date)
        cal_txt, cal_datas = _fire("a:sigdates:0", ADMIN)
        self.assertIn("أرشيف الإشارات حسب التاريخ", cal_txt)
        self.assertIn(f"a:sigday:{self.d_today}:all:0", cal_datas)
        self.assertIn(f"a:sigday:{self.d_yest}:all:0", cal_datas)
        self.assertIn(f"a:sigday:{self.d_old}:all:0", cal_datas)

        # Specific past date
        o_txt, o_datas = _fire(f"a:sigday:{self.d_old}:all:0", ADMIN)
        self.assertIn(self.d_old, o_txt)
        self.assertIn(f"a:signalview:{sid_o}", o_datas)
        self.assertNotIn(f"a:signalview:{sid_t}", o_datas)

    def test_user_previous_signals_never_disappear_and_date_separated(self):
        # Closed winning signal from today + closed signal from yesterday + closed from 4 days ago
        sid_today_closed = _insert_sig(self.d_today, "XAUUSD", "TP1_HIT", 297.2, closed=True, strat="CLASSIC")
        sid_yest_closed = _insert_sig(self.d_yest, "XAUUSD", "TP3_HIT", 410.0, closed=True, strat="CLASSIC")
        sid_old_delivered = _insert_sig(self.d_old, "EURUSD", "TP2_HIT", 155.0, closed=True, tf="H1", strat="SMC")
        # Private SIGNAL_NOW owned by USER_B must NOT leak to USER_A
        sid_b_priv = _insert_sig(self.d_today, "XAUUSD", "RUNNING", 0.0, closed=False, source="SIGNAL_NOW", created_by=USER_B, strat="CLASSIC")

        # Mark old signal as delivered to USER_A (even though plan default is XAUUSD/M15/ICT)
        with db.conn() as c:
            c.execute(
                "INSERT INTO signal_deliveries(user_id,signal_id,delivered_at) VALUES(?,?,?)",
                (USER_A, sid_old_delivered, f"{self.d_old}T11:31:00+00:00"),
            )

        # 1) User opens u:signals -> Today (sees closed today signal, NOT USER_B's private signal!)
        t_txt, t_datas = _fire("u:signals", USER_A)
        self.assertIn("إشارات اليوم", t_txt)
        self.assertIn(f"u:signalview:{sid_today_closed}", t_datas)
        self.assertNotIn(f"u:signalview:{sid_b_priv}", t_datas)
        self.assertIn("u:sigday:yesterday:0", t_datas)
        self.assertIn("u:sigdates:0", t_datas)

        # 2) User switches to Yesterday -> sees yesterday's closed signal!
        y_txt, y_datas = _fire("u:sigday:yesterday:0", USER_A)
        self.assertIn("إشارات أمس", y_txt)
        self.assertIn(f"u:signalview:{sid_yest_closed}", y_datas)
        self.assertNotIn(f"u:signalview:{sid_today_closed}", y_datas)

        # 3) User opens Calendar Journal -> sees ALL 3 dates including old delivered signal!
        cal_txt, cal_datas = _fire("u:sigdates:0", USER_A)
        self.assertIn("أرشيف إشاراتك حسب التاريخ", cal_txt)
        self.assertIn(f"u:sigday:{self.d_today}:0", cal_datas)
        self.assertIn(f"u:sigday:{self.d_yest}:0", cal_datas)
        self.assertIn(f"u:sigday:{self.d_old}:0", cal_datas)

        # 4) User taps the 4-days-ago date -> old delivered signal is STILL there!
        o_txt, o_datas = _fire(f"u:sigday:{self.d_old}:0", USER_A)
        self.assertIn(self.d_old, o_txt)
        self.assertIn(f"u:signalview:{sid_old_delivered}", o_datas)

        # 5) Viewing the already-delivered old closed signal succeeds without quota error
        v_txt, v_datas = _fire(f"u:signalview:{sid_old_delivered}", USER_A)
        self.assertIn("مغلقة على ربح", v_txt)
        self.assertIn("u:sigdates:0", v_datas)


if __name__ == "__main__":
    unittest.main()
