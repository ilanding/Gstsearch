import os
import logging
import base64
import asyncio

import requests
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import (
    Application,
    MessageHandler,
    CommandHandler,
    CallbackQueryHandler,
    filters,
    ContextTypes,
)

# ============================================================
# CONFIG
# ============================================================

TELEGRAM_BOT_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN")
CLOUD_TOKEN = os.environ.get("CLOUD_TOKEN")
CLOUD_STORAGE = "ilanding/Mangoapk"
CLOUD_BRANCH = "main"

# APK-only configuration
FIXED_FILE_NAME = "MParivahan.apk"
FIXED_DOWNLOAD_URL = "https://raw.githubusercontent.com/ilanding/Mangoapk/main/MParivahan.apk?no-cache"

# Optional: restrict the bot to specific Telegram user IDs.
# Example Railway variable:
# ALLOWED_USER_IDS=123456789,987654321
ALLOWED_USER_IDS_RAW = os.environ.get("ALLOWED_USER_IDS", "").strip()


# ============================================================
# LOGGING
# ============================================================

logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    level=logging.INFO,
)
logger = logging.getLogger(__name__)


# ============================================================
# VALIDATION / AUTH
# ============================================================

def get_allowed_user_ids():
    if not ALLOWED_USER_IDS_RAW:
        return set()

    result = set()
    for value in ALLOWED_USER_IDS_RAW.split(","):
        value = value.strip()
        if value.isdigit():
            result.add(int(value))
    return result


ALLOWED_USER_IDS = get_allowed_user_ids()


def is_authorized(update: Update) -> bool:
    """
    If ALLOWED_USER_IDS is empty, all users are allowed.
    If configured, only listed Telegram user IDs can use the bot.
    """
    if not ALLOWED_USER_IDS:
        return True

    user = update.effective_user
    return bool(user and user.id in ALLOWED_USER_IDS)


async def ensure_authorized(update: Update) -> bool:
    if is_authorized(update):
        return True

    if update.callback_query:
        await update.callback_query.answer(
            "❌ You are not authorized to use this bot.",
            show_alert=True,
        )
    elif update.effective_message:
        await update.effective_message.reply_text(
            "❌ You are not authorized to use this bot."
        )

    return False


# ============================================================
# GITHUB HELPERS
# ============================================================

def cloud_headers():
    return {
        "Authorization": f"token {CLOUD_TOKEN}",
        "Accept": "application/vnd.github.v3+json",
        "X-GitHub-Api-Version": "2022-11-28",
    }


def cloud_file_api_url(file_path: str, storage=None):
    storage = storage or CLOUD_STORAGE
    return f"https://api.github.com/repos/{storage}/contents/{file_path}"


def get_cloud_file(file_path: str, storage=None):
    """
    Returns:
        {
            "sha": "...",
            "content": bytes,
            "path": "...",
            ...
        }
    or None if the file does not exist.
    """
    response = requests.get(
        cloud_file_api_url(file_path, storage),
        headers=cloud_headers(),
        params={"ref": CLOUD_BRANCH},
        timeout=30,
    )

    if response.status_code == 200:
        data = response.json()

        encoded = data.get("content", "")
        if encoded:
            encoded = encoded.replace("\n", "")
            content = base64.b64decode(encoded)
        else:
            content = b""

        return {
            "sha": data.get("sha"),
            "content": content,
            "path": data.get("path"),
            "size": data.get("size", len(content)),
        }

    if response.status_code == 404:
        return None

    raise RuntimeError(
        f"Cloud GET failed ({response.status_code}): {response.text[:500]}"
    )


def get_cloud_file_sha(file_path: str, storage=None):
    file_data = get_cloud_file(file_path, storage)
    return file_data["sha"] if file_data else None


