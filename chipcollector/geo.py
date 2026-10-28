"""Utilidades geoespaciais: CRS UTM, grade do chip, conversões pixel<->mapa."""
from __future__ import annotations

import math
from dataclasses import dataclass
from functools import lru_cache

from pyproj import CRS, Transformer
from rasterio.transform import Affine, from_origin
from shapely.geometry import box, mapping, shape
import numpy as np
import shapely


def utm_epsg(lon: float, lat: float) -> int:
    zone = int(math.floor((lon + 180) / 6) % 60) + 1
    return (32600 if lat >= 0 else 32700) + zone


@lru_cache(maxsize=256)
def transformer(src: str | int, dst: str | int) -> Transformer:
    return Transformer.from_crs(CRS.from_user_input(src), CRS.from_user_input(dst), always_xy=True)


def reproject_geom(geom, src, dst):
    t = transformer(src, dst)
    return shapely.transform(geom, lambda c: np.column_stack(t.transform(c[:, 0], c[:, 1])))


@dataclass
class ChipGrid:
    epsg: int
    minx: float
    miny: float
    maxx: float
    maxy: float
    size_px: int
    resolution: float

    @property
    def transform(self) -> Affine:
        return from_origin(self.minx, self.maxy, self.resolution, self.resolution)

    @property
    def crs(self) -> str:
        return f"EPSG:{self.epsg}"

    def bbox_wgs84(self) -> tuple[float, float, float, float]:
        t = transformer(self.epsg, 4326)
        xs, ys = t.transform([self.minx, self.maxx, self.maxx, self.minx],
                             [self.miny, self.miny, self.maxy, self.maxy])
        return min(xs), min(ys), max(xs), max(ys)

    def footprint_wgs84(self):
        return reproject_geom(box(self.minx, self.miny, self.maxx, self.maxy), self.epsg, 4326)

    def to_pixel(self, geom):
        """Geometria no CRS do chip -> coordenadas de pixel (col, row) com origem no canto superior esquerdo."""
        r = self.resolution
        return shapely.transform(geom, lambda c: np.column_stack(((c[:, 0] - self.minx) / r, (self.maxy - c[:, 1]) / r)))

    def from_pixel(self, geom):
        r = self.resolution
        return shapely.transform(geom, lambda c: np.column_stack((self.minx + c[:, 0] * r, self.maxy - c[:, 1] * r)))


def chip_grid(lon: float, lat: float, size_px: int, resolution: float, epsg: int | None = None) -> ChipGrid:
    """Chip quadrado centrado em (lon, lat), alinhado a uma grade de `resolution` metros."""
    epsg = epsg or utm_epsg(lon, lat)
    x, y = transformer(4326, epsg).transform(lon, lat)
    half = size_px * resolution / 2
    minx = math.floor((x - half) / resolution) * resolution
    miny = math.floor((y - half) / resolution) * resolution
    maxx = minx + size_px * resolution
    maxy = miny + size_px * resolution
    return ChipGrid(epsg, minx, miny, maxx, maxy, size_px, resolution)


def grid_from_chip(chip) -> ChipGrid:
    return ChipGrid(chip.epsg, chip.minx, chip.miny, chip.maxx, chip.maxy, chip.size_px, chip.resolution_m)


def fc_transform(fc: dict, fn) -> dict:
    """Aplica `fn(shapely_geom) -> shapely_geom` a todas as feições de uma FeatureCollection."""
    feats = []
    for f in (fc or {}).get("features", []):
        if not f.get("geometry"):
            continue
        g = fn(shape(f["geometry"]))
        if g.is_empty:
            continue
        feats.append({"type": "Feature", "properties": dict(f.get("properties") or {}), "geometry": mapping(g)})
    return {"type": "FeatureCollection", "features": feats}
