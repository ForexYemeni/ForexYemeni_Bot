"""Comprehensive test suite for v7.1.0 Royal Independent Trading Academy Studio:
1. Intensive 15-lesson curriculum (5 FREE + 10 PAID) with 3-question quizzes & explanations
2. PostgreSQL-safe interactive multi-question quiz buttons (u:acadquiz:<id>:<q>:<opt>)
3. Payment methods parity with subscription plans (USDT Fast Lane + manual methods + copy/receipt)
4. Admin Academy Studio: inspect/preview lessons, custom course price/duration,
   add/edit/delete lessons with TEXT / VIDEO / AUDIO / VOICE / PDF / PHOTO media delivery
"""
from __future__ import annotations

import asyncio
from types import SimpleNamespace

import pytest

from fysb import config, db
from fysb.handlers import academy_growth
from fysb.services import academy, payments as payments_service


@pytest.fixture
def isolated_db(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "DB_FILE", tmp_path / "v7100.db")
    monkeypatch.setattr(config, "DATABASE_URL", "")
    monkeypatch.setattr(config, "ADMIN_ID", 999000)
    db.init_db()
    return tmp_path / "v7100.db"


class _FakeBot:
    def __init__(self):
        self.messages = []
        self.videos = []
        self.audios = []
        self.voices = []
        self.documents = []
        self.photos = []

    async def send_message(self, chat_id, text, reply_markup=None, parse_mode=None):
        self.messages.append({"chat_id": chat_id, "text": text, "reply_markup": reply_markup})

    async def send_video(self, chat_id, video, caption="", parse_mode=None):
        self.videos.append({"chat_id": chat_id, "file_id": video, "caption": caption})

    async def send_audio(self, chat_id, audio, caption="", parse_mode=None):
        self.audios.append({"chat_id": chat_id, "file_id": audio, "caption": caption})

    async def send_voice(self, chat_id, voice, caption="", parse_mode=None):
        self.voices.append({"chat_id": chat_id, "file_id": voice, "caption": caption})

    async def send_document(self, chat_id, document, caption="", parse_mode=None):
        self.documents.append({"chat_id": chat_id, "file_id": document, "caption": caption})

    async def send_photo(self, chat_id, photo, caption="", parse_mode=None):
        self.photos.append({"chat_id": chat_id, "photo": photo, "caption": caption})


class _FakeMessage:
    def __init__(self, uid=111, text="", video=None, audio=None, voice=None, document=None, photo=None, caption=""):
        self.chat_id = uid
        self.message_id = 55
        self.chat = SimpleNamespace(id=uid)
        self.text = text
        self.video = video
        self.audio = audio
        self.voice = voice
        self.document = document
        self.photo = photo
        self.caption = caption
        self.replied = []

    async def reply_text(self, text, reply_markup=None, parse_mode=None, disable_web_page_preview=True):
        self.replied.append({"text": text, "reply_markup": reply_markup})
        return self

    async def edit_text(self, text, reply_markup=None, parse_mode=None, disable_web_page_preview=True):
        self.replied.append({"text": text, "reply_markup": reply_markup})
        return self


class _FakeQuery:
    def __init__(self, uid=111):
        self.from_user = SimpleNamespace(id=uid)
        self.message = _FakeMessage(uid=uid)
        self.replied = self.message.replied

    async def answer(self, *a, **kw):
        return None


def _all_buttons(markup) -> list[tuple[str, str]]:
    if not markup:
        return []
    out = []
    for row in markup.inline_keyboard:
        for b in row:
            out.append((b.text, getattr(b, "callback_data", "") or ""))
    return out


def test_curriculum_is_deep_intensive_with_varied_3_question_quizzes(isolated_db):
    lessons = academy.list_lessons()
    assert len(lessons) == 15
    free = [l for l in lessons if l["track"] == "FREE"]
    paid = [l for l in lessons if l["track"] == "PAID"]
    assert len(free) == 5 and len(paid) == 10

    seen_ans = set()
    for les in lessons:
        assert len(les["content"]) >= 650, f"Lesson {les['code']} too short"
        assert len(les["quiz"]) == 3, f"Lesson {les['code']} must have 3 quiz questions"
        for qitem in les["quiz"]:
            assert len(qitem["opts"]) == 3
            assert qitem["ans"] in (0, 1, 2)
            assert qitem.get("exp"), f"Missing explanation in {les['code']}"
            seen_ans.add(qitem["ans"])
    # Varied correct answers (0, 1, and 2 all used — never always option 0!)
    assert seen_ans == {0, 1, 2}


