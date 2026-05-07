import asyncio
import json
import logging
import os
import re
import struct
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

import numpy as np
from PIL import Image, ImageDraw, ImageFont, ImageFilter, ImageEnhance

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
E_BRUSH = "6050679691004612757"
E_EYE = "6037397706505195857"
E_TAG = "5886285355279193209"

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
        "watermark_position": "bottom-right",
        "filter": "none",
        "shadow": True,
    })

def set_user_setting(user_id: int, key: str, value):
    settings = load_settings()
    uid = str(user_id)
    if uid not in settings:
        settings[uid] = {
            "watermark_text": None,
            "watermark_position": "bottom-right",
            "filter": "none",
            "shadow": True,
        }
    settings[uid][key] = value
    save_settings(settings)

# ---------- ФИЛЬТРЫ ----------
AVAILABLE_FILTERS = {
    "none": "Без фильтра",
    "bw": "Ч/Б",
    "vintage": "Винтаж",
    "warm": "Тёплый",
    "cool": "Холодный",
    "vivid": "Яркий",
}

POSITIONS = {
    "top-left": "Левый верх",
    "top-right": "Правый верх",
    "bottom-left": "Левый низ",
    "bottom-right": "Правый низ",
    "center": "Центр",
}

# ---------- ОБРАБОТЧИК ВОДЯНОГО ЗНАКА НА PIL (покадрово) ----------
def draw_watermark(frame: Image.Image, text: str, position: str, shadow: bool) -> Image.Image:
    """Рисует водяной знак на PIL Image"""
    draw = ImageDraw.Draw(frame)
    
    # Размер шрифта — 4% от высоты
    fontsize = max(int(frame.height * 0.04), 12)
    
    try:
        font = ImageFont.truetype("/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf", fontsize)
    except:
        try:
            font = ImageFont.truetype("arial.ttf", fontsize)
        except:
            font = ImageFont.load_default()
    
    # Считаем размер текста
    bbox = draw.textbbox((0, 0), text, font=font)
    tw = bbox[2] - bbox[0]
    th = bbox[3] - bbox[1]
    
    margin = 30
    
    # Позиция
    if position == "top-left":
        x, y = margin, margin
    elif position == "top-right":
        x, y = frame.width - tw - margin, margin
    elif position == "bottom-left":
        x, y = margin, frame.height - th - margin
    elif position == "bottom-right":
        x, y = frame.width - tw - margin, frame.height - th - margin
    else:  # center
        x, y = (frame.width - tw) // 2, (frame.height - th) // 2
    
    # Тень (смещённый чёрный полупрозрачный текст)
    if shadow:
        shadow_img = Image.new('RGBA', frame.size, (0, 0, 0, 0))
        shadow_draw = ImageDraw.Draw(shadow_img)
        shadow_draw.text((x + 3, y + 3), text, font=font, fill=(0, 0, 0, 100))
        frame = Image.alpha_composite(frame.convert('RGBA'), shadow_img)
    
    # Основной текст
    txt_img = Image.new('RGBA', frame.size, (0, 0, 0, 0))
    txt_draw = ImageDraw.Draw(txt_img)
    
    # Белый текст с чёрной обводкой
    outline = 2
    for dx in [-outline, 0, outline]:
        for dy in [-outline, 0, outline]:
            if dx != 0 or dy != 0:
                txt_draw.text((x + dx, y + dy), text, font=font, fill=(0, 0, 0, 180))
    
    txt_draw.text((x, y), text, font=font, fill=(255, 255, 255, 220))
    frame = Image.alpha_composite(frame.convert('RGBA'), txt_img)
    
    return frame.convert('RGB')


def apply_filter(frame: Image.Image, filter_name: str) -> Image.Image:
    """Применяет фильтр к PIL Image"""
    if filter_name == "none":
        return frame
    
    if filter_name == "bw":
        return frame.convert('L').convert('RGB')
    
    if filter_name == "vintage":
        # Сепия
        frame = frame.convert('RGB')
        arr = np.array(frame, dtype=np.float32)
        sepia = np.array([
            [0.393, 0.769, 0.189],
            [0.349, 0.686, 0.168],
            [0.272, 0.534, 0.131]
        ])
        result = arr @ sepia.T
        result = np.clip(result, 0, 255).astype(np.uint8)
        return Image.fromarray(result)
    
    if filter_name == "warm":
        enhancer = ImageEnhance.Color(frame)
        frame = enhancer.enhance(1.3)
        # Добавляем красный оттенок
        r, g, b = frame.split()
        r = r.point(lambda i: min(255, int(i * 1.15)))
        b = b.point(lambda i: min(255, int(i * 0.8)))
        return Image.merge('RGB', (r, g, b))
    
    if filter_name == "cool":
        enhancer = ImageEnhance.Color(frame)
        frame = enhancer.enhance(0.8)
        r, g, b = frame.split()
        r = r.point(lambda i: min(255, int(i * 0.85)))
        b = b.point(lambda i: min(255, int(i * 1.2)))
        return Image.merge('RGB', (r, g, b))
    
    if filter_name == "vivid":
        enhancer = ImageEnhance.Contrast(frame)
        frame = enhancer.enhance(1.3)
        enhancer = ImageEnhance.Color(frame)
        frame = enhancer.enhance(1.4)
        return frame
    
    return frame


