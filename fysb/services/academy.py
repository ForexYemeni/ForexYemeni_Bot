"""Independent Trading Academy Service (v6.27.0).

Design guarantees:
  * 100% SEPARATE from Trading Signals — signal subscriptions never unlock or
    block the Academy, and Academy enrollment never alters signal plans.
  * Master Admin ON/OFF toggle (`academy_enabled`).
  * Separate FREE Track (5 foundation lessons) & PAID Mastery Track (10
    institutional lessons covering all 13 bot strategies + risk & psychology).
  * Interactive Quiz per lesson + live progress % tracking.
  * 100% Free Local Pillow Royal Completion Certificate PNG.
"""
from __future__ import annotations

import hashlib
import io
import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

from fysb import config, db
from fysb.theme import bar, esc

try:
    from PIL import Image, ImageDraw, ImageFont
    PIL_AVAILABLE = True
except Exception:
    PIL_AVAILABLE = False


# 15 institutional Arabic lessons (5 FREE + 10 PAID covering all 13 strategies)
DEFAULT_LESSONS: list[dict] = [
    # --- FREE TRACK (5 Lessons) ---
    {
        "code": "F01_BASICS",
        "title": "1. أساسيات هيكل السوق والسيولة",
        "track": "FREE",
        "sort_order": 1,
        "summary": "فهم القمم والقيعان (HH/HL/LH/LL) ومفهوم كسر الهيكل BOS وتغير الطابع CHoCH.",
        "content": (
            "📌 <b>القاعدة الذهبية في هيكل السوق:</b>\n"
            "السوق يتحرك بين حالتين فقط: <b>اتجاه (Trend)</b> أو <b>تجميع/توزيع (Range)</b>.\n\n"
            "• <b>الاتجاه الصاعد:</b> قمم أعلى (HH) وقيعان أعلى (HL) — نبحث عن الشراء عند إعادة اختبار القيعان.\n"
            "• <b>الاتجاه الهابط:</b> قمم أدنى (LH) وقيعان أدنى (LL) — نبحث عن البيع عند القمم.\n"
            "• <b>كسر الهيكل (BOS):</b> تأكيد استمرار الاتجاه بإغلاق جسم شمعة واضح.\n"
            "• <b>تغير الطابع (CHoCH):</b> أول إنذار مبكر لانعكاس الاتجاه.\n\n"
            "✅ <b>قائمة الفحص قبل الدخول:</b>\n"
            "1) حدّد اتجاه الفريم الأكبر (H4/H1).\n"
            "2) لا تتداول عكس الهيكل إلا بعد كسر واضح + تأكيد سيولة."
        ),
        "quiz": [
            {
                "q": "ما الذي يثبت استمرار الاتجاه الصاعد بشكل مؤسسي؟",
                "opts": ["إغلاق جسم شمعة أعلى آخر قمة (BOS)", "ظل شمعة طويل فقط بدون إغلاق", "تقاطع عشوائي على فريم الدقيقة"],
                "ans": 0,
            }
        ],
    },
    {
        "code": "F02_CLASSIC_SR",
        "title": "2. الدعوم والمقاومات والترند (CLASSIC & TREND)",
        "track": "FREE",
        "sort_order": 2,
        "summary": "رسم مناطق العرض والطلب النظيفة والالتزام بفلتر EMA200 لتجنب الصفقات العكسية.",
        "content": (
            "📌 <b>منهجية CLASSIC & TREND:</b>\n"
            "الدعم والمقاومة ليست خطاً رفيعاً بل <b>منطقة سيولة (Zone)</b> يرتد منها السعر بقوة.\n\n"
            "• <b>قاعدة تبادل الأدوار:</b> المقاومة المكسورة بإغلاق قوي تتحول إلى دعم مؤسسي.\n"
            "• <b>فلتر الاتجاه EMA200:</b> الشراء فوق المتوسط والبيع تحته يرفع نسبة النجاح بشكل ملموس.\n"
            "• <b>مقياس الزخم (ADX/RSI):</b> تجنّب الدخول في الأسواق العرضية الميتة (ADX &lt; 18)."
        ),
        "quiz": [
            {
                "q": "ماذا تصبح المقاومة القوية بعد كسرها وإغلاق السعر أعلاها بوضوح؟",
                "opts": ["منطقة دعم محتملة عند إعادة الاختبار", "إشارة بيع فورية", "مستوى ملغى تماماً"],
                "ans": 0,
            }
        ],
    },
    {
        "code": "F03_PRICE_ACTION",
        "title": "3. البرايس أكشن والشموع الانعكاسية (PRICE ACTION)",
        "track": "FREE",
        "sort_order": 3,
        "summary": "قراءة شمعة البين بار والابتلاع الشرائي/البيعي داخل مناطق القيمة الحقيقية.",
        "content": (
            "📌 <b>سلوك السعر (Price Action):</b>\n"
            "الشمعة الانعكاسية لا قيمة لها في منتصف الفراغ — قيمتها الحقيقية تكون <b>عند منطقة هيكلية أو سيولة</b>.\n\n"
            "• <b>الابتلاع (Engulfing):</b> جسم شمعة يغطي بالكامل جسم الشمعة السابقة ويكشف دخول سيولة حقيقية.\n"
            "• <b>البين بار (Pin Bar):</b> ذيل رفض طويل يساوي ضعف الجسم على الأقل يدل على رفض السعر.\n"
            "• <b>القاعدة الذهبية:</b> انتظر إغلاق الشمعة دائماً — لا تدخل قبل الإغلاق!"
        ),
        "quiz": [
            {
                "q": "متى تكون شمعة الابتلاع (Engulfing) ذات موثوقية عالية؟",
                "opts": ["عند منطقة دعم/مقاومة أو سيولة مع توافق الاتجاه", "في منتصف نطاق عشوائي", "قبل إغلاق الشمعة بدقائق"],
                "ans": 0,
            }
        ],
    },
    {
        "code": "F04_RISK_LOT",
        "title": "4. إدارة المخاطر وحساب اللوت الصحيح",
        "track": "FREE",
        "sort_order": 4,
        "summary": "حماية رأس المال بقاعدة 1%-2% وحارس السحب اليومي 3% وعائد 1:2 فأعلى.",
        "content": (
            "📌 <b>السر الحقيقي لبقاء المحترفين في السوق:</b>\n"
            "استراتيجية بنسبة نجاح 50% وعائد <b>1:2.5</b> تحقق ثروة — بينما استراتيجية 80% بدون إدارة مخاطرة تُصفّر الحساب!\n\n"
            "• <b>المخاطرة الثابتة:</b> لا تخاطر بأكثر من <b>1% إلى 2%</b> في الصفقة الواحدة.\n"
            "• <b>معادلة اللوت:</b> اللوت = (مبلغ المخاطرة $) ÷ (مسافة الستوب بالنقاط × قيمة النقطة).\n"
            "• <b>حارس السحب اليومي (Daily DD Guard):</b> إذا وصلت خسارتك اليومية 3% أغلق المنصة فوراً.\n"
            "• <b>تأمين الصفقة:</b> عند تحقيق TP1 انقل الوقف إلى نقطة الدخول (Break-Even)."
        ),
        "quiz": [
            {
                "q": "إذا كان رصيدك 1,000$ ونسبة مخاطرتك 1%، ما أقصى خسارة مسموحة في الصفقة؟",
                "opts": ["10 دولار فقط", "100 دولار", "50 دولار"],
                "ans": 0,
            }
        ],
    },
    {
        "code": "F05_FIB_ICHIMOKU",
        "title": "5. فيبوناتشي الذهبي والإيشيموكو (FIB & ICHIMOKU)",
        "track": "FREE",
        "sort_order": 5,
        "summary": "اصطياد مناطق التصحيح الذهبية 0.618–0.786 (OTE) وتوافق سحابة Kumo.",
        "content": (
            "📌 <b>التصحيح المؤسسي والزخم السحابي:</b>\n"
            "• <b>منطقة الدخول المثالية (OTE):</b> بين مستويات فيبوناتشي <b>0.618 و 0.786</b> بعد موجة اندفاعية.\n"
            "• <b>سحابة إيشيموكو (Kumo):</b> التداول فوق السحابة مع تقاطع Tenkan فوق Kijun يمنح فلترة زخم مذهلة.\n"
            "• <b>دمج الأداتين:</b> توافق مستوى 0.618 مع حافة السحابة يصنع نقطة دخول عالية الدقة."
        ),
        "quiz": [
            {
                "q": "ما هي النسبة الذهبية الأشهر للتصحيح المؤسسي (OTE) في فيبوناتشي؟",
                "opts": ["بين 0.618 و 0.786", "مستوى 0.10 فقط", "مستوى 0.236 وحده"],
                "ans": 0,
            }
        ],
    },
    # --- PAID MASTERY TRACK (10 Institutional Lessons) ---
    {
        "code": "P01_SMC_OB",
        "title": "6. الأموال الذكية والأوردر بلوك (SMC Institutional)",
        "track": "PAID",
        "sort_order": 6,
        "summary": "تمييز الأوردر بلوك المؤسسي الحقيقي المرتبط بفجوة FVG وكسر هيكل BOS.",
        "content": (
            "👑 <b>منهجية Smart Money Concepts (SMC):</b>\n"
            "البنوك لا تدخل بصفقة واحدة؛ تترك آثارها عبر <b>Order Blocks</b> متبوعة بإزاحة قوية (Displacement).\n\n"
            "• <b>شروط الـ Order Block الصالح:</b>\n"
            "  1) أحدث سحب سيولة (Liquidity Sweep) قبل الانطلاق.\n"
            "  2) تسبب في كسر هيكل واضح (BOS).\n"
            "  3) ترك فجوة قيمة عادلة (FVG) ولم يُختبر سابقاً (Unmitigated).\n"
            "• <b>الوقـف الاحترافي:</b> خلف ذيل الأوردر بلوك مع هامش ATR محسوب."
        ),
        "quiz": [
            {
                "q": "ما الشرط الذي يرفع جودة الأوردر بلوك المؤسسي إلى أعلى درجة؟",
                "opts": ["سحب سيولة + إزاحة قوية + فجوة FVG + عدم اختباره سابقاً", "أي شمعة حمراء عشوائية", "وجوده في سوق عرضي ميت"],
                "ans": 0,
            }
        ],
    },
    {
        "code": "P02_ICT_FVG",
        "title": "7. فجوات القيمة العادلة ومناطق الخصم (ICT Mastery)",
        "track": "PAID",
        "sort_order": 7,
        "summary": "الشراء في منطقة الخصم Discount (<50%) والبيع في العلاوة Premium (>50%) عبر FVG.",
        "content": (
            "👑 <b>منهجية ICT الاحترافية:</b>\n"
            "لا تشترِ أبداً في منطقة غالية (Premium) ولا تبع في منطقة رخيصة (Discount)!\n\n"
            "• <b>مصفوفة PD Array:</b> ارسم من قاع الموجة إلى قمتها — النصف السفلي للشراء فقط والعلوي للبيع فقط.\n"
            "• <b>فجوة القيمة العادلة (FVG):</b> فراغ سعري بين 3 شموع متتالية يعمل كمغناطيس لإعادة التسعير.\n"
            "• <b>التأكيد الزمني:</b> تزداد الدقة بأكثر من 40% داخل جلسات Killzones (لندن ونيويورك)."
        ),
        "quiz": [
            {
                "q": "في منهجية ICT، أين نبحث عن صفقات الشراء حصراً داخل النطاق السعري؟",
                "opts": ["في منطقة الخصم Discount (أقل من 50%)", "في منطقة العلاوة Premium (أعلى من 50%)", "عند القمة المطلقة"],
                "ans": 0,
            }
        ],
    },
    {
        "code": "P03_SILVER_BULLET",
        "title": "8. الرصاصة الفضية ICT Silver Bullet",
        "track": "PAID",
        "sort_order": 8,
        "summary": "اصطياد السيولة الخوارزمية في ساعات نيويورك ولندن الذهبية مع FVG فائق الدقة.",
        "content": (
            "👑 <b>نموذج ICT Silver Bullet:</b>\n"
            "نموذج زمني-سعري يتكرر يومياً في نوافذ محددة بتوقيت نيويورك:\n"
            "• نافذة لندن: <b>07:00–08:00 UTC</b>\n"
            "• نافذة نيويورك الصباحية: <b>14:00–15:00 UTC</b> (الأقوى للذهب والمؤشرات!)\n\n"
            "• <b>الخطوات الثلاث:</b> سحب قمة/قاع سابق → إزاحة قوية تكوّن FVG → دخول عند إعادة لمس FVG بهدف 1:2 إلى 1:3."
        ),
        "quiz": [
            {
                "q": "ما الركيزة الأساسية لنجاح نموذج Silver Bullet؟",
                "opts": ["الوقت المحدد (Killzone) + سحب سيولة + فجوة FVG", "التداول في الساعة 23:00 UTC الهادئة", "مؤشر MACD فقط"],
                "ans": 0,
            }
        ],
    },
    {
        "code": "P04_WYCKOFF",
        "title": "9. وايكوف: التجميع والتوزيع واصطياد الـ Spring (WYCKOFF)",
        "track": "PAID",
        "sort_order": 9,
        "summary": "قراءة بصمات المؤسسات في مراحل التجميع Spring والتوزيع UTAD قبل الانفجار السعري.",
        "content": (
            "👑 <b>مدرسة Wyckoff المؤسسية:</b>\n"
            "قبل كل موجة صعود كبرى، تقوم المؤسسات بـ <b>Spring</b> (كسر كاذب أسفل قاع التجميع لضرب ستوبات الأفراد).\n\n"
            "• <b>نموذج الشراء (Spring):</b> كسر سريع أسفل نطاق التجميع ثم إغلاق فوري داخل النطاق بحجم تداول قوي.\n"
            "• <b>نموذج البيع (UTAD):</b> اختراق كاذب أعلى قمة التوزيع ثم هبوط حاد يكسر الهيكل الداخلي.\n"
            "• <b>الميزة التنافسية:</b> يمنحك أصغر وقف خسارة مع أعلى عائد R:R في السوق (1:3+)."
        ),
        "quiz": [
            {
                "q": "ما هو الـ Spring في منهجية وايكوف؟",
                "opts": ["كسر كاذب أسفل نطاق التجميع لسحب السيولة قبل الصعود القوي", "كسر حقيقي يعلن انهيار السوق", "نموذج استمراري في نهاية الصعود"],
                "ans": 0,
            }
        ],
    },
    {
        "code": "P05_CRT",
        "title": "10. نظرية نطاق الشمعة CRT (Candle Range Theory)",
        "track": "PAID",
        "sort_order": 10,
        "summary": "استغلال قمة وقاع شمعة الفريم الأكبر كخريطة سيولة لاقتناص انعكاسات دقيقة.",
        "content": (
            "👑 <b>منهجية CRT المؤسسية:</b>\n"
            "كل شمعة H4 أو يومية هي نطاق تداول متكامل يحتوي على سيولة أعلى قمتها وأسفل قاعها.\n\n"
            "• <b>الآلية:</b> عندما تسحب الشمعة الجديدة قمة الشمعة السابقة وتُغلق داخلها، يصبح الهدف الطبيعي هو <b>الطرف المقابل للنطاق (50% ثم 100%)</b>.\n"
            "• <b>الدخول المثالي:</b> النزول إلى M15 بعد سحب قمة/قاع شمعة H4 وانتظار كسر هيكل داخلي."
        ),
        "quiz": [
            {
                "q": "إذا سحب السعر قمة شمعة H4 السابقة ثم أغلق داخل نطاقها، ما الهدف المؤسسي الأول؟",
                "opts": ["منتصف النطاق 50% ثم قاع الشمعة", "مضاعفة الصعود للأعلى فوراً", "إغلاق المنصة للأبد"],
                "ans": 0,
            }
        ],
    },
    {
        "code": "P06_AMD_PO3",
        "title": "11. قوة الثلاثة AMD / Power of 3",
        "track": "PAID",
        "sort_order": 11,
        "summary": "التجميع الآسيوي → التلاعب اللندني (Judas Swing) → التوزيع النيويوركي.",
        "content": (
            "👑 <b>دورة Power of 3 (AMD):</b>\n"
            "1) <b>Accumulation (التجميع):</b> نطاق ضيق هادئ غالباً في الجلسة الآسيوية.\n"
            "2) <b>Manipulation (التلاعب / Judas Swing):</b> حركة كاذبة سريعة عكس الاتجاه الحقيقي لضرب الستوبات في افتتاح لندن.\n"
            "3) <b>Distribution (التوزيع):</b> الحركة الحقيقية القوية في الاتجاه الصحيح حتى نهاية نيويورك.\n\n"
            "💡 <b>القاعدة:</b> لا تطارد اختراق آسيا الأول — انتظر فخ التلاعب ثم اركب موجة التوزيع!"
        ),
        "quiz": [
            {
                "q": "ما هو Judas Swing في دورة AMD؟",
                "opts": ["حركة التلاعب الكاذبة التي تسحب السيولة قبل الانطلاق الحقيقي", "حركة التجميع الهادئة في آسيا", "إغلاق السوق يوم الجمعة"],
                "ans": 0,
            }
        ],
    },
    {
        "code": "P07_TURTLE_SOUP",
        "title": "12. حساء السلاحف واصطياد الستوبات (TURTLE SOUP)",
        "track": "PAID",
        "sort_order": 12,
        "summary": "الربح من فشل الاختراقات الكاذبة عند قمم وقيعان 20 شمعة مع عودة فورية للسعر.",
        "content": (
            "👑 <b>استراتيجية Turtle Soup:</b>\n"
            "تستهدف المتداولين الذين يشترون الاختراقات المتأخرة عند قمة 20 شمعة أو يبيعون القاع.\n\n"
            "• <b>إشارة الشراء:</b> يسجل السعر قاعاً جديداً تحت قاع آخر 20 شمعة ثم يرتد ويغلق فوق القاع المكسور.\n"
            "• <b>إشارة البيع:</b> اختراق كاذب أعلى قمة 20 شمعة مع ذيل رفض واضح وإغلاق أدنى القمة.\n"
            "• <b>السر:</b> كلما كان ذيل الرفض أطول زادت سرعة الارتداد نحو الطرف المقابل."
        ),
        "quiz": [
            {
                "q": "على ماذا تعتمد استراتيجية Turtle Soup؟",
                "opts": ["الاختراق الكاذب لقمة/قاع 20 شمعة والعودة الفورية داخل النطاق", "الشراء عند كل قمة جديدة مهما حدث", "مضاعفة اللوت عند الخسارة"],
                "ans": 0,
            }
        ],
    },
    {
        "code": "P08_SK_SYSTEM",
        "title": "13. نظام SK المتقدم ومناطق الارتداد الدقيقة (SK)",
        "track": "PAID",
        "sort_order": 13,
        "summary": "تسلسلات فيبوناتشي المتداخلة ومناطق الانعكاس عالية الاحتمالية على الذهب والمؤشرات.",
        "content": (
            "👑 <b>نظام SK الهندسي:</b>\n"
            "يركز على دمج تسلسلات التصحيح العميقة (0.50 – 0.618 – 0.667) مع موجات الزخم المؤسسي.\n\n"
            "• <b>نقطة القوة:</b> تحديد مناطق انعكاس ضيقة جداً للذهب (XAUUSD) تمنح وقفاً صغيراً وأهدافاً ممتدة.\n"
            "• <b>الشرط الإلزامي:</b> عدم كسر مستوى الإبطال (0.786/1.0) بإغلاق جسم شمعة."
        ),
        "quiz": [
            {
                "q": "ما الذي يبطل منطقة الارتداد في نظام SK؟",
                "opts": ["إغلاق جسم شمعة واضح خارج حد الإبطال", "لمس السعر لمستوى 0.618", "تباطؤ السعر داخل المنطقة"],
                "ans": 0,
            }
        ],
    },
    {
        "code": "P09_NEWS_CORRELATION",
        "title": "14. درع الأخبار، ارتباط الدولار DXY وقوة العملات",
        "track": "PAID",
        "sort_order": 14,
        "summary": "تجنب مصائد CPI/NFP/FOMC وتأكيد الذهب واليورو عبر اتجاه مؤشر الدولار DXY.",
        "content": (
            "👑 <b>الوعي الكلي للسوق (Macro & Correlation):</b>\n"
            "• <b>درع الأخبار (News Guard):</b> تجنب فتح صفقات جديدة قبل وبعد الأخبار الحمراء (CPI / NFP / FOMC) بـ 30 دقيقة.\n"
            "• <b>الارتباط العكسي مع الدولار:</b> صعود مؤشر الدولار DXY يضغط هبوطاً على XAUUSD و EURUSD و GBPUSD.\n"
            "• <b>مقياس قوة العملات الـ 8:</b> اختر دائماً <b>أقوى عملة ضد أضعف عملة</b> لأسرع حركة اتجاهية."
        ),
        "quiz": [
            {
                "q": "كيف يؤثر الصعود القوي لمؤشر الدولار (DXY) عادةً على الذهب XAUUSD؟",
                "opts": ["يمارس ضغطاً هبوطياً (علاقة عكسية غالباً)", "يرفع الذهب دائماً بنسبة 100%", "لا توجد أي علاقة اقتصادية"],
                "ans": 0,
            }
        ],
    },
    {
        "code": "P10_PSYCHOLOGY_JOURNAL",
        "title": "15. سيكولوجية المحترفين ومفكرة التداول التلقائية",
        "track": "PAID",
        "sort_order": 15,
        "summary": "التغلب على FOMO والتداول الانتقامي وبناء انضباط إحصائي عبر المفكرة اليومية.",
        "content": (
            "👑 <b>علم نفس التداول والانضباط المؤسسي:</b>\n"
            "80% من خسائر المتداولين سببها نفسي: <b>FOMO (الخوف من فوات الفرصة)</b> و <b>Revenge Trading (الانتقام بعد الخسارة)</b>.\n\n"
            "• <b>خطة البروتوكول الملكي:</b>\n"
            "  1) سجّل كل صفقة بضغطة (✅ دخلت / ❌ تجاهلت) في مفكرة البوت التلقائية.\n"
            "  2) راجع أداءك الأسبوعي — ستكتشف فوراً أفضل استراتيجية وأفضل جلسة تناسب شخصيتك.\n"
            "  3) التزم بقاعدة: <b>صفقتان خاسرتان متتاليتان = إغلاق اليوم والعودة غداً بذهن صافٍ</b>.\n\n"
            "🎓 <b>تهانينا!</b> بإتمامك هذا الدرس والاختبار تصبح مؤهلاً لإصدار <b>شهادتك الملكية المعتمدة من أكاديمية ForexYemeni</b>!"
        ),
        "quiz": [
            {
                "q": "ما التصرف الاحترافي الصحيح بعد خسارتين متتاليتين وصلتا إلى حد السحب اليومي؟",
                "opts": ["التوقف الفوري عن التداول لبقية اليوم ومراجعة المفكرة بهدوء", "مضاعفة اللوت فوراً لتعويض الخسارة", "إزالة وقف الخسارة في الصفقة القادمة"],
                "ans": 0,
            }
        ],
    },
]


