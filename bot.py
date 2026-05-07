import os, re, json, io, uuid, logging, asyncio
from pathlib import Path
from io import BytesIO
import aiohttp
from PIL import Image, ImageOps, ImageColor

from aiogram import Bot, Dispatcher, F
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

# ---------- ЗЕРКАЛА ----------
mirrors = {}

# ---------- ГЛАВНЫЙ БОТ ----------
bot = Bot(token=BOT_TOKEN, default=DefaultBotProperties(parse_mode=ParseMode.HTML))
dp = Dispatcher(storage=MemoryStorage())

TEMP_DIR = Path("temp_videos")
TEMP_DIR.mkdir(exist_ok=True)
SETTINGS_FILE = Path("user_settings.json")

# ========== ПРЕМИУМ ЭМОДЗИ ==========
E = {
    "menu": "5870982283724328568",
    "tiktok": "5870528606328852614",
    "pinterest": "6035128606563241721",
    "sticker": "5771851822897566479",
    "color": "5778479949572738874",
    "mirror_h": "5891207662678317861",
    "mirror_v": "5893192487324880883",
    "check": "5870633910337015697",
    "cross": "5870657884844462243",
    "back": "5893057118545646106",
    "download": "6039802767931871481",
    "send": "5963103826075456248",
    "loading": "5345906554510012647",
    "sparkles": "6041731551845159060",
    "profile": "5870994129244131212",
    "settings": "5870982283724328568",
    "pencil": "5870676941614354370",
    "trash": "5870875489362513438",
    "info": "6028435952299413210",
    "link": "5769289093221454192",
    "mirror_icon": "6030400221232501136",
    "robot": "6030400221232501136",
}

def e(key: str) -> str:
    """Премиум эмодзи — без текста внутри тега"""
    pid = E.get(key, "")
    if pid:
        return f'<tg-emoji emoji-id="{pid}"></tg-emoji>'
    return ""

def btn(key: str, text: str, cb: str) -> InlineKeyboardButton:
    return InlineKeyboardButton(
        text=text,
        callback_data=cb,
        icon_custom_emoji_id=E.get(key)
    )

def btn_url(key: str, text: str, url: str) -> InlineKeyboardButton:
    return InlineKeyboardButton(
        text=text,
        url=url,
        icon_custom_emoji_id=E.get(key)
    )

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
    "horizontal":  "Горизонтально",
    "vertical":    "Вертикально",
    "both":        "Гориз. + Верт.",
}

class StickerStates(StatesGroup):
    waiting_for_image = State()
    waiting_for_color = State()

def load_settings():
    if SETTINGS_FILE.exists():
        return json.loads(SETTINGS_FILE.read_text(encoding="utf-8"))
    return {}

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

# ========== ОБРАБОТКА ИЗОБРАЖЕНИЙ ==========
async def download_bytes(url: str) -> bytes:
    async with aiohttp.ClientSession() as s:
        async with s.get(url, timeout=60) as r:
            return await r.read()

def recolor_image(img: Image.Image, hex_color: str) -> Image.Image:
    img = img.convert("RGBA")
    _, _, _, a = img.split()
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

async def validate_token(token: str):
    try:
        test_bot = Bot(token=token)
        info = await test_bot.get_me()
        await test_bot.session.close()
        return True, info.username
    except Exception as ex:
        return False, str(ex)

# ========== КЛАВИАТУРЫ ==========
def main_menu():
    return InlineKeyboardMarkup(inline_keyboard=[
        [
            btn("tiktok", "TikTok", "mode_tiktok"),
            btn("pinterest", "Pinterest", "mode_pinterest"),
        ],
        [btn("sticker", "Редактор стикеров", "mode_sticker")],
        [btn("mirror_icon", "Зеркало бота", "mode_mirror")],
        [btn("settings", "Настройки", "open_settings")],
    ])

def back_btn():
    return InlineKeyboardMarkup(inline_keyboard=[
        [btn("back", "Назад", "back_main")]
    ])