def test_interactive_multi_question_quiz_buttons_and_completion(isolated_db):
    les = academy.get_lesson("F01_BASICS")
    assert les is not None
    uid = 444111
    bot = _FakeBot()
    ctx = SimpleNamespace(user_data={}, bot=bot)

    # Open lesson Q0 -> inspect quiz buttons
    q = _FakeQuery(uid=uid)
    asyncio.run(academy_growth.handle_user_growth(q, uid, f"acadles:{les['id']}", ctx, bot))
    btns = _all_buttons(q.replied[-1]["reply_markup"])
    quiz_btns = [(t, d) for t, d in btns if d.startswith("u:acadquiz:")]
    assert len(quiz_btns) == 3
    for _, cb in quiz_btns:
        assert len(cb.encode("utf-8")) < 64

    # Wrong answer on Q0 -> NOT completed
    wrong_opt = (les["quiz"][0]["ans"] + 1) % 3
    asyncio.run(academy_growth.handle_user_growth(q, uid, f"acadquiz:{les['id']}:0:{wrong_opt}", ctx, bot))
    assert "إجابة غير دقيقة" in q.replied[-1]["text"]
    assert les["code"] not in academy.user_progress(uid)["completed_codes"]

    # Correct Q0 -> moves to Q1 (still not completed)
    ans0 = les["quiz"][0]["ans"]
    asyncio.run(academy_growth.handle_user_growth(q, uid, f"acadquiz:{les['id']}:0:{ans0}", ctx, bot))
    assert "إجابة صحيحة" in q.replied[-1]["text"]
    assert f"u:acadles:{les['id']}:1" in [d for _, d in _all_buttons(q.replied[-1]["reply_markup"])]
    assert les["code"] not in academy.user_progress(uid)["completed_codes"]

    # Correct Q1 -> moves to Q2
    ans1 = les["quiz"][1]["ans"]
    asyncio.run(academy_growth.handle_user_growth(q, uid, f"acadquiz:{les['id']}:1:{ans1}", ctx, bot))
    assert f"u:acadles:{les['id']}:2" in [d for _, d in _all_buttons(q.replied[-1]["reply_markup"])]

    # Correct Q2 -> lesson completed 100%!
    ans2 = les["quiz"][2]["ans"]
    asyncio.run(academy_growth.handle_user_growth(q, uid, f"acadquiz:{les['id']}:2:{ans2}", ctx, bot))
    assert "إتمام الدرس 100%" in q.replied[-1]["text"]
    assert les["code"] in academy.user_progress(uid)["completed_codes"]


def test_academy_checkout_matches_plan_payment_methods(isolated_db):
    # Configure BOTH USDT wallets AND Yemeni manual payment methods
    payments_service.set_chain_wallet("TRC20", "TQn9Y2khEsLJW1ChVWFMSMeRDow5KcbLSE")
    payments_service.set_chain_wallet("BEP20", "0x111122223333444455556666777788889999aaaa")
    m1 = payments_service.add_method("محفظة جوالي", "أرسل المبلغ وارفق الإيصال", "777123456", country="اليمن")
    m2 = payments_service.add_method("محفظة واحد كاش", "تحويل فوري", "771999888", country="اليمن")

    uid = 555222
    bot = _FakeBot()
    ctx = SimpleNamespace(user_data={}, bot=bot)
    q = _FakeQuery(uid=uid)

    # Open Academy checkout
    asyncio.run(academy_growth.handle_user_growth(q, uid, "acadbuy", ctx, bot))
    btns = _all_buttons(q.replied[-1]["reply_markup"])
    cbs = [d for _, d in btns]
    assert "u:acadusdt:TRC20" in cbs
    assert "u:acadusdt:BEP20" in cbs
    assert f"u:acadpay:{m1}" in cbs
    assert f"u:acadpay:{m2}" in cbs
    assert "u:redeem" in cbs

    # Pick manual Yemeni wallet -> creates ACADEMY payment request & sets awaiting_receipt
    asyncio.run(academy_growth.handle_user_growth(q, uid, f"acadpay:{m1}", ctx, bot))
    pid = ctx.user_data.get("awaiting_receipt")
    assert pid is not None
    req = payments_service.get_request(pid)
    assert req["plan_code"] == "ACADEMY"
    assert req["amount"] == pytest.approx(academy.paid_price_usd())
    assert "777123456" in q.replied[-1]["text"]

    # Approving payment unlocks Paid Academy!
    appr = payments_service.approve_request(pid, actor=999000)
    assert appr["ok"] is True
    assert academy.has_paid_access(uid) is True