def is_enabled() -> bool:
    return (db.get_setting("academy_enabled", "1") or "1") == "1"


def set_enabled(enabled: bool) -> bool:
    db.set_setting("academy_enabled", "1" if enabled else "0")
    return bool(enabled)


def paid_price_usd() -> float:
    try:
        return max(1.0, float(db.get_setting("academy_price_usd", "49") or "49"))
    except (TypeError, ValueError):
        return 49.0


def paid_duration_days() -> int:
    try:
        return max(0, int(db.get_setting("academy_duration_days", "365") or "365"))
    except (TypeError, ValueError):
        return 365


def set_pricing(price_usd: float | None = None, duration_days: int | None = None) -> tuple[float, int]:
    if price_usd is not None:
        db.set_setting("academy_price_usd", f"{max(1.0, float(price_usd)):g}")
    if duration_days is not None:
        db.set_setting("academy_duration_days", str(max(0, int(duration_days))))
    return paid_price_usd(), paid_duration_days()


def seed_default_lessons() -> int:
    """Idempotently seed the 15 institutional lessons without overwriting Admin edits."""
    inserted = 0
    now_iso = config.now()
    with db.conn() as c:
        for les in DEFAULT_LESSONS:
            exists = c.execute("SELECT 1 FROM academy_lessons WHERE code=?", (les["code"],)).fetchone()
            if not exists:
                c.execute(
                    """INSERT INTO academy_lessons(code,title,track,sort_order,summary,content,quiz_json,enabled,updated_at)
                       VALUES(?,?,?,?,?,?,?,1,?)""",
                    (
                        les["code"], les["title"], les["track"], les["sort_order"],
                        les["summary"], les["content"], json.dumps(les["quiz"], ensure_ascii=False),
                        now_iso,
                    ),
                )
                inserted += 1
    return inserted


