# دليل تشغيل سيرفر Odoo + موديول WhatsApp (odoo_whatsapp_api)

الدليل ده مكتوب عشان أي حد يقدر يشغّل السيرفر، يوقفه، يعمل Upgrade للموديول،
ويحل أشهر المشاكل - من غير ما يحتاج يفهم الكود أو يسأل حد.

---

## 1. معلومات السيرفر الأساسية

| البند | القيمة |
|---|---|
| نظام التشغيل | Windows |
| مكان تثبيت Odoo | `E:\odoo\server` |
| Python المستخدم | `E:\odoo\python\python.exe` |
| ملف تشغيل السيرفر | `E:\odoo\server\odoo-bin` |
| ملف الإعدادات (config) | `E:\odoo\server\odoo.conf` |
| قاعدة البيانات | PostgreSQL - اسمها `odoo19` على `localhost:5432` |
| اسم الجهاز (Host) | `DESKTOP-P35EV0O` |
| البورت | `8069` (الرابط: `http://localhost:8069` أو `http://<اسم الجهاز>:8069`) |
| إصدار Odoo | 19.0 |

### مسارات الإضافات (addons_path) في `odoo.conf`
```
E:\odoo\server\odoo\addons
C:\Users\Dell\AppData\Local\OpenERP S.A.\Odoo\addons\19.0
E:\odoo\server\custom_addons     <-- كل الموديولات الخاصة بينا هنا
E:\odoo\server\addons
```

> **ملحوظة:** لو ظهر تحذير `invalid addons directory 'e:\odoo\server\addons'` في اللوج،
> ده تحذير بس (المسار مش موجود فعليًا)، مش خطأ بيوقف السيرفر - ممكن تتجاهله أو تشيله من
> `addons_path` في `odoo.conf` لو مضايقك.

### الموديولات الخاصة الموجودة في `custom_addons`
- `odoo_whatsapp_api` - تكامل الواتساب (الموضوع الأساسي في الدليل ده)
- `falcon_admin`
- `dvs_van_sales`
- `field_dispatch_dashboard`
- `smart_clinic`
- وموديولات تانية حسب المشروع

---

## 2. تشغيل وإيقاف السيرفر

### تشغيل السيرفر (طريقة عادية)
افتح **Command Prompt** أو **PowerShell** واكتب:
```
E:\odoo\python\python.exe E:\odoo\server\odoo-bin -c E:\odoo\server\odoo.conf
```
لو كل حاجة تمام، هتشوف في آخر اللوج سطر شبه ده:
```
odoo.service.server: HTTP service (werkzeug) running on DESKTOP-P35EV0O:8069
```
وبعدها تقدر تفتح المتصفح على:
```
http://localhost:8069
```

### إيقاف السيرفر
في نفس الـ terminal اللي شغال فيه السيرفر، اضغط:
```
Ctrl + C
```
لو السيرفر شغال كـ Windows Service أو في الخلفية (مفيش terminal ظاهر)، افتح
**Task Manager** ودوّر على process اسمه `python.exe` (اللي بياخد بورت 8069) واقفله من هناك،
أو استخدم في PowerShell:
```powershell
Get-Process python | Where-Object {$_.Path -like "*E:\odoo*"} | Stop-Process
```

### إعادة تشغيل السيرفر بعد أي تعديل في الكود
لازم توقف السيرفر (`Ctrl+C`) وتشغّله تاني بنفس الأمر اللي فوق - Odoo Python
مبيعملش hot-reload للكود تلقائي.

---

## 3. تحديث موديول WhatsApp بعد أي تعديل في الكود

### الخطوات
1. **وقّف السيرفر** الأول (Ctrl+C).
2. استبدل فولدر `E:\odoo\server\custom_addons\odoo_whatsapp_api` بالنسخة الجديدة
   (احذف القديم وحط الجديد مكانه، أو انسخ الملفات المعدّلة فوق القديمة).
3. **شغّل السيرفر تاني** بنفس أمر التشغيل، لكن زوّد عليه `-u odoo_whatsapp_api`
   عشان يعمل Upgrade للموديول تلقائي من غير ما تدوس زرار من الواجهة:
   ```
   E:\odoo\python\python.exe E:\odoo\server\odoo-bin -c E:\odoo\server\odoo.conf -u odoo_whatsapp_api
   ```
4. لو مفيش أخطاء في اللوج، سيبه شغال عادي (من غير `-u`) في المرات الجاية.

### أو من الواجهة (لو السيرفر شغال بالفعل)
1. `http://localhost:8069/odoo`
2. **Apps** (من غير ما تكتب أي حاجة في مربع البحث، لازم تشيل فلتر "Apps" وتدور
   بالاسم لو الموديول مش ظاهر - أو فعّل **Developer Mode** الأول من
   Settings > General Settings > تحت خانة "Activate the developer mode").
