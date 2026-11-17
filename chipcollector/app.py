"""API web (FastAPI) + interface estática."""
from __future__ import annotations

import random
import shutil
import threading
from datetime import timedelta
from pathlib import Path
from typing import Any, Optional

from fastapi import Depends, FastAPI, File, Form, HTTPException, Query, Request, UploadFile
from fastapi.responses import FileResponse, JSONResponse, RedirectResponse, Response
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field
from shapely.geometry import box, mapping, shape
from shapely.validation import make_valid
from sqlalchemy import func
from sqlmodel import Session, select
from starlette.middleware.sessions import SessionMiddleware

from . import __version__
from .auth import admin_user, create_user, current_user, hash_password, verify_password
from .config import get_settings
from .db import (Annotation, Chip, ChipLock, ExportJob, LocalRaster, Project, ReferenceLayer, User,
                 get_engine, get_session, utcnow)
from .geo import grid_from_chip
from .imagery import ImageryError, catalog, normalize_source, render_png
from .layers import LayerError, inspect_layer, is_raster, read_vector, store_upload
from .sampling import SamplingParams, manual_chip, reference_to_pixels, sample_layer
from .service import cache_path, chip_image, get_source

STATIC = Path(__file__).parent / "static"


# =============================================================================== schemas

class Credentials(BaseModel):
    username: str
    password: str
    full_name: str = ""
    email: str = ""


class UserCreate(Credentials):
    is_admin: bool = False


class UserPatch(BaseModel):
    full_name: Optional[str] = None
    email: Optional[str] = None
    password: Optional[str] = None
    is_admin: Optional[bool] = None
    is_active: Optional[bool] = None


class ClassDef(BaseModel):
    id: int
    name: str
    color: str = "#e63946"


class ProjectIn(BaseModel):
    name: str
    description: str = ""
    target: str = ""
    annotation_type: str = Field("classification", pattern="^(classification|segmentation)$")
    classes: list[ClassDef] = Field(default_factory=lambda: [ClassDef(id=1, name="alvo")])
    multiclass: bool = False
    chip_size_px: int = Field(256, ge=16, le=2048)
    resolution_m: float = Field(10.0, gt=0, le=1000)
    annotators_per_chip: int = Field(1, ge=1, le=10)
    image_sources: list[dict[str, Any]] = Field(default_factory=list)


class ProjectPatch(BaseModel):
    name: Optional[str] = None
    description: Optional[str] = None
    target: Optional[str] = None
    annotation_type: Optional[str] = Field(None, pattern="^(classification|segmentation)$")
    classes: Optional[list[ClassDef]] = None
    multiclass: Optional[bool] = None
    chip_size_px: Optional[int] = Field(None, ge=16, le=2048)
    resolution_m: Optional[float] = Field(None, gt=0, le=1000)
    annotators_per_chip: Optional[int] = Field(None, ge=1, le=10)
    image_sources: Optional[list[dict[str, Any]]] = None


class GenerateIn(BaseModel):
    layer_id: int
    n_per_stratum: int = Field(50, ge=1, le=100_000)
    strata: list[str] = Field(default_factory=list)
    class_field: Optional[str] = None
    class_map: dict[str, Optional[int]] = Field(default_factory=dict)
    n_background: int = Field(0, ge=0, le=100_000)
    min_spacing_px: Optional[int] = Field(None, ge=0)
    placement: str = Field("random", pattern="^(random|centroid)$")
    seed: int = 42


class ManualChipIn(BaseModel):
    lon: float
    lat: float
    layer_id: Optional[int] = None


class AnnotationIn(BaseModel):
    source_id: str = ""
    status: str = Field("done", pattern="^(done|skip|uncertain)$")
    labels: list[int] = Field(default_factory=list)
    # FeatureCollection em coordenadas de PIXEL do chip (x = coluna, y = linha)
    features: dict[str, Any] = Field(default_factory=lambda: {"type": "FeatureCollection", "features": []})
    comment: str = ""


class ExportIn(BaseModel):
    source_ids: list[str] = Field(default_factory=list)
    statuses: list[str] = Field(default_factory=lambda: ["done"])
    include_images: bool = True
    split: list[float] = Field(default_factory=lambda: [0.7, 0.15, 0.15])
    seed: int = 42
    drive: bool = False


# =============================================================================== helpers

def _user_dict(u: User) -> dict:
    return {"id": u.id, "username": u.username, "full_name": u.full_name, "email": u.email,
            "is_admin": u.is_admin, "is_active": u.is_active, "drive_connected": bool(u.drive_token)}


def _project_or_404(session: Session, pid: int) -> Project:
    p = session.get(Project, pid)
    if not p:
        raise HTTPException(404, "Projeto não encontrado.")
    return p


