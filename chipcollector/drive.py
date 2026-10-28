"""Envio de arquivos ao Google Drive.

Dois modos:
1. OAuth por usuário (recomendado): cada usuário conecta o próprio Drive pela interface.
   Requer CHIPS_DRIVE_CLIENT_SECRETS (JSON de um cliente OAuth "Web application") e o redirect
   {CHIPS_PUBLIC_URL}/api/drive/callback cadastrado no Google Cloud Console. Escopo: drive.file
   (o app só enxerga os arquivos que ele mesmo cria).
2. Conta de serviço: CHIPS_DRIVE_SERVICE_ACCOUNT + CHIPS_DRIVE_FOLDER_ID apontando para uma pasta
   dentro de um Drive compartilhado (contas de serviço não têm cota no "Meu Drive").
"""
from __future__ import annotations

import json
from pathlib import Path

from .config import get_settings

SCOPES = ["https://www.googleapis.com/auth/drive.file"]


class DriveError(RuntimeError):
    pass


def oauth_enabled() -> bool:
    return bool(get_settings().drive_client_secrets)


def service_account_enabled() -> bool:
    s = get_settings()
    return bool(s.drive_service_account and s.drive_folder_id)


def _flow(state: str | None = None):
    try:
        from google_auth_oauthlib.flow import Flow
    except ImportError as e:  # pragma: no cover
        raise DriveError("Instale o extra 'drive': pip install 'chipcollector[drive]'") from e
    s = get_settings()
    flow = Flow.from_client_secrets_file(s.drive_client_secrets, scopes=SCOPES, state=state)
    flow.redirect_uri = s.public_url.rstrip("/") + "/api/drive/callback"
    return flow


def authorization_url() -> tuple[str, str, str | None]:
    flow = _flow()
    url, state = flow.authorization_url(access_type="offline", prompt="consent", include_granted_scopes="true")
    return url, state, getattr(flow, "code_verifier", None)


def exchange_code(authorization_response: str, state: str, code_verifier: str | None = None) -> str:
    flow = _flow(state)
    if code_verifier:
        flow.code_verifier = code_verifier
    flow.fetch_token(authorization_response=authorization_response)
    return flow.credentials.to_json()


def _service(user_token: str | None):
    try:
        from googleapiclient.discovery import build
    except ImportError as e:  # pragma: no cover
        raise DriveError("Instale o extra 'drive': pip install 'chipcollector[drive]'") from e
    if user_token:
        from google.auth.transport.requests import Request
        from google.oauth2.credentials import Credentials

        creds = Credentials.from_authorized_user_info(json.loads(user_token), SCOPES)
        if not creds.valid and creds.refresh_token:
            creds.refresh(Request())
    elif service_account_enabled():
        from google.oauth2 import service_account

        creds = service_account.Credentials.from_service_account_file(
            get_settings().drive_service_account, scopes=["https://www.googleapis.com/auth/drive"])
    else:
        raise DriveError("Google Drive não conectado. Conecte sua conta em 'Exportar' ou configure uma conta de serviço.")
    return build("drive", "v3", credentials=creds, cache_discovery=False), creds


def _ensure_folder(svc, name: str, parent: str | None) -> str:
    q = f"name = '{name}' and mimeType = 'application/vnd.google-apps.folder' and trashed = false"
    if parent:
        q += f" and '{parent}' in parents"
    res = svc.files().list(q=q, fields="files(id)", supportsAllDrives=True,
                           includeItemsFromAllDrives=True).execute()
    if res.get("files"):
        return res["files"][0]["id"]
    body = {"name": name, "mimeType": "application/vnd.google-apps.folder"}
    if parent:
        body["parents"] = [parent]
    return svc.files().create(body=body, fields="id", supportsAllDrives=True).execute()["id"]


def upload(path: Path, user_token: str | None = None, folder_name: str = "ChipCollector",
           folder_id: str | None = None) -> tuple[str, str | None]:
    """Envia o arquivo; retorna (file_id, token_atualizado_ou_None)."""
    from googleapiclient.http import MediaFileUpload

    svc, creds = _service(user_token)
    parent = folder_id or (None if user_token else get_settings().drive_folder_id) or None
    parent = _ensure_folder(svc, folder_name, parent)
    media = MediaFileUpload(str(path), mimetype="application/zip", resumable=True, chunksize=8 * 1024 * 1024)
    req = svc.files().create(body={"name": path.name, "parents": [parent]}, media_body=media,
                             fields="id, webViewLink", supportsAllDrives=True)
    resp = None
    while resp is None:
        _, resp = req.next_chunk()
    new_token = creds.to_json() if user_token and hasattr(creds, "to_json") else None
    return resp["id"], new_token