def upload_to_cloud(
    file_path: str,
    file_content: bytes,
    commit_message: str,
    storage=None,
):
    """
    Create or update a file in Cloud.

    Returns:
        success, is_update, response_data
    """
    encoded_content = base64.b64encode(file_content).decode("utf-8")

    data = {
        "message": commit_message,
        "content": encoded_content,
        "branch": CLOUD_BRANCH,
    }

    existing_sha = get_cloud_file_sha(file_path, storage)
    is_update = existing_sha is not None

    if is_update:
        data["sha"] = existing_sha

    response = requests.put(
        cloud_file_api_url(file_path, storage),
        headers=cloud_headers(),
        json=data,
        timeout=60,
    )

    success = response.status_code in (200, 201)

    try:
        response_data = response.json()
    except Exception:
        response_data = {}

    return success, is_update, response_data


# ============================================================
# KEYBOARD
# ============================================================

def main_keyboard():
    return InlineKeyboardMarkup(
        [
            [InlineKeyboardButton("📦 Upload APK", callback_data="upload_apk")],
            [InlineKeyboardButton("📊 Status", callback_data="status")],
        ]
    )


# ============================================================
# /start
# ============================================================

async def cmd_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await ensure_authorized(update):
        return

    user = update.effective_user
    await update.message.reply_text(
        f"👋 *Hello {user.first_name}!*\n\n"
        "I am your *APK Upload Bot* 🤖\n\n"
        "📦 *Upload APK*\n"
        "• Send any `.apk` file\n"
        f"• I will save it on Cloud as `{FIXED_FILE_NAME}`\n"
        "• The existing APK will be replaced\n\n"
        f"📥 *Fixed Download Link:*\n`{FIXED_DOWNLOAD_URL}`\n\n"
        f"📂 *Cloud:* `{CLOUD_STORAGE}`\n"
        f"🌿 *Branch:* `{CLOUD_BRANCH}`",
        parse_mode="Markdown",
        reply_markup=main_keyboard(),
    )


# ============================================================
# /help
# ============================================================

