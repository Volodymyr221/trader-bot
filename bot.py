import feedparser
import anthropic
import os
from datetime import datetime, timezone, timedelta
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import Application, CommandHandler, CallbackQueryHandler, ContextTypes
import psycopg2

TELEGRAM_TOKEN = os.environ.get("TELEGRAM_TOKEN")
ANTHROPIC_API_KEY = os.environ.get("ANTHROPIC_API_KEY")
DATABASE_URL = os.environ.get("DATABASE_URL")
CHECK_INTERVAL_MINUTES = 60
MAX_NEWS_PER_RUN = 20
HOURS_LOOKBACK = 2

SECTORS = {
    "forex": {
        "name": "💱 Форекс",
        "keywords": ["EUR/USD", "GBP/USD", "USD/JPY", "AUD/USD", "USD/CHF", "forex", "eurusd", "gbpusd", "usdjpy", "dollar", "euro", "pound", "yen", "currency", "валют", "долар", "євро", "фунт"],
        "assets": ["EUR/USD", "GBP/USD", "USD/JPY", "AUD/USD", "USD/CHF", "NZD/USD"]
    },
    "metals": {
        "name": "🥇 Метали",
        "keywords": ["gold", "silver", "XAU", "XAG", "XAUUSD", "XAGUSD", "золот", "срібл", "метал"],
        "assets": ["XAU/USD", "XAG/USD"]
    },
    "crypto": {
        "name": "💎 Крипто топ",
        "keywords": ["bitcoin", "ethereum", "BTC", "ETH", "SOL", "XRP", "solana", "ripple", "crypto", "крипт", "біткоїн", "ефіріум"],
        "assets": ["BTC/USDT", "ETH/USDT", "SOL/USDT", "XRP/USDT"]
    },
    "alts": {
        "name": "🌀 Альткоїни",
        "keywords": ["ADA", "AVAX", "DOT", "LINK", "MATIC", "UNI", "altcoin", "альткоїн", "cardano", "avalanche", "polkadot"],
        "assets": ["ADA", "AVAX", "DOT", "LINK", "MATIC", "UNI"]
    },
    "macro": {
        "name": "🌍 Макро/Економіка",
        "keywords": ["fed", "federal reserve", "ECB", "inflation", "GDP", "interest rate", "ФРС", "інфляція", "ВВП", "ставк", "економік", "рецесі", "president", "trump", "biden", "macron", "powell", "трамп", "байден", "пауелл", "санкці", "тариф", "trade war", "торгова війна"]
    }
}

RSS_FEEDS = [
    "https://cryptopanic.com/news/rss/",
    "https://www.coindesk.com/arc/outboundfeeds/rss/",
    "https://feeds.content.dowjones.io/public/rss/mw_realtimeheadlines",
]

TWITTER_ACCOUNTS = [
    "CryptoCapo_", "PeterLBrandt", "woonomic",
    "RaoulGMI", "saylor", "DLavrov", "ForexSignals", "FXStreetNews",
]

NITTER_INSTANCES = [
    "https://nitter.net",
    "https://nitter.privacydev.net",
    "https://nitter.poast.org",
]

client = anthropic.Anthropic(api_key=ANTHROPIC_API_KEY)
sent_ids = set()

def get_db():
    return psycopg2.connect(DATABASE_URL)

def init_db():
    conn = get_db()
    cur = conn.cursor()
    cur.execute("""
        CREATE TABLE IF NOT EXISTS subscribers (
            chat_id BIGINT PRIMARY KEY,
            username TEXT,
            sectors TEXT DEFAULT 'all',
            joined_at TIMESTAMP DEFAULT NOW()
        )
    """)
    try:
        cur.execute("ALTER TABLE subscribers ADD COLUMN IF NOT EXISTS sectors TEXT DEFAULT 'all'")
    except:
        pass
    conn.commit()
    cur.close()
    conn.close()
    print("База даних ініціалізована")

def add_subscriber(chat_id, username):
    conn = get_db()
    cur = conn.cursor()
    cur.execute(
        "INSERT INTO subscribers (chat_id, username, sectors) VALUES (%s, %s, %s) ON CONFLICT (chat_id) DO UPDATE SET username=%s",
        (chat_id, username, "", username)
    )
    conn.commit()
    cur.close()
    conn.close()

