"""v8.14.7 tests — «بطاقة الحقيقة الكاملة» (the royal full-truth card).

The owner's decree, extended to the CARD layer: every line is built from the
ONE actual reason the engine returned. The static possibilities template
(«السوق مغلق أو يمرّ بساعة سيولة أو لم تتوفر شموع…») is executed — a closed
market says CLOSED 100% with the REAL reopen anchor, the Friday pre-close
provider-death window names BOTH truths, and data protection never claims a
closure that did not happen. Plus: ONE clock — the degrade guard now freezes
exactly when the royal DST calendar says closed.
"""
from __future__ import annotations

import sys
from datetime import datetime, timezone
from unittest.mock import patch

import pytest

sys.path.insert(0, ".")

import tests.helpers  # noqa: F401  (isolated temp DB + env)
from fysb import db, theme
from fysb.engines import market_data as md
from fysb.engines.signal_engine import generate_signal
from fysb.ui.cards import diagnose_no_signal, render_no_signal_card


@pytest.fixture(autouse=True)
def _init_db():
    db.init_db()
    yield


OLD_TEMPLATE = "السوق مغلق أو يمرّ بساعة سيولة"

ROYAL_CLOSURE = ("🔒 السوق مغلق فعلاً 100% وفق تقويم الجلسات — أُغلق الجمعة 17:00 بتوقيت "
                 "نيويورك ويعود الأحد 18:00 بتوقيت نيويورك (23:00 UTC) — لا إشارات تُصنَّع "
                 "على سوق مغلق؛ السلامة أولاً دائماً")

STALE_CRYPTO = ("📡 مصدر البيانات يعيد لقطة أقدم من السوق الحي — رفض حماية صادق: "
                "لا نولّد إشارة على أسعار غير حية؛ أعد المحاولة بعد لحظات")

INSUFFICIENT = ("📉 تاريخ الشموع المتاح أقصر من الحد الأدنى للتحليل المؤسسي — "
                "لا إشارة من عينة ناقصة")


class _FrozenDT:
    """Patches market_data.datetime so now() returns a FIXED instant while the
    class itself stays real (constructor + classmethods keep working)."""

    def __init__(self, fixed: datetime):
        self._fixed = fixed
        self._real = datetime

    def __call__(self, *args, **kwargs):
        return self._real(*args, **kwargs)

    def now(self, tz=None):
        return self._fixed if tz is None else self._fixed.astimezone(tz)

    def __getattr__(self, item):
        return getattr(self._real, item)


# ---------------------------------------------------------------------------
# ① the royal 100% closure truth with the REAL reopen anchor (DST-correct)
# ---------------------------------------------------------------------------

def test_market_closed_reason_winter_names_100_percent_and_reopen():
    fri = datetime(2026, 11, 27, 23, 0, tzinfo=timezone.utc)  # Friday, winter
    with patch.object(md, "datetime", _FrozenDT(fri)), \
         patch.object(md, "_us_dst_active", return_value=False):
        r = md.market_closed_reason()
    assert "مغلق فعلاً 100%" in r
    assert "الجمعة 17:00" in r and "الأحد 18:00" in r
    assert "23:00 UTC" in r          # winter reopen: NY 18:00 = 23:00 UTC
    assert "تُصنَّع" in r             # nothing manufactured on a closed market


def test_market_closed_reason_summer_reopen_is_2200_utc():
    fri = datetime(2026, 7, 24, 21, 30, tzinfo=timezone.utc)  # Friday, summer
    with patch.object(md, "datetime", _FrozenDT(fri)), \
         patch.object(md, "_us_dst_active", return_value=True):
        r = md.market_closed_reason()
    assert "مغلق فعلاً 100%" in r and "22:00 UTC" in r


def test_market_closed_reason_sunday_says_reopens_today():
    sun = datetime(2026, 11, 29, 20, 0, tzinfo=timezone.utc)  # Sunday pre-open
    with patch.object(md, "datetime", _FrozenDT(sun)), \
         patch.object(md, "_us_dst_active", return_value=False):
        r = md.market_closed_reason()
    assert "يعود اليوم الأحد 18:00" in r and "مغلق فعلاً 100%" in r


# ---------------------------------------------------------------------------
# ② the closure card — never the possibilities template, never «retry soon»
# ---------------------------------------------------------------------------

def test_closure_card_is_royal_and_template_free():
    html = render_no_signal_card(symbol="XAUUSD", timeframe="M15",
                                 strategy_label="جميع المنهجيات", style_label="يومي",
                                 status="REJECTED", reason=ROYAL_CLOSURE)
    assert "السوق مغلق فعلاً 100% — وفق تقويم الجلسات الملكي" in html
    assert "مغلق فعلاً 100% وفق تقويم الجلسات" in html   # the diagnosis line, verbatim
    assert OLD_TEMPLATE not in html
    assert "أو يمرّ" not in html
    assert "أعد المحاولة بعد قليل" not in html


