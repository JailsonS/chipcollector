"""Geração de chips por amostragem estratificada a partir de camadas de referência."""
from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Any

import geopandas as gpd
import numpy as np
import rasterio
from rasterio import features as rfeatures
from rasterio.enums import Resampling
from rasterio.vrt import WarpedVRT
from shapely.geometry import Point, box, mapping, shape

from .geo import ChipGrid, chip_grid, reproject_geom, transformer
from .layers import is_raster, norm_value, read_vector

BACKGROUND = "__background__"


@dataclass
class SamplingParams:
    n_per_stratum: int = 50
    strata: list[str] = field(default_factory=list)  # vazio = todos
    class_field: str = ""
    class_map: dict[str, int] = field(default_factory=dict)  # estrato -> id de classe do projeto
    n_background: int = 0  # chips "negativos" fora das feições de referência
    min_spacing_px: int | None = None  # espaçamento mínimo entre centros (padrão = tamanho do chip)
    placement: str = "random"  # random | centroid
    seed: int = 42

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> "SamplingParams":
        keys = cls.__dataclass_fields__.keys()
        p = cls(**{k: v for k, v in d.items() if k in keys and v is not None})
        p.class_map = {str(k): int(v) for k, v in (p.class_map or {}).items() if v not in (None, "")}
        p.strata = [str(s) for s in p.strata]
        return p


@dataclass
class ChipSpec:
    stratum: str
    lon: float
    lat: float
    grid: ChipGrid
    reference: dict


