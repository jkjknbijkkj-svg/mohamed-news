# mohamed-news

نظام نشر آلي بالعربية لحساب إنستغرام @_mohamednews_.

- `config/` المصادر والجدول والكلمات المحظورة وروابط المشاريع.
- `newsbot/` الكود: جمع، اختيار، كتابة، فحص، رسم، نشر.
- `templates/` قوالب الصور.
- `data/` الحالة وسجل المنشورات (يحدّثها النظام).
- `media/` صور المنشورات المؤقتة (تُحذف بعد 14 يومًا).
- `docs/` صفحة الروابط (GitHub Pages).

التنوّع: المصادر موزّعة على عشرات الجهات حتى لا تهيمن جهة واحدة، وتُجمع خلاصات
Google الثلاث تحت علامة واحدة (`google`). ضابط التنوّع في `config/sources.json`
(كتلة `diversity`) يخفّض نقاط أي جهة نُشرت في آخر 48 ساعة (`window_hours`)
بمقدار `penalty`.

الأوامر:

    python -m newsbot run --slot auto            # ينشر الموعد المستحق
    python -m newsbot run --slot s0800 --dry-run # يرسم الصور دون نشر
    python -m newsbot check-feeds                # يفحص الخلاصات
    python -m newsbot maintenance                # الصيانة الأسبوعية
    python -m pytest -q                          # الاختبارات

لإيقاف النشر: Settings ← Actions ← Disable، أو أضف ملف `data/PAUSED`.
