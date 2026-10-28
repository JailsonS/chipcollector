"""Exportação do conjunto de amostras em formatos usados por fluxos de treinamento.

Estrutura do pacote (zip):

    <projeto>_<data>/
      metadata.json             configuração do projeto, classes, fontes, contagens, autores
      labels.csv                1 linha por anotação: chip, autor, status, rótulos, split, caminhos
      chips.geojson             contorno de cada chip (WGS84) com rótulos e autoria
      annotations.geojson       polígonos anotados (WGS84) — segmentação
      images/<fonte>/chip_000001.tif   GeoTIFF float32 multibanda na grade do chip
      masks/chip_000001__<usuario>.tif GeoTIFF uint8 (0 = fundo, n = id da classe) — segmentação
      coco/<fonte>.json         anotações COCO (pixels) apontando para images/<fonte>/ — segmentação
      README.txt

Compatível com TorchGeo, segmentation-models-pytorch, Raster Vision, Detectron2/MMDetection (COCO),
Keras/TF (CSV + GeoTIFF) e qualquer fluxo baseado em rasterio/xarray.
"""
from __future__ import annotations

import csv
import json
import logging
import random
import shutil
import traceback
import zipfile
from collections import Counter
from datetime import datetime
from pathlib import Path
from typing import Any, Callable

import numpy as np
import rasterio
from rasterio import features as rfeatures
from shapely.geometry import mapping, shape
from sqlmodel import Session, select

from .config import get_settings
from .db import Annotation, Chip, ExportJob, Project, User, get_engine
from .geo import fc_transform, grid_from_chip, reproject_geom
from .service import cache_path, chip_image, get_source, safe_id

log = logging.getLogger(__name__)

DEFAULT_OPTIONS = {
    "source_ids": [],            # vazio = todas as fontes do projeto
    "statuses": ["done"],        # done | uncertain (skip nunca é exportado como rótulo)
    "include_images": True,
    "split": [0.7, 0.15, 0.15],  # treino/validação/teste por chip (semente fixa)
    "seed": 42,
    "drive": False,
}


def _split_for(chip_ids: list[int], ratios: list[float], seed: int) -> dict[int, str]:
    ids = sorted(chip_ids)
    random.Random(seed).shuffle(ids)
    n = len(ids)
    r = [max(0.0, float(x)) for x in (ratios or [1, 0, 0])][:3] + [0, 0, 0]
    tot = sum(r[:3]) or 1
    n_tr = round(n * r[0] / tot)
    n_va = round(n * r[1] / tot)
    out = {}
    for i, cid in enumerate(ids):
        out[cid] = "train" if i < n_tr else ("val" if i < n_tr + n_va else "test")
    return out


def _mask(ann: Annotation, chip: Chip) -> np.ndarray:
    grid = grid_from_chip(chip)
    shapes = []
    for f in (ann.geometry or {}).get("features", []):
        cid = (f.get("properties") or {}).get("class_id")
        if cid is None or not f.get("geometry"):
            continue
        shapes.append((f["geometry"], int(cid)))
    if not shapes:
        return np.zeros((grid.size_px, grid.size_px), "uint8")
    return rfeatures.rasterize(shapes, out_shape=(grid.size_px, grid.size_px), transform=grid.transform,
                               fill=0, dtype="uint8", all_touched=False)


def _write_mask(path: Path, arr: np.ndarray, chip: Chip, classes: list[dict]) -> None:
    grid = grid_from_chip(chip)
    path.parent.mkdir(parents=True, exist_ok=True)
    with rasterio.open(path, "w", driver="GTiff", width=grid.size_px, height=grid.size_px, count=1,
                       dtype="uint8", crs=grid.crs, transform=grid.transform, compress="deflate") as dst:
        dst.write(arr, 1)
        dst.update_tags(classes=json.dumps({c["id"]: c["name"] for c in classes}, ensure_ascii=False))
        cmap = {0: (0, 0, 0, 0)}
        for c in classes:
            h = c.get("color", "#ff0000").lstrip("#")
            cmap[int(c["id"])] = tuple(int(h[i:i + 2], 16) for i in (0, 2, 4)) + (255,)
        dst.write_colormap(1, cmap)


def _coco_segmentation(geom, grid) -> tuple[list, list[float], float]:
    g = grid.to_pixel(geom)
    polys = [g] if g.geom_type == "Polygon" else [p for p in getattr(g, "geoms", []) if p.geom_type == "Polygon"]
    segs = []
    for p in polys:
        coords = list(p.exterior.coords)[:-1]
        if len(coords) >= 3:
            segs.append([round(v, 2) for xy in coords for v in xy])
    minx, miny, maxx, maxy = g.bounds
    return segs, [round(minx, 2), round(miny, 2), round(maxx - minx, 2), round(maxy - miny, 2)], float(g.area)