def _chip_or_404(session: Session, cid: int) -> Chip:
    c = session.get(Chip, cid)
    if not c:
        raise HTTPException(404, "Chip não encontrado.")
    return c


def _validate_sources(sources: list[dict], res: float) -> list[dict]:
    out, seen = [], set()
    for s in sources:
        try:
            n = normalize_source(s, res)
        except ImageryError as e:
            raise HTTPException(400, str(e))
        if n["provider"] != "local" and not (n.get("start") and n.get("end")):
            raise HTTPException(400, f"Fonte '{n['label']}': informe data inicial e final.")
        if n["id"] in seen:
            raise HTTPException(400, f"Id de fonte repetido: {n['id']}")
        seen.add(n["id"])
        n.pop("resolution", None)
        out.append(n)
    return out


def _project_stats(session: Session, p: Project) -> dict:
    n_chips = session.exec(select(func.count()).select_from(Chip).where(Chip.project_id == p.id)).one()
    done_chips = session.exec(select(func.count()).select_from(Chip).where(
        Chip.project_id == p.id, Chip.n_annotations >= p.annotators_per_chip)).one()
    rows = session.exec(select(Annotation.user_id, Annotation.status, func.count()).where(
        Annotation.project_id == p.id).group_by(Annotation.user_id, Annotation.status)).all()
    users = {u.id: u.username for u in session.exec(select(User)).all()}
    by_user: dict[str, dict] = {}
    by_status: dict[str, int] = {}
    for uid, st, n in rows:
        by_user.setdefault(users.get(uid, str(uid)), {})[st] = n
        by_status[st] = by_status.get(st, 0) + n
    strata = session.exec(select(Chip.stratum, func.count()).where(Chip.project_id == p.id)
                          .group_by(Chip.stratum)).all()
    labels: dict[str, int] = {}
    cname = {int(c["id"]): c["name"] for c in p.classes or []}
    for lab in session.exec(select(Annotation.labels).where(Annotation.project_id == p.id,
                                                            Annotation.status != "skip")).all():
        key = ";".join(cname.get(int(i), str(i)) for i in sorted(lab or [])) or "(ausente)"
        labels[key] = labels.get(key, 0) + 1
    return {"chips": n_chips, "chips_complete": done_chips, "annotations": sum(by_status.values()),
            "by_status": by_status, "by_user": by_user, "by_stratum": dict(strata), "by_label": labels}


def _project_dict(p: Project, stats: dict | None = None) -> dict:
    d = p.model_dump()
    d["created_at"] = p.created_at.isoformat()
    if stats is not None:
        d["stats"] = stats
    return d


def _pixel_fc_to_chip(fc: dict, chip: Chip, valid_ids: set[int]) -> dict:
    grid = grid_from_chip(chip)
    cbox = box(grid.minx, grid.miny, grid.maxx, grid.maxy)
    feats = []
    for f in (fc or {}).get("features", []):
        try:
            g = shape(f["geometry"])
        except Exception:
            continue
        g = grid.from_pixel(g)
        if not g.is_valid:
            g = make_valid(g)
        g = g.intersection(cbox)
        if g.is_empty or g.area <= 0:
            continue
        if g.geom_type == "GeometryCollection":
            polys = [x for x in g.geoms if x.geom_type in ("Polygon", "MultiPolygon")]
            if not polys:
                continue
            from shapely.ops import unary_union
            g = unary_union(polys)
        props = dict(f.get("properties") or {})
        cid = props.get("class_id")
        if cid is None or int(cid) not in valid_ids:
            raise HTTPException(400, "Todo polígono precisa de uma classe válida do projeto.")
        feats.append({"type": "Feature", "properties": {"class_id": int(cid)}, "geometry": mapping(g)})
    return {"type": "FeatureCollection", "features": feats}


# =============================================================================== app