def test_legacy_market_closed_reason_still_gets_the_closure_card():
    html = render_no_signal_card(status="REJECTED",
                                 reason=theme.ar_reason("market closed"))
    assert "السوق مغلق فعلاً 100% — وفق تقويم الجلسات الملكي" in html


def test_settlement_pause_card_names_the_pause_only():
    ar = theme.ar_reason("settlement pause 22:00-23:00 UTC; no signal manufactured")
    icon, title, exp, rec = diagnose_no_signal("REJECTED", ar)
    assert icon == "🌙" and "استراحة التسوية" in title
    assert OLD_TEMPLATE not in exp and "السوق مغلق" not in title


# ---------------------------------------------------------------------------
# ③ data protection never claims a closure that did not happen
# ---------------------------------------------------------------------------

def test_stale_crypto_card_never_claims_closure():
    icon, title, exp, _ = diagnose_no_signal("REJECTED", STALE_CRYPTO)
    assert icon == "🛡" and "حماية البيانات" in title
    assert "مغلق" not in title and "مغلق" not in exp      # market is OPEN — data lied
    html = render_no_signal_card(status="REJECTED", reason=STALE_CRYPTO)
    assert "أسعار غير حية" in html and OLD_TEMPLATE not in html


def test_insufficient_data_is_not_mislabeled_as_quality_shield():
    # the word «حد» inside the honest message must not trip the quality shield
    icon, title, _, _ = diagnose_no_signal("REJECTED", INSUFFICIENT)
    assert icon == "🛡" and "حماية البيانات" in title


def test_gold_chain_death_names_its_layer_and_stays_data_card():
    with patch.object(md, "_pre_close_window", return_value=False):
        msg = md._gold_chain_diagnosis("XAUUSD")
    assert "تعذر جلب بيانات الذهب" in msg and "السبوت اللحظي" in msg
    icon, title, _, _ = diagnose_no_signal("REJECTED", msg)
    assert icon == "🛡" and "حماية البيانات" in title


# ---------------------------------------------------------------------------
# ④ the Friday pre-close provider-death window — BOTH truths named
# ---------------------------------------------------------------------------

def test_pre_close_chain_message_names_both_truths():
    with patch.object(md, "_pre_close_window", return_value=True):
        msg = md._gold_chain_diagnosis("XAUUSD")
    assert "التغذية توقفت" in msg
    assert "يقفل رسمياً بعد دقائق" in msg
    assert "الأحد 18:00 بتوقيت نيويورك" in msg
    assert "17:00 بتوقيت نيويورك" in msg


def test_outside_pre_close_window_the_layer_diagnosis_remains():
    with patch.object(md, "_pre_close_window", return_value=False):
        msg = md._gold_chain_diagnosis("XAUUSD")
    assert "التغذية توقفت" not in msg
    assert "السبوت اللحظي" in msg and "تعذر جلب بيانات الذهب" in msg


def test_pre_close_card_names_both_truths_and_no_false_retry():
    ar = md._gold_chain_diagnosis("XAUUSD")  # patched window inside the test
    with patch.object(md, "_pre_close_window", return_value=True):
        ar = md._gold_chain_diagnosis("XAUUSD")
    icon, title, exp, rec = diagnose_no_signal("REJECTED", ar)
    assert icon == "⏳" and "يقفل رسمياً" in title
    assert "لا فائدة من إعادة المحاولة الآن" in rec


def test_pre_close_window_requires_open_friday_within_last_hour():
    def at(y, m, d, h, minute=0):
        dt = datetime(y, m, d, h, minute, tzinfo=timezone.utc)
        with patch.object(md, "datetime", _FrozenDT(dt)), \
             patch.object(md, "market_state", return_value=True), \
             patch.object(md, "_us_dst_active", return_value=False):
            return md._pre_close_window()

    assert at(2026, 11, 27, 21, 30) is True    # Friday, 30 min before 22:00 close
    assert at(2026, 11, 27, 20, 0) is False    # Friday, 2h before close
    assert at(2026, 11, 26, 21, 30) is False   # Thursday — never
    with patch.object(md, "datetime",
                      _FrozenDT(datetime(2026, 11, 27, 21, 30, tzinfo=timezone.utc))), \
         patch.object(md, "market_state", return_value=False), \
         patch.object(md, "_us_dst_active", return_value=False):
        assert md._pre_close_window() is False  # calendar already closed


