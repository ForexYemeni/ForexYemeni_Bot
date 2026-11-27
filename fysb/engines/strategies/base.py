"""Strategy base — every strategy returns a measurable, auditable result.

Contract:
  evaluate(ctx) -> StrategyResult | None
  * ctx carries entry-TF candles, bias-TF candles, instrument spec, market row.
  * score = normalized checklist merit (42..95), NOT a win probability.
  * reasons list every contributing rule (documented in docs/STRATEGIES.md).
  * A result is None when the strategy's entry conditions are simply not met —
    that is not an error, the pipeline records it as NO_SETUP.
"""
from dataclasses import dataclass, field
from datetime import datetime, timezone
from functools import wraps
import logging
import math
import threading
from numbers import Real

log = logging.getLogger(__name__)


@dataclass
class Ctx:
    symbol: str
    timeframe: str            # entry timeframe code (M15, H1...)
    candles: list[dict]       # entry timeframe, oldest -> newest
    bias_candles: list[dict]  # higher timeframe for bias, may be same list
    market_row: dict = None   # market_catalog row (pip specs etc.)
    kill_zone: str = ""
    # v8.13.0 — correlated-symbol series for SMT divergence (same timeframe,
    # oldest -> newest). None = data not supplied: SMT is skipped honestly,
    # never fabricated.
    correlated_candles: list = None


# --- v8.14.6 — «السبب الاستراتيجي الحقيقي» (the strategy-truth diagnosis) ------
# When a strategy refuses to manufacture a setup, the owner and users deserve
# the REAL reason from THAT strategy: which conditions stayed incomplete and
# how close the model came — never vague filler text. Thread-local so parallel
# engine runs never mix their verdicts.
_DIAG = threading.local()

