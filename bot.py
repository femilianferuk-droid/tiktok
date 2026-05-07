import asyncio
import json
import logging
import os
import re
import uuid
import aiohttp
from pathlib import Path
from io import BytesIO

from aiogram import Bot, Dispatcher, F
from aiogram.types import (
    Message, FSInputFile, InlineKeyboardMarkup, InlineKeyboardButton,
    CallbackQuery, InputFile, BufferedInputFile
)
from aiogram.enums import ParseMode
from aiogram.client.default import DefaultBotProperties
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.fsm.storage.memory import MemoryStorage

from PIL import Image, ImageColor

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
TEMP_STICKERS = Path("temp_stickers")
TEMP_STICKERS.mkdir(exist_ok=True)
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
E_BRUSH = "6050679691004612757"
E_EYE = "6037397706505195857"
E_TAG = "5886285355279193209"
E_COG = "6030400221232501136"
E_PHOTO = "6035128606563241721"
E_COLOR = "5778479949572738874"
E_STICKER = "6032644646587338669"

# ---------- API ----------
TIKTOK_API = "https://tikwm.com/api/"

# ---------- FSM ----------
class StickerStates(StatesGroup):
    waiting_for_sticker = State()
    waiting_for_color = State()

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
        "default_color": "#FF0000",
    })

def set_user_setting(user_id: int, key: str, value):
    settings = load_settings()
    uid = str(user_id)
    if uid not in settings:
        settings[uid] = {"default_color": "#FF0000"}
    settings[uid][key] = value
    save_settings(settings)

# ---------- СКАЧИВАНИЕ TIKTOK ----------
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

# ---------- ОБРАБОТКА СТИКЕРОВ ----------
def change_sticker_color(image_data: bytes, color_hex: str) -> bytes:
    """Меняет цвет стикера/эмодзи на указанный"""
    img = Image.open(BytesIO(image_data)).convert("RGBA")
    
    # Создаём маску из альфа-канала
    r, g, b, a = img.split()
    
    # Новый цвет
    new_color = ImageColor.getrgb(color_hex)
    
    # Создаём новый слой с нужным цветом
    colored = Image.new("RGBA", img.size, (*new_color, 255))
    
    # Применяем альфа-маску
    colored.putalpha(a)
    
    # Сохраняем в bytes
    output = BytesIO()
    colored.save(output, format="PNG", optimize=True)
    return output.getvalue()

def create_tg_sticker(image_data: bytes, emoji: str = "⭐") -> bytes:
    """Конвертирует изображение в формат стикера Telegram (512x512 PNG)"""
    img = Image.open(BytesIO(image_data)).convert("RGBA")
    
    # Ресайз до 512px (максимум для стикеров)
    max_size = 512
    ratio = max_size / max(img.size)
    if ratio < 1:
        new_size = (int(img.width * ratio), int(img.height * ratio))
        img = img.resize(new_size, Image.LANCZOS)
    
    # Создаём холст 512x512
    canvas = Image.new("RGBA", (512, 512), (0, 0, 0, 0))
    
    # Центрируем
    x = (512 - img.width) // 2
    y = (512 - img.height) // 2
    canvas.paste(img, (x, y), img)
    
    output = BytesIO()
    canvas.save(output, format="PNG", optimize=True)
    return output.getvalue()

# ---------- КНОПКИ ----------
def main_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(
            text="Скачать TikTok",
            callback_data="mode_tiktok",
            icon_custom_emoji_id=E_FILE
        )],
        [InlineKeyboardButton(
            text="Сменить цвет стикера",
            callback_data="mode_sticker",
            icon_custom_emoji_id=E_STICKER
        )],
        [InlineKeyboardButton(
            text="Настройки",
            callback_data="open_settings",
            icon_custom_emoji_id=E_SETTINGS
        )],
    ])

def settings_keyboard(user_id: int) -> InlineKeyboardMarkup:
    s = get_user_settings(user_id)
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(
            text=f"Цвет по умолчанию: {s['default_color']}",
            callback_data="set_default_color",
            icon_custom_emoji_id=E_COLOR
        )],
        [InlineKeyboardButton(
            text="Назад",
            callback_data="back_to_main",
            icon_custom_emoji_id=E_BACK
        )],
    ])

def color_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="Красный", callback_data="color_#FF0000", icon_custom_emoji_id=E_COLOR),
         InlineKeyboardButton(text="Синий", callback_data="color_#0000FF", icon_custom_emoji_id=E_COLOR)],
        [InlineKeyboardButton(text="Зелёный", callback_data="color_#00FF00", icon_custom_emoji_id=E_COLOR),
         InlineKeyboardButton(text="Жёлтый", callback_data="color_#FFD700", icon_custom_emoji_id=E_COLOR)],
        [InlineKeyboardButton(text="Фиолетовый", callback_data="color_#8B00FF", icon_custom_emoji_id=E_COLOR),
         InlineKeyboardButton(text="Розовый", callback_data="color_#FF69B4", icon_custom_emoji_id=E_COLOR)],
        [InlineKeyboardButton(text="Белый", callback_data="color_#FFFFFF", icon_custom_emoji_id=E_COLOR),
         InlineKeyboardButton(text="Чёрный", callback_data="color_#000000", icon_custom_emoji_id=E_COLOR)],
        [InlineKeyboardButton(text="Свой цвет", callback_data="color_custom", icon_custom_emoji_id=E_PENCIL)],
        [InlineKeyboardButton(text="Назад", callback_data="open_settings", icon_custom_emoji_id=E_BACK)],
    ])

