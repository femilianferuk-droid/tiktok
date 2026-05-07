import os, re, json, io, uuid, logging, asyncio, sys, threading, copy
from pathlib import Path
from io import BytesIO
import aiohttp
from PIL import Image, ImageOps, ImageColor

from aiogram import Bot, Dispatcher, F, types
from aiogram.types import (
    Message, FSInputFile, InlineKeyboardMarkup, InlineKeyboardButton,
    CallbackQuery, BufferedInputFile
)
from aiogram.enums import ParseMode
from aiogram.client.default import DefaultBotProperties
from aiogram.filters import Command, CommandObject
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.fsm.storage.memory import MemoryStorage

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# ---------- ТОКЕН ГЛАВНОГО БОТА ----------
BOT_TOKEN = os.getenv("BOT_TOKEN", "")

# ---------- ХРАНИЛИЩЕ ЗЕРКАЛ ----------
mirrors = {}  # {token: {"bot": Bot, "dp": Dispatcher, "thread": Thread}}

# ---------- БОТ ----------
bot = Bot(token=BOT_TOKEN, default=DefaultBotProperties(parse_mode=ParseMode.HTML))
dp = Dispatcher(storage=MemoryStorage())

TEMP_DIR = Path("temp_videos")
TEMP_DIR.mkdir(exist_ok=True)
SETTINGS_FILE = Path("user_settings.json")

# ---------- EMOJI ----------
E = {
    "menu": ('⚙', "5870982283724328568"),
    "tiktok": ('📁', "5870528606328852614"),
    "pinterest": ('🖼', "6035128606563241721"),
    "sticker": ('🔡', "5771851822897566479"),
    "color": ('↔', "5778479949572738874"),
    "mirror_h": ('👤', "5891207662678317861"),
    "mirror_v": ('👤', "5893192487324880883"),
    "rotate": ('👾', "5260752406890711732"),
    "check": ('✅', "5870633910337015697"),
    "cross": ('❌', "5870657884844462243"),
    "back": ('◁', "5893057118545646106"),
    "download": ('⬇', "6039802767931871481"),
    "send": ('⬆', "5963103826075456248"),
    "loading": ('🔄', "5345906554510012647"),
    "sparkles": ('🎉', "6041731551845159060"),
    "profile": ('👤', "5870994129244131212"),
    "settings": ('⚙', "5870982283724328568"),
    "pencil": ('🖋', "5870676941614354370"),
    "trash": ('🗑', "5870875489362513438"),
    "info": ('ℹ', "6028435952299413210"),
    "link": ('🔗', "5769289093221454192"),
    "brush": ('🖌', "6050679691004612757"),
    "photo": ('🖼', "6035128606563241721"),
    "mirror_icon": ('🪞', "6030400221232501136"),
    "robot": ('🤖', "6030400221232501136"),
}

def emoji(key: str) -> str:
    text, pid = E.get(key, ('❓', ''))
    if pid:
        return f'<tg-emoji emoji-id="{pid}">{text}</tg-emoji>'
    return text

def btn_emoji(key: str) -> tuple:
    text, pid = E.get(key, ('❓', ''))
    return text, pid if pid else None

# ---------- COLORS ----------
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

# ---------- FSM ----------
class StickerStates(StatesGroup):
    waiting_for_image = State()
    waiting_for_color = State()

# ---------- SETTINGS ----------
def load_settings():
    return json.loads(SETTINGS_FILE.read_text(encoding="utf-8")) if SETTINGS_FILE.exists() else {}

def save_settings(data):
    SETTINGS_FILE.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")

def get_user_settings(user_id: int) -> dict:
    data = load_settings()
    return data.get(str(user_id), {"color": "#FF0000", "mirror": "none"})

def set_user_setting(user_id, key, value):
    data = load_settings()
    uid = str(user_id)
    if uid not in data:
        data[uid] = {"color": "#FF0000", "mirror": "none"}
    data[uid][key] = value
    save_settings(data)

# ---------- HELPERS ----------
async def download_bytes(url: str) -> bytes:
    async with aiohttp.ClientSession() as s:
        async with s.get(url, timeout=60) as r:
            return await r.read()