CHECK_AR = {
    "range": "نطاق مرجعي صالح", "sweep": "جرف سيولة من طرف واحد",
    "confirm_close": "إغلاق مؤكد داخل النطاق بعد الجرف", "htf_bias": "توافق الانحياز الأعلى",
    "displacement": "حركة مؤسسية عنيفة (Displacement)", "fvg_inside": "فجوة قيمة داخل النطاق",
    "fvg_zone": "فجوة قيمة اتجاهية", "fvg": "فجوة قيمة عادلة",
    "ob_confluence": "تلاصق كتلة أوامر", "ob": "كتلة أوامر اتجاهية",
    "labels": "تراص القمم والقيعان (Labels)", "bos": "كسر بنية (BOS)",
    "choch": "كسر خصر (CHOCH)", "zone": "منطقة عرض/طلب سليمة",
    "rr_ok": "نسبة العائد إلى المخاطرة المعيارية", "pullback": "عودة سعرية للمتوسط",
    "resumption": "استئناف الاتجاه", "stack": "تراص المتوسطات",
    "phase_expanding": "توسّع اتجاهي حي", "momentum": "زخم مؤكد",
    "pattern": "نموذج شمعة مؤكد", "level": "مستوى أفقي قريب",
    "side": "الجهة الصحيحة من المستوى", "slope": "ميل متوسط متوافق",
    "impulse": "انفعال اتجاهي بحد أدنى", "pocket": "الإغلاق داخل الجيب الذهبي",
    "orderly": "مسار منظم بلا تشتت", "pocket_sweep": "جرف داخل الجيب",
    "bias": "انحياز السوق المتوافق", "touch": "لمسة المستوى بعد اليوم الخصب",
    "conviction": "استعادة بقناعة اتجاهية", "window": "نافذة الجلسة الصحيحة",
    "opening_range": "نطاق افتتاح مكتمل", "in_window_gap": "فجوة تكوّنت داخل النافذة",
    "direction": "اتجاه متوافق", "reclaim": "استعادة المستوى",
    "reclaim_margin": "هامش استعادة كافٍ", "wick": "ذيل رفض واضح",
    "squeeze_state": "انضغاط تاريخي (Squeeze)", "bandwidth_extreme": "انكماش مدى عند قاع تاريخي",
    "trigger": "إطلاق اتجاهي مؤكد", "session": "جلسة صالحة للنموذج",
    "kz": "داخل منطقة قتل مؤسسية", "volatility": "تقلب مناسب",
    "key": "مفتاح 12 شمعة سليم", "trend": "اتجاه قصير حاد",
    "decisive": "كسر حاسم بإغلاق", "class_a": "انحراف فئة A واضح",
    "class_b": "انحراف فئة B", "divergence": "انحراف زخمي مؤكد",
    "volume": "حجم مؤكد", "effort_result": "توافق الجهد والنتيجة",
    "spring": "نبع مؤكد (Spring/Upthrust)", "close_inside": "إغلاق صارم داخل النطاق",
    "gate_bias": "بوابة الانحياز", "week_ref_swept": "جرف مرجع أعلى",
    "cloud": "إغلاق خارج السحابة", "tk": "تراص Tenkan/Kijun",
    "fresh_cross": "تقاطع TK طازج", "forward_cloud": "سحابة أمامية متوافقة",
    "chikou": "تأكيد Chikou", "bias_kijun": "الإغلاق خلف Kijun",
    "kijun_bounce": "ارتداد Kijun بجودة", "kumo_twist": "انتعاش سحابة طازج",
    "chikou_space": "فراغ Chikou المفتوح", "break_fresh": "كسر طازج غير متجدد",
    "body": "جسم اتجاهي كافٍ", "body_min": "جسم الشمعة ≥ الحد",
    "location": "موقع النموذج محصَّن", "rsi": "RSI في نطاق الدعم",
    "sr": "مستوى قرب حقيقي", "dual_sweep": "لا جرف مزدوج",
    "mid": "المسافة لنقطة الارتداد الوسطى", "at_mid": "الوصول لنقطة 50%",
    "ratio_ok": "نسب التوافقية داخل النوافذ", "pivot_seq": "تسلسل محاور متناوب سليم",
    "d_zone": "منطقة D في نطاق الارتداد", "spread_ok": "سبريد قابل للتنفيذ",
    "grade": "درجة جودة سيق كافية",
    "adx": "قوة الاتجاه (ADX)", "adx_strong": "اتجاه قوي بـADX",
    "alternations": "تناوب محاور سليم", "anchor_confluence": "تلاصق نقطة الارتكاز",
    "asian_range": "نطاق آسيا محدد", "at_hist_min_bandwidth": "انكماش عند قاع تاريخي",
    "band_exit_close": "إغلاق خارج النطاق", "bandwidth_expanding": "توسع مدى النطاق",
    "bandwidth_rank": "رتبة الانكماش التاريخية", "breaker_confluence": "تلاصق الكاسر",
    "breakout_retest": "كسر مع إعادة اختبار", "candle_confirm": "تأكيد شمعة اتجاهية",
    "chikou_open_space": "فراغ Chikou المفتوح", "close_back_margin": "هامش العودة داخل النطاق",
    "class": "فئة الانحراف واضحة", "correct_side": "الجهة الصحيحة",
    "d_distribution_eq": "توزيع D بالمقاييس", "dol_room": "مساحة حتى هدف السيولة",
    "effort_vs_result": "توافق الجهد والنتيجة", "ema50_slope": "ميل EMA50 متوافق",
    "ema_trend": "اتجاه المتوسطات", "eq_side": "جهة التعادل",
    "event": "حدث وايكوف مؤكد", "expansion": "توسع حركي",
    "false_break": "كسر وهمي مؤكد", "fixed_zone_touch": "لمسة المنطقة الثابتة",
    "fvg_confluence": "تلاصق الفجوة", "fvg_direction": "اتجاه الفجوة",
    "fvg_in_window": "فجوة داخل نافذة النموذج", "gap_align": "اتجاه الفجوة متوافق",
    "golden_pocket": "الجيب الذهبي 61.8-78.6%", "htf_align": "توافق الفريم الأعلى",
    "inducement_unswept": "الإغراء لم يُجرف بعد", "intact_zone_layers": "طبقات المنطقة سليمة",
    "key_break": "كسر المفتاح بإغلاق", "kijun_proximity": "قرب Kijun",
    "kumo_twist_fresh": "انتعاش سحابة طازج", "level_kind": "نوع المستوى",
    "level_retest": "إعادة اختبار المستوى", "london_manip": "تلاعب لندن",
    "long_window_55": "نافذة 55 شمعة", "lps_event_index": "حدث آخر نقطة عرض",
    "manipulation": "تلاعب واضح بجرف", "micro_trend": "اتجاه دقيق EMA9/21",
    "mss": "كسر بنية مصغّر (MSS)", "or_bias": "انحياز نطاق الافتتاح",
    "ote_depth": "عمق OTE المناسب", "ote_sweet": "داخل الجيب الأمثل",
    "pd_align": "توافق Premium/Discount", "pd_stack": "تراص Premium/Discount",
    "pool_origin": "أصل جيب السيولة", "pool_swept": "جرف جيب السيولة",
    "post_manip_poi": "نقطة اهتمام بعد التلاعب", "protocol_123": "بروتوكول 123",
    "prz_confluence": "تلاصق منطقة الانعكاس", "psych_level": "مستوى نفسي كبير",
    "pullback_volume_dry": "جفاف حجم الارتداد", "range_intact_before": "النطاق سليم قبل الحدث",
    "range_ok": "نطاق صالح", "ratio_centering": "تمركز النسب",
    "reclaim_close": "إغلاق استعادة", "role_flip_proof": "إثبات قلب الدور",
    "rsi_momentum": "زخم RSI", "rsi_regime": "نظام RSI",
    "rsi_side": "جهة RSI الصحيحة", "sb_window": "نافذة الرصاصة",
    "second_trap": "فخ ثانوي مؤكد", "session_side": "جهة الجلسة",
    "shortening_of_thrust": "تقصير الدفع", "smt": "انحراف SMT بالزوج المرتبط",
    "squeeze_quiet": "سكوت الانضغاط", "sr_confluence": "تلاصق دعم/مقاومة",
    "targets_adjusted": "أهداف مضبوطة", "trap_session": "فخ جلسة مؤكد",
    "trend_side": "جهة الاتجاه", "ttm_inside": "TTM داخل النطاق",
    "vol_ok": "تقلب معتدل", "volume_surge": "دفعة حجم مؤكدة",
    "volume_used": "حجم متاح للتحليل", "week_ref_span": "مدى المرجع الأسبوعي",
    "wick_rejection": "رفض بذيل واضح", "zigzag": "تسلسل محاور متناوب",
    "zone_mitigated": "المنطقة غير مُستهلكة", "kill_zone": "داخل منطقة قتل مؤسسية",
    "crt_mid": "الوصول لنقطة 50% من النطاق", "day_ref_swept": "جرف مرجع اليوم",
    "confirm_ladder": "سلم تأكيد لاحق",
}


