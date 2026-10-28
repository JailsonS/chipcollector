"""Configuração via variáveis de ambiente (prefixo CHIPS_)."""
from __future__ import annotations

import os
import secrets
from dataclasses import dataclass, field
from pathlib import Path


def _bool(name: str, default: bool) -> bool:
    v = os.getenv(name)
    if v is None:
        return default
    return v.strip().lower() in {"1", "true", "yes", "on", "sim"}


@dataclass
class Settings:
    data_dir: Path = field(default_factory=lambda: Path(os.getenv("CHIPS_DATA_DIR", "./data")).resolve())
    database_url: str = field(default_factory=lambda: os.getenv("CHIPS_DATABASE_URL", ""))
    secret_key: str = field(default_factory=lambda: os.getenv("CHIPS_SECRET_KEY", ""))
    allow_signup: bool = field(default_factory=lambda: _bool("CHIPS_ALLOW_SIGNUP", True))
    # Tempo (min) que um chip fica reservado para um usuário antes de voltar à fila
    lock_minutes: int = field(default_factory=lambda: int(os.getenv("CHIPS_LOCK_MINUTES", "20")))
    # Google Earth Engine
    gee_project: str = field(default_factory=lambda: os.getenv("CHIPS_GEE_PROJECT", ""))
    gee_service_account: str = field(default_factory=lambda: os.getenv("CHIPS_GEE_SERVICE_ACCOUNT", ""))
    gee_key_file: str = field(default_factory=lambda: os.getenv("CHIPS_GEE_KEY_FILE", ""))
    # Google Drive
    # OAuth (cada usuário conecta o próprio Drive): JSON de cliente "Web application" do Google Cloud
    drive_client_secrets: str = field(default_factory=lambda: os.getenv("CHIPS_DRIVE_CLIENT_SECRETS", ""))
    # Alternativa: conta de serviço com acesso a um Drive compartilhado (Shared Drive)
    drive_service_account: str = field(default_factory=lambda: os.getenv("CHIPS_DRIVE_SERVICE_ACCOUNT", ""))
    # URL pública da aplicação (para o redirect OAuth); ex.: https://amostras.minhaong.org
    public_url: str = field(default_factory=lambda: os.getenv("CHIPS_PUBLIC_URL", "http://localhost:8000"))
    drive_folder_id: str = field(default_factory=lambda: os.getenv("CHIPS_DRIVE_FOLDER_ID", ""))

    def __post_init__(self) -> None:
        self.data_dir.mkdir(parents=True, exist_ok=True)
        for sub in ("layers", "chips", "exports", "rasters"):
            (self.data_dir / sub).mkdir(exist_ok=True)
        if not self.database_url:
            self.database_url = f"sqlite:///{self.data_dir / 'chipcollector.db'}"
        if not self.secret_key:
            key_file = self.data_dir / ".secret_key"
            if key_file.exists():
                self.secret_key = key_file.read_text().strip()
            else:
                self.secret_key = secrets.token_urlsafe(48)
                key_file.write_text(self.secret_key)


_settings: Settings | None = None


def get_settings() -> Settings:
    global _settings
    if _settings is None:
        _settings = Settings()
    return _settings


def reset_settings() -> None:
    """Usado nos testes."""
    global _settings
    _settings = None
