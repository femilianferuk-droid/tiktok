import os, re, json, io, uuid, logging, asyncio
from pathlib import Path
from io import BytesIO
import aiohttp
from PIL import Image, ImageOps, ImageColor, ImageEnhance, ImageFilter

from aiogram import Bot, Dispatcher, F
from aiogram.types import (
    Message, FSInputFile, InlineKeyboardMarkup, InlineKeyboardButton,
    CallbackQuery, BufferedInputFile
)
from aiogram.enums import ParseMode, ChatAction
from aiogram.client.default import DefaultBotProperties
from aiogram.filters import Command, CommandObject
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.fsm.storage.memory import MemoryStorage
from aiogram.utils.keyboard import InlineKeyboardBuilder

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

BOT_TOKEN = os.getenv("BOT_TOKEN", "")

mirrors = {}

bot = Bot(token=BOT_TOKEN, default=DefaultBotProperties(parse_mode=ParseMode.HTML))
dp = Dispatcher(storage=MemoryStorage())

TEMP_DIR = Path("temp_videos")
TEMP_DIR.mkdir(exist_ok=True)
SETTINGS_FILE = Path("user_settings.json")
MIRRORS_FILE = Path("mirrors_data.json")

# ========== ЭМОДЗИ ==========
class Em:
    menu = "⚙️"
    tiktok = "📁"
    pinterest = "🖼️"
    sticker = "🎨"
    color = "🎨"
    mirror_h = "🔄"
    mirror_v = "🔃"
    check = "✅"
    cross = "❌"
    back = "◀️"
    download = "⬇️"
    send = "⬆️"
    loading = "⏳"
    sparkles = "✨"
    profile = "👤"
    settings = "⚙️"
    pencil = "✏️"
    trash = "🗑️"
    info = "ℹ️"
    link = "🔗"
    mirror_icon = "🪞"
    robot = "🤖"
    play = "▶️"
    stop = "⏹️"
    edit = "📝"

# ========== НАСТРОЙКИ ==========
PRESET_COLORS = {
    "red":    ("Красный",     "#FF0000"),
    "orange": ("Оранжевый",   "#FF8C00"),
    "yellow": ("Жёлтый",      "#FFD700"),
    "green":  ("Зелёный",     "#00FF00"),
    "cyan":   ("Голубой",     "#00FFFF"),
    "blue":   ("Синий",       "#0000FF"),
    "purple": ("Фиолетовый",  "#8B00FF"),
    "pink":   ("Розовый",     "#FF69B4"),
    "white":  ("Белый",       "#FFFFFF"),
    "black":  ("Чёрный",      "#000000"),
}

MIRROR_MODES = {
    "none":        "Без зеркала",
    "horizontal":  "🔄 Горизонтально",
    "vertical":    "🔃 Вертикально",
    "both":        "🔄🔃 Оба",
}

STICKER_EFFECTS = {
    "none":      "Без эффекта",
    "bright":    "🔆 Ярче",
    "dark":      "🔅 Темнее",
    "contrast":  "🌓 Контраст",
    "blur":      "🌫 Размытие",
    "sharpen":   "🔪 Резкость",
}

class StickerStates(StatesGroup):
    waiting_for_image = State()
    waiting_for_color = State()

class MirrorSettingsStates(StatesGroup):
    waiting_for_welcome = State()
    waiting_for_token = State()

# ========== РАБОТА С ФАЙЛАМИ ==========
def load_mirrors():
    if MIRRORS_FILE.exists():
        return json.loads(MIRRORS_FILE.read_text(encoding="utf-8"))
    return {}

def save_mirrors(data):
    MIRRORS_FILE.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")

def get_mirror_settings(token: str) -> dict:
    data = load_mirrors()
    return data.get(token, {"welcome": f"{Em.sparkles} Привет! Я зеркало бота!", "running": True})

def set_mirror_setting(token: str, key: str, value):
    data = load_mirrors()
    if token not in data:
        data[token] = {"welcome": f"{Em.sparkles} Привет! Я зеркало бота!", "running": True}
    data[token][key] = value
    save_mirrors(data)

def load_settings():
    if SETTINGS_FILE.exists():
        return json.loads(SETTINGS_FILE.read_text(encoding="utf-8"))
    return {}

def save_settings(data):
    SETTINGS_FILE.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")

def get_user_settings(user_id: int) -> dict:
    data = load_settings()
    return data.get(str(user_id), {"color": "#FF0000", "mirror": "none", "effect": "none"})

def set_user_setting(user_id, key, value):
    data = load_settings()
    uid = str(user_id)
    if uid not in data:
        data[uid] = {"color": "#FF0000", "mirror": "none", "effect": "none"}
    data[uid][key] = value
    save_settings(data)

# ========== ОБРАБОТКА ИЗОБРАЖЕНИЙ ==========
async def download_bytes(url: str) -> bytes:
    async with aiohttp.ClientSession() as s:
        async with s.get(url, timeout=60) as r:
            return await r.read()