def diagnosis_begin() -> None:
    """Reset the per-thread collector (called by evaluate_all each run)."""
    _DIAG.items = []


def diagnosis_record(code: str, checks: dict, earned: float, max_pts: float) -> None:
    """Record WHY this strategy refused: proximity (its own fair merit scale)
    + the REAL incomplete conditions, in royal Arabic labels. Only falsy
    checks are missing — truthful by construction, nothing inferred."""
    try:
        missing = [CHECK_AR.get(k, k) for k, v in (checks or {}).items() if not v]
        proximity = normalized_score(earned, max_pts) if max_pts else 0
        _DIAG.items = getattr(_DIAG, "items", []) or []
        _DIAG.items.append({"code": str(code).upper(), "proximity": int(proximity),
                            "missing": missing})
    except Exception:
        pass  # diagnostics never break signal generation


def diagnosis_snapshot() -> list[dict]:
    """This thread's refusal diagnostics (closest strategy first)."""
    items = list(getattr(_DIAG, "items", []) or [])
    items.sort(key=lambda d: -d["proximity"])
    return items


@dataclass
class StrategyResult:
    code: str
    direction: str            # BUY | SELL
    entry: float
    stop: float
    tp1: float
    tp2: float
    tp3: float
    rr: float
    score: int                # checklist merit, never a calibrated probability
    reasons: list[str] = field(default_factory=list)
    checks: dict = field(default_factory=dict)

    def summary(self) -> str:
        return f"{self.code}:{self.direction}:{self.score} ({'; '.join(self.reasons[:3])})"


