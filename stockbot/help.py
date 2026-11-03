"""/help content: an about card plus sections rendered as embed dicts (shared by Discord and the CLI)."""

from __future__ import annotations

from typing import Any

from .notify import BLUE, GREEN, GREY, ORANGE, PURPLE

AUTHOR = "© Abdulrhman AL-Riyami"

SECTIONS: list[tuple[str, str]] = [
    ("about", "ℹ️ عن البوت"), ("commands", "⌨️ الأوامر"), ("sharia", "🕌 الشريعة"),
    ("xtb", "🏦 XTB"), ("portfolio", "💼 المحفظة والتنبيهات"), ("faq", "❓ أسئلة شائعة"),
]


def embed(section: str) -> dict[str, Any]:
    if section == "about":
        return {
            "title": "📊 بوت أبحاث الأسهم الحلال (XTB)",
            "description": ("مساعد أبحاث للمستثمر الذي يشتري **أسهماً حقيقية** عبر XTB ويلتزم بالأسهم **المتوافقة مع الشريعة**.\n"
                            "يجمع البيانات الحقيقية من Yahoo Finance وSEC EDGAR وFinnhub وFMP، ويحللها، ويشرحها بالعربية عبر Google Gemini."),
            "color": GREEN,
            "fields": [
                {"name": "ما يفعله", "value": ("• يحسم هل السهم حلال بدليل: المنهجية والنسب والمصدر وتاريخ الفحص\n"
                                               "• يمنع CFD: ما لم يُؤكد أنه سهم حقيقي في XTB لا يُعرض كفرصة\n"
                                               "• تحليل فني وأساسي ومخاطر وأخبار بأمر واحد\n"
                                               "• يشرح لماذا انخفض السهم وهل الانخفاض فرصة أم تحذير\n"
                                               "• يمسح السوق بحثاً عن فرص ويقيّمها من 100\n"
                                               "• يتابع محفظتك ويرسل تنبيهات تلقائية\n"
                                               "• يذكّر بفتح السوق وإغلاقه بتوقيتك"), "inline": False},
                {"name": "ما لا يفعله", "value": "• لا ينفذ أي صفقة\n• لا يعد بربح مضمون ولا يقول \"بلا مخاطر\"\n• لا يختلق أرقاماً أو أخباراً: كل معلومة لها مصدر ووقت", "inline": False},
                {"name": "ابدأ بـ", "value": "`/sharia AAPL` ثم `/analyze AAPL` ثم `/report AAPL`", "inline": False},
            ],
            "footer": {"text": f"استخدم الأزرار أدناه للتنقل · {AUTHOR}"},
        }
    if section == "commands":
        return {
            "title": "⌨️ الأوامر",
            "color": BLUE,
            "fields": [
                {"name": "تحليل", "value": ("`/analyze SYMBOL` تحليل كامل مع البوابات والخلاصة\n"
                                            "`/report SYMBOL` تقرير استثماري مفصل من 18 قسماً\n"
                                            "`/stock SYMBOL` السعر والمعلومات الأساسية\n"
                                            "`/compare A B` مقارنة شركتين\n"
                                            "`/news SYMBOL` الأخبار وإفصاحات SEC مع الروابط"), "inline": False},
                {"name": "فرص وسوق", "value": ("`/scan [min_drop] [limit]` مسح القائمة بحثاً عن أسهم هبطت وأساسياتها سليمة (يأخذ دقائق)\n"
                                               "`/opportunities` نتائج آخر مسح\n"
                                               "`/market` المؤشرات وحالة الجلسة والافتتاح القادم بتوقيتك"), "inline": False},
                {"name": "شريعة وXTB", "value": ("`/sharia SYMBOL [method] [rescreen]` الفحص الشرعي بالمنهجية التي تختارها\n"
                                                  "`/xtb status SYMBOL` هل السهم مؤكد كسهم حقيقي في XTB"), "inline": False},
                {"name": "خاص بك", "value": ("`/portfolio show|add|remove` محفظتك\n"
                                             "`/watchlist show|add|remove` قائمة المتابعة\n"
                                             "`/alerts show|add|remove` تنبيهاتك\n"
                                             "`/ask QUESTION [SYMBOL]` سؤال حر يجيب عنه Gemini من البيانات الحقيقية"), "inline": False},
                {"name": "للمشرفين", "value": "`/xtb verify|cfd|unavailable|import` تحديث سجل XTB · `/status` حالة المصادر والاستهلاك", "inline": False},
            ],
            "footer": {"text": f"الرمز بصيغة Yahoo: AAPL, MSFT, ASML, 2222.SR, SAP.DE · {AUTHOR}"},
        }
    if section == "sharia":
        return {
            "title": "🕌 كيف يُفحص السهم شرعياً",
            "color": PURPLE,
            "fields": [
                {"name": "1. فحص النشاط", "value": "يُستبعد: البنوك والتأمين والتمويل الربوي، الكحول، التبغ، القمار، الخنزير، المحتوى الإباحي، الترفيه والإعلام التقليدي، الأسلحة، القنب، منصات العملات الرقمية. الصناعات المختلطة (مطاعم، فنادق، ألعاب) تُعلَّم \"يحتاج مراجعة\".", "inline": False},
                {"name": "2. الفحص المالي (منهجية واحدة لا تُخلط)", "value": ("**AAOIFI** (الافتراضي): الدين الربوي < 30% والنقد الربوي < 30% من القيمة السوقية، الدخل غير الجائز < 5%\n"
                                                                              "**MSCI**: 33.33% من إجمالي الأصول للدين والنقد والذمم، الدخل < 5%\n"
                                                                              "**FTSE**: 33.33% دين ونقد، 50% ذمم + نقد، الدخل < 5%"), "inline": False},
                {"name": "3. الحكم", "value": "✅ **HALAL** اجتاز كل الفحوص ببيانات كاملة\n⛔ **NON_COMPLIANT** فشل في فحص\n⚠️ **QUESTIONABLE** النسب سليمة لكن بند يحتاج مراجعة\n❓ **UNVERIFIED** البيانات غير كافية للحكم", "inline": False},
                {"name": "مهم", "value": "لا يُعطى HALAL بلا دليل. نسبة الدخل غير الجائز لا تُقاس غالباً من البيانات المجانية فيُنزَّل الحكم إلى \"يحتاج مراجعة\" ويُذكر ذلك. للتأكد النهائي استخدم فاحصاً معتمداً مثل Zoya أو Musaffa. يُعاد الفحص تلقائياً كل 30 يوماً ويُحفظ السجل.", "inline": False},
            ],
            "footer": {"text": f"جرّب: /sharia AAPL method: MSCI · {AUTHOR}"},
        }
    if section == "xtb":
        return {
            "title": "🏦 لماذا يظهر \"غير مؤكد في XTB\"؟",
            "color": ORANGE,
            "fields": [
                {"name": "السبب", "value": "XTB أوقفت واجهتها البرمجية في مارس 2025 ولا تنشر قائمة أدواتها آلياً. البوت لا يستطيع أن يعرف وحده هل الاسم متاح عندهم كـ **سهم حقيقي** أم كـ **CFD** فقط، ولا يدّعي ذلك.", "inline": False},
                {"name": "القاعدة", "value": "أي سهم غير مؤكد لا يُعرض كفرصة استثمارية مهما كان تقييمه، حتى لو كان حلالاً. هذه هي الحماية من CFD.", "inline": False},
                {"name": "كيف يُؤكد (المشرف)", "value": ("1. افتح xStation وابحث عن الرمز وتأكد أن نوع الأداة **Stock** وليس CFD\n"
                                                        "2. `/xtb verify SYMBOL` مع السوق والعملة\n"
                                                        "3. أو `/xtb import` بملف CSV: `symbol,type,market,currency`\n"
                                                        "4. `/xtb cfd SYMBOL` لتسجيل أن الاسم CFD فقط فيُستبعد نهائياً"), "inline": False},
                {"name": "للأعضاء", "value": "`/xtb status SYMBOL` يعرض حالة أي سهم. إن كان غير مؤكد اطلب من المشرف تأكيده.", "inline": False},
            ],
            "footer": {"text": AUTHOR},
        }
    if section == "portfolio":
        return {
            "title": "💼 المحفظة وقائمة المتابعة والتنبيهات",
            "color": BLUE,
            "fields": [
                {"name": "المحفظة (خاصة بك ولا يراها غيرك)", "value": ("`/portfolio add symbol: AAPL qty: 10 avg_price: 180`\n"
                                                                     "`/portfolio show` القيمة، الربح/الخسارة، التوزيع القطاعي، المخاطرة، الحكم الشرعي لكل مركز\n"
                                                                     "`/portfolio remove symbol: AAPL`"), "inline": False},
                {"name": "قائمة المتابعة", "value": "`/watchlist add symbol: MSFT note: انتظر هبوط` ثم `/watchlist show`. كل سهم في المحفظة أو المتابعة يُراقب تلقائياً.", "inline": False},
                {"name": "التنبيهات التلقائية", "value": "هبوط يومي ≥ 5%، خبر جوهري، تغيّر الحكم الشرعي، نتائج خلال 3 أيام، انعكاس الاتجاه. تصل برسالة خاصة من البوت، فاسمح بالرسائل الخاصة من أعضاء السيرفر.", "inline": False},
                {"name": "تنبيهات تضيفها", "value": ("`/alerts add symbol: AAPL kind: target threshold: 200` وصول سعر\n"
                                                    "`kind: below threshold: 150` كسر سعر · `kind: drop threshold: 3` هبوط %\n"
                                                    "`kind: news` · `kind: sharia` · `kind: earnings` · `kind: trend`\n"
                                                    "`/alerts show` · `/alerts remove alert_id: 3`"), "inline": False},
            ],
            "footer": {"text": f"لا تنفيذ تلقائي لأي صفقة · {AUTHOR}"},
        }
    return {
        "title": "❓ أسئلة شائعة",
        "color": GREY,
        "fields": [
            {"name": "الأمر يأخذ وقتاً؟", "value": "أول طلب لأي سهم يجلب سنتين من الأسعار والقوائم والأخبار (10 إلى 60 ثانية). الطلبات التالية أسرع لأن البيانات تُحفظ مؤقتاً.", "inline": False},
            {"name": "هل التوصية أمر شراء؟", "value": "لا. هي بحث آلي مبني على بيانات متأخرة ~15 دقيقة. القرار لك، ولا يوجد ربح مضمون.", "inline": False},
            {"name": "ما معنى التقييم من 100؟", "value": "خمسة محاور لكل منها 20: الصحة المالية، الفني، النمو، التقييم، الأخبار. البوابتان (الشريعة وسهم حقيقي) تسبقان الرقم: إن فشلتا فالتصنيف تجنّب أو غير مؤكد مهما كان الرقم.", "inline": False},
            {"name": "لماذا \"يحتاج مراجعة\" وليس حلال؟", "value": "النسب سليمة لكن بنداً لم يُتحقق منه من البيانات المجانية، غالباً نسبة الدخل غير الجائز أو نشاط مختلط. راجع فاحصاً معتمداً.", "inline": False},
            {"name": "ما هو Gemini هنا؟", "value": "يشرح ويلخص فقط من البيانات التي جمعها البوت. ممنوع عليه إضافة أرقام أو أخبار من عنده، وكل الأرقام في البطاقات محسوبة برمجياً لا بالذكاء الاصطناعي.", "inline": False},
            {"name": "الأسواق؟", "value": "أي رمز تدعمه Yahoo Finance. الجدولة والافتتاح/الإغلاق حالياً للسوق الأمريكي.", "inline": False},
        ],
        "footer": {"text": AUTHOR},
    }


def all_embeds() -> list[dict[str, Any]]:
    return [embed(k) for k, _ in SECTIONS]
