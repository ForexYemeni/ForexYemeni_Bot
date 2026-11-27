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
from fysb.theme import (DIRECTION_EMOJI, ICON, ar_reason as theme_ar_reason, bar, card, dir_badge, esc,
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

    # Prices card — each level with its directional signed pip distance from entry
    # (SL always negative -X.X pips, TP1/TP2/TP3 always positive +X.X pips).
    def level_line(icon, label, price):
        pips_d = instruments.signed_pips(symbol, direction, sig["entry"], price, row)
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
    # v6.25.0 (#4): Institutional Confluence Matrix badge when multi-strategy scores exist
    try:
        raw_scores = sig["strategy_scores"]
    except (KeyError, IndexError, TypeError):
        raw_scores = ""
    if raw_scores:
        try:
            from fysb.engines.intelligence import format_confluence_from_scores
            conf_line = format_confluence_from_scores(raw_scores, sig["strategy"])
            if conf_line:
                meta.append(conf_line)
        except Exception:
            pass

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
        out.append("<i>الحالة تفعيل سعري ونتيجة تتبع نموذجية؛ ليست إيصال تنفيذ أو رصيد وسيط.</i>")
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
    conf = an.get("confirmation") or {}
    if isinstance(conf, dict) and conf:
        if conf.get("type"):
            rec.append(f"🔐 <b>نوع التأكيد الحقيقي:</b> {rich(conf['type'])}")
        if conf.get("stability_tf_label"):
            rec.append(f"⏱ <b>إطار الثبات والتأكيد:</b> {rich(conf['stability_tf_label'])}")
        if conf.get("anti_fakeout"):
            rec.append(f"🛡 <b>ضمان عدم الكسر الكاذب:</b> {rich(conf['anti_fakeout'])}")
        if conf.get("live_state"):
            rec.append(f"📡 <b>حالة التفعيل اللحظية:</b> {rich(conf['live_state'])}")
    if an.get("invalidation"):
        inv_tf = (conf.get("invalidation_tf_note") if isinstance(conf, dict) else "") or ""
        inv_tail = f" <i>({esc(inv_tf)})</i>" if inv_tf else ""
        rec.append(f"❌ <b>إبطال الفكرة عند:</b> <code>{esc(an['invalidation'])}</code>{inv_tail}")

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


def signal_row_style(sig) -> str:
    """v7.5.0: Semantic Bot API 9.4 button color for signal list rows.
      * TP1_HIT / TP2_HIT / TP3_HIT -> Royal Emerald Green ('success')
      * RUNNING / ACTIVE            -> Royal Sapphire Blue ('primary')
      * SL_HIT / CANCELLED / EXPIRED -> Royal Ruby Red ('danger')
    """
    st = str((sig.get("status") if isinstance(sig, dict) else sig["status"]) or "").upper()
    if st in ("TP1_HIT", "TP2_HIT", "TP3_HIT"):
        return "success"
    if st in ("RUNNING", "ACTIVE"):
        return "primary"
    return "danger"


def signal_creator_label(sig, users_map: dict | None = None) -> str:
    """v7.5.0: Human-readable Arabic attribution of who created/generated the signal."""
    src = str((sig.get("source") if isinstance(sig, dict) else sig["source"]) or "").upper()
    try:
        cb = int((sig.get("created_by") if isinstance(sig, dict) else sig["created_by"]) or 0)
    except Exception:
        cb = 0
    u_info = (users_map or {}).get(cb) if cb else None
    if not u_info and cb > 0:
        try:
            from fysb import db as _db
            with _db.conn() as c:
                r = c.execute("SELECT user_id, username, full_name FROM users WHERE user_id=?", (cb,)).fetchone()
                if r:
                    u_info = dict(r)
        except Exception:
            u_info = None
    who = ""
    if u_info:
        nm = (u_info.get("full_name") or "").strip()
        un = (u_info.get("username") or "").strip().lstrip("@")
        if nm and un:
            who = f"{nm} (@{un})"
        elif un:
            who = f"@{un}"
        elif nm:
            who = nm
        else:
            who = f"ID {cb}"
    elif cb > 0:
        who = f"ID {cb}"

    if src in ("AUTO", "AUTO_FREE", "ENGINE", "SCANNER"):
        return "🤖 المحرك التلقائي (مجاني)" if src == "AUTO_FREE" else "🤖 المحرك التلقائي"
    if src in ("ADMIN_SIGNAL_NOW", "MANUAL", "ADMIN"):
        return f"👑 الإدارة{(' · ' + who) if who else ''}"
    if src in ("SIGNAL_NOW", "WATCHER", "USER"):
        return f"👤 مستخدم: {who or 'طلب مباشر'}"
    if src.startswith("PARTNER"):
        return f"🤝 شريك: {who or 'قناة شريكة'}"
    return f"👤 {who}" if who else "🤖 المحرك التلقائي"


def render_signal_compact(sig) -> str:
    """v7.5.0: Ultra-clean, mobile-safe ONE-LINE PLAIN-TEXT button label.

    Never overflows mobile button width, never injects HTML tags, and uses
    ▲/▼ arrows instead of 🔴 so Bot API 9.4 semantic colors (`signal_row_style`)
    paint wins Green, active/running Blue, and SL/closed Red cleanly.
    """
    direction = str(sig["direction"] or "BUY").upper()
    arrow = "▲" if direction == "BUY" else "▼"
    st = str(sig["status"] or "ACTIVE").upper()
    closed = is_signal_closed(sig)
    pips = float(sig.get("pips_result") or 0.0) if isinstance(sig, dict) else 0.0
    shown_no = (sig.get("seq") if isinstance(sig, dict) else None) or sig["id"]
    strat = strategy_name(sig["strategy"])
    if st == "RUNNING":
        st_short = "قيد التنفيذ"
    elif st == "ACTIVE":
        st_short = "بانتظار"
    elif st == "TP3_HIT":
        st_short = f"👑 {pips:+.0f}p" if pips else "👑 مكتملة"
    elif st in ("TP1_HIT", "TP2_HIT"):
        tp_lbl = "TP1" if st == "TP1_HIT" else "TP2"
        p_txt = f" {pips:+.0f}p" if pips else ""
        st_short = f"{tp_lbl}{p_txt}" + (" 🛡" if closed else " 🚀")
    elif st == "SL_HIT":
        st_short = f"وقف {pips:+.0f}p" if pips else "وقف"
    elif st == "CANCELLED":
        st_short = "ملغاة"
    elif st == "EXPIRED":
        st_short = "منتهية"
    else:
        st_short = signal_status_ar(sig)
    return f"#{shown_no} {arrow} {sig['symbol']} · {strat} · {st_short}"


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
    diagnosis tuple: (icon, title, explanation, recommendation).

    v8.14.7 — the royal full-truth card (owner decree): EVERY line is built
    from the ONE actual reason the engine returned. The static possibilities
    template («السوق مغلق أو يمرّ بساعة سيولة أو لم تتوفر شموع…») is executed —
    a card may never offer a false possibility beside the real cause, and a
    closed market must say CLOSED 100% with the real reopen anchor."""
    st = (status or "").upper().strip()
    r = theme_ar_reason((reason or "").strip())
    rl = r.lower()

    # ① the real closure — the session calendar itself said closed (the
    #    v8.14.7 royal reason carries «مغلق فعلاً 100%»; the legacy mapping
    #    carries «وفق تقويم الجلسات» — both are calendar statements)
    if "مغلق فعلاً 100%" in r or "وفق تقويم الجلسات" in r:
        return (
            "🔒",
            "السوق مغلق فعلاً 100% — وفق تقويم الجلسات الملكي",
            "هذا إغلاق حقيقي معلن في تقويم الجلسات — لا شيء يُصنَّع على سوق مغلق، والإشارات تعود فور الانفتاح بأول شمعة صالحة.",
            "أعد الفحص بعد عودة السوق، أو فعّل <b>البحث التلقائي</b> ليراقب نيابة عنك ضمن مدته المعلنة — لا إشارات على سوق مغلق أبداً.",
        )
    # ② the Friday pre-close provider-death window — BOTH truths named
    if "يقفل رسمياً بعد دقائق" in r or "التغذية توقفت" in r:
        return (
            "⏳",
            "نافذة ما قبل الإغلاق — التغذية توقفت والسوق يقفل رسمياً",
            "توقفت تغذية الأسعار خلال الساعة الأخيرة قبل الإغلاق الرسمي — لا إشارات تُصنَّع على تغذية ميتة، والسوق يقفل رسمياً بعد دقائق.",
            "لا فائدة من إعادة المحاولة الآن — العودة الأحد 18:00 بتوقيت نيويورك؛ فعّل <b>البحث التلقائي</b> ليستأنف المراقبة بعد العودة.",
        )
    # ③ the daily settlement pause (v8.14.5 royal breathing window)
    if "استراحة التسوية" in r:
        return (
            "🌙",
            "استراحة التسوية اليومية — السوق يلتفت أنفاسه",
            "استراحة تسوية يومية قصيرة ومعلنة سلفاً في تقويم الجلسات — لا إشارات تُصنَّع داخلها، والتدفق والإشارات يعودان فور انتهائها.",
            "أعد الفحص فور انتهاء الاستراحة، أو فعّل <b>البحث التلقائي</b> ليلتقط أول فرصة حقيقية بعد العودة.",
        )
    # ④ the royal 100% truth when NO examination ever ran (v8.14.8): the feed
    #    died, so by the no-repaint law the strategies — including the CHOSEN
    #    one — were never examined. Never dressed up as a strategy verdict.
    if "لم تُفحص" in r:
        return (
            "📡",
            "الفحص الاستراتيجي متوقف — التغذية لم تبلغ حد الصدق",
            "الرفض وقع قبل أي فحص مؤسسي: التغذية لم تبلغ حد الصدق، وقانون عدم إعادة الرسم يمنع أي حكم على شموع جامدة — المنهجيات المختارة لم تُفحص ولم يُصدر عنها حكم بعد.",
            "لا تُكرّر المحاولات اليدوية الآن — فعّل <b>البحث التلقائي</b>؛ فور عودة تغذية صالحة يبدأ الفحص تلقائياً ويعود بالسبب الحقيقي 100% من منهجيتك.",
        )
    # ⑤ honest data protection (stale/frozen/insufficient/chain/proxy) — the
    #    refusal is real, the reason verbatim, zero counters
    if any(a in r for a in ("أسعار غير حية", "متجمّد", "مجمّدة", "عينة ناقصة",
                            "تعذر جلب بيانات", "للعرض فقط",
                            "بيانات أقدم من الحد المسموح")):
        return (
            "🛡",
            "حماية البيانات الصادقة — لا إشارة على بيانات غير صالحة",
            "المحرك يرفض التوليد على بيانات لا تليق بالتنفيذ المؤسسي — سبب الرفض الفعلي مذكور حرفياً في التشخيص الفني أعلاه.",
            "أعد المحاولة بعد لحظات، أو فعّل <b>البحث التلقائي</b> ليراقب التغذية نيابة عنك.",
        )
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
    # ⑤ honest catch-alls — the reason itself is the truth, verbatim above
    if st == "REJECTED":
        return (
            "🛡",
            "رفض حماية صادق من المحرك",
            "المحرك رفض هذه الدورة لسبب فعلي محدد مذكور حرفياً في التشخيص الفني أعلاه — حماية رأس المال أولاً.",
            "أعد المحاولة لاحقاً، أو راجع التشخيص الفني أعلاه لمعرفة السبب بالتحديد.",
        )
    if st == "ERROR":
        return (
            "⚙️",
            "تعذر إتمام الدورة تقنياً — لا تخمين بلا بيانات",
            "حدث عطل فعلي منع إتمام الفحص — السبب الحقيقي مذكور في التشخيص الفني أعلاه، ولا تُصنَّع نتيجة بلا بيانات.",
            "أعد المحاولة بعد قليل؛ إن تكرر العطل راجع حالة التغذية من لوحة المالك.",
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
    icon, title, explanation, recommendation = diagnose_no_signal(status, theme_ar_reason(reason))
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
        quote.append(f"▎📌 <b>التشخيص الفني الدقيق:</b> {esc(theme_ar_reason(reason))}")
        quote.append("")
    quote.append(f"▎💡 <b>لماذا لم تُصدر إشارة؟</b>\n{explanation}")
    if show_watch_hint:
        quote.append("")
        quote.append(f"▎🧭 <b>الخطوة الذكية:</b>\n{recommendation}")
        quote.append("▎⌛ <i>البحث التلقائي يعمل حتى 24 ساعة ويتوقف تلقائياً فور صدور الإشارة أو عند إلغائك له.</i>")

    lines.append("<blockquote>" + "\n".join(quote) + "</blockquote>")
    lines.append("<i>👑 في التداول المؤسسي، الامتناع عن صفقة غير مكتملة الشروط هو بحد ذاته صفقة رابحة.</i>")
    return "\n".join(lines)


_PLAN_TIER_META = {
    "FREE": {
        "icon": "🆓",
        "tagline": "بوابة التجربة المجانية — تحقّق من دقة المحرك قبل الترقية",
        "perks": [],
    },
    "PRO": {
        "icon": "⚡",
        "tagline": "الاختيار الذكي للمتداول المنضبط — فرص يومية منتقاة بسعر اقتصادي",
        "perks": ["🛡 تتبع تلقائي كامل للأهداف (TP1 / TP2 / TP3) وتنبيهات الوقف"],
    },
    "VIP": {
        "icon": "👑",
        "tagline": "الأكثر طلباً 🔥 — مدارس الأموال الذكية (SMC · ICT · Wyckoff · SK)",
        "perks": [
            "🔐 دخول فوري تلقائي لقناة VIP الخاصة برابط حصري فور التفعيل",
            "👁 ميزة مراقبة الفرص التلقائية (Watchers) على أسواقك المختارة",
        ],
    },
    "ROYAL": {
        "icon": "🏆",
        "tagline": "القوة المؤسسية الكاملة — أقوى 4 استراتيجيات قنص سيولة حصرية",
        "perks": [
            "🎯 حصرياً: CRT · AMD · Silver Bullet · Turtle Soup (كل الـ 18 استراتيجية)",
            "🔐 دخول تلقائي لقناة VIP الخاصة + أولوية قصوى في الدعم الفني",
            "📊 وصول كامل لجميع أسواق المنصة والأطر الزمنية والتداول التجريبي الذكي",
        ],
    },
}


def plan_tier_icon(code: str) -> str:
    return _PLAN_TIER_META.get((code or "").upper(), {}).get("icon", "💎")


def render_plan_card(p, trial_info: dict | None = None) -> str:
    code = (p.get("code") or "").upper() if isinstance(p, dict) else (p["code"] or "").upper()
    meta = _PLAN_TIER_META.get(code, {"icon": "💎", "tagline": "", "perks": []})
    icon = meta["icon"]
    price = float(p["price"] or 0)
    price_txt = "مجاناً (0$)" if price <= 0 else f"{price:g} {esc(p['currency'])}"
    dur = int(p["duration_days"] or 0)
    if code == "FREE":
        dur_txt = f"فترة تجريبية {dur} يوم" if dur > 0 else "دائم (مجاني مفتوح)"
    else:
        dur_txt = f"{dur} يوم" if dur > 0 else "دائم"

    mkts = [x.strip() for x in str(p["markets"] or "").split(",") if x.strip()]
    tfs = [x.strip() for x in str(p["timeframes"] or "").split(",") if x.strip()]
    strats = [x.strip() for x in str(p["strategies"] or "").split(",") if x.strip()]
    anl_q = int(p.get("max_analyses_per_day", 0) if isinstance(p, dict) else 0)
    sig_q = int(p["max_signals_per_day"] or 0)

    core = [
        f"▎💰 <b>السعر:</b> <b>{price_txt}</b>",
        f"▎📅 <b>المدة:</b> <b>{dur_txt}</b>",
        f"▎📡 <b>حد الإشارات اليومي:</b> <b>{sig_q} إشارة / يوم</b>",
        f"▎🔬 <b>حد التحليلات اليومي:</b> <b>{anl_q} تحليل / يوم</b>",
        f"▎⭐ <b>الجودة الدنيا:</b> <b>{p['min_score']}/100</b>",
        f"▎📌 <b>الحالة:</b> <b>{'🟢 مفعلة' if p['enabled'] else '⚪ معطلة'}</b>",
    ]
    if trial_info and code == "FREE" and trial_info.get("is_free") and not trial_info.get("unlimited"):
        if trial_info.get("expired"):
            core.append("▎🔒 <b>حالتك الآن:</b> انتهت فترتك المجانية — اختر باقة مدفوعة للاستمرار")
        else:
            dl = trial_info.get("days_left") or 0
            hl = trial_info.get("hours_left") or 0
            rem = f"{dl} يوم" if dl >= 1 else f"{hl} ساعة"
            core.append(f"▎⏳ <b>المتبقي من تجربتك:</b> <b>{rem}</b>")

    mkt_preview = " · ".join(mkts[:8]) + (f" (+{len(mkts) - 8} أخرى)" if len(mkts) > 8 else "")
    scope = [
        f"▎🌐 <b>الأسواق ({len(mkts)}):</b> <code>{esc(mkt_preview or '—')}</code>",
        f"▎⏱ <b>الأطر ({len(tfs)}):</b> <code>{esc(' · '.join(tfs) or '—')}</code>",
        f"▎🧠 <b>الاستراتيجيات ({len(strats)}):</b> <code>{esc(' · '.join(strats) or '—')}</code>",
    ]
    for perk in meta["perks"]:
        scope.append(f"▎{perk}")

    head = f"{icon} <b><u>{esc(p['title'])}</u></b>  <code>[{esc(code)}]</code>"
    if meta["tagline"]:
        head += f"\n<i>{meta['tagline']}</i>"
    return (
        head + "\n━━━━━━━━━━━━━━━━━━\n"
        + card("\n".join(core)) + "\n"
        + card("\n".join(scope))
    )


def _sub_remaining_text(ends_at: str) -> str:
    """Human-readable Arabic remaining duration for an active subscription."""
    s = str(ends_at or "").strip()
    if not s:
        return "♾️ اشتراك دائم (بلا انتهاء)"
    try:
        from datetime import datetime, timezone
        dt = datetime.fromisoformat(s.replace("Z", "+00:00"))
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        delta = dt - datetime.now(timezone.utc)
        secs = int(delta.total_seconds())
        if secs <= 0:
            return "منتهي"
        days = secs // 86400
        hours = (secs % 86400) // 3600
        if days >= 1:
            return f"{days} يوم" + (f" و {hours} ساعة" if hours > 0 else "")
        return f"{max(1, hours)} ساعة"
    except Exception:
        return human_dt(s)


def render_active_subscribed_plan_card(
    p: dict,
    *,
    active_sub: dict | None = None,
    trial_info: dict | None = None,
    sig_used: int = 0,
    anl_used: int = 0,
) -> str:
    """Ultra-luxury Royal Membership Certificate when user opens their CURRENT active plan.

    Shows active membership status, dates, remaining time, live daily quotas,
    and unlocked perks — with ZERO payment prompts.
    """
    code = (p.get("code") or "").upper()
    meta = _PLAN_TIER_META.get(code, {"icon": "💎", "tagline": "", "perks": []})
    icon = meta["icon"]
    sig_q = int(p.get("max_signals_per_day") or 0)
    anl_q = int(p.get("max_analyses_per_day") or 0)
    mkts = [x.strip() for x in str(p.get("markets") or "").split(",") if x.strip()]
    tfs = [x.strip() for x in str(p.get("timeframes") or "").split(",") if x.strip()]
    strats = [x.strip() for x in str(p.get("strategies") or "").split(",") if x.strip()]

    status_lines = [
        "▎✅ <b>حالة العضوية:</b> <b>مشترك ومفعّل بالفعل الآن</b> 🟢",
        f"▎👑 <b>مستوى الباقة:</b> {icon} <b>{esc(p['title'])}</b> <code>[{esc(code)}]</code>",
    ]
    if code == "FREE" and trial_info:
        if trial_info.get("unlimited"):
            status_lines.append("▎⏳ <b>صلاحية الاشتراك:</b> <b>♾️ مفتوحة حالياً</b>")
        else:
            dl = trial_info.get("days_left") or 0
            hl = trial_info.get("hours_left") or 0
            rem = f"{dl} يوم" if dl >= 1 else f"{max(1, hl)} ساعة"
            status_lines.append(f"▎⏳ <b>المتبقي من الفترة التجريبية:</b> <b>{rem}</b>")
    elif active_sub:
        started = active_sub.get("started_at") or ""
        ends = active_sub.get("ends_at") or ""
        if started:
            status_lines.append(f"▎🗓 <b>تاريخ التفعيل:</b> {human_dt(started)}")
        if ends:
            status_lines.append(f"▎📅 <b>تاريخ الانتهاء:</b> {human_dt(ends)}")
        status_lines.append(f"▎⏳ <b>المدة المتبقية:</b> <b>{_sub_remaining_text(ends)}</b>")

    usage_lines = [
        f"▎📡 <b>إشاراتك اليوم:</b> <code>{sig_used}/{sig_q}</code>  {bar(sig_used, max(1, sig_q))}",
        f"▎🔬 <b>تحليلاتك اليوم:</b> <code>{anl_used}/{anl_q}</code>  {bar(anl_used, max(1, anl_q))}",
        f"▎⭐ <b>أدنى جودة إشارة مفعّلة:</b> <b>{p.get('min_score', 70)}/100</b>",
    ]

    mkt_preview = " · ".join(mkts[:8]) + (f" (+{len(mkts) - 8} أخرى)" if len(mkts) > 8 else "")
    scope = [
        f"▎🌐 <b>الأسواق المفتوحة لك ({len(mkts)}):</b> <code>{esc(mkt_preview or '—')}</code>",
        f"▎⏱ <b>الأطر الزمنية ({len(tfs)}):</b> <code>{esc(' · '.join(tfs) or '—')}</code>",
        f"▎🧠 <b>الاستراتيجيات المفتوحة ({len(strats)}):</b> <code>{esc(' · '.join(strats) or '—')}</code>",
    ]
    for perk in meta["perks"]:
        scope.append(f"▎{perk}")

    head = (
        f"👑 <b><u>بطاقة العضوية الملكية المفعّلة — {esc(p['title'])}</u></b>\n"
        f"<i>✨ أنت مشترك بالفعل في هذه الباقة وتتمتع بكامل صلاحياتها الآن</i>"
    )
    return (
        head + "\n━━━━━━━━━━━━━━━━━━\n"
        + card("\n".join(status_lines)) + "\n"
        + "<b>📊 استهلاكك وحصتك اليومية المفعّلة:</b>\n"
        + card("\n".join(usage_lines)) + "\n"
        + "<b>🔓 صلاحياتك ومزاياك المفتوحة:</b>\n"
        + card("\n".join(scope)) + "\n"
        + "💎 <i>لا تحتاج لأي دفع إضافي لباقتك الحالية — يمكنك الاستمتاع بالخدمات مباشرة أو الترقية لباقة أعلى في أي وقت.</i>"
    )


def render_plans_showcase_card(
    plans: list[dict],
    current_plan: dict,
    trial_info: dict | None = None,
    active_sub: dict | None = None,
) -> str:
    """Ultra-luxury overview of all enabled plans on `u:plans`."""
    cur_code = (current_plan.get("code") or "FREE").upper()
    cur_icon = plan_tier_icon(cur_code)
    trial_expired = bool(trial_info and trial_info.get("is_free") and trial_info.get("expired"))
    status_lines = [f"▎{cur_icon} باقتك الحالية: <b>{esc(current_plan['title'])}</b>" + ("" if trial_expired else "  <b>[✅ مفعّلة]</b>")]
    if active_sub and cur_code != "FREE":
        status_lines.append(f"▎⏳ المتبقي من اشتراكك: <b>{_sub_remaining_text(active_sub.get('ends_at') or '')}</b>")
    elif trial_info and trial_info.get("is_free"):
        if trial_info.get("unlimited"):
            status_lines.append("▎⏳ الفترة المجانية: <b>♾️ مفتوحة حالياً</b>")
        elif trial_info.get("expired"):
            status_lines.append("▎🔒 <b>انتهت فترتك المجانية التجريبية</b> — اختر باقة بالأسفل للاستمرار فوراً")
        else:
            dl = trial_info.get("days_left") or 0
            hl = trial_info.get("hours_left") or 0
            rem = f"{dl} يوم" if dl >= 1 else f"{hl} ساعة"
            status_lines.append(f"▎⏳ متبقي من تجربتك المجانية: <b>{rem}</b> (من أصل {trial_info.get('trial_days', 7)} يوم)")

    tier_lines = []
    for r in plans:
        code = (r["code"] or "").upper()
        ic = plan_tier_icon(code)
        price = float(r["price"] or 0)
        p_txt = "مجاناً" if price <= 0 else f"{price:g}$/شهر"
        sig_q = int(r["max_signals_per_day"] or 0)
        anl_q = int(r.get("max_analyses_per_day") or 0)
        n_st = len([x for x in str(r["strategies"] or "").split(",") if x.strip()])
        n_mk = len([x for x in str(r["markets"] or "").split(",") if x.strip()])
        is_mine = (code == cur_code) and not trial_expired
        if is_mine:
            tier_lines.append(
                f"▎👑 <b>{esc(code)}</b> <b>[✅ باقتك المفعّلة]</b> · <b>{p_txt}</b> — "
                f"<code>{sig_q}</code> إشارة · <code>{anl_q}</code> تحليل · "
                f"<code>{n_mk}</code> سوق · <code>{n_st}</code> استراتيجية"
            )
        else:
            badge = " 🔥" if code == "VIP" else (" ✨" if code == "ROYAL" else "")
            tier_lines.append(
                f"▎{ic} <b>{esc(code)}</b>{badge} · <b>{p_txt}</b> — "
                f"<code>{sig_q}</code> إشارة · <code>{anl_q}</code> تحليل · "
                f"<code>{n_mk}</code> سوق · <code>{n_st}</code> استراتيجية"
            )

    return (
        "💎 <b><u>باقات الاشتراك الملكية — FOREXYEMENI</u></b>\n"
        "━━━━━━━━━━━━━━━━━━\n"
        + card("\n".join(status_lines)) + "\n"
        + "<b>مقارنة الباقات المتاحة:</b>\n"
        + card("\n".join(tier_lines)) + "\n"
        + "👑 <i>باقتك المفعّلة مميّزة باللون الأخضر الملكي (اضغطها لعرض بطاقة عضويتك)، واضغط أي باقة أخرى للترقية والتفعيل الفوري:</i>"
    )


def render_luxury_upgrade_card(
    why: str,
    current_plan: dict,
    paid_plans: list[dict],
    recommended_plan: dict | None = None,
    trial_expired: bool = False,
    quota_exhausted: bool = False,
) -> str:
    """Ultra-luxury persuasion card when a user hits a plan gate, daily quota limit, or trial expiry."""
    cur_sig = int(current_plan.get("max_signals_per_day") or 0)
    cur_anl = int(current_plan.get("max_analyses_per_day") or 0)
    cur_strats = len([x for x in str(current_plan.get("strategies") or "").split(",") if x.strip()])
    cur_mkts = len([x for x in str(current_plan.get("markets") or "").split(",") if x.strip()])

    if trial_expired:
        head = "⏳ <b><u>انتهت فترتك المجانية — حان وقت الترقية الملكية!</u></b>"
        lead = (
            f"▎🔒 {esc(why)}\n"
            "▎✨ لقد جرّبت دقة بياناتنا الحقيقية وبطاقاتنا الفاخرة — انتقل الآن للباقات المدفوعة\n"
            "▎   لفتح الإشارات اليومية، التحليلات الفورية، والمدارس المؤسسية بلا توقف."
        )
    elif quota_exhausted:
        head = "🚀 <b><u>وصلت للحد اليومي لباقتك — ارتقِ للخطة التالية الآن!</u></b>"
        lead = (
            f"▎⛔ <b>{esc(why)}</b>\n"
            f"▎💎 باقتك الحالية: <b>{esc(current_plan['title'])}</b> "
            f"(<code>{cur_sig}</code> إشارة · <code>{cur_anl}</code> تحليل/يوم)"
        )
    else:
        head = f"{ICON['crown']} <b><u>ميزة ملكية — ارتقِ بمستوى تداولك الآن</u></b>"
        lead = (
            f"▎🔒 <b>السبب:</b> {esc(why)}\n"
            f"▎💎 باقتك الحالية: <b>{esc(current_plan['title'])}</b>"
        )

    jump_block = ""
    if recommended_plan:
        rec_code = (recommended_plan["code"] or "").upper()
        rec_ic = plan_tier_icon(rec_code)
        rec_sig = int(recommended_plan.get("max_signals_per_day") or 0)
        rec_anl = int(recommended_plan.get("max_analyses_per_day") or 0)
        rec_strats = len([x for x in str(recommended_plan.get("strategies") or "").split(",") if x.strip()])
        rec_mkts = len([x for x in str(recommended_plan.get("markets") or "").split(",") if x.strip()])
        rec_perk = (
            "\n▎🔐 + <b>دخول فوري تلقائي لقناة VIP الخاصة</b>"
            if rec_code in ("VIP", "ROYAL") else ""
        )
        rec_elite = (
            "\n▎🎯 + <b>حصرياً: CRT · AMD · Silver Bullet · Turtle Soup</b>"
            if rec_code == "ROYAL" else ""
        )
        jump_lines = [
            f"▎🌟 <b>خطتك التالية المقترحة:</b> {rec_ic} <b>{esc(recommended_plan['title'])}</b> — "
            f"<b>{recommended_plan['price']:g} {esc(recommended_plan['currency'])}</b> / {int(recommended_plan.get('duration_days') or 30)} يوم",
            f"▎📈 الإشارات: <code>{cur_sig}</code> ⬅️ <b>{rec_sig} يومياً</b> · "
            f"التحليلات: <code>{cur_anl}</code> ⬅️ <b>{rec_anl} يومياً</b>",
            f"▎🌍 الأسواق: <code>{cur_mkts}</code> ⬅️ <b>{rec_mkts} سوق</b> · "
            f"الاستراتيجيات: <code>{cur_strats}</code> ⬅️ <b>{rec_strats} استراتيجية</b>"
            + rec_perk + rec_elite,
        ]
        jump_block = "\n✨ <b>ماذا تفتح فوراً عند الترقية؟</b>\n" + card("\n".join(jump_lines))

    cur_price = float(current_plan.get("price") or 0.0)
    shown_plans = [p for p in paid_plans if float(p.get("price") or 0) > cur_price] or paid_plans
    comp = []
    for p in shown_plans:
        code = (p["code"] or "").upper()
        ic = plan_tier_icon(code)
        n_st = len([x for x in str(p["strategies"] or "").split(",") if x.strip()])
        n_mk = len([x for x in str(p["markets"] or "").split(",") if x.strip()])
        extra = " · 🔐 VIP" if code in ("VIP", "ROYAL") else ""
        crown = " 🔥 الأكثر طلباً" if code == "VIP" else (" 🏆 الكاملة" if code == "ROYAL" else "")
        comp.append(
            f"▎{ic} <b>{esc(code)}</b> ({p['price']:g}$){crown}\n"
            f"▎   <code>{p['max_signals_per_day']}</code> إشارة · <code>{p.get('max_analyses_per_day', 0)}</code> تحليل · "
            f"<code>{n_mk}</code> سوق · <code>{n_st}</code> استراتيجية{extra}"
        )

    return (
        head + "\n━━━━━━━━━━━━━━━━━━\n"
        + card(lead)
        + jump_block + "\n"
        + "<b>الخطط المتاحة للترقية الفورية:</b>\n"
        + card("\n".join(comp)) + "\n"
        + "⚡ <i>تفعيل لحظي تلقائي عبر USDT (TRC20 / BEP20) أو التحويل المحلي — اضغط خطتك بالأسفل:</i>"
    )

