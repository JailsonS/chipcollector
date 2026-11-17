import io
import json
import time
import zipfile

import numpy as np
import pytest
import rasterio
from fastapi.testclient import TestClient


@pytest.fixture()
def env(tmp_path, monkeypatch):
    monkeypatch.setenv("CHIPS_DATA_DIR", str(tmp_path / "data"))
    monkeypatch.setenv("CHIPS_ALLOW_SIGNUP", "true")
    from chipcollector import config, db
    config.reset_settings()
    db.reset_engine()
    from chipcollector.app import create_app
    from chipcollector.demo import create_demo
    files = create_demo(tmp_path / "demo", size=600)
    app = create_app()
    yield app, files
    db.reset_engine()
    config.reset_settings()


def client(app):
    return TestClient(app)


def _signup(c, name, pwd="secret123"):
    r = c.post("/api/auth/signup", json={"username": name, "password": pwd})
    assert r.status_code == 200, r.text
    return r.json()


def _setup_project(app, files, annotation_type="segmentation", annotators=1, multiclass=True):
    admin = client(app)
    u = _signup(admin, "admin")
    assert u["is_admin"]
    r = admin.post("/api/projects", json={
        "name": "Teste", "target": "desmatamento", "annotation_type": annotation_type,
        "classes": [{"id": 1, "name": "desmatamento", "color": "#ff0000"},
                    {"id": 2, "name": "exploracao", "color": "#00ff00"}],
        "multiclass": multiclass, "chip_size_px": 64, "resolution_m": 10, "annotators_per_chip": annotators,
    })
    assert r.status_code == 200, r.text
    pid = r.json()["id"]
    with open(files["vector"], "rb") as fh:
        r = admin.post(f"/api/projects/{pid}/layers", files=[("files", ("ref.gpkg", fh.read()))],
                       data={"name": "PRODES fake", "theme": "desmatamento", "class_field": "classe"})
    assert r.status_code == 200, r.text
    layer = r.json()
    assert set(layer["strata"]) == {"desmatamento", "exploracao_madeireira"}
    with open(files["mosaic"], "rb") as fh:
        r = admin.post(f"/api/projects/{pid}/rasters", files={"file": ("mosaico.tif", fh.read())})
    assert r.status_code == 200, r.text
    rid = r.json()["id"]
    assert r.json()["band_names"] == ["B", "G", "R", "N"]
    r = admin.patch(f"/api/projects/{pid}", json={"image_sources": [
        {"id": "mosaico", "label": "Mosaico local", "provider": "local", "raster_id": rid,
         "rgb": ["R", "G", "B"], "stretch": [0, 0.25]},
        {"id": "s2-2024", "provider": "planetary_computer", "sensor": "sentinel-2",
         "start": "2024-06-01", "end": "2024-09-30"},
    ]})
    assert r.status_code == 200, r.text
    r = admin.post(f"/api/projects/{pid}/chips/generate", json={
        "layer_id": layer["id"], "n_per_stratum": 5, "n_background": 3,
        "class_map": {"desmatamento": 1, "exploracao_madeireira": 2}})
    assert r.status_code == 200, r.text
    assert r.json()["created"] >= 10
    return admin, pid, layer


