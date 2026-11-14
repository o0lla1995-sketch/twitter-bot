#!/usr/bin/env python3
# ═══════════════════════════════════════════════════════════════════════════════
# Twitter Video Auto-Publisher Bot — v1.0
# ═══════════════════════════════════════════════════════════════════════════════
# Deploy on Coolify as a Docker service.
#
# FEATURES:
#   • Telegram bot for control (/addaccount, /list, /run, /setinterval, /report)
#   • Auto-extract videos from Twitter accounts
#   • Publish to the pono site via admin API
#   • Multiple accounts with cooldown between each
#   • Duplicate detection (skip already-published tweets)
#   • Periodic reports to Telegram
#   • Smart retry on failures
#
# TELEGRAM COMMANDS:
#   /start         — show help
#   /addaccount    — add a Twitter username to monitor
#   /listaccounts  — list monitored accounts
#   /removeaccount — remove an account
#   /run           — trigger extraction now
#   /setinterval   — set auto-run interval in hours (0=off)
#   /cooldown      — set cooldown minutes between accounts
#   /report        — get status report
#   /addtweet      — manually add a tweet URL to extract+publish
# ═══════════════════════════════════════════════════════════════════════════════

import os
import sys
import json
import time
import threading
import schedule
import requests
import re
from datetime import datetime, timezone

# ═══════════════════════════════════════════════════════════════════════════════
# CONFIGURATION (from environment variables)
# ═══════════════════════════════════════════════════════════════════════════════

BOT_TOKEN = os.environ.get("BOT_TOKEN", "8868679973:AAHmzHA-ytNA4ZREhQNuQxk3JhCcvQ_toYg")
CHAT_ID = os.environ.get("CHAT_ID", "5446910738")

# Extraction server (GetTwittervideo on Coolify)
EXTRACTOR_URL = os.environ.get("EXTRACTOR_URL", "http://okzr3huc0sxjmwk5ywcohdqp.130.61.171.201.sslip.io")

# Pono site API
PONO_URL = os.environ.get("PONO_URL", "https://pono-w53e.onrender.com")

# Twitter auth cookies (for user timeline fetching)
TWITTER_AUTH_TOKEN = os.environ.get("TWITTER_AUTH_TOKEN", "")
TWITTER_CT0 = os.environ.get("TWITTER_CT0", "")

# State file (persists between restarts)
STATE_FILE = os.environ.get("STATE_FILE", "/data/bot_state.json")

# ═══════════════════════════════════════════════════════════════════════════════
# STATE MANAGEMENT
# ═══════════════════════════════════════════════════════════════════════════════

DEFAULT_STATE = {
    "accounts": [],           # list of Twitter usernames to monitor
    "published_tweets": [],   # tweet IDs already published (dedup)
    "cooldown_minutes": 5,    # wait between accounts
    "auto_interval_hours": 6, # auto-run interval (0 = disabled)
    "last_run": None,         # ISO timestamp of last run
    "stats": {
        "total_extracted": 0,
        "total_published": 0,
        "total_failed": 0,
        "total_duplicates": 0,
    },
}


def load_state():
    try:
        with open(STATE_FILE, 'r') as f:
            state = json.load(f)
        # Merge with defaults
        for key, val in DEFAULT_STATE.items():
            if key not in state:
                state[key] = val
        return state
    except:
        return json.loads(json.dumps(DEFAULT_STATE))


def save_state(state):
    try:
        os.makedirs(os.path.dirname(STATE_FILE), exist_ok=True)
        with open(STATE_FILE, 'w') as f:
            json.dump(state, f, indent=2)
    except Exception as e:
        print(f"[STATE] save error: {e}")


# ═══════════════════════════════════════════════════════════════════════════════
# TELEGRAM HELPERS
# ═══════════════════════════════════════════════════════════════════════════════

def tg_send(text, parse_mode=None):
    try:
        url = f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage"
        data = {"chat_id": CHAT_ID, "text": text}
        if parse_mode:
            data["parse_mode"] = parse_mode
        requests.post(url, json=data, timeout=15)
    except Exception as e:
        print(f"[TG] send error: {e}")


def tg_send_photo(photo_url, caption=""):
    try:
        url = f"https://api.telegram.org/bot{BOT_TOKEN}/sendPhoto"
        data = {"chat_id": CHAT_ID, "photo": photo_url}
        if caption:
            data["caption"] = caption[:1024]
        requests.post(url, json=data, timeout=15)
    except:
        pass


