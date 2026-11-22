"""
Helper script to generate token.json for OAuth2 personal Google Drive access.
Run this ONCE locally in an interactive terminal with a browser:
    python setup_oauth.py
After this completes, token.json is created and the bot can run headlessly/offline!
"""

import os
from pathlib import Path
from google_auth_oauthlib.flow import InstalledAppFlow
from google.oauth2.credentials import Credentials
from google.auth.transport.requests import Request

SCOPES = ["https://www.googleapis.com/auth/drive"]
BASE_DIR = Path(__file__).resolve().parent
CREDENTIALS_FILE = BASE_DIR / "credentials.json"
TOKEN_FILE = BASE_DIR / "token.json"


def main():
    if not CREDENTIALS_FILE.exists():
        print(f"❌ Error: {CREDENTIALS_FILE} not found!")
        print("Please download your OAuth 2.0 Client credentials from Google Cloud Console")
        print("and save it as 'credentials.json' in this folder.")
        return

    creds = None
    if TOKEN_FILE.exists():
        creds = Credentials.from_authorized_user_file(str(TOKEN_FILE), SCOPES)

    if not creds or not creds.valid:
        if creds and creds.expired and creds.refresh_token:
            print("🔄 Refreshing expired token...")
            creds.refresh(Request())
        else:
            print("🌐 Opening browser to authenticate with Google Drive...")
            flow = InstalledAppFlow.from_client_secrets_file(
                str(CREDENTIALS_FILE), SCOPES
            )
            creds = flow.run_local_server(port=0)

        with open(TOKEN_FILE, "w", encoding="utf-8") as f:
            f.write(creds.to_json())
        print(f"✅ Success! Token saved to {TOKEN_FILE}.")
        print("You can now run 'python bot.py' anytime, even offline or on a remote server!")


if __name__ == "__main__":
    main()