def ensure_seeded() -> None:
    with db.conn() as c:
        cnt = c.execute("SELECT COUNT(*) n FROM academy_lessons").fetchone()["n"]
    if cnt == 0:
        seed_default_lessons()


def list_lessons(track: str | None = None, include_disabled: bool = False) -> list[dict]:
    ensure_seeded()
    sql = "SELECT * FROM academy_lessons WHERE 1=1"
    args: list = []
    if not include_disabled:
        sql += " AND enabled=1"
    if track:
        sql += " AND UPPER(track)=?"
        args.append(track.strip().upper())
    sql += " ORDER BY sort_order ASC, id ASC"
    with db.conn() as c:
        rows = [dict(r) for r in c.execute(sql, tuple(args)).fetchall()]
    for r in rows:
        try:
            r["quiz"] = json.loads(r.get("quiz_json") or "[]")
        except Exception:
            r["quiz"] = []
    return rows


def get_lesson(code: str) -> dict | None:
    ensure_seeded()
    with db.conn() as c:
        r = c.execute("SELECT * FROM academy_lessons WHERE code=?", (code.strip(),)).fetchone()
    if not r:
        return None
    d = dict(r)
    try:
        d["quiz"] = json.loads(d.get("quiz_json") or "[]")
    except Exception:
        d["quiz"] = []
    return d