def test_full_segmentation_flow(env):
    app, files = env
    admin, pid, layer = _setup_project(app, files)
    ann = client(app)
    _signup(ann, "maria")
    assert not ann.get("/api/auth/me").json()["is_admin"]
    # anotadores não criam projetos
    assert ann.post("/api/projects", json={"name": "x"}).status_code == 403

    a = admin.get(f"/api/projects/{pid}/next").json()["chip_id"]
    b = ann.get(f"/api/projects/{pid}/next").json()["chip_id"]
    assert a and b and a != b, "dois usuários não devem receber o mesmo chip"
    # pedir de novo devolve o mesmo (reserva)
    assert ann.get(f"/api/projects/{pid}/next").json()["chip_id"] == b

    chip = ann.get(f"/api/chips/{b}").json()
    assert chip["size_px"] == 64
    png = ann.get(f"/api/chips/{b}/image.png", params={"source": "mosaico"})
    assert png.status_code == 200 and png.content[:4] == b"\x89PNG"
    info = ann.get(f"/api/chips/{b}/image-info", params={"source": "mosaico"}).json()
    assert info["bands"] == ["B", "G", "R", "N"]

    # polígono em pixels (metade esquerda do chip) + um que extrapola o chip (será recortado)
    feats = {"type": "FeatureCollection", "features": [
        {"type": "Feature", "properties": {"class_id": 1},
         "geometry": {"type": "Polygon", "coordinates": [[[0, 0], [32, 0], [32, 64], [0, 64], [0, 0]]]}},
        {"type": "Feature", "properties": {"class_id": 2},
         "geometry": {"type": "Polygon", "coordinates": [[[50, 50], [80, 50], [80, 80], [50, 80], [50, 50]]]}},
    ]}
    r = ann.put(f"/api/chips/{b}/annotation", json={"source_id": "mosaico", "features": feats})
    assert r.status_code == 200, r.text
    assert r.json()["labels"] == [1, 2]
    mine = ann.get(f"/api/chips/{b}").json()["my_annotation"]
    xs = [x for f in mine["features"]["features"] for ring in f["geometry"]["coordinates"] for x, _ in ring]
    assert max(xs) <= 64.0001
    # sem classe -> erro
    bad = {"type": "FeatureCollection", "features": [{"type": "Feature", "properties": {},
           "geometry": {"type": "Polygon", "coordinates": [[[0, 0], [5, 0], [5, 5], [0, 0]]]}}]}
    assert ann.put(f"/api/chips/{a}/annotation", json={"features": bad}).status_code == 400

    r = admin.put(f"/api/chips/{a}/annotation", json={"source_id": "mosaico", "features": {
        "type": "FeatureCollection", "features": []}})
    assert r.status_code == 200 and r.json()["labels"] == []

    # próximo chip para maria não repete o anotado
    c = ann.get(f"/api/projects/{pid}/next").json()["chip_id"]
    assert c not in (a, b)
    # pular
    assert ann.put(f"/api/chips/{c}/annotation", json={"status": "skip"}).status_code == 200

    stats = admin.get(f"/api/projects/{pid}").json()["stats"]
    assert stats["annotations"] == 3 and stats["by_user"]["maria"] == {"done": 1, "skip": 1}

    # exportação (somente fonte local — sem rede)
    r = ann.post(f"/api/projects/{pid}/exports", json={"source_ids": ["mosaico"]})
    assert r.status_code == 200, r.text
    jid = r.json()["id"]
    for _ in range(100):
        j = ann.get(f"/api/exports/{jid}").json()
        if j["status"] in ("done", "error"):
            break
        time.sleep(0.1)
    assert j["status"] == "done", j
    z = zipfile.ZipFile(io.BytesIO(ann.get(f"/api/exports/{jid}/download").content))
    names = z.namelist()
    root = names[0].split("/")[0]
    assert f"{root}/labels.csv" in names and f"{root}/coco/mosaico.json" in names
    assert f"{root}/annotations.geojson" in names
    masks = [n for n in names if "/masks/" in n]
    assert len(masks) == 2
    mask_name = [m for m in masks if "maria" in m][0]
    with rasterio.open(io.BytesIO(z.read(mask_name))) as m:
        arr = m.read(1)
        assert arr.shape == (64, 64)
        assert (arr[:, :32] == 1).all() and (arr[60:, 60:] == 2).all() and (arr[:40, 40:] == 0).all()
    imgs = [n for n in names if "/images/mosaico/" in n]
    assert len(imgs) == 2
    coco = json.loads(z.read(f"{root}/coco/mosaico.json"))
    assert {c["name"] for c in coco["categories"]} == {"desmatamento", "exploracao"}
    assert len(coco["annotations"]) == 2
    meta = json.loads(z.read(f"{root}/metadata.json"))
    assert meta["counts"]["by_annotator"] == {"admin": 1, "maria": 1}
    csv_text = z.read(f"{root}/labels.csv").decode()
    assert "annotator" in csv_text and "maria" in csv_text


def test_classification_and_multi_annotator(env):
    app, files = env
    admin, pid, layer = _setup_project(app, files, annotation_type="classification", annotators=2,
                                       multiclass=False)
    ann = client(app)
    _signup(ann, "joao")
    a = admin.get(f"/api/projects/{pid}/next").json()["chip_id"]
    b = ann.get(f"/api/projects/{pid}/next").json()["chip_id"]
    assert a == b, "com 2 anotadores por chip, ambos recebem o mesmo chip primeiro"
    assert admin.put(f"/api/chips/{a}/annotation", json={"labels": [1, 2]}).status_code == 400  # multiclasse off
    assert admin.put(f"/api/chips/{a}/annotation", json={"labels": [1]}).status_code == 200
    assert ann.put(f"/api/chips/{b}/annotation", json={"labels": []}).status_code == 200  # ausência
    lst = admin.get(f"/api/projects/{pid}/chips").json()
    done = [c for c in lst if c["id"] == a][0]
    assert done["complete"] and done["n_annotations"] == 2
    r = admin.post(f"/api/projects/{pid}/exports", json={"include_images": False})
    jid = r.json()["id"]
    for _ in range(100):
        j = admin.get(f"/api/exports/{jid}").json()
        if j["status"] in ("done", "error"):
            break
        time.sleep(0.1)
    assert j["status"] == "done", j