def settings_menu(user_id):
    s = get_user_settings(user_id)
    return InlineKeyboardMarkup(inline_keyboard=[
        [btn("color", f"Цвет: {s['color']}", "set_color")],
        [btn("mirror_h", f"Зеркало: {MIRROR_MODES[s['mirror']]}", "set_mirror")],
        [btn("back", "Назад", "back_main")],
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
    btns.append([btn("pencil", "Свой HEX", "custom_color")])
    btns.append([btn("back", "Назад", "open_settings")])
    return InlineKeyboardMarkup(inline_keyboard=btns)

def mirror_menu():
    btns = []
    for key, name in MIRROR_MODES.items():
        btns.append([InlineKeyboardButton(text=name, callback_data=f"setmir_{key}")])
    btns.append([btn("back", "Назад", "open_settings")])
    return InlineKeyboardMarkup(inline_keyboard=btns)

# ========== ЗЕРКАЛО (ОДИН EVENT LOOP) ==========
async def create_mirror_task(token: str):
    """Запускает зеркало как asyncio.Task"""
    mirror_bot = Bot(token=token, default=DefaultBotProperties(parse_mode=ParseMode.HTML))
    mirror_dp = Dispatcher(storage=MemoryStorage())
    
    @mirror_dp.message(Command("start"))
    async def m_start(msg: Message):
        await msg.answer(
            f"<b>{e('sparkles')} Привет! Я зеркало бота!</b>\n\n"
            f"<b>Я умею то же, что и основной бот:</b>\n"
            f"{e('tiktok')} Скачивать TikTok\n"
            f"{e('pinterest')} Скачивать Pinterest\n"
            f"{e('sticker')} Перекрашивать стикеры\n\n"
            "<b>Выбери действие:</b>",
            reply_markup=main_menu()
        )
    
    @mirror_dp.callback_query()
    async def m_callback(cb: CallbackQuery, state: FSMContext):
        data = cb.data
        
        if data == "back_main":
            await cb.message.edit_text(
                f"<b>{e('sparkles')} Главное меню (зеркало):</b>",
                reply_markup=main_menu()
            )
        elif data == "mode_tiktok":
            await cb.message.edit_text(
                f"<b>{e('tiktok')} Отправь ссылку на TikTok:</b>",
                reply_markup=back_btn()
            )
        elif data == "mode_pinterest":
            await cb.message.edit_text(
                f"<b>{e('pinterest')} Отправь ссылку на Pinterest:</b>",
                reply_markup=back_btn()
            )
        elif data == "mode_sticker":
            await cb.message.edit_text(
                f"<b>{e('sticker')} Отправь стикер, эмодзи или картинку:</b>",
                reply_markup=back_btn()
            )
            await state.set_state(StickerStates.waiting_for_image)
        elif data == "mode_mirror":
            active = len(mirrors)
            await cb.message.edit_text(
                f"<b>{e('mirror_icon')} Зеркала:</b>\nАктивных: {active}",
                reply_markup=back_btn()
            )
        elif data == "open_settings":
            await cb.message.edit_text(
                f"<b>{e('settings')} Настройки:</b>",
                reply_markup=settings_menu(cb.from_user.id)
            )
        elif data == "set_color":
            await cb.message.edit_text(
                f"<b>{e('color')} Выбери цвет:</b>",
                reply_markup=color_menu()
            )
        elif data.startswith("setcl_"):
            hex_ = data.replace("setcl_", "")
            set_user_setting(cb.from_user.id, "color", hex_)
            await cb.message.edit_text(
                f"<b>{e('check')} Цвет: {hex_}</b>",
                reply_markup=settings_menu(cb.from_user.id)
            )
        elif data == "custom_color":
            await cb.message.edit_text(
                f"<b>{e('pencil')} Отправь HEX:</b>",
                reply_markup=InlineKeyboardMarkup(inline_keyboard=[
                    [btn("back", "Назад", "set_color")]
                ])
            )
            await state.set_state(StickerStates.waiting_for_color)
        elif data == "set_mirror":
            await cb.message.edit_text(
                f"<b>{e('mirror_h')} Режим зеркала:</b>",
                reply_markup=mirror_menu()
            )
        elif data.startswith("setmir_"):
            mode = data.replace("setmir_", "")
            set_user_setting(cb.from_user.id, "mirror", mode)
            await cb.message.edit_text(
                f"<b>{e('check')} Зеркало: {MIRROR_MODES[mode]}</b>",
                reply_markup=settings_menu(cb.from_user.id)
            )
        
        await cb.answer()
    
    @mirror_dp.message(StickerStates.waiting_for_color, F.text)
    async def m_color(msg: Message, state: FSMContext):
        color = msg.text.strip()
        if not color.startswith("#"):
            color = "#" + color
        try:
            ImageColor.getrgb(color)
            set_user_setting(msg.from_user.id, "color", color)
            await msg.answer(
                f"<b>{e('check')} Цвет: {color}</b>",
                reply_markup=settings_menu(msg.from_user.id)
            )
            await state.clear()
        except:
            await msg.answer(f"<b>{e('cross')} Неверный HEX!</b>")
    
    @mirror_dp.message(StickerStates.waiting_for_image, F.photo | F.document | F.sticker)
    async def m_sticker(msg: Message, state: FSMContext):
        settings = get_user_settings(msg.from_user.id)
        status = await msg.answer(f"<b>{e('loading')} Обрабатываю...</b>")
        
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
            caption=f"<b>{e('check')} Готово! Цвет: {settings['color']}</b>",
            reply_markup=InlineKeyboardMarkup(inline_keyboard=[
                [btn("sticker", "Ещё", "mode_sticker"), btn("back", "Меню", "back_main")]
            ])
        )
        await status.delete()
        await state.clear()
    
    @mirror_dp.message(F.text.regexp(r'https?://(?:www\.)?(?:tiktok\.com|vm\.tiktok\.com|vt\.tiktok\.com)/\S+'))
    async def m_tiktok(msg: Message):
        url = msg.text.strip()
        status = await msg.answer(f"<b>{e('loading')} Скачиваю...</b>")
        try:
            async with aiohttp.ClientSession() as s:
                async with s.get("https://tikwm.com/api/", params={"url": url, "hd": 1}, timeout=30) as r:
                    data = await r.json()
                    if data.get("code") == 0:
                        v = data["data"]
                        video_url = v.get("hdplay") or v.get("play")
                        author = v.get("author", {}).get("nickname", "unknown")
                        video = await download_bytes(video_url)
                        
                        fp = TEMP_DIR / f"m_{uuid.uuid4().hex}.mp4"
                        fp.write_bytes(video)
                        
                        if fp.stat().st_size <= 50 * 1024 * 1024:
                            await msg.reply_video(
                                video=FSInputFile(fp),
                                caption=f"<b>{e('sparkles')} Готово!</b>\n@{author}",
                                reply_markup=InlineKeyboardMarkup(inline_keyboard=[
                                    [btn_url("profile", f"@{author}", f"https://tiktok.com/@{author}")],
                                    [btn("back", "Меню", "back_main")]
                                ])
                            )
                        else:
                            await msg.answer(f"<b>{e('cross')} >50 МБ</b>")
                        fp.unlink(missing_ok=True)
                    else:
                        await status.edit_text(f"<b>{e('cross')} Ошибка</b>")
                        return
        except:
            await status.edit_text(f"<b>{e('cross')} Ошибка</b>")
            return
        await status.delete()
    
    @mirror_dp.message(F.text.regexp(r'https?://(?:pin\.it|(?:www\.)?pinterest\.\w+/pin)/\S+'))
    async def m_pinterest(msg: Message):
        url = msg.text.strip()
        status = await msg.answer(f"<b>{e('loading')} Скачиваю...</b>")
        try:
            async with aiohttp.ClientSession() as s:
                async with s.get(f"https://api.pinterest.com/v3/pidgets/pins/info/?pin={url}", timeout=30) as r:
                    data = await r.json()
                    img_url = data.get("data", {}).get("images", {}).get("orig", {}).get("url")
                    if img_url:
                        img_bytes = await download_bytes(img_url)
                        await msg.reply_photo(
                            photo=BufferedInputFile(img_bytes, filename="pin.jpg"),
                            caption=f"<b>{e('sparkles')} Готово!</b>",
                            reply_markup=InlineKeyboardMarkup(inline_keyboard=[
                                [btn("back", "Меню", "back_main")]
                            ])
                        )
                    else:
                        await status.edit_text(f"<b>{e('cross')} Не найдено</b>")
                        return
        except:
            await status.edit_text(f"<b>{e('cross')} Ошибка</b>")
            return
        await status.delete()
    
    @mirror_dp.message()
    async def m_fallback(msg: Message):
        await msg.answer(
            f"<b>{e('info')} Используй меню или отправь ссылку.</b>",
            reply_markup=main_menu()
        )
    
    task = asyncio.create_task(mirror_dp.start_polling(mirror_bot))
    mirrors[token] = {"bot": mirror_bot, "dp": mirror_dp, "task": task}
    
    try:
        info = await mirror_bot.get_me()
        mirrors[token]["info"] = {"username": info.username, "name": info.full_name}
    except:
        mirrors[token]["info"] = {"username": "unknown", "name": "unknown"}
    
    logger.info(f"Зеркало запущено: @{mirrors[token]['info']['username']}")
    return mirrors[token]["info"]

# ========== КОМАНДА /mirror ==========
@dp.message(Command("mirror"))
async def cmd_mirror(msg: Message, command: CommandObject):
    token = command.args.strip() if command.args else None
    
    if not token:
        active = len(mirrors)
        txt = f"<b>{e('mirror_icon')} Создание зеркала:</b>\n\nАктивных: {active}\n\n"
        if mirrors:
            txt += "<b>Список:</b>\n"
            for t, m in mirrors.items():
                info = m.get("info", {})
                txt += f"• @{info.get('username', '?')} — <code>{t[:12]}...</code>\n"
        txt += f"\n<code>/mirror ТОКЕН</code> — создать новое"
        await msg.answer(txt)
        return
    
    if token in mirrors:
        info = mirrors[token].get("info", {})
        await msg.answer(
            f"<b>{e('info')} Это зеркало уже запущено!</b>\n"
            f"Бот: @{info.get('username', '?')}"
        )
        return
    
    status = await msg.answer(f"<b>{e('loading')} Проверяю токен...</b>")
    
    valid, info = await validate_token(token)
    if not valid:
        await status.edit_text(
            f"<b>{e('cross')} Токен невалиден!</b>\n<i>{info}</i>"
        )
        return
    
    await status.edit_text(f"<b>{e('loading')} Запускаю зеркало @{info}...</b>")
    
    result = await create_mirror_task(token)
    
    await status.edit_text(
        f"<b>{e('check')} Зеркало запущено!</b>\n\n"
        f"Бот: @{result['username']}\n"
        f"Имя: {result['name']}\n"
        f"Токен: <code>{token[:12]}...</code>",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=[
            [btn("back", "В меню", "back_main")]
        ])
    )