def toggle_lesson_track(code: str) -> str | None:
    les = get_lesson(code)
    if not les:
        return None
    nxt = "FREE" if str(les["track"]).upper() == "PAID" else "PAID"
    with db.conn() as c:
        c.execute("UPDATE academy_lessons SET track=?, updated_at=? WHERE code=?", (nxt, config.now(), code))
    return nxt


def toggle_lesson_enabled(code: str) -> bool | None:
    les = get_lesson(code)
    if not les:
        return None
    nxt = 0 if int(les.get("enabled") or 0) == 1 else 1
    with db.conn() as c:
        c.execute("UPDATE academy_lessons SET enabled=?, updated_at=? WHERE code=?", (nxt, config.now(), code))
    return bool(nxt)


def has_paid_access(user_id: int) -> bool:
    """Strictly independent from trading-signal subscriptions."""
    if int(user_id) == config.ADMIN_ID:
        return True
    with db.conn() as c:
        r = c.execute("SELECT * FROM academy_enrollments WHERE user_id=?", (int(user_id),)).fetchone()
    if not r:
        return False
    ends = str(r["ends_at"] or "").strip()
    if not ends:
        return True  # lifetime pass
    try:
        dt = datetime.fromisoformat(ends.replace("Z", "+00:00"))
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt > datetime.now(timezone.utc)
    except Exception:
        return True


