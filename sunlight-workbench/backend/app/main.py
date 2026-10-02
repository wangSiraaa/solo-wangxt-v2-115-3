"""FastAPI 入口：场景 / 测点 / 分析运行 / 快照 / 单点遮挡追查。"""
from __future__ import annotations

from datetime import datetime

from fastapi import Depends, FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from geoalchemy2.shape import to_shape
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy.orm import Session

from . import models, seed
from .analysis import MeasurePointGeom, analyze_point
from .db import Base, engine, get_db
from .geometry import BuildingGeom, build_scene
from .solar import enu_to_model, sun_vector_enu, solar_positions
from .geometry import cast_sun_ray
import pandas as pd

app = FastAPI(title="日照分析工作台（合成场景·示例口径）")
app.add_middleware(CORSMiddleware, allow_origins=["*"],
                   allow_methods=["*"], allow_headers=["*"])

DISCLAIMER = ("合成场景 + 示例评价口径输出，未建模遮挡（树木等）见场景说明；"
              "逐时采样≠连续日照时长；本结果不构成规划合规结论。")


@app.on_event("startup")
def startup():
    Base.metadata.create_all(engine)


# ---------- 场景 ----------

class SceneOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: int
    name: str
    description: str
    latitude: float
    longitude: float
    timezone: str
    north_offset_deg: float
    unmodeled_occluders: str


def _scene_json(s: models.Scene) -> dict:
    return SceneOut.model_validate(s).model_dump()


@app.get("/api/scenes")
def list_scenes(db: Session = Depends(get_db)):
    return [_scene_json(s) for s in db.query(models.Scene).all()]


@app.post("/api/scenes/seed")
def seed_scenes(db: Session = Depends(get_db)):
    if db.query(models.Scene).count():
        raise HTTPException(409, "已有场景，拒绝重复种子")
    return {"scene_ids": seed.seed_database(db)}


def _load_scene_bundle(db: Session, scene_id: int):
    s = db.get(models.Scene, scene_id)
    if not s:
        raise HTTPException(404, "场景不存在")
    buildings = db.query(models.Building).filter_by(scene_id=scene_id).all()
    points = db.query(models.MeasurePoint).filter_by(scene_id=scene_id).all()
    return s, buildings, points


def _bundle_payload(s, buildings, points) -> dict:
    """场景完整 JSON（即快照内容，前端渲染也用它）。"""
    return {
        "scene": _scene_json(s),
        "buildings": [{
            "id": b.id, "name": b.name, "kind": b.kind, "color": b.color,
            "footprint": list(to_shape(b.footprint).exterior.coords)[:-1],
            "base_height": b.base_height, "top_height": b.top_height,
        } for b in buildings],
        "points": [{
            "id": p.id, "name": p.name, "window_id": p.window_id,
            "position": list(to_shape(p.geom).coords[0]), "normal": p.normal,
        } for p in points],
    }


@app.get("/api/scenes/{scene_id}")
def get_scene(scene_id: int, db: Session = Depends(get_db)):
    return _bundle_payload(*_load_scene_bundle(db, scene_id))


# ---------- 分析 ----------

class RunRequest(BaseModel):
    scene_id: int
    date: str = Field(..., pattern=r"^\d{4}-\d{2}-\d{2}$")
    step_minutes: int = Field(5, ge=1, le=60)
    point_ids: list[int] | None = None  # 缺省 = 场景全部测点


def _geom_from_bundle(buildings, points):
    geoms = [BuildingGeom(name=b.name,
                          footprint=[tuple(c) for c in
                                     to_shape(b.footprint).exterior.coords][:-1],
                          base_height=b.base_height, top_height=b.top_height)
             for b in buildings]
    built = build_scene(geoms)
    pts = [MeasurePointGeom(id=str(p.id), name=p.name,
                            position=tuple(to_shape(p.geom).coords[0]),
                            normal=tuple(p.normal))
           for p in points]
    return built, pts


