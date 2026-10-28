"""Carregamento de camadas de referência (vetoriais e matriciais) e inspeção de estratos."""
from __future__ import annotations

import shutil
import uuid
import zipfile
from pathlib import Path
from typing import Iterable

import geopandas as gpd
import numpy as np
import rasterio
from rasterio.enums import Resampling
from shapely.validation import make_valid

VECTOR_EXT = {".gpkg", ".geojson", ".json", ".shp", ".kml", ".fgb", ".parquet", ".geoparquet"}
RASTER_EXT = {".tif", ".tiff", ".vrt", ".img", ".jp2"}
SIDE_CAR = {".shx", ".dbf", ".prj", ".cpg", ".qix", ".sbn", ".sbx", ".xml", ".aux", ".ovr", ".tfw"}


class LayerError(ValueError):
    pass


def store_upload(files: Iterable[tuple[str, bytes]], dest_root: Path) -> Path:
    """Grava os arquivos enviados num diretório próprio e devolve o arquivo principal.

    Aceita: GeoPackage, GeoJSON, KML, FlatGeobuf, GeoParquet, Shapefile (.zip ou .shp+.dbf+.shx+.prj),
    GeoTIFF/COG, ou um .zip com qualquer um destes.
    """
    dest = dest_root / uuid.uuid4().hex[:12]
    dest.mkdir(parents=True)
    for name, data in files:
        safe = Path(name).name
        if not safe:
            continue
        (dest / safe).write_bytes(data)
    for z in list(dest.glob("*.zip")):
        with zipfile.ZipFile(z) as zf:
            for member in zf.infolist():
                target = (dest / member.filename).resolve()
                if not str(target).startswith(str(dest.resolve())):
                    raise LayerError("Arquivo zip com caminho inválido.")
            zf.extractall(dest)
        z.unlink()
    candidates = [p for p in dest.rglob("*") if p.is_file() and p.suffix.lower() in VECTOR_EXT | RASTER_EXT]
    if not candidates:
        shutil.rmtree(dest, ignore_errors=True)
        raise LayerError("Nenhum arquivo geoespacial reconhecido (use GPKG, GeoJSON, SHP, KML, FGB, Parquet ou GeoTIFF).")
    # prioriza vetores, depois rasters
    candidates.sort(key=lambda p: (p.suffix.lower() not in VECTOR_EXT, len(str(p))))
    return candidates[0]


def is_raster(path: Path | str) -> bool:
    return Path(path).suffix.lower() in RASTER_EXT


def read_vector(path: Path | str) -> gpd.GeoDataFrame:
    p = Path(path)
    if p.suffix.lower() in {".parquet", ".geoparquet"}:
        gdf = gpd.read_parquet(p)
    else:
        gdf = gpd.read_file(p)
    if gdf.crs is None:
        gdf = gdf.set_crs(4326)  # suposição usual para GeoJSON/KML
    gdf = gdf[gdf.geometry.notna() & ~gdf.geometry.is_empty].copy()
    gdf["geometry"] = gdf.geometry.apply(lambda g: g if g.is_valid else make_valid(g))
    gdf = gdf.to_crs(4326)
    gdf = gdf.reset_index(drop=True)
    return gdf


def inspect_vector(path: Path | str, class_field: str = "") -> dict:
    gdf = read_vector(path)
    fields = [c for c in gdf.columns if c != "geometry"]
    strata: dict[str, int] = {}
    if class_field:
        if class_field not in gdf.columns:
            raise LayerError(f"Campo '{class_field}' não existe. Campos: {', '.join(fields)}")
        vc = gdf[class_field].astype(str).value_counts()
        strata = {str(k): int(v) for k, v in vc.items()}
    else:
        strata = {"__all__": int(len(gdf))}
    return {
        "kind": "vector",
        "crs": "EPSG:4326",
        "n_features": int(len(gdf)),
        "fields": fields,
        "geom_types": sorted(set(gdf.geom_type.astype(str))),
        "strata": strata,
        "bounds": [float(v) for v in gdf.total_bounds],
    }


def inspect_raster(path: Path | str, max_pixels: int = 4_000_000) -> dict:
    """Para rasters categóricos (ex.: MapBiomas, PRODES): conta valores numa versão reamostrada."""
    with rasterio.open(path) as src:
        scale = max(1.0, float(np.sqrt(src.width * src.height / max_pixels)))
        h, w = max(1, int(src.height / scale)), max(1, int(src.width / scale))
        arr = src.read(1, out_shape=(h, w), resampling=Resampling.nearest)
        nodata = src.nodata
        from rasterio.warp import transform_bounds
        b = transform_bounds(src.crs, "EPSG:4326", *src.bounds) if src.crs else src.bounds
        info = {"kind": "raster", "crs": src.crs.to_string() if src.crs else "", "count": src.count,
                "dtype": src.dtypes[0], "width": src.width, "height": src.height,
                "bounds": [float(v) for v in b], "fields": []}
    mask = np.ones(arr.shape, bool) if nodata is None else arr != nodata
    is_int = np.issubdtype(arr.dtype, np.integer)
    if is_int:
        vals, counts = np.unique(arr[mask], return_counts=True)
        if len(vals) > 512:
            raise LayerError("Raster com mais de 512 valores distintos — use um raster categórico (classes inteiras).")
        info["strata"] = {str(int(v)): int(c * scale * scale) for v, c in zip(vals, counts)}
    else:
        raise LayerError("Raster de referência deve ser categórico (valores inteiros por classe).")
    info["n_features"] = int(mask.sum() * scale * scale)
    return info


def inspect_layer(path: Path | str, class_field: str = "") -> dict:
    return inspect_raster(path) if is_raster(path) else inspect_vector(path, class_field)
