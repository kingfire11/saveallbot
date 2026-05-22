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
MAX_SIZE = 50 * 1024 * 1024

STRINGS = {
    "ru": {
        "choose_lang": "Выбери язык:",
        "lang_set": "Язык: Русский 🇷🇺\n\nОтправь мне ссылку на фото или видео.",
        "send_link": "Отправь мне ссылку на фото или видео.",
        "downloading": "⏳ Скачиваю...",
        "choose_quality": "Выбери качество:",
        "too_large": "❌ Файл больше 50 МБ — не могу отправить через Telegram.",
        "error": "❌ Не удалось скачать. Проверь ссылку.",
        "not_url": "Это не похоже на ссылку. Отправь URL.",
    },
    "en": {
        "choose_lang": "Choose language:",
        "lang_set": "Language: English 🇬🇧\n\nSend me a link to a photo or video.",
        "send_link": "Send me a link to a photo or video.",
        "downloading": "⏳ Downloading...",
        "choose_quality": "Choose quality:",
        "too_large": "❌ File is larger than 50 MB — can't send via Telegram.",
        "error": "❌ Failed to download. Check the link.",
        "not_url": "That doesn't look like a URL. Send a link.",
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
    _, fmt, url = query.data.split(":", 2)
    uid = query.from_user.id
    msg = await query.edit_message_text(t(uid, "downloading"))
    await do_download(url, fmt, uid, query.message.chat_id, context, msg)


async def handle_url(update: Update, context: ContextTypes.DEFAULT_TYPE):
    uid = update.effective_user.id
    text = update.message.text.strip()

    if not text.startswith(("http://", "https://")):
        await update.message.reply_text(t(uid, "not_url"))
        return

    msg = await update.message.reply_text(t(uid, "downloading"))
    loop = asyncio.get_event_loop()

    try:
        info = await loop.run_in_executor(None, lambda: _get_info(text))
    except Exception:
        await msg.edit_text(t(uid, "error"))
        return

    formats = info.get("formats") or []
    has_video = any(f.get("vcodec") not in (None, "none") for f in formats)

    if has_video:
        available = _available_qualities(formats)
        if len(available) > 1:
            kb = [
                [InlineKeyboardButton(label, callback_data=f"quality:{fid}:{text}")]
                for label, fid in available
            ]
            await msg.edit_text(t(uid, "choose_quality"), reply_markup=InlineKeyboardMarkup(kb))
            return

    await do_download(text, "best", uid, update.effective_chat.id, context, msg)


def _get_info(url: str) -> dict:
    with yt_dlp.YoutubeDL({"quiet": True, "no_warnings": True, "skip_download": True}) as ydl:
        return ydl.extract_info(url, download=False)


def _download_file(url: str, fmt: str, out_tmpl: str):
    opts = {
        "quiet": True,
        "no_warnings": True,
        "format": fmt,
        "outtmpl": out_tmpl,
        "merge_output_format": "mp4",
    }
    with yt_dlp.YoutubeDL(opts) as ydl:
        ydl.download([url])


def _available_qualities(formats: list) -> list:
    heights = {int(f["height"]) for f in formats if f.get("height")}
    result = []
    for label, max_h, fid in [
        ("1080p", 1080, "bestvideo[height<=1080]+bestaudio/best[height<=1080]"),
        ("720p",  720,  "bestvideo[height<=720]+bestaudio/best[height<=720]"),
        ("480p",  480,  "bestvideo[height<=480]+bestaudio/best[height<=480]"),
        ("360p",  360,  "bestvideo[height<=360]+bestaudio/best[height<=360]"),
    ]:
        if any(h <= max_h for h in heights):
            result.append((label, fid))
    if not result:
        result.append(("Best", "best"))
    return result


async def do_download(url: str, fmt: str, uid: int, chat_id: int, context, status_msg):
    tmp_id = str(uuid.uuid4())
    out_tmpl = f"/tmp/{tmp_id}.%(ext)s"
    loop = asyncio.get_event_loop()

    try:
        await loop.run_in_executor(None, lambda: _download_file(url, fmt, out_tmpl))
    except Exception:
        await status_msg.edit_text(t(uid, "error"))
        return

    actual = next(
        (f"/tmp/{f}" for f in os.listdir("/tmp") if f.startswith(tmp_id)),
        None
    )

    if not actual:
        await status_msg.edit_text(t(uid, "error"))
        return

    try:
        if os.path.getsize(actual) > MAX_SIZE:
            await status_msg.edit_text(t(uid, "too_large"))
            return

        ext = actual.rsplit(".", 1)[-1].lower()
        with open(actual, "rb") as fh:
            if ext in ("jpg", "jpeg", "png", "webp"):
                await context.bot.send_photo(chat_id=chat_id, photo=fh)
            elif ext == "gif":
                await context.bot.send_animation(chat_id=chat_id, animation=fh)
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