def recolor_image(img: Image.Image, hex_color: str) -> Image.Image:
    """Правильная перекраска с сохранением теней и градиентов"""
    img = img.convert("RGBA")
    r, g, b, a = img.split()
    
    import numpy as np
    new_r, new_g, new_b = ImageColor.getrgb(hex_color)
    
    r_arr = np.array(r, dtype=np.float32) / 255.0
    g_arr = np.array(g, dtype=np.float32) / 255.0
    b_arr = np.array(b, dtype=np.float32) / 255.0
    a_arr = np.array(a, dtype=np.float32) / 255.0
    
    brightness = 0.299 * r_arr + 0.587 * g_arr + 0.114 * b_arr
    
    new_r_arr = (brightness * new_r / 255.0 * 255).clip(0, 255).astype(np.uint8)
    new_g_arr = (brightness * new_g / 255.0 * 255).clip(0, 255).astype(np.uint8)
    new_b_arr = (brightness * new_b / 255.0 * 255).clip(0, 255).astype(np.uint8)
    
    result = Image.merge("RGBA", (
        Image.fromarray(new_r_arr),
        Image.fromarray(new_g_arr),
        Image.fromarray(new_b_arr),
        a
    ))
    return result

def mirror_image(img: Image.Image, mode: str) -> Image.Image:
    if mode == "horizontal":
        return ImageOps.mirror(img)
    elif mode == "vertical":
        return ImageOps.flip(img)
    elif mode == "both":
        return ImageOps.mirror(ImageOps.flip(img))
    return img

def apply_effect(img: Image.Image, effect: str) -> Image.Image:
    if effect == "bright":
        return ImageEnhance.Brightness(img).enhance(1.5)
    elif effect == "dark":
        return ImageEnhance.Brightness(img).enhance(0.6)
    elif effect == "contrast":
        return ImageEnhance.Contrast(img).enhance(1.5)
    elif effect == "blur":
        return img.filter(ImageFilter.GaussianBlur(radius=2))
    elif effect == "sharpen":
        return img.filter(ImageFilter.SHARPEN)
    return img

def resize_for_sticker(img: Image.Image, max_size=512) -> Image.Image:
    img = img.convert("RGBA")
    ratio = max_size / max(img.size)
    w, h = (int(img.width * ratio), int(img.height * ratio)) if ratio < 1 else img.size
    if ratio < 1:
        img = img.resize((w, h), Image.LANCZOS)
    canvas = Image.new("RGBA", (max_size, max_size), (0, 0, 0, 0))
    x, y = (max_size - w) // 2, (max_size - h) // 2
    canvas.paste(img, (x, y), img)
    return canvas

async def validate_token(token: str):
    try:
        test_bot = Bot(token=token)
        info = await test_bot.get_me()
        await test_bot.session.close()
        return True, info.username
    except Exception as ex:
        return False, str(ex)

# ========== КЛАВИАТУРЫ С ЦВЕТАМИ ==========
def main_menu():
    builder = InlineKeyboardBuilder()
    builder.row(
        InlineKeyboardButton(text=f"{Em.tiktok} TikTok", callback_data="mode_tiktok"),
        InlineKeyboardButton(text=f"{Em.pinterest} Pinterest", callback_data="mode_pinterest")
    )
    builder.row(InlineKeyboardButton(text=f"{Em.sticker} Редактор стикеров", callback_data="mode_sticker"))
    builder.row(InlineKeyboardButton(text=f"{Em.settings} Настройки", callback_data="open_settings"))
    return builder.as_markup()

def back_btn(callback: str = "back_main"):
    builder = InlineKeyboardBuilder()
    builder.row(InlineKeyboardButton(text=f"{Em.back} Назад", callback_data=callback))
    return builder.as_markup()

def settings_menu(user_id):
    s = get_user_settings(user_id)
    builder = InlineKeyboardBuilder()
    builder.row(InlineKeyboardButton(text=f"{Em.color} Цвет: {s['color']}", callback_data="set_color"))
    builder.row(InlineKeyboardButton(text=f"{Em.mirror_h} Зеркало: {MIRROR_MODES[s['mirror']]}", callback_data="set_mirror"))
    builder.row(InlineKeyboardButton(text=f"✨ Эффект: {STICKER_EFFECTS[s['effect']]}", callback_data="set_effect"))
    builder.row(InlineKeyboardButton(text=f"{Em.mirror_icon} Зеркала бота", callback_data="mode_mirror"))
    builder.row(InlineKeyboardButton(text=f"{Em.back} Назад", callback_data="back_main"))
    return builder.as_markup()

def color_menu():
    builder = InlineKeyboardBuilder()
    row = []
    for key, (name, hex_) in PRESET_COLORS.items():
        row.append(InlineKeyboardButton(text=name, callback_data=f"setcl_{hex_}"))
        if len(row) == 2:
            builder.row(*row)
            row = []
    if row:
        builder.row(*row)
    builder.row(InlineKeyboardButton(text=f"{Em.pencil} Свой HEX", callback_data="custom_color"))
    builder.row(InlineKeyboardButton(text=f"{Em.back} Назад", callback_data="open_settings"))
    return builder.as_markup()

