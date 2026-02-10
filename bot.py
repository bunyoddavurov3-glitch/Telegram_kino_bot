import json
import os
import random
import re
from datetime import datetime
from typing import Any, Dict, Optional, List, Tuple

from aiogram import Bot, Dispatcher, executor, types
from aiogram.contrib.fsm_storage.memory import MemoryStorage
from aiogram.dispatcher import FSMContext
from aiogram.dispatcher.filters.state import State, StatesGroup
from dotenv import load_dotenv

# ================== ENV ==================
load_dotenv()

BOT_TOKEN = os.getenv("BOT_TOKEN")
ADMIN_ID = int(os.getenv("ADMIN_ID", "0"))

# Kanal IDlar (K1 baza, K2 biznes)
CHANNEL1_ID = int(os.getenv("BASE_CHANNEL_ID", "0"))
CHANNEL2_ID = int(os.getenv("BUSINESS_CHANNEL_ID", "0"))

# Majburiy obuna (2 ta kanal)
FORCE_SUB_1_ID = int(os.getenv("FORCE_SUB_1_ID", "0"))
FORCE_SUB_1_LINK = os.getenv("FORCE_SUB_1_LINK", "")

FORCE_SUB_2_ID = int(os.getenv("FORCE_SUB_2_ID", "0"))
FORCE_SUB_2_LINK = os.getenv("FORCE_SUB_2_LINK", "")

FORCE_SUB_ENABLED = (os.getenv("FORCE_SUB_ENABLED", "true").lower() == "true")

BOT_USERNAME = (os.getenv("BOT_USERNAME") or "").lstrip("@").strip()
MOVIES_FILE = os.getenv("MOVIES_FILE", "movies.json")

STATS_FILE = os.getenv("STATS_FILE", "statistics.json")

ADMINS = {ADMIN_ID}

# ================== BOT ==================
bot = Bot(token=BOT_TOKEN, parse_mode="HTML")
dp = Dispatcher(bot, storage=MemoryStorage())

# ================== XOTIRA ==================
# Yakuniy talab:
# - Yakka film: tugma 1 marta ishlasin (bosilgandan keyin eskirsin)
# - Serial: epizod tugmalari xohlagancha ishlasin
last_movie_request: Dict[int, str] = {}     # {user_id: code}
last_watch_token: Dict[int, str] = {}       # {user_id: token}

# ================== JSON (atomic) ==================
def _atomic_write_json(path: str, data: Any) -> None:
    tmp = f"{path}.tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
    os.replace(tmp, path)

def load_db() -> Dict[str, Any]:
    if not os.path.exists(MOVIES_FILE):
        return {}
    try:
        with open(MOVIES_FILE, "r", encoding="utf-8") as f:
            db = json.load(f)
    except Exception:
        return {}

    # Backward compatibility (eski movies.json):
    # eski botda item: {post_file_id, post_caption, video_file_id, video_unique_id, channel_msg_id?}
    # yangi botda item["type"] mavjud
    fixed: Dict[str, Any] = {}
    for code, item in (db or {}).items():
        if not isinstance(item, dict):
            continue
        if "type" not in item:
            # eski format -> movie
            fixed[code] = {
                "type": "movie",
                "post_file_id": item.get("post_file_id"),
                "post_caption": item.get("post_caption", ""),
                "video_file_id": item.get("video_file_id"),
                "video_unique_id": item.get("video_unique_id"),
                "channel_msg_id": item.get("channel_msg_id"),
            }
        else:
            fixed[code] = item
    return fixed

def save_db(data: Dict[str, Any]) -> None:
    _atomic_write_json(MOVIES_FILE, data)