@dp.message(Command("mirrors"))
async def cmd_mirrors(msg: Message):
    if not mirrors:
        await msg.answer(
            f"<b>{e('info')} Нет активных зеркал.</b>\n"
            f"<code>/mirror ТОКЕН</code> — создать"
        )
        return
    
    txt = f"<b>{e('mirror_icon')} Зеркала ({len(mirrors)}):</b>\n\n"
    for i, (token, m) in enumerate(mirrors.items(), 1):
        info = m.get("info", {})
        txt += f"{i}. @{info.get('username', '?')} — <code>{token[:12]}...</code>\n"
    
    await msg.answer(txt)

@dp.callback_query(F.data == "mode_mirror")
async def cb_mirror(cb: CallbackQuery):
    active = len(mirrors)
    txt = f"<b>{e('mirror_icon')} Зеркало бота:</b>\n\nАктивных: {active}\n\n"
    
    if mirrors:
        txt += "<b>Список:</b>\n"
        for token, m in mirrors.items():
            info = m.get("info", {})
            txt += f"• @{info.get('username', '?')}\n"
    
    txt += f"\n<b>Создать:</b>\n<code>/mirror ТОКЕН</code>"
    
    await cb.message.edit_text(txt, reply_markup=back_btn())
    await cb.answer()

