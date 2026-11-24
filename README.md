# 🤖 Telegram Google Drive Access Bot (Offline Autonomous Sharer)

A Telegram Bot that automatically grants file access or delivers files from your Google Drive to people who request them—**even when you are completely offline**.

---

## 🌟 Key Features

- **Autonomous Offline Sharing:** Runs 24/7 on your PC or cloud server. When a colleague, client, or friend requests a file, the bot fulfills it immediately without requiring you to be online.
- **Direct Drive Permission Granting:** The bot can directly add the requester's Gmail/Google account as a **Viewer (`reader`)** on Google Drive via the official Google Drive API, automatically triggering Google's invitation email.
- **Direct Telegram Delivery:** For files under 50MB, the bot can stream and send the file directly inside Telegram chat.
- **Smart Fallback:** If a file exceeds Telegram's 50MB limit, it generates an instant Google Drive link.
- **Granular Security & Privacy:**
  - **Folder Scoping:** Restrict the bot to a designated folder so it cannot touch your personal, private drive.
  - **Passcode Protection:** Optionally require a secret PIN (`/auth <passcode>`) before anyone can access or search files.
  - **User Whitelist:** Restrict access to specific Telegram usernames or user IDs.
- **Offline Audit Trail:** Whenever someone accesses or downloads a file, the bot sends an instant notification to your personal Telegram account with the requester's info, email, file name, and timestamp.

---

## 📁 Project Structure

```
telegram-drive-bot/
├── bot.py                # Main Telegram bot logic and handlers
├── drive_service.py      # Google Drive API integration (Search, Download, Permissions)
├── config.py             # Configuration loader & validation
├── setup_oauth.py        # Helper script for OAuth2 personal Google Drive setup
├── requirements.txt      # Python dependencies
├── .env.example          # Environment variables template
├── .gitignore            # Protects credentials and secrets
└── README.md             # Documentation & setup guide
```

---

## 🚀 Setup Guide

### Step 1: Create a Telegram Bot

1. Open Telegram and search for [`@BotFather`](https://t.me/BotFather).
2. Send `/newbot` and follow the instructions to choose a name and username.
3. BotFather will provide an **HTTP API Token** (e.g. `123456789:ABCdefGhIJKlmNoPQRsTUVwxyZ`).
4. Copy this token for your `.env` file.
5. *(Optional)* Get your personal Telegram User ID by messaging [`@userinfobot`](https://t.me/userinfobot) so the bot can send you offline activity alerts.

---

### Step 2: Set Up Google Drive API (Recommended: Service Account)

Using a **Google Cloud Service Account** is the best and cleanest approach for an autonomous bot because it requires no browser logins and runs indefinitely without token expirations.

1. Go to the [Google Cloud Console](https://console.cloud.google.com/).
2. Create a new project (e.g., `Telegram-Drive-Bot`).
3. In the sidebar, go to **APIs & Services** > **Library**.
4. Search for **Google Drive API** and click **Enable**.
5. Go to **APIs & Services** > **Credentials**.
6. Click **Create Credentials** > **Service Account**:
   - Name: `drive-bot-service`
   - Role: `Editor` or `Owner` (or leave default).
   - Click **Done**.
7. In the Service Accounts list, click on the newly created service account email.
8. Go to the **Keys** tab > **Add Key** > **Create new key** > select **JSON** > **Create**.
9. A JSON file will download. Rename it to `service_account.json` and move it into the `telegram-drive-bot/` folder.
10. **Share your Drive Folder with the Bot:**
    - Open Google Drive in your browser.
    - Create or select the folder you want the bot to share files from.
    - Click **Share**, and paste the Service Account email address (e.g., `drive-bot-service@your-project.iam.gserviceaccount.com`).
    - Grant it **Editor** permissions so it can manage file permissions on your behalf.
    - Copy the folder ID from the URL (`drive.google.com/drive/folders/<FOLDER_ID>`) into `.env`.

*(Alternative: If you prefer to use your personal Google account with full Drive access instead of a Service Account, download OAuth 2.0 Client credentials as `credentials.json` and run `python setup_oauth.py`).*

---

### Step 3: Install Dependencies

Open PowerShell or terminal in the project directory:

```bash
cd D:\telegram-drive-bot

# 1. Create a virtual environment
python -m venv venv

# 2. Activate virtual environment
# On Windows PowerShell:
.\venv\Scripts\Activate.ps1
# On Linux / macOS:
# source venv/bin/activate

# 3. Install packages
pip install -r requirements.txt
```

---

### Step 4: Configure `.env`

Copy the template:

```bash
cp .env.example .env
```

Open `.env` and fill in your details:

```ini
TELEGRAM_BOT_TOKEN=123456789:ABCdefGhIJKlmNoPQRsTUVwxyZ
ADMIN_TELEGRAM_ID=your_telegram_id_here
GOOGLE_DRIVE_FOLDER_ID=your_drive_folder_id_here
ACCESS_PASSCODE=mysecretcode   # Set a passcode or leave blank for open access
DEFAULT_SHARE_ROLE=reader     # 'reader' (Viewer) or 'commenter'
```

---

### Step 5: Start the Bot

```bash
python bot.py
```

You should see:
```
🚀 Initializing Telegram Drive Access Bot...
🤖 Bot is running! Press Ctrl+C to stop.
```

---

## 📱 How Users Interact with Your Bot

| Action | Command / Interaction | Description |
|---|---|---|
| **Start** | `/start` or `/start @AcessV_Bot` | Displays welcome screen (tailored for private chat or group). |
| **Authorize** | `/auth <passcode>` | Unlocks file access if you configured a passcode. |
| **Register** | `/register <your@gmail.com>` | Links user's Google email and grants Drive repository access. |
| **Browse** | `/list` or `/files` | Lists available files with action buttons. |
| **Search** | `/search <filename>` | Searches files matching the keyword. |
| **Request Access** | `/request <your@gmail.com>` | Instantly grants access to the shared Google Drive folder. |
| **Download** | Click `📥 Download File` or `/get <file_id>` | Sends file directly via Telegram (if <= 50MB) or provides link. |
| **Grant Access** | Click `🔑 Grant Drive Access` | Prompts user for their Gmail, then grants instant Drive access! |
| **Direct Share** | `/share <file_id> <email>` | Grants access to the specified Google account immediately. |

---

## 🌐 Running 24/7 While You Are Offline

To allow the bot to answer requests when your personal computer is turned off, you can run it on any free/low-cost cloud platform or background service:

### Option A: Free Cloud Hosting (e.g. Render / Railway / Fly.io)
1. Push this project to a private GitHub repository (make sure `.env` and `service_account.json` are in `.gitignore`).
2. Deploy as a background worker on [Render](https://render.com) or [Railway](https://railway.app).
3. Set your environment variables in the host dashboard.
4. For `service_account.json` on cloud hosts, you can paste its content as an environment variable or secret file.

### Option B: Run on a Home PC / Mini PC / Raspberry Pi
Use Windows Task Scheduler or NSSM (Non-Sucking Service Manager) to run `python bot.py` as a background Windows service that auto-starts on system boot.
