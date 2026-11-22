import io
import logging
import os
from typing import Any, Dict, List, Optional

from google.auth.transport.requests import Request
from google.oauth2 import service_account
from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import InstalledAppFlow
from googleapiclient.discovery import Resource, build
from googleapiclient.http import MediaIoBaseDownload

from config import (
    GOOGLE_DRIVE_FOLDER_ID,
    OAUTH_CREDENTIALS_FILE,
    OAUTH_TOKEN_FILE,
    SERVICE_ACCOUNT_FILE,
)

logger = logging.getLogger(__name__)

# Scopes needed: full drive access to read, download, and manage permissions
SCOPES = ["https://www.googleapis.com/auth/drive"]

# Mapping of Google Workspace mime types to export formats
GOOGLE_EXPORT_MIME_TYPES = {
    "application/vnd.google-apps.document": (
        "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
        ".docx",
    ),
    "application/vnd.google-apps.spreadsheet": (
        "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        ".xlsx",
    ),
    "application/vnd.google-apps.presentation": (
        "application/vnd.openxmlformats-officedocument.presentationml.presentation",
        ".pptx",
    ),
}


class GoogleDriveService:
    def __init__(self):
        self.service: Optional[Resource] = None
        self._init_service()

    def _init_service(self):
        """Initializes the Google Drive API service using Service Account or OAuth2."""
        # 1. Try Service Account first (recommended for headless/offline bots)
        if os.path.exists(SERVICE_ACCOUNT_FILE):
            logger.info("Authenticating with Service Account: %s", SERVICE_ACCOUNT_FILE)
            creds = service_account.Credentials.from_service_account_file(
                SERVICE_ACCOUNT_FILE, scopes=SCOPES
            )
            self.service = build("drive", "v3", credentials=creds)
            return

        # 2. Try OAuth2 credentials / token
        if os.path.exists(OAUTH_TOKEN_FILE):
            logger.info("Authenticating with OAuth2 token: %s", OAUTH_TOKEN_FILE)
            creds = Credentials.from_authorized_user_file(OAUTH_TOKEN_FILE, SCOPES)
            if creds and creds.expired and creds.refresh_token:
                creds.refresh(Request())
                with open(OAUTH_TOKEN_FILE, "w", encoding="utf-8") as token_file:
                    token_file.write(creds.to_json())
            self.service = build("drive", "v3", credentials=creds)
            return

        if os.path.exists(OAUTH_CREDENTIALS_FILE):
            logger.info("Initializing OAuth2 flow from %s", OAUTH_CREDENTIALS_FILE)
            flow = InstalledAppFlow.from_client_secrets_file(
                OAUTH_CREDENTIALS_FILE, SCOPES
            )
            creds = flow.run_local_server(port=0)
            with open(OAUTH_TOKEN_FILE, "w", encoding="utf-8") as token_file:
                token_file.write(creds.to_json())
            self.service = build("drive", "v3", credentials=creds)
            return

        logger.warning(
            "No Google Drive credentials found! Please provide service_account.json or credentials.json."
        )

    def is_authenticated(self) -> bool:
        return self.service is not None

    def list_files(
        self, query_name: Optional[str] = None, page_size: int = 15
    ) -> List[Dict[str, Any]]:
        """Lists files, optionally filtered by folder and filename query."""
        if not self.service:
            raise RuntimeError("Google Drive service is not authenticated.")

        conditions = ["trashed = false"]

        if GOOGLE_DRIVE_FOLDER_ID:
            conditions.append(f"'{GOOGLE_DRIVE_FOLDER_ID}' in parents")

        if query_name:
            # Escape single quotes in search query
            safe_query = query_name.replace("'", "\\'")
            conditions.append(f"name contains '{safe_query}'")

        q = " and ".join(conditions)

        fields = (
            "nextPageToken, files(id, name, mimeType, size, webViewLink, webContentLink)"
        )
        response = (
            self.service.files()
            .list(
                q=q,
                pageSize=page_size,
                fields=fields,
                orderBy="modifiedTime desc",
                supportsAllDrives=True,
                includeItemsFromAllDrives=True,
            )
            .execute()
        )

        return response.get("files", [])

    def get_file_metadata(self, file_id: str) -> Dict[str, Any]:
        """Fetches metadata for a specific file."""
        if not self.service:
            raise RuntimeError("Google Drive service is not authenticated.")

        fields = "id, name, mimeType, size, webViewLink, webContentLink, permissions"
        return (
            self.service.files()
            .get(
                fileId=file_id,
                fields=fields,
                supportsAllDrives=True,
            )
            .execute()
        )

    def download_file(self, file_id: str) -> tuple[io.BytesIO, str]:
        """
        Downloads a file or exports Google Workspace docs into BytesIO.
        Returns (bytes_stream, filename).
        """
        if not self.service:
            raise RuntimeError("Google Drive service is not authenticated.")

        metadata = self.get_file_metadata(file_id)
        name = metadata.get("name", "downloaded_file")
        mime_type = metadata.get("mimeType", "")

        stream = io.BytesIO()

        # Handle native Google Docs / Sheets / Slides export
        if mime_type in GOOGLE_EXPORT_MIME_TYPES:
            export_mime, ext = GOOGLE_EXPORT_MIME_TYPES[mime_type]
            if not name.endswith(ext):
                name = f"{name}{ext}"
            request = self.service.files().export_media(
                fileId=file_id, mimeType=export_mime
            )
        else:
            request = self.service.files().get_media(
                fileId=file_id, supportsAllDrives=True
            )

        downloader = MediaIoBaseDownload(stream, request)
        done = False
        while not done:
            _, done = downloader.next_chunk()

        stream.seek(0)
        return stream, name

    def share_file_with_email(
        self, file_id: str, email: str, role: str = "reader"
    ) -> Dict[str, Any]:
        """
        Grants access to the specified Google account email.
        Role can be 'reader' (Viewer) or 'commenter'.
        Google automatically sends an invitation email.
        """
        if not self.service:
            raise RuntimeError("Google Drive service is not authenticated.")

        permission = {
            "type": "user",
            "role": role,
            "emailAddress": email,
        }

        created = (
            self.service.permissions()
            .create(
                fileId=file_id,
                body=permission,
                fields="id, emailAddress, role",
                sendNotificationEmail=True,
                supportsAllDrives=True,
            )
            .execute()
        )

        logger.info(
            "Granted %s access on file %s to %s (permission ID: %s)",
            role,
            file_id,
            email,
            created.get("id"),
        )
        return created

    def make_shareable_link(self, file_id: str) -> str:
        """
        Generates or ensures a public view link ('anyone with link can view').
        """
        if not self.service:
            raise RuntimeError("Google Drive service is not authenticated.")

        permission = {
            "type": "anyone",
            "role": "reader",
        }

        try:
            self.service.permissions().create(
                fileId=file_id,
                body=permission,
                supportsAllDrives=True,
            ).execute()
        except Exception as e:
            # If already exists or error, continue to fetch link
            logger.debug("Permission create notice: %s", e)

        meta = self.get_file_metadata(file_id)
        return meta.get("webViewLink", "")