def process_video_frames(input_path: str, output_path: str, user_id: int) -> bool:
    """
    Покадровая обработка видео через PIL + Numpy.
    Извлекает кадры, обрабатывает, собирает обратно.
    Работает через чтение сырых байтов — без FFmpeg.
    """
    settings = get_user_settings(user_id)
    watermark_text = settings.get("watermark_text")
    video_filter = settings.get("filter", "none")
    shadow = settings.get("shadow", True)
    position = settings.get("watermark_position", "bottom-right")
    
    if not watermark_text and video_filter == "none":
        return False
    
    try:
        # Читаем видео как сырые кадры
        # Для MP4 это сложно без FFmpeg, поэтому используем imageio
        import imageio.v3 as iio
        import imageio as iio_write
        
        # Читаем все кадры
        frames = []
        metadata = {}
        
        reader = iio.imiter(input_path, plugin="pyav")
        for frame in reader:
            pil_frame = Image.fromarray(frame)
            
            # Применяем фильтр
            pil_frame = apply_filter(pil_frame, video_filter)
            
            # Добавляем водяной знак
            if watermark_text:
                pil_frame = draw_watermark(pil_frame, watermark_text, position, shadow)
            
            frames.append(np.array(pil_frame))
        
        if not frames:
            return False
        
        # Записываем обработанное видео
        iio_write.imwrite(
            output_path,
            frames,
            plugin="pyav",
            codec="libx264",
            fps=30,
            quality=8
        )
        
        return True
        
    except Exception as e:
        logger.error(f"Frame processing error: {e}")
        return False


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

# ---------- КНОПКИ ----------
def settings_keyboard(user_id: int) -> InlineKeyboardMarkup:
    s = get_user_settings(user_id)
    wm = s["watermark_text"] or "Нет"
    filt = AVAILABLE_FILTERS[s["filter"]]
    sh = "Да" if s["shadow"] else "Нет"
    pos = POSITIONS[s["watermark_position"]]

    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(
            text=f"Текст: {wm[:20]}",
            callback_data="set_wm_text",
            icon_custom_emoji_id=E_PENCIL
        )],
        [InlineKeyboardButton(
            text=f"Позиция: {pos}",
            callback_data="set_wm_position",
            icon_custom_emoji_id=E_GEOPIN
        )],
        [InlineKeyboardButton(
            text=f"Фильтр: {filt}",
            callback_data="set_filter",
            icon_custom_emoji_id=E_BRUSH
        )],
        [InlineKeyboardButton(
            text=f"Тень: {sh}",
            callback_data="toggle_shadow",
            icon_custom_emoji_id=E_EYE
        )],
        [InlineKeyboardButton(
            text="Сбросить всё",
            callback_data="reset_settings",
            icon_custom_emoji_id=E_TRASH
        )],
        [InlineKeyboardButton(
            text="Закрыть",
            callback_data="close_settings",
            icon_custom_emoji_id=E_CROSS
        )],
    ])

def position_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="Левый верх", callback_data="pos_top-left", icon_custom_emoji_id=E_GEOPIN),
         InlineKeyboardButton(text="Правый верх", callback_data="pos_top-right", icon_custom_emoji_id=E_GEOPIN)],
        [InlineKeyboardButton(text="Левый низ", callback_data="pos_bottom-left", icon_custom_emoji_id=E_GEOPIN),
         InlineKeyboardButton(text="Правый низ", callback_data="pos_bottom-right", icon_custom_emoji_id=E_GEOPIN)],
        [InlineKeyboardButton(text="Центр", callback_data="pos_center", icon_custom_emoji_id=E_GEOPIN)],
        [InlineKeyboardButton(text="Назад", callback_data="back_to_settings", icon_custom_emoji_id=E_BACK)],
    ])