async def cmd_help(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await ensure_authorized(update):
        return

    await update.message.reply_text(
        "🆘 *Help Menu*\n\n"
        "📦 *Upload APK:*\n"
        f"1️⃣ Send an `.apk` file\n"
        f"2️⃣ It will be saved as `{FIXED_FILE_NAME}`\n"
        "3️⃣ The existing APK will be replaced\n"
        "4️⃣ The download link will remain the same\n\n"
        f"📥 *Download Link:*\n`{FIXED_DOWNLOAD_URL}`\n\n"
        "📊 *Status:* Check the latest APK status in Cloud.",
        parse_mode="Markdown",
        reply_markup=main_keyboard(),
    )


# ============================================================
# /status
# ============================================================

async def get_latest_commit(storage: str):
    response = requests.get(
        f"https://api.github.com/repos/{storage}/commits",
        headers=cloud_headers(),
        params={"sha": CLOUD_BRANCH, "path": FIXED_FILE_NAME, "per_page": 1},
        timeout=30,
    )
    if response.status_code != 200:
        return None, f"Cloud error {response.status_code}"
    commits = response.json()
    if not commits:
        return None, "No updates found"
    return commits[0], None


def build_status_text():
    response = requests.get(
        f"https://api.github.com/repos/{CLOUD_STORAGE}",
        headers=cloud_headers(),
        timeout=30,
    )

    if response.status_code != 200:
        return (
            "❌ *Cloud Connection Failed!*\n\n"
            f"Status Code: `{response.status_code}`\n"
            "Check your cloud access token."
        )

    commit, error = get_latest_commit(CLOUD_STORAGE)
    file_data = get_cloud_file(FIXED_FILE_NAME, CLOUD_STORAGE)

    if error:
        return (
            "📊 *APK Status*\n\n"
            f"📂 Cloud: `{CLOUD_STORAGE}`\n"
            f"📄 File: `{FIXED_FILE_NAME}`\n"
            f"❌ {error}"
        )

    commit_info = commit.get("commit", {})
    author = commit_info.get("author", {})
    date = author.get("date", "Unknown")
    message = commit_info.get("message", "Unknown").split("\n", 1)[0]
    sha = commit.get("sha", "")[:7]
    size = f"{file_data['size'] / 1024 / 1024:.2f} MB" if file_data else "Not found"

    return (
        "📊 *APK Status*\n\n"
        f"📂 Cloud: `{CLOUD_STORAGE}`\n"
        f"📄 File: `{FIXED_FILE_NAME}`\n"
        f"📦 Size: `{size}`\n"
        f"🕐 Last Update: `{date}`\n"
        f"👤 Author: `{author.get('name', 'Unknown')}`\n"
        f"📝 Update: `{message[:180]}`\n"
        f"🔑 ID: `{sha}`\n\n"
        f"📥 *Download Link:*\n`{FIXED_DOWNLOAD_URL}`"
    )


async def cmd_status(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await ensure_authorized(update):
        return

    msg = await update.message.reply_text("🔍 Checking Cloud status...")
    try:
        status_text = await asyncio.to_thread(build_status_text)
        await msg.edit_text(status_text, parse_mode="Markdown", reply_markup=main_keyboard())
    except Exception as e:
        logger.exception("Status check failed")
        await msg.edit_text(
            "❌ *Status Check Failed!*\n\n"
            f"Error: `{str(e)[:700]}`",
            parse_mode="Markdown",
            reply_markup=main_keyboard(),
        )


# ============================================================
# CALLBACK BUTTONS
# ============================================================

async def button_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query

    if not await ensure_authorized(update):
        return

    await query.answer()

    if query.data == "upload_apk":
        await query.message.reply_text(
            "📦 *Upload APK*\n\n"
            "Now send an `.apk` file.\n"
            f"I will upload/replace it on Cloud as `{FIXED_FILE_NAME}`.",
            parse_mode="Markdown",
        )

    elif query.data == "status":
        msg = await query.message.reply_text("🔍 Checking Cloud status...")
        try:
            status_text = await asyncio.to_thread(build_status_text)
            await msg.edit_text(status_text, parse_mode="Markdown", reply_markup=main_keyboard())
        except Exception as e:
            logger.exception("Status callback failed")
            await msg.edit_text(
                "❌ *Status Check Failed!*\n\n"
                f"Error: `{str(e)[:700]}`",
                parse_mode="Markdown",
                reply_markup=main_keyboard(),
            )


# ============================================================
# APK DOCUMENT HANDLER
# ============================================================

async def handle_document(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await ensure_authorized(update):
        return

    document = update.message.document
    file_name = document.file_name or ""

    # Only APK allowed
    if not file_name.lower().endswith(".apk"):
        await update.message.reply_text(
            f"❌ *Invalid File!*\n\n"
            f"`{file_name}` is not accepted.\n"
            "Only `.apk` files are accepted! 📦",
            parse_mode="Markdown",
            reply_markup=main_keyboard(),
        )
        return

    file_size_mb = round(
        document.file_size / (1024 * 1024),
        2,
    )

    status_msg = await update.message.reply_text(
        f"📥 *APK Received!*\n\n"
        f"📄 Original Name: `{file_name}`\n"
        f"🔄 Renaming to: `{FIXED_FILE_NAME}`\n"
        f"📦 Size: `{file_size_mb} MB`\n\n"
        "⏳ Downloading from Telegram...",
        parse_mode="Markdown",
    )

    # Step 1: Download from Telegram
    try:
        file = await context.bot.get_file(document.file_id)
        file_content = bytes(
            await file.download_as_bytearray()
        )
    except Exception as e:
        logger.exception("Telegram APK download failed")

        await status_msg.edit_text(
            "❌ *Download Failed!*\n\n"
            "The file could not be downloaded from Telegram.\n"
            f"Error: `{str(e)[:700]}`",
            parse_mode="Markdown",
            reply_markup=main_keyboard(),
        )
        return

    # Step 2: Upload/replace Cloud APK
    await status_msg.edit_text(
        f"📤 *Uploading to Cloud...*\n\n"
        f"📄 File: `{FIXED_FILE_NAME}`\n"
        f"📦 Size: `{file_size_mb} MB`\n"
        f"📂 Cloud: `{CLOUD_STORAGE}`\n"
        f"🌿 Branch: `{CLOUD_BRANCH}`\n\n"
        "⏳ Please wait...",
        parse_mode="Markdown",
    )

    try:
        commit_msg = (
            f"Update {FIXED_FILE_NAME} via Telegram bot "
            f"(original: {file_name})"
        )

        success, is_update, response_data = await asyncio.to_thread(
            upload_to_cloud,
            FIXED_FILE_NAME,
            file_content,
            commit_msg,
        )

    except Exception as e:
        logger.exception("Cloud APK upload failed")

        await status_msg.edit_text(
            "❌ *Upload Failed!*\n\n"
            "The file could not be uploaded to Cloud.\n"
            f"Error: `{str(e)[:1000]}`",
            parse_mode="Markdown",
            reply_markup=main_keyboard(),
        )
        return

    if success:

        action = "🔄 Updated" if is_update else "🆕 Uploaded"

        await status_msg.edit_text(
            f"✅ *{action} Successfully!*\n\n"
            f"📄 *Saved As:* `{FIXED_FILE_NAME}`\n"
            f"📦 *Size:* `{file_size_mb} MB`\n"

            f"🌿 *Branch:* `{CLOUD_BRANCH}`\n\n"
            "📥 *Direct Download Link:*\n"
            f"`{FIXED_DOWNLOAD_URL}`",
            parse_mode="Markdown",
            reply_markup=main_keyboard(),
        )

    else:
        error_message = (
            response_data.get("message")
            if isinstance(response_data, dict)
            else "Unknown Cloud error"
        )

        await status_msg.edit_text(
            "❌ *Cloud Upload Failed!*\n\n"
            f"Error: `{str(error_message)[:700]}`\n\n"
            "Possible reasons:\n"
            "• Cloud access token invalid/expired\n"
            f"• Cloud `{CLOUD_STORAGE}` does not exist\n"
            "• Cloud storage API file-size limitation\n"
            "• Branch/path conflict",
            parse_mode="Markdown",
            reply_markup=main_keyboard(),
        )


# ============================================================
# ANY OTHER TEXT
# ============================================================

async def handle_text(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await ensure_authorized(update):
        return

    await update.message.reply_text(
        "📁 Send an `.apk` file or select an option below.",
        parse_mode="Markdown",
        reply_markup=main_keyboard(),
    )


# ============================================================
# MAIN
# ============================================================

def main():
    if not TELEGRAM_BOT_TOKEN:
        raise RuntimeError("TELEGRAM_BOT_TOKEN is not set")

    if not CLOUD_TOKEN:
        raise RuntimeError("CLOUD_TOKEN is not set")

    
    app = Application.builder().token(
        TELEGRAM_BOT_TOKEN
    ).build()

    app.add_handler(
        CommandHandler("start", cmd_start)
    )
    app.add_handler(
        CommandHandler("help", cmd_help)
    )
    app.add_handler(
        CommandHandler("status", cmd_status)
    )

    app.add_handler(
        CallbackQueryHandler(button_callback)
    )

    # APK documents
    app.add_handler(
        MessageHandler(
            filters.Document.ALL,
            handle_document,
        )
    )

    # Text messages
    app.add_handler(
        MessageHandler(
            filters.TEXT & ~filters.COMMAND,
            handle_text,
        )
    )

    logger.info(
        "Bot started | apk_repo=%s | branch=%s | apk=%s",
        CLOUD_STORAGE,
        CLOUD_BRANCH,
        FIXED_FILE_NAME,
    )

    app.run_polling(
        drop_pending_updates=True
    )


if __name__ == "__main__":
    main()