@app.post("/api/analysis/run")
def run_analysis(req: RunRequest, db: Session = Depends(get_db)):
    s, buildings, points = _load_scene_bundle(db, req.scene_id)
    if req.point_ids:
        points = [p for p in points if p.id in req.point_ids]
        if not points:
            raise HTTPException(400, "point_ids 无匹配测点")
    # 1) 快照先行：结果关联快照，场景后续被改动也不影响追溯
    payload = _bundle_payload(s, buildings, points)
    snap = models.Snapshot(scene_id=s.id, payload=payload)
    db.add(snap)
    db.flush()
    run = models.Run(scene_id=s.id, snapshot_id=snap.id,
                     run_date=datetime.strptime(req.date, "%Y-%m-%d").date(),
                     step_minutes=req.step_minutes,
                     params={"point_ids": [p.id for p in points]},
                     disclaimer=DISCLAIMER)
    db.add(run)
    db.flush()
    built, pts = _geom_from_bundle(buildings, points)
    for pt in pts:
        r = analyze_point(built, pt, latitude=s.latitude, longitude=s.longitude,
                          tz=s.timezone, date=req.date,
                          north_offset_deg=s.north_offset_deg,
                          step_minutes=req.step_minutes)
        db.add(models.RunPointResult(
            run_id=run.id, point_id=int(pt.id),
            hourly_samples=r["hourly_samples"],
            continuous_intervals=r["continuous_intervals"],
            fine_samples=r["fine_samples"], summary=r["summary"]))
    db.commit()
    return {"run_id": run.id, "snapshot_id": snap.id, "disclaimer": DISCLAIMER}


@app.get("/api/analysis/{run_id}")
def get_run(run_id: int, db: Session = Depends(get_db)):
    run = db.get(models.Run, run_id)
    if not run:
        raise HTTPException(404, "运行不存在")
    return {
        "run_id": run.id, "scene_id": run.scene_id,
        "snapshot_id": run.snapshot_id, "date": str(run.run_date),
        "step_minutes": run.step_minutes, "disclaimer": run.disclaimer,
        "results": [{
            "point_id": r.point_id,
            "summary": r.summary,
            "hourly_samples": r.hourly_samples,
            "continuous_intervals": r.continuous_intervals,
            "fine_samples": r.fine_samples,
        } for r in run.results],
    }


@app.get("/api/analysis/{run_id}/points/{point_id}/trace")
def trace_point(run_id: int, point_id: int, time: str | None = None,
                db: Session = Depends(get_db)):
    """单点追查：返回该点逐样本遮挡物；给定 time 时只返回该时刻。"""
    r = (db.query(models.RunPointResult)
         .filter_by(run_id=run_id, point_id=point_id).first())
    if not r:
        raise HTTPException(404, "结果不存在")
    samples = r.fine_samples
    if time:
        samples = [s for s in samples if s["time"].startswith(time)]
        if not samples:
            raise HTTPException(404, "该时刻无采样（注意步长与本地时区）")
    shaded = [s for s in samples if s["status"] == "shaded"]
    return {
        "point_id": point_id,
        "queried": len(samples), "shaded": len(shaded),
        "occluders": sorted({s["occluder"] for s in shaded}),
        "samples": samples,
        "note": "遮挡物名称来自场景快照几何；未建模遮挡（树木等）见场景说明。",
    }


# ---------- 快照 ----------

@app.get("/api/snapshots/{snapshot_id}")
def get_snapshot(snapshot_id: int, db: Session = Depends(get_db)):
    snap = db.get(models.Snapshot, snapshot_id)
    if not snap:
        raise HTTPException(404, "快照不存在")
    return {"snapshot_id": snap.id, "created_at": str(snap.created_at),
            "payload": snap.payload}


# ---------- 太阳路径（前端可视化） ----------

@app.get("/api/scenes/{scene_id}/sunpath")
def sunpath(scene_id: int, date: str, db: Session = Depends(get_db)):
    s = db.get(models.Scene, scene_id)
    if not s:
        raise HTTPException(404, "场景不存在")
    times = pd.date_range(pd.Timestamp(date, tz=s.timezone),
                          periods=96, freq="15min")
    pos = solar_positions(s.latitude, s.longitude, s.timezone, times)
    out = []
    for t, row in zip(times, pos.itertuples()):
        if row.apparent_elevation <= 0:
            continue
        d = enu_to_model(sun_vector_enu(row.apparent_elevation, row.azimuth),
                         s.north_offset_deg)
        out.append({"time": t.isoformat(), "dir": [round(float(v), 5) for v in d],
                    "elevation": round(float(row.apparent_elevation), 2),
                    "azimuth": round(float(row.azimuth), 2)})
    return {"date": date, "timezone": s.timezone, "points": out}
