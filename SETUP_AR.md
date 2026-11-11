# خطوات الإعداد اليدوية

1. أنشئ الملفات وافحصها: `python -m compileall newsbot` ثم `python -m pytest -q`.
2. افحص الخلاصات: `python -m newsbot check-feeds`.
3. حمّل خط Cairo إلى `assets/fonts/` (الأمر في رسالة المحادثة).
4. ضع شعارك (اختياري) باسم `assets/logo.png`.
5. جرّب الرسم محليًا: `python -m newsbot run --slot s0800 --dry-run`.
6. ارفع الملفات إلى GitHub (لا ترفع `.venv` ولا `out` ولا `__pycache__`).
7. أنشئ ملفي `.github/workflows/post.yml` و`maintenance.yml` من واجهة GitHub.
8. Settings ← Actions ← General ← Workflow permissions ← Read and write.
9. Settings ← Pages ← Deploy from a branch ← main ← /docs.
10. شغّل post يدويًا مع dry_run مفعّل، ثم maintenance، ثم شغّل حيًا.
11. ضع رابط الصفحة في سيرة الحساب.
12. لاستئناف النشر بعد إيقاف تلقائي: احذف الملف data/PAUSED.