def enrollment_info(user_id: int) -> dict | None:
    with db.conn() as c:
        r = c.execute("SELECT * FROM academy_enrollments WHERE user_id=?", (int(user_id),)).fetchone()
    return dict(r) if r else None


def grant_paid_access(
    user_id: int,
    duration_days: int | None = None,
    source: str = "MANUAL",
    granted_by: int = 0,
) -> dict:
    """Grant or extend independent Paid Academy access (0 days = lifetime)."""
    days = paid_duration_days() if duration_days is None else max(0, int(duration_days))
    now_dt = datetime.now(timezone.utc)
    now_iso = now_dt.isoformat()
    ends_iso = ""
    if days > 0:
        # Extend from existing unexpired ends_at if present
        cur = enrollment_info(user_id)
        base_dt = now_dt
        if cur and cur.get("ends_at"):
            try:
                old_dt = datetime.fromisoformat(str(cur["ends_at"]).replace("Z", "+00:00"))
                if old_dt.tzinfo is None:
                    old_dt = old_dt.replace(tzinfo=timezone.utc)
                if old_dt > now_dt:
                    base_dt = old_dt
            except Exception:
                pass
        ends_iso = (base_dt + timedelta(days=days)).isoformat()
    with db.conn() as c:
        c.execute(
            """INSERT INTO academy_enrollments(user_id,plan_type,starts_at,ends_at,source,granted_by,created_at)
               VALUES(?,?,?,?,?,?,?)
               ON CONFLICT(user_id) DO UPDATE SET
                 plan_type='PAID', ends_at=excluded.ends_at, source=excluded.source, granted_by=excluded.granted_by""",
            (int(user_id), "PAID", now_iso, ends_iso, source, int(granted_by), now_iso),
        )
    return {"user_id": int(user_id), "duration_days": days, "ends_at": ends_iso, "lifetime": days == 0}