def recolor_image(img: Image.Image, hex_color: str) -> Image.Image:
    img = img.convert("RGBA")
    r, g, b, a = img.split()
    new_rgb = ImageColor.getrgb(hex_color)
    colored = Image.new("RGBA", img.size, (*new_rgb, 255))
    colored.putalpha(a)
    return colored

def mirror_image(img: Image.Image, mode: str) -> Image.Image:
    if mode == "horizontal":
        return ImageOps.mirror(img)
    elif mode == "vertical":
        return ImageOps.flip(img)
    elif mode == "both":
        return ImageOps.mirror(ImageOps.flip(img))
    return img

def resize_for_sticker(img: Image.Image, max_size=512) -> Image.Image:
    img = img.convert("RGBA")
    ratio = max_size / max(img.size)
    w, h = int(img.width * ratio), int(img.height * ratio)
    img = img.resize((w, h), Image.LANCZOS)
    canvas = Image.new("RGBA", (max_size, max_size), (0, 0, 0, 0))
    x, y = (max_size - w) // 2, (max_size - h) // 2
    canvas.paste(img, (x, y), img)
    return canvas

async def validate_token(token: str) -> bool:
    """Проверяет, валидный ли токен"""
    try:
        test_bot = Bot(token=token)
        await test_bot.get_me()
        await test_bot.session.close()
        return True
    except:
        return False

# ---------- KEYBOARDS ----------
def make_btn(text: str, cb: str, emoji_key: str = None) -> InlineKeyboardButton:
    if emoji_key:
        txt, pid = btn_emoji(emoji_key)
        return InlineKeyboardButton(text=f"{txt} {text}", callback_data=cb, icon_custom_emoji_id=pid)
    return InlineKeyboardButton(text=text, callback_data=cb)

def main_menu():
    return InlineKeyboardMarkup(inline_keyboard=[
        [make_btn("TikTok", "mode_tiktok", "tiktok"),
         make_btn("Pinterest", "mode_pinterest", "pinterest")],
        [make_btn("Редактор стикеров", "mode_sticker", "sticker")],
        [make_btn("Зеркало бота", "mode_mirror", "mirror_icon")],
        [make_btn("Настройки", "open_settings", "settings")],
    ])

def settings_menu(user_id):
    s = get_user_settings(user_id)
    return InlineKeyboardMarkup(inline_keyboard=[
        [make_btn(f"Цвет: {s['color']}", "set_color", "color")],
        [make_btn(f"Зеркало: {MIRROR_MODES[s['mirror']]}", "set_mirror", "mirror_h")],
        [make_btn("Назад", "back_main", "back")],
    ])

def color_menu():
    btns = []
    row = []
    for key, (name, hex_) in PRESET_COLORS.items():
        row.append(InlineKeyboardButton(text=name, callback_data=f"setcl_{hex_}"))
        if len(row) == 3:
            btns.append(row)
            row = []
    if row:
        btns.append(row)
    btns.append([make_btn("Свой HEX", "custom_color", "pencil")])
    btns.append([make_btn("Назад", "open_settings", "back")])
    return InlineKeyboardMarkup(inline_keyboard=btns)

def mirror_menu():
    btns = []
    for key, name in MIRROR_MODES.items():
        btns.append([InlineKeyboardButton(text=name, callback_data=f"setmir_{key}")])
    btns.append([make_btn("Назад", "open_settings", "back")])
    return InlineKeyboardMarkup(inline_keyboard=btns)

# ========== ЗЕРКАЛО (АВТОЗАПУСК КЛОНА) ==========

