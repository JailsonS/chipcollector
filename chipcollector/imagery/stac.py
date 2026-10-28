"""Leitura de imagens via catálogos STAC abertos (Planetary Computer e Earth Search).

Cada asset COG é lido diretamente na grade do chip (CRS/transform/tamanho) com WarpedVRT,
as nuvens são mascaradas (SCL no Sentinel-2, QA_PIXEL no Landsat) e as cenas são compostas
por mediana ou pela cena menos nublada (preenchendo lacunas com as seguintes).
"""
from __future__ import annotations

import os
from concurrent.futures import ThreadPoolExecutor
from typing import Any

import numpy as np
import rasterio
from rasterio.enums import Resampling
from rasterio.vrt import WarpedVRT

from ..geo import ChipGrid
from . import SENSORS, ChipImage, ImageryError

ENDPOINTS = {
    "planetary_computer": "https://planetarycomputer.microsoft.com/api/stac/v1",
    "earth_search": "https://earth-search.aws.element84.com/v1",
}

# (coleção, mapa banda canônica -> asset, asset de máscara, filtro extra)
COLLECTIONS: dict[tuple[str, str], dict[str, Any]] = {
    ("planetary_computer", "sentinel-2"): {
        "collection": "sentinel-2-l2a",
        "assets": {b: b for b in SENSORS["sentinel-2"]["bands"]},
        "mask": "SCL",
    },
    ("earth_search", "sentinel-2"): {
        "collection": "sentinel-2-l2a",
        "assets": {"B02": "blue", "B03": "green", "B04": "red", "B05": "rededge1", "B06": "rededge2",
                   "B07": "rededge3", "B08": "nir", "B8A": "nir08", "B11": "swir16", "B12": "swir22"},
        "mask": "scl",
    },
    ("planetary_computer", "landsat"): {
        "collection": "landsat-c2-l2",
        "assets": {b: b for b in SENSORS["landsat"]["bands"]},
        "mask": "qa_pixel",
        "query": {"platform": {"in": ["landsat-8", "landsat-9"]}},
    },
    ("planetary_computer", "landsat-legacy"): {
        "collection": "landsat-c2-l2",
        "assets": {b: b for b in SENSORS["landsat-legacy"]["bands"]},
        "mask": "qa_pixel",
        "query": {"platform": {"in": ["landsat-5", "landsat-7"]}},
    },
    ("planetary_computer", "sentinel-1"): {
        "collection": "sentinel-1-rtc",
        "assets": {"vv": "vv", "vh": "vh"},
        "mask": None,
        "db": True,
    },
}

GDAL_ENV = dict(
    GDAL_DISABLE_READDIR_ON_OPEN="EMPTY_DIR",
    CPL_VSIL_CURL_ALLOWED_EXTENSIONS=".tif,.TIF,.tiff,.jp2",
    GDAL_HTTP_MULTIRANGE="YES",
    GDAL_HTTP_MERGE_CONSECUTIVE_RANGES="YES",
    GDAL_HTTP_MAX_RETRY="4",
    GDAL_HTTP_RETRY_DELAY="1",
    VSI_CACHE="TRUE",
    AWS_NO_SIGN_REQUEST="YES",
)
# usa o bundle de CAs do sistema/proxy, se configurado
if os.getenv("REQUESTS_CA_BUNDLE") or os.getenv("SSL_CERT_FILE"):
    GDAL_ENV["CURL_CA_BUNDLE"] = os.getenv("REQUESTS_CA_BUNDLE") or os.getenv("SSL_CERT_FILE")


def _client(provider: str):
    import pystac_client

    if provider == "planetary_computer":
        import planetary_computer

        return pystac_client.Client.open(ENDPOINTS[provider], modifier=planetary_computer.sign_inplace)
    return pystac_client.Client.open(ENDPOINTS[provider])


def search_items(source: dict[str, Any], grid: ChipGrid) -> list:
    key = (source["provider"], source["sensor"])
    if key not in COLLECTIONS:
        raise ImageryError(f"Sensor {source['sensor']} indisponível em {source['provider']}.")
    cfg = COLLECTIONS[key]
    query = dict(cfg.get("query", {}))
    if SENSORS[source["sensor"]]["optical"] and source.get("max_cloud") is not None:
        query["eo:cloud_cover"] = {"lt": float(source["max_cloud"])}
    search = _client(source["provider"]).search(
        collections=[cfg["collection"]],
        bbox=list(grid.bbox_wgs84()),
        datetime=f"{source['start']}/{source['end']}",
        query=query or None,
        max_items=200,
    )
    items = list(search.items())
    items.sort(key=lambda it: it.properties.get("eo:cloud_cover", 0) or 0)
    return items[: int(source.get("max_items", 8))]


