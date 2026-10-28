"""Bases de imagem: sensores, períodos e provedores (STAC abertos, Google Earth Engine, raster local).

Uma *fonte de imagem* (image source) é um dicionário salvo no projeto:

    {
      "id": "s2-2024-seca",                 # identificador único no projeto
      "label": "Sentinel-2 · seca 2024",
      "provider": "planetary_computer",     # planetary_computer | earth_search | gee | local
      "sensor": "sentinel-2",               # chave de SENSORS (ou "custom" no GEE)
      "start": "2024-06-01", "end": "2024-09-30",
      "max_cloud": 20,                      # % de nuvens por cena (sensores ópticos)
      "composite": "median",                # median | least_cloudy
      "bands": ["B02","B03","B04","B08"],   # bandas exportadas (padrão: todas do sensor)
      "rgb": ["B04","B03","B02"],           # composição para visualização
      "raster_id": 3                        # apenas provider=local
    }
"""
from __future__ import annotations

import io
import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import numpy as np
import rasterio
from PIL import Image

from ..geo import ChipGrid

# Bandas com nomes "canônicos" por sensor; cada provedor mapeia para seus próprios nomes de asset/banda.
SENSORS: dict[str, dict[str, Any]] = {
    "sentinel-2": {
        "label": "Sentinel-2 MSI L2A (10–20 m)",
        "optical": True,
        "bands": ["B02", "B03", "B04", "B05", "B06", "B07", "B08", "B8A", "B11", "B12"],
        "rgb": ["B04", "B03", "B02"],
        "stretch": [0.0, 0.3],
        "since": "2017-03-28",
    },
    "landsat": {
        "label": "Landsat 8/9 OLI Collection 2 L2 (30 m)",
        "optical": True,
        "bands": ["blue", "green", "red", "nir08", "swir16", "swir22"],
        "rgb": ["red", "green", "blue"],
        "stretch": [0.0, 0.3],
        "since": "2013-04-11",
    },
    "landsat-legacy": {
        "label": "Landsat 5/7 TM/ETM+ Collection 2 L2 (30 m) — séries históricas",
        "optical": True,
        "bands": ["blue", "green", "red", "nir08", "swir16", "swir22"],
        "rgb": ["red", "green", "blue"],
        "stretch": [0.0, 0.3],
        "since": "1984-03-16",
    },
    "sentinel-1": {
        "label": "Sentinel-1 SAR (VV/VH, dB)",
        "optical": False,
        "bands": ["vv", "vh"],
        "rgb": ["vv", "vh", "vv"],
        "stretch": [-25.0, 0.0],
        "since": "2014-10-03",
    },
    "nicfi": {
        "label": "Planet NICFI mosaicos trópicos (4,77 m, mensal) — somente GEE",
        "optical": True,
        "bands": ["B", "G", "R", "N"],
        "rgb": ["R", "G", "B"],
        "stretch": [0.0, 0.25],
        "since": "2015-12-01",
    },
    "custom": {"label": "Coleção personalizada (GEE)", "optical": True, "bands": [], "rgb": [], "stretch": None},
    "local": {"label": "Raster local enviado pelo usuário", "optical": True, "bands": [], "rgb": [], "stretch": None},
}

PROVIDERS = {
    "planetary_computer": {"label": "Microsoft Planetary Computer (STAC, gratuito)",
                           "sensors": ["sentinel-2", "landsat", "landsat-legacy", "sentinel-1"]},
    "earth_search": {"label": "Element84 Earth Search / AWS Open Data (STAC, gratuito)",
                     "sensors": ["sentinel-2"]},
    "gee": {"label": "Google Earth Engine (gratuito p/ ONGs, pesquisa e uso não comercial)",
            "sensors": ["sentinel-2", "landsat", "landsat-legacy", "sentinel-1", "nicfi", "custom"]},
    "local": {"label": "Raster local (GeoTIFF/COG enviado)", "sensors": ["local"]},
}


class ImageryError(RuntimeError):
    pass


@dataclass
class ChipImage:
    data: np.ndarray  # float32 (bandas, H, W), NaN = sem dado
    band_names: list[str]
    meta: dict[str, Any] = field(default_factory=dict)


