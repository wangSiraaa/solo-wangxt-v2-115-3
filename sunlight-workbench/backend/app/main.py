"""FastAPI 入口：场景 / 测点 / 分析运行（单日+多日期组）/ 快照 / 单点遮挡追查。"""
from __future__ import annotations

import re
from datetime import date, datetime

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

DATE_PATTERN = re.compile(r"^\d{4}-\d{2}-\d{2}$")


def _parse_date(d: str) -> date:
    """校验单个本地日期；不合法时在执行前抛出带具体原因的 400。"""
    if not DATE_PATTERN.match(d):
        raise HTTPException(
            400, f"日期 {d!r} 格式不合法：应为 YYYY-MM-DD（场景本地日期）")
    try:
        return datetime.strptime(d, "%Y-%m-%d").date()
    except ValueError:
        raise HTTPException(
            400, f"日期 {d!r} 在公历中不存在，请检查月份/日是否有效")


def _validate_step(step_minutes: int) -> None:
    """步长须整除全天 1440 分钟（与连续采样网格口径一致），执行前校验。"""
    if 1440 % step_minutes != 0:
        raise HTTPException(
            400, f"采样步长 {step_minutes} 分钟不能整除全天 1440 分钟；"
                 "请改用 1/2/3/5/10/15/30/60 等整除值")


def _validate_group_dates(dates: list[str]) -> list[date]:
    """多日期组：2~5 个互不重复的真实本地日期，返回升序列表。"""
    if not 2 <= len(dates) <= 5:
        raise HTTPException(
            400, f"多日期分析需要 2～5 个日期，当前提交了 {len(dates)} 个")
    parsed = [_parse_date(d) for d in dates]
    dups = sorted({d.isoformat() for d in parsed if parsed.count(d) > 1})
    if dups:
        raise HTTPException(
            400, f"日期重复：{'、'.join(dups)}；每个日期只能出现一次")
    return sorted(parsed)


class RunRequest(BaseModel):
    scene_id: int
    date: str = Field(..., pattern=r"^\d{4}-\d{2}-\d{2}$")
    step_minutes: int = Field(5, ge=1, le=60)
    point_ids: list[int] | None = None  # 缺省 = 场景全部测点


class RunGroupRequest(BaseModel):
    scene_id: int
    dates: list[str]                      # 2~5 个本地日期，逐项校验见 _validate_group_dates
    step_minutes: int = Field(5, ge=1, le=60)  # 全组统一采样步长
    point_ids: list[int] | None = None


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


def _filter_points(points, point_ids):
    if not point_ids:
        return points
    points = [p for p in points if p.id in point_ids]
    if not points:
        raise HTTPException(400, "point_ids 无匹配测点")
    return points


def _execute_run(db: Session, *, scene, built, pts, snapshot,
                 run_date: date, step_minutes: int,
                 group: models.RunGroup | None = None) -> models.Run:
    """落库一个单日运行。单日入口与多日期组共用本函数，保证两种入口
    同一日期/步长/测点的结果逐样本一致。"""
    run = models.Run(scene_id=scene.id, snapshot_id=snapshot.id,
                     group_id=group.id if group else None,
                     run_date=run_date, step_minutes=step_minutes,
                     params={"point_ids": [int(p.id) for p in pts]},
                     disclaimer=DISCLAIMER)
    db.add(run)
    db.flush()
    for pt in pts:
        r = analyze_point(built, pt, latitude=scene.latitude,
                          longitude=scene.longitude, tz=scene.timezone,
                          date=run_date.isoformat(),
                          north_offset_deg=scene.north_offset_deg,
                          step_minutes=step_minutes)
        db.add(models.RunPointResult(
            run_id=run.id, point_id=int(pt.id),
            hourly_samples=r["hourly_samples"],
            continuous_intervals=r["continuous_intervals"],
            fine_samples=r["fine_samples"], summary=r["summary"]))
    return run


@app.post("/api/analysis/run")
def run_analysis(req: RunRequest, db: Session = Depends(get_db)):
    run_date = _parse_date(req.date)
    _validate_step(req.step_minutes)
    s, buildings, points = _load_scene_bundle(db, req.scene_id)
    points = _filter_points(points, req.point_ids)
    # 1) 快照先行：结果关联快照，场景后续被改动也不影响追溯
    payload = _bundle_payload(s, buildings, points)
    snap = models.Snapshot(scene_id=s.id, payload=payload)
    db.add(snap)
    db.flush()
    built, pts = _geom_from_bundle(buildings, points)
    run = _execute_run(db, scene=s, built=built, pts=pts, snapshot=snap,
                       run_date=run_date, step_minutes=req.step_minutes)
    db.commit()
    return {"run_id": run.id, "snapshot_id": snap.id, "disclaimer": DISCLAIMER}


