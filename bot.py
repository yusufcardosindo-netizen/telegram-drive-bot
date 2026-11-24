import html
import io
import json
import logging
import os
import re
import threading
from datetime import datetime
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path
from typing import Any, Dict, Optional, Set

from telegram import (
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    Update,
)
from telegram.constants import ParseMode
from telegram.ext import (
    Application,
    CallbackQueryHandler,
    CommandHandler,
    ContextTypes,
    ConversationHandler,
    MessageHandler,
    filters,
)

from config import (
    ACCESS_PASSCODE,
    ADMIN_TELEGRAM_ID,
    ALLOWED_EMAILS,
    ALLOWED_TELEGRAM_USERS,
    DEFAULT_SHARE_ROLE,
    ENABLE_DIRECT_DOWNLOAD,
    ENABLE_EMAIL_SHARING,
    GOOGLE_DRIVE_FOLDER_ID,
    MAX_TELEGRAM_FILE_SIZE,
    TELEGRAM_BOT_TOKEN,
)
from drive_service import GoogleDriveService

# Configure logging
logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s", level=logging.INFO
)
logger = logging.getLogger(__name__)

# States for ConversationHandler
AWAITING_EMAIL = 1
AWAITING_SEARCH_QUERY = 2

# In-memory storage for authorized sessions (user_id) if passcode is configured
authenticated_users: Set[int] = set()

# Persistent registration file
REGISTRATIONS_FILE = Path(__file__).resolve().parent / "registered_users.json"