3. دوّر على **WhatsApp API** (أو `odoo_whatsapp_api`).
4. دوس على التلات نقط ⋮ فوق الكارت بتاعه، اختار **Upgrade**.

---

## 4. تفعيل Developer Mode (وضع المطوّر)
محتاجه عشان تقدر تشوف تفاصيل تقنية (زي اللوج التفصيلي، أو موديولات مش ظاهرة عادي):
```
http://localhost:8069/web?debug=1
```
أو من Settings > General Settings > انزل تحت لحد "Developer Tools" ودوس
**Activate the developer mode**.

---

## 5. إعدادات حساب الواتساب (Evolution API)

الموديول بيتكلم مع سيرفر **Evolution API** (مش Meta WhatsApp Cloud API الرسمي).
الإعدادات دي بتتحط من داخل أودو مش في أي ملف كونفچ:

1. روح لـ **WhatsApp > Configuration > Accounts**.
2. افتح حساب الشركة، وهتلاقي فيه:
   - **Server URL**: رابط سيرفر Evolution API بتاعك (مثلاً `https://evo.example.com`).
   - **Instance Name**: اسم الـ instance اللي متسجل عليه رقم الواتساب.
   - **API Key**: مفتاح الدخول بتاع Evolution.
   - **Default Country Code**: كود الدولة الافتراضي (مثلاً `20` لمصر) بيستخدمه
     السيستم لو رقم جاله من غير كود دولة.
3. **Callback URL / Webhook URL**: اللي المفروض يتسجل في Evolution Manager نفسه
   (مش في أودو) على:
   ```
   http://<رابط السيرفر بتاعك>/api/v1/whatsapp/webhook
   ```
   ده اللي Evolution بيبعت عليه كل الأحداث (رسايل جديدة، تحديث حالة الاتصال...).

### شاشة الشات المباشر (Live Chat)
`WhatsApp` (القائمة الرئيسية) > بتفتح شاشة شات كاملة زي واتساب ويب، فيها:
- المحادثات (Conversations) - مقسّمة تابات: الكل / غير مقروءة / جروبات / مفضّلة.
- إرسال نص وميديا.
- تفاعل بإيموجي (React) على أي رسالة.
- Forward لرسالة لرقم/محادثة تانية.
- حذف رسالة "عندي بس" (Delete for me).
- استيراد تاريخ محادثة قديمة (Sync History).

---

## 6. أشهر المشاكل وحلولها

### ❌ `psycopg2.pool.PoolError: The Connection Pool Is Full`
**السبب:** Evolution API بيدخل في حلقة إعادة اتصال (reconnect loop) وبيبعت
مئات أحداث `connection-update` في الثانية، وكل واحدة فيهم كانت (قبل الإصلاح)
بتفتح اتصال جديد بقاعدة البيانات.
**الحل:** اتصلح في كود الموديول (فلترة الأحداث دي قبل ما توصل للداتابيز +
دمج التكرارات في نفس الثانية). لو المشكلة رجعت تاني:
1. تأكد إن `odoo.conf` فيه:
   ```
   db_maxconn = 128
   workers = 4
   ```
2. تأكد إن جهاز/رقم الواتساب متصل بشكل مستقر (مش بيفصل ويرجع يحاول يتصل
   باستمرار) - افتح شاشة الشات (WhatsApp Live Chat) وشوف حالة الاتصال فوق.

### ❌ التوست الأحمر "Odoo Server Error" بيظهر باستمرار وانت فاتح شات
عادي لو حصل مرة واحدة بسبب اتصال بطيء لحظي - الشاشة بقت بتحاول تاني تلقائي
من غير ما تضايقك بتوست متكرر (اتصلح في الكود). لو استمر:
1. افتح **Developer Console** في المتصفح (F12) > تبويب **Console**.
2. دوّر على سطور تبدأ بـ `WhatsApp fullview:` - هتقولك السبب الحقيقي.
3. لو السبب اتصال بالداتابيز، شوف الحل اللي فوق (Connection Pool).

### ❌ محادثات الجروبات مش ظاهرة في تاب "Groups"
**السبب الأغلب:** إعداد **Ignore Groups** مفعّل في إعدادات الـ instance
جوه Evolution Manager نفسه (مش في أودو) - وده بيمنع Evolution من إنه يبعت
أي حدث لرسايل الجروبات من الأساس.
**الحل:**
1. ادخل على Evolution Manager (لوحة تحكم سيرفر Evolution API نفسه).
2. روح لإعدادات الـ Instance بتاعك، دوّر على **Ignore Groups / Groups Ignore**،
   وتأكد إنه **Off**.