def create_app() -> FastAPI:
    settings = get_settings()
    get_engine()
    app = FastAPI(title="ChipCollector", version=__version__,
                  description="Coleta de amostras de referência para treinamento de modelos de IA")
    app.add_middleware(SessionMiddleware, secret_key=settings.secret_key, session_cookie="chipcollector",
                       max_age=14 * 24 * 3600, same_site="lax", https_only=settings.public_url.startswith("https"))

    @app.exception_handler(ImageryError)
    async def _imagery_err(_, exc: ImageryError):
        return JSONResponse({"detail": str(exc)}, status_code=502)

    @app.exception_handler(LayerError)
    async def _layer_err(_, exc: LayerError):
        return JSONResponse({"detail": str(exc)}, status_code=400)

    # ------------------------------------------------------------------ config/auth
    @app.get("/api/config")
    def config(session: Session = Depends(get_session)):
        from .drive import oauth_enabled, service_account_enabled
        s = get_settings()
        return {"version": __version__, "allow_signup": s.allow_signup,
                "has_users": session.exec(select(User.id)).first() is not None, "drive_oauth": oauth_enabled(),
                "drive_service_account": service_account_enabled(), "gee_project": bool(s.gee_project),
                "catalog": catalog()}

    @app.post("/api/auth/signup")
    def signup(body: Credentials, request: Request, session: Session = Depends(get_session)):
        first = session.exec(select(User)).first() is None
        if not get_settings().allow_signup and not first:
            raise HTTPException(403, "Cadastro desabilitado. Peça a um administrador.")
        u = create_user(session, body.username, body.password, full_name=body.full_name, email=body.email)
        request.session["uid"] = u.id
        return _user_dict(u)

    @app.post("/api/auth/login")
    def login(body: Credentials, request: Request, session: Session = Depends(get_session)):
        u = session.exec(select(User).where(User.username == body.username.strip().lower())).first()
        if not u or not u.is_active or not verify_password(body.password, u.password_hash):
            raise HTTPException(401, "Usuário ou senha inválidos.")
        request.session["uid"] = u.id
        return _user_dict(u)

    @app.post("/api/auth/logout")
    def logout(request: Request):
        request.session.clear()
        return {"ok": True}

    @app.get("/api/auth/me")
    def me(user: User = Depends(current_user)):
        return _user_dict(user)

    @app.get("/api/users")
    def list_users(_: User = Depends(admin_user), session: Session = Depends(get_session)):
        return [_user_dict(u) for u in session.exec(select(User).order_by(User.username)).all()]

    @app.post("/api/users")
    def add_user(body: UserCreate, _: User = Depends(admin_user), session: Session = Depends(get_session)):
        return _user_dict(create_user(session, body.username, body.password, full_name=body.full_name,
                                      email=body.email, is_admin=body.is_admin))

    @app.patch("/api/users/{uid}")
    def patch_user(uid: int, body: UserPatch, me_: User = Depends(current_user),
                   session: Session = Depends(get_session)):
        if uid != me_.id and not me_.is_admin:
            raise HTTPException(403, "Sem permissão.")
        u = session.get(User, uid)
        if not u:
            raise HTTPException(404, "Usuário não encontrado.")
        if body.password:
            if len(body.password) < 6:
                raise HTTPException(400, "Senha muito curta.")
            u.password_hash = hash_password(body.password)
        for f in ("full_name", "email"):
            if getattr(body, f) is not None:
                setattr(u, f, getattr(body, f))
        if me_.is_admin:
            if body.is_admin is not None and uid != me_.id:
                u.is_admin = body.is_admin
            if body.is_active is not None and uid != me_.id:
                u.is_active = body.is_active
        session.add(u)
        session.commit()
        return _user_dict(u)

    # ------------------------------------------------------------------ projetos
    @app.get("/api/projects")
    def list_projects(user: User = Depends(current_user), session: Session = Depends(get_session)):
        return [_project_dict(p, _project_stats(session, p))
                for p in session.exec(select(Project).order_by(Project.id.desc())).all()]

    @app.post("/api/projects")
    def create_project(body: ProjectIn, user: User = Depends(admin_user), session: Session = Depends(get_session)):
        data = body.model_dump()
        data["classes"] = [c.model_dump() for c in body.classes]
        if not data["classes"]:
            raise HTTPException(400, "Defina pelo menos uma classe.")
        data["image_sources"] = _validate_sources(body.image_sources, body.resolution_m)
        p = Project(**data, created_by=user.id)
        session.add(p)
        session.commit()
        session.refresh(p)
        return _project_dict(p)

    @app.get("/api/projects/{pid}")
    def get_project(pid: int, user: User = Depends(current_user), session: Session = Depends(get_session)):
        p = _project_or_404(session, pid)
        return _project_dict(p, _project_stats(session, p))

    @app.patch("/api/projects/{pid}")
    def patch_project(pid: int, body: ProjectPatch, user: User = Depends(admin_user),
                      session: Session = Depends(get_session)):
        p = _project_or_404(session, pid)
        has_chips = session.exec(select(Chip.id).where(Chip.project_id == pid)).first() is not None
        if has_chips and ((body.chip_size_px and body.chip_size_px != p.chip_size_px) or
                          (body.resolution_m and body.resolution_m != p.resolution_m)):
            raise HTTPException(409, "Tamanho do chip/resolução não podem mudar depois que chips foram gerados.")
        data = body.model_dump(exclude_none=True)
        if "classes" in data:
            data["classes"] = [c.model_dump() for c in body.classes]
        if "image_sources" in data:
            data["image_sources"] = _validate_sources(body.image_sources, body.resolution_m or p.resolution_m)
        for k, v in data.items():
            setattr(p, k, v)
        session.add(p)
        session.commit()
        session.refresh(p)
        return _project_dict(p)

    @app.delete("/api/projects/{pid}")
    def delete_project(pid: int, user: User = Depends(admin_user), session: Session = Depends(get_session)):
        p = _project_or_404(session, pid)
        for model in (Annotation, ChipLock, ExportJob, LocalRaster, ReferenceLayer):
            if model is ChipLock:
                ids = session.exec(select(Chip.id).where(Chip.project_id == pid)).all()
                for lk in session.exec(select(ChipLock).where(ChipLock.chip_id.in_(ids))).all() if ids else []:
                    session.delete(lk)
                continue
            for obj in session.exec(select(model).where(model.project_id == pid)).all():
                session.delete(obj)
        session.commit()
        for c in session.exec(select(Chip).where(Chip.project_id == pid)).all():
            session.delete(c)
        session.delete(p)
        session.commit()
        s = get_settings()
        for sub in ("chips", "exports", "layers", "rasters"):
            shutil.rmtree(s.data_dir / sub / str(pid), ignore_errors=True)
        return {"ok": True}

    # ------------------------------------------------------------------ camadas de referência
    @app.post("/api/projects/{pid}/layers")
    async def upload_layer(pid: int, files: list[UploadFile] = File(...), name: str = Form(""),
                           theme: str = Form(""), class_field: str = Form(""),
                           user: User = Depends(admin_user), session: Session = Depends(get_session)):
        _project_or_404(session, pid)
        payload = [(f.filename or "arquivo", await f.read()) for f in files]
        main = store_upload(payload, get_settings().data_dir / "layers" / str(pid))
        try:
            info = inspect_layer(main, class_field)
        except Exception:
            shutil.rmtree(main.parent, ignore_errors=True)
            raise
        layer = ReferenceLayer(project_id=pid, name=name or main.stem, theme=theme, kind=info["kind"],
                               path=str(main), class_field=class_field if info["kind"] == "vector" else "",
                               crs=info["crs"], n_features=info["n_features"], strata=info["strata"],
                               bounds=info["bounds"], uploaded_by=user.id)
        session.add(layer)
        session.commit()
        session.refresh(layer)
        return {**layer.model_dump(), "fields": info.get("fields", []), "geom_types": info.get("geom_types", [])}

    @app.get("/api/projects/{pid}/layers")
    def list_layers(pid: int, user: User = Depends(current_user), session: Session = Depends(get_session)):
        return [l.model_dump() for l in session.exec(select(ReferenceLayer).where(ReferenceLayer.project_id == pid)).all()]

    @app.get("/api/layers/{lid}/inspect")
    def inspect(lid: int, class_field: str = "", save: bool = False, user: User = Depends(current_user),
                session: Session = Depends(get_session)):
        layer = session.get(ReferenceLayer, lid)
        if not layer:
            raise HTTPException(404, "Camada não encontrada.")
        info = inspect_layer(layer.path, class_field)
        if save and user.is_admin and layer.kind == "vector":
            layer.class_field, layer.strata, layer.n_features = class_field, info["strata"], info["n_features"]
            session.add(layer)
            session.commit()
        return info

    @app.get("/api/layers/{lid}/geojson")
    def layer_geojson(lid: int, max_features: int = Query(3000, le=20000), user: User = Depends(current_user),
                      session: Session = Depends(get_session)):
        layer = session.get(ReferenceLayer, lid)
        if not layer:
            raise HTTPException(404, "Camada não encontrada.")
        if layer.kind == "raster":
            b = layer.bounds
            return {"type": "FeatureCollection", "features": [
                {"type": "Feature", "properties": {"name": layer.name}, "geometry": mapping(box(*b))}]}
        gdf = read_vector(layer.path)
        if len(gdf) > max_features:
            gdf = gdf.sample(max_features, random_state=0)
        gdf["geometry"] = gdf.geometry.simplify(0.0002, preserve_topology=True)
        cols = [layer.class_field] if layer.class_field else []
        out = gdf[cols + ["geometry"]].copy()
        out["stratum"] = gdf[layer.class_field].astype(str) if layer.class_field else "__all__"
        return Response(out[["stratum", "geometry"]].to_json(), media_type="application/geo+json")

    @app.delete("/api/layers/{lid}")
    def delete_layer(lid: int, user: User = Depends(admin_user), session: Session = Depends(get_session)):
        layer = session.get(ReferenceLayer, lid)
        if not layer:
            raise HTTPException(404, "Camada não encontrada.")
        for c in session.exec(select(Chip).where(Chip.layer_id == lid)).all():
            c.layer_id = None
            session.add(c)
        session.delete(layer)
        session.commit()
        shutil.rmtree(Path(layer.path).parent, ignore_errors=True)
        return {"ok": True}

    # ------------------------------------------------------------------ rasters locais
    @app.post("/api/projects/{pid}/rasters")
    async def upload_raster(pid: int, file: UploadFile = File(...), name: str = Form(""),
                            user: User = Depends(admin_user), session: Session = Depends(get_session)):
        _project_or_404(session, pid)
        main = store_upload([(file.filename or "raster.tif", await file.read())],
                            get_settings().data_dir / "rasters" / str(pid))
        if not is_raster(main):
            shutil.rmtree(main.parent, ignore_errors=True)
            raise HTTPException(400, "Envie um GeoTIFF/COG.")
        import rasterio
        from rasterio.warp import transform_bounds
        with rasterio.open(main) as src:
            b = transform_bounds(src.crs, "EPSG:4326", *src.bounds)
            names = [d or str(i) for i, d in enumerate(src.descriptions, start=1)]
            r = LocalRaster(project_id=pid, name=name or main.stem, path=str(main), crs=src.crs.to_string(),
                            count=src.count, bounds=list(b), uploaded_by=user.id)
        session.add(r)
        session.commit()
        session.refresh(r)
        return {**r.model_dump(), "band_names": names}

    @app.get("/api/projects/{pid}/rasters")
    def list_rasters(pid: int, user: User = Depends(current_user), session: Session = Depends(get_session)):
        from .imagery.local import raster_band_names
        return [{**r.model_dump(), "band_names": raster_band_names(r.path)}
                for r in session.exec(select(LocalRaster).where(LocalRaster.project_id == pid)).all()]

    @app.delete("/api/rasters/{rid}")
    def delete_raster(rid: int, user: User = Depends(admin_user), session: Session = Depends(get_session)):
        r = session.get(LocalRaster, rid)
        if not r:
            raise HTTPException(404, "Raster não encontrado.")
        session.delete(r)
        session.commit()
        shutil.rmtree(Path(r.path).parent, ignore_errors=True)
        return {"ok": True}

    # ------------------------------------------------------------------ chips
    @app.post("/api/projects/{pid}/chips/generate")
    def generate(pid: int, body: GenerateIn, user: User = Depends(admin_user),
                 session: Session = Depends(get_session)):
        p = _project_or_404(session, pid)
        layer = session.get(ReferenceLayer, body.layer_id)
        if not layer or layer.project_id != pid:
            raise HTTPException(404, "Camada não encontrada no projeto.")
        params = SamplingParams.from_dict({**body.model_dump(), "class_field": body.class_field
                                           if body.class_field is not None else layer.class_field})
        specs = sample_layer(layer.path, params, p.chip_size_px, p.resolution_m)
        random.Random(body.seed).shuffle(specs)  # intercala estratos na fila de anotação
        for s in specs:
            g = s.grid
            session.add(Chip(project_id=pid, layer_id=layer.id, stratum=s.stratum, lon=s.lon, lat=s.lat,
                             epsg=g.epsg, minx=g.minx, miny=g.miny, maxx=g.maxx, maxy=g.maxy,
                             size_px=g.size_px, resolution_m=g.resolution, reference=s.reference))
        session.commit()
        by = {}
        for s in specs:
            by[s.stratum] = by.get(s.stratum, 0) + 1
        return {"created": len(specs), "by_stratum": by}

    @app.post("/api/projects/{pid}/chips/manual")
    def add_manual_chip(pid: int, body: ManualChipIn, user: User = Depends(current_user),
                        session: Session = Depends(get_session)):
        p = _project_or_404(session, pid)
        layer = session.get(ReferenceLayer, body.layer_id) if body.layer_id else None
        s = manual_chip(body.lon, body.lat, p.chip_size_px, p.resolution_m, layer.path if layer else None,
                        layer.class_field if layer else "")
        g = s.grid
        c = Chip(project_id=pid, layer_id=layer.id if layer else None, stratum=s.stratum, lon=s.lon, lat=s.lat,
                 epsg=g.epsg, minx=g.minx, miny=g.miny, maxx=g.maxx, maxy=g.maxy, size_px=g.size_px,
                 resolution_m=g.resolution, reference=s.reference)
        session.add(c)
        session.commit()
        session.refresh(c)
        return {"id": c.id}

    @app.get("/api/projects/{pid}/chips")
    def list_chips(pid: int, limit: int = Query(5000, le=50000), user: User = Depends(current_user),
                   session: Session = Depends(get_session)):
        p = _project_or_404(session, pid)
        chips = session.exec(select(Chip).where(Chip.project_id == pid).order_by(Chip.id).limit(limit)).all()
        mine = set(session.exec(select(Annotation.chip_id).where(Annotation.project_id == pid,
                                                                 Annotation.user_id == user.id)).all())
        return [{"id": c.id, "lon": c.lon, "lat": c.lat, "stratum": c.stratum, "n_annotations": c.n_annotations,
                 "complete": c.n_annotations >= p.annotators_per_chip, "mine": c.id in mine} for c in chips]

    @app.delete("/api/projects/{pid}/chips")
    def delete_unannotated(pid: int, user: User = Depends(admin_user), session: Session = Depends(get_session)):
        n = 0
        for c in session.exec(select(Chip).where(Chip.project_id == pid, Chip.n_annotations == 0)).all():
            for lk in session.exec(select(ChipLock).where(ChipLock.chip_id == c.id)).all():
                session.delete(lk)
            session.delete(c)
            n += 1
        session.commit()
        return {"deleted": n}

    @app.get("/api/projects/{pid}/next")
    def next_chip(pid: int, stratum: str = "", exclude: int = 0, user: User = Depends(current_user),
                  session: Session = Depends(get_session)):
        p = _project_or_404(session, pid)
        k = p.annotators_per_chip
        cutoff = utcnow() - timedelta(minutes=get_settings().lock_minutes)
        # limpa reservas vencidas
        for lk in session.exec(select(ChipLock).where(ChipLock.locked_at < cutoff)).all():
            session.delete(lk)
        session.commit()
        # retoma chip já reservado pelo usuário
        mine = session.exec(select(ChipLock).join(Chip, Chip.id == ChipLock.chip_id)
                            .where(ChipLock.user_id == user.id, Chip.project_id == pid,
                                   ChipLock.chip_id != exclude)).first()
        if mine:
            done = session.exec(select(Annotation.id).where(Annotation.chip_id == mine.chip_id,
                                                            Annotation.user_id == user.id)).first()
            if not done:
                return {"chip_id": mine.chip_id}
            session.delete(mine)
            session.commit()
        if exclude:
            for lk in session.exec(select(ChipLock).where(ChipLock.chip_id == exclude,
                                                          ChipLock.user_id == user.id)).all():
                session.delete(lk)
            session.commit()
        annotated = select(Annotation.chip_id).where(Annotation.user_id == user.id, Annotation.project_id == pid)
        q = select(Chip).where(Chip.project_id == pid, Chip.n_annotations < k, Chip.id.not_in(annotated),
                               Chip.id != exclude)
        if stratum:
            q = q.where(Chip.stratum == stratum)
        q = q.order_by(Chip.n_annotations.desc(), Chip.id).limit(300)
        for c in session.exec(q).all():
            others = session.exec(select(func.count()).select_from(ChipLock).where(
                ChipLock.chip_id == c.id, ChipLock.user_id != user.id)).one()
            if c.n_annotations + others >= k:
                continue
            session.add(ChipLock(chip_id=c.id, user_id=user.id))
            try:
                session.commit()
            except Exception:
                session.rollback()
                continue
            others = session.exec(select(func.count()).select_from(ChipLock).where(
                ChipLock.chip_id == c.id, ChipLock.user_id != user.id)).one()
            if c.n_annotations + others >= k:  # outro usuário pegou ao mesmo tempo
                lk = session.exec(select(ChipLock).where(ChipLock.chip_id == c.id,
                                                         ChipLock.user_id == user.id)).first()
                if lk:
                    session.delete(lk)
                    session.commit()
                continue
            return {"chip_id": c.id}
        return {"chip_id": None}

    @app.post("/api/chips/{cid}/release")
    def release(cid: int, user: User = Depends(current_user), session: Session = Depends(get_session)):
        for lk in session.exec(select(ChipLock).where(ChipLock.chip_id == cid, ChipLock.user_id == user.id)).all():
            session.delete(lk)
        session.commit()
        return {"ok": True}

    @app.get("/api/chips/{cid}")
    def get_chip(cid: int, user: User = Depends(current_user), session: Session = Depends(get_session)):
        c = _chip_or_404(session, cid)
        p = _project_or_404(session, c.project_id)
        grid = grid_from_chip(c)
        ann = session.exec(select(Annotation).where(Annotation.chip_id == cid,
                                                    Annotation.user_id == user.id)).first()
        my = None
        if ann:
            my = {"id": ann.id, "status": ann.status, "labels": ann.labels, "comment": ann.comment,
                  "source_id": ann.source_id, "features": reference_to_pixels(ann.geometry or {}, grid),
                  "updated_at": ann.updated_at.isoformat()}
        others = []
        if user.is_admin:
            users = {u.id: u.username for u in session.exec(select(User)).all()}
            for a in session.exec(select(Annotation).where(Annotation.chip_id == cid,
                                                           Annotation.user_id != user.id)).all():
                others.append({"annotator": users.get(a.user_id), "status": a.status, "labels": a.labels,
                               "features": reference_to_pixels(a.geometry or {}, grid)})
        return {
            "id": c.id, "project_id": c.project_id, "stratum": c.stratum, "lon": c.lon, "lat": c.lat,
            "epsg": c.epsg, "size_px": c.size_px, "resolution_m": c.resolution_m,
            "bounds": [c.minx, c.miny, c.maxx, c.maxy], "footprint": mapping(grid.footprint_wgs84()),
            "reference": reference_to_pixels(c.reference or {}, grid), "n_annotations": c.n_annotations,
            "annotators_per_chip": p.annotators_per_chip, "my_annotation": my, "other_annotations": others,
        }

    @app.get("/api/chips/{cid}/image.png")
    def chip_png(cid: int, source: str = "", rgb: str = "", stretch: str = "", refresh: bool = False,
                 user: User = Depends(current_user), session: Session = Depends(get_session)):
        c = _chip_or_404(session, cid)
        p = _project_or_404(session, c.project_id)
        src = get_source(p, source or None)
        img = chip_image(session, p, c, src, refresh=refresh)
        bands = [b for b in rgb.split(",") if b] or src["rgb"]
        st = [float(x) for x in stretch.split(",")] if stretch else src.get("stretch")
        return Response(render_png(img, bands, st), media_type="image/png",
                        headers={"Cache-Control": "private, max-age=3600"})

    @app.get("/api/chips/{cid}/image-info")
    def chip_image_info(cid: int, source: str = "", user: User = Depends(current_user),
                        session: Session = Depends(get_session)):
        c = _chip_or_404(session, cid)
        p = _project_or_404(session, c.project_id)
        src = get_source(p, source or None)
        img = chip_image(session, p, c, src)
        return {"source": src, "bands": img.band_names, "meta": img.meta}

    @app.get("/api/chips/{cid}/image.tif")
    def chip_tif(cid: int, source: str = "", user: User = Depends(current_user),
                 session: Session = Depends(get_session)):
        c = _chip_or_404(session, cid)
        p = _project_or_404(session, c.project_id)
        src = get_source(p, source or None)
        chip_image(session, p, c, src)
        return FileResponse(cache_path(p, c, src), media_type="image/tiff",
                            filename=f"chip_{cid:06d}_{src['id']}.tif")

    @app.put("/api/chips/{cid}/annotation")
    def save_annotation(cid: int, body: AnnotationIn, user: User = Depends(current_user),
                        session: Session = Depends(get_session)):
        c = _chip_or_404(session, cid)
        p = _project_or_404(session, c.project_id)
        valid_ids = {int(x["id"]) for x in p.classes or []}
        labels = sorted({int(i) for i in body.labels})
        if any(i not in valid_ids for i in labels):
            raise HTTPException(400, "Rótulo inválido para o projeto.")
        if not p.multiclass and len(labels) > 1:
            raise HTTPException(400, "Este projeto permite apenas uma classe por chip.")
        geom = {"type": "FeatureCollection", "features": []}
        if p.annotation_type == "segmentation" and body.status != "skip":
            geom = _pixel_fc_to_chip(body.features, c, valid_ids)
            labels = sorted({f["properties"]["class_id"] for f in geom["features"]})
            if not p.multiclass and len(labels) > 1:
                raise HTTPException(400, "Este projeto permite apenas uma classe por chip.")
        if body.status == "skip":
            labels = []
        ann = session.exec(select(Annotation).where(Annotation.chip_id == cid,
                                                    Annotation.user_id == user.id)).first()
        if ann is None:
            ann = Annotation(chip_id=cid, project_id=p.id, user_id=user.id)
            c.n_annotations += 1
        ann.source_id, ann.status, ann.labels, ann.geometry = body.source_id, body.status, labels, geom
        ann.comment, ann.updated_at = body.comment, utcnow()
        session.add(ann)
        session.add(c)
        for lk in session.exec(select(ChipLock).where(ChipLock.chip_id == cid, ChipLock.user_id == user.id)).all():
            session.delete(lk)
        session.commit()
        session.refresh(ann)
        return {"id": ann.id, "labels": ann.labels, "status": ann.status, "n_features": len(geom["features"])}

    @app.delete("/api/chips/{cid}/annotation")
    def delete_annotation(cid: int, user: User = Depends(current_user), session: Session = Depends(get_session)):
        c = _chip_or_404(session, cid)
        ann = session.exec(select(Annotation).where(Annotation.chip_id == cid,
                                                    Annotation.user_id == user.id)).first()
        if ann:
            session.delete(ann)
            c.n_annotations = max(0, c.n_annotations - 1)
            session.add(c)
            session.commit()
        return {"ok": True}

    @app.get("/api/projects/{pid}/annotations")
    def list_annotations(pid: int, mine: bool = True, limit: int = Query(200, le=5000),
                         user: User = Depends(current_user), session: Session = Depends(get_session)):
        q = select(Annotation).where(Annotation.project_id == pid)
        if mine or not user.is_admin:
            q = q.where(Annotation.user_id == user.id)
        users = {u.id: u.username for u in session.exec(select(User)).all()}
        rows = session.exec(q.order_by(Annotation.updated_at.desc()).limit(limit)).all()
        return [{"id": a.id, "chip_id": a.chip_id, "annotator": users.get(a.user_id), "status": a.status,
                 "labels": a.labels, "n_features": len((a.geometry or {}).get("features", [])),
                 "updated_at": a.updated_at.isoformat()} for a in rows]

    # ------------------------------------------------------------------ exportação
    @app.post("/api/projects/{pid}/exports")
    def start_export(pid: int, body: ExportIn, user: User = Depends(current_user),
                     session: Session = Depends(get_session)):
        _project_or_404(session, pid)
        from .drive import oauth_enabled, service_account_enabled
        if body.drive and not (user.drive_token or service_account_enabled()):
            raise HTTPException(400, "Conecte o Google Drive antes (ou configure conta de serviço)."
                                if oauth_enabled() else "Google Drive não configurado no servidor.")
        job = ExportJob(project_id=pid, user_id=user.id, options=body.model_dump())
        session.add(job)
        session.commit()
        session.refresh(job)
        from .export import run_export_job
        threading.Thread(target=run_export_job, args=(job.id,), daemon=True).start()
        return job.model_dump()

    @app.get("/api/projects/{pid}/exports")
    def list_exports(pid: int, user: User = Depends(current_user), session: Session = Depends(get_session)):
        q = select(ExportJob).where(ExportJob.project_id == pid)
        if not user.is_admin:
            q = q.where(ExportJob.user_id == user.id)
        out = []
        for j in session.exec(q.order_by(ExportJob.id.desc()).limit(50)).all():
            d = j.model_dump(exclude={"path"})
            d["created_at"] = j.created_at.isoformat()
            d["filename"] = Path(j.path).name if j.path else ""
            d["options"] = {k: v for k, v in (j.options or {}).items() if k != "trace"}
            out.append(d)
        return out

    @app.get("/api/exports/{jid}")
    def get_export(jid: int, user: User = Depends(current_user), session: Session = Depends(get_session)):
        j = session.get(ExportJob, jid)
        if not j or (j.user_id != user.id and not user.is_admin):
            raise HTTPException(404, "Exportação não encontrada.")
        d = j.model_dump(exclude={"path"})
        d["filename"] = Path(j.path).name if j.path else ""
        return d

    @app.get("/api/exports/{jid}/download")
    def download_export(jid: int, user: User = Depends(current_user), session: Session = Depends(get_session)):
        j = session.get(ExportJob, jid)
        if not j or (j.user_id != user.id and not user.is_admin) or j.status != "done" or not j.path:
            raise HTTPException(404, "Arquivo não disponível.")
        return FileResponse(j.path, media_type="application/zip", filename=Path(j.path).name)

    # ------------------------------------------------------------------ Google Drive (OAuth)
    @app.get("/api/drive/authorize")
    def drive_authorize(request: Request, user: User = Depends(current_user)):
        from .drive import authorization_url, oauth_enabled
        if not oauth_enabled():
            raise HTTPException(400, "OAuth do Google Drive não configurado (CHIPS_DRIVE_CLIENT_SECRETS).")
        url, state, verifier = authorization_url()
        request.session["drive_state"] = state
        request.session["drive_verifier"] = verifier
        return {"url": url}

    @app.get("/api/drive/callback")
    def drive_callback(request: Request, user: User = Depends(current_user),
                       session: Session = Depends(get_session)):
        from .drive import exchange_code
        state = request.session.pop("drive_state", None)
        verifier = request.session.pop("drive_verifier", None)
        if not state or request.query_params.get("state") != state:
            raise HTTPException(400, "Estado OAuth inválido.")
        url = get_settings().public_url.rstrip("/") + "/api/drive/callback?" + str(request.query_params)
        user.drive_token = exchange_code(url, state, verifier)
        session.add(user)
        session.commit()
        return RedirectResponse("/#drive=ok")

    @app.post("/api/drive/disconnect")
    def drive_disconnect(user: User = Depends(current_user), session: Session = Depends(get_session)):
        user.drive_token = ""
        session.add(user)
        session.commit()
        return {"ok": True}

    # ------------------------------------------------------------------ frontend
    app.mount("/static", StaticFiles(directory=STATIC), name="static")

    @app.get("/", include_in_schema=False)
    def index():
        return FileResponse(STATIC / "index.html")

    return app


def __getattr__(name):  # permite `uvicorn chipcollector.app:app`
    if name == "app":
        return create_app()
    raise AttributeError(name)
