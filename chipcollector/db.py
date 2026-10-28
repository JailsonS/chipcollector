"""Modelos de dados (SQLModel/SQLAlchemy). SQLite por padrão, PostgreSQL via CHIPS_DATABASE_URL."""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Optional

from sqlalchemy import JSON, Column, UniqueConstraint, event
from sqlmodel import Field, Session, SQLModel, create_engine

from .config import get_settings


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


class User(SQLModel, table=True):
    id: Optional[int] = Field(default=None, primary_key=True)
    username: str = Field(index=True, unique=True)
    full_name: str = ""
    email: str = ""
    password_hash: str
    is_admin: bool = False
    is_active: bool = True
    drive_token: str = ""  # credenciais OAuth do Google Drive do próprio usuário (JSON)
    created_at: datetime = Field(default_factory=utcnow)


class Project(SQLModel, table=True):
    id: Optional[int] = Field(default=None, primary_key=True)
    name: str
    description: str = ""
    target: str = ""  # ex.: "extração madeireira", "desmatamento", "uso e cobertura"
    annotation_type: str = "classification"  # classification | segmentation
    # [{"id": 1, "name": "desmatamento", "color": "#e63946"}, ...]
    classes: list[dict[str, Any]] = Field(default_factory=list, sa_column=Column(JSON))
    multiclass: bool = False  # permite mais de uma classe por chip
    chip_size_px: int = 256
    resolution_m: float = 10.0
    annotators_per_chip: int = 1
    # Fontes de imagem — ver imagery/__init__.py
    image_sources: list[dict[str, Any]] = Field(default_factory=list, sa_column=Column(JSON))
    created_by: Optional[int] = Field(default=None, foreign_key="user.id")
    created_at: datetime = Field(default_factory=utcnow)


class ReferenceLayer(SQLModel, table=True):
    id: Optional[int] = Field(default=None, primary_key=True)
    project_id: int = Field(foreign_key="project.id", index=True)
    name: str
    theme: str = ""
    kind: str = "vector"  # vector | raster
    path: str
    class_field: str = ""  # campo do vetor usado para estratificar
    crs: str = ""
    n_features: int = 0
    # valores de estrato encontrados e sua contagem
    strata: dict[str, Any] = Field(default_factory=dict, sa_column=Column(JSON))
    bounds: list[float] = Field(default_factory=list, sa_column=Column(JSON))
    uploaded_by: Optional[int] = Field(default=None, foreign_key="user.id")
    created_at: datetime = Field(default_factory=utcnow)


class LocalRaster(SQLModel, table=True):
    """Mosaicos/imagens próprias enviadas pelo usuário (ex.: Planet NICFI baixado, drone)."""
    id: Optional[int] = Field(default=None, primary_key=True)
    project_id: int = Field(foreign_key="project.id", index=True)
    name: str
    path: str
    crs: str = ""
    count: int = 0
    bounds: list[float] = Field(default_factory=list, sa_column=Column(JSON))
    uploaded_by: Optional[int] = Field(default=None, foreign_key="user.id")
    created_at: datetime = Field(default_factory=utcnow)


class Chip(SQLModel, table=True):
    id: Optional[int] = Field(default=None, primary_key=True)
    project_id: int = Field(foreign_key="project.id", index=True)
    layer_id: Optional[int] = Field(default=None, foreign_key="referencelayer.id")
    stratum: str = ""
    lon: float
    lat: float
    epsg: int  # CRS métrico (UTM) do chip
    minx: float
    miny: float
    maxx: float
    maxy: float
    size_px: int
    resolution_m: float
    # geometrias de referência recortadas, em coordenadas do CRS do chip (para refinamento)
    reference: dict[str, Any] = Field(default_factory=dict, sa_column=Column(JSON))
    n_annotations: int = 0
    created_at: datetime = Field(default_factory=utcnow)


class ChipLock(SQLModel, table=True):
    __table_args__ = (UniqueConstraint("chip_id", "user_id"),)
    id: Optional[int] = Field(default=None, primary_key=True)
    chip_id: int = Field(foreign_key="chip.id", index=True)
    user_id: int = Field(foreign_key="user.id", index=True)
    locked_at: datetime = Field(default_factory=utcnow)


class Annotation(SQLModel, table=True):
    __table_args__ = (UniqueConstraint("chip_id", "user_id"),)
    id: Optional[int] = Field(default=None, primary_key=True)
    chip_id: int = Field(foreign_key="chip.id", index=True)
    project_id: int = Field(foreign_key="project.id", index=True)
    user_id: int = Field(foreign_key="user.id", index=True)
    source_id: str = ""  # fonte de imagem usada na interpretação
    status: str = "done"  # done | skip | uncertain
    # Classificação: lista de ids de classe presentes (vazia = ausência)
    labels: list[int] = Field(default_factory=list, sa_column=Column(JSON))
    # Segmentação: FeatureCollection em coordenadas do CRS do chip, properties.class_id
    geometry: dict[str, Any] = Field(default_factory=dict, sa_column=Column(JSON))
    comment: str = ""
    created_at: datetime = Field(default_factory=utcnow)
    updated_at: datetime = Field(default_factory=utcnow)


class ExportJob(SQLModel, table=True):
    id: Optional[int] = Field(default=None, primary_key=True)
    project_id: int = Field(foreign_key="project.id", index=True)
    user_id: int = Field(foreign_key="user.id")
    options: dict[str, Any] = Field(default_factory=dict, sa_column=Column(JSON))
    status: str = "pending"  # pending | running | done | error
    message: str = ""
    progress: float = 0.0
    path: str = ""
    drive_file_id: str = ""
    created_at: datetime = Field(default_factory=utcnow)


_engine = None


def get_engine():
    global _engine
    if _engine is None:
        url = get_settings().database_url
        kwargs: dict[str, Any] = {}
        if url.startswith("sqlite"):
            kwargs["connect_args"] = {"check_same_thread": False, "timeout": 30}
        _engine = create_engine(url, **kwargs)
        if url.startswith("sqlite"):
            @event.listens_for(_engine, "connect")
            def _sqlite_pragmas(dbapi_conn, _):  # pragma: no cover - simple config
                cur = dbapi_conn.cursor()
                cur.execute("PRAGMA journal_mode=WAL")  # leitores e escritor simultâneos
                cur.execute("PRAGMA foreign_keys=ON")
                cur.close()
        SQLModel.metadata.create_all(_engine)
    return _engine


def reset_engine() -> None:
    global _engine
    if _engine is not None:
        _engine.dispose()
    _engine = None


def get_session():
    with Session(get_engine()) as session:
        yield session