def create_mirror_bot(token: str):
    """Создаёт и запускает клон бота на новом токене"""
    
    # Создаём свои экземпляры
    mirror_bot = Bot(token=token, default=DefaultBotProperties(parse_mode=ParseMode.HTML))
    mirror_dp = Dispatcher(storage=MemoryStorage())
    
    # Копируем все хендлеры
    # Регистрируем те же команды и обработчики
    
    @mirror_dp.message(Command("start"))
    async def mirror_start(msg: Message):
        await msg.answer(
            f"<b>{emoji('sparkles')} Привет! Я зеркало бота! 🪞</b>\n\n"
            f"{emoji('tiktok')} Скачиваю видео из TikTok\n"
            f"{emoji('pinterest')} Скачиваю фото из Pinterest\n"
            f"{emoji('sticker')} Перекрашиваю стикеры и эмодзи\n\n"
            "<b>Выбери действие:</b>",
            reply_markup=main_menu()
        )
    
    # TikTok
    @mirror_dp.message(F.text.regexp(r'https?://(?:www\.)?(?:tiktok\.com|vm\.tiktok\.com|vt\.tiktok\.com)/\S+'))
    async def mirror_tiktok(msg: Message):
        await download_tiktok_handler(msg)
    
    # Pinterest
    @mirror_dp.message(F.text.regexp(r'https?://(?:pin\.it|(?:www\.)?pinterest\.\w+/pin)/\S+'))
    async def mirror_pinterest(msg: Message):
        await download_pinterest_handler(msg)
    
    # Callbacks (все те же)
    @mirror_dp.callback_query(F.data == "back_main")
    async def mirror_back(cb: CallbackQuery):
        await cb.message.edit_text(
            f"<b>{emoji('sparkles')} Главное меню (зеркало):</b>",
            reply_markup=main_menu()
        )
        await cb.answer()
    
    @mirror_dp.callback_query(F.data == "mode_tiktok")
    async def mirror_cb_tiktok(cb: CallbackQuery):
        await cb.message.edit_text(
            f"<b>{emoji('tiktok')} Отправь ссылку на TikTok видео:</b>",
            reply_markup=InlineKeyboardMarkup(inline_keyboard=[
                [make_btn("Назад", "back_main", "back")]
            ])
        )
        await cb.answer()
    
    @mirror_dp.callback_query(F.data == "mode_pinterest")
    async def mirror_cb_pinterest(cb: CallbackQuery):
        await cb.message.edit_text(
            f"<b>{emoji('pinterest')} Отправь ссылку на Pin:</b>",
            reply_markup=InlineKeyboardMarkup(inline_keyboard=[
                [make_btn("Назад", "back_main", "back")]
            ])
        )
        await cb.answer()
    
    @mirror_dp.callback_query(F.data == "mode_sticker")
    async def mirror_cb_sticker(cb: CallbackQuery, state: FSMContext):
        await cb.message.edit_text(
            f"<b>{emoji('sticker')} Отправь стикер, эмодзи или картинку:</b>",
            reply_markup=InlineKeyboardMarkup(inline_keyboard=[
                [make_btn("Назад", "back_main", "back")]
            ])
        )
        await state.set_state(StickerStates.waiting_for_image)
        await cb.answer()
    
    @mirror_dp.callback_query(F.data == "open_settings")
    async def mirror_cb_settings(cb: CallbackQuery):
        await cb.message.edit_text(
            f"<b>{emoji('settings')} Настройки:</b>",
            reply_markup=settings_menu(cb.from_user.id)
        )
        await cb.answer()
    
    @mirror_dp.callback_query(F.data == "set_color")
    async def mirror_cb_set_color(cb: CallbackQuery):
        await cb.message.edit_text(
            f"<b>{emoji('color')} Выбери цвет:</b>",
            reply_markup=color_menu()
        )
        await cb.answer()
    
    @mirror_dp.callback_query(F.data.startswith("setcl_"))
    async def mirror_cb_color_select(cb: CallbackQuery):
        hex_ = cb.data.replace("setcl_", "")
        set_user_setting(cb.from_user.id, "color", hex_)
        await cb.message.edit_text(
            f"<b>{emoji('check')} Цвет: {hex_}</b>",
            reply_markup=settings_menu(cb.from_user.id)
        )
        await cb.answer()
    
    @mirror_dp.callback_query(F.data == "custom_color")
    async def mirror_cb_custom(cb: CallbackQuery, state: FSMContext):
        await cb.message.edit_text(
            f"<b>{emoji('pencil')} Отправь HEX цвет:</b>",
            reply_markup=InlineKeyboardMarkup(inline_keyboard=[
                [make_btn("Назад", "set_color", "back")]
            ])
        )
        await state.set_state(StickerStates.waiting_for_color)
        await cb.answer()
    
    @mirror_dp.message(StickerStates.waiting_for_color, F.text)
    async def mirror_process_color(msg: Message, state: FSMContext):
        color = msg.text.strip()
        if not color.startswith("#"):
            color = "#" + color
        try:
            ImageColor.getrgb(color)
            set_user_setting(msg.from_user.id, "color", color)
            await msg.answer(
                f"<b>{emoji('check')} Цвет: {color}</b>",
                reply_markup=settings_menu(msg.from_user.id)
            )
            await state.clear()
        except:
            await msg.answer(f"<b>{emoji('cross')} Неверный HEX!</b>")
    
    @mirror_dp.callback_query(F.data == "set_mirror")
    async def mirror_cb_mirror(cb: CallbackQuery):
        await cb.message.edit_text(
            f"<b>{emoji('mirror_h')} Выбери режим:</b>",
            reply_markup=mirror_menu()
        )
        await cb.answer()
    
    @mirror_dp.callback_query(F.data.startswith("setmir_"))
    async def mirror_cb_mirror_select(cb: CallbackQuery):
        mode = cb.data.replace("setmir_", "")
        set_user_setting(cb.from_user.id, "mirror", mode)
        await cb.message.edit_text(
            f"<b>{emoji('check')} Зеркало: {MIRROR_MODES[mode]}</b>",
            reply_markup=settings_menu(cb.from_user.id)
        )
        await cb.answer()
    
    # Стикеры
    @mirror_dp.message(StickerStates.waiting_for_image, F.photo | F.document | F.sticker)
    async def mirror_process_sticker(msg: Message, state: FSMContext):
        settings = get_user_settings(msg.from_user.id)
        status = await msg.answer(f"<b>{emoji('loading')} Обрабатываю...</b>")
        
        file_id = msg.photo[-1].file_id if msg.photo else (
            msg.sticker.file_id if msg.sticker else msg.document.file_id
        )
        file = await mirror_bot.get_file(file_id)
        img_bytes = BytesIO()
        await mirror_bot.download_file(file.file_path, img_bytes)
        img = Image.open(img_bytes).convert("RGBA")
        
        img = recolor_image(img, settings["color"])
        img = mirror_image(img, settings["mirror"])
        sticker_img = resize_for_sticker(img)
        
        output = BytesIO()
        sticker_img.save(output, format="PNG")
        output.seek(0)
        
        await msg.reply_document(
            document=BufferedInputFile(output.getvalue(), filename="sticker.png"),
            caption=f"<b>{emoji('check')} Стикер готов!</b>",
            reply_markup=InlineKeyboardMarkup(inline_keyboard=[
                [make_btn("Ещё", "mode_sticker", "sticker"),
                 make_btn("Меню", "back_main", "back")]
            ])
        )
        await status.delete()
        await state.clear()
    
    @mirror_dp.message()
    async def mirror_fallback(msg: Message):
        await msg.answer(
            f"<b>{emoji('info')} Используй меню или отправь ссылку.</b>",
            reply_markup=main_menu()
        )
    
    # Запускаем в отдельном потоке
    async def start_mirror():
        logger.info(f"Зеркало запущено: {token[:10]}...")
        await mirror_dp.start_polling(mirror_bot)
    
    loop = asyncio.new_event_loop()
    
    def run_mirror():
        asyncio.set_event_loop(loop)
        loop.run_until_complete(start_mirror())
    
    thread = threading.Thread(target=run_mirror, daemon=True)
    thread.start()
    
    # Сохраняем
    mirrors[token] = {
        "bot": mirror_bot,
        "dp": mirror_dp,
        "loop": loop,
        "thread": thread,
    }
    
    return True