def clamp_score(score: float) -> int:
    return int(max(0, min(95, round(score))))


def normalized_score(
    earned: float,
    max_possible: float,
    *,
    floor: float = 42.0,
    ceiling: float = 95.0,
) -> int:
    """v8.0.0 Fair Merit Score Normalizer shared across ALL 13 strategies.

    Eliminates structural base-score bias (previously Base=50 for ICT/SMC/PA vs
    Base=32 for Wyckoff/Ichimoku/Trend/SK/Classic) so every methodology competes
    strictly on the fraction of its own institutional checklist passed.
    """
    if max_possible <= 0:
        return clamp_score(floor)
    ratio = max(0.0, min(1.0, float(earned) / float(max_possible)))
    return clamp_score(floor + ratio * (ceiling - floor))


def ensure_min_tp_rr(
    entry: float,
    stop: float,
    tp1: float,
    tp2: float,
    tp3: float,
    direction: str,
    *,
    min_rr1: float = 2.0,
    min_rr2: float = 3.0,
    min_rr3: float = 4.5,
) -> tuple[float, float, float, float]:
    """v8.0.0 Royal Target Floor — guarantees TP1 >= 2.0 * risk (twice SL!),
    TP2 >= 3.0 * risk, TP3 >= 4.5 * risk, strictly monotonic in trade direction.
    Returns (tp1, tp2, tp3, rr1)."""
    e = float(entry)
    s = float(stop)
    risk = abs(e - s)
    if risk <= 1e-12:
        return float(tp1), float(tp2), float(tp3), 0.0
    d = (direction or "BUY").upper()
    if d == "BUY":
        t1 = max(float(tp1), e + risk * min_rr1)
        t2 = max(float(tp2), t1 + risk * 0.5, e + risk * min_rr2)
        t3 = max(float(tp3), t2 + risk * 0.75, e + risk * min_rr3)
    else:
        t1 = min(float(tp1), e - risk * min_rr1)
        t2 = min(float(tp2), t1 - risk * 0.5, e - risk * min_rr2)
        t3 = min(float(tp3), t2 - risk * 0.75, e - risk * min_rr3)
    rr1 = round(abs(t1 - e) / risk, 2)
    return t1, t2, t3, rr1


def safe_rr(entry: float, stop: float, tp: float, direction: str) -> float:
    risk = abs(entry - stop)
    if risk <= 0:
        return 0.0
    reward = abs(tp - entry)
    if direction == "BUY" and tp <= entry:
        return 0.0
    if direction == "SELL" and tp >= entry:
        return 0.0
    return reward / risk


def cap_stop(
    entry: float,
    stop: float,
    direction: str,
    atr_val: float,
    *,
    min_atr: float = 0.8,
    fallback_atr: float = 1.6,
    max_atr: float = 2.6,
    cap_atr: float = 2.4,
) -> float:
    """Widen tight/invalid stops, NEVER move a valid structural stop inward.

    ``max_atr``/``cap_atr`` remain accepted for call-site compatibility only.
    Oversized stops must be rejected by issuance geometry/risk policy, or sized
    down by execution policy, not cosmetically pulled inside invalidation.
    """
    e, s, a = float(entry), float(stop), float(atr_val or 0.0)
    d = (direction or "").upper()
    if d not in ("BUY", "SELL") or not all(math.isfinite(x) for x in (e, s, a)):
        raise ValueError("invalid stop inputs")
    if a <= 0:
        return s
    distance = max(float(min_atr), float(fallback_atr)) * a
    if d == "BUY":
        return e - distance if s >= e or (e - s) < min_atr * a else s
    return e + distance if s <= e or (s - e) < min_atr * a else s


