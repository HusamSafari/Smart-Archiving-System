"""
Google Drive Uploader Module

Handles all Google Drive API operations:
- Service account authentication (path or JSON string)
- File uploads from bytes (no local disk)
- Folder creation for media groups
- File size and MIME type validation with clear exceptions
"""

import io
import json
import logging
from datetime import datetime
from typing import Optional

from google.oauth2 import service_account
from googleapiclient.discovery import build
from googleapiclient.http import MediaIoBaseUpload
from googleapiclient.errors import HttpError

logger = logging.getLogger(__name__)


class FileTooLargeError(Exception):
    """Raised when file size exceeds MAX_FILE_SIZE_BYTES."""

    def __init__(self, size: int, max_size: int):
        self.size = size
        self.max_size = max_size
        super().__init__(f"File size {size} exceeds maximum {max_size} bytes")


class MimeNotAllowedError(Exception):
    """Raised when MIME type is not in ALLOWED_MIME_TYPES."""

    def __init__(self, mime_type: str):
        self.mime_type = mime_type
        super().__init__(f"MIME type not allowed: {mime_type}")


class DriveUploader:
    """Handles Google Drive file upload operations (in-memory only, no local files)."""

    SCOPES = ["https://www.googleapis.com/auth/drive.file"]

    def __init__(
        self,
        service_account_json: str,
        max_file_size: int = 20 * 1024 * 1024,  # 20 MB default
        allowed_mime_types: Optional[list] = None,
        text_format: str = "txt",
    ):
        """
        Initialize Drive Uploader.

        Args:
            service_account_json: Path to service account JSON file, or raw JSON string.
            max_file_size: Maximum file size in bytes.
            allowed_mime_types: List of allowed MIME types (None = allow all).
            text_format: "txt" or "doc" for text uploads.
        """
        self.max_file_size = max_file_size
        self.allowed_mime_types = allowed_mime_types
        self.text_format = text_format.lower() if text_format else "txt"

        credentials = self._get_credentials(service_account_json)
        self.service = build("drive", "v3", credentials=credentials)
        logger.info("Google Drive service initialized successfully")

    def _get_credentials(self, value: str):
        """Load credentials from file path or raw JSON string."""
        value = (value or "").strip()
        if not value:
            raise ValueError("GOOGLE_SERVICE_ACCOUNT_JSON is required")
        if value.startswith("{"):
            try:
                info = json.loads(value)
                return service_account.Credentials.from_service_account_info(
                    info, scopes=self.SCOPES
                )
            except json.JSONDecodeError as e:
                raise ValueError(f"Invalid service account JSON: {e}") from e
        try:
            return service_account.Credentials.from_service_account_file(
                value, scopes=self.SCOPES
            )
        except Exception as e:
            logger.error(f"Failed to load service account: {e}")
            raise

    def _validate_size(self, size: int) -> None:
        """Raise FileTooLargeError if size exceeds limit."""
        if size > self.max_file_size:
            raise FileTooLargeError(size, self.max_file_size)

    def _validate_mime(self, mime_type: Optional[str]) -> None:
        """Raise MimeNotAllowedError if MIME not allowed. None = no restriction when allowlist unset."""
        if self.allowed_mime_types is None:
            return
        if not mime_type:
            return
        if mime_type not in self.allowed_mime_types:
            raise MimeNotAllowedError(mime_type)

    def upload_file_bytes(
        self,
        folder_id: str,
        filename: str,
        content: bytes,
        mime_type: Optional[str] = None,
    ) -> str:
        """
        Upload bytes to Google Drive. Validates size and MIME; raises on failure.

        Returns:
            Drive file ID.
        """
        self._validate_size(len(content))
        self._validate_mime(mime_type)

        file_metadata = {"name": filename, "parents": [folder_id]}
        media = MediaIoBaseUpload(
            io.BytesIO(content),
            mimetype=mime_type or "application/octet-stream",
            resumable=True,
        )
        file = (
            self.service.files()
            .create(body=file_metadata, media_body=media, fields="id,name")
            .execute()
        )
        logger.info("Uploaded %s (ID: %s)", file.get("name"), file.get("id"))
        return file["id"]

    def create_subfolder(self, parent_id: str, name: str) -> str:
        """Create a folder under parent_id. Returns new folder ID."""
        file_metadata = {
            "name": name,
            "mimeType": "application/vnd.google-apps.folder",
            "parents": [parent_id],
        }
        folder = (
            self.service.files()
            .create(body=file_metadata, fields="id,name")
            .execute()
        )
        logger.info("Created folder %s (ID: %s)", folder.get("name"), folder.get("id"))
        return folder["id"]

    def upload_text_as_file(
        self,
        folder_id: str,
        content: str,
        base_name: str,
        format_type: str,
    ) -> str:
        """
        Upload text as .txt or Google Doc.

        Args:
            folder_id: Drive folder ID.
            content: Plain text content.
            base_name: Base filename (e.g. Note_20250202_143022); .txt added for txt.
            format_type: "txt" or "doc".

        Returns:
            Drive file ID.
        """
        fmt = (format_type or self.text_format).lower()
        if fmt not in ("txt", "doc"):
            fmt = "txt"

        if fmt == "doc":
            file_metadata = {
                "name": base_name,
                "parents": [folder_id],
                "mimeType": "application/vnd.google-apps.document",
            }
            media = MediaIoBaseUpload(
                io.BytesIO(content.encode("utf-8")),
                mimetype="text/plain",
                resumable=True,
            )
        else:
            file_metadata = {
                "name": f"{base_name}.txt",
                "parents": [folder_id],
            }
            media = MediaIoBaseUpload(
                io.BytesIO(content.encode("utf-8")),
                mimetype="text/plain",
                resumable=True,
            )

        file = (
            self.service.files()
            .create(body=file_metadata, media_body=media, fields="id,name")
            .execute()
        )
        logger.info("Uploaded text as %s (ID: %s)", file.get("name"), file.get("id"))
        return file["id"]

    def upload_media_group(
        self,
        folder_id: str,
        items: list[tuple[str, bytes, Optional[str]]],
    ) -> str:
        """
        Create subfolder Album_YYYYMMDD_HHMMSS and upload all items from memory.

        Args:
            folder_id: Parent folder ID.
            items: List of (filename, content_bytes, mime_type).

        Returns:
            New subfolder ID.
        """
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        subfolder_name = f"Album_{timestamp}"
        subfolder_id = self.create_subfolder(folder_id, subfolder_name)

        for filename, content, mime_type in items:
            self._validate_size(len(content))
            self._validate_mime(mime_type)
            self.upload_file_bytes(subfolder_id, filename, content, mime_type)

        logger.info("Media group uploaded: %s with %d files", subfolder_name, len(items))
        return subfolder_id