def filter_keyboard() -> InlineKeyboardMarkup:
    btns = []
    for key, name in AVAILABLE_FILTERS.items():
        btns.append([InlineKeyboardButton(
            text=name,
            callback_data=f"filter_{key}",
            icon_custom_emoji_id=E_BRUSH
        )])
    btns.append([InlineKeyboardButton(
        text="Назад",
        callback_data="back_to_settings",
        icon_custom_emoji_id=E_BACK
    )])
    return InlineKeyboardMarkup(inline_keyboard=btns)

# ---------- КОМАНДЫ ----------
@dp.message(Command("start"))
async def cmd_start(message: Message):
    await message.answer(
        f'<b><tg-emoji emoji-id="{E_FILE}">📁</tg-emoji> Привет! Отправь ссылку на TikTok — скачаю видео с водяным знаком и фильтрами!</b>\n\n'
        f'<tg-emoji emoji-id="{E_SETTINGS}">⚙</tg-emoji> /settings — настроить обработку\n'
        f'<tg-emoji emoji-id="{E_LINK}">🔗</tg-emoji> Просто пришли ссылку на видео'
    )

@dp.message(Command("settings"))
async def cmd_settings(message: Message):
    await message.answer(
        f'<b><tg-emoji emoji-id="{E_SETTINGS}">⚙</tg-emoji> Настройки видео:</b>',
        reply_markup=settings_keyboard(message.from_user.id)
    )

# ---------- CALLBACKS ----------
@dp.callback_query(F.data == "back_to_settings")
async def cb_back(callback: CallbackQuery):
    await callback.message.edit_text(
        f'<b><tg-emoji emoji-id="{E_SETTINGS}">⚙</tg-emoji> Настройки видео:</b>',
        reply_markup=settings_keyboard(callback.from_user.id)
    )
    await callback.answer()

@dp.callback_query(F.data == "close_settings")
async def cb_close(callback: CallbackQuery):
    await callback.message.delete()
    await callback.answer()

@dp.callback_query(F.data == "reset_settings")
async def cb_reset(callback: CallbackQuery):
    uid = str(callback.from_user.id)
    settings = load_settings()
    settings[uid] = {
        "watermark_text": None,
        "watermark_position": "bottom-right",
        "filter": "none",
        "shadow": True,
    }
    save_settings(settings)
    await callback.message.edit_text(
        f'<b><tg-emoji emoji-id="{E_CHECK}">✅</tg-emoji> Настройки сброшены!</b>',
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

@dp.callback_query(F.data == "set_wm_position")
async def cb_wm_position(callback: CallbackQuery):
    await callback.message.edit_text(
        f'<b><tg-emoji emoji-id="{E_GEOPIN}">📍</tg-emoji> Выбери позицию водяного знака:</b>',
        reply_markup=position_keyboard()
    )
    await callback.answer()

@dp.callback_query(F.data.startswith("pos_"))
async def cb_pos_select(callback: CallbackQuery):
    pos = callback.data.replace("pos_", "")
    set_user_setting(callback.from_user.id, "watermark_position", pos)
    await callback.message.edit_text(
        f'<b><tg-emoji emoji-id="{E_CHECK}">✅</tg-emoji> Позиция: {POSITIONS[pos]}</b>',
        reply_markup=settings_keyboard(callback.from_user.id)
    )
    await callback.answer()

@dp.callback_query(F.data == "set_filter")
async def cb_filter(callback: CallbackQuery):
    await callback.message.edit_text(
        f'<b><tg-emoji emoji-id="{E_BRUSH}">🖌</tg-emoji> Выбери фильтр:</b>',
        reply_markup=filter_keyboard()
    )
    await callback.answer()

@dp.callback_query(F.data.startswith("filter_"))
async def cb_filter_select(callback: CallbackQuery):
    filt = callback.data.replace("filter_", "")
    set_user_setting(callback.from_user.id, "filter", filt)
    await callback.message.edit_text(
        f'<b><tg-emoji emoji-id="{E_CHECK}">✅</tg-emoji> Фильтр: {AVAILABLE_FILTERS[filt]}</b>',
        reply_markup=settings_keyboard(callback.from_user.id)
    )
    await callback.answer()

@dp.callback_query(F.data == "toggle_shadow")
async def cb_shadow(callback: CallbackQuery):
    s = get_user_settings(callback.from_user.id)
    new_val = not s["shadow"]
    set_user_setting(callback.from_user.id, "shadow", new_val)
    await callback.message.edit_text(
        f'<b><tg-emoji emoji-id="{E_CHECK}">✅</tg-emoji> Тень: {"Да" if new_val else "Нет"}</b>',
        reply_markup=settings_keyboard(callback.from_user.id)
    )
    await callback.answer()

# ---------- FSM: текст водяного знака ----------
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
            f'<b><tg-emoji emoji-id="{E_CHECK}">✅</tg-emoji> Текст: {text[:30]}</b>',
            reply_markup=settings_keyboard(message.from_user.id)
        )
    await state.clear()