# ---------- КОМАНДА /mirror ----------
@dp.message(Command("mirror"))
async def cmd_mirror(msg: Message, command: CommandObject):
    token = command.args.strip() if command.args else None
    
    if not token:
        await msg.answer(
            f"<b>{emoji('mirror_icon')} Создание зеркала бота:</b>\n\n"
            f"Отправь команду с токеном:\n"
            f"<code>/mirror 123456:ABCdef...</code>\n\n"
            f"<i>Бот запустит свою копию на этом токене автоматически.</i>"
        )
        return
    
    # Валидация токена
    status = await msg.answer(f"<b>{emoji('loading')} Проверяю токен...</b>")
    
    if not await validate_token(token):
        await status.edit_text(
            f"<b>{emoji('cross')} Неверный токен! Проверь и попробуй снова.</b>"
        )
        return
    
    # Проверяем, не запущен ли уже
    if token in mirrors:
        await status.edit_text(
            f"<b>{emoji('info')} Зеркало на этот токен уже запущено!</b>"
        )
        return
    
    await status.edit_text(f"<b>{emoji('loading')} Запускаю зеркало...</b>")
    
    # Запускаем зеркало
    success = create_mirror_bot(token)
    
    if success:
        await status.edit_text(
            f"<b>{emoji('check')} Зеркало успешно запущено!</b>\n\n"
            f"{emoji('robot')} Бот-клон активен и готов к работе.\n"
            f"{emoji('info')} Токен: <code>{token[:10]}...</code>\n\n"
            f"<i>Зеркало работает параллельно с основным ботом.</i>",
            reply_markup=InlineKeyboardMarkup(inline_keyboard=[
                [make_btn("В меню", "back_main", "back")]
            ])
        )
    else:
        await status.edit_text(
            f"<b>{emoji('cross')} Ошибка запуска зеркала.</b>"
        )

