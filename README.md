# بوت أسهم XTB على Discord

بوت يراقب قائمة أسهم أمريكية (متاحة على XTB) ويرسل إلى قناة Discord:

| متى (بتوقيت نيويورك) | ماذا يرسل |
|---|---|
| قبل الافتتاح بـ 45 دقيقة | **نشرة اليوم**: قراءة السوق، الأحداث المهمة، أفضل فكرة، قائمة الشراء مع سعر الدخول والوقف والهدف وعدد الأسهم المقترح، قائمة البيع وجني الأرباح، قائمة "لا تشترِ"، ونصيحة لكل سهم في محفظتك |
| 09:30 | السوق فتح |
| 12:00 (اختياري) | أداء أفكار اليوم حتى الآن |
| 16:00 (13:00 في الأيام القصيرة) | **ملخص الإغلاق**: المؤشرات، أداء كل فكرة، الأفكار السابقة التي وصلت الهدف أو الوقف، أكبر التحركات، دروس اليوم، وما تراقبه غداً |
| عطلات وإجازات | رسالة "السوق مغلق اليوم" مع موعد الافتتاح القادم بتوقيتك |

كل الأوقات تظهر بتوقيتك (`USER_TZ`، الافتراضي مسقط) وبتوقيت نيويورك. التقويم يعرف عطل NYSE لعامي 2026 و2027 وأيام الإغلاق المبكر.

البيانات من Yahoo Finance مجاناً. التحليل بـ Claude. البوت لا يتصل بحساب XTB إطلاقاً، أنت تنفذ يدوياً.

## التثبيت على سيرفر Oracle Cloud (أمر واحد)

```bash
curl -fsSL https://raw.githubusercontent.com/binhamed33/xtb-stocks-bot/main/deploy/install.sh | sudo bash
sudo nano /opt/xtb-stocks-bot/.env        # ضع DISCORD_WEBHOOK_URL و ANTHROPIC_API_KEY
sudo systemctl restart xtb-stocks-bot
cd /opt/xtb-stocks-bot && sudo -u stocksbot .venv/bin/python -m stockbot test-discord
```

السكربت ينشئ مستخدماً معزولاً، بيئة Python، وخدمة systemd تعمل دائماً وتعود بعد إعادة التشغيل.

التحديث بعد أي تطوير جديد على GitHub:

```bash
sudo bash /opt/xtb-stocks-bot/deploy/update.sh
```

المتابعة:

```bash
sudo systemctl status xtb-stocks-bot
sudo journalctl -u xtb-stocks-bot -f
```

## التشغيل على جهازك (Windows) للتجربة

```bat
cd stocks-bot
py -m venv .venv && .venv\Scripts\activate
pip install -r requirements.txt
copy .env.example .env  &&  notepad .env
py -m stockbot calendar        # حالة السوق والموعد القادم بتوقيتك
py -m stockbot test-discord    # رسالة اختبار
py -m stockbot now             # نشرة فورية (استدعاء Claude واحد)
py -m stockbot run             # الجدولة الدائمة
```

## الإعدادات المهمة في `.env`

- `STOCK_UNIVERSE`: الأسهم المراقبة. الافتراضي 40 سهماً أمريكياً كبيراً. عدّلها بما يناسبك (حد أقصى 80).
- `HOLDINGS`: محفظتك بصيغة `AAPL:10@180.5,MSFT:5@400` لتصلك نصيحة احتفظ/بع لكل سهم.
- `PORTFOLIO_SIZE` و`RISK_PER_TRADE_PCT`: لحساب عدد الأسهم المقترح في كل فكرة.
- `PREMARKET_MINUTES_BEFORE_OPEN`، `SEND_MIDDAY_UPDATE`، `MAX_BUY_PICKS`...

## التكلفة

استدعاءان لـ Claude في اليوم (النشرة والملخص). مع `claude-opus-5` التكلفة تقريباً دولار أو أقل يومياً حسب حجم القائمة. لتقليلها استخدم `CLAUDE_EFFORT=medium` أو `ANTHROPIC_MODEL=claude-sonnet-5`.

## الاختبارات

```bash
python -m pytest -q
```

## تنبيه

هذه أبحاث آلية وليست نصيحة استثمارية. النماذج اللغوية لا تتنبأ بالسوق. استخدم الأفكار كنقطة بداية، ونفّذ فقط بحجم تتحمل خسارته.