def update_sectors(chat_id, sectors):
    conn = get_db()
    cur = conn.cursor()
    cur.execute("UPDATE subscribers SET sectors=%s WHERE chat_id=%s", (",".join(sectors), chat_id))
    conn.commit()
    cur.close()
    conn.close()

def get_user_sectors(chat_id):
    conn = get_db()
    cur = conn.cursor()
    cur.execute("SELECT sectors FROM subscribers WHERE chat_id=%s", (chat_id,))
    row = cur.fetchone()
    cur.close()
    conn.close()
    if not row or not row[0]:
        return []
    return row[0].split(",")

def remove_subscriber(chat_id):
    conn = get_db()
    cur = conn.cursor()
    cur.execute("DELETE FROM subscribers WHERE chat_id = %s", (chat_id,))
    conn.commit()
    cur.close()
    conn.close()

def get_subscribers():
    conn = get_db()
    cur = conn.cursor()
    cur.execute("SELECT chat_id, sectors FROM subscribers WHERE sectors != ''")
    rows = cur.fetchall()
    cur.close()
    conn.close()
    return rows

def is_subscriber(chat_id):
    conn = get_db()
    cur = conn.cursor()
    cur.execute("SELECT 1 FROM subscribers WHERE chat_id = %s", (chat_id,))
    result = cur.fetchone()
    cur.close()
    conn.close()
    return result is not None

def sectors_keyboard(selected=[]):
    buttons = []
    for key, sector in SECTORS.items():
        check = "✅ " if key in selected else ""
        buttons.append([InlineKeyboardButton(f"{check}{sector['name']}", callback_data=f"sector_{key}")])
    buttons.append([InlineKeyboardButton("💾 Зберегти вибір", callback_data="save_sectors")])
    return InlineKeyboardMarkup(buttons)

user_temp_sectors = {}

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = update.effective_chat.id
    username = update.effective_user.username or update.effective_user.first_name
    add_subscriber(chat_id, username)
    user_temp_sectors[chat_id] = get_user_sectors(chat_id) or []
    await update.message.reply_text(
        "Вітаю в TradeAgent! 📊\n\n"
        "Обери сектори які тебе цікавлять.\n"
        "Можна обрати декілька:",
        reply_markup=sectors_keyboard(user_temp_sectors[chat_id])
    )

async def sector_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    chat_id = query.from_user.id

    if chat_id not in user_temp_sectors:
        user_temp_sectors[chat_id] = get_user_sectors(chat_id) or []

    if query.data.startswith("sector_"):
        sector_key = query.data.replace("sector_", "")
        if sector_key in user_temp_sectors[chat_id]:
            user_temp_sectors[chat_id].remove(sector_key)
        else:
            user_temp_sectors[chat_id].append(sector_key)
        await query.edit_message_reply_markup(
            reply_markup=sectors_keyboard(user_temp_sectors[chat_id])
        )

    elif query.data == "save_sectors":
        selected = user_temp_sectors.get(chat_id, [])
        if not selected:
            await query.edit_message_text("Будь ласка обери хоча б один сектор!")
            await query.message.reply_text(
                "Обери сектори:",
                reply_markup=sectors_keyboard([])
            )
            return
        update_sectors(chat_id, selected)
        names = [SECTORS[s]["name"] for s in selected if s in SECTORS]
        await query.edit_message_text(
            f"Чудово! Ти підписався на:\n" +
            "\n".join(names) +
            "\n\nДайджест надходитиме щогодини по обраних секторах.\n\n"
            "/settings — змінити сектори\n"
            "/status — статус підписки\n"
            "/stop — відписатись"
        )