3. ابعت رسالة تجربة في أي جروب، وشوف لوج أودو وقتها - لو ظهر سطر فيه
   `messages.upsert` و `@g.us`، يبقى اتحل والجروب هيبان في الشاشة.

### ❌ زرار الـ React (التفاعل بإيموجي) مش شغال في الجروبات
اتصلح في الكود (كان بيبعت الرقم بصيغة غلط للجروبات). لو لسه مش شغال بعد
تحديث الموديول، تأكد إنك عملت **Upgrade** فعلي للموديول (خطوة 3 فوق) مش
مجرد إعادة تشغيل عادي.

### ❌ السيرفر مش عايز يفتح / بورت 8069 مشغول
معناها فيه نسخة تانية من Odoo شغالة بالفعل. من PowerShell:
```powershell
netstat -ano | findstr :8069
```
هيديك رقم الـ PID، وبعدين:
```powershell
taskkill /PID <الرقم> /F
```

### ❌ رسالة "Missing `author` key in manifest" أو تحذيرات WARNING تانية في اللوج
دي تحذيرات (Warnings) بس مش أخطاء - السيرفر بيشتغل عادي معاها، ممكن تتجاهل.

---

## 7. هيكل ملفات موديول odoo_whatsapp_api (للمرجعية السريعة)

```
odoo_whatsapp_api/
├── __init__.py
├── __manifest__.py
├── controllers/
│   ├── webhook.py                     <- الموديلات: wa.message, wa.webhook.messages, ...
│   ├── whatsapp_webhook_controller.py <- استقبال الأحداث من Evolution (الـ webhook endpoint)
│   ├── whatsapp_fullview_controller.py<- الـ API بتاع شاشة الشات المباشر
│   ├── wa_message_fullview.py         <- منطق شاشة الشات (المحادثات/الجروبات/التفاعلات)
│   └── website_widget.py              <- ويدجت شات الموقع (لو مفعّل)
├── models/
│   ├── whatsapp_account.py            <- إعدادات الحساب (Server URL / API Key / ...)
│   ├── wa_message_template.py
│   ├── wa_qr_code.py
│   ├── Wa_account_connect_wizard.py
│   ├── whatsapp_account_fullview.py
│   ├── wa_conversation_favorite.py
│   ├── mail_template.py
│   └── res_config_settings.py
├── static/src/
│   ├── js/whatsapp_full_view.js       <- الفرونت إند بتاع شاشة الشات الكاملة
│   ├── xml/whatsapp_full_view.xml
│   └── scss/whatsapp_full_view.scss
├── views/                             <- شاشات الإعدادات والتقارير جوه أودو
├── wizards/
├── data/
└── security/ir.model.access.csv       <- صلاحيات الوصول للموديلات
```

> **ملحوظة مهمة:** فيه ملف قديم متروك بنفس الاسم `wa_message_fullview.py`
> جوه فولدر `models/` كمان - **ده مش شغال خالص** (مش متسجل في
> `models/__init__.py`)، والنسخة الشغالة فعليًا هي اللي جوه `controllers/`.
> ماتلمسش/تعدّلش في نسخة `models/` دي غلط، تعديلاتك مش هتشتغل.

---

## 8. تشيك سريع إن كل حاجة شغالة (Health Check)

بعد أي تشغيل/تحديث، اتأكد من الآتي بالترتيب:

1. **السيرفر شغال؟** افتح `http://localhost:8069` وشوف صفحة تسجيل الدخول بتظهر.
2. **الموديول متثبت ومحدّث؟** Apps > دوّر على WhatsApp API > تأكد إن فيه زرار
   "Upgrade" مش "Install" (يعني هو متثبت بالفعل).
3. **الاتصال بـ Evolution شغال؟** افتح شاشة WhatsApp Live Chat > لازم تشوف
   المحادثات بتحمّل من غير ما تفضل عالقة على شاشة QR Code.
4. **اللوج نضيف؟** افتح ملف اللوج (أو شوف الـ terminal) وتأكد مفيش
   `PoolError` أو `ERROR` متكررة بشكل غير طبيعي.

---

## 9. جهات اتصال / مراجع سريعة

| الحاجة | الرابط/المكان |
|---|---|
| واجهة أودو | `http://localhost:8069/odoo` |
| Developer Mode | `http://localhost:8069/web?debug=1` |
| شاشة الشات المباشر | من القائمة الرئيسية > WhatsApp |
| Webhook endpoint (للتسجيل في Evolution) | `http://<سيرفرك>/api/v1/whatsapp/webhook` |
| ملف الإعدادات | `E:\odoo\server\odoo.conf` |
| فولدر الموديولات الخاصة | `E:\odoo\server\custom_addons\` |