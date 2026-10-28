"""Raster local (GeoTIFF/COG/VRT enviado pelo usuário: mosaico NICFI baixado, drone, CBERS, etc.)."""
from __future__ import annotations

from typing import Any

import numpy as np
import rasterio
from rasterio.enums import Resampling
from rasterio.vrt import WarpedVRT

from ..geo import ChipGrid
from . import ChipImage, ImageryError


def raster_band_names(path: str) -> list[str]:
    with rasterio.open(path) as src:
        return [d or str(i) for i, d in enumerate(src.descriptions, start=1)]


def fetch_local(source: dict[str, Any], grid: ChipGrid, path: str) -> ChipImage:
    names = raster_band_names(path)
    wanted = source.get("bands") or names
    idx = []
    for b in wanted:
        if b in names:
            idx.append(names.index(b) + 1)
        elif str(b).isdigit() and 0 < int(b) <= len(names):
            idx.append(int(b))
        else:
            raise ImageryError(f"Banda '{b}' não encontrada no raster ({names}).")
    with rasterio.open(path) as src, WarpedVRT(
        src, crs=grid.crs, transform=grid.transform, width=grid.size_px, height=grid.size_px,
        resampling=Resampling.bilinear,
    ) as vrt:
        arr = vrt.read(idx, masked=True).astype("float32")
    data = arr.filled(np.nan) if np.ma.isMaskedArray(arr) else np.asarray(arr)
    scale = float(source.get("scale", 1.0))
    offset = float(source.get("offset", 0.0))
    data = data * scale + offset
    band_names = [names[i - 1] for i in idx]
    return ChipImage(data.astype("float32"), band_names,
                     {"provider": "local", "path_name": path.split("/")[-1],
                      "valid_fraction": float(np.isfinite(data).all(axis=0).mean())})
