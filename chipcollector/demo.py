"""Dados sintéticos para testar a plataforma sem acesso a catálogos de imagem.

Gera, numa área perto de Castanhal-PA:
  * reference_desmatamento.gpkg — polígonos com campo `classe` (desmatamento / exploracao_madeireira)
  * reference_uso_cobertura.tif — raster categórico (1 floresta, 2 pasto, 3 água)
  * mosaico_demo.tif — "imagem" de 4 bandas (B,G,R,NIR) a 10 m em UTM 23S, coerente com a referência
  * amostras_estratificadas.geojson — amostra já estratificada "fora do sistema": pontos e polígonos com
    os campos `id`, `estrato` (desenho amostral) e `classe` (referência). Traz de propósito uma amostra
    sem estrato e uma duplicada, para a verificação ter o que apontar.
"""
from __future__ import annotations

from pathlib import Path

import geopandas as gpd
import numpy as np
import rasterio
from rasterio import features as rfeatures
from rasterio.transform import from_origin
from shapely.geometry import Point

CENTER = (-47.92, -1.30)
EPSG = 32723  # UTM 23S


def create_demo(out: Path, seed: int = 0, size: int = 1200, res: float = 10.0) -> dict[str, Path]:
    out.mkdir(parents=True, exist_ok=True)
    rng = np.random.default_rng(seed)
    from pyproj import Transformer

    x0, y0 = Transformer.from_crs(4326, EPSG, always_xy=True).transform(*CENTER)
    minx, maxy = x0 - size * res / 2, y0 + size * res / 2
    tr = from_origin(minx, maxy, res, res)

    polys, classes = [], []
    for i in range(40):
        cx = minx + rng.uniform(0.1, 0.9) * size * res
        cy = maxy - rng.uniform(0.1, 0.9) * size * res
        cls = "desmatamento" if i % 2 == 0 else "exploracao_madeireira"
        r = rng.uniform(150, 450) if cls == "desmatamento" else rng.uniform(60, 160)
        g = Point(cx, cy).buffer(r, quad_segs=6)
        if cls == "desmatamento":
            g = g.simplify(r * 0.25)
        polys.append(g)
        classes.append(cls)
    gdf = gpd.GeoDataFrame({"classe": classes, "ano": [2023] * len(classes)}, geometry=polys, crs=EPSG)
    vpath = out / "reference_desmatamento.gpkg"
    gdf.to_crs(4326).to_file(vpath, driver="GPKG")

    # raster de uso e cobertura
    lulc = np.ones((size, size), "uint8")
    yy, xx = np.mgrid[0:size, 0:size]
    lulc[(xx + yy * 0.3) % 400 < 120] = 2
    lulc[((xx - size * 0.7) ** 2 + (yy - size * 0.3) ** 2) < (size * 0.08) ** 2] = 3
    lulc_path = out / "reference_uso_cobertura.tif"
    with rasterio.open(lulc_path, "w", driver="GTiff", width=size, height=size, count=1, dtype="uint8",
                       crs=EPSG, transform=tr, nodata=0, compress="deflate") as dst:
        dst.write(lulc, 1)

    # mosaico sintético (reflectância)
    burn = rfeatures.rasterize([(g, 1 if c == "desmatamento" else 2) for g, c in zip(polys, classes)],
                               out_shape=(size, size), transform=tr, fill=0, dtype="uint8")
    base = {1: (0.03, 0.06, 0.04, 0.32), 2: (0.06, 0.09, 0.10, 0.25), 3: (0.05, 0.06, 0.04, 0.02)}
    img = np.zeros((4, size, size), "float32")
    for k, refl in base.items():
        for b in range(4):
            img[b][lulc == k] = refl[b]
    for b, v in enumerate((0.09, 0.12, 0.16, 0.20)):
        img[b][burn == 1] = v
    for b, v in enumerate((0.05, 0.08, 0.08, 0.27)):
        img[b][burn == 2] = v
    img += rng.normal(0, 0.008, img.shape).astype("float32")
    mpath = out / "mosaico_demo.tif"
    with rasterio.open(mpath, "w", driver="GTiff", width=size, height=size, count=4, dtype="float32",
                       crs=EPSG, transform=tr, compress="deflate") as dst:
        dst.write(img)
        for i, n in enumerate(["B", "G", "R", "N"], start=1):
            dst.set_band_description(i, n)
    # amostra estratificada externa (pontos + polígonos, estrato e classe em campos separados)
    rows = []
    for i, (g, c) in enumerate(zip(polys, classes)):
        if i % 3 == 0:  # parte das amostras vem como polígono (a própria área), parte como ponto
            geom, est = g, "alteracao_" + ("corte_raso" if c == "desmatamento" else "seletiva")
        else:
            geom, est = g.representative_point(), "alteracao_" + ("corte_raso" if c == "desmatamento" else "seletiva")
        rows.append({"id": f"A{i + 1:03d}", "estrato": est, "classe": c, "geometry": geom})
    k = 0
    while k < 12:  # pontos em área sem alteração (estrato de floresta estável)
        pt = Point(minx + rng.uniform(0.05, 0.95) * size * res, maxy - rng.uniform(0.05, 0.95) * size * res)
        if any(g.distance(pt) < 400 for g in polys):
            continue
        k += 1
        rows.append({"id": f"F{k:03d}", "estrato": "floresta_estavel", "classe": "floresta", "geometry": pt})
    rows.append({"id": "X001", "estrato": None, "classe": "floresta", "geometry": rows[-1]["geometry"].buffer(1)})
    rows.append(dict(rows[1], id="D001"))  # duplicada
    samples = gpd.GeoDataFrame(rows, crs=EPSG).to_crs(4326)
    spath = out / "amostras_estratificadas.geojson"
    samples.to_file(spath, driver="GeoJSON")

    print(f"Dados de demonstração em {out.resolve()}:\n  {vpath.name}\n  {lulc_path.name}\n  {mpath.name}\n  {spath.name}")
    return {"vector": vpath, "raster_ref": lulc_path, "mosaic": mpath, "samples": spath}