def test_raster_reference_and_manual_chip(env):
    app, files = env
    admin, pid, _ = _setup_project(app, files)
    with open(files["raster_ref"], "rb") as fh:
        r = admin.post(f"/api/projects/{pid}/layers", files=[("files", ("lulc.tif", fh.read()))],
                       data={"name": "LULC"})
    assert r.status_code == 200, r.text
    lay = r.json()
    assert lay["kind"] == "raster" and set(lay["strata"]) == {"1", "2", "3"}
    r = admin.post(f"/api/projects/{pid}/chips/generate", json={
        "layer_id": lay["id"], "n_per_stratum": 2, "strata": ["2", "3"], "class_map": {"2": 1, "3": 2}})
    assert r.status_code == 200, r.text
    assert r.json()["by_stratum"].get("2") == 2
    r = admin.post(f"/api/projects/{pid}/chips/manual", json={"lon": -47.92, "lat": -1.30, "layer_id": lay["id"]})
    cid = r.json()["id"]
    chip = admin.get(f"/api/chips/{cid}").json()
    assert chip["stratum"] == "__manual__" and chip["reference"]["features"]


def test_sampling_spacing_and_grid():
    from chipcollector.geo import chip_grid, utm_epsg
    assert utm_epsg(-47.9, -1.3) == 32723
    g = chip_grid(-47.9, -1.3, 128, 10)
    assert g.maxx - g.minx == 1280 and g.minx % 10 == 0
    from shapely.geometry import box
    px = g.to_pixel(box(g.minx, g.miny, g.maxx, g.maxy))
    assert px.bounds == (0, 0, 128, 128)
    back = g.from_pixel(px)
    assert np.allclose(back.bounds, (g.minx, g.miny, g.maxx, g.maxy))


def test_cloud_masks_and_sources():
    from chipcollector.imagery import ImageryError, normalize_source
    from chipcollector.imagery.stac import _cloud_mask
    scl = np.array([[0, 4, 8, 5]])
    assert _cloud_mask("sentinel-2", scl).tolist() == [[False, True, False, True]]
    qa = np.array([[21824, 22280, 1 << 3]])  # claro, nuvem (bit3), nuvem
    assert _cloud_mask("landsat", qa).tolist()[0][0]
    s = normalize_source({"provider": "gee", "sensor": "nicfi", "start": "2022-01-01", "end": "2022-12-31"}, 5)
    assert s["rgb"] == ["R", "G", "B"]
    with pytest.raises(ImageryError):
        normalize_source({"provider": "earth_search", "sensor": "nicfi"}, 10)


def test_delete_project_with_annotations(env):
    app, files = env
    admin, pid, layer = _setup_project(app, files)
    cid = admin.get(f"/api/projects/{pid}/next").json()["chip_id"]
    assert admin.put(f"/api/chips/{cid}/annotation", json={"features": {"type": "FeatureCollection", "features": []}}).status_code == 200
    admin.get(f"/api/projects/{pid}/next")  # deixa uma reserva (lock) ativa
    r = admin.post(f"/api/projects/{pid}/exports", json={"include_images": False})
    jid = r.json()["id"]
    for _ in range(100):
        if admin.get(f"/api/exports/{jid}").json()["status"] in ("done", "error"):
            break
        time.sleep(0.1)
    r = admin.delete(f"/api/projects/{pid}")
    assert r.status_code == 200, r.text
    assert admin.get(f"/api/projects/{pid}").status_code == 404
    assert admin.get("/api/projects").json() == []


