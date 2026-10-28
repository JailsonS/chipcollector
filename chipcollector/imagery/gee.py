"""Google Earth Engine (acesso gratuito para ONGs, pesquisa e uso não comercial).

Autenticação (uma das opções):
  * conta de serviço: CHIPS_GEE_SERVICE_ACCOUNT + CHIPS_GEE_KEY_FILE (JSON)
  * credenciais de usuário: `earthengine authenticate` uma vez na máquina
Em ambos os casos informe CHIPS_GEE_PROJECT (projeto Cloud registrado no Earth Engine).

Os pixels são obtidos com `ee.data.computePixels` diretamente na grade do chip.
Planet NICFI (trópicos, 4,77 m) exige cadastro gratuito no programa NICFI para liberar o asset.
"""
from __future__ import annotations

import threading
from typing import Any

import numpy as np

from ..config import get_settings
from ..geo import ChipGrid
from . import ChipImage, ImageryError

_init_lock = threading.Lock()
_initialized = False


def init_ee():
    global _initialized
    try:
        import ee
    except ImportError as e:  # pragma: no cover
        raise ImageryError("Instale o extra 'gee': pip install 'chipcollector[gee]'") from e
    with _init_lock:
        if not _initialized:
            s = get_settings()
            kwargs = {"project": s.gee_project} if s.gee_project else {}
            try:
                if s.gee_service_account and s.gee_key_file:
                    creds = ee.ServiceAccountCredentials(s.gee_service_account, s.gee_key_file)
                    ee.Initialize(creds, **kwargs)
                else:
                    ee.Initialize(**kwargs)
            except Exception as e:
                raise ImageryError(f"Falha ao inicializar o Earth Engine: {e}") from e
            _initialized = True
    return ee


# Mapas banda canônica -> banda GEE
S2_BANDS = {b: b for b in ["B02", "B03", "B04", "B05", "B06", "B07", "B08", "B8A", "B11", "B12"]}
S2_BANDS = {k: k.replace("B0", "B") if k != "B8A" else "B8A" for k in S2_BANDS}
L89_BANDS = {"blue": "SR_B2", "green": "SR_B3", "red": "SR_B4", "nir08": "SR_B5", "swir16": "SR_B6", "swir22": "SR_B7"}
L57_BANDS = {"blue": "SR_B1", "green": "SR_B2", "red": "SR_B3", "nir08": "SR_B4", "swir16": "SR_B5", "swir22": "SR_B7"}


