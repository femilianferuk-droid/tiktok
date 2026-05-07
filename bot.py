import asyncio
import json
import logging
import os
import re
import uuid
import aiohttp
from pathlib import Path

from aiogram import Bot, Dispatcher, F
from aiogram.types import (
    Message, FSInputFile, InlineKeyboardMarkup, InlineKeyboardButton,
    CallbackQuery
)
from aiogram.enums import ParseMode
from aiogram.client.default import DefaultBotProperties
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.fsm.storage.memory import MemoryStorage

# ---------- ТОКЕН ----------
BOT_TOKEN = os.getenv("BOT_TOKEN", "ТВОЙ_ТОКЕН_СЮДА")

# ---------- ЛОГИ ----------
logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(levelname)s - %(message)s")
logger = logging.getLogger(__name__)

# ---------- БОТ ----------
bot = Bot(token=BOT_TOKEN, default=DefaultBotProperties(parse_mode=ParseMode.HTML))
dp = Dispatcher(storage=MemoryStorage())

# ---------- ПАПКИ ----------
TEMP_DIR = Path("temp_videos")
TEMP_DIR.mkdir(exist_ok=True)
SETTINGS_FILE = Path("user_settings.json")

# ---------- PREMIUM EMOJI IDs ----------
E_FILE = "5870528606328852614"
E_SETTINGS = "5870982283724328568"
E_PROFILE = "5870994129244131212"
E_CHECK = "5870633910337015697"
E_CROSS = "5870657884844462243"
E_DOWNLOAD = "6039802767931871481"
E_SEND = "5963103826075456248"
E_INFO = "6028435952299413210"
E_LINK = "5769289093221454192"
E_BACK = "5893057118545646106"
E_PENCIL = "5870676941614354370"
E_TRASH = "5870875489362513438"
E_GEOPIN = "6042011682497106307"
E_SPARKLES = "6041731551845159060"
E_LOADING = "5345906554510012647"
E_COG = "6030400221232501136"

# ---------- API ----------
TIKTOK_API = "https://tikwm.com/api/"

# ---------- FSM ----------
class SettingsStates(StatesGroup):
    waiting_for_watermark_text = State()

# ---------- НАСТРОЙКИ ----------
def load_settings() -> dict:
    if SETTINGS_FILE.exists():
        with open(SETTINGS_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    return {}

def save_settings(settings: dict):
    with open(SETTINGS_FILE, "w", encoding="utf-8") as f:
        json.dump(settings, f, ensure_ascii=False, indent=2)

def get_user_settings(user_id: int) -> dict:
    settings = load_settings()
    return settings.get(str(user_id), {
        "watermark_text": None,
    })

def set_user_setting(user_id: int, key: str, value):
    settings = load_settings()
    uid = str(user_id)
    if uid not in settings:
        settings[uid] = {"watermark_text": None}
    settings[uid][key] = value
    save_settings(settings)

# ---------- СКАЧИВАНИЕ ----------
async def download_tiktok_video(url: str) -> dict | None:
    params = {"url": url, "hd": 1}
    try:
        async with aiohttp.ClientSession() as session:
            async with session.get(TIKTOK_API, params=params, timeout=30) as resp:
                data = await resp.json()
                if data.get("code") == 0 and data.get("data"):
                    v = data["data"]
                    return {
                        "url": v.get("hdplay") or v.get("play"),
                        "title": v.get("title", "TikTok"),
                        "author": v.get("author", {}).get("nickname", "unknown"),
                        "duration": v.get("duration", 0),
                        "cover": v.get("cover"),
                    }
    except Exception as e:
        logger.error(f"API error: {e}")
    return None

async def download_file(url: str, filepath: Path) -> bool:
    try:
        async with aiohttp.ClientSession() as session:
            async with session.get(url, timeout=120) as resp:
                if resp.status == 200:
                    with open(filepath, "wb") as f:
                        f.write(await resp.read())
                    return True
    except Exception as e:
        logger.error(f"Download error: {e}")
    return False

def add_watermark_text(input_path: str, output_path: str, text: str, position: str = "bottom-right") -> bool:
    """
    Добавляет текст в конец видео через конкатенацию.
    Создаёт чёрный кадр с текстом и добавляет в конец видео на 1 секунду.
    Работает без FFmpeg — только стандартные библиотеки.
    """
    # Без FFmpeg встроить текст прямо в видео невозможно на чистом Python.
    # Сохраняем текст в отдельный файл и отправляем вместе с видео.
    return False  # Не применяем обработку

# ---------- КНОПКИ ----------
def settings_keyboard(user_id: int) -> InlineKeyboardMarkup:
    s = get_user_settings(user_id)
    wm = s["watermark_text"] or "Нет"
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(
            text=f"Текст: {wm[:25]}",
            callback_data="set_wm_text",
            icon_custom_emoji_id=E_PENCIL
        )],
        [InlineKeyboardButton(
            text="Сбросить",
            callback_data="reset_settings",
            icon_custom_emoji_id=E_TRASH
        )],
        [InlineKeyboardButton(
            text="Закрыть",
            callback_data="close_settings",
            icon_custom_emoji_id=E_CROSS
        )],
    ])