# ================== STATISTIKA ==================
def load_stats() -> Dict[str, Any]:
    if not os.path.exists(STATS_FILE):
        return {
            "users": [],
            "total_requests": 0,
            "today": {"date": datetime.now().strftime("%Y-%m-%d"), "count": 0}
        }
    try:
        with open(STATS_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return {
            "users": [],
            "total_requests": 0,
            "today": {"date": datetime.now().strftime("%Y-%m-%d"), "count": 0}
        }

def save_stats(data: Dict[str, Any]) -> None:
    _atomic_write_json(STATS_FILE, data)

def update_stats(user_id: int) -> None:
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
def generate_unique_code(db: Dict[str, Any]) -> str:
    while True:
        code = str(random.randint(1000, 9999))
        if code not in db:
            return code

# ================== OBUNA ==================
async def check_subscription(user_id: int) -> bool:
    if not FORCE_SUB_ENABLED:
        return True
    try:
        member1 = await bot.get_chat_member(FORCE_SUB_1_ID, user_id)
        member2 = await bot.get_chat_member(FORCE_SUB_2_ID, user_id)
        ok1 = member1.status in ("member", "administrator", "creator")
        ok2 = member2.status in ("member", "administrator", "creator")
        return ok1 and ok2
    except Exception:
        return False

def subscribe_kb():
    kb = types.InlineKeyboardMarkup(row_width=1)
    kb.add(
        types.InlineKeyboardButton("🔔 Kanalga obuna bo‘lish", url=FORCE_SUB_1_LINK),
        types.InlineKeyboardButton("🔔 Kanalga obuna bo‘lish", url=FORCE_SUB_2_LINK),
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
    kb.row("➕ Kino qo‘shish", "➕ Serial qo‘shish")
    kb.row("✏️ Tahrirlash", "🗑 O‘chirish")
    kb.row("🎬 Qidiruv", "📊 Statistika")
    kb.row("📦 Kino backup", "📈 Statistika backup")
    kb.row("❌ Bekor qilish")
    return kb

def is_admin(uid: int) -> bool:
    return uid in ADMINS

# ================== FSM ==================
class AddMovie(StatesGroup):
    post = State()
    video = State()

class AddSeries(StatesGroup):
    poster = State()
    episodes = State()

class EditFlow(StatesGroup):
    choose_type = State()    # movie / series
    choose_code = State()
    choose_action = State()
    await_forward = State()
    await_ep_delete = State()

class DeleteFlow(StatesGroup):
    code = State()

# ================== HELPERS ==================
CODE_LINE_RE = re.compile(r"(🆔\s*Kod:\s*([0-9]{4}))", re.IGNORECASE)

def _ensure_code_line_kept(new_caption: str, old_caption_with_code: str, code: str) -> str:
    # Kanal1 captionida kod bo‘lmaydi, Kanal2’da esa kod saqlanib qolishi shart
    m = CODE_LINE_RE.search(old_caption_with_code or "")
    code_line = m.group(1) if m else f"🆔 Kod: {code}"
    cleaned = CODE_LINE_RE.sub("", (new_caption or "")).strip()
    return f"{cleaned}\n\n{code_line}".strip() if cleaned else code_line

def _duplicate_video_exists(db: Dict[str, Any], video_unique_id: str) -> bool:
    for it in db.values():
        if it.get("type") == "movie":
            if it.get("video_unique_id") == video_unique_id:
                return True
        elif it.get("type") == "series":
            for epv in (it.get("episodes", {}) or {}).values():
                if isinstance(epv, dict) and epv.get("video_unique_id") == video_unique_id:
                    return True
    return False

async def _is_forward_from_base(message: types.Message) -> bool:
    return bool(message.forward_from_chat and int(message.forward_from_chat.id) == int(CHANNEL1_ID))

def _parse_episode_caption(caption: str) -> Tuple[Optional[int], str]:
    """
    QOIDALAR:
    - Birinchi uchragan raqam -> qism raqami
    - Qolgan matn -> nom (ichidagi boshqa raqamlar ahamiyatsiz)
    """
    if not caption:
        return None, ""
    text = caption.strip()
    m = re.search(r"\d+", text)
    if not m:
        return None, text
    ep = int(m.group(0))
    title = (text[:m.start()] + text[m.end():]).strip()
    title = re.sub(r"^[\s\|\-:–—]+", "", title).strip()
    return ep, title

def _episode_user_caption(ep: int, title: str) -> str:
    # Siz aytgandek: 1-qisim(Film nomi...)
    title = (title or "").strip()
    if title:
        return f"{ep}-qisim({title})"
    return f"{ep}-qisim"

def _sorted_episode_numbers(item: Dict[str, Any]) -> List[int]:
    eps = item.get("episodes", {}) or {}
    nums: List[int] = []
    for k in eps.keys():
        if str(k).isdigit():
            nums.append(int(k))
    return sorted(nums)

# ================== INLINE KB ==================
def movie_watch_kb(code: str, token: str) -> types.InlineKeyboardMarkup:
    kb = types.InlineKeyboardMarkup()
    kb.add(types.InlineKeyboardButton("🎬 Filmni ko‘rish", callback_data=f"watch2_{code}_{token}"))
    return kb

def channel_movie_kb(code: str) -> types.InlineKeyboardMarkup:
    kb = types.InlineKeyboardMarkup()
    kb.add(types.InlineKeyboardButton("🎬 Filmni bot orqali ko‘rish", url=f"https://t.me/{BOT_USERNAME}?start={code}"))
    return kb

def channel_series_kb(code: str) -> types.InlineKeyboardMarkup:
    kb = types.InlineKeyboardMarkup()
    # Kanalga hech narsa yubormaslik uchun callback emas, URL!
    kb.add(types.InlineKeyboardButton("📺 Barcha qismlari", url=f"https://t.me/{BOT_USERNAME}?start=series_{code}"))
    return kb

def series_eps_kb(code: str, eps: List[int]) -> types.InlineKeyboardMarkup:
    kb = types.InlineKeyboardMarkup(row_width=5)
    kb.add(*[types.InlineKeyboardButton(str(n), callback_data=f"series_ep:{code}:{n}") for n in eps])
    return kb

# ================== BEKOR (har qanday holatda) ==================
@dp.message_handler(lambda m: (m.text or "").strip() == "❌ Bekor qilish" or ("bekor" in (m.text or "").lower()), state="*")
async def cancel_anytime(message: types.Message, state: FSMContext):
    await state.finish()
    if is_admin(message.from_user.id):
        await message.answer("❎ Bekor qilindi tog'o", reply_markup=admin_menu())
    else:
        await message.answer("❎ Bekor qilindi", reply_markup=user_menu())

# ================== START ==================
@dp.message_handler(commands=["start"])
async def start_cmd(message: types.Message, state: FSMContext):
    await state.finish()

    args = (message.get_args() or "").strip()

    # Kanal2 serial tugmasidan keladi: /start series_1234
    if args.startswith("series_"):
        code = args.replace("series_", "").strip()
        if code.isdigit():
            await send_series_to_user(message.from_user.id, code)
            return

    # Oddiy kino/serial kod: /start 1234
    if args.isdigit():
        message.text = args
        await search_movie(message)
        return

    if is_admin(message.from_user.id):
        await message.answer("👑 <b>Admin panel</b>", reply_markup=admin_menu())
    else:
        await message.answer("🎬 Kino kodini yuboring", reply_markup=user_menu())

# ================== QIDIRUV ==================
@dp.message_handler(lambda m: m.text == "🎬 Qidiruv")
async def search_btn(message: types.Message):
    kb = admin_menu() if is_admin(message.from_user.id) else user_menu()
    await message.answer("🔎 Kino kodini yuboring", reply_markup=kb)

# ================== KINO QO‘SHISH (YAKKA) ==================
@dp.message_handler(lambda m: m.text == "➕ Kino qo‘shish")
async def add_movie_btn(message: types.Message):
    if message.from_user.id not in ADMINS:
        await message.answer(
            "❌ <b>Brat siz admin emassiz!</b>\n"
            "🎬 Faqat <b>Qidiruv</b> tugmasidan foydalanishingiz mumkin.",
            reply_markup=user_menu()
        )
        return
    await message.answer("📨 Rasm-pasimlarini tashang", reply_markup=admin_menu())
    await AddMovie.post.set()

@dp.message_handler(content_types=types.ContentType.PHOTO, state=AddMovie.post)
async def add_post(message: types.Message, state: FSMContext):
    db = load_db()
    code = generate_unique_code(db)

    await state.update_data(
        code=code,
        post_file_id=message.photo[-1].file_id,
        post_caption=message.caption or ""
    )

    await message.answer(f"🆔 <b>Kino kodi avtomatik berildi:</b> {code}\n\n🎥 Endi video tashang", reply_markup=admin_menu())
    await AddMovie.video.set()

@dp.message_handler(content_types=types.ContentType.VIDEO, state=AddMovie.video)
async def add_video(message: types.Message, state: FSMContext):
    db = load_db()

    if _duplicate_video_exists(db, message.video.file_unique_id):
        await message.answer("❗ Bu kino borku tog'o", reply_markup=admin_menu())
        await state.finish()
        return

    data = await state.get_data()
    code = data["code"]

    db[code] = {
        "type": "movie",
        "post_file_id": data["post_file_id"],
        "post_caption": data["post_caption"],
        "video_file_id": message.video.file_id,
        "video_unique_id": message.video.file_unique_id,
        "channel_msg_id": None
    }
    save_db(db)

    kb = types.InlineKeyboardMarkup()
    kb.add(
        types.InlineKeyboardButton("✅ Kanalga jo'nataymi", callback_data=f"publish_movie:{code}"),
        types.InlineKeyboardButton("❌ Yo jo'natmayinmi?", callback_data="cancel_send")
    )

    await message.answer(f"✅ Kino saqlandi\n🆔 Kod: {code}\n\nKanalga yuboraymi?", reply_markup=kb)
    await state.finish()

# ================== SERIAL QO‘SHISH ==================
@dp.message_handler(lambda m: m.text == "➕ Serial qo‘shish")
async def add_series_btn(message: types.Message):
    if not is_admin(message.from_user.id):
        await message.answer(
            "❌ <b>Brat siz admin emassiz!</b>\n"
            "🎬 Faqat <b>Qidiruv</b> tugmasidan foydalanishingiz mumkin.",
            reply_markup=user_menu()
        )
        return
    await message.answer("📨 Serial posteri (rasm + caption)ni yuboring", reply_markup=admin_menu())
    await AddSeries.poster.set()

@dp.message_handler(content_types=types.ContentType.PHOTO, state=AddSeries.poster)
async def add_series_poster(message: types.Message, state: FSMContext):
    db = load_db()
    code = generate_unique_code(db)

    await state.update_data(
        code=code,
        poster_file_id=message.photo[-1].file_id,
        poster_caption=message.caption or "",
        episodes={}
    )

    await message.answer(
        f"🆔 <b>Kino kodi avtomatik berildi:</b> {code}\n\n"
        "Endi Kanal1 (baza)dan videoni forward qiling.\n"
        "Caption misol: <b>1 Yura davri 3</b> yoki <b>7 | Forsaj: G'azablangan</b>\n\n"
        "Tugatish uchun <b>Ha</b> deb yozing.",
        reply_markup=admin_menu()
    )
    await AddSeries.episodes.set()

@dp.message_handler(lambda m: (m.text or "").strip().lower() == "ha", state=AddSeries.episodes)
async def add_series_finish(message: types.Message, state: FSMContext):
    data = await state.get_data()
    episodes = data.get("episodes", {})

    if not episodes:
        await message.answer("❗ Hech bo‘lmasa bitta qism qo‘shing.", reply_markup=admin_menu())
        return

    db = load_db()
    code = data["code"]

    db[code] = {
        "type": "series",
        "poster_file_id": data["poster_file_id"],
        "poster_caption": data["poster_caption"],
        "episodes": episodes,
        "channel_msg_id": None
    }
    save_db(db)

    kb = types.InlineKeyboardMarkup()
    kb.add(
        types.InlineKeyboardButton("✅ Kanalga jo'nataymi", callback_data=f"publish_series:{code}"),
        types.InlineKeyboardButton("❌ Yo jo'natmayinmi?", callback_data="cancel_send")
    )

    await message.answer(f"✅ Kino saqlandi\n🆔 Kod: {code}\n\nKanalga yuboraymi?", reply_markup=kb)
    await state.finish()

@dp.message_handler(content_types=types.ContentType.VIDEO, state=AddSeries.episodes)
async def add_series_episode(message: types.Message, state: FSMContext):
    if not await _is_forward_from_base(message):
        await message.answer("❗ Iltimos, <b>Kanal1 (baza)</b>dan forward qiling.", reply_markup=admin_menu())
        return

    ep_num, ep_title = _parse_episode_caption(message.caption or "")
    if ep_num is None:
        await message.answer("❗ Video captionida qism raqami yo‘q.\nMasalan: <b>1 Yura davri 3</b>", reply_markup=admin_menu())
        return

    db = load_db()
    if _duplicate_video_exists(db, message.video.file_unique_id):
        await message.answer("❗ Bu kino borku tog'o", reply_markup=admin_menu())
        return

    data = await state.get_data()
    episodes: Dict[str, Any] = data.get("episodes", {})

    # Qo‘shish jarayonida bir xil qism kelib qolsa ustidan yozib ketadi (sizga qulay)
    episodes[str(ep_num)] = {
        "video_file_id": message.video.file_id,
        "video_unique_id": message.video.file_unique_id,
        "title": (ep_title or "").strip()
    }

    await state.update_data(episodes=episodes)
    await message.answer(f"✅ Qabul qilindi: <b>{ep_num}-qisim</b>", reply_markup=admin_menu())

@dp.message_handler(state=AddSeries.episodes, content_types=types.ContentType.TEXT)
async def add_series_text_in_episodes(message: types.Message, state: FSMContext):
    await message.answer(
        "🎥 Kanal1 (baza)dan videoni forward qiling.\n"
        "Tugatish uchun <b>Ha</b> deb yozing.",
        reply_markup=admin_menu()
    )

# ================== KANALGA YUBORISH ==================
@dp.callback_query_handler(lambda c: c.data == "cancel_send")
async def cancel_send(call: types.CallbackQuery):
    await call.message.edit_text("❎ Bekor qilindi")
    await call.answer()

@dp.callback_query_handler(lambda c: c.data.startswith("publish_movie:"))
async def publish_movie(call: types.CallbackQuery):
    code = call.data.split(":", 1)[1]
    db = load_db()
    item = db.get(code)

    if not item or item.get("type") != "movie":
        await call.answer("❌ Topilmadi", show_alert=True)
        return

    caption = f"{(item.get('post_caption') or '').strip()}\n\n🆔 Kod: {code}".strip()
    msg = await bot.send_photo(CHANNEL2_ID, item["post_file_id"], caption=caption, reply_markup=channel_movie_kb(code))
    item["channel_msg_id"] = msg.message_id
    db[code] = item
    save_db(db)

    await call.message.edit_text("🚀 Kanalga keeetti tog'o")
    await call.answer()

@dp.callback_query_handler(lambda c: c.data.startswith("publish_series:"))
async def publish_series(call: types.CallbackQuery):
    code = call.data.split(":", 1)[1]
    db = load_db()
    item = db.get(code)

    if not item or item.get("type") != "series":
        await call.answer("❌ Topilmadi", show_alert=True)
        return

    caption = f"{(item.get('poster_caption') or '').strip()}\n\n🆔 Kod: {code}".strip()
    msg = await bot.send_photo(CHANNEL2_ID, item["poster_file_id"], caption=caption, reply_markup=channel_series_kb(code))
    item["channel_msg_id"] = msg.message_id
    db[code] = item
    save_db(db)

    await call.message.edit_text("🚀 Kanalga keeetti tog'o")
    await call.answer()

# ================== QIDIRISH (KOD) ==================
@dp.message_handler(lambda m: m.text and m.text.strip().isdigit())
async def search_movie(message: types.Message):
    kb = admin_menu() if is_admin(message.from_user.id) else user_menu()

    if not await check_subscription(message.from_user.id):
        await message.answer("❗ Avval kanalga obuna bo‘ling", reply_markup=subscribe_kb())
        return

    db = load_db()
    code = message.text.strip()
    item = db.get(code)

    if not item:
        await message.answer("❌ Bunday kodli kino topilmadi", reply_markup=kb)
        return

    update_stats(message.from_user.id)

    if item.get("type") == "movie":
        # 1 martalik token
        token = str(random.randint(100000, 999999))
        last_movie_request[message.from_user.id] = code
        last_watch_token[message.from_user.id] = token

        await message.answer_photo(
            item["post_file_id"],
            item.get("post_caption", ""),
            reply_markup=movie_watch_kb(code, token),
            protect_content=True
        )
        return

    # serial: bot ichida “Barcha qismlari”
    await message.answer_photo(
        item["poster_file_id"],
        item.get("poster_caption", ""),
        reply_markup=types.InlineKeyboardMarkup().add(
            types.InlineKeyboardButton("📺 Barcha qismlari", callback_data=f"series_private:{code}")
        ),
        protect_content=True
    )

# ================== FILMNI KO‘RISH (YAKKA) ==================
# Eski watch_ tugmalar (agar qolib ketsa) — doim eskirgan
@dp.callback_query_handler(lambda c: c.data.startswith("watch_"))
async def watch_old(call: types.CallbackQuery):
    await call.answer(
        "❗ Tugma eskirgan. Faqat oxirgi so'ralgan filmni ko'rishingiz mumkin. "
        "Ushbu filmni ko'rish uchun esa kod orqali qayta qidiring yoki "
        "kanaldagi bu film posti ostidagi ko'rish tugmasini bosing ",
        show_alert=True
    )

# Yangi: 1 martalik
@dp.callback_query_handler(lambda c: c.data.startswith("watch2_"))
async def watch_movie(call: types.CallbackQuery):
    parts = call.data.split("_", 2)  # watch2_<code>_<token>
    if len(parts) != 3:
        await call.answer("❌ Topilmadi", show_alert=True)
        return

    code = parts[1]
    token = parts[2]

    if last_movie_request.get(call.from_user.id) != code or last_watch_token.get(call.from_user.id) != token:
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

    db = load_db()
    item = db.get(code)
    if not item or item.get("type") != "movie":
        await call.answer("❌ Topilmadi", show_alert=True)
        return

    await bot.send_video(call.from_user.id, item["video_file_id"], protect_content=True)

    # ENDI TUGMA ESKIRADI (1 martalik)
    last_watch_token.pop(call.from_user.id, None)

    await call.answer()

# ================== SERIALNI USERGA YUBORISH (kanalga emas) ==================
async def send_series_to_user(user_id: int, code: str):
    if not await check_subscription(user_id):
        await bot.send_message(user_id, "❗ Avval kanalga obuna bo‘ling", reply_markup=subscribe_kb())
        return

    db = load_db()
    item = db.get(code)
    if not item or item.get("type") != "series":
        await bot.send_message(user_id, "❌ Bunday kodli kino topilmadi", reply_markup=user_menu())
        return

    ep_nums = _sorted_episode_numbers(item)
    if not ep_nums:
        await bot.send_message(user_id, "❌ Qismlar topilmadi", reply_markup=user_menu())
        return

    # Kanal2 dagi o‘sha post nusxasini userga yuboramiz
    ch_msg_id = item.get("channel_msg_id")
    if ch_msg_id:
        await bot.copy_message(
            chat_id=user_id,
            from_chat_id=CHANNEL2_ID,
            message_id=ch_msg_id,
            reply_markup=series_eps_kb(code, ep_nums),
        )
    else:
        await bot.send_photo(
            chat_id=user_id,
            photo=item["poster_file_id"],
            caption=item.get("poster_caption", ""),
            reply_markup=series_eps_kb(code, ep_nums),
            protect_content=True
        )

@dp.callback_query_handler(lambda c: c.data.startswith("series_private:"))
async def series_private_from_bot(call: types.CallbackQuery):
    code = call.data.split(":", 1)[1]
    await send_series_to_user(call.from_user.id, code)
    await call.answer()

@dp.callback_query_handler(lambda c: c.data.startswith("series_ep:"))
async def series_ep(call: types.CallbackQuery):
    _, code, ep_str = call.data.split(":")
    ep_num = int(ep_str)

    if not await check_subscription(call.from_user.id):
        await call.message.answer("❗ Avval kanalga obuna bo‘ling", reply_markup=subscribe_kb())
        await call.answer()
        return

    db = load_db()
    item = db.get(code)
    if not item or item.get("type") != "series":
        await call.answer("❌ Topilmadi", show_alert=True)
        return

    ep = (item.get("episodes", {}) or {}).get(str(ep_num))
    if not ep:
        await call.answer("❌ Topilmadi", show_alert=True)
        return

    cap = _episode_user_caption(ep_num, (ep or {}).get("title", ""))
    await bot.send_video(call.from_user.id, ep["video_file_id"], caption=cap, protect_content=True)
    await call.answer()

# ================== STATISTIKA ==================
def stats_text():
    stats = load_stats()
    db = load_db()
    movies_count = sum(1 for v in db.values() if v.get("type") == "movie")
    series_count = sum(1 for v in db.values() if v.get("type") == "series")
    return (
        "📊 <b>Bot statistikasi</b>\n\n"
        f"👥 Userlar: <b>{len(stats.get('users', []))}</b>\n"
        f"🎬 Filmlar: <b>{movies_count}</b>\n"
        f"📺 Seriallar: <b>{series_count}</b>\n"
        f"📥 Bugun so‘rovlar: <b>{stats.get('today', {}).get('count', 0)}</b>\n"
        f"🔢 Jami so‘rovlar: <b>{stats.get('total_requests', 0)}</b>"
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
    if not is_admin(message.from_user.id):
        await message.answer(
            "❌ <b>Brat siz admin emassiz!</b>\n"
            "🎬 Faqat <b>Qidiruv</b> tugmasidan foydalanishingiz mumkin.",
            reply_markup=user_menu()
        )
        return
    await message.answer(stats_text(), reply_markup=stats_kb())

@dp.callback_query_handler(lambda c: c.data == "stats_refresh")
async def refresh_stats(call: types.CallbackQuery):
    await call.message.edit_text(stats_text(), reply_markup=stats_kb())
    await call.answer()

@dp.callback_query_handler(lambda c: c.data == "stats_close")
async def close_stats(call: types.CallbackQuery):
    try:
        await call.message.delete()
    except Exception:
        pass
    await call.answer()

# ================== BACKUP ==================
@dp.message_handler(lambda m: m.text == "📦 Kino backup")
async def backup_movies(message: types.Message):
    if not is_admin(message.from_user.id):
        await message.answer(
            "❌ <b>Brat siz admin emassiz!</b>\n"
            "🎬 Faqat <b>Qidiruv</b> tugmasidan foydalanishingiz mumkin.",
            reply_markup=user_menu()
        )
        return
    if not os.path.exists(MOVIES_FILE):
        await message.answer("❌ movies.json topilmadi", reply_markup=admin_menu())
        return
    await message.answer_document(types.InputFile(MOVIES_FILE), reply_markup=admin_menu())

@dp.message_handler(lambda m: m.text == "📈 Statistika backup")
async def backup_stats(message: types.Message):
    if not is_admin(message.from_user.id):
        await message.answer(
            "❌ <b>Brat siz admin emassiz!</b>\n"
            "🎬 Faqat <b>Qidiruv</b> tugmasidan foydalanishingiz mumkin.",
            reply_markup=user_menu()
        )
        return
    if not os.path.exists(STATS_FILE):
        await message.answer("❌ statistics.json topilmadi", reply_markup=admin_menu())
        return
    await message.answer_document(types.InputFile(STATS_FILE), reply_markup=admin_menu())

# ================== O‘CHIRISH ==================
@dp.message_handler(lambda m: m.text == "🗑 O‘chirish")
async def del_btn(message: types.Message, state: FSMContext):
    if not is_admin(message.from_user.id):
        await message.answer(
            "❌ <b>Brat siz admin emassiz!</b>\n"
            "🎬 Faqat <b>Qidiruv</b> tugmasidan foydalanishingiz mumkin.",
            reply_markup=user_menu()
        )
        return
    await state.finish()
    await message.answer("🗑 Koddi ayting tog'o", reply_markup=admin_menu())
    await DeleteFlow.code.set()

@dp.message_handler(state=DeleteFlow.code)
async def delete_item(message: types.Message, state: FSMContext):
    code = (message.text or "").strip()
    if not code.isdigit():
        await message.answer("🗑 Koddi ayting tog'o", reply_markup=admin_menu())
        return

    db = load_db()
    item = db.get(code)
    if not item:
        await message.answer("❌ Bunaqa kino o'zi yo'q tog'o", reply_markup=admin_menu())
        await state.finish()
        return

    msg_id = item.get("channel_msg_id")
    if msg_id:
        try:
            await bot.delete_message(CHANNEL2_ID, msg_id)
        except Exception:
            pass

    del db[code]
    save_db(db)

    await message.answer(f"🗑 O'chirib tashadim tog'o\n🆔 Kod: {code}", reply_markup=admin_menu())
    await state.finish()

# ================== TAHRIRLASH ==================
def edit_type_kb():
    kb = types.InlineKeyboardMarkup(row_width=2)
    kb.add(
        types.InlineKeyboardButton("🎬 Yakka film", callback_data="edit_type:movie"),
        types.InlineKeyboardButton("📺 Serial", callback_data="edit_type:series"),
    )
    return kb

def edit_movie_kb(code: str):
    kb = types.InlineKeyboardMarkup(row_width=1)
    kb.add(
        types.InlineKeyboardButton("♻️ Kanal1 postni yuboring", callback_data=f"edit_movie_post:{code}"),
        types.InlineKeyboardButton("🎥 Kanal1 video yuboring", callback_data=f"edit_movie_video:{code}"),
        types.InlineKeyboardButton("🗑 O‘chirish", callback_data=f"edit_delete:{code}")
    )
    return kb

def edit_series_kb(code: str):
    kb = types.InlineKeyboardMarkup(row_width=1)
    kb.add(
        types.InlineKeyboardButton("♻️ Kanal1 postni yuboring", callback_data=f"edit_series_post:{code}"),
        types.InlineKeyboardButton("➕ Yangi qism (video yuboring)", callback_data=f"series_add:{code}"),
        types.InlineKeyboardButton("🔁 Qismni almashtirish (video yuboring)", callback_data=f"series_replace:{code}"),
        types.InlineKeyboardButton("🗑 Qismni o‘chirish", callback_data=f"series_del:{code}"),
        types.InlineKeyboardButton("🗑 Serialni o‘chirish", callback_data=f"edit_delete:{code}")
    )
    return kb

@dp.message_handler(lambda m: m.text == "✏️ Tahrirlash")
async def edit_start(message: types.Message, state: FSMContext):
    if not is_admin(message.from_user.id):
        await message.answer(
            "❌ <b>Brat siz admin emassiz!</b>\n"
            "🎬 Faqat <b>Qidiruv</b> tugmasidan foydalanishingiz mumkin.",
            reply_markup=user_menu()
        )
        return
    await state.finish()
    await message.answer("Nimani tahrirlaymiz?", reply_markup=edit_type_kb())
    await EditFlow.choose_type.set()

@dp.callback_query_handler(lambda c: c.data.startswith("edit_type:"), state=EditFlow.choose_type)
async def edit_choose_type(call: types.CallbackQuery, state: FSMContext):
    typ = call.data.split(":", 1)[1]
    await state.update_data(edit_type=typ)
    await call.message.edit_text("🆔 Koddi ayting tog'o")
    await EditFlow.choose_code.set()
    await call.answer()

@dp.message_handler(state=EditFlow.choose_code)
async def edit_choose_code(message: types.Message, state: FSMContext):
    code = (message.text or "").strip()
    if not code.isdigit():
        await message.answer("🆔 Koddi ayting tog'o", reply_markup=admin_menu())
        return

    db = load_db()
    data = await state.get_data()
    typ = data.get("edit_type")
    item = db.get(code)

    if not item or item.get("type") != typ:
        await message.answer("❌ Bunaqa kino o'zi yo'q tog'o", reply_markup=admin_menu())
        await state.finish()
        return

    await state.update_data(code=code)
    if typ == "movie":
        await message.answer("🎬 Tahrirlash:", reply_markup=edit_movie_kb(code))
    else:
        await message.answer("📺 Tahrirlash:", reply_markup=edit_series_kb(code))
    await EditFlow.choose_action.set()

@dp.callback_query_handler(lambda c: c.data.startswith("edit_movie_post:"), state=EditFlow.choose_action)
async def edit_movie_post(call: types.CallbackQuery, state: FSMContext):
    code = call.data.split(":", 1)[1]
    await state.update_data(pending=("movie_post", code))
    await call.message.answer("♻️ Kanal1 (baza)dagi <b>yangilangan postni</b> forward qiling.", reply_markup=admin_menu())
    await EditFlow.await_forward.set()
    await call.answer()

@dp.callback_query_handler(lambda c: c.data.startswith("edit_movie_video:"), state=EditFlow.choose_action)
async def edit_movie_video(call: types.CallbackQuery, state: FSMContext):
    code = call.data.split(":", 1)[1]
    await state.update_data(pending=("movie_video", code))
    await call.message.answer("🎥 Kanal1 (baza)dagi <b>yangilangan videoni</b> forward qiling.", reply_markup=admin_menu())
    await EditFlow.await_forward.set()
    await call.answer()

@dp.callback_query_handler(lambda c: c.data.startswith("edit_series_post:"), state=EditFlow.choose_action)
async def edit_series_post(call: types.CallbackQuery, state: FSMContext):
    code = call.data.split(":", 1)[1]
    await state.update_data(pending=("series_post", code))
    await call.message.answer("♻️ Kanal1 (baza)dagi <b>yangilangan poster postni</b> forward qiling.", reply_markup=admin_menu())
    await EditFlow.await_forward.set()
    await call.answer()

@dp.callback_query_handler(lambda c: c.data.startswith("series_add:"), state=EditFlow.choose_action)
async def edit_series_add(call: types.CallbackQuery, state: FSMContext):
    code = call.data.split(":", 1)[1]
    await state.update_data(pending=("series_add", code))
    await call.message.answer("➕ Kanal1 dan videoni forward qiling.\nMasalan: <b>1 Yura davri 3</b>", reply_markup=admin_menu())
    await EditFlow.await_forward.set()
    await call.answer()

@dp.callback_query_handler(lambda c: c.data.startswith("series_replace:"), state=EditFlow.choose_action)
async def edit_series_replace(call: types.CallbackQuery, state: FSMContext):
    code = call.data.split(":", 1)[1]
    await state.update_data(pending=("series_replace", code))
    await call.message.answer("🔁 Kanal1 dan videoni forward qiling.\nMasalan: <b>1 Yura davri 3</b>", reply_markup=admin_menu())
    await EditFlow.await_forward.set()
    await call.answer()

@dp.callback_query_handler(lambda c: c.data.startswith("series_del:"), state=EditFlow.choose_action)
async def edit_series_del(call: types.CallbackQuery, state: FSMContext):
    code = call.data.split(":", 1)[1]
    await state.update_data(pending=("series_del", code))
    await call.message.answer("🗑 Qaysi qisimni o‘chiramiz? (raqam yuboring, masalan: 1)", reply_markup=admin_menu())
    await EditFlow.await_ep_delete.set()
    await call.answer()

@dp.callback_query_handler(lambda c: c.data.startswith("edit_delete:"), state=EditFlow.choose_action)
async def edit_delete(call: types.CallbackQuery, state: FSMContext):
    code = call.data.split(":", 1)[1]
    db = load_db()
    item = db.get(code)
    if not item:
        await call.answer("❌ Topilmadi", show_alert=True)
        await state.finish()
        return

    msg_id = item.get("channel_msg_id")
    if msg_id:
        try:
            await bot.delete_message(CHANNEL2_ID, msg_id)
        except Exception:
            pass

    del db[code]
    save_db(db)
    await call.message.answer(f"🗑 O'chirib tashadim tog'o\n🆔 Kod: {code}", reply_markup=admin_menu())
    await state.finish()
    await call.answer()

@dp.message_handler(state=EditFlow.await_ep_delete)
async def edit_series_del_number(message: types.Message, state: FSMContext):
    text = (message.text or "").strip()
    if not text.isdigit():
        await message.answer("🗑 Qaysi qisimni o‘chiramiz? (raqam yuboring, masalan: 1)", reply_markup=admin_menu())
        return

    ep_num = int(text)
    data = await state.get_data()
    pending = data.get("pending")

    if not pending or pending[0] != "series_del":
        await message.answer("❎ Bekor qilindi tog'o", reply_markup=admin_menu())
        await state.finish()
        return

    code = pending[1]
    db = load_db()
    item = db.get(code)
    if not item or item.get("type") != "series":
        await message.answer("❌ Bunaqa kino o'zi yo'q tog'o", reply_markup=admin_menu())
        await state.finish()
        return

    eps = item.get("episodes", {}) or {}
    if str(ep_num) not in eps:
        await message.answer("❌ Bunaqa qisim yo'q tog'o", reply_markup=admin_menu())
        return

    del eps[str(ep_num)]
    item["episodes"] = eps
    db[code] = item
    save_db(db)

    await message.answer(f"🗑 O'chirib tashadim tog'o\n🆔 Kod: {code}", reply_markup=admin_menu())
    await state.finish()

@dp.message_handler(state=EditFlow.await_forward, content_types=types.ContentType.ANY)
async def edit_receive_forward(message: types.Message, state: FSMContext):
    if not await _is_forward_from_base(message):
        await message.answer("❗ Iltimos, <b>Kanal1 (baza)</b>dan forward qiling.", reply_markup=admin_menu())
        return

    data = await state.get_data()
    pending = data.get("pending")
    if not pending:
        await message.answer("❎ Bekor qilindi tog'o", reply_markup=admin_menu())
        await state.finish()
        return

    action, code = pending
    db = load_db()
    item = db.get(code)

    if not item:
        await message.answer("❌ Bunaqa kino o'zi yo'q tog'o", reply_markup=admin_menu())
        await state.finish()
        return

    # MOVIE POST
    if action == "movie_post":
        if message.content_type != types.ContentType.PHOTO:
            await message.answer("❗ Rasm (photo) forward qiling.", reply_markup=admin_menu())
            return

        new_photo = message.photo[-1].file_id
        new_caption = message.caption or ""

        ch_msg_id = item.get("channel_msg_id")
        if ch_msg_id:
            old_with_code = f"{(item.get('post_caption') or '').strip()}\n\n🆔 Kod: {code}"
            final_caption = _ensure_code_line_kept(new_caption, old_with_code, code)
            try:
                media = types.InputMediaPhoto(media=new_photo, caption=final_caption, parse_mode="HTML")
                await bot.edit_message_media(CHANNEL2_ID, ch_msg_id, media=media, reply_markup=channel_movie_kb(code))
            except Exception:
                try:
                    await bot.edit_message_caption(CHANNEL2_ID, ch_msg_id, caption=final_caption, reply_markup=channel_movie_kb(code))
                except Exception:
                    pass

        item["post_file_id"] = new_photo
        item["post_caption"] = new_caption
        db[code] = item
        save_db(db)

        await message.answer("✅ Yangilandi tog'o", reply_markup=admin_menu())
        await state.finish()
        return

    # MOVIE VIDEO
    if action == "movie_video":
        if message.content_type != types.ContentType.VIDEO:
            await message.answer("❗ Video forward qiling.", reply_markup=admin_menu())
            return

        if _duplicate_video_exists(db, message.video.file_unique_id):
            await message.answer("❗ Bu kino borku tog'o", reply_markup=admin_menu())
            return

        item["video_file_id"] = message.video.file_id
        item["video_unique_id"] = message.video.file_unique_id
        db[code] = item
        save_db(db)

        await message.answer("✅ Yangilandi tog'o", reply_markup=admin_menu())
        await state.finish()
        return

    # SERIES POST
    if action == "series_post":
        if message.content_type != types.ContentType.PHOTO:
            await message.answer("❗ Rasm (photo) forward qiling.", reply_markup=admin_menu())
            return

        new_photo = message.photo[-1].file_id
        new_caption = message.caption or ""

        ch_msg_id = item.get("channel_msg_id")
        if ch_msg_id:
            old_with_code = f"{(item.get('poster_caption') or '').strip()}\n\n🆔 Kod: {code}"
            final_caption = _ensure_code_line_kept(new_caption, old_with_code, code)
            try:
                media = types.InputMediaPhoto(media=new_photo, caption=final_caption, parse_mode="HTML")
                await bot.edit_message_media(CHANNEL2_ID, ch_msg_id, media=media, reply_markup=channel_series_kb(code))
            except Exception:
                try:
                    await bot.edit_message_caption(CHANNEL2_ID, ch_msg_id, caption=final_caption, reply_markup=channel_series_kb(code))
                except Exception:
                    pass

        item["poster_file_id"] = new_photo
        item["poster_caption"] = new_caption
        db[code] = item
        save_db(db)

        await message.answer("✅ Yangilandi tog'o", reply_markup=admin_menu())
        await state.finish()
        return

    # SERIES ADD / REPLACE
    if action in ("series_add", "series_replace"):
        if message.content_type != types.ContentType.VIDEO:
            await message.answer("❗ Video forward qiling.", reply_markup=admin_menu())
            return

        ep_num, ep_title = _parse_episode_caption(message.caption or "")
        if ep_num is None:
            await message.answer("❗ Video captionida qism raqimi yo‘q.\nMasalan: <b>1 Yura davri 3</b>", reply_markup=admin_menu())
            return

        if _duplicate_video_exists(db, message.video.file_unique_id):
            await message.answer("❗ Bu kino borku tog'o", reply_markup=admin_menu())
            return

        eps = item.get("episodes", {}) or {}
        exists = str(ep_num) in eps

        if action == "series_add" and exists:
            await message.answer("❗ Bu qisim bor tog'o. Almashtirish tanlang.", reply_markup=admin_menu())
            return
        if action == "series_replace" and not exists:
            await message.answer("❗ Bu qisim yo'q tog'o. Yangi qisim qo‘shish tanlang.", reply_markup=admin_menu())
            return

        eps[str(ep_num)] = {
            "video_file_id": message.video.file_id,
            "video_unique_id": message.video.file_unique_id,
            "title": (ep_title or "").strip()
        }
        item["episodes"] = eps
        db[code] = item
        save_db(db)

        await message.answer("✅ Yangilandi tog'o", reply_markup=admin_menu())
        await state.finish()
        return

    await message.answer("❎ Bekor qilindi tog'o", reply_markup=admin_menu())
    await state.finish()

# ================== OBUNA TEKSHIR ==================
@dp.callback_query_handler(lambda c: c.data == "check_sub")
async def recheck(call: types.CallbackQuery):
    if await check_subscription(call.from_user.id):
        await call.message.edit_text("✅ Obuna tasdiqlandi. Kod yuboring.")
    else:
        await call.answer("❌ Hali obuna bo'lmadingizku 😕", show_alert=True)

# ================== FALLBACK (hech qachon jim emas) ==================
@dp.message_handler(content_types=types.ContentType.ANY, state="*")
async def fallback_all(message: types.Message):
    # User jim bo‘lmasin:
    if not is_admin(message.from_user.id):
        await message.answer(
            "❌ <b>Brat siz admin emassiz!</b>\n"
            "🎬 Faqat <b>Qidiruv</b> tugmasidan foydalanishingiz mumkin.",
            reply_markup=user_menu()
        )
    else:
        await message.answer("❌ Noto'g'ri buyruq tog'o.\n👇 Menudan foydalaning.", reply_markup=admin_menu())

# ================== STARTUP ==================
async def on_startup(dp):
    await bot.delete_webhook(drop_pending_updates=True)

if __name__ == "__main__":
    executor.start_polling(
        dp,
        skip_updates=True,
        on_startup=on_startup
    )
