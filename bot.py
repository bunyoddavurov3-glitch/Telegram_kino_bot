import json
import os
import random
from datetime import datetime

import aiohttp
import asyncio

from aiogram import Bot, Dispatcher, executor, types
from aiogram.contrib.fsm_storage.memory import MemoryStorage
from aiogram.dispatcher import FSMContext
from aiogram.dispatcher.filters.state import State, StatesGroup
from dotenv import load_dotenv

# ================== ENV ==================
load_dotenv()

BOT_TOKEN = os.getenv("BOT_TOKEN")
ADMIN_ID = int(os.getenv("ADMIN_ID"))
CHANNEL1_ID = int(os.getenv("CHANNEL1_ID"))
CHANNEL2_ID = int(os.getenv("CHANNEL2_ID"))
CHANNEL2_LINK = os.getenv("CHANNEL2_LINK")
BOT_USERNAME = os.getenv("BOT_USERNAME")
MOVIES_FILE = os.getenv("MOVIES_FILE", "movies.json")

ADMINS = [ADMIN_ID]

# ================== RICHADS ENV ==================
RICHADS_PUBLISHER_ID = os.getenv("RICHADS_PUBLISHER_ID")
RICHADS_URL = os.getenv("RICHADS_URL", "http://15068.xml.adx1.com/telegram-mb")

# ================== BOT ==================
bot = Bot(token=BOT_TOKEN, parse_mode="HTML")
dp = Dispatcher(bot, storage=MemoryStorage())

# ================== XOTIRA ==================
last_movie_request = {}

# ================== RICHADS XOTIRA ==================
ads_counter = {}

def need_show_ad(user_id: int, every: int = 3) -> bool:
    ads_counter[user_id] = ads_counter.get(user_id, 0) + 1
    return ads_counter[user_id] % every == 0


async def get_richads_ad(user_id: int, language_code: str = "ru"):
    if not RICHADS_PUBLISHER_ID or not RICHADS_URL:
        return None

    payload = {
        "language_code": language_code,
        "publisher_id": str(RICHADS_PUBLISHER_ID),
        "telegram_id": str(user_id),
        "production": True
    }

    try:
        timeout = aiohttp.ClientTimeout(total=4)
        async with aiohttp.ClientSession(timeout=timeout) as session:
            async with session.post(RICHADS_URL, json=payload) as resp:
                if resp.status != 200:
                    return None

                data = await resp.json(content_type=None)

                if isinstance(data, list) and len(data) > 0:
                    return data[0]

                if isinstance(data, dict):
                    return data

                return None

    except (aiohttp.ClientError, asyncio.TimeoutError):
        return None
    except Exception:
        return None


async def ping_notification_url(notification_url: str):
    if not notification_url:
        return
    try:
        timeout = aiohttp.ClientTimeout(total=3)
        async with aiohttp.ClientSession(timeout=timeout) as session:
            await session.get(notification_url)
    except Exception:
        return


async def send_richads_ad(message: types.Message, ad: dict):
    if not ad:
        return False

    title = ad.get("title") or ""
    text = ad.get("message") or ""
    link = ad.get("link") or ""
    button = ad.get("button") or "🔗 Ochish"
    img = ad.get("image_preload") or ad.get("image")

    caption = "📢 Reklama\n\n"
    if title:
        caption += f"<b>{title}</b>\n"
    if text:
        caption += f"{text}\n"

    kb = None
    if link:
        kb = types.InlineKeyboardMarkup()
        kb.add(types.InlineKeyboardButton(button, url=link))

    try:
        if img:
            await message.answer_photo(photo=img, caption=caption, reply_markup=kb)
        else:
            await message.answer(caption, reply_markup=kb)
        return True
    except Exception:
        return False

# ================== JSON ==================
def load_movies():
    if not os.path.exists(MOVIES_FILE):
        return {}
    with open(MOVIES_FILE, "r", encoding="utf-8") as f:
        return json.load(f)

def save_movies(data):
    with open(MOVIES_FILE, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)

# ================== STATISTIKA ==================
STATS_FILE = "statistics.json"

def load_stats():
    if not os.path.exists(STATS_FILE):
        return {
            "users": [],
            "total_requests": 0,
            "today": {
                "date": datetime.now().strftime("%Y-%m-%d"),
                "count": 0
            }
        }
    with open(STATS_FILE, "r", encoding="utf-8") as f:
        return json.load(f)