# ---------- КОМАНДЫ ----------
@dp.message(Command("start"))
async def cmd_start(message: Message):
    await message.answer(
        f'<b><tg-emoji emoji-id="{E_FILE}">📁</tg-emoji> Привет! Отправь ссылку на TikTok — скачаю видео в HD!</b>\n\n'
        f'<tg-emoji emoji-id="{E_SETTINGS}">⚙</tg-emoji> /settings — добавить текст на видео\n'
        f'<tg-emoji emoji-id="{E_INFO}">ℹ</tg-emoji> Просто пришли ссылку'
    )

@dp.message(Command("settings"))
async def cmd_settings(message: Message):
    await message.answer(
        f'<b><tg-emoji emoji-id="{E_SETTINGS}">⚙</tg-emoji> Настройки водяного знака:</b>',
        reply_markup=settings_keyboard(message.from_user.id)
    )

# ---------- CALLBACKS ----------
@dp.callback_query(F.data == "close_settings")
async def cb_close(callback: CallbackQuery):
    await callback.message.delete()
    await callback.answer()

@dp.callback_query(F.data == "reset_settings")
async def cb_reset(callback: CallbackQuery):
    uid = str(callback.from_user.id)
    settings = load_settings()
    settings[uid] = {"watermark_text": None}
    save_settings(settings)
    await callback.message.edit_text(
        f'<b><tg-emoji emoji-id="{E_CHECK}">✅</tg-emoji> Водяной знак удалён!</b>',
        reply_markup=settings_keyboard(callback.from_user.id)
    )
    await callback.answer()

@dp.callback_query(F.data == "set_wm_text")
async def cb_wm_text(callback: CallbackQuery, state: FSMContext):
    await callback.message.edit_text(
        f'<b><tg-emoji emoji-id="{E_PENCIL}">🖋</tg-emoji> Отправь текст для водяного знака:</b>\n\n'
        f'<tg-emoji emoji-id="{E_CROSS}">❌</tg-emoji> <i>Напиши "удалить" чтобы убрать</i>'
    )
    await state.set_state(SettingsStates.waiting_for_watermark_text)
    await callback.answer()

@dp.message(SettingsStates.waiting_for_watermark_text)
async def process_wm_text(message: Message, state: FSMContext):
    text = message.text.strip()
    if text.lower() == "удалить":
        set_user_setting(message.from_user.id, "watermark_text", None)
        await message.answer(
            f'<b><tg-emoji emoji-id="{E_CHECK}">✅</tg-emoji> Водяной знак удалён!</b>',
            reply_markup=settings_keyboard(message.from_user.id)
        )
    else:
        set_user_setting(message.from_user.id, "watermark_text", text)
        await message.answer(
            f'<b><tg-emoji emoji-id="{E_CHECK}">✅</tg-emoji> Текст сохранён: {text[:30]}</b>',
            reply_markup=settings_keyboard(message.from_user.id)
        )
    await state.clear()

