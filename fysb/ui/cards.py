"""Card templates — signal card, analysis card, payment/user/plan cards.

The signal card is the platform's product: full precision stays in the DB,
display formatting goes through instruments.spec() digits per symbol.
Cards use native Telegram <blockquote> surfaces with bold labels — the luxury
look requested for every section.
Users NEVER see machine codes: strategies/timeframes/statuses are always
rendered through the Arabic name maps, and the data source is shown as a
neutral "بيانات السوق اللحظية" (provider names are never exposed).
"""
from fysb import instruments
from fysb.theme import (DIRECTION_EMOJI, ICON, bar, card, dir_badge, esc,
                        expandable, footer, human_dt, is_signal_closed, rich,
                        signal_status_ar, signal_status_badge, status_ar, status_badge,
                        strategy_name, tf_name)


def fmt_sym_price(symbol: str, price: float, row=None) -> str:
    return instruments.fmt_price(symbol, price, row)


def _market_state_line(symbol: str, row=None) -> str:
    from fysb.engines.market_data import market_state
    cat = (row or {}).get("category") if row else None
    if cat is None:
        cat = instruments.spec(symbol).category
    open_now = market_state(cat)
    if open_now is True:
        return "🟢 <b>السوق مفتوح الآن</b> — بيانات حية"
    if open_now is False:
        return "🔴 <b>السوق مغلق الآن</b> — الأسعار آخر أسعار الإغلاق الفعلية"
    return "🟡 حالة السوق غير محددة لهذه الأداة"


def render_signal_card(sig, user_view: bool = False, row=None) -> str:
    """The unified luxury signal card (native Telegram cards)."""
    symbol = sig["symbol"]
    direction = sig["direction"]
    d_emoji = DIRECTION_EMOJI.get(direction, "")
    status_line = signal_status_badge(sig)
    spec = instruments.spec(symbol, row)

    # Prices card — each level with its pip distance from entry
    def level_line(icon, label, price):
        pips_d = instruments.pips(symbol, sig["entry"], price, row)
        return (f"{icon} <b>{label}:</b> <code>{fmt_sym_price(symbol, price, row)}</code>"
                f"  <i>({pips_d:+.1f} pips)</i>")

    levels = [
        f"{ICON['target']} <b>المنطقة / سعر الدخول:</b> <code>{fmt_sym_price(symbol, sig['entry'], row)}</code>",
        level_line("🛑", "وقف الخسارة SL", sig["stop"]),
        level_line("🏁", "الهدف الأول TP1", sig["tp1"]),
        level_line("🏁", "الهدف الثاني TP2", sig["tp2"]),
        level_line("🏁", "الهدف الثالث TP3", sig["tp3"]),
    ]

    rr = float(sig["rr"] or 0)
    score = int(sig["score"] or 0)
    meta = [
        f"⚖️ <b>المخاطرة/العائد:</b> <code>1:{rr:.2f}</code>  {bar(min(rr, 5), 5)}",
        f"⭐ <b>الجودة:</b> <code>{score}/100</code>  {bar(score, 100)}",
        f"🧠 <b>الاستراتيجية:</b> <b>{esc(strategy_name(sig['strategy']))}</b>",
        f"📌 <b>الحالة:</b> {status_line}",
    ]

    head = (f"{d_emoji} <b><u>{esc(symbol)}</u></b>  ·  <b>{'شراء' if direction == 'BUY' else 'بيع'}</b>  ·  "
            f"<b>{esc(tf_name(sig['timeframe']))}</b>\n{status_line}")

    out = [head, card("\n".join(levels)), card("\n".join(meta))]

    if sig["reason"]:
        out.append(f"{ICON['book']} <b>سبب الدخول (سياق القرار):</b>")
        out.append(expandable(esc(sig["reason"])))

    if user_view:
        data_lines = [_market_state_line(symbol, row)]
        dt = human_dt(sig["data_time"]) if sig["data_time"] else "—"
        data_lines.append(f"🕐 <b>آخر شمعة سوق فعلية:</b> {dt}")
        data_lines.append(f"🕐 <b>وقت الإصدار:</b> {human_dt(sig['created_at'])}")
        try:
            closed_at = sig["closed_at"]
        except (KeyError, IndexError, TypeError):
            closed_at = ""
        if closed_at:
            data_lines.append(f"🔒 <b>وقت الإغلاق:</b> {human_dt(closed_at)}")
        if sig["pips_result"]:
            emoji = "📈" if sig["pips_result"] > 0 else "📉"
            data_lines.append(f"{emoji} <b>النتيجة المسجلة:</b> {sig['pips_result']:+.1f} pips")
        out.append(card("\n".join(data_lines)))
        out.append(footer(""))
    return "\n".join(out)


