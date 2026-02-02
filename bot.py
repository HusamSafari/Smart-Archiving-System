"""
Telegram → Google Drive Archiving Bot

Entry point: PTB Application with commands, text/media handlers,
media-group aggregation, and reactions for feedback.
"""

import asyncio
import logging
import os
from datetime import datetime
from dotenv import load_dotenv
from telegram import Update, ReactionTypeEmoji
from telegram.ext import (
    Application,
    CommandHandler,
    ContextTypes,
    MessageHandler,
    filters,
)

from drive_uploader import DriveUploader, FileTooLargeError, MimeNotAllowedError
from topic_manager import TopicManager

load_dotenv()

# Env config
TELEGRAM_BOT_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN")
GOOGLE_SERVICE_ACCOUNT_JSON = os.environ.get("GOOGLE_SERVICE_ACCOUNT_JSON")
DEFAULT_DRIVE_FOLDER_ID = os.environ.get("DEFAULT_DRIVE_FOLDER_ID")
MAX_FILE_SIZE_BYTES = int(os.environ.get("MAX_FILE_SIZE_BYTES", "20971520"))  # 20 MB
ALLOWED_MIME_TYPES_STR = os.environ.get("ALLOWED_MIME_TYPES", "")
ALLOWED_MIME_TYPES = [s.strip() for s in ALLOWED_MIME_TYPES_STR.split(",") if s.strip()] or None
TEXT_FORMAT = os.environ.get("TEXT_FORMAT", "txt").lower() or "txt"
SEND_DETAILED_ERRORS = os.environ.get("SEND_DETAILED_ERRORS", "false").lower() in ("true", "1", "yes")
LOG_LEVEL = os.environ.get("LOG_LEVEL", "INFO").upper()

# Reactions
REACTION_PROCESSING = [ReactionTypeEmoji(emoji="\u270d\ufe0f")]  # ✍️
REACTION_SUCCESS = [ReactionTypeEmoji(emoji="👍")]
REACTION_ERROR = [ReactionTypeEmoji(emoji="\U0001f937\u200d\u2642\ufe0f")]  # 🤷‍♂️

# Media group buffer: media_group_id -> list of (message, context); processed after delay
MEDIA_GROUP_DELAY_SEC = 1.5
_media_group_buffer: dict[str, list[tuple]] = {}
_media_group_lock = asyncio.Lock()
_media_group_tasks: dict[str, asyncio.Task] = {}

logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    level=getattr(logging, LOG_LEVEL, logging.INFO),
)
logger = logging.getLogger(__name__)

# Globals set in main()
topic_manager: TopicManager
drive_uploader: DriveUploader
loop: asyncio.AbstractEventLoop


async def set_reaction(context: ContextTypes.DEFAULT_TYPE, chat_id: int, message_id: int, reaction_list: list):
    """Set message reaction (processing / success / error)."""
    try:
        await context.bot.set_message_reaction(
            chat_id=chat_id,
            message_id=message_id,
            reaction=reaction_list,
        )
    except Exception as e:
        logger.warning("Failed to set reaction: %s", e)


def run_sync(sync_fn):
    """Run sync Drive/IO in executor so event loop is not blocked. sync_fn must take no args."""
    return loop.run_in_executor(None, sync_fn)


async def get_folder_and_hashtag(user_id: int):
    """Resolve folder_id and optional hashtag for user (async wrapper)."""
    folder_id = await topic_manager.get_folder_id_for_user(user_id)
    hashtag = await topic_manager.get_hashtag_for_user(user_id)
    return folder_id, hashtag