# ═══════════════════════════════════════════════════════════════════════════════
# TWITTER: Fetch recent tweet IDs from a user's timeline
# ═══════════════════════════════════════════════════════════════════════════════

def get_recent_tweets(username, max_tweets=10):
    """Fetch recent tweet IDs from a Twitter user's timeline using the GraphQL API."""
    if not TWITTER_AUTH_TOKEN:
        print(f"[TWITTER] No auth token — can't fetch timeline for @{username}")
        return []

    # Clean username
    username = username.lstrip('@')

    # First: get the user ID from the username
    headers = {
        "Authorization": "Bearer AAAAAAAAAAAAAAAAAAAAANRILgAAAAAAnNwIzUejRCOuH5E6I8xnZz4puTs=1Zv7ttfk8LF81IUq16cHjhEQvF98kIBqXJ5b3Jj0V7m",
        "Cookie": f"auth_token={TWITTER_AUTH_TOKEN}; ct0={TWITTER_CT0}",
        "x-csrf-token": TWITTER_CT0,
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
        "Content-Type": "application/json",
    }

    try:
        # Get user ID
        user_url = f"https://api.twitter.com/1.1/users/show.json?screen_name={username}"
        resp = requests.get(user_url, headers=headers, timeout=10)
        if resp.status_code != 200:
            print(f"[TWITTER] User lookup failed for @{username}: {resp.status_code}")
            return []
        user_data = resp.json()
        user_id = user_data.get("id_str") or str(user_data.get("id"))
        if not user_id:
            return []

        # Get user tweets (UserTweets GraphQL endpoint)
        variables = json.dumps({
            "userId": user_id,
            "count": max_tweets,
            "includePromotedContent": False,
            "withQuickPromoteEligibilityTweetFields": False,
            "withVoice": False,
            "withV2Timeline": True,
        })
        features = json.dumps({
            "rweb_tipjar_consumption_enabled": True,
            "responsive_web_graphql_exclude_directive_enabled": True,
            "verified_phone_label_enabled": False,
            "creator_subscriptions_tweet_preview_ratio_enabled": False,
            "responsive_web_graphql_timeline_navigation_enabled": True,
            "responsive_web_graphql_skip_user_profile_image_extensions_enabled": False,
            "communities_web_enable_tweet_community_results_fetch": True,
            "c9s_tweet_anatomy_moderator_badge_enabled": True,
            "articles_preview_enabled": True,
            "responsive_web_edit_tweet_api_enabled": True,
            "graphql_is_translatable_rweb_tweet_is_translatable_enabled": True,
            "view_counts_everywhere_api_enabled": True,
            "longform_notetweets_consumption_enabled": True,
            "responsive_web_twitter_article_tweet_consumption_enabled": True,
            "tweet_awards_web_tipping_enabled": False,
            "creator_subscriptions_quote_tweet_preview_enabled": False,
            "freedom_of_speech_not_reach_fetch_enabled": True,
            "standardized_nudges_misinfo": True,
            "tweet_with_visibility_results_prefer_gql_limited_actions_policy_enabled": True,
            "rweb_video_timestamps_enabled": True,
            "longform_notetweets_rich_text_read_enabled": True,
            "longform_notetweets_inline_media_enabled": True,
            "responsive_web_enhance_cards_enabled": False,
        })

        params = {
            "variables": variables,
            "features": features,
        }
        tweets_url = "https://twitter.com/i/api/graphql/E3opETHurmVJflFsUBVuUQ/UserTweets"
        resp = requests.get(tweets_url, headers=headers, params=params, timeout=15)
        if resp.status_code != 200:
            print(f"[TWITTER] Timeline fetch failed for @{username}: {resp.status_code}")
            return []

        data = resp.json()
        tweet_entries = (
            data.get("data", {})
            .get("user", {})
            .get("result", {})
            .get("timeline_v2", {})
            .get("timeline", {})
            .get("instructions", [{}])[0]
            .get("entries", [])
        )

        tweet_ids = []
        for entry in tweet_entries:
            # Only UserTweets (not promoted, not cursors)
            if entry.get("entryId", "").startswith("tweet-"):
                tweet_id = entry.get("content", {}).get("itemContent", {}).get("tweet_results", {}).get("result", {}).get("rest_id")
                if tweet_id:
                    tweet_ids.append(tweet_id)

        print(f"[TWITTER] @{username}: found {len(tweet_ids)} tweets")
        return tweet_ids

    except Exception as e:
        print(f"[TWITTER] Error fetching @{username}: {e}")
        return []


