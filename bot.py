import os
import uuid
import asyncio
import yt_dlp
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import (
    Application, CommandHandler, MessageHandler,
    CallbackQueryHandler, ContextTypes, filters
)

TOKEN = "7634616460:AAGPJK4Uck_oLGd9ghGkJxrWoJUUnfbK1Z8"

STRINGS = {
    "ru": {
        "start": "Привет! Отправь мне ссылку на фото или видео.",
        "choose_lang": "Выбери язык / Choose language:",
        "send_link": "Отправь мне ссылку.",
        "downloading": "⏳ Скачиваю...",
        "choose_quality": "Выбери качество:",
        "too_large": "❌ Файл больше 50 МБ, не могу отправить.",
        "error": "❌ Ошибка при скачивании.",
        "lang_set": "Язык установлен: Русский 🇷🇺",
    },
    "en": {
        "start": "Hi! Send me a link to a photo or video.",
        "choose_lang": "Choose language / Выбери язык:",
        "send_link": "Send me a link.",
        "downloading": "⏳ Downloading...",
        "choose_quality": "Choose quality:",
        "too_large": "❌ File is larger than 50 MB, can't send.",
        "error": "❌ Error downloading.",
        "lang_set": "Language set: English 🇬🇧",
    },
}

user_lang: dict[int, str] = {}

def t(uid: int, key: str) -> str:
    return STRINGS[user_lang.get(uid, "en")][key]


async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    kb = [[
        InlineKeyboardButton("Русский 🇷🇺", callback_data="lang:ru"),
        InlineKeyboardButton("English 🇬🇧", callback_data="lang:en"),
    ]]
    await update.message.reply_text(
        STRINGS["en"]["choose_lang"],
        reply_markup=InlineKeyboardMarkup(kb),
    )


async def lang_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    lang = query.data.split(":")[1]
    user_lang[query.from_user.id] = lang
    await query.edit_message_text(STRINGS[lang]["lang_set"])


async def quality_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    parts = query.data.split(":", 2)  # quality:<fmt>:<url>
    fmt, url = parts[1], parts[2]
    uid = query.from_user.id
    msg = await query.edit_message_text(t(uid, "downloading"))
    await do_download(url, fmt, uid, query.message.chat_id, context, msg)


async def handle_url(update: Update, context: ContextTypes.DEFAULT_TYPE):
    uid = update.effective_user.id
    url = update.message.text.strip()
    msg = await update.message.reply_text(t(uid, "downloading"))

    loop = asyncio.get_event_loop()
    try:
        info = await loop.run_in_executor(None, lambda: _get_info(url))
    except Exception:
        await msg.edit_text(t(uid, "error"))
        return

    formats = info.get("formats") or []
    is_video = info.get("_type") != "photo" and any(
        f.get("vcodec") not in (None, "none") for f in formats
    )

    # If video with multiple qualities, show keyboard
    if is_video and formats:
        available = _available_qualities(formats)
        if len(available) > 1:
            kb = [
                [InlineKeyboardButton(q, callback_data=f"quality:{fid}:{url}")]
                for q, fid in available
            ]
            await msg.edit_text(
                t(uid, "choose_quality"),
                reply_markup=InlineKeyboardMarkup(kb),
            )
            return

    await do_download(url, "best", uid, update.effective_chat.id, context, msg)


def _available_qualities(formats):
    seen = set()
    result = []
    labels = [
        ("1080p", "bestvideo[height<=1080]+bestaudio/best[height<=1080]"),
        ("720p",  "bestvideo[height<=720]+bestaudio/best[height<=720]"),
        ("480p",  "bestvideo[height<=480]+bestaudio/best[height<=480]"),
        ("360p",  "bestvideo[height<=360]+bestaudio/best[height<=360]"),
        ("best",  "best"),
    ]
    heights = {int(f["height"]) for f in formats if f.get("height")}
    for label, fid in labels:
        if label == "best":
            result.append(("Best", fid))
        else:
            h = int(label[:-1])
            if any(x <= h for x in heights) and label not in seen:
                seen.add(label)
                result.append((label, fid))
    return result


def _get_info(url):
    ydl_opts = {"quiet": True, "no_warnings": True, "skip_download": True}
    with yt_dlp.YoutubeDL(ydl_opts) as ydl:
        return ydl.extract_info(url, download=False)


def _download_file(url: str, fmt: str, path: str):
    ydl_opts = {
        "quiet": True,
        "no_warnings": True,
        "format": fmt,
        "outtmpl": path,
        "merge_output_format": "mp4",
    }
    with yt_dlp.YoutubeDL(ydl_opts) as ydl:
        ydl.download([url])


async def do_download(url, fmt, uid, chat_id, context, status_msg):
    tmp_base = f"/tmp/{uuid.uuid4()}"
    tmp_path = tmp_base + ".%(ext)s"

    loop = asyncio.get_event_loop()
    try:
        await loop.run_in_executor(None, lambda: _download_file(url, fmt, tmp_base + ".%(ext)s"))
    except Exception:
        await status_msg.edit_text(t(uid, "error"))
        return

    # Find the actual downloaded file
    actual = None
    for f in os.listdir("/tmp"):
        if f.startswith(os.path.basename(tmp_base)):
            actual = f"/tmp/{f}"
            break

    if not actual or not os.path.exists(actual):
        await status_msg.edit_text(t(uid, "error"))
        return

    size = os.path.getsize(actual)
    if size > 50 * 1024 * 1024:
        os.remove(actual)
        await status_msg.edit_text(t(uid, "too_large"))
        return

    ext = actual.rsplit(".", 1)[-1].lower()
    try:
        with open(actual, "rb") as fh:
            if ext in ("jpg", "jpeg", "png", "webp", "gif"):
                await context.bot.send_photo(chat_id=chat_id, photo=fh)
            else:
                await context.bot.send_video(chat_id=chat_id, video=fh, supports_streaming=True)
        await status_msg.delete()
    except Exception:
        await status_msg.edit_text(t(uid, "error"))
    finally:
        if os.path.exists(actual):
            os.remove(actual)


def main():
    app = Application.builder().token(TOKEN).build()
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CallbackQueryHandler(lang_callback, pattern=r"^lang:"))
    app.add_handler(CallbackQueryHandler(quality_callback, pattern=r"^quality:"))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_url))
    app.run_polling(drop_pending_updates=True)


if __name__ == "__main__":
    main()
