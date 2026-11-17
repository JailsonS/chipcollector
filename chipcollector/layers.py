"""Carregamento de camadas de referência (vetoriais e matriciais) e inspeção de estratos."""
from __future__ import annotations

import re
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


def read_vector_raw(path: Path | str) -> gpd.GeoDataFrame:
    """Leitura sem limpeza (para a verificação contar geometrias vazias), em WGS84."""
    p = Path(path)
    gdf = gpd.read_parquet(p) if p.suffix.lower() in {".parquet", ".geoparquet"} else gpd.read_file(p)
    if gdf.crs is None:
        gdf = gdf.set_crs(4326)
    return gdf.to_crs(4326).reset_index(drop=True)


def norm_value(v) -> str | None:
    """Valor de atributo como texto estável: vazio/nulo -> None, 3.0 -> "3"."""
    if v is None:
        return None
    try:
        if isinstance(v, float) and np.isnan(v):
            return None
    except TypeError:
        pass
    if isinstance(v, (float, np.floating)) and float(v).is_integer():
        return str(int(v))
    t = str(v).strip()
    return t or None


def value_series(gdf: gpd.GeoDataFrame, field: str):
    if field not in gdf.columns:
        raise LayerError(f"Campo '{field}' não existe. Campos: {', '.join(c for c in gdf.columns if c != 'geometry')}")
    return gdf[field].map(norm_value)


def count_values(gdf: gpd.GeoDataFrame, field: str) -> dict[str, int]:
    vc = value_series(gdf, field).dropna().value_counts()
    return {str(k): int(v) for k, v in vc.items()}


_STRATUM_HINT = re.compile(r"estrat|strat", re.I)
_CLASS_HINT = re.compile(r"^(classe?|class(_?name)?|label|rotulo|rótulo|categoria|category|tipo|ref.*)$", re.I)


def field_summary(gdf: gpd.GeoDataFrame, max_examples: int = 6) -> list[dict]:
    out = []
    for c in gdf.columns:
        if c == "geometry":
            continue
        vals = gdf[c].map(norm_value)
        uniq = vals.dropna().unique()
        out.append({"name": c, "dtype": str(gdf[c].dtype), "n_unique": int(len(uniq)),
                    "n_empty": int(vals.isna().sum()), "examples": [str(x) for x in list(uniq[:max_examples])]})
    return out


def suggest_config(gdf: gpd.GeoDataFrame) -> dict:
    """Sugere se a camada já é uma amostra estratificada e quais campos usar (o admin confirma)."""
    fields = [c for c in gdf.columns if c != "geometry"]
    gtypes = set(gdf.geom_type.dropna().astype(str))
    stratum = next((f for f in fields if _STRATUM_HINT.search(f)), "")
    klass = next((f for f in fields if _CLASS_HINT.match(f) and f != stratum), "")
    only_points = bool(gtypes) and gtypes <= {"Point", "MultiPoint"}
    role = "samples" if (stratum or only_points) else "reference"
    reasons = []
    if stratum:
        reasons.append(f"campo '{stratum}' parece indicar o estrato")
    if only_points:
        reasons.append("todas as feições são pontos")
    return {"role": role, "stratum_field": stratum, "class_field": klass, "reasons": reasons}


def inspect_vector(path: Path | str, class_field: str = "", role: str = "reference", stratum_field: str = "") -> dict:
    gdf = read_vector(path)
    group = stratum_field if role == "samples" else class_field
    strata = count_values(gdf, group) if group else {"__all__": int(len(gdf))}
    return {
        "kind": "vector",
        "crs": "EPSG:4326",
        "n_features": int(len(gdf)),
        "fields": [c for c in gdf.columns if c != "geometry"],
        "field_info": field_summary(gdf),
        "geom_types": sorted(set(gdf.geom_type.astype(str))),
        "strata": strata,
        "bounds": [float(v) for v in gdf.total_bounds],
        "suggestion": suggest_config(gdf),
    }