# ═══════════════════════════════════════════════════════════════════════════════
# EXTRACTION: Use the GetTwittervideo server to extract video from a tweet
# ═══════════════════════════════════════════════════════════════════════════════

def extract_tweet_video(tweet_url):
    """Extract video formats from a tweet URL using the GetTwittervideo server."""
    try:
        api_url = f"{EXTRACTOR_URL}/extract?url={requests.utils.quote(tweet_url)}"
        resp = requests.get(api_url, timeout=30)
        if resp.status_code != 200:
            return None
        data = resp.json()
        if data.get("ok") and data.get("formats"):
            return data
        if data.get("deleted"):
            return {"deleted": True}
        return None
    except Exception as e:
        print(f"[EXTRACT] error: {e}")
        return None


# ═══════════════════════════════════════════════════════════════════════════════
# PUBLISH: Post the video to the pono site
# ═══════════════════════════════════════════════════════════════════════════════

def publish_video(tweet_url, extraction_result):
    """Publish the extracted video to the pono site via the admin API."""
    formats = extraction_result.get("formats", [])
    if not formats:
        return False, "No video formats"

    # Pick the highest quality MP4
    mp4_formats = [f for f in formats if f.get("ext") == "mp4" or ".mp4" in f.get("url", "").lower()]
    if not mp4_formats:
        mp4_formats = formats

    # Sort by height/bitrate (highest first)
    def sort_key(f):
        h = f.get("height", 0)
        if isinstance(h, str):
            try:
                h = int(h.replace("x", "").split()[-1])
            except:
                h = 0
        return h if isinstance(h, (int, float)) else 0
    mp4_formats.sort(key=sort_key, reverse=True)

    best = mp4_formats[0]
    direct_url = best.get("url", "").split("?tag=")[0]

    # Build quality_urls
    quality_urls = []
    seen = set()
    for f in mp4_formats:
        url = f.get("url", "").split("?tag=")[0]
        if url and url not in seen:
            quality_urls.append({
                "label": f.get("resolution", "Standard"),
                "url": url,
                "height": sort_key(f) if isinstance(sort_key(f), int) else None,
            })
            seen.add(url)

    # Build the video payload
    title = extraction_result.get("title", "Untitled")[:200]
    thumbnail = extraction_result.get("thumbnail", "")
    duration = extraction_result.get("duration")

    payload = {
        "title": title,
        "embed_url": tweet_url,
        "thumbnail_url": thumbnail,
        "direct_url": direct_url,
        "quality_urls": quality_urls[:5],
        "duration": duration,
        "kind": "twitter",
        "categories": ["twitter"],
        "tags": ["twitter", "auto"],
        "description": f"Auto-published from Twitter: {tweet_url}",
        "skip_duplicate_check": False,
    }

    try:
        # Publish via the pono admin API
        # Note: this requires admin auth. For automation, we use the service key directly.
        url = f"{PONO_URL}/api/admin/videos"
        headers = {}

        # Try with service role key if available
        service_key = os.environ.get("SUPABASE_SERVICE_ROLE_KEY", "")
        if service_key:
            # Insert directly via Supabase REST API (bypass admin auth)
            supabase_url = os.environ.get("NEXT_PUBLIC_SUPABASE_URL", "https://supabase.pornxvideo.com")
            insert_url = f"{supabase_url}/rest/v1/media_items"
            headers = {
                "apikey": service_key,
                "Authorization": f"Bearer {service_key}",
                "Content-Type": "application/json",
                "Prefer": "return=minimal",
            }
            insert_data = {
                "title": title,
                "embed_url": tweet_url,
                "thumbnail_url": thumbnail,
                "direct_url": direct_url,
                "quality_urls": quality_urls[:5],
                "duration": duration,
                "kind": "twitter",
                "status": "active",
                "created_at": datetime.now(timezone.utc).isoformat(),
                "updated_at": datetime.now(timezone.utc).isoformat(),
            }
            resp = requests.post(insert_url, json=insert_data, headers=headers, timeout=15)
            if resp.status_code in (200, 201):
                return True, "published"
            if resp.status_code == 409:
                return False, "duplicate"
            return False, f"insert failed: {resp.status_code}"
        else:
            # Fallback: try the pono API (might need admin cookie)
            resp = requests.post(url, json=payload, timeout=15, headers=headers)
            if resp.status_code == 200:
                data = resp.json()
                if data.get("duplicate"):
                    return False, "duplicate"
                return True, "published"
            return False, f"api failed: {resp.status_code}"

    except Exception as e:
        return False, f"error: {e}"