def test_admin_studio_view_custom_price_and_multimedia_lessons(isolated_db):
    admin_uid = config.ADMIN_ID
    bot = _FakeBot()
    ctx = SimpleNamespace(user_data={}, bot=bot)
    q = _FakeQuery(uid=admin_uid)

    # 1) Custom course price ($135)
    asyncio.run(academy_growth.handle_admin_growth(q, admin_uid, "acadprice_custom", "", "", ctx, bot))
    assert ctx.user_data.get("awaiting_acad_price") is True
    msg_price = _FakeMessage(uid=admin_uid, text="135")
    upd_price = SimpleNamespace(message=msg_price, effective_message=msg_price)
    asyncio.run(academy_growth.handle_growth_text_input(upd_price, ctx, admin_uid, "135"))
    assert academy.paid_price_usd() == pytest.approx(135.0)

    # 2) Admin inspects existing lesson (sees full content + marked quiz answers)
    f01 = academy.get_lesson("F01_BASICS")
    asyncio.run(academy_growth.handle_admin_growth(q, admin_uid, "acadles_view", str(f01["id"]), "", ctx, bot))
    assert "اختبار الدرس (عرض الإدارة)" in q.replied[-1]["text"]
    assert "✅" in q.replied[-1]["text"]

    # 3) Admin creates a brand-new VIDEO lesson via guided wizard + media upload + quiz
    asyncio.run(academy_growth.handle_admin_growth(q, admin_uid, "acadnew_tr", "FREE", "", ctx, bot))
    asyncio.run(academy_growth.handle_admin_growth(q, admin_uid, "acadnew_mt", "VIDEO", "", ctx, bot))

    m_title = _FakeMessage(uid=admin_uid, text="16. فيديو تطبيقي لقنص الذهب")
    asyncio.run(academy_growth.handle_growth_text_input(SimpleNamespace(message=m_title, effective_message=m_title), ctx, admin_uid, m_title.text))
    m_content = _FakeMessage(uid=admin_uid, text="شرح فيديو مباشر لصفقة بيع ذهب من منطقة سيولة.")
    asyncio.run(academy_growth.handle_growth_text_input(SimpleNamespace(message=m_content, effective_message=m_content), ctx, admin_uid, m_content.text))

    assert ctx.user_data["acad_new_draft"]["step"] == "media"
    m_vid = _FakeMessage(uid=admin_uid, video=SimpleNamespace(file_id="VID_FILE_XYZ_123", file_name="gold_sniper.mp4"), caption="فيديو الذهب")
    handled = asyncio.run(academy_growth.handle_growth_media_input(SimpleNamespace(effective_message=m_vid), ctx, admin_uid))
    assert handled is True

    m_quiz = _FakeMessage(uid=admin_uid, text="أين كان الدخول؟ | عند منطقة السيولة | عشوائي | قبل الإغلاق | 1 | الدخول دائماً من السيولة")
    asyncio.run(academy_growth.handle_growth_text_input(SimpleNamespace(message=m_quiz, effective_message=m_quiz), ctx, admin_uid, m_quiz.text))

    # Verify newly created video lesson exists and delivers video when a student opens it!
    free_lessons = academy.list_lessons("FREE")
    vid_les = [l for l in free_lessons if l["media_type"] == "VIDEO"]
    assert len(vid_les) == 1
    assert vid_les[0]["media_file_id"] == "VID_FILE_XYZ_123"

    student_uid = 888333
    q_stu = _FakeQuery(uid=student_uid)
    asyncio.run(academy_growth.handle_user_growth(q_stu, student_uid, f"acadles:{vid_les[0]['id']}", SimpleNamespace(user_data={}, bot=bot), bot))
    assert len(bot.videos) == 1
    assert bot.videos[0]["file_id"] == "VID_FILE_XYZ_123"

    # 4) Admin attaches a PDF & VOICE note to existing lessons & deletes custom lesson
    pdf_les = academy.create_lesson("كتاب PDF شامل", track="FREE", content="ملخص الكتاب", media_type="PDF", media_file_id="PDF_FID_99")
    voice_les = academy.create_lesson("توجيه صوتي يومي", track="FREE", content="ملخص الصوت", media_type="VOICE", media_file_id="VOICE_FID_77")
    asyncio.run(academy_growth.handle_user_growth(q_stu, student_uid, f"acadles:{pdf_les['id']}", SimpleNamespace(user_data={}, bot=bot), bot))
    asyncio.run(academy_growth.handle_user_growth(q_stu, student_uid, f"acadles:{voice_les['id']}", SimpleNamespace(user_data={}, bot=bot), bot))
    assert len(bot.documents) == 1 and bot.documents[0]["file_id"] == "PDF_FID_99"
    assert len(bot.voices) == 1 and bot.voices[0]["file_id"] == "VOICE_FID_77"

    assert academy.delete_lesson(pdf_les["id"]) is True
    assert academy.get_lesson_by_id(pdf_les["id"]) is None
