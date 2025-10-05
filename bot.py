import os
import json
import random
import time
import asyncio
import aiosqlite
from dotenv import load_dotenv
from aiogram import Bot, Dispatcher, F, types
from aiogram.filters import Command
from aiogram.types import (
    InlineKeyboardMarkup,
    InlineKeyboardButton,
    ReplyKeyboardMarkup,
    KeyboardButton
)

load_dotenv()
TOKEN = os.getenv("TOKEN")

bot = Bot(token=TOKEN)
dp = Dispatcher()

with open("questions.json", "r", encoding="utf-8") as f:
    QDB = json.load(f)["topics"]

with open("rules.json", "r", encoding="utf-8") as f:
    RULES = json.load(f)

MAIN_MENU = ReplyKeyboardMarkup(
    keyboard=[
        [KeyboardButton(text="🧠 Quiz"), KeyboardButton(text="📚 Topics")],
        [KeyboardButton(text="📈 Progress"), KeyboardButton(text="📘 Literature")],
        [KeyboardButton(text="💬 Help")]
    ],
    resize_keyboard=True
)

# Database setup & user management
async def ensure_user(user_id: int, username: str):
    async with aiosqlite.connect("bot.db") as db:
        await db.execute(
            """CREATE TABLE IF NOT EXISTS users(
                id INTEGER PRIMARY KEY,
                username TEXT,
                difficulty TEXT,
                correct_streak INTEGER,
                wrong_streak INTEGER,
                active_topic TEXT
            )"""
        )
        await db.execute(
            """CREATE TABLE IF NOT EXISTS topic_stats(
                user_id INTEGER,
                topic TEXT,
                weight REAL,
                last_seen INTEGER,
                PRIMARY KEY(user_id, topic)
            )"""
        )
        await db.commit()
        cur = await db.execute("SELECT id FROM users WHERE id = ?", (user_id,))
        row = await cur.fetchone()
        if not row:
            await db.execute(
                "INSERT INTO users VALUES(?,?,?,?,?,?)",
                (user_id, username or "", "easy", 0, 0, "Mixed")
            )
            for topic in QDB.keys():
                await db.execute(
                    "INSERT OR REPLACE INTO topic_stats VALUES(?,?,?,?)",
                    (user_id, topic, 1.0, int(time.time()))
                )
            await db.commit()

async def get_user(user_id: int):
    async with aiosqlite.connect("bot.db") as db:
        cur = await db.execute("SELECT * FROM users WHERE id = ?", (user_id,))
        row = await cur.fetchone()
    if not row:
        return None
    return {
        "id": row[0],
        "username": row[1],
        "difficulty": row[2],
        "correct_streak": row[3],
        "wrong_streak": row[4],
        "active_topic": row[5]
    }

async def update_user(user_id: int, **kwargs):
    async with aiosqlite.connect("bot.db") as db:
        fields = [f"{k} = ?" for k in kwargs]
        values = list(kwargs.values()) + [user_id]
        await db.execute(f"UPDATE users SET {', '.join(fields)} WHERE id = ?", values)
        await db.commit()

async def get_topic_weights(user_id: int):
    async with aiosqlite.connect("bot.db") as db:
        cur = await db.execute("SELECT topic, weight FROM topic_stats WHERE user_id = ?", (user_id,))
        rows = await cur.fetchall()
    return {r[0]: r[1] for r in rows}

async def update_topic_weight(user_id: int, topic: str, delta: float):
    async with aiosqlite.connect("bot.db") as db:
        cur = await db.execute(
            "SELECT weight FROM topic_stats WHERE user_id = ? AND topic = ?",
            (user_id, topic)
        )
        row = await cur.fetchone()
        w = row[0] if row else 1.0
        w = max(0.1, w + delta)
        await db.execute(
            "INSERT OR REPLACE INTO topic_stats VALUES(?,?,?,?)",
            (user_id, topic, w, int(time.time()))
        )
        await db.commit()

async def record_attempt(user_id: int, qid: str, correct: bool):
    async with aiosqlite.connect("bot.db") as db:
        await db.execute(
            """CREATE TABLE IF NOT EXISTS attempts(
                user_id INTEGER,
                qid TEXT,
                correct INTEGER,
                timestamp INTEGER
            )"""
        )
        await db.execute(
            "INSERT INTO attempts VALUES(?,?,?,?)",
            (user_id, qid, 1 if correct else 0, int(time.time()))
        )
        await db.commit()