def test_pre_stratified_samples(env):
    """Amostra estratificada fora do sistema: verificar, configurar, gerar 1 chip por amostra, exportar."""
    app, files = env
    admin, pid, _ = _setup_project(app, files)
    n_before = len(admin.get(f"/api/projects/{pid}/chips").json())
    with open(files["samples"], "rb") as fh:
        r = admin.post(f"/api/projects/{pid}/layers", files=[("files", ("amostras.geojson", fh.read()))],
                       data={"name": "Amostra externa", "theme": "desmatamento"})
    assert r.status_code == 200, r.text
    lay = r.json()
    assert lay["role"] == "reference"  # nada é assumido sem o admin confirmar
    sug = lay["suggestion"]
    assert sug == {**sug, "role": "samples", "stratum_field": "estrato", "class_field": "classe"}
    names = {f["name"] for f in lay["field_info"]}
    assert {"id", "estrato", "classe"} <= names

    # verificação
    rep = admin.post(f"/api/layers/{lay['id']}/check", json={"role": "samples", "stratum_field": "estrato",
                                                              "class_field": "classe"}).json()
    assert rep["ok"] and rep["n_points"] > 0 and rep["n_polygons"] > 0
    assert set(rep["by_stratum"]) == {"alteracao_corte_raso", "alteracao_seletiva", "floresta_estavel"}
    assert rep["duplicates"] == 1 and any("sem estrato" in w for w in rep["warnings"])
    bad = admin.post(f"/api/layers/{lay['id']}/check", json={"role": "samples", "stratum_field": "xyz"}).json()
    assert not bad["ok"]
    assert admin.patch(f"/api/layers/{lay['id']}", json={"role": "samples", "stratum_field": "xyz"}).status_code == 400

    # configurar
    r = admin.patch(f"/api/layers/{lay['id']}", json={"role": "samples", "stratum_field": "estrato", "class_field": "classe"})
    assert r.status_code == 200, r.text
    assert r.json()["role"] == "samples" and r.json()["strata"] == rep["by_stratum"]

    # gerar: um chip por amostra com estrato; de novo não duplica
    body = {"layer_id": lay["id"], "class_map": {"desmatamento": 1, "exploracao_madeireira": 2}}
    r1 = admin.post(f"/api/projects/{pid}/chips/generate", json=body).json()
    assert r1["created"] == rep["n_usable"] and r1["already_in_chips"] == 0
    r2 = admin.post(f"/api/projects/{pid}/chips/generate", json=body).json()
    assert r2["created"] == 0 and r2["already_in_chips"] == rep["n_usable"]
    only = admin.post(f"/api/projects/{pid}/chips/generate", json={**body, "strata": ["floresta_estavel"]}).json()
    assert only["created"] == 0
    chips = admin.get(f"/api/projects/{pid}/chips").json()
    assert len(chips) == n_before + rep["n_usable"]

    # chip guarda a amostra e a classe de referência
    from chipcollector.db import Chip, get_engine
    from sqlmodel import Session, select
    with Session(get_engine()) as s:
        c = s.exec(select(Chip).where(Chip.layer_id == lay["id"], Chip.stratum == "alteracao_corte_raso")).first()
        cid = c.id
    detail = admin.get(f"/api/chips/{cid}").json()
    smp = detail["reference"]["sample"]
    assert smp["stratum"] == "alteracao_corte_raso" and smp["class_value"] == "desmatamento" and smp["class_id"] == 1
    assert smp["attributes"]["id"]
    assert detail["reference"]["features"], "a própria amostra aparece como referência"

    # exportação traz sample_index e ref_class
    assert admin.put(f"/api/chips/{cid}/annotation", json={"features": {"type": "FeatureCollection", "features": []}}).status_code == 200
    jid = admin.post(f"/api/projects/{pid}/exports", json={"include_images": False}).json()["id"]
    for _ in range(100):
        j = admin.get(f"/api/exports/{jid}").json()
        if j["status"] in ("done", "error"):
            break
        time.sleep(0.1)
    assert j["status"] == "done", j
    z = zipfile.ZipFile(io.BytesIO(admin.get(f"/api/exports/{jid}/download").content))
    csv_name = [n for n in z.namelist() if n.endswith("labels.csv")][0]
    import csv as _csv
    rows = list(_csv.DictReader(io.StringIO(z.read(csv_name).decode())))
    row = [r for r in rows if int(r["chip_id"]) == cid][0]
    assert row["ref_class"] == "desmatamento" and row["sample_index"] != "" and row["stratum"] == "alteracao_corte_raso"


def test_old_database_gets_new_columns(tmp_path, monkeypatch):
    import sqlite3
    monkeypatch.setenv("CHIPS_DATA_DIR", str(tmp_path / "d"))
    from chipcollector import config, db
    config.reset_settings(); db.reset_engine()
    db.get_engine()
    con = sqlite3.connect(tmp_path / "d" / "chipcollector.db")
    con.executescript("create table t as select id, project_id, name, theme, kind, path, class_field, crs, n_features,"
                      " strata, bounds, uploaded_by, created_at from referencelayer;"
                      " drop table referencelayer; alter table t rename to referencelayer;")
    con.close()
    db.reset_engine()
    added = db.add_missing_columns(db.get_engine())  # já aplicada em get_engine; rodar de novo não faz nada
    cols = {r[1] for r in sqlite3.connect(tmp_path / "d" / "chipcollector.db").execute("pragma table_info(referencelayer)")}
    assert {"role", "stratum_field"} <= cols and added == []
    db.reset_engine(); config.reset_settings()