def save_stats(data):
    with open(STATS_FILE, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)

def update_stats(user_id: int):
    stats = load_stats()
    today = datetime.now().strftime("%Y-%m-%d")

    if user_id not in stats["users"]:
        stats["users"].append(user_id)

    stats["total_requests"] += 1

    if stats["today"]["date"] != today:
        stats["today"] = {"date": today, "count": 1}
    else:
        stats["today"]["count"] += 1

    save_stats(stats)

# ================== AVTOKOD ==================
def generate_unique_code(movies: dict) -> str:
    while True:
        code = str(random.randint(1000, 9999))
        if code not in movies:
            return code

# ================== OBUNA ==================
async def check_subscription(user_id: int) -> bool:
    try:
        member = await bot.get_chat_member(CHANNEL2_ID, user_id)
        return member.status in ("member", "administrator", "creator")
    except:
        return False

def subscribe_kb():
    kb = types.InlineKeyboardMarkup(row_width=1)
    kb.add(
        types.InlineKeyboardButton("🔔 Kanalga obuna bo‘lish", url=CHANNEL2_LINK),
        types.InlineKeyboardButton("✅ Tekshirish", callback_data="check_sub")
    )
    return kb

# ================== MENULAR ==================
def user_menu():
    kb = types.ReplyKeyboardMarkup(resize_keyboard=True)
    kb.add("🎬 Qidiruv")
    return kb

def admin_menu():
    kb = types.ReplyKeyboardMarkup(resize_keyboard=True)
    kb.row("➕ Kino qo‘shish", "🗑 Kino o‘chirish")
    kb.row("🎬 Qidiruv", "❌ Bekor qilish")
    kb.row("📊 Statistika")
    kb.row("📦 Kino backup", "📈 Statistika backup")
    return kb

# ================== FSM ==================
class AddMovie(StatesGroup):
    post = State()
    video = State()

class DeleteMovie(StatesGroup):
    code = State()

# ================== START ==================
@dp.message_handler(commands=["start"])
async def start_cmd(message: types.Message, state: FSMContext):
    await state.finish()

    args = message.get_args()
    if args.isdigit():
        message.text = args
        await search_movie(message)
        return

    if message.from_user.id in ADMINS:
        await message.answer("👑 <b>Admin panel</b>", reply_markup=admin_menu())
    else:
        await message.answer("🎬 Kino kodini yuboring", reply_markup=user_menu())

# ================== QIDIRUV ==================
@dp.message_handler(lambda m: m.text == "🎬 Qidiruv")
async def search_btn(message: types.Message):
    kb = admin_menu() if message.from_user.id in ADMINS else user_menu()
    await message.answer("🔎 Kino kodini yuboring", reply_markup=kb)

# ================== KINO QO‘SHISH ==================
@dp.message_handler(lambda m: m.text == "➕ Kino qo‘shish")
async def add_movie_btn(message: types.Message):
    if message.from_user.id not in ADMINS:
        return
    await message.answer("📨 Rasm-pasimlarini tashang", reply_markup=admin_menu())
    await AddMovie.post.set()

@dp.message_handler(content_types=types.ContentType.PHOTO, state=AddMovie.post)
async def add_post(message: types.Message, state: FSMContext):
    movies = load_movies()
    code = generate_unique_code(movies)

    await state.update_data(
        code=code,
        post_file_id=message.photo[-1].file_id,
        post_caption=message.caption or ""
    )

    await message.answer(f"🆔 <b>Kino kodi avtomatik berildi:</b> {code}\n\n🎥 Endi video tashang")
    await AddMovie.video.set()