def load_registered_users() -> Dict[str, Any]:
    """Loads user registrations from JSON file."""
    if REGISTRATIONS_FILE.exists():
        try:
            with open(REGISTRATIONS_FILE, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception as e:
            logger.error("Error reading registered_users.json: %s", e)
    return {}


def save_registered_user(user_id: int, user_info: Dict[str, Any]):
    """Saves or updates a user registration record."""
    users = load_registered_users()
    users[str(user_id)] = user_info
    try:
        with open(REGISTRATIONS_FILE, "w", encoding="utf-8") as f:
            json.dump(users, f, indent=2, ensure_ascii=False)
    except Exception as e:
        logger.error("Error saving registered_users.json: %s", e)

# Initialize Google Drive Service
drive_service = GoogleDriveService()

EMAIL_REGEX = re.compile(r"^[\w\.-]+@([\w-]+\.)+[\w-]{2,4}$")


async def safe_answer_callback(query):
    """Safely answers a callback query without throwing if it timed out or expired."""
    try:
        await query.answer()
    except Exception as e:
        logger.debug("Callback answer skipped (likely expired): %s", e)


async def error_handler(update: object, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Global error handler so the bot never terminates on unhandled exceptions."""
    logger.error("Exception while handling an update:", exc_info=context.error)


def is_user_authorized(update: Update) -> bool:
    """Checks whether the user is permitted to interact with the bot."""
    user = update.effective_user
    if not user:
        return False

    # 1. Admin is always authorized
    if ADMIN_TELEGRAM_ID and str(user.id) == str(ADMIN_TELEGRAM_ID):
        return True

    # 2. Check whitelist if configured
    if ALLOWED_TELEGRAM_USERS:
        user_name = (user.username or "").lower()
        if str(user.id) not in ALLOWED_TELEGRAM_USERS and user_name not in ALLOWED_TELEGRAM_USERS:
            return False

    # 3. Check passcode requirement
    if ACCESS_PASSCODE and user.id not in authenticated_users:
        return False

    return True


async def notify_admin(context: ContextTypes.DEFAULT_TYPE, message: str):
    """Sends audit notifications to the Drive owner while they are offline."""
    if not ADMIN_TELEGRAM_ID:
        return
    try:
        await context.bot.send_message(
            chat_id=ADMIN_TELEGRAM_ID,
            text=message,
            parse_mode=ParseMode.HTML,
        )
    except Exception as e:
        logger.warning("Failed to notify admin: %s", e)


def format_file_size(size_bytes: Any) -> str:
    """Formats file size into human-readable string."""
    try:
        bytes_val = int(size_bytes)
    except (ValueError, TypeError):
        return "Unknown size"

    for unit in ["B", "KB", "MB", "GB"]:
        if bytes_val < 1024.0:
            return f"{bytes_val:.1f} {unit}"
        bytes_val /= 1024.0
    return f"{bytes_val:.1f} TB"


def build_file_keyboard(file_id: str) -> InlineKeyboardMarkup:
    """Builds inline action buttons for a specific file."""
    buttons = []
    if ENABLE_DIRECT_DOWNLOAD:
        buttons.append(
            InlineKeyboardButton("📥 Download File", callback_data=f"get_{file_id}")
        )
    if ENABLE_EMAIL_SHARING:
        buttons.append(
            InlineKeyboardButton("🔑 Grant Drive Access", callback_data=f"share_{file_id}")
        )
    buttons.append(
        InlineKeyboardButton("🔗 Open Drive Link", callback_data=f"link_{file_id}")
    )
    # Arrange buttons neatly
    keyboard = [[b] for b in buttons]
    return InlineKeyboardMarkup(keyboard)


# ==================== COMMAND HANDLERS ====================


async def start_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handles /start command both in private chat and in groups (/start @AcessV_Bot)."""
    user = update.effective_user
    user_name = html.escape(user.first_name if user else "there")
    chat = update.effective_chat
    is_group = chat and chat.type in ["group", "supergroup"]

    if not is_user_authorized(update):
        if ACCESS_PASSCODE:
            await update.effective_message.reply_text(
                f"👋 Hello {user_name}!\n\n"
                "🔒 This bot requires an access passcode to view or request files.\n"
                "Please enter the passcode using:\n"
                "<code>/auth &lt;passcode&gt;</code>",
                parse_mode=ParseMode.HTML,
            )
        else:
            await update.effective_message.reply_text(
                "⛔ Sorry, you are not authorized to use this bot."
            )
        return

    # Special greeting when /start or /start @AcessV_Bot is used in a group/chat
    if is_group:
        chat_title = html.escape(chat.title or "this chat")
        welcome_group_text = (
            f"👋 Hello everyone in <b>{chat_title}</b>!\n\n"
            "I am <b>@AcessV_Bot</b>, your automated Google Drive Assistant. "
            "Members can browse, download, and request Google Drive file access 24/7 "
            "even when the owner is offline!\n\n"
            "<b>Available Commands in this Group:</b>\n"
            "• <code>/register your@gmail.com</code> - Register your Google account for access\n"
            "• <code>/request your@gmail.com</code> - Request instant folder access\n"
            "• <code>/list</code> - Browse shared files\n"
            "• <code>/search &lt;query&gt;</code> - Search for a specific file\n"
            "• <code>/help</code> - Detailed instructions\n\n"
            "<i>💡 Tip: You can also message me in private chat for 1-on-1 direct file downloads!</i>"
        )
        keyboard = [
            [
                InlineKeyboardButton("📂 Browse Files", callback_data="btn_list"),
                InlineKeyboardButton("🔍 Search Files", callback_data="btn_search"),
            ],
            [
                InlineKeyboardButton("💬 Open in Private Chat", url="https://t.me/AcessV_Bot?start=group"),
            ],
        ]
        await update.effective_message.reply_text(
            welcome_group_text,
            reply_markup=InlineKeyboardMarkup(keyboard),
            parse_mode=ParseMode.HTML,
        )
        return

    welcome_text = (
        f"👋 Welcome <b>{user_name}</b>!\n\n"
        "I am your automated <b>Google Drive File Access Assistant</b>. "
        "Even when the drive owner is offline, I can help you search, download, or "
        "grant direct Google Drive access to the files you need.\n\n"
        "<b>Available Commands:</b>\n"
        "• <code>/register your@gmail.com</code> - Register your Google account for Drive access\n"
        "• <code>/request your@gmail.com</code> - Request instant access for your Google account\n"
        "• <code>/list</code> - Browse available files\n"
        "• <code>/search &lt;query&gt;</code> - Search for a specific file\n"
        "• <code>/help</code> - How to use this bot"
    )

    keyboard = [
        [
            InlineKeyboardButton("📂 Browse Files", callback_data="btn_list"),
            InlineKeyboardButton("🔍 Search Files", callback_data="btn_search"),
        ],
        [InlineKeyboardButton("ℹ️ Help & Instructions", callback_data="btn_help")],
    ]

    await update.message.reply_text(
        welcome_text,
        reply_markup=InlineKeyboardMarkup(keyboard),
        parse_mode=ParseMode.HTML,
    )


async def on_new_chat_members(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Automatically greets the group when @AcessV_Bot is added to a chat or group."""
    if not update.message or not update.message.new_chat_members:
        return

    bot_id = context.bot.id
    for member in update.message.new_chat_members:
        if member.id == bot_id or (member.username and member.username.lower() == "acessv_bot"):
            chat_title = html.escape(update.effective_chat.title or "this chat")
            welcome_text = (
                f"🎉 <b>Thanks for adding me to {chat_title}!</b>\n\n"
                "I am <b>@AcessV_Bot</b>, your automated Google Drive Assistant.\n"
                "Members can request or register for file access 24/7 even when the drive owner is offline!\n\n"
                "<b>Quick Start:</b>\n"
                "• <code>/register your@gmail.com</code> - Register your Google account for Drive access\n"
                "• <code>/request your@gmail.com</code> - Request instant folder access\n"
                "• <code>/list</code> - View files in the repository\n"
                "• <code>/help</code> - Show bot commands\n\n"
                "<i>Type <code>/start @AcessV_Bot</code> anytime to see this menu again!</i>"
            )
            keyboard = [
                [
                    InlineKeyboardButton("📂 Browse Files", callback_data="btn_list"),
                    InlineKeyboardButton("ℹ️ Help", callback_data="btn_help"),
                ],
                [
                    InlineKeyboardButton("💬 Open in Private Chat", url="https://t.me/AcessV_Bot?start=group_welcome"),
                ],
            ]
            await update.message.reply_text(
                welcome_text,
                reply_markup=InlineKeyboardMarkup(keyboard),
                parse_mode=ParseMode.HTML,
            )
            break


async def auth_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handles /auth <passcode> for protected access."""
    if not ACCESS_PASSCODE:
        await update.message.reply_text("✅ No passcode is required for this bot.")
        return

    user = update.effective_user
    args = context.args

    if not args:
        await update.message.reply_text("Usage: <code>/auth &lt;passcode&gt;</code>", parse_mode=ParseMode.HTML)
        return

    provided_code = args[0].strip()
    if provided_code == ACCESS_PASSCODE:
        authenticated_users.add(user.id)
        await update.message.reply_text(
            "🔓 <b>Authentication successful!</b>\nYou can now browse and request files.\nType /list to see files.",
            parse_mode=ParseMode.HTML,
        )
        logger.info("User %s (%s) successfully authenticated with passcode.", user.id, user.username)
    else:
        await update.message.reply_text("❌ Incorrect passcode. Please try again.")


async def help_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handles /help command."""
    help_text = (
        "📖 <b>How to Access Files:</b>\n\n"
        "1. <b>Register Email:</b> Use <code>/register your@gmail.com</code> to link your Google account "
        "and receive access to the shared Google Drive repository.\n"
        "2. <b>Request Folder Access:</b> Use <code>/request your@gmail.com</code> to request "
        "Viewer access to the shared Google Drive folder.\n"
        "3. <b>Browse Files:</b> Use <code>/list</code> to see the files in the repository.\n"
        "4. <b>Search:</b> Use <code>/search &lt;filename&gt;</code> to quickly find a file.\n"
        "5. <b>Direct Download:</b> Click <i>'📥 Download File'</i> or use <code>/get &lt;file_id&gt;</code>. "
        "Files under 50MB will be sent directly to you in Telegram.\n"
        "6. <b>Single File Sharing:</b> Click <i>'🔑 Grant Drive Access'</i> to add your Google account to a specific file."
    )
    if update.callback_query:
        await safe_answer_callback(update.callback_query)
        await update.callback_query.message.reply_text(help_text, parse_mode=ParseMode.HTML)
    else:
        await update.message.reply_text(help_text, parse_mode=ParseMode.HTML)


async def list_files_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Lists files available in the Drive folder."""
    if not is_user_authorized(update):
        await update.effective_message.reply_text("🔒 Please authenticate first using /auth <passcode>.")
        return

    target_message = update.callback_query.message if update.callback_query else update.message
    if update.callback_query:
        await safe_answer_callback(update.callback_query)

    status_msg = await target_message.reply_text("⏳ Fetching files from Google Drive...")

    try:
        files = drive_service.list_files(page_size=10)
        if not files:
            await status_msg.edit_text("📂 No files found in the shared Google Drive directory.")
            return

        await status_msg.delete()
        for f in files:
            file_name = html.escape(f.get("name", "Unnamed File"))
            file_size = format_file_size(f.get("size"))
            file_id = f.get("id")

            text = (
                f"📄 <b>{file_name}</b>\n"
                f"📦 Size: {file_size}\n"
                f"🆔 <code>{file_id}</code>"
            )
            reply_markup = build_file_keyboard(file_id)
            await target_message.reply_text(text, reply_markup=reply_markup, parse_mode=ParseMode.HTML)

    except Exception as e:
        logger.error("Error listing files: %s", e)
        await status_msg.edit_text(f"❌ Error fetching files from Drive: {html.escape(str(e))}")


async def search_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handles /search <query> command."""
    if not is_user_authorized(update):
        await update.effective_message.reply_text("🔒 Please authenticate first using /auth <passcode>.")
        return

    query = " ".join(context.args).strip() if context.args else ""
    if not query:
        await update.message.reply_text(
            "Please specify a keyword to search, e.g.:\n<code>/search report</code>",
            parse_mode=ParseMode.HTML,
        )
        return

    status_msg = await update.message.reply_text(f"🔍 Searching for '<i>{html.escape(query)}</i>'...", parse_mode=ParseMode.HTML)

    try:
        files = drive_service.list_files(query_name=query, page_size=10)
        if not files:
            await status_msg.edit_text(f"🔍 No files found matching '<b>{html.escape(query)}</b>'.", parse_mode=ParseMode.HTML)
            return

        await status_msg.delete()
        for f in files:
            file_name = html.escape(f.get("name", "Unnamed File"))
            file_size = format_file_size(f.get("size"))
            file_id = f.get("id")

            text = (
                f"📄 <b>{file_name}</b>\n"
                f"📦 Size: {file_size}\n"
                f"🆔 <code>{file_id}</code>"
            )
            reply_markup = build_file_keyboard(file_id)
            await update.message.reply_text(text, reply_markup=reply_markup, parse_mode=ParseMode.HTML)

    except Exception as e:
        logger.error("Error searching files: %s", e)
        await status_msg.edit_text(f"❌ Search error: {html.escape(str(e))}")


# ==================== ACTIONS: GET, SHARE & LINK ====================


async def execute_download(file_id: str, update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Executes file download and sends document or web link."""
    chat_id = update.effective_chat.id
    user = update.effective_user

    status_msg = await context.bot.send_message(
        chat_id=chat_id, text="⏳ Downloading file from Google Drive..."
    )

    try:
        meta = drive_service.get_file_metadata(file_id)
        file_size_raw = meta.get("size")
        file_name = meta.get("name", "file")

        # Check if file exceeds Telegram's 50MB limit
        if file_size_raw and int(file_size_raw) > MAX_TELEGRAM_FILE_SIZE:
            view_link = drive_service.make_shareable_link(file_id)
            await status_msg.edit_text(
                f"⚠️ <b>File too large for Telegram!</b>\n\n"
                f"📄 <b>{html.escape(file_name)}</b> is {format_file_size(file_size_raw)} "
                f"(Telegram bot limit is 50MB).\n\n"
                f"🔗 You can access/download it directly via Google Drive:\n"
                f"<a href='{view_link}'>Open in Google Drive</a>",
                parse_mode=ParseMode.HTML,
                disable_web_page_preview=False,
            )
            return

        # Download stream
        stream, final_name = drive_service.download_file(file_id)
        await status_msg.edit_text("📤 Sending file to you...")

        await context.bot.send_document(
            chat_id=chat_id,
            document=stream,
            filename=final_name,
            caption=f"✅ Here is your file: <b>{html.escape(final_name)}</b>",
            parse_mode=ParseMode.HTML,
        )
        await status_msg.delete()

        # Offline Owner Notification
        now_str = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        await notify_admin(
            context,
            f"🔔 <b>Offline Bot Activity Alert</b>\n"
            f"• <b>User:</b> {html.escape(user.full_name)} (@{user.username or 'none'}, ID: <code>{user.id}</code>)\n"
            f"• <b>Action:</b> Downloaded file\n"
            f"• <b>File:</b> <i>{html.escape(final_name)}</i>\n"
            f"• <b>Time:</b> {now_str}",
        )

    except Exception as e:
        logger.error("Download failed for file %s: %s", file_id, e)
        await status_msg.edit_text(f"❌ Failed to download file: {html.escape(str(e))}")


async def handle_callback_query(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Dispatches button callbacks."""
    query = update.callback_query
    await safe_answer_callback(query)

    if not is_user_authorized(update):
        await query.message.reply_text("🔒 Please authenticate first using /auth <passcode>.")
        return

    data = query.data

    if data == "btn_list":
        await list_files_command(update, context)
    elif data == "btn_help":
        await help_command(update, context)
    elif data == "btn_search":
        await query.message.reply_text(
            "🔍 To search, send: <code>/search &lt;name&gt;</code>\n"
            "Example: <code>/search project_report</code>",
            parse_mode=ParseMode.HTML,
        )
    elif data.startswith("get_"):
        file_id = data.split("get_")[1]
        await execute_download(file_id, update, context)
    elif data.startswith("link_"):
        file_id = data.split("link_")[1]
        link = drive_service.make_shareable_link(file_id)
        await query.message.reply_text(
            f"🔗 <b>Google Drive Link:</b>\n<a href='{link}'>{link}</a>",
            parse_mode=ParseMode.HTML,
            disable_web_page_preview=False,
        )
    elif data.startswith("share_"):
        file_id = data.split("share_")[1]
        context.user_data["pending_share_file_id"] = file_id
        meta = drive_service.get_file_metadata(file_id)
        file_name = html.escape(meta.get("name", "this file"))

        await query.message.reply_text(
            f"🔑 <b>Grant Google Drive Access</b>\n\n"
            f"Please reply with the <b>Google Email / Gmail</b> you want to grant access to for:\n"
            f"📄 <b>{file_name}</b>\n\n"
            f"Example: <code>alex@gmail.com</code>\n"
            f"(Or type <code>/cancel</code> to abort)",
            parse_mode=ParseMode.HTML,
        )
        return AWAITING_EMAIL


async def process_email_share(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Processes user's email reply and grants Google Drive permissions."""
    user = update.effective_user
    email = update.message.text.strip()
    file_id = context.user_data.get("pending_share_file_id")

    if not file_id:
        await update.message.reply_text("⚠️ No file selected for sharing. Please use /list or /search again.")
        return ConversationHandler.END

    if not EMAIL_REGEX.match(email):
        await update.message.reply_text(
            "❌ Invalid email format. Please send a valid Gmail / Google Account email (or /cancel):"
        )
        return AWAITING_EMAIL

    # Check if email is on the approved list (if restricted)
    if ALLOWED_EMAILS and email.lower() not in ALLOWED_EMAILS:
        await update.message.reply_text(
            f"⛔ <b>Access Denied</b>\n\n"
            f"The email <code>{html.escape(email)}</code> is not on the approved access list.\n"
            "Please contact the owner to have your email authorized.",
            parse_mode=ParseMode.HTML,
        )
        now_str = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        await notify_admin(
            context,
            f"⚠️ <b>Unauthorized Email Blocked</b>\n"
            f"• <b>User:</b> {html.escape(user.full_name)} (@{user.username or 'none'})\n"
            f"• <b>Blocked Email:</b> <code>{html.escape(email)}</code>\n"
            f"• <b>Time:</b> {now_str}",
        )
        context.user_data.pop("pending_share_file_id", None)
        return ConversationHandler.END

    status_msg = await update.message.reply_text(f"⏳ Granting Google Drive access to <b>{html.escape(email)}</b>...", parse_mode=ParseMode.HTML)

    try:
        meta = drive_service.get_file_metadata(file_id)
        file_name = meta.get("name", "File")

        drive_service.share_file_with_email(file_id, email, role=DEFAULT_SHARE_ROLE)

        await status_msg.edit_text(
            f"🎉 <b>Access Granted Successfully!</b>\n\n"
            f"📄 <b>File:</b> {html.escape(file_name)}\n"
            f"👤 <b>Shared with:</b> {html.escape(email)}\n"
            f"🛡️ <b>Role:</b> {DEFAULT_SHARE_ROLE.capitalize()} (Viewer)\n\n"
            f"Google has dispatched an invitation email to your address with the link to open this file in your Google Drive.",
            parse_mode=ParseMode.HTML,
        )

        # Notify Owner
        now_str = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        await notify_admin(
            context,
            f"🔔 <b>Offline Bot Activity Alert</b>\n"
            f"• <b>User:</b> {html.escape(user.full_name)} (@{user.username or 'none'}, ID: <code>{user.id}</code>)\n"
            f"• <b>Action:</b> Granted Drive Access ({DEFAULT_SHARE_ROLE})\n"
            f"• <b>Target Email:</b> <code>{html.escape(email)}</code>\n"
            f"• <b>File:</b> <i>{html.escape(file_name)}</i>\n"
            f"• <b>Time:</b> {now_str}",
        )

    except Exception as e:
        logger.error("Failed to share file %s with %s: %s", file_id, email, e)
        await status_msg.edit_text(f"❌ Failed to grant Drive access: {html.escape(str(e))}")

    # Clear state
    context.user_data.pop("pending_share_file_id", None)
    return ConversationHandler.END


async def cancel_conversation(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Cancels the interactive sharing dialog."""
    context.user_data.pop("pending_share_file_id", None)
    await update.message.reply_text("🚫 Action cancelled.")
    return ConversationHandler.END


# ==================== DIRECT COMMANDS ====================


async def register_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Direct command: /register your@gmail.com to register a user's Google email for Drive access."""
    if not is_user_authorized(update):
        await update.message.reply_text("🔒 Please authenticate first using /auth <passcode>.")
        return

    user = update.effective_user
    args = context.args
    if not args:
        await update.message.reply_text(
            "Usage: <code>/register your@gmail.com</code>\n"
            "Example: <code>/register user@gmail.com</code>\n\n"
            "This command registers your Google email and grants you access to the shared Google Drive repository.",
            parse_mode=ParseMode.HTML,
        )
        return

    email = args[0].strip().lower()
    if not EMAIL_REGEX.match(email):
        await update.message.reply_text(
            "❌ Invalid email format. Please provide a valid Google / Gmail address:\n"
            "<code>/register yourname@gmail.com</code>",
            parse_mode=ParseMode.HTML,
        )
        return

    now_str = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

    # 1. Check if email whitelist/exclusion is active
    if ALLOWED_EMAILS and email not in ALLOWED_EMAILS:
        await update.message.reply_text(
            f"⛔ <b>Access Denied</b>\n\n"
            f"The email <code>{html.escape(email)}</code> is not on the pre-approved whitelist.\n\n"
            "Your registration attempt has been recorded. Please contact the owner to have your email approved.",
            parse_mode=ParseMode.HTML,
        )
        await notify_admin(
            context,
            f"⚠️ <b>Registration Attempt Blocked (Not in Whitelist)</b>\n"
            f"• <b>User:</b> {html.escape(user.full_name)} (@{user.username or 'none'}, ID: <code>{user.id}</code>)\n"
            f"• <b>Attempted Email:</b> <code>{html.escape(email)}</code>\n"
            f"• <b>Time:</b> {now_str}",
        )
        return

    # 2. Persist user registration
    user_record = {
        "user_id": user.id,
        "full_name": user.full_name,
        "username": user.username or "",
        "email": email,
        "registered_at": now_str,
    }
    save_registered_user(user.id, user_record)

    # 3. Grant Google Drive folder access if folder is configured
    folder_granted = False
    folder_name = "Shared Drive"
    folder_link = ""

    if GOOGLE_DRIVE_FOLDER_ID:
        status_msg = await update.message.reply_text(
            f"⏳ Registering <b>{html.escape(email)}</b> and granting Google Drive access...",
            parse_mode=ParseMode.HTML,
        )
        try:
            folder_meta = drive_service.get_file_metadata(GOOGLE_DRIVE_FOLDER_ID)
            folder_name = folder_meta.get("name", "Shared Drive Folder")
            folder_link = folder_meta.get("webViewLink", "")

            drive_service.share_file_with_email(
                GOOGLE_DRIVE_FOLDER_ID, email, role=DEFAULT_SHARE_ROLE
            )
            folder_granted = True
        except Exception as e:
            logger.error("Failed to grant folder access during registration for %s: %s", email, e)
            await status_msg.edit_text(
                f"⚠️ Registered <code>{html.escape(email)}</code>, but failed to grant automatic Drive access: {html.escape(str(e))}"
            )
            return

        link_html = (
            f"\n🔗 <a href='{folder_link}'>Open Shared Folder in Google Drive</a>"
            if folder_link
            else ""
        )

        await status_msg.edit_text(
            f"🎉 <b>Registration Complete!</b>\n\n"
            f"👤 <b>User:</b> {html.escape(user.full_name)}\n"
            f"📧 <b>Linked Email:</b> <code>{html.escape(email)}</code>\n"
            f"📂 <b>Drive Folder:</b> {html.escape(folder_name)}\n"
            f"🛡️ <b>Permission:</b> {DEFAULT_SHARE_ROLE.capitalize()} (Viewer)\n\n"
            f"Google has dispatched an invitation email to your address. You now have full access to the repository!{link_html}",
            parse_mode=ParseMode.HTML,
            disable_web_page_preview=False,
        )
    else:
        await update.message.reply_text(
            f"🎉 <b>Registration Complete!</b>\n\n"
            f"👤 <b>User:</b> {html.escape(user.full_name)}\n"
            f"📧 <b>Linked Email:</b> <code>{html.escape(email)}</code>\n\n"
            "Your email has been registered successfully. You can now browse or download files.",
            parse_mode=ParseMode.HTML,
        )

    # 4. Notify Owner
    await notify_admin(
        context,
        f"🔔 <b>New User Registered via /register</b>\n"
        f"• <b>User:</b> {html.escape(user.full_name)} (@{user.username or 'none'}, ID: <code>{user.id}</code>)\n"
        f"• <b>Registered Email:</b> <code>{html.escape(email)}</code>\n"
        f"• <b>Drive Access Granted:</b> {'Yes' if folder_granted else 'No'}\n"
        f"• <b>Time:</b> {now_str}",
    )


async def request_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Direct command: /request your@gmail.com to request access to the shared Google Drive folder."""
    if not is_user_authorized(update):
        await update.message.reply_text("🔒 Please authenticate first using /auth <passcode>.")
        return

    user = update.effective_user
    args = context.args
    if not args:
        await update.message.reply_text(
            "Usage: <code>/request your@gmail.com</code>\n"
            "Example: <code>/request user@gmail.com</code>",
            parse_mode=ParseMode.HTML,
        )
        return

    email = args[0].strip().lower()
    if not EMAIL_REGEX.match(email):
        await update.message.reply_text(
            "❌ Invalid email format. Please provide a valid Google / Gmail address:\n"
            "<code>/request yourname@gmail.com</code>",
            parse_mode=ParseMode.HTML,
        )
        return

    now_str = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

    # 1. Check if email exclusion/whitelist is active
    if ALLOWED_EMAILS and email not in ALLOWED_EMAILS:
        await update.message.reply_text(
            f"⛔ <b>Access Denied</b>\n\n"
            f"The email <code>{html.escape(email)}</code> is not on the approved access list.\n\n"
            "Your request has been recorded. Please contact the owner to have your email approved.",
            parse_mode=ParseMode.HTML,
        )
        await notify_admin(
            context,
            f"⚠️ <b>Access Request Blocked (Not in Whitelist)</b>\n"
            f"• <b>User:</b> {html.escape(user.full_name)} (@{user.username or 'none'}, ID: <code>{user.id}</code>)\n"
            f"• <b>Requested Email:</b> <code>{html.escape(email)}</code>\n"
            f"• <b>Time:</b> {now_str}",
        )
        return

    # 2. Grant access to the Google Drive folder
    if not GOOGLE_DRIVE_FOLDER_ID:
        await update.message.reply_text(
            "⚠️ No root Google Drive folder is configured. Please use <code>/list</code> to select specific files.",
            parse_mode=ParseMode.HTML,
        )
        return

    status_msg = await update.message.reply_text(
        f"⏳ Processing access request for <b>{html.escape(email)}</b>...",
        parse_mode=ParseMode.HTML,
    )

    try:
        folder_meta = drive_service.get_file_metadata(GOOGLE_DRIVE_FOLDER_ID)
        folder_name = folder_meta.get("name", "Shared Drive Folder")

        drive_service.share_file_with_email(
            GOOGLE_DRIVE_FOLDER_ID, email, role=DEFAULT_SHARE_ROLE
        )

        folder_link = folder_meta.get("webViewLink", "")
        link_html = (
            f"\n🔗 <a href='{folder_link}'>Open Shared Folder in Google Drive</a>"
            if folder_link
            else ""
        )

        await status_msg.edit_text(
            f"🎉 <b>Access Granted Successfully!</b>\n\n"
            f"📂 <b>Folder:</b> {html.escape(folder_name)}\n"
            f"👤 <b>Email:</b> <code>{html.escape(email)}</code>\n"
            f"🛡️ <b>Permission:</b> {DEFAULT_SHARE_ROLE.capitalize()} (Viewer)\n\n"
            f"Google has sent an invitation email to your address with access to the folder and all its files.{link_html}",
            parse_mode=ParseMode.HTML,
            disable_web_page_preview=False,
        )

        await notify_admin(
            context,
            f"🔔 <b>Offline Bot Activity Alert</b>\n"
            f"• <b>User:</b> {html.escape(user.full_name)} (@{user.username or 'none'}, ID: <code>{user.id}</code>)\n"
            f"• <b>Action:</b> Granted Folder Access via /request\n"
            f"• <b>Target Email:</b> <code>{html.escape(email)}</code>\n"
            f"• <b>Folder:</b> <i>{html.escape(folder_name)}</i>\n"
            f"• <b>Time:</b> {now_str}",
        )

    except Exception as e:
        logger.error("Failed to grant folder access to %s: %s", email, e)
        await status_msg.edit_text(f"❌ Failed to grant Drive access: {html.escape(str(e))}")


async def direct_share_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Direct command: /share <file_id> <email>"""
    if not is_user_authorized(update):
        await update.message.reply_text("🔒 Please authenticate first using /auth <passcode>.")
        return

    args = context.args
    if not args or len(args) < 2:
        await update.message.reply_text(
            "Usage: <code>/share &lt;file_id&gt; &lt;email@gmail.com&gt;</code>",
            parse_mode=ParseMode.HTML,
        )
        return

    file_id, email = args[0].strip(), args[1].strip()
    if not EMAIL_REGEX.match(email):
        await update.message.reply_text("❌ Invalid email format.")
        return

    # Check if email is on the approved list (if restricted)
    if ALLOWED_EMAILS and email.lower() not in ALLOWED_EMAILS:
        await update.message.reply_text(
            f"⛔ <b>Access Denied:</b> <code>{html.escape(email)}</code> is not on the approved access list.",
            parse_mode=ParseMode.HTML,
        )
        return

    status_msg = await update.message.reply_text(f"⏳ Granting access to {html.escape(email)}...")
    try:
        meta = drive_service.get_file_metadata(file_id)
        file_name = meta.get("name", "File")
        drive_service.share_file_with_email(file_id, email, role=DEFAULT_SHARE_ROLE)

        await status_msg.edit_text(
            f"✅ Access to <b>{html.escape(file_name)}</b> granted to <b>{html.escape(email)}</b>!",
            parse_mode=ParseMode.HTML,
        )

        user = update.effective_user
        now_str = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        await notify_admin(
            context,
            f"🔔 <b>Offline Bot Activity Alert</b>\n"
            f"• <b>User:</b> {html.escape(user.full_name)} (@{user.username or 'none'})\n"
            f"• <b>Action:</b> Granted Drive Access\n"
            f"• <b>Target Email:</b> <code>{html.escape(email)}</code>\n"
            f"• <b>File:</b> <i>{html.escape(file_name)}</i>\n"
            f"• <b>Time:</b> {now_str}",
        )
    except Exception as e:
        logger.error("Direct share error: %s", e)
        await status_msg.edit_text(f"❌ Failed to share: {html.escape(str(e))}")


async def direct_get_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Direct command: /get <file_id>"""
    if not is_user_authorized(update):
        await update.message.reply_text("🔒 Please authenticate first using /auth <passcode>.")
        return

    args = context.args
    if not args:
        await update.message.reply_text("Usage: <code>/get &lt;file_id&gt;</code>", parse_mode=ParseMode.HTML)
        return

    file_id = args[0].strip()
    await execute_download(file_id, update, context)


class HealthCheckHandler(BaseHTTPRequestHandler):
    """Simple HTTP handler so cloud hosts like Render can monitor bot health."""
    def do_GET(self):
        self.send_response(200)
        self.send_header("Content-type", "text/plain")
        self.end_headers()
        self.wfile.write(b"Telegram Drive Bot is active and running 24/7!\n")

    def log_message(self, format, *args):
        # Silence access logs so terminal stays clean
        pass


def start_health_server():
    """Binds to $PORT (or 8080) in a background thread for cloud platforms (Render, etc.)."""
    port = int(os.getenv("PORT", "8080"))
    try:
        server = HTTPServer(("0.0.0.0", port), HealthCheckHandler)
        logger.info("Health-check server running on port %s", port)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
    except Exception as e:
        logger.warning("Could not start HTTP health server: %s", e)


def main():
    """Main application loop."""
    if not TELEGRAM_BOT_TOKEN:
        print("\n" + "=" * 60)
        print("❌ ERROR: TELEGRAM_BOT_TOKEN is not set!")
        print("Please copy .env.example to .env and configure your bot token.")
        print("=" * 60 + "\n")
        return

    if not drive_service.is_authenticated():
        print("\n" + "=" * 60)
        print("⚠️ WARNING: Google Drive credentials not found.")
        print("Please place 'service_account.json' or 'credentials.json' in the project directory.")
        print("=" * 60 + "\n")

    print("🚀 Initializing Telegram Drive Access Bot...")
    start_health_server()
    application = Application.builder().token(TELEGRAM_BOT_TOKEN).build()

    # Share email conversation handler
    share_conv_handler = ConversationHandler(
        entry_points=[CallbackQueryHandler(handle_callback_query, pattern=r"^share_")],
        states={
            AWAITING_EMAIL: [
                MessageHandler(filters.TEXT & ~filters.COMMAND, process_email_share)
            ]
        },
        fallbacks=[CommandHandler("cancel", cancel_conversation)],
        allow_reentry=True,
    )

    # Register handlers
    application.add_handler(CommandHandler("start", start_command))
    application.add_handler(CommandHandler("auth", auth_command))
    application.add_handler(CommandHandler("help", help_command))
    application.add_handler(CommandHandler("list", list_files_command))
    application.add_handler(CommandHandler("files", list_files_command))
    application.add_handler(CommandHandler("search", search_command))
    application.add_handler(CommandHandler("get", direct_get_command))
    application.add_handler(CommandHandler("share", direct_share_command))
    application.add_handler(CommandHandler("request", request_command))
    application.add_handler(CommandHandler("register", register_command))

    # Handle when bot is added to a new chat/group
    application.add_handler(MessageHandler(filters.StatusUpdate.NEW_CHAT_MEMBERS, on_new_chat_members))

    # Add conversation handler before generic callback handler
    application.add_handler(share_conv_handler)
    application.add_handler(CallbackQueryHandler(handle_callback_query))

    # Add error handler so bot never crashes on unhandled telegram exceptions
    application.add_error_handler(error_handler)

    print("🤖 Bot is running! Press Ctrl+C to stop.")
    application.run_polling(drop_pending_updates=True)


if __name__ == "__main__":
    main()
