"""Google Drive uploader.

The module is split in two layers:

  * `DriveClient` (Protocol)   — narrow surface used by the rest of the code.
  * `GoogleDriveClient`        — concrete implementation backed by the official
                                 `google-api-python-client` SDK.

The thin protocol makes the surface easy to fake in tests (see test_drive.py)
without dragging in the SDK or network.

Higher-level helpers `ensure_folder_chain` and `upload_file` implement the
idempotent, state-cached logic the runner uses.
"""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Iterable, Protocol

from .state import State

FOLDER_MIME = "application/vnd.google-apps.folder"


class DriveAPIError(RuntimeError):
    """Raised when the underlying Drive API call fails."""


class DriveClient(Protocol):
    def find_one(self, parent_id: str, name: str, *, folder: bool = False) -> str | None: ...
    def create_folder(self, parent_id: str, name: str) -> str: ...
    def create_file(self, parent_id: str, name: str, abspath: Path) -> str: ...
    def update_file(self, file_id: str, abspath: Path) -> str: ...


# ---------- high-level helpers ----------

def ensure_folder_chain(
    *,
    client: DriveClient,
    root_parent_id: str,
    parts: Iterable[str],
    state: State,
) -> str:
    """Ensure the folder chain `root_parent_id / parts[0] / parts[1] / ...` exists.

    State cache key is the joined relative path (no leading slash). The function
    is safe to call repeatedly; missing levels are created and cached.
    """
    parts = [p for p in parts if p]
    if not parts:
        return root_parent_id

    parent_id = root_parent_id
    cache_key = ""
    for name in parts:
        cache_key = f"{cache_key}/{name}" if cache_key else name

        cached = state.get_drive_folder(cache_key)
        if cached:
            parent_id = cached
            continue

        existing = client.find_one(parent_id, name, folder=True)
        if existing:
            state.set_drive_folder(cache_key, existing)
            parent_id = existing
            continue

        created = client.create_folder(parent_id, name)
        state.set_drive_folder(cache_key, created)
        parent_id = created

    return parent_id


def upload_file(
    *,
    client: DriveClient,
    parent_id: str,
    name: str,
    abspath: Path,
) -> str:
    """Upload `abspath` as `name` under `parent_id`. If a file with that name
    already exists in the parent, update it instead. Returns the Drive file ID.
    """
    existing = client.find_one(parent_id, name, folder=False)
    if existing:
        return client.update_file(existing, abspath)
    return client.create_file(parent_id, name, abspath)


# ---------- concrete Google Drive client ----------

@dataclass
class GoogleDriveClient:
    """Concrete DriveClient backed by `googleapiclient.discovery.build('drive', 'v3', ...)`.

    Construct via `GoogleDriveClient.from_credentials(creds_path, token_path)`.
    """

    service: object  # googleapiclient discovery resource

    @classmethod
    def from_credentials(
        cls,
        credentials_path: Path,
        token_path: Path,
        *,
        scopes: tuple[str, ...] = ("https://www.googleapis.com/auth/drive.file",),
    ) -> "GoogleDriveClient":
        # Imports kept local so that test environments without the SDK still
        # exercise the rest of the codebase.
        from google.auth.transport.requests import Request
        from google.oauth2.credentials import Credentials
        from google_auth_oauthlib.flow import InstalledAppFlow
        from googleapiclient.discovery import build

        creds: Credentials | None = None
        token_path = Path(token_path)
        credentials_path = Path(credentials_path)

        if token_path.exists():
            creds = Credentials.from_authorized_user_file(str(token_path), list(scopes))

        if creds and creds.expired and creds.refresh_token:
            try:
                creds.refresh(Request())
            except Exception as exc:
                raise DriveAPIError(f"failed to refresh OAuth token: {exc}") from exc

        if not creds or not creds.valid:
            if not credentials_path.exists():
                raise DriveAPIError(
                    f"no valid token at {token_path} and no credentials at {credentials_path}; "
                    "run scripts/authorize_drive.py once on a desktop machine"
                )
            flow = InstalledAppFlow.from_client_secrets_file(str(credentials_path), list(scopes))
            creds = flow.run_local_server(port=0)
            token_path.parent.mkdir(parents=True, exist_ok=True)
            token_path.write_text(creds.to_json())

        service = build("drive", "v3", credentials=creds, cache_discovery=False)
        return cls(service=service)

    # ----- DriveClient interface -----

    def find_one(self, parent_id: str, name: str, *, folder: bool = False) -> str | None:
        from googleapiclient.errors import HttpError

        escaped = name.replace("\\", "\\\\").replace("'", "\\'")
        q_parts = [
            f"'{parent_id}' in parents",
            f"name = '{escaped}'",
            "trashed = false",
        ]
        if folder:
            q_parts.append(f"mimeType = '{FOLDER_MIME}'")
        else:
            q_parts.append(f"mimeType != '{FOLDER_MIME}'")
        try:
            resp = (
                self.service.files()  # type: ignore[attr-defined]
                .list(q=" and ".join(q_parts), fields="files(id,name)", pageSize=2)
                .execute()
            )
        except HttpError as exc:
            raise DriveAPIError(f"list failed for {name!r} under {parent_id}: {exc}") from exc

        files = resp.get("files", [])
        if not files:
            return None
        return files[0]["id"]

    def create_folder(self, parent_id: str, name: str) -> str:
        from googleapiclient.errors import HttpError

        body = {"name": name, "mimeType": FOLDER_MIME, "parents": [parent_id]}
        try:
            resp = self.service.files().create(body=body, fields="id").execute()  # type: ignore[attr-defined]
        except HttpError as exc:
            raise DriveAPIError(f"create folder {name!r} under {parent_id} failed: {exc}") from exc
        return resp["id"]

    def create_file(self, parent_id: str, name: str, abspath: Path) -> str:
        from googleapiclient.errors import HttpError
        from googleapiclient.http import MediaFileUpload

        body = {"name": name, "parents": [parent_id]}
        media = MediaFileUpload(str(abspath), resumable=True)
        try:
            resp = (
                self.service.files()  # type: ignore[attr-defined]
                .create(body=body, media_body=media, fields="id")
                .execute()
            )
        except HttpError as exc:
            raise DriveAPIError(f"create file {name!r} under {parent_id} failed: {exc}") from exc
        return resp["id"]

    def update_file(self, file_id: str, abspath: Path) -> str:
        from googleapiclient.errors import HttpError
        from googleapiclient.http import MediaFileUpload

        media = MediaFileUpload(str(abspath), resumable=True)
        try:
            resp = (
                self.service.files()  # type: ignore[attr-defined]
                .update(fileId=file_id, media_body=media, fields="id")
                .execute()
            )
        except HttpError as exc:
            raise DriveAPIError(f"update file {file_id} failed: {exc}") from exc
        return resp["id"]
