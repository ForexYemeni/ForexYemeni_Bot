"""Strategy info service — the REAL, documented identity of every methodology.

One source of truth shared by the admin governance panel AND the user package
pages (owner demand v6.10.0: «يجب ان تعرف عمل كل استراتيجية و مميزاتها بشكل
حقيقي 100% وليس كلام وهمي»).

Honesty contract:
  * HOW IT ENTERS is the documented rule set of the ACTUAL engine module —
    never marketing prose. For not-yet-implemented schools the page says
    plainly: مغلق حتى يُنفذ ويُختبر آلياً.
  * STATS come only from strategy_stats.leaderboard() (real closed signals).
    No history yet => «لا بيانات كافية بعد» — invented numbers are forbidden.
"""
from fysb import db
from fysb.theme import esc

# The mechanical playbook of every IMPLEMENTED engine — mirrors the module
# docstrings (tests pin these against the real code paths).
HOW_IT_WORKS = {
    "ICT": [
        "يمسح المحرك مسح السيولة (Liquidity Sweep) فوق/تحت قمة أو قاع مرجعي.",
        "يشترط إزاحة سعرية قوية (Displacement) بعد المسح تترك فجوة قيمة عادلة FVG.",
        "الدخول عند العودة إلى FVG أو كتلة الأوامر Order Block المؤسسية.",
        "الوقف خلف أصل المسح، والأهداف بنسب R متدرجة (2R/3R/5R).",
    ],
    "SMC": [
        "يقرأ هيكل السوق: BOS (كسر هيكل) و CHOCH (تغير شخصية) بالقمم والقيعان.",
        "يصنف القمم والقيعان HH/HL/LH/LL ويحدد الاتجاه المؤسسي الفعلي.",
        "الدخول عند مناطق العرض والطلب Supply & Demand المتوافقة مع الهيكل.",
        "الوقف خلف الهيكل المعيب والأهداف على امتداد الاندفاع المؤسسي.",
    ],
    "PRICE_ACTION": [
        "يكتشف شموع القرار: الابتلاع الصاعد/الهابط، والدبابيس (مطرقة/نجمة).",
        "يشترط كسراً بمغلق مؤكد ثم إعادة اختبار ناجحة لمستوى الدعم/المقاومة.",
        "الدخول بعد الإغلاق المؤكد فوق/تحت المستوى مع الاستمرار.",
        "الوقف خلف الشمعة أو المستوى، والأهداف على امتداد الاختراق.",
    ],
    "CRT": [
        "يرسم المدى المرجعي للشمعة الأعلى (Candle Range).",
        "ينتظر سحب السيولة خارج المدى ثم إغلاق داخل المدى من جديد.",
        "الدخول عند مستوى 50% من المدى (CRT 50%) في اتجاه الإغلاق.",
        "الوقف خلف حد المدى المعيب والأهداف على قياس المدى.",
    ],
    "AMD": [
        "يقسم نطاق الافتتاح: تراكم Accumulation ثم تلاعب Manipulation (Judas Swing) ثم توزيع Distribution.",
        "يشترط خداع واضح خارج النطاق ثم عودة به داخل الاتجاه الحقيقي.",
        "الدخول مع بداية مرحلة التوزيع في اتجاه اليوم.",
        "الوقف خلف مدى الخداع والأهداف على قياس النطاق اليومي.",
    ],
    "TURTLE_SOUP": [
        "يرصد فشل كسر قمة/قاع 20 شمعة (اصطياد سيولة المشترين المتأخرين).",
        "يشترط اختراقاً يفشل بإغلاق عائد داخل النطاق.",
        "الدخول عكس الاختراق الفاشل (انعكاس مضاد).",
        "الوقف خلف قمة الاختراق الفاشل والأهداف نحو الجهة المقابلة للنطاق.",
    ],
    "SILVER_BULLET": [
        "يعمل داخل نوافذ ICT الزمنية (لندن/نيويورك) فقط — خارج النافذة لا يولد شيئاً.",
        "يشترط FVG حديثة في اتجاه الجلسة داخل النافذة.",
        "الدخول عند العودة إلى الفجوة واستمرار الحركة.",
        "الوقف خلف الفجوة والأهداف قياساً على امتداد الجلسة.",
    ],
    "FIBONACCI": [
        "يقيس الموجة الدافعية ويحسب ارتداد 61.8–78.6% (الجيب الذهبي).",
        "يشترط بنية سليمة (عدم كسر الهيكل) عند منطقة الارتداد.",
        "الدخول داخل الجيب مع شمعة تأكيد بالاتجاه.",
        "الوقف خلف أصل الموجة والأهداف عند امتدادات الموجة التالية.",
    ],
    "TREND": [
        "يشترط تراص EMA9/EMA21/EMA50 بفصل حقيقي (≥0.25 ATR) في اتجاه واحد.",
        "يشترط انحياز الإطار الأعلى (EMA21/EMA50) مع الاتجاه — لا تعاكس أبداً.",
        "يشترط زيارة منطقة القيمة (ارتداد إلى EMA21) ثم شمعة استئناف تتجاوز EMA9.",
        "الوقف خلف أصل الارتداد والأهداف 2R/3R/5R.",
    ],
    "ICHIMOKU": [
        "يقرأ كسر السحابة (Kumo Breakout) على الإطار المدخل.",
        "يشترط تقاطع تينكان/كيجون في الاتجاه وسحابة مستقبلية مؤكدة.",
        "الدخول بعد إغلاق مؤكد خارج السحابة مع الاتجاه.",
        "الوقف خلف السحابة والأهداف على امتداد الموجة.",
    ],
    "CLASSIC": [
        "يشترط سعراً على الجهة الصحيحة من EMA50 مع ميل حقيقي للخط.",
        "يشترط مستوى أفقياً حقيقياً (من قمم/قيعان ملموسة) يُعاد اختباره من الجهة الصحيحة.",
        "يشترط شمعة تأكيد (ابتلاع أو دبوس) في اتجاه الصفقة عند المستوى.",
        "الوقف خلف المستوى والقمة/القاع المحلي والأهداف 2R/3R/4.5R.",
    ],
    "SK": [
        "محرك سكالبينج سريع: اتجاه مصغّر EMA9/EMA21 بفصل ≥0.15 ATR.",
        "مستوى مفتاحي آخر 12 شمعة يجب أن يُكسر بإغلاق مؤكد (لا دخول بالذيل).",
        "زخم RSI(14) يؤكد الاندفاعة (≥53 للشراء / ≤47 للبيع).",
        "وقف سكالبينج ضيق خلف شمعة الكسر والأهداف 2R/3R/4R.",
    ],
}

