"""FastAPI 入口：场景 / 测点 / 分析运行 / 快照 / 单点遮挡追查 / 多日期分析组。"""
from __future__ import annotations

import re
from datetime import datetime
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from fastapi import Depends, FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from geoalchemy2.shape import to_shape
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy.orm import Session

from . import models, seed
from .analysis import MeasurePointGeom, analyze_point, longest_sunlit_interval
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
    # 轻量幂等迁移：旧库（analysis_groups/runs.group_id 之前创建）补列
    from sqlalchemy import inspect, text
    if engine.dialect.name != "postgresql":
        return  # SQLite 等测试库由 create_all 直接建出新表
    insp = inspect(engine)
    with engine.begin() as conn:
        if "analysis_groups" not in insp.get_table_names():
            conn.execute(text(
                "CREATE TABLE analysis_groups ("
                "id SERIAL PRIMARY KEY,"
                "scene_id INTEGER REFERENCES scenes(id) ON DELETE CASCADE,"
                "snapshot_id INTEGER NOT NULL REFERENCES snapshots(id),"
                "step_minutes INTEGER NOT NULL DEFAULT 5,"
                "params JSON DEFAULT '{}',"
                "disclaimer TEXT,"
                "created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP)"))
            conn.execute(text(
                "CREATE INDEX ix_analysis_groups_scene_id "
                "ON analysis_groups (scene_id)"))
        if "group_id" not in {c["name"] for c in insp.get_columns("runs")}:
            conn.execute(text(
                "ALTER TABLE runs ADD COLUMN group_id INTEGER "
                "REFERENCES analysis_groups(id) ON DELETE CASCADE"))
            conn.execute(text("CREATE INDEX ix_runs_group_id ON runs (group_id)"))


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

DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")


class RunRequest(BaseModel):
    # 日期格式/真实性由 parse_local_date 统一校验并返回中文 400 原因
    scene_id: int
    date: str
    step_minutes: int = Field(5, ge=1, le=60)
    point_ids: list[int] | None = None  # 缺省 = 场景全部测点


class GroupRequest(BaseModel):
    """多日期分析：一次提交 2～5 个本地日期 + 统一采样步长。

    日期个数（2～5）由 _validate_group_dates 在执行前统一校验，
    错误信息与格式/日历/重复等原因保持同一套中文 400 口径。
    """
    scene_id: int
    dates: list[str]
    step_minutes: int = Field(5, ge=1, le=60)
    point_ids: list[int] | None = None


def parse_local_date(text: str, tz: str) -> datetime.date:
    """执行前校验本地日期：格式/真实日历日期/场景时区可用性，不合法即给原因。"""
    if not DATE_RE.match(text or ""):
        raise HTTPException(400, f"日期 {text!r} 不合法：需要 YYYY-MM-DD 格式")
    try:
        d = datetime.strptime(text, "%Y-%m-%d").date()
    except ValueError:
        raise HTTPException(400, f"日期 {text!r} 不合法：不是真实日历日期")
    # 采样按场景本地时区生成，先确认时区名可用，避免执行中途才失败
    try:
        ZoneInfo(tz)
    except ZoneInfoNotFoundError:
        raise HTTPException(400, f"场景时区 {tz!r} 不合法，无法按本地日期采样")
    return d


def _select_points(points, point_ids):
    if point_ids:
        chosen = [p for p in points if p.id in point_ids]
        if not chosen:
            raise HTTPException(400, "point_ids 无匹配测点")
        return chosen
    return points


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


def _run_one_day(db, s, buildings, points, built, pts, date_text,
                 step_minutes, snapshot, group=None):
    """单日分析：快照由调用方先行生成，单日/多日期复用同一套计算与落库逻辑。"""
    run = models.Run(scene_id=s.id, snapshot_id=snapshot.id,
                     group_id=group.id if group else None,
                     run_date=datetime.strptime(date_text, "%Y-%m-%d").date(),
                     step_minutes=step_minutes,
                     params={"point_ids": [p.id for p in points]},
                     disclaimer=DISCLAIMER)
    db.add(run)
    db.flush()
    for pt in pts:
        r = analyze_point(built, pt, latitude=s.latitude, longitude=s.longitude,
                          tz=s.timezone, date=date_text,
                          north_offset_deg=s.north_offset_deg,
                          step_minutes=step_minutes)
        db.add(models.RunPointResult(
            run_id=run.id, point_id=int(pt.id),
            hourly_samples=r["hourly_samples"],
            continuous_intervals=r["continuous_intervals"],
            fine_samples=r["fine_samples"], summary=r["summary"]))
    return run


@app.post("/api/analysis/run")
def run_analysis(req: RunRequest, db: Session = Depends(get_db)):
    s, buildings, points = _load_scene_bundle(db, req.scene_id)
    points = _select_points(points, req.point_ids)
    parse_local_date(req.date, s.timezone)  # 不合法 → 执行前 400 并说明原因
    if 1440 % req.step_minutes != 0:
        raise HTTPException(400, f"步长 {req.step_minutes} 分钟不合法：必须整除 1440")
    # 1) 快照先行：结果关联快照，场景后续被改动也不影响追溯
    payload = _bundle_payload(s, buildings, points)
    snap = models.Snapshot(scene_id=s.id, payload=payload)
    db.add(snap)
    db.flush()
    built, pts = _geom_from_bundle(buildings, points)
    run = _run_one_day(db, s, buildings, points, built, pts,
                       req.date, req.step_minutes, snap)
    db.commit()
    return {"run_id": run.id, "snapshot_id": snap.id, "disclaimer": DISCLAIMER}