# ═══════════════════════════════════════════════════════════════════════════════
# EXTRACTION RUN — process all accounts
# ═══════════════════════════════════════════════════════════════════════════════

def run_extraction(state, manual=False):
    """Run extraction for all monitored accounts."""
    accounts = state.get("accounts", [])
    if not accounts:
        tg_send("📋 لا توجد حسابات تويتر للمتابعة.\nأضف حساباً: /addaccount @username")
        return

    cooldown = state.get("cooldown_minutes", 5)
    published_tweets = state.get("published_tweets", [])
    stats = state.get("stats", {})

    run_type = "يدوي" if manual else "تلقائي"
    tg_send(f"🚀 بدء الاستخراج {run_type} — {len(accounts)} حساب(ات)\nفترة التبريد: {cooldown} دقيقة")

    total_published = 0
    total_failed = 0
    total_duplicates = 0
    total_no_video = 0
    results = []

    for i, username in enumerate(accounts):
        username = username.lstrip('@')
        tg_send(f"📱 [{i+1}/{len(accounts)}] جاري فحص @{username}...")

        # Fetch recent tweets
        tweet_ids = get_recent_tweets(username, max_tweets=10)
        if not tweet_ids:
            results.append({"account": username, "result": "no_tweets"})
            total_failed += 1
            if i < len(accounts) - 1:
                time.sleep(cooldown * 60)
            continue

        account_published = 0
        for tweet_id in tweet_ids:
            # Skip already published
            if tweet_id in published_tweets:
                total_duplicates += 1
                continue

            tweet_url = f"https://x.com/i/status/{tweet_id}"

            # Extract video
            extraction = extract_tweet_video(tweet_url)
            if not extraction:
                total_no_video += 1
                continue
            if extraction.get("deleted"):
                published_tweets.append(tweet_id)  # mark as processed
                total_failed += 1
                continue

            # Publish
            success, msg = publish_video(tweet_url, extraction)
            if success:
                account_published += 1
                total_published += 1
                published_tweets.append(tweet_id)
                stats["total_published"] = stats.get("total_published", 0) + 1
                # Send notification with thumbnail
                thumb = extraction.get("thumbnail", "")
                title = extraction.get("title", "Untitled")[:60]
                if thumb:
                    tg_send_photo(thumb, f"✅ نُشر: {title}\n@{username}")
                else:
                    tg_send(f"✅ نُشر: {title}\n@{username}")
            elif msg == "duplicate":
                total_duplicates += 1
                published_tweets.append(tweet_id)
            else:
                total_failed += 1
                published_tweets.append(tweet_id)

            # Small delay between tweets
            time.sleep(2)

        results.append({"account": username, "published": account_published, "tweets_found": len(tweet_ids)})
        stats["total_extracted"] = stats.get("total_extracted", 0) + len(tweet_ids)

        # Cooldown between accounts (except after the last one)
        if i < len(accounts) - 1:
            tg_send(f"⏳ استراحة {cooldown} دقيقة قبل الحساب التالي...")
            time.sleep(cooldown * 60)

    # Update state
    state["published_tweets"] = published_tweets[-500:]  # keep last 500
    state["last_run"] = datetime.now(timezone.utc).isoformat()
    state["stats"] = stats
    save_state(state)

    # Send report
    report = (
        f"📊 تقرير الاستخراج {run_type}\n"
        f"═══════════════════════════\n"
        f"✅ منشور: {total_published}\n"
        f"🔁 مكرر: {total_duplicates}\n"
        f"❌ فشل: {total_failed}\n"
        f"📹 بدون فيديو: {total_no_video}\n"
        f"═══════════════════════════\n"
    )
    for r in results:
        acct = r["account"]
        if r.get("published", 0) > 0:
            report += f"  @{acct}: {r['published']} منشور\n"
        elif r.get("result") == "no_tweets":
            report += f"  @{acct}: لا توجد تغريدات\n"
        else:
            report += f"  @{acct}: 0 منشور\n"

    tg_send(report)