def mirror_menu():
    builder = InlineKeyboardBuilder()
    for key, name in MIRROR_MODES.items():
        builder.row(InlineKeyboardButton(text=name, callback_data=f"setmir_{key}"))
    builder.row(InlineKeyboardButton(text=f"{Em.back} Назад", callback_data="open_settings"))
    return builder.as_markup()

def effect_menu():
    builder = InlineKeyboardBuilder()
    for key, name in STICKER_EFFECTS.items():
        builder.row(InlineKeyboardButton(text=name, callback_data=f"seteff_{key}"))
    builder.row(InlineKeyboardButton(text=f"{Em.back} Назад", callback_data="open_settings"))
    return builder.as_markup()

def mirror_management_menu(token: str):
    settings = get_mirror_settings(token)
    running = settings.get("running", True)
    
    builder = InlineKeyboardBuilder()
    builder.row(InlineKeyboardButton(
        text=f"{Em.play} Работает" if running else f"{Em.stop} Остановлен",
        callback_data=f"mrinfo_{token[:16]}"
    ))
    builder.row(
        InlineKeyboardButton(
            text=f"{Em.stop} Остановить" if running else f"{Em.play} Запустить",
            callback_data=f"mrtoggle_{token[:16]}"
        )
    )
    builder.row(InlineKeyboardButton(
        text=f"{Em.edit} Приветствие",
        callback_data=f"mrwelcome_{token[:16]}"
    ))
    builder.row(InlineKeyboardButton(
        text=f"{Em.trash} Удалить зеркало",
        callback_data=f"mrdelete_{token[:16]}"
    ))
    builder.row(InlineKeyboardButton(
        text=f"{Em.back} Назад",
        callback_data="mode_mirror"
    ))
    return builder.as_markup()

def mirrors_list_menu(page=0):
    """Меню со списком зеркал"""
    builder = InlineKeyboardBuilder()
    
    if mirrors:
        for token, m in mirrors.items():
            info = m.get("info", {})
            settings = get_mirror_settings(token)
            running = settings.get("running", True)
            status_icon = Em.play if running else Em.stop
            builder.row(InlineKeyboardButton(
                text=f"{status_icon} @{info.get('username', '?')}",
                callback_data=f"mrmenu_{token[:16]}"
            ))
    
    builder.row(InlineKeyboardButton(
        text=f"{Em.robot} Создать зеркало",
        callback_data="mirror_create"
    ))
    builder.row(InlineKeyboardButton(
        text=f"{Em.back} Назад",
        callback_data="open_settings"
    ))
    return builder.as_markup()

# ========== СТАРТ ==========
@dp.message(Command("start"))
async def start(msg: Message):
    builder = InlineKeyboardBuilder()
    builder.row(
        InlineKeyboardButton(text=f"{Em.tiktok} TikTok", callback_data="mode_tiktok"),
        InlineKeyboardButton(text=f"{Em.pinterest} Pinterest", callback_data="mode_pinterest")
    )
    builder.row(InlineKeyboardButton(text=f"{Em.sticker} Редактор стикеров", callback_data="mode_sticker"))
    builder.row(InlineKeyboardButton(text=f"{Em.settings} Настройки", callback_data="open_settings"))
    
    await msg.answer(
        f"{Em.sparkles} <b>Привет! Я умею:</b>\n\n"
        f"{Em.tiktok} Скачивать видео из TikTok\n"
        f"{Em.pinterest} Скачивать фото из Pinterest\n"
        f"{Em.sticker} Перекрашивать стикеры\n"
        f"{Em.settings} Создавать зеркала в Настройках\n\n"
        "<b>Выбери действие:</b>",
        reply_markup=builder.as_markup()
    )

# ========== CALLBACKS ==========
@dp.callback_query(F.data == "back_main")
async def cb_back_main(cb: CallbackQuery):
    await cb.message.edit_text(
        f"{Em.sparkles} <b>Главное меню:</b>",
        reply_markup=main_menu()
    )
    await cb.answer()

@dp.callback_query(F.data == "mode_tiktok")
async def cb_tiktok(cb: CallbackQuery):
    await cb.message.edit_text(
        f"{Em.tiktok} <b>Отправь ссылку на TikTok:</b>\n"
        "<i>Пример: https://vm.tiktok.com/...</i>",
        reply_markup=back_btn()
    )
    await cb.answer()

@dp.callback_query(F.data == "mode_pinterest")
async def cb_pinterest(cb: CallbackQuery):
    await cb.message.edit_text(
        f"{Em.pinterest} <b>Отправь ссылку на Pinterest:</b>\n"
        "<i>Пример: https://pin.it/...</i>",
        reply_markup=back_btn()
    )
    await cb.answer()

@dp.callback_query(F.data == "mode_sticker")
async def cb_sticker(cb: CallbackQuery, state: FSMContext):
    s = get_user_settings(cb.from_user.id)
    await cb.message.edit_text(
        f"{Em.sticker} <b>Отправь стикер или картинку:</b>\n\n"
        f"{Em.color} Цвет: {s['color']}\n"
        f"{Em.mirror_h} Зеркало: {MIRROR_MODES[s['mirror']]}\n"
        f"✨ Эффект: {STICKER_EFFECTS[s['effect']]}",
        reply_markup=back_btn()
    )
    await state.set_state(StickerStates.waiting_for_image)
    await cb.answer()