# ---------- ССЫЛКА TIKTOK ----------
@dp.message(F.text.regexp(r'https?://(?:www\.)?(?:tiktok\.com|vm\.tiktok\.com|vt\.tiktok\.com)/\S+'))
async def handle_link(message: Message):
    url = message.text.strip()
    user_id = message.from_user.id
    settings = get_user_settings(user_id)

    status = await message.answer(
        f'<b><tg-emoji emoji-id="{E_LOADING}">🔄</tg-emoji> Получаю информацию...</b>',
        reply_to_message_id=message.message_id
    )

    info = await download_tiktok_video(url)
    if not info or not info["url"]:
        await status.edit_text(
            f'<b><tg-emoji emoji-id="{E_CROSS}">❌</tg-emoji> Не удалось получить видео.</b>'
        )
        return

    await status.edit_text(
        f'<b><tg-emoji emoji-id="{E_DOWNLOAD}">⬇</tg-emoji> Скачиваю видео...</b>'
    )

    raw_file = TEMP_DIR / f"raw_{uuid.uuid4().hex}.mp4"
    if not await download_file(info["url"], raw_file):
        await status.edit_text(
            f'<b><tg-emoji emoji-id="{E_CROSS}">❌</tg-emoji> Ошибка скачивания.</b>'
        )
        return

    await status.edit_text(
        f'<b><tg-emoji emoji-id="{E_BRUSH}">🖌</tg-emoji> Обрабатываю видео...</b>'
    )

    processed_file = TEMP_DIR / f"proc_{uuid.uuid4().hex}.mp4"
    processed = process_video_frames(str(raw_file), str(processed_file), user_id)
    
    if not processed:
        # Если обработка не удалась — отправляем оригинал
        await status.edit_text(
            f'<b><tg-emoji emoji-id="{E_INFO}">ℹ</tg-emoji> Отправляю без обработки...</b>'
        )
        final_file = raw_file
    else:
        final_file = processed_file

    file_size = os.path.getsize(final_file)
    if file_size > 50 * 1024 * 1024:
        await status.edit_text(
            f'<b><tg-emoji emoji-id="{E_CROSS}">❌</tg-emoji> Видео >50 МБ.</b>'
        )
        raw_file.unlink(missing_ok=True)
        processed_file.unlink(missing_ok=True)
        return

    await status.edit_text(
        f'<b><tg-emoji emoji-id="{E_SEND}">⬆</tg-emoji> Отправляю видео...</b>'
    )

    author = info["author"]
    filt_name = AVAILABLE_FILTERS[settings["filter"]]
    wm_status = f"Да" if settings["watermark_text"] else "Нет"

    caption = (
        f'<b><tg-emoji emoji-id="{E_SPARKLES}">🎉</tg-emoji> Видео готово!</b>\n'
        f'<tg-emoji emoji-id="{E_PROFILE}">👤</tg-emoji> @{author}\n'
        f'<tg-emoji emoji-id="{E_BRUSH}">🖌</tg-emoji> Фильтр: {filt_name}\n'
        f'<tg-emoji emoji-id="{E_TAG}">🏷</tg-emoji> Водяной знак: {wm_status}'
    )

    video = FSInputFile(final_file)
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
                callback_data="back_to_settings",
                icon_custom_emoji_id=E_SETTINGS
            )]
        ])
    )

    await status.delete()
    raw_file.unlink(missing_ok=True)
    processed_file.unlink(missing_ok=True)

# ---------- ОСТАЛЬНОЕ ----------
@dp.message()
async def unknown(message: Message):
    await message.answer(
        f'<b><tg-emoji emoji-id="{E_SPARKLES}">🎉</tg-emoji> Отправь ссылку на TikTok!</b>\n'
        f'<tg-emoji emoji-id="{E_LINK}">🔗</tg-emoji> Пример: https://vm.tiktok.com/...\n'
        f'<tg-emoji emoji-id="{E_SETTINGS}">⚙</tg-emoji> /settings — водяной знак и фильтры'
    )

# ---------- ЗАПУСК ----------
async def main():
    logger.info("Бот запущен!")
    await dp.start_polling(bot)

if __name__ == "__main__":
    asyncio.run(main())