# ---------- 多日期分析组 ----------

def _validate_group_dates(date_texts: list[str], tz: str) -> list:
    """全部日期先校验再执行：格式、真实日历、2～5 个、不重复（按提交顺序）。"""
    if not (2 <= len(date_texts) <= 5):
        raise HTTPException(400, f"多日期分析需要 2～5 个日期，收到 {len(date_texts)} 个")
    seen, dates = set(), []
    for text in date_texts:
        d = parse_local_date(text, tz)
        if d in seen:
            raise HTTPException(400, f"日期 {text} 重复：组内日期必须互不相同")
        seen.add(d)
        dates.append(d)
    return dates


def _group_member_dict(run: models.Run, name_by_point: dict) -> dict:
    return {
        "run_id": run.id,
        "date": str(run.run_date),
        "points": [{
            "point_id": r.point_id,
            "point_name": name_by_point.get(r.point_id, f"#{r.point_id}"),
            "sunlit_minutes": r.summary["sunlit_minutes"],
            "longest_continuous_sunlit_minutes":
                r.summary["longest_continuous_sunlit_minutes"],
            "longest_sunlit_interval": longest_sunlit_interval(r.fine_samples),
        } for r in sorted(run.results, key=lambda x: x.point_id)],
    }


def _group_json(g: models.AnalysisGroup) -> dict:
    # 点名称从提交时的快照取，场景后来改名/删点不影响组的展示
    snap_points = {p["id"]: p["name"] for p in g.snapshot.payload["points"]}
    # 成员按提交时的日期顺序（params.dates），保证与用户输入及 run_ids 一致
    order = g.params.get("dates") if g.params else None
    by_date = {str(r.run_date): r for r in g.runs}
    runs = ([by_date[d] for d in order if d in by_date]
            if order else sorted(g.runs, key=lambda r: r.run_date))
    return {
        "group_id": g.id,
        "scene_id": g.scene_id,
        "snapshot_id": g.snapshot_id,
        "step_minutes": g.step_minutes,
        "dates": [str(r.run_date) for r in runs],
        "created_at": str(g.created_at),
        "disclaimer": g.disclaimer,
        "members": [_group_member_dict(r, snap_points) for r in runs],
    }


@app.post("/api/analysis/groups")
def create_group(req: GroupRequest, db: Session = Depends(get_db)):
    s, buildings, points = _load_scene_bundle(db, req.scene_id)
    points = _select_points(points, req.point_ids)
    if 1440 % req.step_minutes != 0:
        raise HTTPException(400, f"步长 {req.step_minutes} 分钟不合法：必须整除 1440")
    # 全部日期合法后才建快照/写库：任一不合法在执行前返回原因
    _validate_group_dates(req.dates, s.timezone)
    # 一次提交 = 一个快照：整组结果锁死在提交时的场景配置上
    payload = _bundle_payload(s, buildings, points)
    snap = models.Snapshot(scene_id=s.id, payload=payload)
    db.add(snap)
    db.flush()
    group = models.AnalysisGroup(
        scene_id=s.id, snapshot_id=snap.id,
        step_minutes=req.step_minutes,
        params={"dates": list(req.dates),
                "point_ids": [p.id for p in points]},
        disclaimer=DISCLAIMER)
    db.add(group)
    db.flush()
    built, pts = _geom_from_bundle(buildings, points)
    for date_text in req.dates:  # 复用与单日完全相同的分析与落库逻辑
        _run_one_day(db, s, buildings, points, built, pts,
                     date_text, req.step_minutes, snap, group=group)
    db.commit()
    by_date = {str(r.run_date): r for r in group.runs}
    ordered_runs = [by_date[d] for d in req.dates]
    return {"group_id": group.id, "snapshot_id": snap.id,
            "run_ids": [r.id for r in ordered_runs],
            "disclaimer": DISCLAIMER}


@app.get("/api/analysis/groups/{group_id}")
def get_group(group_id: int, db: Session = Depends(get_db)):
    g = db.get(models.AnalysisGroup, group_id)
    if not g:
        raise HTTPException(404, "分析组不存在")
    return _group_json(g)


@app.get("/api/scenes/{scene_id}/groups")
def list_scene_groups(scene_id: int, db: Session = Depends(get_db)):
    """场景下已保存的分析组（刷新后可重新打开并下钻）。"""
    if not db.get(models.Scene, scene_id):
        raise HTTPException(404, "场景不存在")
    groups = (db.query(models.AnalysisGroup)
              .filter_by(scene_id=scene_id)
              .order_by(models.AnalysisGroup.created_at.desc()).all())
    return [{
        "group_id": g.id,
        "snapshot_id": g.snapshot_id,
        "step_minutes": g.step_minutes,
        "dates": list((g.params or {}).get("dates",
                     [str(r.run_date) for r in sorted(g.runs, key=lambda x: x.run_date)])),
        "created_at": str(g.created_at),
    } for g in groups]


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