class _Spacing:
    """Hash espacial simples em metros (Web Mercator aproximado) para manter distância mínima."""

    def __init__(self, min_dist_m: float):
        self.d = max(min_dist_m, 1e-9)
        self.cells: dict[tuple[int, int], list[tuple[float, float]]] = {}
        self.t = transformer(4326, 3857)

    def ok_and_add(self, lon: float, lat: float) -> bool:
        x, y = self.t.transform(lon, lat)
        k = math.cos(math.radians(lat))  # corrige escala do Mercator
        x, y = x * k, y * k
        cx, cy = int(x // self.d), int(y // self.d)
        for i in (-1, 0, 1):
            for j in (-1, 0, 1):
                for px, py in self.cells.get((cx + i, cy + j), ()):
                    if (px - x) ** 2 + (py - y) ** 2 < self.d ** 2:
                        return False
        self.cells.setdefault((cx, cy), []).append((x, y))
        return True


def _random_point_in(geom, rng: np.random.Generator, tries: int = 50):
    if geom.geom_type == "Point":
        return geom
    if geom.geom_type in ("LineString", "MultiLineString"):
        return geom.interpolate(rng.uniform(0, geom.length))
    minx, miny, maxx, maxy = geom.bounds
    for _ in range(tries):
        p = Point(rng.uniform(minx, maxx), rng.uniform(miny, maxy))
        if geom.contains(p):
            return p
    return geom.representative_point()


# ----------------------------------------------------------------------------- vetor

def clip_vector_reference(gdf: gpd.GeoDataFrame, grid: ChipGrid, class_field: str,
                          class_map: dict[str, int]) -> dict:
    fp = grid.footprint_wgs84()
    idx = list(gdf.sindex.query(fp, predicate="intersects"))
    feats = []
    if idx:
        sub = gdf.iloc[idx].to_crs(grid.epsg)
        cbox = box(grid.minx, grid.miny, grid.maxx, grid.maxy)
        for _, row in sub.iterrows():
            g = row.geometry.intersection(cbox)
            if g.is_empty:
                continue
            stratum = (norm_value(row[class_field]) or "") if class_field else "__all__"
            props = {"stratum": stratum, "class_id": class_map.get(stratum)}
            feats.append({"type": "Feature", "properties": props, "geometry": mapping(g)})
    return {"type": "FeatureCollection", "features": feats}


def _sample_vector(path: str, params: SamplingParams, size_px: int, res: float) -> list[ChipSpec]:
    rng = np.random.default_rng(params.seed)
    gdf = read_vector(path)
    cf = params.class_field
    gdf["_stratum"] = gdf[cf].map(norm_value) if cf else "__all__"
    sampleable = gdf[gdf["_stratum"].notna()]  # feições sem classe não são sorteadas, mas contam como referência
    strata = params.strata or sorted(sampleable["_stratum"].unique().tolist())
    spacing = _Spacing((params.min_spacing_px or size_px) * res)
    out: list[ChipSpec] = []

    def add(stratum: str, p) -> bool:
        if not spacing.ok_and_add(p.x, p.y):
            return False
        grid = chip_grid(p.x, p.y, size_px, res)
        ref = clip_vector_reference(gdf, grid, cf, params.class_map)
        out.append(ChipSpec(stratum, p.x, p.y, grid, ref))
        return True

    for s in strata:
        sub = sampleable[sampleable["_stratum"] == s]
        if sub.empty:
            continue
        n_target = params.n_per_stratum
        order = rng.permutation(len(sub))
        got = 0
        max_attempts = len(sub) if params.placement == "centroid" else max(n_target * 20, len(sub))
        for attempt in range(max_attempts):
            if got >= n_target:
                break
            geom = sub.geometry.iloc[order[attempt % len(sub)]]
            p = geom.representative_point() if params.placement == "centroid" else _random_point_in(geom, rng)
            if add(s, p):
                got += 1

    if params.n_background > 0:
        union_idx = gdf.sindex
        minx, miny, maxx, maxy = gdf.total_bounds
        pad = size_px * res / 111_320
        got, attempts = 0, 0
        while got < params.n_background and attempts < params.n_background * 200:
            attempts += 1
            p = Point(rng.uniform(minx - pad, maxx + pad), rng.uniform(miny - pad, maxy + pad))
            grid = chip_grid(p.x, p.y, size_px, res)
            if len(union_idx.query(grid.footprint_wgs84(), predicate="intersects")):
                continue  # chip toca alguma feição -> não é fundo puro
            if add(BACKGROUND, p):
                got += 1
    return out


# ----------------------------------------------------------------------------- raster

def clip_raster_reference(path: str, grid: ChipGrid, class_map: dict[str, int],
                          strata: list[str] | None = None) -> dict:
    with rasterio.open(path) as src, WarpedVRT(
        src, crs=grid.crs, transform=grid.transform, width=grid.size_px, height=grid.size_px,
        resampling=Resampling.nearest,
    ) as vrt:
        arr = vrt.read(1)
        nodata = src.nodata
    mask = np.ones(arr.shape, bool) if nodata is None else arr != nodata
    if strata:
        mask &= np.isin(arr.astype(str), strata)
    feats = []
    for geom, val in rfeatures.shapes(arr.astype("int32"), mask=mask, transform=grid.transform):
        s = str(int(val))
        feats.append({"type": "Feature", "properties": {"stratum": s, "class_id": class_map.get(s)},
                      "geometry": geom})
    return {"type": "FeatureCollection", "features": feats}


def _sample_raster(path: str, params: SamplingParams, size_px: int, res: float,
                   max_pixels: int = 16_000_000) -> list[ChipSpec]:
    rng = np.random.default_rng(params.seed)
    with rasterio.open(path) as src:
        scale = max(1.0, float(np.sqrt(src.width * src.height / max_pixels)))
        h, w = max(1, int(src.height / scale)), max(1, int(src.width / scale))
        arr = src.read(1, out_shape=(h, w), resampling=Resampling.nearest)
        tr = src.transform @ src.transform.scale(src.width / w, src.height / h)
        nodata, crs = src.nodata, src.crs
    to_ll = transformer(crs.to_string(), 4326)
    valid = np.ones(arr.shape, bool) if nodata is None else arr != nodata
    strata = params.strata or [str(int(v)) for v in np.unique(arr[valid])]
    spacing = _Spacing((params.min_spacing_px or size_px) * res)
    out: list[ChipSpec] = []
    selected = [s for s in strata]

    def take(stratum: str, rows: np.ndarray, cols: np.ndarray, n: int):
        if len(rows) == 0:
            return
        order = rng.permutation(len(rows))
        got = 0
        for i in order[: n * 50]:
            x, y = tr @ (cols[i] + rng.uniform(0, 1), rows[i] + rng.uniform(0, 1))
            lon, lat = to_ll.transform(x, y)
            if not spacing.ok_and_add(lon, lat):
                continue
            grid = chip_grid(lon, lat, size_px, res)
            ref = clip_raster_reference(path, grid, params.class_map, selected)
            out.append(ChipSpec(stratum, lon, lat, grid, ref))
            got += 1
            if got >= n:
                break

    for s in strata:
        rows, cols = np.nonzero(valid & (arr == int(s)))
        take(s, rows, cols, params.n_per_stratum)
    if params.n_background > 0:
        rows, cols = np.nonzero(valid & ~np.isin(arr, [int(s) for s in strata]))
        take(BACKGROUND, rows, cols, params.n_background)
    return out


def sample_layer(path: str, params: SamplingParams, size_px: int, res: float) -> list[ChipSpec]:
    if is_raster(path):
        return _sample_raster(path, params, size_px, res)
    return _sample_vector(path, params, size_px, res)


def samples_to_chips(path: str, stratum_field: str, class_field: str, size_px: int, res: float,
                     strata: list[str] | None = None, class_map: dict[str, int] | None = None,
                     skip: set[int] | None = None) -> list[ChipSpec]:
    """Amostra estratificada fora do sistema: cada ponto ou polígono vira um chip centrado nele.

    `class_map` liga valores do campo de classe às classes do projeto (pré-rotulagem).
    `skip` são índices de feições que já viraram chip (para não duplicar ao gerar de novo).
    A referência do chip guarda a própria amostra (recortada ao chip) e, em `sample`, o índice da
    feição no arquivo, o estrato, a classe de referência e os atributos originais.
    """
    from .layers import norm_value, read_vector_raw

    class_map = class_map or {}
    skip = skip or set()
    wanted = set(strata or [])
    raw = read_vector_raw(path)
    out: list[ChipSpec] = []
    attrs = [c for c in raw.columns if c != "geometry"][:30]
    for idx, row in raw.iterrows():
        g = row.geometry
        if g is None or g.is_empty or g.geom_type not in ("Point", "MultiPoint", "Polygon", "MultiPolygon"):
            continue
        st = norm_value(row[stratum_field])
        if st is None or (wanted and st not in wanted) or int(idx) in skip:
            continue
        cv = norm_value(row[class_field]) if class_field else None
        if not g.is_valid:
            from shapely.validation import make_valid
            g = make_valid(g)
        c = g if g.geom_type == "Point" else g.representative_point()
        grid = chip_grid(c.x, c.y, size_px, res)
        local = reproject_geom(g, 4326, grid.epsg).intersection(box(grid.minx, grid.miny, grid.maxx, grid.maxy))
        cid = class_map.get(cv) if cv is not None else None
        feats = []
        if not local.is_empty:
            feats.append({"type": "Feature", "geometry": mapping(local),
                          "properties": {"stratum": st, "class_value": cv, "class_id": cid, "sample": True}})
        props = {k: norm_value(row[k]) for k in attrs}
        ref = {"type": "FeatureCollection", "features": feats,
               "sample": {"index": int(idx), "stratum": st, "class_value": cv, "class_id": cid,
                          "geometry_type": g.geom_type, "attributes": props}}
        out.append(ChipSpec(st, float(c.x), float(c.y), grid, ref))
    return out


def manual_chip(lon: float, lat: float, size_px: int, res: float, layer_path: str | None,
                class_field: str = "", class_map: dict[str, int] | None = None) -> ChipSpec:
    """Chip criado por clique do usuário no mapa."""
    grid = chip_grid(lon, lat, size_px, res)
    ref: dict = {"type": "FeatureCollection", "features": []}
    if layer_path:
        if is_raster(layer_path):
            ref = clip_raster_reference(layer_path, grid, class_map or {})
        else:
            ref = clip_vector_reference(read_vector(layer_path), grid, class_field, class_map or {})
    return ChipSpec("__manual__", lon, lat, grid, ref)


def reference_to_pixels(reference: dict, grid: ChipGrid) -> dict:
    feats = []
    for f in reference.get("features", []):
        g = grid.to_pixel(shape(f["geometry"]))
        feats.append({"type": "Feature", "properties": f.get("properties", {}), "geometry": mapping(g)})
    out = {"type": "FeatureCollection", "features": feats}
    if reference.get("sample"):
        out["sample"] = reference["sample"]
    return out


__all__ = ["SamplingParams", "ChipSpec", "sample_layer", "samples_to_chips", "manual_chip", "reference_to_pixels",
           "BACKGROUND"]