def build_dataset(session: Session, project: Project, options: dict[str, Any], out_dir: Path,
                  progress: Callable[[float, str], None] = lambda p, m: None) -> Path:
    opts = {**DEFAULT_OPTIONS, **(options or {})}
    classes = project.classes or []
    cname = {int(c["id"]): c["name"] for c in classes}
    seg = project.annotation_type == "segmentation"
    statuses = set(opts["statuses"] or ["done"])

    anns = session.exec(select(Annotation).where(Annotation.project_id == project.id)).all()
    anns = [a for a in anns if a.status in statuses]
    if not anns:
        raise ValueError("Não há anotações com os status escolhidos para exportar.")
    chips = {c.id: c for c in session.exec(select(Chip).where(Chip.project_id == project.id)).all()}
    users = {u.id: u.username for u in session.exec(select(User)).all()}
    split = _split_for(sorted({a.chip_id for a in anns}), opts["split"], int(opts["seed"]))

    src_ids = opts["source_ids"] or [s["id"] for s in project.image_sources or []]
    sources = [get_source(project, sid) for sid in src_ids] if opts["include_images"] else []

    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    root_name = f"{safe_id(project.name)}_{stamp}"
    root = out_dir / root_name
    root.mkdir(parents=True, exist_ok=True)

    # ---------------------------------------------------------------- imagens
    image_paths: dict[tuple[int, str], str] = {}
    image_errors: list[str] = []
    chip_list = sorted({a.chip_id for a in anns})
    total = max(1, len(chip_list) * max(1, len(sources)))
    k = 0
    for cid in chip_list:
        chip = chips[cid]
        for src in sources:
            k += 1
            progress(0.05 + 0.75 * k / total, f"Imagens {k}/{total}")
            rel = f"images/{safe_id(src['id'])}/chip_{cid:06d}.tif"
            try:
                chip_image(session, project, chip, src)
                (root / rel).parent.mkdir(parents=True, exist_ok=True)
                shutil.copyfile(cache_path(project, chip, src), root / rel)
                image_paths[(cid, src["id"])] = rel
            except Exception as e:  # noqa: BLE001 - registra e segue (ex.: ImageryError, rede)
                image_errors.append(f"chip {cid} / {src['id']}: {e}")

    # ---------------------------------------------------------------- rótulos
    progress(0.82, "Rótulos e máscaras")
    label_rows, chip_feats, ann_feats = [], [], []
    coco: dict[str, dict] = {s["id"]: {"images": [], "annotations": []} for s in sources} if seg else {}
    coco_ann_id = 1
    for a in sorted(anns, key=lambda x: (x.chip_id, x.user_id)):
        chip = chips[a.chip_id]
        grid = grid_from_chip(chip)
        user = users.get(a.user_id, str(a.user_id))
        labels = sorted(set(a.labels or []))
        if seg:  # em segmentação, as classes presentes vêm dos polígonos
            labels = sorted({int(f["properties"]["class_id"]) for f in (a.geometry or {}).get("features", [])
                             if (f.get("properties") or {}).get("class_id") is not None})
        row = {
            "chip_id": a.chip_id, "annotation_id": a.id, "annotator": user, "status": a.status,
            "source_used": a.source_id, "stratum": chip.stratum, "split": split[a.chip_id],
            "lon": round(chip.lon, 7), "lat": round(chip.lat, 7), "epsg": chip.epsg,
            "size_px": chip.size_px, "resolution_m": chip.resolution_m,
            "label_ids": ";".join(map(str, labels)),
            "label_names": ";".join(cname.get(i, str(i)) for i in labels),
            "present": int(bool(labels)),
        }
        for c in classes:
            row[f"cls_{safe_id(c['name'])}"] = int(int(c["id"]) in labels)
        if seg:
            mrel = f"masks/chip_{a.chip_id:06d}__{safe_id(user)}.tif"
            _write_mask(root / mrel, _mask(a, chip), chip, classes)
            row["mask_path"] = mrel
        for src in sources:
            row[f"image_{safe_id(src['id'])}"] = image_paths.get((a.chip_id, src["id"]), "")
        row["comment"] = a.comment
        row["created_at"] = a.created_at.isoformat()
        row["updated_at"] = a.updated_at.isoformat()
        label_rows.append(row)

        chip_feats.append({"type": "Feature", "geometry": mapping(grid.footprint_wgs84()),
                           "properties": {k2: row[k2] for k2 in ("chip_id", "annotation_id", "annotator", "status",
                                                                 "stratum", "split", "label_names", "present")}})
        if seg:
            fc = fc_transform(a.geometry, lambda g, e=chip.epsg: reproject_geom(g, e, 4326))
            for f in fc["features"]:
                cid = f["properties"].get("class_id")
                f["properties"].update({"chip_id": a.chip_id, "annotator": user,
                                        "class_name": cname.get(int(cid), "") if cid is not None else ""})
                ann_feats.append(f)
            for src in sources:
                rel = image_paths.get((a.chip_id, src["id"]))
                if not rel:
                    continue
                img_id = a.id
                coco[src["id"]]["images"].append({"id": img_id, "file_name": rel,
                                                  "width": chip.size_px, "height": chip.size_px,
                                                  "chip_id": a.chip_id, "annotator": user, "split": split[a.chip_id]})
                for f in (a.geometry or {}).get("features", []):
                    cid = (f.get("properties") or {}).get("class_id")
                    if cid is None:
                        continue
                    segm, bbox, area = _coco_segmentation(shape(f["geometry"]), grid)
                    if not segm:
                        continue
                    coco[src["id"]]["annotations"].append({"id": coco_ann_id, "image_id": img_id,
                                                           "category_id": int(cid), "segmentation": segm,
                                                           "bbox": bbox, "area": area, "iscrowd": 0})
                    coco_ann_id += 1

    progress(0.9, "Gravando arquivos")
    fieldnames = list(dict.fromkeys(k for r in label_rows for k in r.keys()))
    with open(root / "labels.csv", "w", newline="", encoding="utf-8") as fh:
        w = csv.DictWriter(fh, fieldnames=fieldnames)
        w.writeheader()
        w.writerows(label_rows)
    (root / "chips.geojson").write_text(json.dumps({"type": "FeatureCollection", "features": chip_feats}))
    if seg:
        (root / "annotations.geojson").write_text(json.dumps({"type": "FeatureCollection", "features": ann_feats}))
        (root / "coco").mkdir(exist_ok=True)
        cats = [{"id": int(c["id"]), "name": c["name"], "supercategory": project.target or "alvo"} for c in classes]
        for sid, data in coco.items():
            data.update({"info": {"description": project.name, "date_created": stamp, "source": sid},
                         "categories": cats, "licenses": []})
            (root / "coco" / f"{safe_id(sid)}.json").write_text(json.dumps(data))

    authors = Counter(r["annotator"] for r in label_rows)
    meta = {
        "project": {"id": project.id, "name": project.name, "description": project.description,
                    "target": project.target, "annotation_type": project.annotation_type,
                    "multiclass": project.multiclass, "chip_size_px": project.chip_size_px,
                    "resolution_m": project.resolution_m},
        "classes": classes, "image_sources": sources, "options": opts,
        "counts": {"annotations": len(label_rows), "chips": len(chip_list),
                   "by_split": dict(Counter(split.values())), "by_annotator": dict(authors),
                   "by_label": dict(Counter(r["label_names"] or "(ausente)" for r in label_rows))},
        "image_errors": image_errors[:500],
        "exported_at": datetime.now().isoformat(),
        "software": "chipcollector",
    }
    (root / "metadata.json").write_text(json.dumps(meta, indent=2, ensure_ascii=False, default=str))
    (root / "README.txt").write_text(__doc__ or "", encoding="utf-8")

    progress(0.95, "Compactando")
    zpath = out_dir / f"{root_name}.zip"
    with zipfile.ZipFile(zpath, "w", zipfile.ZIP_DEFLATED) as zf:
        for p in sorted(root.rglob("*")):
            if p.is_file():
                zf.write(p, p.relative_to(out_dir))
    shutil.rmtree(root, ignore_errors=True)
    return zpath


def run_export_job(job_id: int) -> None:
    """Executado em thread de fundo pela API."""
    with Session(get_engine()) as session:
        job = session.get(ExportJob, job_id)
        project = session.get(Project, job.project_id)
        user = session.get(User, job.user_id)

        def progress(p: float, msg: str):
            job.progress, job.message, job.status = round(p, 3), msg, "running"
            session.add(job)
            session.commit()

        try:
            out_dir = get_settings().data_dir / "exports" / str(project.id)
            out_dir.mkdir(parents=True, exist_ok=True)
            zpath = build_dataset(session, project, job.options, out_dir, progress)
            job.path = str(zpath)
            if job.options.get("drive"):
                from .drive import upload
                progress(0.97, "Enviando ao Google Drive")
                fid, new_token = upload(zpath, user.drive_token or None, folder_name="ChipCollector")
                job.drive_file_id = fid
                if new_token:
                    user.drive_token = new_token
                    session.add(user)
            job.status, job.progress, job.message = "done", 1.0, "Concluído"
        except Exception as e:  # noqa: BLE001
            log.exception("export failed")
            job.status, job.message = "error", f"{e}"
            job.options = {**job.options, "trace": traceback.format_exc()[-2000:]}
        session.add(job)
        session.commit()