async def settings(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = update.effective_chat.id
    user_temp_sectors[chat_id] = get_user_sectors(chat_id) or []
    await update.message.reply_text(
        "Зміни свої сектори:",
        reply_markup=sectors_keyboard(user_temp_sectors[chat_id])
    )

async def stop(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = update.effective_chat.id
    if is_subscriber(chat_id):
        remove_subscriber(chat_id)
        await update.message.reply_text("Ти відписався. Повертайся будь-коли — /start")
    else:
        await update.message.reply_text("Ти не підписаний. Натисни /start")

async def status(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = update.effective_chat.id
    count = len(get_subscribers())
    if is_subscriber(chat_id):
        sectors = get_user_sectors(chat_id)
        names = [SECTORS[s]["name"] for s in sectors if s in SECTORS]
        await update.message.reply_text(
            f"Статус: активна підписка ✅\n"
            f"Твої сектори: {', '.join(names) if names else 'не обрано'}\n"
            f"Всього підписників: {count}\n\n"
            "/settings — змінити сектори"
        )
    else:
        await update.message.reply_text("Ти не підписаний. Натисни /start")

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

def item_matches_sectors(item, user_sectors):
    text = (item["title"] + " " + item["summary"]).lower()
    for sector_key in user_sectors:
        if sector_key not in SECTORS:
            continue
        for keyword in SECTORS[sector_key]["keywords"]:
            if keyword.lower() in text:
                return True
    return False

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
                        "source": feed.feed.get("title", url)
                    })
        except Exception as e:
            print(f"Помилка RSS {url}: {e}")
    return items

def fetch_twitter():
    items = []
    for account in TWITTER_ACCOUNTS:
        for instance in NITTER_INSTANCES:
            try:
                feed = feedparser.parse(f"{instance}/{account}/rss")
                if feed.entries:
                    for entry in feed.entries[:3]:
                        if is_recent(entry):
                            items.append({
                                "id": entry.get("id", entry.link),
                                "title": f"@{account}: {entry.title}",
                                "summary": entry.get("summary", "")[:300],
                                "source": f"Twitter @{account}"
                            })
                    break
            except:
                continue
    return items

def analyze_with_claude(news_items, sectors):
    sector_names = [SECTORS[s]["name"] for s in sectors if s in SECTORS]
    news_text = "\n\n".join([
        f"[{item['source']}] {item['title']}\n{item['summary']}"
        for item in news_items
    ])
    response = client.messages.create(
        model="claude-sonnet-4-20250514",
        max_tokens=2000,
        messages=[{"role": "user", "content": f"""Ти досвідчений торговий аналітик. Склади дайджест для трейдера.

Користувача цікавлять сектори: {', '.join(sector_names)}
Аналізуй тільки новини які стосуються цих секторів.

Форматування:
- Тільки чистий текст і емодзі, без **, ##, __
- Між новинами лінія ——————
- Sentiment: 🟢 Бичачий / 🔴 Ведмежий / ⚪ Нейтральний
- Крипто: 💎, форекс/метали: 💱, макро: 🌍

Структура кожної важливої новини:
📌 Заголовок

Висновок: 2-3 речення що це означає для трейдера

Sentiment: 🟢/🔴/⚪ + чому

Активи: список активів

——————

Починай з: 📊 Дайджест ринку
Закінчуй з: 🔮 Загальний висновок: [4-6 речень]

Якщо немає релевантних новин — відповідай тільки: "НЕМАЄ_НОВИН"
Відповідай українською.

НОВИНИ:
{news_text}"""}]
    )
    return response.content[0].text

async def send_digest(context: ContextTypes.DEFAULT_TYPE):
    global sent_ids
    print("Запуск дайджесту...")
    all_items = fetch_news() + fetch_twitter()
    new_items = [n for n in all_items if n["id"] not in sent_ids]

    if not new_items:
        print("Нових новин немає.")
        return

    sent_ids.update(n["id"] for n in new_items)
    subscribers = get_subscribers()

    for chat_id, sectors_str in subscribers:
        try:
            if not sectors_str:
                continue
            sectors = sectors_str.split(",")
            relevant = [n for n in new_items if item_matches_sectors(n, sectors)]

            if not relevant:
                print(f"Немає релевантних новин для {chat_id}")
                continue

            items_to_analyze = relevant[:MAX_NEWS_PER_RUN]
            analysis = analyze_with_claude(items_to_analyze, sectors)

            if "НЕМАЄ_НОВИН" in analysis:
                continue

            await context.bot.send_message(chat_id=chat_id, text=analysis)
            print(f"Відправлено {chat_id}")
        except Exception as e:
            print(f"Помилка {chat_id}: {e}")

def main():
    init_db()
    app = Application.builder().token(TELEGRAM_TOKEN).build()
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("stop", stop))
    app.add_handler(CommandHandler("status", status))
    app.add_handler(CommandHandler("settings", settings))
    app.add_handler(CallbackQueryHandler(sector_callback))
    app.job_queue.run_repeating(send_digest, interval=CHECK_INTERVAL_MINUTES * 60, first=30)
    print("Бот запущено!")
    app.run_polling()

if __name__ == "__main__":
    main()