def _finite_number(value) -> bool:
    try:
        return isinstance(value, Real) and not isinstance(value, bool) and math.isfinite(value)
    except (OverflowError, TypeError, ValueError):
        return False


def candle_input_error(candles: list[dict]) -> str:
    """Validate rather than sort, coerce, repair or silently drop provider bars.

    UTC-naive timestamps follow the provider contract. This checks chronology,
    NOT whether bars have closed or whether the labelled timeframe is correct;
    those are responsibilities of the data/issuance pipeline.
    """
    if not isinstance(candles, (list, tuple)):
        return "candle series must be a list or tuple"
    previous = None
    for bar in candles:
        try:
            o, h, l, c = (bar[k] for k in ("open", "high", "low", "close"))
            if not all(_finite_number(v) and v > 0 for v in (o, h, l, c)):
                return "non-finite/non-positive OHLC"
            if not l <= min(o, c) <= max(o, c) <= h:
                return "inconsistent OHLC bounds"
            v = bar.get("volume")
            if v is not None and (not _finite_number(v) or v < 0):
                return "invalid volume"
            dt = datetime.fromisoformat(bar["datetime"].replace("Z", "+00:00"))
            dt = dt.replace(tzinfo=timezone.utc) if dt.tzinfo is None else dt.astimezone(timezone.utc)
            if previous is not None and dt <= previous:
                return "duplicate/out-of-order timestamps"
            previous = dt
        except (KeyError, TypeError, ValueError, AttributeError, OverflowError):
            return "malformed candle"
    return ""


def result_error(result: StrategyResult, code: str) -> str:
    """Shared mathematical contract, independent of strategy profitability."""
    if not isinstance(result, StrategyResult) or result.code != code:
        return "invalid strategy identity"
    prices = (result.entry, result.stop, result.tp1, result.tp2, result.tp3)
    if not all(_finite_number(v) and v > 0 for v in prices):
        return "invalid/non-positive result prices"
    e, s, t1, t2, t3 = prices
    if result.direction == "BUY":
        ordered = s < e < t1 < t2 < t3
    elif result.direction == "SELL":
        ordered = s > e > t1 > t2 > t3
    else:
        ordered = False
    if not ordered:
        return "invalid directional geometry"
    rr = abs(t1 - e) / abs(e - s)
    if not _finite_number(result.rr) or abs(result.rr - round(rr, 2)) > 0.011:
        return "RR does not describe TP1"
    if rr < 2.0 - 1e-7 or abs(t2-e)/abs(e-s) < 3.0 - 1e-7 or abs(t3-e)/abs(e-s) < 4.5 - 1e-7:
        return "target floors violated"
    if not _finite_number(result.score) or not 0 <= result.score <= 95:
        return "invalid merit score"
    if not result.reasons or not all(isinstance(r, str) and r.strip() for r in result.reasons):
        return "missing decision reasons"
    if not isinstance(result.checks, dict):
        return "missing decision checks"
    return ""


def checked_strategy(evaluate):
    """Apply the same contract to registry AND direct module callers.

    Invalid inputs/results are rejected with diagnostics. Programming failures
    inside a rule are not swallowed here: evaluate_all logs their traceback.
    No cache is used: callers can mutate/reuse their candle lists.
    """
    code = evaluate.__module__.rsplit(".", 1)[-1].upper()

    @wraps(evaluate)
    def checked(ctx: Ctx) -> StrategyResult | None:
        for label, bars in (("entry", ctx.candles), ("bias", ctx.bias_candles)):
            error = candle_input_error(bars)
            if error:
                log.warning("strategy input rejected code=%s series=%s reason=%s", code, label, error)
                return None
        result = evaluate(ctx)
        if result is not None:
            error = result_error(result, code)
            if error:
                log.error("strategy result rejected code=%s reason=%s", code, error)
                return None
        return result

    return checked