# ========== СТАРТ ==========
@dp.message(Command("start"))
async def start(msg: Message):
    await msg.answer(
        f"<b>{e('sparkles')} Привет! Я умею:</b>\n\n"
        f"{e('tiktok')} Скачивать видео из TikTok\n"
        f"{e('pinterest')} Скачивать фото из Pinterest\n"
        f"{e('sticker')} Перекрашивать стикеры\n"
        f"{e('mirror_icon')} Создавать зеркала бота\n\n"
        "<b>Выбери действие:</b>",
        reply_markup=main_menu()
    )

# ========== CALLBACKS ==========
@dp.callback_query(F.data == "back_main")
async def cb_back_main(cb: CallbackQuery):
    await cb.message.edit_text(
        f"<b>{e('sparkles')} Главное меню:</b>",
        reply_markup=main_menu()
    )
    await cb.answer()

@dp.callback_query(F.data == "mode_tiktok")
async def cb_tiktok(cb: CallbackQuery):
    await cb.message.edit_text(
        f"<b>{e('tiktok')} Отправь ссылку на TikTok:</b>",
        reply_markup=back_btn()
    )
    await cb.answer()

@dp.callback_query(F.data == "mode_pinterest")
async def cb_pinterest(cb: CallbackQuery):
    await cb.message.edit_text(
        f"<b>{e('pinterest')} Отправь ссылку на Pinterest:</b>",
        reply_markup=back_btn()
    )
    await cb.answer()