def _read_asset(href: str, grid: ChipGrid, resampling: Resampling) -> np.ndarray:
    with rasterio.Env(**GDAL_ENV):
        with rasterio.open(href) as src, WarpedVRT(
            src, crs=grid.crs, transform=grid.transform, width=grid.size_px, height=grid.size_px,
            resampling=resampling, src_nodata=src.nodata, nodata=src.nodata if src.nodata is not None else 0,
        ) as vrt:
            arr = vrt.read(1, masked=True)
    return arr.filled(np.nan).astype("float32") if np.ma.isMaskedArray(arr) else arr.astype("float32")


def _scale_offset(item, asset_key: str, sensor: str) -> tuple[float, float]:
    asset = item.assets[asset_key]
    rb = (asset.extra_fields.get("raster:bands") or [{}])[0]
    scale, offset = rb.get("scale"), rb.get("offset")
    if sensor == "sentinel-2":
        if scale is None:
            scale = 1e-4
        if offset is None:
            # baseline >= 04.00 adiciona +1000 ao DN (BOA_ADD_OFFSET)
            pb = str(item.properties.get("s2:processing_baseline", "00.00"))
            offset = -0.1 if pb >= "04.00" else 0.0
    if sensor.startswith("landsat"):
        scale = 2.75e-5 if scale is None else scale
        offset = -0.2 if offset is None else offset
    return float(scale or 1.0), float(offset or 0.0)


def _cloud_mask(sensor: str, mask_arr: np.ndarray) -> np.ndarray:
    """True = pixel válido."""
    m = np.nan_to_num(mask_arr, nan=0).astype("int64")
    if sensor == "sentinel-2":
        return np.isin(m, [2, 4, 5, 6, 7, 11])  # remove sem dado, saturado, sombra, nuvens, cirrus
    if sensor.startswith("landsat"):
        bad = (1 << 0) | (1 << 1) | (1 << 2) | (1 << 3) | (1 << 4)  # fill, dilated, cirrus, cloud, shadow
        return (m & bad) == 0
    return np.ones(m.shape, bool)


def _load_item(item, cfg: dict, source: dict, grid: ChipGrid) -> np.ndarray:
    sensor = source["sensor"]
    bands = source["bands"]
    out = np.full((len(bands), grid.size_px, grid.size_px), np.nan, dtype="float32")
    for i, b in enumerate(bands):
        akey = cfg["assets"].get(b, b)
        if akey not in item.assets:
            continue
        arr = _read_asset(item.assets[akey].href, grid, Resampling.bilinear)
        scale, offset = _scale_offset(item, akey, sensor)
        if not cfg.get("db"):
            arr = np.where(arr == 0, np.nan, arr * scale + offset)
        out[i] = arr
    if cfg.get("mask") and cfg["mask"] in item.assets:
        valid = _cloud_mask(sensor, _read_asset(item.assets[cfg["mask"]].href, grid, Resampling.nearest))
        out[:, ~valid] = np.nan
    return out


def fetch_stac(source: dict[str, Any], grid: ChipGrid) -> ChipImage:
    cfg = COLLECTIONS[(source["provider"], source["sensor"])]
    items = search_items(source, grid)
    if not items:
        raise ImageryError("Nenhuma cena encontrada para o período/nuvens escolhidos. Amplie o período ou o limite de nuvens.")
    with ThreadPoolExecutor(max_workers=min(4, len(items))) as ex:
        stack = list(ex.map(lambda it: _load_item(it, cfg, source, grid), items))
    cube = np.stack(stack)  # (cenas, bandas, H, W)
    with np.errstate(all="ignore"):
        if source.get("composite") == "least_cloudy":
            comp = cube[0].copy()
            for k in range(1, len(cube)):
                gap = np.isnan(comp)
                comp[gap] = cube[k][gap]
        else:
            import warnings

            with warnings.catch_warnings():
                warnings.simplefilter("ignore", RuntimeWarning)
                comp = np.nanmedian(cube, axis=0)
        if cfg.get("db"):
            comp = 10 * np.log10(np.where(comp > 0, comp, np.nan))
    meta = {
        "provider": source["provider"], "sensor": source["sensor"], "collection": cfg["collection"],
        "composite": source.get("composite", "median"), "start": source["start"], "end": source["end"],
        "items": [it.id for it in items],
        "dates": sorted({str(it.datetime.date()) for it in items if it.datetime}),
        "valid_fraction": float(np.isfinite(comp).all(axis=0).mean()),
    }
    return ChipImage(comp.astype("float32"), list(source["bands"]), meta)