def seed_demo(out: Path, username: str = "admin", password: str = "admin123") -> int:
    """Cria usuário admin + dois projetos de demonstração (segmentação e classificação) usando o mosaico local.

    Também adiciona bases Sentinel-2/Landsat (Planetary Computer) para uso quando houver internet.
    """
    from fastapi.testclient import TestClient

    from .app import create_app

    files = create_demo(out)
    c = TestClient(create_app())
    r = c.post("/api/auth/login", json={"username": username, "password": password})
    if r.status_code != 200:
        r = c.post("/api/auth/signup", json={"username": username, "password": password, "full_name": "Administrador"})
        r.raise_for_status()
    pids = []
    for kind, name, classes in (
        ("segmentation", "Demo — segmentação de desmatamento e exploração",
         [{"id": 1, "name": "desmatamento", "color": "#e63946"}, {"id": 2, "name": "exploracao_madeireira", "color": "#f4a261"}]),
        ("classification", "Demo — classificação binária de desmatamento",
         [{"id": 1, "name": "desmatamento", "color": "#e63946"}]),
    ):
        p = c.post("/api/projects", json={"name": name, "target": "desmatamento", "annotation_type": kind,
                                          "classes": classes, "multiclass": kind == "segmentation",
                                          "chip_size_px": 128, "resolution_m": 10}).json()
        pid = p["id"]
        with open(files["vector"], "rb") as fh:
            lay = c.post(f"/api/projects/{pid}/layers", files=[("files", (files["vector"].name, fh.read()))],
                         data={"name": "Referência sintética", "theme": "desmatamento", "class_field": "classe"}).json()
        with open(files["mosaic"], "rb") as fh:
            ras = c.post(f"/api/projects/{pid}/rasters", files={"file": (files["mosaic"].name, fh.read())},
                         data={"name": "Mosaico demo"}).json()
        c.patch(f"/api/projects/{pid}", json={"image_sources": [
            {"id": "mosaico-demo", "label": "Mosaico demo (local)", "provider": "local", "raster_id": ras["id"],
             "rgb": ["R", "G", "B"], "stretch": [0, 0.22]},
            {"id": "mosaico-demo-nir", "label": "Mosaico demo — falsa cor (NIR)", "provider": "local",
             "raster_id": ras["id"], "rgb": ["N", "R", "G"], "stretch": [0, 0.35]},
            {"id": "s2-2024-seca", "label": "Sentinel-2 · seca 2024", "provider": "planetary_computer",
             "sensor": "sentinel-2", "start": "2024-06-01", "end": "2024-09-30", "max_cloud": 20},
            {"id": "landsat-2024", "label": "Landsat 8/9 · 2024", "provider": "planetary_computer",
             "sensor": "landsat", "start": "2024-06-01", "end": "2024-10-31", "max_cloud": 30},
        ]}).raise_for_status()
        cmap = {"desmatamento": 1, "exploracao_madeireira": 2} if kind == "segmentation" else {"desmatamento": 1}
        c.post(f"/api/projects/{pid}/chips/generate", json={
            "layer_id": lay["id"], "n_per_stratum": 15, "n_background": 10, "class_map": cmap}).raise_for_status()
        pids.append(pid)
    print(f"Projetos de demonstração criados: {pids}. Login: {username} / {password}")
    return pids[0]