@dp.callback_query(F.data == "mode_sticker")
async def cb_sticker(cb: CallbackQuery, state: FSMContext):
    await cb.message.edit_text(
        f"<b>{e('sticker')} Отправь стикер, эмодзи или картинку:</b>",
        reply_markup=back_btn()
    )
    await state.set_state(StickerStates.waiting_for_image)
    await cb.answer()

@dp.callback_query(F.data == "open_settings")
async def cb_settings(cb: CallbackQuery):
    await cb.message.edit_text(
        f"<b>{e('settings')} Настройки:</b>",
        reply_markup=settings_menu(cb.from_user.id)
    )
    await cb.answer()

@dp.callback_query(F.data == "set_color")
async def cb_set_color(cb: CallbackQuery):
    await cb.message.edit_text(
        f"<b>{e('color')} Выбери цвет:</b>",
        reply_markup=color_menu()
    )
    await cb.answer()

@dp.callback_query(F.data.startswith("setcl_"))
async def cb_color_select(cb: CallbackQuery):
    hex_ = cb.data.replace("setcl_", "")
    set_user_setting(cb.from_user.id, "color", hex_)
    await cb.message.edit_text(
        f"<b>{e('check')} Цвет: {hex_}</b>",
        reply_markup=settings_menu(cb.from_user.id)
    )
    await cb.answer()

