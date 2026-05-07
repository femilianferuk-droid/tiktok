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
    CallbackQuery
)
from aiogram.enums import ParseMode
from aiogram.client.default import DefaultBotProperties
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.fsm.storage.memory import MemoryStorage

# MoviePy — сам скачает ffmpeg при первом использовании
from moviepy.editor import VideoFileClip, TextClip, CompositeVideoClip, ColorClip
from moviepy.video.fx.all import blackwhite, mirror_x, mirror_y
import numpy as np
from PIL import Image, ImageDraw, ImageFont

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
E_MONEY = "5904462880941545555"
E_CLOCK = "5983150113483134607"
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

# ---------- ФИЛЬТРЫ (moviepy эффекты) ----------
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

def get_position_coords(video_size: tuple, text_size: tuple, position: str, margin: int = 30):
    """Возвращает (x, y) для текста относительно видео"""
    vw, vh = video_size
    tw, th = text_size
    
    positions = {
        "top-left": (margin, margin),
        "top-right": (vw - tw - margin, margin),
        "bottom-left": (margin, vh - th - margin),
        "bottom-right": (vw - tw - margin, vh - th - margin),
        "center": ((vw - tw) // 2, (vh - th) // 2),
    }
    return positions.get(position, positions["bottom-right"])

def apply_vintage_effect(frame):
    """Винтажный эффект: сепия + уменьшенная насыщенность"""
    # Конвертируем в PIL для обработки
    img = Image.fromarray(frame)
    # Применяем сепию через numpy
    arr = np.array(img, dtype=np.float32)
    # Сепия матрица
    sepia = np.array([
        [0.393, 0.769, 0.189],
        [0.349, 0.686, 0.168],
        [0.272, 0.534, 0.131]
    ])
    result = arr @ sepia.T
    result = np.clip(result, 0, 255).astype(np.uint8)
    # Уменьшаем насыщенность
    pil_img = Image.fromarray(result)
    return np.array(pil_img)

def apply_warm_effect(frame):
    """Тёплый фильтр: добавляем красный и жёлтый оттенок"""
    arr = np.array(frame, dtype=np.float32)
    arr[:, :, 0] *= 1.2  # Красный канал
    arr[:, :, 1] *= 1.1  # Зелёный
    arr[:, :, 2] *= 0.8  # Синий
    return np.clip(arr, 0, 255).astype(np.uint8)

def apply_cool_effect(frame):
    """Холодный фильтр: добавляем синий оттенок"""
    arr = np.array(frame, dtype=np.float32)
    arr[:, :, 0] *= 0.8
    arr[:, :, 1] *= 0.95
    arr[:, :, 2] *= 1.2
    return np.clip(arr, 0, 255).astype(np.uint8)

def apply_vivid_effect(frame):
    """Яркий фильтр: увеличиваем контраст и насыщенность"""
    arr = np.array(frame, dtype=np.float32)
    mean = np.mean(arr, axis=(0, 1), keepdims=True)
    arr = mean + (arr - mean) * 1.4  # Увеличиваем контраст
    return np.clip(arr, 0, 255).astype(np.uint8)

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

# ---------- ОБРАБОТКА ВИДЕО (moviepy) ----------
def process_video_with_moviepy(input_path: str, output_path: str, user_id: int) -> bool:
    """Добавляет водяной знак и применяет фильтры через moviepy"""
    settings = get_user_settings(user_id)
    watermark_text = settings.get("watermark_text")
    video_filter = settings.get("filter", "none")
    shadow = settings.get("shadow", True)
    position = settings.get("watermark_position", "bottom-right")

    if not watermark_text and video_filter == "none":
        return False

    try:
        clip = VideoFileClip(input_path)
        
        # Применяем фильтр
        if video_filter == "bw":
            clip = clip.fx(blackwhite)
        elif video_filter != "none":
            # Применяем кастомные эффекты покадрово
            if video_filter == "vintage":
                clip = clip.fl_image(apply_vintage_effect)
            elif video_filter == "warm":
                clip = clip.fl_image(apply_warm_effect)
            elif video_filter == "cool":
                clip = clip.fl_image(apply_cool_effect)
            elif video_filter == "vivid":
                clip = clip.fl_image(apply_vivid_effect)

        # Добавляем водяной знак
        if watermark_text:
            # Создаём текстовый клип
            fontsize = int(clip.h * 0.04)  # 4% от высоты видео
            margin = int(clip.w * 0.03)    # 3% от ширины
            
            txt_clip = TextClip(
                watermark_text,
                fontsize=fontsize,
                color='white',
                font='Arial',
                stroke_color='black' if shadow else None,
                stroke_width=2 if shadow else 0,
            )
            
            # Позиционируем
            txt_clip = txt_clip.set_position(get_position_coords(
                clip.size, txt_clip.size, position, margin
            ))
            txt_clip = txt_clip.set_duration(clip.duration)
            
            # Добавляем тень (смещённый чёрный текст)
            if shadow:
                shadow_clip = TextClip(
                    watermark_text,
                    fontsize=fontsize,
                    color='black',
                    font='Arial',
                ).set_opacity(0.5)
                
                pos = get_position_coords(clip.size, shadow_clip.size, position, margin)
                shadow_clip = shadow_clip.set_position((pos[0] + 2, pos[1] + 2))
                shadow_clip = shadow_clip.set_duration(clip.duration)
                
                clip = CompositeVideoClip([clip, shadow_clip, txt_clip])
            else:
                clip = CompositeVideoClip([clip, txt_clip])

        # Сохраняем с хорошим качеством
        clip.write_videofile(
            output_path,
            codec='libx264',
            audio_codec='aac',
            bitrate='5000k',
            threads=2,
            preset='fast',
            logger=None,
        )
        clip.close()
        return True
        
    except Exception as e:
        logger.error(f"MoviePy error: {e}")
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

    # Применяем обработку
    await status.edit_text(
        f'<b><tg-emoji emoji-id="{E_BRUSH}">🖌</tg-emoji> Обрабатываю видео...</b>'
    )

    processed_file = TEMP_DIR / f"proc_{uuid.uuid4().hex}.mp4"
    processed = process_video_with_moviepy(str(raw_file), str(processed_file), user_id)
    final_file = processed_file if processed else raw_file

    # Проверяем размер
    file_size = os.path.getsize(final_file)
    if file_size > 50 * 1024 * 1024:
        await status.edit_text(
            f'<b><tg-emoji emoji-id="{E_CROSS}">❌</tg-emoji> Видео >50 МБ после обработки.</b>'
        )
        raw_file.unlink(missing_ok=True)
        processed_file.unlink(missing_ok=True)
        return

    await status.edit_text(
        f'<b><tg-emoji emoji-id="{E_SEND}">⬆</tg-emoji> Отправляю видео...</b>'
    )

    author = info["author"]
    filt_name = AVAILABLE_FILTERS[settings["filter"]]
    wm_status = f"Да: {settings['watermark_text'][:15]}" if settings["watermark_text"] else "Нет"

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
                text="Настройки обработки",
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