# ---------- КОМАНДЫ ----------
@dp.message(Command("start"))
async def cmd_start(message: Message):
    await message.answer(
        f'<b><tg-emoji emoji-id="{E_SPARKLES}">🎉</tg-emoji> Привет! Я умею:</b>\n\n'
        f'<tg-emoji emoji-id="{E_FILE}">📁</tg-emoji> Скачивать видео из TikTok в HD\n'
        f'<tg-emoji emoji-id="{E_STICKER}">🎁</tg-emoji> Менять цвет стикеров и эмодзи\n\n'
        f'<b>Выбери действие:</b>',
        reply_markup=main_keyboard()
    )

# ---------- CALLBACKS ----------
@dp.callback_query(F.data == "back_to_main")
async def cb_main(callback: CallbackQuery):
    await callback.message.edit_text(
        f'<b><tg-emoji emoji-id="{E_SPARKLES}">🎉</tg-emoji> Выбери действие:</b>',
        reply_markup=main_keyboard()
    )
    await callback.answer()

@dp.callback_query(F.data == "mode_tiktok")
async def cb_tiktok(callback: CallbackQuery):
    await callback.message.edit_text(
        f'<b><tg-emoji emoji-id="{E_LINK}">🔗</tg-emoji> Отправь ссылку на TikTok видео:</b>\n\n'
        f'<i>Поддерживаются ссылки вида:</i>\n'
        f'• https://www.tiktok.com/@user/video/...\n'
        f'• https://vm.tiktok.com/...\n'
        f'• https://vt.tiktok.com/...'
    )
    await callback.answer()

@dp.callback_query(F.data == "mode_sticker")
async def cb_sticker(callback: CallbackQuery, state: FSMContext):
    await callback.message.edit_text(
        f'<b><tg-emoji emoji-id="{E_STICKER}">🎁</tg-emoji> Отправь мне стикер или эмодзи (как картинку):</b>\n\n'
        f'<i>Я изменю его цвет на выбранный тобой.</i>'
    )
    await state.set_state(StickerStates.waiting_for_sticker)
    await callback.answer()

@dp.callback_query(F.data == "open_settings")
async def cb_settings(callback: CallbackQuery):
    await callback.message.edit_text(
        f'<b><tg-emoji emoji-id="{E_SETTINGS}">⚙</tg-emoji> Настройки:</b>',
        reply_markup=settings_keyboard(callback.from_user.id)
    )
    await callback.answer()

@dp.callback_query(F.data == "set_default_color")
async def cb_color_menu(callback: CallbackQuery):
    await callback.message.edit_text(
        f'<b><tg-emoji emoji-id="{E_COLOR}">↔</tg-emoji> Выбери цвет по умолчанию:</b>',
        reply_markup=color_keyboard()
    )
    await callback.answer()

@dp.callback_query(F.data.startswith("color_"))
async def cb_color_select(callback: CallbackQuery):
    if callback.data == "color_custom":
        await callback.message.edit_text(
            f'<b><tg-emoji emoji-id="{E_PENCIL}">🖋</tg-emoji> Отправь цвет в HEX формате:</b>\n\n'
            f'<i>Например: #FF5733 или FF5733</i>'
        )
        await callback.answer()
        return
    
    color = callback.data.replace("color_", "")
    set_user_setting(callback.from_user.id, "default_color", color)
    await callback.message.edit_text(
        f'<b><tg-emoji emoji-id="{E_CHECK}">✅</tg-emoji> Цвет по умолчанию: {color}</b>',
        reply_markup=settings_keyboard(callback.from_user.id)
    )
    await callback.answer()

# ---------- ОБРАБОТКА СТИКЕРОВ ----------
@dp.message(StickerStates.waiting_for_sticker, F.photo | F.document)
async def process_sticker_image(message: Message, state: FSMContext):
    user_id = message.from_user.id
    settings = get_user_settings(user_id)
    
    status = await message.answer(
        f'<b><tg-emoji emoji-id="{E_LOADING}">🔄</tg-emoji> Обрабатываю изображение...</b>'
    )
    
    # Скачиваем файл
    if message.photo:
        file_id = message.photo[-1].file_id
    else:
        file_id = message.document.file_id
    
    file = await bot.get_file(file_id)
    image_data = BytesIO()
    await bot.download_file(file.file_path, image_data)
    image_bytes = image_data.getvalue()
    
    # Меняем цвет
    colored_bytes = change_sticker_color(image_bytes, settings["default_color"])
    
    # Отправляем результат
    await message.reply_photo(
        photo=BufferedInputFile(colored_bytes, filename="colored_sticker.png"),
        caption=f'<b><tg-emoji emoji-id="{E_CHECK}">✅</tg-emoji> Готово! Цвет: {settings["default_color"]}</b>',
        reply_markup=InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(
                text="Сменить цвет",
                callback_data="mode_sticker",
                icon_custom_emoji_id=E_STICKER
            )],
            [InlineKeyboardButton(
                text="В меню",
                callback_data="back_to_main",
                icon_custom_emoji_id=E_BACK
            )]
        ])
    )
    
    await status.delete()
    await state.clear()