# Question logic
def choose_question(topic: str, difficulty: str, weights: dict):
    pool = []
    topics = [topic] if topic != "Mixed" else list(QDB.keys())
    for t in topics:
        qs = QDB[t]["questions"].get(difficulty, [])
        for q in qs:
            pool.append((t, q, weights.get(t, 1.0)))
    if not pool:
        return None
    total = sum(w for _, _, w in pool)
    r = random.random() * total
    upto = 0
    for t, q, w in pool:
        if upto + w >= r:
            return t, q
        upto += w
    return random.choice(pool)

async def send_question(chat_id: int, user_id: int, topic: str, difficulty: str):
    weights = await get_topic_weights(user_id)
    chosen = choose_question(topic, difficulty, weights)
    if not chosen:
        await bot.send_message(chat_id, "❌ No questions available.")
        return
    t, q = chosen
    buttons = [
        [InlineKeyboardButton(text=f"🔹 {opt}", callback_data=f"answer|{t}|{q['id']}|{opt}")]
        for opt in q["options"]
    ]
    buttons.append([InlineKeyboardButton(text="⏭️ Skip", callback_data=f"skip|{t}|{q['id']}")])
    keyboard = InlineKeyboardMarkup(inline_keyboard=buttons)
    await bot.send_message(
        chat_id,
        f"📘 *Topic:* {t}\n"
        f"💡 *Question:* {q['question']}",
        reply_markup=keyboard,
        parse_mode="Markdown"
    )

# Handlers
@dp.message(Command("start"))
async def cmd_start(message: types.Message):
    await ensure_user(message.from_user.id, message.from_user.username)
    await message.answer(
        "🤖 *Welcome to DSA Learning Bot!*\n\n"
        "📚 Master *Data Structures & Algorithms* with quizzes, adaptive difficulty, and instant feedback.\n\n"
        "Use the menu below to begin 👇",
        reply_markup=MAIN_MENU,
        parse_mode="Markdown"
    )

@dp.message(F.text == "📘 Literature")
async def menu_lit(message: types.Message):
    keyboard = InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(
                text="📖 Algorithms, 4th Edition — Sedgewick & Wayne",
                callback_data="lit|sedgewick"
            )]
        ]
    )
    await message.answer("📚 *Recommended literature:*", reply_markup=keyboard, parse_mode="Markdown")

@dp.message(F.text == "🧠 Quiz")
async def menu_quiz(message: types.Message):
    await ensure_user(message.from_user.id, message.from_user.username)
    buttons = [[InlineKeyboardButton(text="🌐 Mixed Topics", callback_data="start_quiz|Mixed")]]
    for t in QDB.keys():
        buttons.append([InlineKeyboardButton(text=f"📘 {t}", callback_data=f"start_quiz|{t}")])
    keyboard = InlineKeyboardMarkup(inline_keyboard=buttons)
    await message.answer("🎯 *Choose a topic to start your quiz:*", reply_markup=keyboard, parse_mode="Markdown")

@dp.message(F.text == "📚 Topics")
async def menu_topics(message: types.Message):
    buttons = [[InlineKeyboardButton(text=f"📘 {t}", callback_data=f"topic_info|{t}")] for t in QDB.keys()]
    keyboard = InlineKeyboardMarkup(inline_keyboard=buttons)
    await message.answer("🧩 *Select a topic to review theory:*", reply_markup=keyboard, parse_mode="Markdown")

@dp.message(F.text == "📈 Progress")
async def menu_progress(message: types.Message):
    u = await get_user(message.from_user.id)
    if not u:
        await ensure_user(message.from_user.id, message.from_user.username)
        u = await get_user(message.from_user.id)
    weights = await get_topic_weights(message.from_user.id)
    text = (
        f"📊 *Your Progress Summary*\n"
        f"━━━━━━━━━━━━━━━\n"
        f"👤 User: `{u['username'] or u['id']}`\n"
        f"🎯 Difficulty: *{u['difficulty']}*\n"
        f"🔥 Correct Streak: *{u['correct_streak']}*\n"
        f"💥 Wrong Streak: *{u['wrong_streak']}*\n"
        f"📘 Active Topic: *{u['active_topic']}*\n\n"
        f"📈 *Topic Weights:*\n"
    )
    for k, v in weights.items():
        text += f"▫️ {k}: {v:.2f}\n"
    await message.answer(text, parse_mode="Markdown")