@dp.callback_query(F.data == "open_settings")
async def cb_settings(cb: CallbackQuery):
    await cb.message.edit_text(
        f"{Em.settings} <b>Настройки:</b>",
        reply_markup=settings_menu(cb.from_user.id)
    )
    await cb.answer()

@dp.callback_query(F.data == "mode_mirror")
async def cb_mirrors_list(cb: CallbackQuery):
    active = sum(1 for m in mirrors.values() if get_mirror_settings(
        [t for t, d in mirrors.items() if d == m][0] if m in mirrors.values() else ""
    ).get("running", True))
    
    txt = f"{Em.mirror_icon} <b>Зеркала бота:</b>\n\n"
    txt += f"Всего: {len(mirrors)} | Активных: {active}\n\n"
    
    if mirrors:
        txt += "<b>Список:</b>\n"
        for token, m in mirrors.items():
            info = m.get("info", {})
            settings = get_mirror_settings(token)
            status = "▶️" if settings.get("running", True) else "⏹️"
            txt += f"{status} @{info.get('username', '?')}\n"
    else:
        txt += "<i>Нет созданных зеркал</i>\n"
    
    await cb.message.edit_text(txt, reply_markup=mirrors_list_menu())
    await cb.answer()

@dp.callback_query(F.data.startswith("mrmenu_"))
async def cb_mirror_menu(cb: CallbackQuery):
    token_key = cb.data.replace("mrmenu_", "")
    
    full_token = next((t for t in mirrors if t.startswith(token_key)), None)
    if not full_token:
        await cb.answer("Зеркало не найдено", show_alert=True)
        return
    
    info = mirrors[full_token].get("info", {})
    settings = get_mirror_settings(full_token)
    running = settings.get("running", True)
    
    txt = f"{Em.mirror_icon} <b>Управление:</b>\n\n"
    txt += f"Бот: @{info.get('username', '?')}\n"
    txt += f"Имя: {info.get('name', '?')}\n"
    txt += f"Статус: {'▶️ Работает' if running else '⏹️ Остановлен'}\n"
    txt += f"Приветствие: <i>{settings.get('welcome', '...')[:100]}</i>\n"
    
    await cb.message.edit_text(txt, reply_markup=mirror_management_menu(full_token))
    await cb.answer()

@dp.callback_query(F.data.startswith("mrtoggle_"))
async def cb_mirror_toggle(cb: CallbackQuery):
    token_key = cb.data.replace("mrtoggle_", "")
    full_token = next((t for t in mirrors if t.startswith(token_key)), None)
    
    if not full_token:
        await cb.answer("Зеркало не найдено", show_alert=True)
        return
    
    current = get_mirror_settings(full_token).get("running", True)
    set_mirror_setting(full_token, "running", not current)
    
    if not current:
        await create_mirror_task(full_token)
        await cb.answer("Зеркало запущено!", show_alert=True)
    else:
        await stop_mirror(full_token)
        await cb.answer("Зеркало остановлено", show_alert=True)
    
    await cb.message.edit_reply_markup(reply_markup=mirror_management_menu(full_token))

@dp.callback_query(F.data.startswith("mrwelcome_"))
async def cb_mirror_welcome(cb: CallbackQuery, state: FSMContext):
    token_key = cb.data.replace("mrwelcome_", "")
    full_token = next((t for t in mirrors if t.startswith(token_key)), None)
    
    if not full_token:
        await cb.answer("Зеркало не найдено", show_alert=True)
        return
    
    await cb.message.edit_text(
        f"{Em.edit} <b>Отправь новое приветствие:</b>",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text=f"{Em.back} Отмена", callback_data=f"mrmenu_{token_key}")]
        ])
    )
    await state.set_state(MirrorSettingsStates.waiting_for_welcome)
    await state.update_data(mirror_token=full_token)
    await cb.answer()

@dp.message(MirrorSettingsStates.waiting_for_welcome, F.text)
async def process_welcome(msg: Message, state: FSMContext):
    data = await state.get_data()
    full_token = data.get("mirror_token")
    
    if not full_token:
        await state.clear()
        return
    
    welcome = msg.html_text if msg.html_text else msg.text
    set_mirror_setting(full_token, "welcome", welcome)
    
    info = mirrors[full_token].get("info", {})
    await msg.answer(
        f"{Em.check} <b>Приветствие обновлено для @{info.get('username', '?')}!</b>",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text=f"{Em.back} К управлению", callback_data=f"mrmenu_{full_token[:16]}")]
        ])
    )
    await state.clear()

