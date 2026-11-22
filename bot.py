import html
import io
import logging
import os
import re
import threading
from datetime import datetime
from http.server import BaseHTTPRequestHandler, HTTPServer
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
    """Handles /start command."""
    user = update.effective_user
    user_name = html.escape(user.first_name if user else "there")

    if not is_user_authorized(update):
        if ACCESS_PASSCODE:
            await update.message.reply_text(
                f"👋 Hello {user_name}!\n\n"
                "🔒 This bot requires an access passcode to view or request files.\n"
                "Please enter the passcode using:\n"
                "<code>/auth &lt;passcode&gt;</code>",
                parse_mode=ParseMode.HTML,
            )
        else:
            await update.message.reply_text(
                "⛔ Sorry, you are not authorized to use this bot."
            )
        return

    welcome_text = (
        f"👋 Welcome <b>{user_name}</b>!\n\n"
        "I am your automated <b>Google Drive File Access Assistant</b>. "
        "Even when the drive owner is offline, I can help you search, download, or "
        "grant direct Google Drive access to the files you need.\n\n"
        "<b>Available Commands:</b>\n"
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
        "1. <b>Browse Files:</b> Use <code>/list</code> to see the files shared in the repository.\n"
        "2. <b>Search:</b> Use <code>/search &lt;filename&gt;</code> to quickly find a file.\n"
        "3. <b>Direct Download:</b> Click <i>'📥 Download File'</i> or use <code>/get &lt;file_id&gt;</code>. "
        "Files under 50MB will be sent directly to you in Telegram.\n"
        "4. <b>Google Drive Sharing:</b> Click <i>'🔑 Grant Drive Access'</i> to add your Google/Gmail account "
        "as a viewer on Drive so you can open it directly in your own Google Drive."
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

    # Add conversation handler before generic callback handler
    application.add_handler(share_conv_handler)
    application.add_handler(CallbackQueryHandler(handle_callback_query))

    # Add error handler so bot never crashes on unhandled telegram exceptions
    application.add_error_handler(error_handler)

    print("🤖 Bot is running! Press Ctrl+C to stop.")
    application.run_polling(drop_pending_updates=True)


if __name__ == "__main__":
    main()