# ---------------------------------------------------------------------------
# ⑤ ONE clock — the degrade guard freezes exactly when the calendar says closed
# ---------------------------------------------------------------------------

def test_gold_market_closed_delegates_to_royal_calendar():
    with patch.object(md, "market_state", return_value=False) as ms:
        assert md._gold_market_closed() is True
        assert ms.call_args[0][0] == "metal"
    with patch.object(md, "market_state", return_value=True):
        assert md._gold_market_closed() is False
    with patch.object(md, "market_state", return_value=None):
        assert md._gold_market_closed() is False   # unknown → never freeze unfairly


# ---------------------------------------------------------------------------
# ⑥ the engine returns the royal closure reason — Arabic, no English leftovers
# ---------------------------------------------------------------------------

def test_engine_market_closed_returns_royal_reason():
    with patch.object(md, "market_state", return_value=False), \
         patch.object(md, "in_settlement_pause", return_value=False), \
         patch.object(db, "kill_switch_on", return_value=False):
        res = generate_signal("XAUUSD", "M15", force=False)
    assert res["status"] == "REJECTED"
    assert "مغلق فعلاً 100%" in res["reason"]
    assert "الأحد 18:00" in res["reason"]
    assert "market closed" not in res["reason"]
    assert "no signal manufactured" not in res["reason"]


def test_run_line_market_closed_is_royal_without_english_leak():
    line = theme.ar_run_line("MARKET_CLOSED", ROYAL_CLOSURE + "؛ لا إشارة مصنوعة")
    assert "مغلق فعلاً 100%" in line
    assert "market closed" not in line and "no signal manufactured" not in line


def test_result_map_market_closed_is_royal():
    text = theme.ar_result("MARKET_CLOSED")
    assert "🔒" in text and "مغلق فعلاً 100%" in text


# ---------------------------------------------------------------------------
# ⑦ the sweep: NO reason ever revives the possibilities template
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("reason", [
    ROYAL_CLOSURE,
    theme.ar_reason("market closed"),
    theme.ar_reason("settlement pause 22:00-23:00 UTC; no signal manufactured"),
    STALE_CRYPTO,
    theme.ar_reason("data frozen (repeats 3, age 90min)"),
    INSUFFICIENT,
    "تعذر جلب بيانات الذهب الآن — التغذية توقفت والسوق يقفل رسمياً بعد دقائق — لا إشارات حتى عودة الأحد 18:00 بتوقيت نيويورك",
    "تعذر جلب بيانات الذهب الآن — السبوت اللحظي: غير متاح الآن · العقود: آخر معايرة منذ 0 سا · المصدر المفتاحي: فشل أيضاً — أعد المحاولة بعد قليل",
    theme.ar_reason("proxy candles are indicative only, not execution evidence"),
    "🔍 فحصنا المنهجيات المفعّلة (CRT، SMC) — لم يكتمل أي نموذج مؤسسي 100% حالياً؛ فور اكتمال شروط أي منهجية تُولَّد الإشارة فوراً وبكل حقيقة",
    theme.ar_reason("no qualifying setup"),
    "حظر أخبار — خبر قوي قريب من الإصدار",
])
def test_no_reason_revives_the_or_template(reason):
    for st in ("REJECTED", "ERROR", "NO_SETUP"):
        html = render_no_signal_card(symbol="XAUUSD", timeframe="M15",
                                     strategy_label="جميع المنهجيات",
                                     style_label="يومي",
                                     status=st, reason=reason)
        assert OLD_TEMPLATE not in html
        assert "حالة الجلسة والسيولة" not in html


def test_render_contract_and_shields_survive_the_rewrite():
    # the v6.20.9 luxury contract still holds on the specific shields
    ic, title, _, _ = diagnose_no_signal("REJECTED",
                                         "معاكس لاتجاه الفريم الأعلى (HTF=DOWN)")
    assert ic == "🛡" and "الاتجاه" in title
    ic2, title2, _, _ = diagnose_no_signal("REJECTED", "سوق متذبذب عرضي (ER=0.14)")
    assert ic2 == "🌊" and "تذبذب" in title2
    ic3, _, _, _ = diagnose_no_signal("DUPLICATE", "توجد إشارة مفتوحة")
    assert ic3 == "🔁"
    ic4, _, _, _ = diagnose_no_signal("REJECTED", "جودة الإشارة أقل من الحد الأدنى 72")
    assert ic4 == "⚖️"
    ic6, title6, _, _ = diagnose_no_signal("NO_SETUP", "")
    assert ic6 == "👑" and "صمت المحرك أمانة" in title6