@dp.callback_query(F.data == "mode_mirror")
async def cb_mirror(cb: CallbackQuery):
    active_mirrors = len(mirrors)
    await cb.message.edit_text(
        f"<b>{emoji('mirror_icon')} Зеркало бота:</b>\n\n"
        f"{emoji('robot')} Активных зеркал: {active_mirrors}\n\n"
        f"<b>Как создать зеркало:</b>\n"
        f"1. Получи токен у @BotFather\n"
        f"2. Отправь команду:\n"
        f"<code>/mirror ТВОЙ_ТОКЕН</code>\n\n"
        f"<i>Бот автоматически запустит копию самого себя!</i>",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=[
            [make_btn("Назад", "back_main", "back")]
        ])
    )
    await cb.answer()

# ---------- КОМАНДА /mirrors ----------
@dp.message(Command("mirrors"))
async def cmd_mirrors(msg: Message):
    if not mirrors:
        await msg.answer(
            f"<b>{emoji('info')} Нет активных зеркал.</b>\n"
            f"Создай: <code>/mirror ТОКЕН</code>"
        )
        return
    
    text = f"<b>{emoji('mirror_icon')} Активные зеркала ({len(mirrors)}):</b>\n\n"
    for i, token in enumerate(mirrors, 1):
        text += f"{i}. <code>{token[:15]}...</code>\n"
    
    await msg.answer(text)

# ---------- START ----------
@dp.message(Command("start"))
async def start(msg: Message):
    await msg.answer(
        f"<b>{emoji('sparkles')} Привет! Я умею:</b>\n\n"
        f"{emoji('tiktok')} Скачивать видео из TikTok\n"
        f"{emoji('pinterest')} Скачивать фото из Pinterest\n"
        f"{emoji('sticker')} Перекрашивать стикеры и эмодзи\n"
        f"{emoji('mirror_icon')} Создавать зеркала бота\n\n"
        "<b>Выбери действие:</b>",
        reply_markup=main_menu()
    )

# ---------- MAIN CALLBACKS ----------
@dp.callback_query(F.data == "back_main")
async def cb_back_main(cb: CallbackQuery):
    await cb.message.edit_text(
        f"<b>{emoji('sparkles')} Главное меню:</b>",
        reply_markup=main_menu()
    )
    await cb.answer()

@dp.callback_query(F.data == "mode_tiktok")
async def cb_tiktok(cb: CallbackQuery):
    await cb.message.edit_text(
        f"<b>{emoji('tiktok')} Отправь ссылку на TikTok видео:</b>\n"
        f"<i>Пример: https://vm.tiktok.com/... или https://www.tiktok.com/...</i>",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=[
            [make_btn("Назад", "back_main", "back")]
        ])
    )
    await cb.answer()

@dp.callback_query(F.data == "mode_pinterest")
async def cb_pinterest(cb: CallbackQuery):
    await cb.message.edit_text(
        f"<b>{emoji('pinterest')} Отправь ссылку на Pin:</b>\n"
        f"<i>Пример: https://pin.it/... или https://www.pinterest.com/pin/...</i>",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=[
            [make_btn("Назад", "back_main", "back")]
        ])
    )
    await cb.answer()