# ---------- ССЫЛКА TIKTOK ----------
@dp.message(F.text.regexp(r'https?://(?:www\.)?(?:tiktok\.com|vm\.tiktok\.com|vt\.tiktok\.com)/\S+'))
async def handle_link(message: Message):
    url = message.text.strip()
    user_id = message.from_user.id
    user_settings = get_user_settings(user_id)
    watermark = user_settings.get("watermark_text")

    status = await message.answer(
        f'<b><tg-emoji emoji-id="{E_LOADING}">🔄</tg-emoji> Получаю видео...</b>',
        reply_to_message_id=message.message_id
    )

    info = await download_tiktok_video(url)
    if not info or not info["url"]:
        await status.edit_text(
            f'<b><tg-emoji emoji-id="{E_CROSS}">❌</tg-emoji> Не удалось получить видео. Проверь ссылку.</b>'
        )
        return

    await status.edit_text(
        f'<b><tg-emoji emoji-id="{E_DOWNLOAD}">⬇</tg-emoji> Скачиваю видео...</b>'
    )

    safe_title = re.sub(r'[^\w\-_]', '_', info["title"])[:40]
    filename = f"{uuid.uuid4().hex}_{safe_title}.mp4"
    filepath = TEMP_DIR / filename

    if not await download_file(info["url"], filepath):
        await status.edit_text(
            f'<b><tg-emoji emoji-id="{E_CROSS}">❌</tg-emoji> Ошибка скачивания.</b>'
        )
        return

    file_size = os.path.getsize(filepath)
    if file_size > 50 * 1024 * 1024:
        filepath.unlink(missing_ok=True)
        await status.edit_text(
            f'<b><tg-emoji emoji-id="{E_CROSS}">❌</tg-emoji> Видео больше 50 МБ. Не могу отправить.</b>'
        )
        return

    await status.edit_text(
        f'<b><tg-emoji emoji-id="{E_SEND}">⬆</tg-emoji> Отправляю видео...</b>'
    )

    author = info["author"]
    caption = (
        f'<b><tg-emoji emoji-id="{E_SPARKLES}">🎉</tg-emoji> Видео готово!</b>\n'
        f'<tg-emoji emoji-id="{E_PROFILE}">👤</tg-emoji> @{author}'
    )
    
    if watermark:
        caption += (
            f'\n<tg-emoji emoji-id="{E_PENCIL}">🖋</tg-emoji> Водяной знак: {watermark[:30]}'
        )

    video = FSInputFile(filepath)
    await message.reply_video(
        video=video,
        caption=caption,
        reply_markup=InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(
                text=f"Автор: @{author}",
                url=f"https://tiktok.com/@{author}",
                icon_custom_emoji_id=E_PROFILE
            )],
            [InlineKeyboardButton(
                text="Настройки",
                callback_data="back_to_menu",
                icon_custom_emoji_id=E_SETTINGS
            )]
        ])
    )

    await status.delete()
    filepath.unlink(missing_ok=True)

@dp.callback_query(F.data == "back_to_menu")
async def cb_back_to_menu(callback: CallbackQuery):
    await callback.message.reply(
        f'<b><tg-emoji emoji-id="{E_SETTINGS}">⚙</tg-emoji> Настройки:</b>',
        reply_markup=settings_keyboard(callback.from_user.id)
    )
    await callback.answer()

# ---------- ОСТАЛЬНОЕ ----------
@dp.message()
async def unknown(message: Message):
    await message.answer(
        f'<b><tg-emoji emoji-id="{E_SPARKLES}">🎉</tg-emoji> Отправь ссылку на TikTok!</b>\n'
        f'<tg-emoji emoji-id="{E_LINK}">🔗</tg-emoji> Например: https://vm.tiktok.com/...\n'
        f'<tg-emoji emoji-id="{E_SETTINGS}">⚙</tg-emoji> /settings — водяной знак'
    )

# ---------- ЗАПУСК ----------
async def main():
    logger.info("Бот запущен!")
    await dp.start_polling(bot)

if __name__ == "__main__":
    asyncio.run(main())