@dp.message_handler(content_types=types.ContentType.VIDEO, state=AddMovie.video)
async def add_video(message: types.Message, state: FSMContext):
    movies = load_movies()

    new_unique_id = message.video.file_unique_id
    for movie in movies.values():
        if movie.get("video_unique_id") == new_unique_id:
            await message.answer("❗ Bu kino borku tog'o")
            await state.finish()
            return

    data = await state.get_data()
    movies[data["code"]] = {
        "post_file_id": data["post_file_id"],
        "post_caption": data["post_caption"],
        "video_file_id": message.video.file_id,
        "video_unique_id": message.video.file_unique_id
    }
    save_movies(movies)

    kb = types.InlineKeyboardMarkup()
    kb.add(
        types.InlineKeyboardButton("✅ Kanalga jo'nataymi", callback_data=f"send_{data['code']}"),
        types.InlineKeyboardButton("❌ Yo jo'natmayinmi?", callback_data="cancel_send")
    )

    await message.answer(f"✅ Kino saqlandi\n🆔 Kod: {data['code']}\n\nKanalga yuboraymi?", reply_markup=kb)
    await state.finish()

# ================== KANALGA YUBORISH ==================
@dp.callback_query_handler(lambda c: c.data.startswith("send_"))
async def send_to_channel(call: types.CallbackQuery):
    code = call.data.split("_")[1]
    movies = load_movies()
    movie = movies.get(code)

    if not movie:
        await call.answer("❌ Topilmadi", show_alert=True)
        return

    kb = types.InlineKeyboardMarkup()
    kb.add(types.InlineKeyboardButton("🎬 Filmni bot orqali ko‘rish", url=f"https://t.me/{BOT_USERNAME}?start={code}"))

    caption = f"{movie['post_caption']}\n\n🆔 Kod: {code}"

    msg = await bot.send_photo(CHANNEL2_ID, movie["post_file_id"], caption, reply_markup=kb)
    movies[code]["channel_msg_id"] = msg.message_id
    save_movies(movies)

    await call.message.edit_text("🚀 Kanalga keeetti tog'o")
    await call.answer()

@dp.callback_query_handler(lambda c: c.data == "cancel_send")
async def cancel_send(call: types.CallbackQuery):
    await call.message.edit_text("❎ Bekor qilindi")
    await call.answer()

# ================== QIDIRISH ==================
@dp.message_handler(lambda m: m.text and m.text.isdigit())
async def search_movie(message: types.Message):
    if not await check_subscription(message.from_user.id):
        await message.answer("❗ Avval kanalga obuna bo‘ling", reply_markup=subscribe_kb())
        return

    movies = load_movies()
    if message.text not in movies:
        await message.answer("❌ Bunday kodli kino topilmadi")
        return

    update_stats(message.from_user.id)
    last_movie_request[message.from_user.id] = message.text

    kb = types.InlineKeyboardMarkup()
    kb.add(types.InlineKeyboardButton("🎬 Filmni ko‘rish", callback_data=f"watch_{message.text}"))

    await message.answer_photo(
        movies[message.text]["post_file_id"],
        movies[message.text]["post_caption"],
        reply_markup=kb,
        protect_content=True
    )

# ================== VIDEO KO‘RISH ==================
@dp.callback_query_handler(lambda c: c.data.startswith("watch_"))
async def watch_movie(call: types.CallbackQuery):
    code = call.data.split("_")[1]

    if last_movie_request.get(call.from_user.id) != code:
        await call.answer(
            "❗ Tugma eskirgan. Faqat oxirgi so'ralgan filmni ko'rishingiz mumkin. "
            "Ushbu filmni ko'rish uchun esa kod orqali qayta qidiring yoki "
            "kanaldagi bu film posti ostidagi ko'rish tugmasini bosing ",
            show_alert=True
        )
        return

    if not await check_subscription(call.from_user.id):
        await call.message.answer("❗ Avval kanalga obuna bo‘lingda", reply_markup=subscribe_kb())
        await call.answer()
        return

    # ================== RICHADS (3/1, KINO OLDIDAN) ==================
    try:
        if need_show_ad(call.from_user.id, every=3):
            ad = await get_richads_ad(call.from_user.id, language_code="ru")
            sent = await send_richads_ad(call.message, ad)
            if sent and ad:
                await ping_notification_url(ad.get("notification_url"))
    except Exception:
        pass
    # ================================================================

    await bot.send_video(call.from_user.id, load_movies()[code]["video_file_id"], protect_content=True)
    await call.answer()