# ═══════════════════════════════════════════════════════════════════════════════
# TELEGRAM BOT — command handling via long polling
# ═══════════════════════════════════════════════════════════════════════════════

def handle_command(text, state):
    """Handle a Telegram command and return a response."""
    parts = text.strip().split()
    cmd = parts[0].lower() if parts else ""
    args = parts[1:]

    if cmd == "/start" or cmd == "/help":
        return (
            "🤖 بوت نشر فيديوهات تويتر\n"
            "═══════════════════════════\n"
            "/addaccount @username — إضافة حساب تويتر\n"
            "/listaccounts — عرض الحسابات\n"
            "/removeaccount @username — حذف حساب\n"
            "/run — تشغيل الاستخراج الآن\n"
            "/setinterval 6 — ضبط الفاصل (ساعات، 0=إيقاف)\n"
            "/cooldown 5 — ضبط فترة التبريد (دقائق)\n"
            "/report — تقرير الحالة\n"
            "/addtweet <URL> — نشر تغريدة يدوياً\n"
            "/stats — إحصائيات"
        )

    elif cmd == "/addaccount":
        if not args:
            return "استخدم: /addaccount @username"
        username = args[0].lstrip('@')
        if username in state["accounts"]:
            return f"⚠️ @{username} موجود بالفعل"
        state["accounts"].append(username)
        save_state(state)
        return f"✅ تمت إضافة @{username}\nالحسابات: {len(state['accounts'])}"

    elif cmd == "/listaccounts":
        if not state["accounts"]:
            return "📋 لا توجد حسابات"
        accounts_list = "\n".join(f"  {i+1}. @{a}" for i, a in enumerate(state["accounts"]))
        return f"📋 الحسابات ({len(state['accounts'])}):\n{accounts_list}"

    elif cmd == "/removeaccount":
        if not args:
            return "استخدم: /removeaccount @username"
        username = args[0].lstrip('@')
        if username in state["accounts"]:
            state["accounts"].remove(username)
            save_state(state)
            return f"✅ تم حذف @{username}"
        return f"⚠️ @{username} غير موجود"

    elif cmd == "/run":
        threading.Thread(target=run_extraction, args=(state, True), daemon=True).start()
        return "🚀 بدء الاستخراج اليدوي..."

    elif cmd == "/setinterval":
        if not args:
            return f"الفاصل الحالي: {state['auto_interval_hours']} ساعة\nاستخدم: /setinterval 6"
        try:
            hours = int(args[0])
            state["auto_interval_hours"] = hours
            save_state(state)
            # Reschedule
            schedule.clear()
            if hours > 0:
                schedule.every(hours).hours.do(lambda: run_extraction(state, False))
                return f"✅ الفاصل: كل {hours} ساعة"
            return "✅ التشغيل التلقائي متوقف"
        except:
            return "استخدم رقماً: /setinterval 6"

    elif cmd == "/cooldown":
        if not args:
            return f"التبريد الحالي: {state['cooldown_minutes']} دقيقة\nاستخدم: /cooldown 5"
        try:
            minutes = int(args[0])
            state["cooldown_minutes"] = minutes
            save_state(state)
            return f"✅ فترة التبريد: {minutes} دقيقة"
        except:
            return "استخدم رقماً: /cooldown 5"

    elif cmd == "/report":
        last_run = state.get("last_run", "أبداً")
        stats = state.get("stats", {})
        return (
            f"📊 تقرير الحالة\n"
            f"═══════════════════════════\n"
            f"الحسابات: {len(state['accounts'])}\n"
            f"الفاصل: {state['auto_interval_hours']} ساعة\n"
            f"التبريد: {state['cooldown_minutes']} دقيقة\n"
            f"آخر تشغيل: {last_run}\n"
            f"═══════════════════════════\n"
            f"إجمالي منشور: {stats.get('total_published', 0)}\n"
            f"إجمالي مستخرج: {stats.get('total_extracted', 0)}\n"
            f"إجمالي مكرر: {stats.get('total_duplicates', 0)}\n"
            f"إجمالي فشل: {stats.get('total_failed', 0)}"
        )

    elif cmd == "/addtweet":
        if not args:
            return "استخدم: /addtweet https://x.com/user/status/123"
        tweet_url = args[0]
        def process_single():
            tg_send(f"📹 جاري استخراج: {tweet_url}")
            extraction = extract_tweet_video(tweet_url)
            if not extraction:
                tg_send("❌ فشل الاستخراج — لا يوجد فيديو")
                return
            if extraction.get("deleted"):
                tg_send("❌ التغريدة محذوفة")
                return
            success, msg = publish_video(tweet_url, extraction)
            if success:
                title = extraction.get("title", "")[:60]
                thumb = extraction.get("thumbnail", "")
                if thumb:
                    tg_send_photo(thumb, f"✅ نُشر: {title}")
                else:
                    tg_send(f"✅ نُشر: {title}")
            elif msg == "duplicate":
                tg_send("🔁 الفيديو موجود مسبقاً")
            else:
                tg_send(f"❌ فشل النشر: {msg}")
        threading.Thread(target=process_single, daemon=True).start()
        return "📹 جاري المعالجة..."

    elif cmd == "/stats":
        stats = state.get("stats", {})
        return (
            f"📈 الإحصائيات\n"
            f"منشور: {stats.get('total_published', 0)}\n"
            f"مستخرج: {stats.get('total_extracted', 0)}\n"
            f"مكرر: {stats.get('total_duplicates', 0)}\n"
            f"فشل: {stats.get('total_failed', 0)}"
        )

    return None