@dp.callback_query(F.data == "mode_sticker")
async def cb_sticker(cb: CallbackQuery, state: FSMContext):
    await cb.message.edit_text(
        f"<b>{emoji('sticker')} Отправь стикер, эмодзи или картинку:</b>",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=[
            [make_btn("Назад", "back_main", "back")]
        ])
    )
    await state.set_state(StickerStates.waiting_for_image)
    await cb.answer()

@dp.callback_query(F.data == "open_settings")
async def cb_settings(cb: CallbackQuery):
    await cb.message.edit_text(
        f"<b>{emoji('settings')} Настройки:</b>",
        reply_markup=settings_menu(cb.from_user.id)
    )
    await cb.answer()

# ---------- SETTINGS CALLBACKS ----------
@dp.callback_query(F.data == "set_color")
async def cb_set_color(cb: CallbackQuery):
    await cb.message.edit_text(
        f"<b>{emoji('color')} Выбери цвет:</b>",
        reply_markup=color_menu()
    )
    await cb.answer()

@dp.callback_query(F.data.startswith("setcl_"))
async def cb_color_select(cb: CallbackQuery):
    hex_ = cb.data.replace("setcl_", "")
    set_user_setting(cb.from_user.id, "color", hex_)
    await cb.message.edit_text(
        f"<b>{emoji('check')} Цвет: {hex_}</b>",
        reply_markup=settings_menu(cb.from_user.id)
    )
    await cb.answer()

@dp.callback_query(F.data == "custom_color")
async def cb_custom_color(cb: CallbackQuery, state: FSMContext):
    await cb.message.edit_text(
        f"<b>{emoji('pencil')} Отправь HEX цвет:</b>",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=[
            [make_btn("Назад", "set_color", "back")]
        ])
    )
    await state.set_state(StickerStates.waiting_for_color)
    await cb.answer()

@dp.message(StickerStates.waiting_for_color, F.text)
async def process_custom_color(msg: Message, state: FSMContext):
    color = msg.text.strip()
    if not color.startswith("#"):
        color = "#" + color
    try:
        ImageColor.getrgb(color)
        set_user_setting(msg.from_user.id, "color", color)
        await msg.answer(
            f"<b>{emoji('check')} Цвет: {color}</b>",
            reply_markup=settings_menu(msg.from_user.id)
        )
        await state.clear()
    except:
        await msg.answer(f"<b>{emoji('cross')} Неверный HEX!</b>")

@dp.callback_query(F.data == "set_mirror")
async def cb_set_mirror(cb: CallbackQuery):
    await cb.message.edit_text(
        f"<b>{emoji('mirror_h')} Выбери режим:</b>",
        reply_markup=mirror_menu()
    )
    await cb.answer()

@dp.callback_query(F.data.startswith("setmir_"))
async def cb_mirror_select(cb: CallbackQuery):
    mode = cb.data.replace("setmir_", "")
    set_user_setting(cb.from_user.id, "mirror", mode)
    await cb.message.edit_text(
        f"<b>{emoji('check')} Зеркало: {MIRROR_MODES[mode]}</b>",
        reply_markup=settings_menu(cb.from_user.id)
    )
    await cb.answer()

# ---------- STICKER PROCESSING ----------
@dp.message(StickerStates.waiting_for_image, F.photo | F.document | F.sticker)
async def process_sticker(msg: Message, state: FSMContext):
    settings = get_user_settings(msg.from_user.id)
    status = await msg.answer(f"<b>{emoji('loading')} Обрабатываю...</b>")

    file_id = msg.photo[-1].file_id if msg.photo else (
        msg.sticker.file_id if msg.sticker else msg.document.file_id
    )
    file = await bot.get_file(file_id)
    img_bytes = BytesIO()
    await bot.download_file(file.file_path, img_bytes)
    img = Image.open(img_bytes).convert("RGBA")

    img = recolor_image(img, settings["color"])
    img = mirror_image(img, settings["mirror"])
    sticker_img = resize_for_sticker(img)
    
    output = BytesIO()
    sticker_img.save(output, format="PNG")
    output.seek(0)

    await msg.reply_document(
        document=BufferedInputFile(output.getvalue(), filename="sticker.png"),
        caption=f"<b>{emoji('check')} Стикер готов!</b>",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=[
            [make_btn("Ещё", "mode_sticker", "sticker"),
             make_btn("Меню", "back_main", "back")]
        ])
    )

    await status.delete()
    await state.clear()