def render_analysis_card(an: dict, market_row=None) -> str:
    """LUXURY analytics recommendation card — real price, no fabricated targets.

    an keys: symbol, timeframe, tool, analysis_type, strategy, price, price_time,
             direction, quality, summary(list[str]), zones(list[str]),
             activation, invalidation, risk_note, market_open(bool|None)
    """
    d_emoji = DIRECTION_EMOJI.get(an.get("direction") or "", "🔵")
    dir_txt = {"BUY": "صاعد", "SELL": "هبوطي", "NEUTRAL": "محايد/انتظاري"}.get(an.get("direction") or "", "محايد")
    quality = int(an.get("quality") or 0)

    head = (f"{ICON['lab']} <b><u>تحليل {esc(an['symbol'])}</u></b> · <b>{esc(an['timeframe'])}</b>\n"
            f"🧠 <b>المنهجية:</b> <b>{esc(an.get('strategy_name') or 'قراءة مؤسسية شاملة')}</b>")

    price_block = [
        f"💰 <b>سعر السوق الحقيقي الآن:</b> <code>{fmt_sym_price(an['symbol'], an['price'], market_row)}</code>",
        f"🕐 <b>وقت آخر شمعة سوق فعلية:</b> {human_dt(an.get('price_time') or '')}",
    ]
    if an.get("market_open") is False:
        price_block.append("🔴 <b>السوق مغلق حالياً</b> — التحليل على آخر أسعار إغلاق فعلية (لا تخمين).")

    bias = [
        f"{d_emoji} <b>الانحياز على إطار {esc(an['timeframe'])}:</b> <b>{dir_txt}</b>",
        f"⭐ <b>جودة السياق:</b> <code>{quality}/100</code>  {bar(quality, 100)}",
    ]

    rec = [
        f"🎯 <b>التوصية ({esc(an['timeframe'])}):</b> {rich(an.get('recommendation') or 'انتظار أفضل سياق')}",
    ]
    if an.get("activation"):
        rec.append(f"✅ <b>شرط تفعيل الفكرة:</b> {rich(an['activation'])}")
    if an.get("invalidation"):
        rec.append(f"❌ <b>إبطال الفكرة عند:</b> <code>{esc(an['invalidation'])}</code>")

    zones_txt = "\n".join(f"▎{rich(z)}" for z in (an.get("zones") or []))
    body_lines = (an.get("summary") or [])
    body_txt = "\n".join(f"▎{rich(x)}" for x in body_lines)

    out = [head,
           card("\n".join(price_block)),
           card("\n".join(bias))]
    if zones_txt:
        out.append(f"{ICON['diamond']} <b>المناطق والمستويات المهمة:</b>")
        out.append(card(zones_txt))
    if body_txt:
        out.append(f"{ICON['chart']} <b>قراءة السوق:</b>")
        out.append(card(body_txt))
    out.append(f"{ICON['fire']} <b>الخلاصة التنفيذية:</b>")
    out.append(card("\n".join(rec)))
    if an.get("risk_note"):
        out.append(f"🛡 <b>إدارة المخاطر:</b> <i>{esc(an['risk_note'])}</i>")
    out.append(footer(""))
    return "\n".join(out)