@dp.callback_query(F.data.startswith("mrdelete_"))
async def cb_mirror_delete(cb: CallbackQuery):
    token_key = cb.data.replace("mrdelete_", "")
    full_token = next((t for t in mirrors if t.startswith(token_key)), None)
    
    if not full_token:
        await cb.answer("Зеркало не найдено", show_alert=True)
        return
    
    info = mirrors[full_token].get("info", {})
    await stop_mirror(full_token)
    
    data = load_mirrors()
    data.pop(full_token, None)
    save_mirrors(data)
    
    await cb.message.edit_text(
        f"{Em.trash} <b>Зеркало @{info.get('username', '?')} удалено.</b>",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text=f"{Em.back} К списку", callback_data="mode_mirror")]
        ])
    )
    await cb.answer(f"Удалено @{info.get('username', '?')}", show_alert=True)

@dp.callback_query(F.data == "mirror_create")
async def cb_mirror_create(cb: CallbackQuery, state: FSMContext):
    await cb.message.edit_text(
        f"{Em.robot} <b>Создание зеркала:</b>\n\n"
        "Отправь токен бота от @BotFather:",
        reply_markup=back_btn("mode_mirror")
    )
    await state.set_state(MirrorSettingsStates.waiting_for_token)
    await cb.answer()

@dp.message(MirrorSettingsStates.waiting_for_token, F.text)
async def process_token(msg: Message, state: FSMContext):
    token = msg.text.strip()
    
    if token in mirrors:
        info = mirrors[token].get("info", {})
        await msg.answer(
            f"{Em.info} Зеркало уже запущено! (@{info.get('username', '?')})",
            reply_markup=InlineKeyboardMarkup(inline_keyboard=[
                [InlineKeyboardButton(text=f"{Em.back} К списку", callback_data="mode_mirror")]
            ])
        )
        await state.clear()
        return
    
    status = await msg.answer(f"{Em.loading} Проверяю токен...")
    
    valid, info = await validate_token(token)
    if not valid:
        await status.edit_text(f"{Em.cross} <b>Токен невалиден!</b>\n<i>{info}</i>")
        await state.clear()
        return
    
    await status.edit_text(f"{Em.loading} Запускаю @{info}...")
    
    result = await create_mirror_task(token)
    
    await status.edit_text(
        f"{Em.check} <b>Зеркало создано!</b>\n\n"
        f"Бот: @{result['username']}\n"
        f"Имя: {result['name']}\n\n"
        f"Управление в {Em.settings} Настройки → {Em.mirror_icon} Зеркала",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text=f"{Em.settings} Управлять", callback_data=f"mrmenu_{token[:16]}")],
            [InlineKeyboardButton(text=f"{Em.back} К списку", callback_data="mode_mirror")],
        ])
    )
    await state.clear()

# ========== НАСТРОЙКИ ==========
@dp.callback_query(F.data == "set_color")
async def cb_set_color(cb: CallbackQuery):
    await cb.message.edit_text(
        f"{Em.color} <b>Выбери цвет:</b>",
        reply_markup=color_menu()
    )
    await cb.answer()

@dp.callback_query(F.data.startswith("setcl_"))
async def cb_color_select(cb: CallbackQuery):
    hex_ = cb.data.replace("setcl_", "")
    set_user_setting(cb.from_user.id, "color", hex_)
    await cb.message.edit_text(
        f"{Em.check} <b>Цвет: {hex_}</b>",
        reply_markup=settings_menu(cb.from_user.id)
    )
    await cb.answer()

@dp.callback_query(F.data == "custom_color")
async def cb_custom_color(cb: CallbackQuery, state: FSMContext):
    await cb.message.edit_text(
        f"{Em.pencil} <b>Отправь HEX цвет:</b>\n<i>Например: #FF5733</i>",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text=f"{Em.back} Назад", callback_data="set_color")]
        ])
    )
    await state.set_state(StickerStates.waiting_for_color)
    await cb.answer()

@dp.message(StickerStates.waiting_for_color, F.text)
async def process_color(msg: Message, state: FSMContext):
    color = msg.text.strip()
    if not color.startswith("#"):
        color = "#" + color
    try:
        ImageColor.getrgb(color)
        set_user_setting(msg.from_user.id, "color", color)
        await msg.answer(
            f"{Em.check} <b>Цвет: {color}</b>",
            reply_markup=settings_menu(msg.from_user.id)
        )
        await state.clear()
    except:
        await msg.answer(f"{Em.cross} <b>Неверный HEX!</b>")

@dp.callback_query(F.data == "set_mirror")
async def cb_set_mirror(cb: CallbackQuery):
    await cb.message.edit_text(
        f"{Em.mirror_h} <b>Режим зеркала:</b>",
        reply_markup=mirror_menu()
    )
    await cb.answer()

@dp.callback_query(F.data.startswith("setmir_"))
async def cb_mirror_select(cb: CallbackQuery):
    mode = cb.data.replace("setmir_", "")
    set_user_setting(cb.from_user.id, "mirror", mode)
    await cb.message.edit_text(
        f"{Em.check} <b>Зеркало: {MIRROR_MODES[mode]}</b>",
        reply_markup=settings_menu(cb.from_user.id)
    )
    await cb.answer()

@dp.callback_query(F.data == "set_effect")
async def cb_set_effect(cb: CallbackQuery):
    await cb.message.edit_text(
        f"✨ <b>Выбери эффект:</b>",
        reply_markup=effect_menu()
    )
    await cb.answer()

