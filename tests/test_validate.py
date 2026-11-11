from newsbot.validate import normalize_news, numbers_in, validate_news

BANNED = {
    "rumor_phrases_ar": ["يُشاع", "مصادر مجهولة"],
    "advice_phrases_ar": ["توصية شراء"],
    "spam_phrases_ar": ["اضغط هنا"],
    "source_blocklist": [],
}
SOURCE = (
    "OpenAI announced a new model for developers building agents. "
    "The model supports multi-step tasks and is available through the API this week."
)


def good():
    return {
        "headline": "شركة OpenAI تطلق نموذجًا جديدًا لمساعدة المطورين على بناء الوكلاء الذكية",
        "summary": (
            "أعلنت OpenAI عن نموذج جديد موجه للمطورين الذين يبنون وكلاء ذكية. "
            "يقول النص إن النموذج يدعم المهام متعددة الخطوات. "
            "ويتوفر النموذج عبر واجهة البرمجة هذا الأسبوع."
        ),
        "hook": "إعلان جديد من OpenAI للمطورين.",
        "hashtags": ["الذكاء_الاصطناعي", "وكلاء_ذكية", "مطورون"],
        "alt_text": "صورة تحمل عنوان الخبر وشعار الحساب",
    }


def check(data):
    return validate_news(normalize_news(data), SOURCE, BANNED)


def test_valid_post_passes():
    assert check(good()) == []


def test_unknown_number_is_rejected():
    data = good()
    data["summary"] += " ونمت الإيرادات 40% خلال العام."
    assert any("numbers" in p for p in check(data))


def test_unknown_latin_name_is_rejected():
    data = good()
    data["headline"] = "شركة Google تطلق نموذجًا جديدًا لمساعدة المطورين على بناء الوكلاء الذكية"
    assert any("Latin" in p for p in check(data))


def test_rumor_phrase_is_rejected():
    data = good()
    data["summary"] += " ويُشاع أن النموذج سيتوسع."
    assert any("forbidden" in p for p in check(data))


def test_short_headline_is_rejected():
    data = good()
    data["headline"] = "OpenAI تطلق نموذجًا"
    assert any("headline" in p for p in check(data))


def test_url_is_rejected():
    data = good()
    data["hook"] = "التفاصيل على https://example.com الآن."
    assert any("URL" in p for p in check(data))


def test_numbers_in_handles_arabic_digits_and_commas():
    found = numbers_in("١٢٣ و 1,200 و 3.5")
    assert {"123", "1200", "3.5"} <= found