def _collection(ee, source: dict[str, Any], region):
    sensor = source["sensor"]
    start, end = source["start"], source["end"]
    cloud = float(source.get("max_cloud", 30))
    bands = source["bands"]

    if sensor == "sentinel-2":
        def prep(img):
            scl = img.select("SCL")
            ok = scl.eq(2).Or(scl.eq(4)).Or(scl.eq(5)).Or(scl.eq(6)).Or(scl.eq(7)).Or(scl.eq(11))
            return img.select([S2_BANDS[b] for b in bands], bands).multiply(1e-4).updateMask(ok) \
                .copyProperties(img, ["system:time_start", "CLOUDY_PIXEL_PERCENTAGE"])
        col = (ee.ImageCollection("COPERNICUS/S2_SR_HARMONIZED").filterBounds(region).filterDate(start, end)
               .filter(ee.Filter.lt("CLOUDY_PIXEL_PERCENTAGE", cloud)).sort("CLOUDY_PIXEL_PERCENTAGE"))
        return col.map(prep), "CLOUDY_PIXEL_PERCENTAGE"

    if sensor in ("landsat", "landsat-legacy"):
        ids, bmap = (("LANDSAT/LC08/C02/T1_L2", "LANDSAT/LC09/C02/T1_L2"), L89_BANDS) if sensor == "landsat" \
            else (("LANDSAT/LT05/C02/T1_L2", "LANDSAT/LE07/C02/T1_L2"), L57_BANDS)

        def prep(img):
            qa = img.select("QA_PIXEL")
            ok = qa.bitwiseAnd(0b11111).eq(0)
            return img.select([bmap[b] for b in bands], bands).multiply(2.75e-5).add(-0.2).updateMask(ok) \
                .copyProperties(img, ["system:time_start", "CLOUD_COVER"])
        col = ee.ImageCollection(ids[0]).merge(ee.ImageCollection(ids[1])) \
            .filterBounds(region).filterDate(start, end).filter(ee.Filter.lt("CLOUD_COVER", cloud)).sort("CLOUD_COVER")
        return col.map(prep), "CLOUD_COVER"

    if sensor == "sentinel-1":
        col = (ee.ImageCollection("COPERNICUS/S1_GRD").filterBounds(region).filterDate(start, end)
               .filter(ee.Filter.eq("instrumentMode", "IW"))
               .filter(ee.Filter.listContains("transmitterReceiverPolarisation", "VH")))
        col = col.map(lambda img: img.select(["VV", "VH"], ["vv", "vh"]).select(bands)
                      .copyProperties(img, ["system:time_start"]))
        return col, None

    if sensor == "nicfi":
        col = ee.ImageCollection(source.get("collection") or "projects/planet-nicfi/assets/basemaps/americas") \
            .filterBounds(region).filterDate(start, end)
        return col.map(lambda img: img.select(bands).multiply(1e-4).copyProperties(img, ["system:time_start"])), None

    if sensor == "custom":
        cid = source.get("collection")
        if not cid:
            raise ImageryError("Informe 'collection' (ID da ImageCollection no GEE) para sensor custom.")
        col = ee.ImageCollection(cid).filterBounds(region).filterDate(start, end)
        scale = float(source.get("scale", 1.0))
        offset = float(source.get("offset", 0.0))
        return col.map(lambda img: img.select(bands).multiply(scale).add(offset)
                       .copyProperties(img, ["system:time_start"])), source.get("cloud_property")

    raise ImageryError(f"Sensor {sensor} não suportado no GEE.")


def fetch_gee(source: dict[str, Any], grid: ChipGrid) -> ChipImage:
    ee = init_ee()
    minx, miny, maxx, maxy = grid.bbox_wgs84()
    region = ee.Geometry.Rectangle([minx, miny, maxx, maxy])
    col, _ = _collection(ee, source, region)
    col = col.limit(int(source.get("max_items", 8)) if source.get("composite") == "least_cloudy" else 200)
    n = col.size().getInfo()
    if n == 0:
        raise ImageryError("Nenhuma cena encontrada no GEE para o período/nuvens escolhidos.")
    img = col.mosaic() if source.get("composite") == "least_cloudy" else col.median()
    img = img.select(source["bands"]).toFloat()
    request = {
        "expression": img,
        "fileFormat": "NUMPY_NDARRAY",
        "grid": {
            "dimensions": {"width": grid.size_px, "height": grid.size_px},
            "affineTransform": {"scaleX": grid.resolution, "shearX": 0, "translateX": grid.minx,
                                "shearY": 0, "scaleY": -grid.resolution, "translateY": grid.maxy},
            "crsCode": grid.crs,
        },
    }
    try:
        arr = ee.data.computePixels(request)
    except Exception as e:
        raise ImageryError(f"Erro do Earth Engine: {e}") from e
    data = np.stack([np.asarray(arr[b], dtype="float32") for b in source["bands"]])
    data[~np.isfinite(data)] = np.nan
    try:
        dates = col.aggregate_array("system:time_start").getInfo()
        from datetime import datetime, timezone
        dates = sorted({datetime.fromtimestamp(d / 1000, timezone.utc).date().isoformat() for d in dates})[:50]
    except Exception:
        dates = []
    meta = {"provider": "gee", "sensor": source["sensor"], "start": source["start"], "end": source["end"],
            "composite": source.get("composite", "median"), "n_scenes": n, "dates": dates,
            "valid_fraction": float(np.isfinite(data).all(axis=0).mean())}
    return ChipImage(data, list(source["bands"]), meta)