def render_signal_compact(sig) -> str:
    """ONE-LINE PLAIN-TEXT summary — used as TELEGRAM BUTTON text.

    Inline keyboard buttons render PLAIN TEXT ONLY: any <b>/<code> markup
    shows up as literal «رموز برمجية» on screen. No HTML here, ever.
    `seq` (when present) is the REAL sequential position in the signals
    center — after a deletion the following signals take the freed number
    (v6.8.0 «الترقيم الحقيقي»), while `id` stays the stable internal key.
    """
    d_emoji = DIRECTION_EMOJI.get(sig["direction"], "")
    status_map = {"ACTIVE": "🟢", "RUNNING": "🟢", "TP1_HIT": "🟢", "TP2_HIT": "🟢", "TP3_HIT": "🟢",
                  "SL_HIT": "🔴", "CANCELLED": "⚪", "EXPIRED": "⚪"}
    shown_no = sig.get("seq") or sig["id"]
    return (f"{status_map.get(sig['status'], '🔵')} #{shown_no} · {sig['symbol']} {d_emoji} "
            f"{tf_name(sig['timeframe'])} · {strategy_name(sig['strategy'])} · {sig['score']}/100 · {signal_status_ar(sig)}")


def render_payment_card(p, plan_title: str = "") -> str:
    status = status_badge(p["status"])
    body = [
        f"👤 المستخدم: <code>{p['user_id']}</code>",
        f"💎 الباقة: <b>{esc(plan_title or p['plan_code'])}</b>",
        f"💰 المبلغ: <b>{p['amount']:.2f} {esc(p.get('currency', 'USD'))}</b>",
        f"💳 وسيلة الدفع: <b>{esc(p.get('method') or '—')}</b>",
    ]
    if p.get("chain"):
        body.append(f"🌐 شبكة التحقق: <b>{esc(p['chain'])}</b>")
    if p.get("txid"):
        tx = str(p["txid"])
        body.append(f"🔗 TXID موثق: <code>{esc(tx[:14])}…{esc(tx[-8:])}</code>")
    body += [
        f"🧾 الإيصال: {'مرفق ✅' if p.get('receipt_file_id') else 'غير مرفق ⚠️'}",
        f"📌 الحالة: {status}",
    ]
    if p.get("rejection_reason"):
        body.append(f"🚫 سبب الرفض: <b>{esc(p['rejection_reason'])}</b>")
    body.append(f"🕐 أنشئ: {human_dt(p['created_at'])}")
    if p.get("reviewed_at"):
        body.append(f"✍️ المراجعة: {human_dt(p['reviewed_at'])}")
    return f"{ICON['wallet']} <b>طلب دفع #{p['id']}</b>\n" + card("\n".join(body))


def render_user_card(u: dict, profile: dict) -> str:
    ends = human_dt(profile["plan_ends"]) if profile.get("plan_ends") else "بدون انتهاء"
    q = profile["quota"]
    used = q["used"]
    quota = q["quota"] or 1
    identity = [
        f"👤 <b>{esc(u['full_name'])}</b>  @{esc(u['username'] or '—')}",
        f"🆔 المعرّف: <code>{u['user_id']}</code>",
        f"📅 التسجيل: {human_dt(u['joined_at'])}",
        f"🕐 آخر نشاط: {human_dt(u.get('last_seen'))}",
    ]
    membership = [
        f"💎 العضوية: <b>{esc(profile['plan_title'])}</b> ({esc(profile['plan_code'])})",
        f"⏳ الانتهاء: {ends}",
        f"📡 استهلاك اليوم: <b>{used}/{q['quota']}</b> {bar(used, quota)}",
        f"🧾 المدفوعات: <b>{profile['payments_count']}</b> (مقبول: <b>{profile['payments_total']:.2f}</b>)",
    ]
    state = [f"{'⛔ الحالة: محظور' if u['is_blocked'] else '🟢 الحالة: نشط'}",
             f"🔔 الإشعارات: <b>{'مفعلة' if u['notifications'] else 'متوقفة'}</b>"]
    out = [f"{ICON['users']} <b><u>بطاقة العضوية الملكية</u></b>",
           card("\n".join(identity)),
           card("\n".join(membership))]
    if profile.get("demo_balance") is not None:
        out.append(card(f"🧪 رصيد التجريبي: <b>{profile['demo_balance']:,.2f} USD</b>"))
    out.append(card("\n".join(state)))
    return "\n".join(out)