def revoke_paid_access(user_id: int) -> bool:
    with db.conn() as c:
        cur = c.execute("DELETE FROM academy_enrollments WHERE user_id=?", (int(user_id),))
        return cur.rowcount > 0


def submit_quiz_answer(user_id: int, lesson_code: str, opt_idx: int, q_idx: int = 0) -> dict:
    """Evaluate a student's quiz choice and record lesson completion if passed."""
    les = get_lesson(lesson_code)
    if not les:
        return {"ok": False, "error": "lesson_not_found"}
    quiz = les.get("quiz") or []
    if not quiz or q_idx >= len(quiz):
        mark_lesson_complete(user_id, lesson_code, 1, 1)
        return {"ok": True, "correct": True, "lesson": les}
    q_item = quiz[q_idx]
    correct = int(opt_idx) == int(q_item.get("ans", 0))
    if correct:
        mark_lesson_complete(user_id, lesson_code, 1, len(quiz))
    return {
        "ok": True,
        "correct": correct,
        "expected_idx": int(q_item.get("ans", 0)),
        "expected_text": q_item["opts"][int(q_item.get("ans", 0))],
        "lesson": les,
    }


def mark_lesson_complete(user_id: int, lesson_code: str, quiz_score: int = 1, quiz_total: int = 1) -> None:
    now_iso = config.now()
    with db.conn() as c:
        c.execute(
            """INSERT INTO academy_progress(user_id,lesson_code,completed,quiz_score,quiz_total,completed_at)
               VALUES(?,?,1,?,?,?)
               ON CONFLICT(user_id,lesson_code) DO UPDATE SET
                 completed=1,
                 quiz_score=MAX(academy_progress.quiz_score, excluded.quiz_score),
                 quiz_total=excluded.quiz_total,
                 completed_at=excluded.completed_at""",
            (int(user_id), lesson_code, int(quiz_score), max(1, int(quiz_total)), now_iso),
        )


