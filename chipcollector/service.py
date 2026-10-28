"""Funções de serviço compartilhadas entre API, exportação e CLI."""
from __future__ import annotations

import re
from pathlib import Path

from fastapi import HTTPException
from sqlmodel import Session

from .config import get_settings
from .db import Chip, LocalRaster, Project
from .geo import grid_from_chip
from .imagery import ChipImage, get_chip_image, normalize_source


def safe_id(s: str) -> str:
    import unicodedata
    s = unicodedata.normalize("NFKD", str(s)).encode("ascii", "ignore").decode()
    return re.sub(r"[^A-Za-z0-9_.-]+", "_", s)[:80] or "fonte"


def get_source(project: Project, source_id: str | None) -> dict:
    sources = project.image_sources or []
    if not sources:
        raise HTTPException(400, "O projeto não tem nenhuma base de imagem configurada.")
    if not source_id:
        return normalize_source(sources[0], project.resolution_m)
    for s in sources:
        if s.get("id") == source_id:
            return normalize_source(s, project.resolution_m)
    raise HTTPException(404, f"Fonte de imagem '{source_id}' não encontrada.")


def local_path_for(session: Session, project: Project, source: dict) -> str | None:
    if source.get("provider") != "local":
        return None
    r = session.get(LocalRaster, int(source.get("raster_id") or 0))
    if not r or r.project_id != project.id:
        raise HTTPException(400, "Raster local da fonte não encontrado.")
    return r.path


def cache_path(project: Project, chip: Chip, source: dict) -> Path:
    return get_settings().data_dir / "chips" / str(project.id) / str(chip.id) / f"{safe_id(source['id'])}.tif"


def chip_image(session: Session, project: Project, chip: Chip, source: dict, refresh: bool = False) -> ChipImage:
    return get_chip_image(source, grid_from_chip(chip), cache_path(project, chip, source),
                          local_path=local_path_for(session, project, source), refresh=refresh)