# ---------- TIKTOK ----------
async def tiktok_info(url: str):
    try:
        async with aiohttp.ClientSession() as s:
            async with s.get("https://tikwm.com/api/", params={"url": url, "hd": 1}, timeout=30) as r:
                data = await r.json()
                if data.get("code") == 0 and data.get("data"):
                    v = data["data"]
                    return {
                        "url": v.get("hdplay") or v.get("play"),
                        "title": v.get("title", "TikTok"),
                        "author": v.get("author", {}).get("nickname", "unknown"),
                    }
    except:
        pass
    return None

async def download_tiktok_handler(msg: Message):
    url = msg.text.strip()
    status = await msg.answer(f"<b>{emoji('loading')} Скачиваю TikTok...</b>")
    
    info = await tiktok_info(url)
    if not info:
        await status.edit_text(f"<b>{emoji('cross')} Не удалось скачать.</b>")
        return

    video_bytes = await download_bytes(info["url"])
    filepath = TEMP_DIR / f"{uuid.uuid4().hex}.mp4"
    filepath.write_bytes(video_bytes)

    if filepath.stat().st_size > 50 * 1024 * 1024:
        filepath.unlink()
        await status.edit_text(f"<b>{emoji('cross')} Видео >50 МБ.</b>")
        return

    await status.edit_text(f"<b>{emoji('send')} Отправляю...</b>")
    
    await msg.reply_video(
        video=FSInputFile(filepath),
        caption=f"<b>{emoji('sparkles')} Готово!</b>\n{emoji('profile')} @{info['author']}",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text=f"👤 @{info['author']}", url=f"https://tiktok.com/@{info['author']}")],
            [make_btn("Меню", "back_main", "back")]
        ])
    )
    await status.delete()
    filepath.unlink()

@dp.message(F.text.regexp(r'https?://(?:www\.)?(?:tiktok\.com|vm\.tiktok\.com|vt\.tiktok\.com)/\S+'))
async def download_tiktok(msg: Message):
    await download_tiktok_handler(msg)

# ---------- PINTEREST ----------
async def pinterest_image(url: str) -> bytes:
    try:
        async with aiohttp.ClientSession() as s:
            async with s.get(f"https://api.pinterest.com/v3/pidgets/pins/info/?pin={url}", timeout=30) as r:
                data = await r.json()
                img_url = data.get("data", {}).get("images", {}).get("orig", {}).get("url")
                if img_url:
                    async with s.get(img_url, timeout=60) as img_r:
                        return await img_r.read()
    except:
        pass
    return None

async def download_pinterest_handler(msg: Message):
    url = msg.text.strip()
    status = await msg.answer(f"<b>{emoji('loading')} Скачиваю Pinterest...</b>")
    
    img_bytes = await pinterest_image(url)
    if not img_bytes:
        await status.edit_text(f"<b>{emoji('cross')} Не удалось скачать.</b>")
        return

    await msg.reply_photo(
        photo=BufferedInputFile(img_bytes, filename="pin.jpg"),
        caption=f"<b>{emoji('sparkles')} Готово!</b>",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=[
            [make_btn("Меню", "back_main", "back")]
        ])
    )
    await status.delete()

@dp.message(F.text.regexp(r'https?://(?:pin\.it|(?:www\.)?pinterest\.\w+/pin)/\S+'))
async def download_pinterest(msg: Message):
    await download_pinterest_handler(msg)

# ---------- FALLBACK ----------
@dp.message()
async def fallback(msg: Message):
    await msg.answer(
        f"<b>{emoji('info')} Используй меню или отправь ссылку.</b>",
        reply_markup=main_menu()
    )

# ---------- RUN ----------
async def main():
    logger.info("✅ Основной бот запущен!")
    await dp.start_polling(bot)

if __name__ == "__main__":
    asyncio.run(main())