def diagnose_no_signal(status: str, reason: str = "") -> tuple[str, str, str, str]:
    """Translate raw engine status/reason into a luxury institutional protection
    diagnosis tuple: (icon, title, explanation, recommendation)."""
    st = (status or "").upper().strip()
    r = (reason or "").strip()
    rl = r.lower()

    if "htf" in rl or "الفريم الأعلى" in r or "عكس الاتجاه" in r:
        return (
            "🛡",
            "درع حماية الاتجاه المؤسسي — تم حجب صفقة عكس التيار",
            "الفريم الأعلى يحمل زخماً قوياً معاكساً للاتجاه المقترح؛ الدخول ضد التيار المؤسسي يرفع احتمالية ضرب الوقف، لذلك تدخّل درع الحماية ومنع الصفقة حفاظاً على رأس المال.",
            "فعّل <b>البحث التلقائي الملكي</b> بالأسفل ليقتنص المحرك لحظة اصطفاف السعر مع الاتجاه العام، أو جرّب فريم <code>H1 / H4</code> أو سوقاً آخر.",
        )
    if "متذبذب" in r or "عرضي" in r or "chop" in rl or "er=" in rl or "كوفمان" in r:
        return (
            "🌊",
            "درع حماية السيولة — السوق في نطاق عرضي متذبذب",
            "حركة السعر حالياً متقطعة بلا اتجاه مؤسسي واضح؛ التداول داخل مناطق التذبذب يسبّب إشارات كاذبة ويستنزف الرصيد.",
            "فعّل <b>البحث التلقائي الملكي</b> لالتقاط أول اختراق حقيقي وزخم مؤسسي مؤكد، أو افحص زوجاً آخر.",
        )
    if "زخم" in r or "momentum" in rl or "اندفاع" in r:
        return (
            "⚡",
            "درع حماية الزخم — السعر في موجة معاكسة حادة",
            "الشموع الأخيرة تُظهر اندفاعاً مضاداً قوياً ضد منطقة الدخول؛ الانتظار حتى هدوء الزخم وتأكيد الارتداد يحمي الصفقة.",
            "فعّل <b>البحث التلقائي</b> لمتابعة السعر لحظة بلحظة وإصدار الإشارة فور استقرار السلوك السعري.",
        )
    if st == "DUPLICATE" or "مفتوحة" in r or "تهدئة" in r or "cooldown" in rl or "مكرر" in r:
        return (
            "🔁",
            "حماية التمركز — توجد صفقة نشطة أو فترة تهدئة جارية",
            "المحرك يمنع تكديس الصفقات المتكررة على نفس الزوج خلال فترة قصيرة حفاظاً على انضباط إدارة رأس المال.",
            "تابع البطاقة الحية في <b>📡 الإشارات المباشرة</b> أو اختر زوجاً آخر للفحص الآن.",
        )
    if "جودة" in r or "score" in rl or "rr" in rl or "مخاطر" in r or "وقف" in r or "حد" in r or "kill" in rl:
        return (
            "⚖️",
            "درع الجودة والمخاطر — الإعداد دون المعيار المؤسسي",
            "المعطيات الحالية لم تبلغ الحد الصارم للجودة أو نسبة العائد إلى المخاطرة المعتمدة، والمنصة لا تُصدر إلا الفرص النخبوية.",
            "فعّل <b>البحث التلقائي الملكي</b> لانتظار فرصة بدرجة جودة أعلى، أو جرّب إطاراً زمنياً آخر.",
        )
    if (
        st == "ERROR"
        or "مغلق" in r
        or "سيولة" in r
        or "جلسة" in r
        or "بيانات" in r
        or "شموع" in r
        or "مزوّد" in r
        or "مزود" in r
        or "thin" in rl
        or "stale" in rl
    ):
        return (
            "🌙",
            "حالة الجلسة والسيولة — السوق غير مهيّأ للتنفيذ حالياً",
            "السوق مغلق أو يمرّ بساعة سيولة ضعيفة أو لم تتوفر شموع لحظية كافية للتحقق الدقيق؛ السلامة أولاً دائماً.",
            "أعد المحاولة خلال جلسة لندن أو نيويورك، أو فعّل <b>البحث التلقائي</b> وسيتولى المحرك المراقبة عنك.",
        )
    return (
        "👑",
        "صمت المحرك أمانة — لا يوجد نموذج مؤسسي مكتمل الآن",
        "تم فحص الشموع المغلقة بدقة عبر قواعد التحليل المؤسسي ولم تكتمل شروط الدخول بنسبة 100%؛ المنصة ترفض تصنيع إشارات وهمية.",
        "اختر فترة <b>البحث التلقائي الملكي</b> بالأسفل وسيراقب المحرك السوق عنك حتى اقتناص أول فرصة حقيقية.",
    )


