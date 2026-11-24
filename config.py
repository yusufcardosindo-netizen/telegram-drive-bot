import os
from pathlib import Path
from dotenv import load_dotenv

# Load environment variables from .env or .env.example
BASE_DIR = Path(__file__).resolve().parent
RENDER_SECRETS_DIR = Path("/etc/secrets")

# 1. Load local .env if present
if (BASE_DIR / ".env").exists():
    load_dotenv(BASE_DIR / ".env")
# 2. Load Render Cloud Secret File if present
if (RENDER_SECRETS_DIR / ".env").exists():
    load_dotenv(RENDER_SECRETS_DIR / ".env")
# 3. Load .env.example as fallback
if (BASE_DIR / ".env.example").exists():
    load_dotenv(BASE_DIR / ".env.example")

# Telegram Configuration
TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "").strip()
ADMIN_TELEGRAM_ID = os.getenv("ADMIN_TELEGRAM_ID", "").strip()

# Google Drive Configuration
# Folder ID to scope queries to (Leave empty to search entire accessible Drive)
_raw_folder_id = os.getenv("GOOGLE_DRIVE_FOLDER_ID", "").strip()
if "folders/" in _raw_folder_id:
    GOOGLE_DRIVE_FOLDER_ID = _raw_folder_id.split("folders/")[1].split("?")[0].strip()
else:
    GOOGLE_DRIVE_FOLDER_ID = _raw_folder_id.split("?")[0].strip()

# Authentication file paths: checks local directory, environment variable, and Render /etc/secrets
_service_candidates = [
    os.getenv("SERVICE_ACCOUNT_FILE"),
    str(BASE_DIR / "service_account.json"),
    str(RENDER_SECRETS_DIR / "service_account.json"),
]
SERVICE_ACCOUNT_FILE = next(
    (p for p in _service_candidates if p and os.path.exists(p)),
    str(BASE_DIR / "service_account.json"),
)
OAUTH_CREDENTIALS_FILE = os.getenv(
    "OAUTH_CREDENTIALS_FILE", str(BASE_DIR / "credentials.json")
)
OAUTH_TOKEN_FILE = os.getenv("OAUTH_TOKEN_FILE", str(BASE_DIR / "token.json"))

# Access Control & Security
# If set, users must supply this passcode via /auth <passcode> before requesting files
ACCESS_PASSCODE = os.getenv("ACCESS_PASSCODE", "").strip()

# Comma-separated list of Telegram User IDs or Usernames allowed to request files
_allowed_users_raw = os.getenv("ALLOWED_TELEGRAM_USERS", "").strip()
ALLOWED_TELEGRAM_USERS = (
    [u.strip().lstrip("@").lower() for u in _allowed_users_raw.split(",") if u.strip()]
    if _allowed_users_raw
    else []
)

# Comma-separated list of approved Google/Gmail accounts allowed to receive file access
# If set, only these emails will be granted Google Drive permissions.
# Example: friend1@gmail.com,colleague@gmail.com
_allowed_emails_raw = os.getenv("ALLOWED_EMAILS", "").strip()
ALLOWED_EMAILS = (
    [e.strip().lower() for e in _allowed_emails_raw.split(",") if e.strip()]
    if _allowed_emails_raw
    else []
)

# Comma-separated list of allowed Topic/Thread IDs in Telegram forum supergroups
# If set, the bot will strictly and ONLY respond inside these topics/sections.
# Example: ALLOWED_TOPIC_IDS=1949
_allowed_topics_raw = os.getenv("ALLOWED_TOPIC_IDS", "").strip()
ALLOWED_TOPIC_IDS = (
    [int(t.strip()) for t in _allowed_topics_raw.split(",") if t.strip().isdigit()]
    if _allowed_topics_raw
    else []
)

# Feature Flags
ENABLE_DIRECT_DOWNLOAD = os.getenv("ENABLE_DIRECT_DOWNLOAD", "true").lower() in ("1", "true", "yes")
ENABLE_EMAIL_SHARING = os.getenv("ENABLE_EMAIL_SHARING", "true").lower() in ("1", "true", "yes")
DEFAULT_SHARE_ROLE = os.getenv("DEFAULT_SHARE_ROLE", "reader").lower()  # 'reader' or 'commenter'

# Max file size in bytes for direct Telegram sending (Telegram Bot API limit is 50MB)
MAX_TELEGRAM_FILE_SIZE = 49 * 1024 * 1024  # 49 MB to be safe