@dp.callback_query(F.data == "custom_color")
async def cb_custom_color(cb: CallbackQuery, state: FSMContext):
    await cb.message.edit_text(
        f"<b>{e('pencil')} Отправь HEX:</b>",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=[
            [btn("back", "Назад", "set_color")]
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
            f"<b>{e('check')} Цвет: {color}</b>",
            reply_markup=settings_menu(msg.from_user.id)
        )
        await state.clear()
    except:
        await msg.answer(f"<b>{e('cross')} Неверный HEX!</b>")

@dp.callback_query(F.data == "set_mirror")
async def cb_set_mirror(cb: CallbackQuery):
    await cb.message.edit_text(
        f"<b>{e('mirror_h')} Режим зеркала:</b>",
        reply_markup=mirror_menu()
    )
    await cb.answer()

@dp.callback_query(F.data.startswith("setmir_"))
async def cb_mirror_select(cb: CallbackQuery):
    mode = cb.data.replace("setmir_", "")
    set_user_setting(cb.from_user.id, "mirror", mode)
    await cb.message.edit_text(
        f"<b>{e('check')} Зеркало: {MIRROR_MODES[mode]}</b>",
        reply_markup=settings_menu(cb.from_user.id)
    )
    await cb.answer()

# ========== СТИКЕРЫ ==========
@dp.message(StickerStates.waiting_for_image, F.photo | F.document | F.sticker)
async def process_sticker(msg: Message, state: FSMContext):
    settings = get_user_settings(msg.from_user.id)
    status = await msg.answer(f"<b>{e('loading')} Обрабатываю...</b>")

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
        caption=f"<b>{e('check')} Готово! Цвет: {settings['color']}</b>",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=[
            [btn("sticker", "Ещё", "mode_sticker"), btn("back", "Меню", "back_main")]
        ])
    )
    await status.delete()
    await state.clear()

# ========== TIKTOK ==========
@dp.message(F.text.regexp(r'https?://(?:www\.)?(?:tiktok\.com|vm\.tiktok\.com|vt\.tiktok\.com)/\S+'))
async def download_tiktok(msg: Message):
    url = msg.text.strip()
    status = await msg.answer(f"<b>{e('loading')} Скачиваю...</b>")
    
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
                        await status.edit_text(f"<b>{e('send')} Отправляю...</b>")
                        await msg.reply_video(
                            video=FSInputFile(fp),
                            caption=f"<b>{e('sparkles')} Готово!</b>\n@{author}",
                            reply_markup=InlineKeyboardMarkup(inline_keyboard=[
                                [btn_url("profile", f"@{author}", f"https://tiktok.com/@{author}")],
                                [btn("back", "Меню", "back_main")]
                            ])
                        )
                    else:
                        await status.edit_text(f"<b>{e('cross')} Видео >50 МБ</b>")
                    fp.unlink(missing_ok=True)
                    await status.delete()
                else:
                    await status.edit_text(f"<b>{e('cross')} Не удалось скачать</b>")
    except:
        await status.edit_text(f"<b>{e('cross')} Ошибка</b>")

# ========== PINTEREST ==========
@dp.message(F.text.regexp(r'https?://(?:pin\.it|(?:www\.)?pinterest\.\w+/pin)/\S+'))
async def download_pinterest(msg: Message):
    url = msg.text.strip()
    status = await msg.answer(f"<b>{e('loading')} Скачиваю...</b>")
    
    try:
        async with aiohttp.ClientSession() as s:
            async with s.get(f"https://api.pinterest.com/v3/pidgets/pins/info/?pin={url}", timeout=30) as r:
                data = await r.json()
                img_url = data.get("data", {}).get("images", {}).get("orig", {}).get("url")
                if img_url:
                    img_bytes = await download_bytes(img_url)
                    await msg.reply_photo(
                        photo=BufferedInputFile(img_bytes, filename="pin.jpg"),
                        caption=f"<b>{e('sparkles')} Готово!</b>",
                        reply_markup=InlineKeyboardMarkup(inline_keyboard=[
                            [btn("back", "Меню", "back_main")]
                        ])
                    )
                    await status.delete()
                else:
                    await status.edit_text(f"<b>{e('cross')} Не найдено</b>")
    except:
        await status.edit_text(f"<b>{e('cross')} Ошибка</b>")

# ========== FALLBACK ==========
@dp.message()
async def fallback(msg: Message):
    await msg.answer(
        f"<b>{e('info')} Используй меню или отправь ссылку.</b>",
        reply_markup=main_menu()
    )

# ========== ЗАПУСК ==========
async def main():
    logger.info("Бот запущен!")
    await dp.start_polling(bot)

if __name__ == "__main__":
    asyncio.run(main())