@dp.callback_query(F.data.startswith("seteff_"))
async def cb_effect_select(cb: CallbackQuery):
    effect = cb.data.replace("seteff_", "")
    set_user_setting(cb.from_user.id, "effect", effect)
    await cb.message.edit_text(
        f"{Em.check} <b>Эффект: {STICKER_EFFECTS[effect]}</b>",
        reply_markup=settings_menu(cb.from_user.id)
    )
    await cb.answer()

# ========== СТИКЕРЫ ==========
@dp.message(StickerStates.waiting_for_image, F.photo | F.document | F.sticker)
async def process_sticker(msg: Message, state: FSMContext):
    settings = get_user_settings(msg.from_user.id)
    status = await msg.answer(f"{Em.loading} <b>Обрабатываю...</b>")
    await bot.send_chat_action(msg.chat.id, ChatAction.UPLOAD_DOCUMENT)

    file_id = msg.photo[-1].file_id if msg.photo else (
        msg.sticker.file_id if msg.sticker else msg.document.file_id
    )
    file = await bot.get_file(file_id)
    img_bytes = BytesIO()
    await bot.download_file(file.file_path, img_bytes)
    img = Image.open(img_bytes).convert("RGBA")

    img = recolor_image(img, settings["color"])
    img = mirror_image(img, settings["mirror"])
    img = apply_effect(img, settings["effect"])
    sticker_img = resize_for_sticker(img)
    
    output = BytesIO()
    sticker_img.save(output, format="PNG", optimize=True)
    output.seek(0)

    await msg.reply_sticker(
        sticker=BufferedInputFile(output.getvalue(), filename="sticker.webp"),
        reply_markup=InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text=f"{Em.sticker} Ещё", callback_data="mode_sticker"),
             InlineKeyboardButton(text=f"{Em.back} В меню", callback_data="back_main")]
        ])
    )
    await status.delete()
    await state.clear()

# ========== TIKTOK ==========
@dp.message(F.text.regexp(r'https?://(?:www\.)?(?:tiktok\.com|vm\.tiktok\.com|vt\.tiktok\.com)/\S+'))
async def download_tiktok(msg: Message):
    url = msg.text.strip()
    status = await msg.answer(f"{Em.loading} <b>Скачиваю...</b>")
    
    try:
        async with aiohttp.ClientSession() as s:
            async with s.get("https://tikwm.com/api/", params={"url": url, "hd": 1}, timeout=30) as r:
                data = await r.json()
                if data.get("code") == 0:
                    v = data["data"]
                    video_url = v.get("hdplay") or v.get("play")
                    author = v.get("author", {}).get("nickname", "unknown")
                    video = await download_bytes(video_url)
                    
                    fp = TEMP_DIR / f"{uuid.uuid4().hex}.mp4"
                    fp.write_bytes(video)
                    
                    if fp.stat().st_size <= 50 * 1024 * 1024:
                        await status.edit_text(f"{Em.send} <b>Отправляю...</b>")
                        await bot.send_chat_action(msg.chat.id, ChatAction.UPLOAD_VIDEO)
                        await msg.reply_video(
                            video=FSInputFile(fp),
                            caption=f"{Em.sparkles} <b>Готово!</b>\n@{author}",
                            reply_markup=InlineKeyboardMarkup(inline_keyboard=[
                                [InlineKeyboardButton(text=f"👤 @{author}", url=f"https://tiktok.com/@{author}")],
                                [InlineKeyboardButton(text=f"{Em.back} В меню", callback_data="back_main")]
                            ])
                        )
                    else:
                        await status.edit_text(f"{Em.cross} <b>Видео >50 МБ</b>")
                    fp.unlink(missing_ok=True)
                    await status.delete()
                else:
                    await status.edit_text(f"{Em.cross} <b>Не удалось скачать</b>")
    except:
        await status.edit_text(f"{Em.cross} <b>Ошибка</b>")

# ========== PINTEREST ==========
@dp.message(F.text.regexp(r'https?://(?:pin\.it|(?:www\.)?pinterest\.\w+/pin)/\S+'))
async def download_pinterest(msg: Message):
    url = msg.text.strip()
    status = await msg.answer(f"{Em.loading} <b>Скачиваю...</b>")
    
    try:
        async with aiohttp.ClientSession() as s:
            async with s.get(f"https://api.pinterest.com/v3/pidgets/pins/info/?pin={url}", timeout=30) as r:
                data = await r.json()
                img_url = data.get("data", {}).get("images", {}).get("orig", {}).get("url")
                if img_url:
                    img_bytes = await download_bytes(img_url)
                    await msg.reply_photo(
                        photo=BufferedInputFile(img_bytes, filename="pin.jpg"),
                        caption=f"{Em.sparkles} <b>Готово!</b>",
                        reply_markup=InlineKeyboardMarkup(inline_keyboard=[
                            [InlineKeyboardButton(text=f"{Em.back} В меню", callback_data="back_main")]
                        ])
                    )
                    await status.delete()
                else:
                    await status.edit_text(f"{Em.cross} <b>Не найдено</b>")
    except:
        await status.edit_text(f"{Em.cross} <b>Ошибка</b>")

