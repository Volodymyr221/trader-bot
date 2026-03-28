import feedparser
import anthropic
import json
import os
import asyncio
from datetime import datetime, timezone, timedelta
from telegram import Bot
from apscheduler.schedulers.asyncio import AsyncIOScheduler

TELEGRAM_TOKEN = os.environ.get("TELEGRAM_TOKEN")
TELEGRAM_CHAT_ID = os.environ.get("TELEGRAM_CHAT_ID")
ANTHROPIC_API_KEY = os.environ.get("ANTHROPIC_API_KEY")
CHECK_INTERVAL_MINUTES = 60
MAX_NEWS_PER_RUN = 15
HOURS_LOOKBACK = 2

RSS_FEEDS = [
    "https://cryptopanic.com/news/rss/",
    "https://www.coindesk.com/arc/outboundfeeds/rss/",
    "https://feeds.content.dowjones.io/public/rss/mw_realtimeheadlines",
]

TWITTER_ACCOUNTS = [
    "CryptoCapo_",
    "PeterLBrandt",
    "woonomic",
    "RaoulGMI",
    "saylor",
    "DLavrov",
    "ForexSignals",
    "FXStreetNews",
]

NITTER_INSTANCES = [
    "https://nitter.net",
    "https://nitter.privacydev.net",
    "https://nitter.poast.org",
]

client = anthropic.Anthropic(api_key=ANTHROPIC_API_KEY)
bot = Bot(token=TELEGRAM_TOKEN)
sent_ids = set()

def is_recent(entry):
    try:
        import time
        t = entry.get("published_parsed") or entry.get("updated_parsed")
        if not t:
            return True
        pub_time = datetime.fromtimestamp(time.mktime(t), tz=timezone.utc)
        return datetime.now(timezone.utc) - pub_time < timedelta(hours=HOURS_LOOKBACK)
    except:
        return True

def fetch_news():
    items = []
    for url in RSS_FEEDS:
        try:
            feed = feedparser.parse(url)
            for entry in feed.entries[:8]:
                if is_recent(entry):
                    items.append({
                        "id": entry.get("id", entry.link),
                        "title": entry.title,
                        "summary": entry.get("summary", "")[:300],
                        "link": entry.link,
                        "source": feed.feed.get("title", url)
                    })
        except Exception as e:
            print(f"Помилка RSS {url}: {e}")
    return items

def fetch_twitter():
    items = []
    for account in TWITTER_ACCOUNTS:
        fetched = False
        for instance in NITTER_INSTANCES:
            try:
                url = f"{instance}/{account}/rss"
                feed = feedparser.parse(url)
                if feed.entries:
                    for entry in feed.entries[:3]:
                        if is_recent(entry):
                            items.append({
                                "id": entry.get("id", entry.link),
                                "title": f"@{account}: {entry.title}",
                                "summary": entry.get("summary", "")[:300],
                                "link": entry.link,
                                "source": f"Twitter @{account}"
                            })
                    fetched = True
                    break
            except Exception as e:
                print(f"Nitter {instance} помилка для @{account}: {e}")
        if not fetched:
            print(f"Не вдалось отримати твіти @{account}")
    return items

def analyze_with_claude(news_items):
    news_text = "\n\n".join([
        f"[{item['source']}] {item['title']}\n{item['summary']}"
        for item in news_items
    ])
    response = client.messages.create(
        model="claude-sonnet-4-20250514",
        max_tokens=2000,
        messages=[{"role": "user", "content": f"""Ти досвідчений торговий аналітик. Проаналізуй новини та твіти і склади красивий дайджест для трейдера в Telegram.

ВАЖЛИВО щодо форматування:
- Використовуй емодзі для візуального розділення
- Жодних зірочок **, решіток ##, підкреслень __ — тільки чистий текст і емодзі
- Між кожною новиною роби відступ з лінією ——————
- Sentiment позначай: 🟢 Бичачий / 🔴 Ведмежий / ⚪ Нейтральний
- Активи позначай через 💎 (крипто) або 💱 (форекс/акції)

СТРУКТУРА кожної важливої новини:
📌 Заголовок новини

Висновок: 2-3 речення з детальним поясненням що це означає для ринку і трейдера

Sentiment: 🟢/🔴/⚪ + коротко чому

Активи: 💎 BTC, ETH або 💱 EUR/USD

——————

Наприкінці зроби ЗАГАЛЬНИЙ ВИСНОВОК по ринку — 4-6 речень. Що домінує зараз, бики чи ведмеді, на що звернути увагу трейдеру сьогодні, які активи виглядають цікаво.

Починай повідомлення з:
📊 Дайджест ринку

І завершуй:
🔮 Загальний висновок:
[твій висновок тут]

Неважливі новини повністю ігноруй. Відповідай українською мовою.

НОВИНИ ТА ТВІТИ:
{news_text}"""}]
    )
    return response.content[0].text

async def run_digest():
    global sent_ids
    print("Запуск дайджесту...")
    all_items = fetch_news() + fetch_twitter()
    new_items = [n for n in all_items if n["id"] not in sent_ids]

    if not new_items:
        print("Нових новин немає.")
        return

    items_to_analyze = new_items[:MAX_NEWS_PER_RUN]
    analysis = analyze_with_claude(items_to_analyze)

    await bot.send_message(
        chat_id=TELEGRAM_CHAT_ID,
        text=analysis
    )

    sent_ids.update(n["id"] for n in items_to_analyze)
    print(f"Відправлено {len(items_to_analyze)} матеріалів.")

async def main():
    await run_digest()
    scheduler = AsyncIOScheduler()
    scheduler.add_job(run_digest, "interval", minutes=CHECK_INTERVAL_MINUTES)
    scheduler.start()
    print(f"Бот працює. Перевірка кожні {CHECK_INTERVAL_MINUTES} хв.")
    await asyncio.Event().wait()

if __name__ == "__main__":
    asyncio.run(main())