# ---------- 多日期分析组 ----------

@app.post("/api/analysis/run-group")
def run_analysis_group(req: RunGroupRequest, db: Session = Depends(get_db)):
    """多日期分析：一个场景 + 2~5 个本地日期 + 统一采样步长。

    - 全部校验在执行前完成：任一日期/步长不合法都不会产生部分结果。
    - 整组只建一份场景快照，组内各日期运行共享，不混入提交后的场景编辑。
    """
    dates = _validate_group_dates(req.dates)
    _validate_step(req.step_minutes)
    s, buildings, points = _load_scene_bundle(db, req.scene_id)
    points = _filter_points(points, req.point_ids)
    payload = _bundle_payload(s, buildings, points)
    snap = models.Snapshot(scene_id=s.id, payload=payload)
    db.add(snap)
    db.flush()
    group = models.RunGroup(scene_id=s.id, snapshot_id=snap.id,
                            dates=[d.isoformat() for d in dates],
                            step_minutes=req.step_minutes,
                            params={"point_ids": [p.id for p in points]},
                            disclaimer=DISCLAIMER)
    db.add(group)
    db.flush()
    built, pts = _geom_from_bundle(buildings, points)
    runs = [_execute_run(db, scene=s, built=built, pts=pts, snapshot=snap,
                         run_date=d, step_minutes=req.step_minutes,
                         group=group)
            for d in dates]
    db.commit()
    return {"group_id": group.id, "snapshot_id": snap.id,
            "runs": [{"run_id": r.id, "date": r.run_date.isoformat()}
                     for r in runs],
            "disclaimer": DISCLAIMER}


def _longest_sunlit_interval(intervals: list[dict],
                             step_minutes: int) -> dict | None:
    """连续口径下最长的连续晒到区间（本地起止时间）；全天无晒到则为 None。"""
    sunlit = [iv for iv in intervals if iv["status"] == "sunlit"]
    if not sunlit:
        return None
    best = max(sunlit, key=lambda iv: iv["samples"])
    return {"start": best["start"], "end": best["end"],
            "minutes": best["samples"] * step_minutes}


def _group_json(group: models.RunGroup, runs: list[models.Run]) -> dict:
    """组汇总：每个日期 × 测点的连续口径累计分钟与最长连续区间。

    数据全部来自已落库的运行结果行——重开组或场景后续被编辑都不改变输出。
    """
    return {
        "group_id": group.id, "scene_id": group.scene_id,
        "snapshot_id": group.snapshot_id, "dates": list(group.dates),
        "step_minutes": group.step_minutes, "disclaimer": group.disclaimer,
        "created_at": str(group.created_at),
        "runs": [{
            "run_id": run.id, "date": run.run_date.isoformat(),
            "points": [{
                "point_id": r.point_id,
                "daylight_minutes": r.summary["daylight_minutes"],
                "sunlit_minutes": r.summary["sunlit_minutes"],
                "longest_continuous_sunlit_minutes":
                    r.summary["longest_continuous_sunlit_minutes"],
                "longest_sunlit_interval": _longest_sunlit_interval(
                    r.continuous_intervals, run.step_minutes),
            } for r in sorted(run.results, key=lambda r: r.point_id)],
        } for run in runs],
    }


@app.get("/api/analysis-groups/{group_id}")
def get_analysis_group(group_id: int, db: Session = Depends(get_db)):
    group = db.get(models.RunGroup, group_id)
    if not group:
        raise HTTPException(404, "分析组不存在")
    runs = (db.query(models.Run).filter_by(group_id=group.id)
            .order_by(models.Run.run_date).all())
    return _group_json(group, runs)


@app.get("/api/scenes/{scene_id}/analysis-groups")
def list_analysis_groups(scene_id: int, db: Session = Depends(get_db)):
    """场景下已保存的多日期分析组（页面刷新后由此重新打开）。"""
    groups = (db.query(models.RunGroup).filter_by(scene_id=scene_id)
              .order_by(models.RunGroup.id.desc()).all())
    return [{"group_id": g.id, "dates": list(g.dates),
             "step_minutes": g.step_minutes, "snapshot_id": g.snapshot_id,
             "created_at": str(g.created_at)} for g in groups]


@app.get("/api/analysis/{run_id}")
def get_run(run_id: int, db: Session = Depends(get_db)):
    run = db.get(models.Run, run_id)
    if not run:
        raise HTTPException(404, "运行不存在")
    return {
        "run_id": run.id, "scene_id": run.scene_id,
        "snapshot_id": run.snapshot_id, "group_id": run.group_id,
        "date": str(run.run_date),
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