# ========== FALLBACK ==========
@dp.message()
async def fallback(msg: Message):
    await msg.answer(
        f"{Em.info} <b>Используй меню или отправь ссылку.</b>\n"
        f"{Em.tiktok} TikTok: https://vm.tiktok.com/...\n"
        f"{Em.pinterest} Pinterest: https://pin.it/...",
        reply_markup=main_menu()
    )

# ========== ЗЕРКАЛА ==========
async def stop_mirror(token: str):
    if token in mirrors:
        m = mirrors[token]
        if m.get("task"):
            m["task"].cancel()
            try:
                await m["task"]
            except:
                pass
        if m.get("bot"):
            try:
                await m["bot"].session.close()
            except:
                pass
        mirrors.pop(token, None)

async def create_mirror_task(token: str):
    if token in mirrors:
        return mirrors[token].get("info", {"username": "unknown", "name": "unknown"})
    
    mirror_bot = Bot(token=token, default=DefaultBotProperties(parse_mode=ParseMode.HTML))
    mirror_dp = Dispatcher(storage=MemoryStorage())
    
    settings = get_mirror_settings(token)
    welcome_text = settings.get("welcome", f"{Em.sparkles} Привет! Я зеркало бота!")
    
    @mirror_dp.message(Command("start"))
    async def m_start(msg: Message):
        await msg.answer(f"{welcome_text}\n\n<b>Выбери действие:</b>", reply_markup=main_menu())
    
    @mirror_dp.callback_query()
    async def m_callback(cb: CallbackQuery, state: FSMContext):
        data = cb.data
        if data == "back_main":
            await cb.message.edit_text(welcome_text, reply_markup=main_menu())
        elif data == "mode_tiktok":
            await cb.message.edit_text(f"{Em.tiktok} <b>Отправь ссылку на TikTok:</b>", reply_markup=back_btn())
        elif data == "mode_pinterest":
            await cb.message.edit_text(f"{Em.pinterest} <b>Отправь ссылку на Pinterest:</b>", reply_markup=back_btn())
        elif data == "mode_sticker":
            await cb.message.edit_text(f"{Em.sticker} <b>Отправь стикер:</b>", reply_markup=back_btn())
            await state.set_state(StickerStates.waiting_for_image)
        elif data == "open_settings":
            await cb.message.edit_text(f"{Em.settings} <b>Настройки:</b>", reply_markup=settings_menu(cb.from_user.id))
        elif data == "set_color":
            await cb.message.edit_text(f"{Em.color} <b>Цвет:</b>", reply_markup=color_menu())
        elif data.startswith("setcl_"):
            hex_ = data.replace("setcl_", "")
            set_user_setting(cb.from_user.id, "color", hex_)
            await cb.message.edit_text(f"{Em.check} <b>Цвет: {hex_}</b>", reply_markup=settings_menu(cb.from_user.id))
        elif data == "custom_color":
            await cb.message.edit_text(f"{Em.pencil} <b>Отправь HEX:</b>", reply_markup=InlineKeyboardMarkup(inline_keyboard=[
                [InlineKeyboardButton(text=f"{Em.back} Назад", callback_data="set_color")]
            ]))
            await state.set_state(StickerStates.waiting_for_color)
        elif data == "set_mirror":
            await cb.message.edit_text(f"{Em.mirror_h} <b>Зеркало:</b>", reply_markup=mirror_menu())
        elif data.startswith("setmir_"):
            mode = data.replace("setmir_", "")
            set_user_setting(cb.from_user.id, "mirror", mode)
            await cb.message.edit_text(f"{Em.check} <b>{MIRROR_MODES[mode]}</b>", reply_markup=settings_menu(cb.from_user.id))
        elif data == "set_effect":
            await cb.message.edit_text(f"✨ <b>Эффект:</b>", reply_markup=effect_menu())
        elif data.startswith("seteff_"):
            effect = data.replace("seteff_", "")
            set_user_setting(cb.from_user.id, "effect", effect)
            await cb.message.edit_text(f"{Em.check} <b>{STICKER_EFFECTS[effect]}</b>", reply_markup=settings_menu(cb.from_user.id))
        await cb.answer()
    
    @mirror_dp.message(StickerStates.waiting_for_color, F.text)
    async def m_color(msg: Message, state: FSMContext):
        color = msg.text.strip()
        if not color.startswith("#"):
            color = "#" + color
        try:
            ImageColor.getrgb(color)
            set_user_setting(msg.from_user.id, "color", color)
            await msg.answer(f"{Em.check} <b>{color}</b>", reply_markup=settings_menu(msg.from_user.id))
            await state.clear()
        except:
            await msg.answer(f"{Em.cross} <b>Неверный HEX!</b>")
    
    @mirror_dp.message(StickerStates.waiting_for_image, F.photo | F.document | F.sticker)
    async def m_sticker(msg: Message, state: FSMContext):
        s = get_user_settings(msg.from_user.id)
        await msg.bot.send_chat_action(msg.chat.id, ChatAction.UPLOAD_DOCUMENT)
        
        file_id = msg.photo[-1].file_id if msg.photo else (
            msg.sticker.file_id if msg.sticker else msg.document.file_id
        )
        file = await mirror_bot.get_file(file_id)
        img_bytes = BytesIO()
        await mirror_bot.download_file(file.file_path, img_bytes)
        img = Image.open(img_bytes).convert("RGBA")
        
        img = recolor_image(img, s["color"])
        img = mirror_image(img, s["mirror"])
        img = apply_effect(img, s["effect"])
        sticker_img = resize_for_sticker(img)
        
        output = BytesIO()
        sticker_img.save(output, format="PNG", optimize=True)
        output.seek(0)
        
        await msg.reply_sticker(
            sticker=BufferedInputFile(output.getvalue(), filename="sticker.webp"),
            reply_markup=InlineKeyboardMarkup(inline_keyboard=[
                [InlineKeyboardButton(text=f"{Em.sticker} Ещё", callback_data="mode_sticker"),
                 InlineKeyboardButton(text=f"{Em.back} Меню", callback_data="back_main")]
            ])
        )
        await state.clear()
    
    @mirror_dp.message(F.text.regexp(r'https?://(?:www\.)?(?:tiktok\.com|vm\.tiktok\.com|vt\.tiktok\.com)/\S+'))
    async def m_tiktok(msg: Message):
        url = msg.text.strip()
        try:
            async with aiohttp.ClientSession() as s:
                async with s.get("https://tikwm.com/api/", params={"url": url, "hd": 1}, timeout=30) as r:
                    data = await r.json()
                    if data.get("code") == 0:
                        v = data["data"]
                        video = await download_bytes(v.get("hdplay") or v.get("play"))
                        fp = TEMP_DIR / f"m_{uuid.uuid4().hex}.mp4"
                        fp.write_bytes(video)
                        if fp.stat().st_size <= 50 * 1024 * 1024:
                            await msg.reply_video(video=FSInputFile(fp), caption=f"{Em.sparkles} Готово!",
                                reply_markup=InlineKeyboardMarkup(inline_keyboard=[
                                    [InlineKeyboardButton(text=f"{Em.back} Меню", callback_data="back_main")]
                                ]))
                        fp.unlink(missing_ok=True)
        except:
            pass
    
    @mirror_dp.message()
    async def m_fallback(msg: Message):
        await msg.answer(f"{Em.info} <b>Используй меню.</b>", reply_markup=main_menu())
    
    task = asyncio.create_task(mirror_dp.start_polling(mirror_bot))
    
    try:
        info = await mirror_bot.get_me()
    except:
        info = type('obj', (object,), {'username': 'unknown', 'full_name': 'unknown'})
    
    mirrors[token] = {
        "bot": mirror_bot,
        "dp": mirror_dp,
        "task": task,
        "info": {"username": info.username, "name": info.full_name}
    }
    
    logger.info(f"Зеркало запущено: @{info.username}")
    return mirrors[token]["info"]