@dp.message(StickerStates.waiting_for_sticker, F.sticker)
async def process_sticker(message: Message, state: FSMContext):
    user_id = message.from_user.id
    settings = get_user_settings(user_id)
    
    status = await message.answer(
        f'<b><tg-emoji emoji-id="{E_LOADING}">🔄</tg-emoji> Обрабатываю стикер...</b>'
    )
    
    # Скачиваем стикер
    file = await bot.get_file(message.sticker.file_id)
    sticker_data = BytesIO()
    await bot.download_file(file.file_path, sticker_data)
    sticker_bytes = sticker_data.getvalue()
    
    # Меняем цвет
    colored_bytes = change_sticker_color(sticker_bytes, settings["default_color"])
    
    # Конвертируем в стикер
    sticker_png = create_tg_sticker(colored_bytes)
    
    # Отправляем как стикер
    await message.reply_document(
        document=BufferedInputFile(sticker_png, filename="sticker.png"),
        caption=f'<b><tg-emoji emoji-id="{E_CHECK}">✅</tg-emoji> Стикер перекрашен! Отправь этот файл @Stickers боту чтобы добавить в набор.</b>'
    )
    
    # И как картинку для удобства
    await message.reply_photo(
        photo=BufferedInputFile(colored_bytes, filename="preview.png"),
        reply_markup=InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(
                text="Ещё стикер",
                callback_data="mode_sticker",
                icon_custom_emoji_id=E_STICKER
            )],
            [InlineKeyboardButton(
                text="В меню",
                callback_data="back_to_main",
                icon_custom_emoji_id=E_BACK
            )]
        ])
    )
    
    await status.delete()
    await state.clear()

@dp.message(StickerStates.waiting_for_sticker)
async def process_sticker_invalid(message: Message):
    await message.answer(
        f'<b><tg-emoji emoji-id="{E_CROSS}">❌</tg-emoji> Отправь стикер или картинку!</b>'
    )

# ---------- ПРОИЗВОЛЬНЫЙ ЦВЕТ ----------
@dp.message(F.text.regexp(r'^#?[0-9a-fA-F]{6}$'))
async def process_color_hex(message: Message):
    color = message.text.strip()
    if not color.startswith("#"):
        color = "#" + color
    
    try:
        ImageColor.getrgb(color)
        set_user_setting(message.from_user.id, "default_color", color)
        await message.answer(
            f'<b><tg-emoji emoji-id="{E_CHECK}">✅</tg-emoji> Цвет изменён на {color}</b>',
            reply_markup=settings_keyboard(message.from_user.id)
        )
    except:
        await message.answer(
            f'<b><tg-emoji emoji-id="{E_CROSS}">❌</tg-emoji> Неверный HEX-цвет!</b>'
        )

# ---------- ССЫЛКА TIKTOK ----------
@dp.message(F.text.regexp(r'https?://(?:www\.)?(?:tiktok\.com|vm\.tiktok\.com|vt\.tiktok\.com)/\S+'))
async def handle_tiktok(message: Message):
    url = message.text.strip()

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
        f'<b><tg-emoji emoji-id="{E_DOWNLOAD}">⬇</tg-emoji> Скачиваю...</b>'
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
            f'<b><tg-emoji emoji-id="{E_CROSS}">❌</tg-emoji> Видео >50 МБ.</b>'
        )
        return

    await status.edit_text(
        f'<b><tg-emoji emoji-id="{E_SEND}">⬆</tg-emoji> Отправляю...</b>'
    )

    author = info["author"]
    caption = (
        f'<b><tg-emoji emoji-id="{E_SPARKLES}">🎉</tg-emoji> Готово!</b>\n'
        f'<tg-emoji emoji-id="{E_PROFILE}">👤</tg-emoji> @{author}'
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
                text="Скачать ещё",
                callback_data="mode_tiktok",
                icon_custom_emoji_id=E_DOWNLOAD
            )]
        ])
    )

    await status.delete()
    filepath.unlink(missing_ok=True)

# ---------- ОСТАЛЬНОЕ ----------
@dp.message()
async def unknown(message: Message):
    await message.answer(
        f'<b><tg-emoji emoji-id="{E_INFO}">ℹ</tg-emoji> Используй кнопки меню или отправь ссылку на TikTok.</b>\n\n'
        f'<tg-emoji emoji-id="{E_LINK}">🔗</tg-emoji> /start — главное меню'
    )

# ---------- ЗАПУСК ----------
async def main():
    logger.info("Бот запущен!")
    await dp.start_polling(bot)

if __name__ == "__main__":
    asyncio.run(main())