# ================== STATISTIKA ==================
def stats_text():
    stats = load_stats()
    movies = load_movies()
    return (
        "📊 <b>Bot statistikasi</b>\n\n"
        f"👥 Bezorilar: <b>{len(stats['users'])}</b>\n"
        f"🎬 Kinolar: <b>{len(movies)}</b>\n"
        f"📥 Bugun so‘rovlar: <b>{stats['today']['count']}</b>\n"
        f"🔢 Jami so‘rovlar: <b>{stats['total_requests']}</b>"
    )

def stats_kb():
    kb = types.InlineKeyboardMarkup()
    kb.add(
        types.InlineKeyboardButton("🔄 Yangilash", callback_data="stats_refresh"),
        types.InlineKeyboardButton("❌ Yopish", callback_data="stats_close")
    )
    return kb

@dp.message_handler(lambda m: m.text == "📊 Statistika")
async def show_stats(message: types.Message):
    if message.from_user.id not in ADMINS:
        return
    await message.answer(stats_text(), reply_markup=stats_kb())

@dp.callback_query_handler(lambda c: c.data == "stats_refresh")
async def refresh_stats(call: types.CallbackQuery):
    await call.message.edit_text(stats_text(), reply_markup=stats_kb())
    await call.answer()

@dp.callback_query_handler(lambda c: c.data == "stats_close")
async def close_stats(call: types.CallbackQuery):
    await call.message.delete()
    await call.answer()

# ================== BACKUP ==================
@dp.message_handler(lambda m: m.text == "📦 Kino backup")
async def backup_movies(message: types.Message):
    if message.from_user.id not in ADMINS:
        return
    if not os.path.exists(MOVIES_FILE):
        await message.answer("❌ movies.json topilmadi")
        return
    await message.answer_document(types.InputFile(MOVIES_FILE))

@dp.message_handler(lambda m: m.text == "📈 Statistika backup")
async def backup_stats(message: types.Message):
    if message.from_user.id not in ADMINS:
        return
    if not os.path.exists(STATS_FILE):
        await message.answer("❌ statistics.json topilmadi")
        return
    await message.answer_document(types.InputFile(STATS_FILE))

# ================== KINO O‘CHIRISH ==================
@dp.message_handler(lambda m: m.text == "🗑 Kino o‘chirish")
async def del_btn(message: types.Message):
    if message.from_user.id not in ADMINS:
        return
    await message.answer("🗑 Koddi ayting tog'o", reply_markup=admin_menu())
    await DeleteMovie.code.set()

@dp.message_handler(lambda m: m.text.isdigit(), state=DeleteMovie.code)
async def delete_movie(message: types.Message, state: FSMContext):
    movies = load_movies()
    code = message.text.strip()

    if code not in movies:
        await message.answer("❌ Bunaqa kino o'zi yo'q tog'o", reply_markup=admin_menu())
        await state.finish()
        return

    msg_id = movies[code].get("channel_msg_id")
    if msg_id:
        try:
            await bot.delete_message(CHANNEL2_ID, msg_id)
        except:
            pass

    del movies[code]
    save_movies(movies)

    await message.answer(f"🗑 O'chirib tashadim tog'o\n🆔 Kod: {code}", reply_markup=admin_menu())
    await state.finish()

# ================== BEKOR ==================
@dp.message_handler(lambda m: m.text == "❌ Bekor qilish", state="*")
async def cancel_all(message: types.Message, state: FSMContext):
    await state.finish()
    await message.answer("❎ Bekor qilindi tog'o", reply_markup=admin_menu())

# ================== OBUNA TEKSHIR ==================
@dp.callback_query_handler(lambda c: c.data == "check_sub")
async def recheck(call: types.CallbackQuery):
    if await check_subscription(call.from_user.id):
        await call.message.edit_text("✅ Obuna tasdiqlandi. Kod yuboring.")
    else:
        await call.answer("❌ Hali obuna bo'lmadingizku 😕", show_alert=True)

# ================== USER XATOLI XABAR ==================
@dp.message_handler(
    lambda m: m.from_user.id not in ADMINS,
    content_types=types.ContentType.ANY,
    state="*"
)
async def user_wrong_input(message: types.Message):
    await message.answer(
        "❌ <b>Brat siz admin emassiz!</b>\n"
        "🎬 Faqat <b>Qidiruv</b> tugmasidan foydalanishingiz mumkin."
    )

# ================== START ==================
if __name__ == "__main__":
    executor.start_polling(dp, skip_updates=True)