# ========== КОМАНДА /mirror ==========
@dp.message(Command("mirror"))
async def cmd_mirror(msg: Message, command: CommandObject):
    token = command.args.strip() if command.args else None
    
    if not token:
        active = sum(1 for m in mirrors.values() if True)
        await msg.answer(
            f"{Em.mirror_icon} <b>Зеркала:</b>\n\nАктивных: {active}/{len(mirrors)}\n\n"
            f"<code>/mirror ТОКЕН</code> — создать\n"
            f"Управление в {Em.settings} Настройки → {Em.mirror_icon} Зеркала",
            reply_markup=InlineKeyboardMarkup(inline_keyboard=[
                [InlineKeyboardButton(text=f"{Em.settings} Настройки", callback_data="open_settings")]
            ])
        )
        return
    
    if token in mirrors:
        info = mirrors[token].get("info", {})
        await msg.answer(
            f"{Em.info} Зеркало уже запущено! (@{info.get('username', '?')})",
            reply_markup=InlineKeyboardMarkup(inline_keyboard=[
                [InlineKeyboardButton(text=f"{Em.settings} Управлять", callback_data=f"mrmenu_{token[:16]}")]
            ])
        )
        return
    
    status = await msg.answer(f"{Em.loading} <b>Проверяю...</b>")
    
    valid, info = await validate_token(token)
    if not valid:
        await status.edit_text(f"{Em.cross} <b>Токен невалиден!</b>\n<i>{info}</i>")
        return
    
    await status.edit_text(f"{Em.loading} <b>Запускаю @{info}...</b>")
    
    result = await create_mirror_task(token)
    
    await status.edit_text(
        f"{Em.check} <b>Зеркало создано!</b>\n\n"
        f"Бот: @{result['username']}\nИмя: {result['name']}",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text=f"{Em.settings} Управлять", callback_data=f"mrmenu_{token[:16]}")],
            [InlineKeyboardButton(text=f"{Em.back} В меню", callback_data="back_main")],
        ])
    )

# ========== ЗАПУСК ==========
async def main():
    logger.info("Бот запущен!")
    await dp.start_polling(bot)

if __name__ == "__main__":
    asyncio.run(main())