@dp.callback_query(lambda c: c.data and c.data.startswith("lit|"))
async def cb_lit(callback: types.CallbackQuery):
    if "sedgewick" in callback.data:
        await callback.message.answer(
            "📘 *Reference:*\n"
            "[Algorithms, 4th Edition — Robert Sedgewick & Kevin Wayne]"
            "(https://www.cs.princeton.edu/~rs/Algs4.pdf)",
            parse_mode="Markdown"
        )
    await callback.answer()

@dp.callback_query(lambda c: c.data and c.data.startswith("start_quiz|"))
async def cb_start_quiz(callback: types.CallbackQuery):
    topic = callback.data.split("|")[1]
    await ensure_user(callback.from_user.id, callback.from_user.username)
    await update_user(callback.from_user.id, active_topic=topic)
    u = await get_user(callback.from_user.id)
    if topic != "Mixed":
        theory = QDB[topic]["theory"]
        await callback.message.answer(f"📘 *Theory for {topic}:*\n\n{theory}", parse_mode="Markdown")
    await callback.message.answer("🚀 *Starting quiz...*", parse_mode="Markdown")
    await send_question(callback.message.chat.id, callback.from_user.id, topic, u["difficulty"])
    await callback.answer()

@dp.callback_query(lambda c: c.data and (c.data.startswith("answer|") or c.data.startswith("skip|")))
async def cb_answer(callback: types.CallbackQuery):
    parts = callback.data.split("|")
    cmd, topic, qid = parts[0], parts[1], parts[2]
    user_id = callback.from_user.id
    u = await get_user(user_id)

    if cmd == "skip":
        await callback.message.answer("⏭️ Skipped! Next question:")
        await send_question(callback.message.chat.id, user_id, u["active_topic"], u["difficulty"])
        await callback.answer()
        return

    selected = parts[3]
    q = next(
        (qq for lvl in ["easy", "medium", "hard"] for qq in QDB[topic]["questions"].get(lvl, [])
         if qq["id"] == qid),
        None
    )
    if not q:
        await callback.answer("⚠️ Question not found.")
        return

    correct = (selected == q["answer"])
    await record_attempt(user_id, qid, correct)
    if correct:
        await update_user(user_id, correct_streak=u["correct_streak"] + 1, wrong_streak=0)
        await update_topic_weight(user_id, topic, -0.3)
        await callback.message.answer("✅ *Correct!* Well done 🎉", parse_mode="Markdown")
    else:
        await update_user(user_id, correct_streak=0, wrong_streak=u["wrong_streak"] + 1)
        await update_topic_weight(user_id, topic, 0.5)
        await callback.message.answer(f"❌ *Wrong.* Correct answer: `{q['answer']}`", parse_mode="Markdown")

    u = await get_user(user_id)
    levels = ["easy", "medium", "hard"]
    idx = levels.index(u["difficulty"])
    if u["correct_streak"] >= 3 and idx < 2:
        await update_user(user_id, difficulty=levels[idx + 1])
        await callback.message.answer(f"🌟 Difficulty increased to *{levels[idx + 1]}*", parse_mode="Markdown")
    if u["wrong_streak"] >= 2 and idx > 0:
        await update_user(user_id, difficulty=levels[idx - 1])
        await callback.message.answer(f"⚙️ Difficulty decreased to *{levels[idx - 1]}*", parse_mode="Markdown")

    await send_question(callback.message.chat.id, user_id, u["active_topic"], u["difficulty"])
    await callback.answer()

@dp.message(F.text)
async def fallback(message: types.Message):
    text = message.text.lower()
    for rule in RULES:
        if any(keyword in text for keyword in rule["keywords"]):
            await message.answer(random.choice(rule["responses"]))
            return
    await message.answer("💡 Use menu options below to continue:", reply_markup=MAIN_MENU)

async def main():
    await dp.start_polling(bot)

if __name__ == "__main__":
    asyncio.run(main())