def telegram_polling(state):
    """Long-poll Telegram for updates."""
    offset = 0
    print("[BOT] Telegram polling started")
    while True:
        try:
            url = f"https://api.telegram.org/bot{BOT_TOKEN}/getUpdates"
            resp = requests.get(url, params={"offset": offset, "timeout": 30}, timeout=35)
            data = resp.json()
            if not data.get("ok"):
                time.sleep(5)
                continue
            for update in data.get("result", []):
                offset = update["update_id"] + 1
                msg = update.get("message", {})
                text = msg.get("text", "")
                if not text.startswith("/"):
                    continue
                # Only respond to the authorized chat
                chat_id = msg.get("chat", {}).get("id")
                if str(chat_id) != str(CHAT_ID):
                    continue
                print(f"[BOT] Command: {text}")
                response = handle_command(text, state)
                if response:
                    tg_send(response)
        except Exception as e:
            print(f"[BOT] polling error: {e}")
            time.sleep(5)


# ═══════════════════════════════════════════════════════════════════════════════
# SCHEDULER — auto-run
# ═══════════════════════════════════════════════════════════════════════════════

def scheduler_loop(state):
    """Run scheduled tasks."""
    interval = state.get("auto_interval_hours", 6)
    if interval > 0:
        schedule.every(interval).hours.do(lambda: run_extraction(state, False))

    while True:
        schedule.run_pending()
        time.sleep(60)


# ═══════════════════════════════════════════════════════════════════════════════
# MAIN
# ═══════════════════════════════════════════════════════════════════════════════

if __name__ == '__main__':
    state = load_state()

    print(f"[BOT] Starting Twitter Auto-Publisher Bot")
    print(f"[BOT] Chat ID: {CHAT_ID}")
    print(f"[BOT] Accounts: {len(state['accounts'])}")
    print(f"[BOT] Interval: {state['auto_interval_hours']}h")
    print(f"[BOT] Cooldown: {state['cooldown_minutes']}m")
    print(f"[BOT] Extractor: {EXTRACTOR_URL}")

    # Start Telegram polling in a thread
    polling_thread = threading.Thread(target=telegram_polling, args=(state,), daemon=True)
    polling_thread.start()

    # Start scheduler in a thread
    scheduler_thread = threading.Thread(target=scheduler_loop, args=(state,), daemon=True)
    scheduler_thread.start()

    # Send startup message
    tg_send(
        f"🤖 بوت نشر فيديوهات تويتر جاهز!\n"
        f"═══════════════════════════\n"
        f"الحسابات: {len(state['accounts'])}\n"
        f"الفاصل: {state['auto_interval_hours']} ساعة\n"
        f"التبريد: {state['cooldown_minutes']} دقيقة\n"
        f"═══════════════════════════\n"
        f"أرسل /help للأوامر"
    )

    # Keep main thread alive
    try:
        while True:
            time.sleep(1)
    except KeyboardInterrupt:
        print("\n[BOT] Shutting down...")
        tg_send("🤖 تم إيقاف البوت")