def normalize_source(src: dict[str, Any], default_res: float) -> dict[str, Any]:
    s = dict(src)
    s.setdefault("provider", "planetary_computer")
    s.setdefault("sensor", "local" if s["provider"] == "local" else "sentinel-2")
    if s["provider"] not in PROVIDERS:
        raise ImageryError(f"Provedor desconhecido: {s['provider']}")
    if s["sensor"] not in PROVIDERS[s["provider"]]["sensors"]:
        raise ImageryError(f"Sensor '{s['sensor']}' não disponível no provedor '{s['provider']}'.")
    spec = SENSORS[s["sensor"]]
    s["bands"] = list(s.get("bands") or spec["bands"])
    s["rgb"] = list(s.get("rgb") or spec["rgb"] or s["bands"][:3])
    if s.get("stretch") is None:
        s["stretch"] = spec.get("stretch")
    s.setdefault("max_cloud", 30)
    s.setdefault("composite", "median")
    # Todas as fontes são reamostradas para a grade do chip (resolução do projeto), garantindo
    # que anotações feitas sobre um sensor fiquem alinhadas a qualquer outro.
    s["resolution"] = float(default_res)
    s.setdefault("max_items", 8)
    if not s.get("id"):
        s["id"] = f"{s['sensor']}-{s.get('start', '')}-{s.get('end', '')}".strip("-")
    s.setdefault("label", s["id"])
    return s


def fetch(source: dict[str, Any], grid: ChipGrid, *, local_path: str | None = None) -> ChipImage:
    prov = source["provider"]
    if prov in ("planetary_computer", "earth_search"):
        from .stac import fetch_stac
        return fetch_stac(source, grid)
    if prov == "gee":
        from .gee import fetch_gee
        return fetch_gee(source, grid)
    if prov == "local":
        from .local import fetch_local
        if not local_path:
            raise ImageryError("Fonte local sem raster associado.")
        return fetch_local(source, grid, local_path)
    raise ImageryError(f"Provedor desconhecido: {prov}")


# ---------------------------------------------------------------- cache em disco

def write_geotiff(path: Path, img: ChipImage, grid: ChipGrid) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    profile = dict(driver="GTiff", width=grid.size_px, height=grid.size_px, count=img.data.shape[0],
                   dtype="float32", crs=grid.crs, transform=grid.transform, nodata=np.nan,
                   compress="deflate", predictor=3, tiled=grid.size_px >= 256,
                   blockxsize=min(256, grid.size_px), blockysize=min(256, grid.size_px))
    if not profile["tiled"]:
        profile.pop("blockxsize"), profile.pop("blockysize")
    with rasterio.open(path, "w", **profile) as dst:
        dst.write(img.data.astype("float32"))
        for i, name in enumerate(img.band_names, start=1):
            dst.set_band_description(i, name)
        dst.update_tags(chipcollector=json.dumps(img.meta, default=str))


def read_geotiff(path: Path) -> ChipImage:
    with rasterio.open(path) as src:
        data = src.read().astype("float32")
        names = [d or f"b{i}" for i, d in enumerate(src.descriptions, start=1)]
        meta = json.loads(src.tags().get("chipcollector", "{}"))
    return ChipImage(data, names, meta)


def get_chip_image(source: dict[str, Any], grid: ChipGrid, cache_path: Path, *,
                   local_path: str | None = None, refresh: bool = False) -> ChipImage:
    if cache_path.exists() and not refresh:
        return read_geotiff(cache_path)
    img = fetch(source, grid, local_path=local_path)
    if img.data.shape[1:] != (grid.size_px, grid.size_px):
        raise ImageryError(f"Imagem com dimensão inesperada {img.data.shape}")
    write_geotiff(cache_path, img, grid)
    return img


# ---------------------------------------------------------------- renderização

def render_png(img: ChipImage, rgb: list[str], stretch: list[float] | None = None) -> bytes:
    idx = []
    for b in rgb[:3]:
        if b in img.band_names:
            idx.append(img.band_names.index(b))
        elif str(b).isdigit() and 0 < int(b) <= len(img.band_names):
            idx.append(int(b) - 1)
    if not idx:
        idx = list(range(min(3, img.data.shape[0])))
    while len(idx) < 3:
        idx.append(idx[-1])
    arr = img.data[idx].astype("float32")
    valid = np.isfinite(arr).all(axis=0)
    out = np.zeros(arr.shape, dtype="float32")
    for i in range(3):
        band = arr[i]
        if stretch:
            lo, hi = stretch
        else:
            vals = band[np.isfinite(band)]
            lo, hi = (np.percentile(vals, [2, 98]) if vals.size else (0, 1))
        if hi <= lo:
            hi = lo + 1e-6
        out[i] = np.clip((band - lo) / (hi - lo), 0, 1)
    rgba = np.zeros((arr.shape[1], arr.shape[2], 4), dtype="uint8")
    rgba[..., :3] = (np.nan_to_num(np.moveaxis(out, 0, -1)) * 255).astype("uint8")
    rgba[..., 3] = np.where(valid, 255, 0)
    buf = io.BytesIO()
    Image.fromarray(rgba, "RGBA").save(buf, format="PNG", optimize=True)
    return buf.getvalue()


def catalog() -> dict[str, Any]:
    return {"sensors": SENSORS, "providers": PROVIDERS}