def user_progress(user_id: int) -> dict:
    ensure_seeded()
    all_les = list_lessons()
    free_codes = [l["code"] for l in all_les if str(l["track"]).upper() == "FREE"]
    paid_codes = [l["code"] for l in all_les if str(l["track"]).upper() == "PAID"]
    with db.conn() as c:
        rows = c.execute(
            "SELECT lesson_code, completed, quiz_score, quiz_total FROM academy_progress WHERE user_id=? AND completed=1",
            (int(user_id),),
        ).fetchall()
    done = {r["lesson_code"] for r in rows}
    free_done = sum(1 for code in free_codes if code in done)
    paid_done = sum(1 for code in paid_codes if code in done)
    total_done = free_done + paid_done
    total_all = len(free_codes) + len(paid_codes)
    free_pct = round(free_done / len(free_codes) * 100.0, 1) if free_codes else 0.0
    paid_pct = round(paid_done / len(paid_codes) * 100.0, 1) if paid_codes else 0.0
    overall_pct = round(total_done / total_all * 100.0, 1) if total_all else 0.0
    paid_access = has_paid_access(user_id)
    cert_ready_free = len(free_codes) > 0 and free_done >= len(free_codes)
    cert_ready_paid = paid_access and len(paid_codes) > 0 and paid_done >= len(paid_codes)
    return {
        "completed_codes": done,
        "free_done": free_done,
        "free_total": len(free_codes),
        "free_pct": free_pct,
        "paid_done": paid_done,
        "paid_total": len(paid_codes),
        "paid_pct": paid_pct,
        "total_done": total_done,
        "total_all": total_all,
        "overall_pct": overall_pct,
        "has_paid": paid_access,
        "cert_ready_free": cert_ready_free,
        "cert_ready_paid": cert_ready_paid,
    }


def certificate_serial(user_id: int, track: str = "PAID") -> str:
    yr = datetime.now(timezone.utc).year
    h = hashlib.sha256(f"FY-ACAD-{user_id}-{track.upper()}".encode()).hexdigest()[:6].upper()
    return f"FY-ACAD-{yr}-{h}"


def _font(size: int, bold: bool = False):
    paths = (
        ["/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf", "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf"]
        if bold
        else ["/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf", "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf"]
    )
    for p in paths:
        if Path(p).exists():
            try:
                return ImageFont.truetype(p, size)
            except Exception:
                pass
    return ImageFont.load_default()