def render_no_signal_card(
    symbol: str = "",
    timeframe: str = "",
    strategy_label: str = "",
    style_label: str = "",
    status: str = "NO_SETUP",
    reason: str = "",
    show_watch_hint: bool = True,
) -> str:
    """Luxury Royal Institutional Protection Card shown whenever a live scan
    or Signal-Now finds no qualifying setup (v6.20.9)."""
    icon, title, explanation, recommendation = diagnose_no_signal(status, reason)
    meta_parts = []
    if symbol:
        meta_parts.append(f"🌐 <b>السوق:</b> <code>{esc(symbol)}</code>")
    if timeframe:
        meta_parts.append(f"⏱ <b>الفريم:</b> <code>{esc(tf_name(timeframe))}</code>")
    meta_line_1 = " · ".join(meta_parts)

    meta_parts_2 = []
    if strategy_label:
        meta_parts_2.append(f"🧠 <b>المنهجية:</b> {esc(strategy_label)}")
    if style_label:
        meta_parts_2.append(f"🎯 <b>الأسلوب:</b> {esc(style_label)}")
    meta_line_2 = " · ".join(meta_parts_2)

    lines = [
        f"{icon} <b><u>تقرير الفحص المؤسسي — حماية رأس المال</u></b>",
        "━━━━━━━━━━━━━━━━━━",
        "▎👑 <b>قرار المحرك:</b> <b>لا توجد إشارة مؤهلة للدخول حالياً</b>",
    ]
    if meta_line_1:
        lines.append(f"▎{meta_line_1}")
    if meta_line_2:
        lines.append(f"▎{meta_line_2}")
    lines.append("━━━━━━━━━━━━━━━━━━")

    quote = [f"{icon} <b>{title}</b>", ""]
    if reason and reason.upper() not in ("NO_SETUP", "REJECTED"):
        quote.append(f"▎📌 <b>التشخيص الفني الدقيق:</b>\n<code>{esc(reason)}</code>")
        quote.append("")
    quote.append(f"▎💡 <b>لماذا لم تُصدر إشارة؟</b>\n{explanation}")
    if show_watch_hint:
        quote.append("")
        quote.append(f"▎🧭 <b>الخطوة الذكية:</b>\n{recommendation}")
        quote.append("▎⌛ <i>البحث التلقائي يعمل حتى 24 ساعة ويتوقف تلقائياً فور صدور الإشارة أو عند إلغائك له.</i>")

    lines.append("<blockquote>" + "\n".join(quote) + "</blockquote>")
    lines.append("<i>👑 في التداول المؤسسي، الامتناع عن صفقة غير مكتملة الشروط هو بحد ذاته صفقة رابحة.</i>")
    return "\n".join(lines)


def render_plan_card(p) -> str:
    body = [
        f"💰 السعر: <b>{p['price']:g} {esc(p['currency'])}</b>",
        f"📅 المدة: <b>{p['duration_days'] or 'دائم (مجاني)'}</b>",
        f"📡 حد الإشارات اليومي: <b>{p['max_signals_per_day']}</b>",
        f"🔬 حد التحليلات اليومي: <b>{p.get('max_analyses_per_day', 0) if isinstance(p, dict) else 0}</b>",
        f"⭐ الجودة الدنيا: <b>{p['min_score']}/100</b>",
        f"🌐 الأسواق: <code>{esc(p['markets'])}</code>",
        f"⏱ الأطر: <code>{esc(p['timeframes'])}</code>",
        f"🧠 الاستراتيجيات: <code>{esc(p['strategies'])}</code>",
        f"📌 الحالة: <b>{'🟢 مفعلة' if p['enabled'] else '⚪ معطلة'}</b>",
    ]
    return f"{ICON['diamond']} <b><u>{esc(p['title'])}</u></b> ({esc(p['code'])})\n" + card("\n".join(body))