def check_samples(path: Path | str, stratum_field: str, class_field: str = "",
                  chip_size_px: int = 256, resolution_m: float = 10.0) -> dict:
    """Confere se o arquivo pode ser usado como amostra já estratificada (cada feição = um chip).

    Devolve erros (impedem gerar chips), avisos (o admin decide) e números por estrato/classe.
    """
    errors: list[str] = []
    warnings: list[str] = []
    raw = read_vector_raw(path)
    n_total = int(len(raw))
    empty_geom = int((raw.geometry.isna() | raw.geometry.is_empty).sum())
    gdf = raw[raw.geometry.notna() & ~raw.geometry.is_empty].copy()
    gtypes = gdf.geom_type.astype(str)
    n_points = int(gtypes.isin(["Point", "MultiPoint"]).sum())
    n_polys = int(gtypes.isin(["Polygon", "MultiPolygon"]).sum())
    n_other = int(len(gdf) - n_points - n_polys)

    if not stratum_field:
        errors.append("Escolha o campo do estrato.")
    elif stratum_field not in gdf.columns:
        errors.append(f"Campo do estrato '{stratum_field}' não existe no arquivo.")
    if class_field and class_field not in gdf.columns:
        errors.append(f"Campo da classe '{class_field}' não existe no arquivo.")
    if n_other:
        errors.append(f"{n_other} feição(ões) não são pontos nem polígonos (ex.: linhas). Remova-as do arquivo.")
    if empty_geom:
        warnings.append(f"{empty_geom} feição(ões) sem geometria serão ignoradas.")

    by_stratum: dict[str, int] = {}
    crosstab: dict[str, dict[str, int]] = {}
    by_class: dict[str, int] = {}
    empty_stratum = empty_class = 0
    if not errors:
        st = gdf[stratum_field].map(norm_value)
        empty_stratum = int(st.isna().sum())
        by_stratum = {str(k): int(v) for k, v in st.dropna().value_counts().items()}
        if empty_stratum:
            warnings.append(f"{empty_stratum} amostra(s) sem estrato serão ignoradas.")
        if len(by_stratum) < 2:
            warnings.append("Só há um estrato. Confira se o campo escolhido é mesmo o do estrato.")
        n_ok = sum(by_stratum.values())
        if n_ok >= 10 and len(by_stratum) > max(20, n_ok // 2):
            warnings.append(f"O campo do estrato tem {len(by_stratum)} valores diferentes para {n_ok} amostras: "
                            "parece um identificador, não um estrato.")
        if class_field:
            cl = gdf[class_field].map(norm_value)
            empty_class = int(cl.isna().sum())
            by_class = {str(k): int(v) for k, v in cl.dropna().value_counts().items()}
            if empty_class:
                warnings.append(f"{empty_class} amostra(s) sem classe de referência.")
            for s_val, c_val in zip(st, cl):
                if s_val is None or (isinstance(s_val, float) and np.isnan(s_val)):
                    continue
                row = crosstab.setdefault(s_val, {})
                key = c_val if isinstance(c_val, str) else "(vazio)"
                row[key] = row.get(key, 0) + 1

    duplicates = overlapping = big_polys = 0
    side = chip_size_px * resolution_m
    if len(gdf) and not n_other:
        utm = gdf.estimate_utm_crs()
        proj = gdf.to_crs(utm)
        duplicates = int(proj.geometry.normalize().to_wkb().duplicated().sum())
        centers = proj.geometry.representative_point()
        if len(centers) > 1:
            import shapely
            tree = shapely.STRtree(centers.values)
            left, right = tree.query(centers.values, predicate="dwithin", distance=side)
            overlapping = int(len({i for i, j in zip(left, right) if i < j} | {j for i, j in zip(left, right) if i < j}))
        if n_polys:
            b = proj.geometry.bounds
            big_polys = int((((b.maxx - b.minx) > side) | ((b.maxy - b.miny) > side)).sum())
        if duplicates:
            warnings.append(f"{duplicates} amostra(s) com geometria repetida (viram chips iguais).")
        if overlapping:
            warnings.append(f"{overlapping} amostra(s) a menos de {side:g} m de outra: os chips vão se sobrepor.")
        if big_polys:
            warnings.append(f"{big_polys} polígono(s) maiores que o chip ({side:g} m): o chip fica centrado e corta o polígono.")

    return {
        "ok": not errors,
        "errors": errors,
        "warnings": warnings,
        "n_total": n_total,
        "n_usable": int(sum(by_stratum.values())) if not errors else 0,
        "n_points": n_points,
        "n_polygons": n_polys,
        "by_stratum": by_stratum,
        "by_class": by_class,
        "crosstab": crosstab,
        "duplicates": duplicates,
        "overlapping": overlapping,
        "big_polygons": big_polys,
        "chip_side_m": side,
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


def inspect_layer(path: Path | str, class_field: str = "", role: str = "reference", stratum_field: str = "") -> dict:
    return inspect_raster(path) if is_raster(path) else inspect_vector(path, class_field, role, stratum_field)
