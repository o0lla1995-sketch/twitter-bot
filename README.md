# Twitter Video Auto-Publisher Bot

## النشر على Coolify

### 1. انسخ الريبو إلى GitHub
```bash
cd twitter-bot
git init
git add .
git commit -m "Twitter auto-publisher bot v1.0"
git remote add origin https://github.com/o0lla1995-sketch/twitter-bot.git
git push -u origin main
```

### 2. أنشئ خدمة جديدة على Coolify
- اذهب إلى Coolify → New Resource → Public Repository (أو Private)
- اختر الريبو `twitter-bot`
- Coolify سيكتشف Dockerfile تلقائياً

### 3. أضف متغيرات البيئة
في Coolify → Environment Variables:
```
BOT_TOKEN=8868679973:AAHmzHA-ytNA4ZREhQNuQxk3JhCcvQ_toYg
CHAT_ID=5446910738
EXTRACTOR_URL=http://okzr3huc0sxjmwk5ywcohdqp.130.61.171.201.sslip.io
PONO_URL=https://pono-w53e.onrender.com
STATE_FILE=/data/bot_state.json
TWITTER_AUTH_TOKEN=<your_twitter_auth_token>
TWITTER_CT0=<your_twitter_ct0>
SUPABASE_SERVICE_ROLE_KEY=<your_supabase_service_key>
NEXT_PUBLIC_SUPABASE_URL=https://supabase.pornxvideo.com
```

### 4. أضف Volume للحفاظ على الحالة
في Coolify → Volumes:
```
/data → persistent storage
```

### 5. انشر
اضغط Deploy — البوت سيبدأ ويرسل رسالة تأكيد على Telegram.

## أوامر Telegram

| الأمر | الوصف |
|---|---|
| `/start` أو `/help` | عرض المساعدة |
| `/addaccount @username` | إضافة حساب تويتر للمتابعة |
| `/listaccounts` | عرض الحسابات |
| `/removeaccount @username` | حذف حساب |
| `/run` | تشغيل الاستخراج الآن |
| `/setinterval 6` | ضبط الفاصل (ساعات، 0=إيقاف) |
| `/cooldown 5` | ضبط فترة التبريد (دقائق) |
| `/report` | تقرير الحالة |
| `/addtweet <URL>` | نشر تغريدة يدوياً |
| `/stats` | إحصائيات |

## كيف يعمل
1. البوت يفحص حسابات تويتر المضافة دورياً (كل N ساعات)
2. يبحث عن التغريدات الجديدة التي تحتوي على فيديو
3. يستخرج الفيديو عبر خادم GetTwittervideo
4. ينشره على الموقع عبر Supabase API
5. يرسل تقريراً بكل فيديو منشور على Telegram
6. يطبق فترة تبريد بين الحسابات لتفادي الحظر