# One-line identity for buttons and compact lists.
SHORT_LABEL = {
    "ICT": "ICT — السيولة المؤسسية",
    "SMC": "SMC — المال الذكي",
    "PRICE_ACTION": "PRICE_ACTION — حركة السعر",
    "CRT": "CRT — مدى الشمعة",
    "AMD": "AMD — قوة الثلاثة",
    "TURTLE_SOUP": "TURTLE_SOUP — اصطياد السيولة",
    "SILVER_BULLET": "SILVER_BULLET — الرصاصة الفضية",
    "FIBONACCI": "FIBONACCI — الجيب الذهبي",
    "TREND": "TREND — اتباع الاتجاه",
    "ICHIMOKU": "ICHIMOKU — السحابة",
    "CLASSIC": "CLASSIC — الكلاسيكية",
    "SK": "SK — السكالبينج",
    "WYCKOFF": "WYCKOFF — وايكوف",
}


def implemented(code: str) -> bool:
    from fysb.engines.strategies import REGISTRY
    return REGISTRY.get((code or "").upper()) is not None


def real_stats(code: str) -> dict:
    """Real closed-signal stats for one strategy (honest when history is thin)."""
    from fysb.services import strategy_stats
    for d in strategy_stats.leaderboard():
        if d["code"] == (code or "").upper():
            return d
    return {}


def info_page(code: str) -> str:
    """The luxury, 100% honest identity card for one strategy (HTML)."""
    code = (code or "").upper()
    with db.conn() as c:
        row = c.execute("SELECT * FROM strategy_catalog WHERE code=?", (code,)).fetchone()
    title = (row["title"] if row else code) or code
    desc = (row["description"] if row else "") or ""
    verified = bool(row and row["verified"])
    enabled = bool(row and row["enabled"])
    is_impl = implemented(code)
    stats = real_stats(code) if is_impl else {}

    state = "🟢 مفعلة وتولّد إشارات الآن" if (verified and enabled) else \
            ("🔵 موثقة ومختبرة — معطلة من الإدارة" if verified else "⚪ مغلقة — غير مختبرة بعد")
    lines = [f"🧩 <b><u>{esc(code)}</u></b> — {esc(title)}",
             "<blockquote>"
             f"▎📌 الحالة: <b>{state}</b>",
             f"▎📖 {esc(desc)}",
             "</blockquote>"]
    steps = HOW_IT_WORKS.get(code)
    if steps:
        lines.append("⚙️ <b>كيف تدخل هذه المنهجية فعلياً (القواعد الميكانيكية للمحرك):</b>")
        lines.append("<blockquote>" + "\n".join(f"▎{i+1}. {esc(s)}" for i, s in enumerate(steps)) + "</blockquote>")
    else:
        lines.append("<blockquote>▎⛔ هذه المدرسة <b>غير منفذة كمحرك بعد</b> — لا تُصنع لها إشارات ولا تُدّعى نتائج. "
                     "تُفتح تلقائياً عندما يكتمل محركها واجتيازه الاختبار الآلي.</blockquote>")
    if stats:
        lines.append("📊 <b>أداؤها الحقيقي من إشارات المنصة المغلقة:</b>")
        lines.append("<blockquote>"
                     f"▎📦 إشارات مغلقة: <b>{stats['n']}</b> · رابحة: <b>{stats['win_rate']:.0f}%</b>\n"
                     f"▎🎯 وصول TP3: <b>{stats['tp3_share']:.0f}%</b> · متوسط R: <b>{stats['avg_r']:+.2f}</b>\n"
                     f"▎🏆 قوة مركبة: <b>{stats['strength']:.1f}</b> (الترتيب العالمي #{stats['rank']})"
                     "</blockquote>")
    elif is_impl:
        lines.append("<blockquote>▎📊 لا توجد إشارات مغلقة لهذه المنهجية بعد — يُبنى الترتيب تلقائياً "
                     "من النتائج الفعلية فور أول إغلاقاتها (لا أرقام مصطنعة أبداً).</blockquote>")
    lines.append("<i>كل ما تقرأ هنا قواعد المحرك الفعلي وأرقام المنصة المسجلة — بلا وعود تسويقية.</i>")
    return "\n".join(lines)
