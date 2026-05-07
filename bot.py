import asyncio
import logging
import os
import re
import uuid
import aiohttp
from pathlib import Path
from aiogram import Bot, Dispatcher, F
from aiogram.types import Message, FSInputFile, InlineKeyboardMarkup, InlineKeyboardButton
from aiogram.enums import ParseMode
from aiogram.client.default import DefaultBotProperties
from aiogram.filters import Command

# ---------- ТОКЕН ----------
BOT_TOKEN = os.getenv("BOT_TOKEN")  # Бери из переменных окружения

# ---------- ЛОГИ ----------
logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(levelname)s - %(message)s")
logger = logging.getLogger(__name__)

# ---------- БОТ ----------
bot = Bot(token=BOT_TOKEN, default=DefaultBotProperties(parse_mode=ParseMode.HTML))
dp = Dispatcher()

# ---------- ПАПКА ДЛЯ ВИДЕО ----------
TEMP_DIR = Path("temp_videos")
TEMP_DIR.mkdir(exist_ok=True)

# ---------- API ДЛЯ СКАЧИВАНИЯ ----------
TIKTOK_API = "https://tikwm.com/api/"


async def get_tiktok_video(url: str) -> dict | None:
    """Получает прямую ссылку на видео через TikTok API"""
    params = {"url": url, "hd": 1}
    try:
        async with aiohttp.ClientSession() as session:
            async with session.get(TIKTOK_API, params=params, timeout=30) as resp:
                data = await resp.json()
                logger.info(f"Ответ API: {data}")

                if data.get("code") == 0 and data.get("data"):
                    video_data = data["data"]
                    # Пробуем HD, если нет — обычное качество
                    video_url = video_data.get("hdplay") or video_data.get("play")
                    if video_url:
                        return {
                            "url": video_url,
                            "title": video_data.get("title", "TikTok"),
                            "author": video_data.get("author", {}).get("nickname", "Unknown"),
                            "duration": video_data.get("duration", 0),
                            "cover": video_data.get("cover"),
                        }
                return None
    except Exception as e:
        logger.error(f"Ошибка получения данных: {e}")
        return None


async def download_video(url: str, filename: str) -> str | None:
    """Скачивает видео и сохраняет на диск"""
    try:
        async with aiohttp.ClientSession() as session:
            async with session.get(url, timeout=120) as resp:
                if resp.status == 200:
                    filepath = TEMP_DIR / filename
                    with open(filepath, "wb") as f:
                        f.write(await resp.read())
                    return str(filepath)
                return None
    except Exception as e:
        logger.error(f"Ошибка скачивания: {e}")
        return None


# ---------- КОМАНДА /start ----------
@dp.message(Command("start"))
async def cmd_start(message: Message):
    await message.answer(
        f'<b><tg-emoji emoji-id="5870528606328852614">📁</tg-emoji> Привет! Отправь ссылку на TikTok видео, и я скачаю его в лучшем качестве.</b>\n\n'
        f'<tg-emoji emoji-id="6028435952299413210">ℹ</tg-emoji> Просто вставь ссылку в чат.'
    )


# ---------- ОБРАБОТКА ССЫЛОК ----------
@dp.message(F.text.regexp(r'https?://(?:www\.)?(?:tiktok\.com|vm\.tiktok\.com|vt\.tiktok\.com)/\S+'))
async def handle_tiktok_link(message: Message):
    url = message.text.strip()
    logger.info(f"Получена ссылка: {url} от {message.from_user.id}")

    # Скачиваем
    status_msg = await message.answer(
        f'<b><tg-emoji emoji-id="5345906554510012647">🔄</tg-emoji> Получаю информацию о видео...</b>',
        reply_to_message_id=message.message_id
    )

    video_info = await get_tiktok_video(url)

    if not video_info or not video_info.get("url"):
        await status_msg.edit_text(
            f'<b><tg-emoji emoji-id="5870657884844462243">❌</tg-emoji> Не удалось получить видео. Проверь ссылку или попробуй позже.</b>'
        )
        return

    await status_msg.edit_text(
        f'<b><tg-emoji emoji-id="6039802767931871481">⬇</tg-emoji> Скачиваю видео...</b>'
    )

    # Генерируем уникальное имя файла
    safe_title = re.sub(r'[^\w\-_]', '_', video_info.get("title", "video"))[:50]
    filename = f"{uuid.uuid4().hex}_{safe_title}.mp4"
    filepath = await download_video(video_info["url"], filename)

    if not filepath:
        await status_msg.edit_text(
            f'<b><tg-emoji emoji-id="5870657884844462243">❌</tg-emoji> Не удалось скачать видеофайл.</b>'
        )
        return

    file_size = os.path.getsize(filepath)

    # Если файл > 50 МБ — Телеграм не пропустит (ограничение ботов)
    if file_size > 50 * 1024 * 1024:
        os.remove(filepath)
        await status_msg.edit_text(
            f'<b><tg-emoji emoji-id="5870657884844462243">❌</tg-emoji> Видео слишком большое (>50 МБ). Не могу отправить через бота.</b>'
        )
        return

    await status_msg.edit_text(
        f'<b><tg-emoji emoji-id="5963103826075456248">⬆</tg-emoji> Отправляю видео...</b>'
    )

    # Отправляем видео с премиум-эмодзи в подписи
    author = video_info.get("author", "TikTok")
    caption = (
        f'<b><tg-emoji emoji-id="5870982283724328568">⚙</tg-emoji> Видео готово!</b>\n'
        f'<tg-emoji emoji-id="5870994129244131212">👤</tg-emoji> Автор: @{author}\n'
        f'<tg-emoji emoji-id="5870528606328852614">📁</tg-emoji> Качество: HD'
    )

    video_file = FSInputFile(filepath)
    await message.reply_video(
        video=video_file,
        caption=caption,
        reply_markup=InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(
                text="Автор видео",
                url=f"https://tiktok.com/@{author}",
                icon_custom_emoji_id="5870994129244131212"  # 👤 Профиль
            )],
            [InlineKeyboardButton(
                text="Скачать ещё",
                url="https://t.me/" + (await bot.me()).username + "?start=download",
                icon_custom_emoji_id="6039802767931871481"  # ⬇ Скачать
            )]
        ])
    )

    await status_msg.delete()

    # Чистим за собой
    try:
        os.remove(filepath)
    except:
        pass


# ---------- НЕИЗВЕСТНЫЕ СООБЩЕНИЯ ----------
@dp.message()
async def unknown_message(message: Message):
    await message.answer(
        f'<b><tg-emoji emoji-id="6041731551845159060">🎉</tg-emoji> Отправь мне ссылку на TikTok видео, и я его скачаю!</b>\n'
        f'<tg-emoji emoji-id="5769289093221454192">🔗</tg-emoji> Например: https://vm.tiktok.com/... или https://www.tiktok.com/...'
    )


# ---------- ЗАПУСК ----------
async def main():
    logger.info("Бот запущен!")
    await dp.start_polling(bot)


if __name__ == "__main__":
    asyncio.run(main())