def render_certificate_png(user_id: int, student_name: str, track: str = "PAID") -> bytes | None:
    """100% Free Local Pillow Royal Institutional Diploma Certificate (1400x900 PNG)."""
    if not PIL_AVAILABLE:
        return None
    w, h = 1400, 900
    bg = (11, 14, 22, 255)
    panel = (16, 21, 34, 255)
    gold = (224, 184, 76, 255)
    gold_dim = (130, 105, 42, 255)
    white = (242, 246, 255, 255)
    muted = (158, 170, 195, 255)
    emerald = (22, 199, 132, 255)

    img = Image.new("RGBA", (w, h), bg)
    draw = ImageDraw.Draw(img)

    # Outer & inner ornamental gold frame
    draw.rectangle([28, 28, w - 28, h - 28], fill=panel, outline=gold, width=4)
    draw.rectangle([46, 46, w - 46, h - 46], outline=gold_dim, width=2)
    # Corner accents
    for cx, cy in ((46, 46), (w - 46, 46), (46, h - 46), (w - 46, h - 46)):
        draw.ellipse([cx - 10, cy - 10, cx + 10, cy + 10], fill=gold)

    f_brand = _font(28, bold=True)
    f_title = _font(54, bold=True)
    f_sub = _font(26, bold=False)
    f_name = _font(58, bold=True)
    f_track = _font(30, bold=True)
    f_meta = _font(22, bold=False)

    def _cx(text: str, font, y: int, fill):
        bb = draw.textbbox((0, 0), text, font=font)
        tw = bb[2] - bb[0]
        draw.text(((w - tw) // 2, y), text, font=font, fill=fill)

    safe_name = "".join(ch for ch in (student_name or f"Trader #{user_id}") if ord(ch) < 128).strip()
    if len(safe_name) < 2:
        safe_name = f"Trader ID #{user_id}"

    t_upper = (track or "PAID").upper()
    track_label = (
        "INSTITUTIONAL PRO MASTERY TRACK (13 STRATEGIES)"
        if t_upper == "PAID"
        else "FOUNDATION TRADING TRACK"
    )
    serial = certificate_serial(user_id, t_upper)
    date_str = datetime.now(timezone.utc).strftime("%Y-%m-%d UTC")

    _cx("FOREXYEMENI ROYAL TRADING ACADEMY", f_brand, 92, gold)
    draw.line([(360, 142), (w - 360, 142)], fill=gold_dim, width=2)
    _cx("CERTIFICATE OF COMPLETION", f_title, 175, white)
    _cx("THIS PROUDLY CERTIFIES THAT", f_sub, 272, muted)
    _cx(safe_name.upper(), f_name, 338, gold)
    draw.line([(280, 422), (w - 280, 422)], fill=gold, width=3)
    _cx("HAS SUCCESSFULLY COMPLETED THE CURRICULUM & EXAMINATIONS OF", f_sub, 458, muted)
    _cx(track_label, f_track, 515, emerald)
    _cx("Smart Money Concepts · ICT · Wyckoff · Silver Bullet · Risk & Psychology", f_meta, 578, white)

    # Footer metadata
    draw.line([(120, 710), (w - 120, 710)], fill=gold_dim, width=1)
    draw.text((130, 742), f"ISSUED: {date_str}", font=f_meta, fill=muted)
    draw.text((130, 778), f"STUDENT UID: {user_id}", font=f_meta, fill=muted)
    _cx("VERIFIED OFFICIAL DIPLOMA", f_meta, 756, gold)
    ser_txt = f"SERIAL: {serial}"
    bb = draw.textbbox((0, 0), ser_txt, font=f_meta)
    draw.text((w - 130 - (bb[2] - bb[0]), 742), ser_txt, font=f_meta, fill=white)
    sig_txt = "ForexYemeni Institutional Board"
    bb2 = draw.textbbox((0, 0), sig_txt, font=f_meta)
    draw.text((w - 130 - (bb2[2] - bb2[0]), 778), sig_txt, font=f_meta, fill=gold)

    buf = io.BytesIO()
    img.convert("RGB").save(buf, format="PNG", optimize=True)
    return buf.getvalue()


def render_academy_home_card(user_id: int) -> str:
    """Luxury Independent Trading Academy home screen."""
    prog = user_progress(user_id)
    price = paid_price_usd()
    days = paid_duration_days()
    dur_txt = "مدى الحياة ♾" if days == 0 else f"{days} يوماً"
    paid_badge = "👑 مفعّل (وصول كامل)" if prog["has_paid"] else f"🔒 مقفل (اشتراك مستقل {price:g}$ / {dur_txt})"
    return (
        "🎓 <b><u>أكاديمية ForexYemeni الملكية للتداول</u></b>\n"
        "<i>منصة تعليمية مستقلة بالكامل عن إشارات التداول — منهج الـ 13 استراتيجية المؤسسية</i>\n\n"
        "<blockquote>"
        f"▎📗 <b>المسار المجاني (الأساسيات):</b> {prog['free_done']}/{prog['free_total']} درس ({prog['free_pct']:.0f}%) {bar(prog['free_pct'], 100)}\n"
        f"▎👑 <b>المسار الاحترافي المدفوع:</b> {paid_badge}\n"
        f"▎📊 <b>تقدمك الاحترافي:</b> {prog['paid_done']}/{prog['paid_total']} درس ({prog['paid_pct']:.0f}%) {bar(prog['paid_pct'], 100)}\n"
        f"▎🏆 <b>التقدم الإجمالي:</b> {prog['total_done']}/{prog['total_all']} درس ({prog['overall_pct']:.0f}%)\n"
        f"▎📜 <b>الشهادة الملكية المعتمدة:</b> "
        f"{'✅ جاهزة للإصدار الفوري!' if (prog['cert_ready_free'] or prog['cert_ready_paid']) else 'أكمل أحد المسارين واجتز اختباراته لإصدارها مجاناً'}"
        "</blockquote>"
    )


def render_lesson_card(user_id: int, les: dict) -> str:
    prog = user_progress(user_id)
    done = les["code"] in prog["completed_codes"]
    track_badge = "📗 مسار مجاني" if str(les["track"]).upper() == "FREE" else "👑 مسار احترافي"
    status_badge = "✅ مكتمل" if done else "📖 قيد الدراسة"
    quiz = les.get("quiz") or []
    quiz_block = ""
    if quiz:
        q0 = quiz[0]
        opts_txt = "\n".join(f"  {i + 1}) {esc(opt)}" for i, opt in enumerate(q0["opts"]))
        quiz_block = (
            f"\n\n📝 <b><u>اختبار إتمام الدرس:</u></b>\n"
            f"<blockquote><b>{esc(q0['q'])}</b>\n{opts_txt}</blockquote>"
        )
    return (
        f"🎓 <b><u>{esc(les['title'])}</u></b>\n"
        f"<i>{track_badge} · {status_badge}</i>\n\n"
        f"<blockquote>{les['content']}</blockquote>"
        f"{quiz_block}"
    )


def admin_stats() -> dict:
    ensure_seeded()
    with db.conn() as c:
        lessons_total = c.execute("SELECT COUNT(*) n FROM academy_lessons").fetchone()["n"]
        free_n = c.execute("SELECT COUNT(*) n FROM academy_lessons WHERE track='FREE' AND enabled=1").fetchone()["n"]
        paid_n = c.execute("SELECT COUNT(*) n FROM academy_lessons WHERE track='PAID' AND enabled=1").fetchone()["n"]
        enrolled = c.execute("SELECT COUNT(*) n FROM academy_enrollments").fetchone()["n"]
        completions = c.execute("SELECT COUNT(*) n FROM academy_progress WHERE completed=1").fetchone()["n"]
        students = c.execute("SELECT COUNT(DISTINCT user_id) n FROM academy_progress").fetchone()["n"]
    return {
        "enabled": is_enabled(),
        "price_usd": paid_price_usd(),
        "duration_days": paid_duration_days(),
        "lessons_total": lessons_total,
        "free_lessons": free_n,
        "paid_lessons": paid_n,
        "enrolled_paid": enrolled,
        "active_students": students,
        "lesson_completions": completions,
    }


def render_admin_academy_card() -> str:
    st = admin_stats()
    status = "🟢 مفتوحة ومفعّلة للمستخدمين" if st["enabled"] else "🔴 مغلقة مؤقتاً من الإدارة"
    dur_txt = "مدى الحياة ♾" if st["duration_days"] == 0 else f"{st['duration_days']} يوماً"
    return (
        "🎓 <b><u>إدارة أكاديمية التداول المستقلة</u></b>\n"
        "<blockquote>"
        f"▎⚡ حالة الأكاديمية: <b>{status}</b>\n"
        f"▎💵 سعر المسار المدفوع: <b>{st['price_usd']:g} $</b> · المدة: <b>{dur_txt}</b>\n"
        f"▎📚 المنهج: <b>{st['lessons_total']}</b> درس ({st['free_lessons']} مجاني · {st['paid_lessons']} مدفوع)\n"
        f"▎👑 المشتركون في المسار المدفوع: <b>{st['enrolled_paid']}</b>\n"
        f"▎👨‍🎓 الطلاب النشطون: <b>{st['active_students']}</b> · دروس مكتملة: <b>{st['lesson_completions']}</b>"
        "</blockquote>"
    )