async def cmd_start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Handle /start."""
    await update.message.reply_text(
        "Hi! I archive your messages and files to Google Drive.\n\n"
        "• Use /topic <name> or /work, /marketing etc. to set your current topic.\n"
        "• Use /topics to list topics, /current to see your current topic.\n"
        "• Send me any text, photo, video, voice, audio, or document and I’ll save it to the current topic’s folder."
    )


async def cmd_topic(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Handle /topic <name> — set current topic by name."""
    if not update.message or not update.message.text:
        return
    user_id = update.effective_user.id if update.effective_user else 0
    parts = update.message.text.strip().split(maxsplit=1)
    name = (parts[1] if len(parts) > 1 else "").strip().lower()
    if not name:
        await update.message.reply_text("Usage: /topic <name> (e.g. /topic work)")
        return
    ok = await topic_manager.set_user_topic(user_id, name)
    if ok:
        await update.message.reply_text(f"Topic set to: {name}")
        try:
            await set_reaction(
                context,
                update.effective_chat.id,
                update.message.id,
                REACTION_SUCCESS,
            )
        except Exception:
            pass
    else:
        await update.message.reply_text(f"Unknown topic: {name}. Use /topics to list topics.")
        try:
            await set_reaction(
                context,
                update.effective_chat.id,
                update.message.id,
                REACTION_ERROR,
            )
        except Exception:
            pass


async def cmd_topic_by_name(update: Update, context: ContextTypes.DEFAULT_TYPE, topic_name: str) -> None:
    """Handle /work, /marketing etc. — set current topic by command name."""
    if not update.message:
        return
    user_id = update.effective_user.id if update.effective_user else 0
    ok = await topic_manager.set_user_topic(user_id, topic_name)
    if ok:
        await update.message.reply_text(f"Topic set to: {topic_name}")
        try:
            await set_reaction(
                context,
                update.effective_chat.id,
                update.message.id,
                REACTION_SUCCESS,
            )
        except Exception:
            pass
    else:
        await update.message.reply_text(f"Unknown topic: {topic_name}. Use /topics to list topics.")
        try:
            await set_reaction(
                context,
                update.effective_chat.id,
                update.message.id,
                REACTION_ERROR,
            )
        except Exception:
            pass


async def cmd_topics(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Handle /topics — list topic names and descriptions."""
    topics = await topic_manager.get_all_topics()
    if not topics:
        await update.message.reply_text("No topics configured. Add entries to topics.json.")
        return
    lines = []
    for t in topics:
        name = t.get("name", "?")
        desc = t.get("description", "")
        lines.append(f"• /{name} — {desc}")
    await update.message.reply_text("Topics:\n" + "\n".join(lines))


async def cmd_current(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Handle /current — show current topic for the user."""
    user_id = update.effective_user.id if update.effective_user else 0
    topic_name = await topic_manager.get_user_topic(user_id)
    if topic_name:
        await update.message.reply_text(f"Current topic: {topic_name}")
    else:
        await update.message.reply_text("No topic set. Use /topic <name> or /work, /marketing etc.")


async def handle_text(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Save text message to Drive (txt or Doc)."""
    if not update.message or not update.message.text:
        return
    msg = update.message
    chat_id = msg.chat_id
    message_id = msg.message_id
    user_id = update.effective_user.id if update.effective_user else 0
    username = (update.effective_user.username if update.effective_user else None) or "unknown"
    text = msg.text

    await set_reaction(context, chat_id, message_id, REACTION_PROCESSING)

    try:
        folder_id, hashtag = await get_folder_and_hashtag(user_id)
        content_parts = []
        if hashtag:
            content_parts.append(hashtag)
        content_parts.append(f"@{username}")
        content_parts.append("")
        content_parts.append(text)
        content = "\n".join(content_parts)

        ts = datetime.now().strftime("%Y%m%d_%H%M%S")
        base_name = f"Note_{ts}"

        def _upload():
            return drive_uploader.upload_text_as_file(
                folder_id, content, base_name, TEXT_FORMAT
            )

        await run_sync(_upload)
        await set_reaction(context, chat_id, message_id, REACTION_SUCCESS)
    except Exception as e:
        logger.exception("Text upload failed")
        await set_reaction(context, chat_id, message_id, REACTION_ERROR)
        if SEND_DETAILED_ERRORS:
            await msg.reply_text(f"Error: {e}")


def _get_attachment_info(message):
    """Return (file, filename, mime_type) for photo/video/voice/audio/document."""
    if message.photo:
        photo = message.photo[-1]
        ext = "jpg"
        mime = "image/jpeg"
        return photo.get_file(), f"photo_{datetime.now().strftime('%Y%m%d_%H%M%S')}.{ext}", mime
    if message.video:
        v = message.video
        mime = v.mime_type or "video/mp4"
        name = v.file_name or f"video_{datetime.now().strftime('%Y%m%d_%H%M%S')}.mp4"
        return v.get_file(), name, mime
    if message.voice:
        mime = message.voice.mime_type or "audio/ogg"
        name = f"voice_{datetime.now().strftime('%Y%m%d_%H%M%S')}.ogg"
        return message.voice.get_file(), name, mime
    if message.audio:
        a = message.audio
        mime = a.mime_type or "audio/mpeg"
        name = a.file_name or f"audio_{datetime.now().strftime('%Y%m%d_%H%M%S')}.mp3"
        return a.get_file(), name, mime
    if message.document:
        d = message.document
        mime = d.mime_type or "application/octet-stream"
        name = d.file_name or f"document_{datetime.now().strftime('%Y%m%d_%H%M%S')}"
        return d.get_file(), name, mime
    return None, None, None


async def handle_single_media(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Download one file to memory, validate, upload to Drive."""
    if not update.message:
        return
    msg = update.message
    chat_id = msg.chat_id
    message_id = msg.message_id
    user_id = update.effective_user.id if update.effective_user else 0

    file_obj, filename, mime_type = _get_attachment_info(msg)
    if not file_obj:
        return

    await set_reaction(context, chat_id, message_id, REACTION_PROCESSING)

    try:
        # Download to bytes (in memory)
        tg_file = await file_obj.get_file()
        blob = await tg_file.download_as_bytearray()
        content = bytes(blob)

        folder_id, _ = await get_folder_and_hashtag(user_id)

        def _upload():
            return drive_uploader.upload_file_bytes(
                folder_id, filename, content, mime_type
            )

        await run_sync(_upload)
        await set_reaction(context, chat_id, message_id, REACTION_SUCCESS)
    except FileTooLargeError as e:
        logger.warning("File too large: %s", e)
        await set_reaction(context, chat_id, message_id, REACTION_ERROR)
        if SEND_DETAILED_ERRORS:
            await msg.reply_text(f"File too large (max {e.max_size} bytes).")
    except MimeNotAllowedError as e:
        logger.warning("MIME not allowed: %s", e)
        await set_reaction(context, chat_id, message_id, REACTION_ERROR)
        if SEND_DETAILED_ERRORS:
            await msg.reply_text(str(e))
    except Exception as e:
        logger.exception("Media upload failed")
        await set_reaction(context, chat_id, message_id, REACTION_ERROR)
        if SEND_DETAILED_ERRORS:
            await msg.reply_text(f"Error: {e}")


async def process_media_group(media_group_id: str) -> None:
    """Collect all messages for this media_group_id, create subfolder, upload all."""
    async with _media_group_lock:
        if media_group_id not in _media_group_buffer:
            return
        entries = _media_group_buffer.pop(media_group_id)
        if media_group_id in _media_group_tasks:
            del _media_group_tasks[media_group_id]

    if not entries:
        return

    (first_msg, context) = entries[0]
    chat_id = first_msg.chat_id
    first_message_id = first_msg.message_id
    user_id = first_msg.from_user.id if first_msg.from_user else 0

    await set_reaction(context, chat_id, first_message_id, REACTION_PROCESSING)

    items = []  # (filename, bytes, mime_type)
    try:
        folder_id, _ = await get_folder_and_hashtag(user_id)

        for msg, _ in entries:
            file_obj, filename, mime_type = _get_attachment_info(msg)
            if not file_obj:
                continue
            tg_file = await file_obj.get_file()
            blob = await tg_file.download_as_bytearray()
            items.append((filename, bytes(blob), mime_type))

        if not items:
            await set_reaction(context, chat_id, first_message_id, REACTION_ERROR)
            return

        def _upload_group():
            return drive_uploader.upload_media_group(folder_id, items)

        await run_sync(_upload_group)
        await set_reaction(context, chat_id, first_message_id, REACTION_SUCCESS)
    except FileTooLargeError as e:
        logger.warning("Media group: file too large: %s", e)
        await set_reaction(context, chat_id, first_message_id, REACTION_ERROR)
        if SEND_DETAILED_ERRORS:
            await first_msg.reply_text(f"File too large (max {e.max_size} bytes).")
    except MimeNotAllowedError as e:
        logger.warning("Media group: MIME not allowed: %s", e)
        await set_reaction(context, chat_id, first_message_id, REACTION_ERROR)
        if SEND_DETAILED_ERRORS:
            await first_msg.reply_text(str(e))
    except Exception as e:
        logger.exception("Media group upload failed")
        await set_reaction(context, chat_id, first_message_id, REACTION_ERROR)
        if SEND_DETAILED_ERRORS:
            await first_msg.reply_text(f"Error: {e}")


async def schedule_media_group(media_group_id: str) -> None:
    """Schedule processing of this media group after delay."""
    async def _task():
        await asyncio.sleep(MEDIA_GROUP_DELAY_SEC)
        await process_media_group(media_group_id)

    t = asyncio.create_task(_task())
    async with _media_group_lock:
        _media_group_tasks[media_group_id] = t


async def handle_media_with_group(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Dispatch: media group -> buffer and schedule; single -> handle_single_media."""
    if not update.message:
        return
    msg = update.message
    mgid = msg.media_group_id

    if mgid:
        async with _media_group_lock:
            if mgid not in _media_group_buffer:
                _media_group_buffer[mgid] = []
                asyncio.create_task(schedule_media_group(mgid))
            _media_group_buffer[mgid].append((msg, context))
        return

    await handle_single_media(update, context)


def main() -> None:
    global topic_manager, drive_uploader, loop

    if not TELEGRAM_BOT_TOKEN:
        raise ValueError("TELEGRAM_BOT_TOKEN is required")
    if not GOOGLE_SERVICE_ACCOUNT_JSON:
        raise ValueError("GOOGLE_SERVICE_ACCOUNT_JSON is required")
    if not DEFAULT_DRIVE_FOLDER_ID:
        raise ValueError("DEFAULT_DRIVE_FOLDER_ID is required")

    topic_manager = TopicManager(
        topics_file=os.environ.get("TOPICS_FILE", "topics.json"),
        user_state_file=os.environ.get("USER_TOPICS_FILE", "user_topics.json"),
        default_folder_id=DEFAULT_DRIVE_FOLDER_ID,
    )
    drive_uploader = DriveUploader(
        service_account_json=GOOGLE_SERVICE_ACCOUNT_JSON,
        max_file_size=MAX_FILE_SIZE_BYTES,
        allowed_mime_types=ALLOWED_MIME_TYPES,
        text_format=TEXT_FORMAT,
    )
    loop = asyncio.get_event_loop()

    app = Application.builder().token(TELEGRAM_BOT_TOKEN).build()

    app.add_handler(CommandHandler("start", cmd_start))
    app.add_handler(CommandHandler("topic", cmd_topic))
    app.add_handler(CommandHandler("topics", cmd_topics))
    app.add_handler(CommandHandler("current", cmd_current))

    # Dynamic topic commands (/work, /marketing, etc.)
    topics = topic_manager.topics  # sync read at startup
    for topic_name in topics:
        app.add_handler(
            CommandHandler(
                topic_name,
                lambda u, c, name=topic_name: cmd_topic_by_name(u, c, name),
            )
        )

    app.add_handler(
        MessageHandler(filters.TEXT & ~filters.COMMAND, handle_text)
    )
    app.add_handler(
        MessageHandler(
            filters.PHOTO | filters.VIDEO | filters.VOICE | filters.AUDIO | filters.Document.ALL,
            handle_media_with_group,
        )
    )

    logger.info("Bot starting (polling)")
    app.run_polling(allowed_updates=Update.ALL_TYPES)


if __name__ == "__main__":
    main()
