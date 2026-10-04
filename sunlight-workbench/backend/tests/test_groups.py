"""多日期分析组的端到端逻辑测试（SQLite + 几何列降级为 WKB 二进制）。

PostGIS 空间 DDL 不可用于 SQLite，但几何列只存 WKB：测试中把
Geography/Geometry 列替换为 LargeBinary，写入 WKBElement.data 原始字节，
读取时包回 WKBElement，即可在无 PostgreSQL 环境下完整走通
快照→单日/多日期分析→组汇总→下钻→场景编辑不影响已存组 的逻辑。

验收点对应：
- 三个日期的组汇总与各自单日 analyze_point 结果一致；
- 组内所有 run 共用同一次提交的快照；
- 提交后再改场景，已保存组仍显示原有数据；
- 日期不合法在执行前（建快照/写库之前）返回 400 及原因；
- 组明细返回的每日 run_id 可直接走 GET /api/analysis/{id} 下钻。
"""
from datetime import date

import pytest
import shapely.wkb
from fastapi import HTTPException
from geoalchemy2.elements import WKBElement
from geoalchemy2.shape import from_shape, to_shape
from shapely.geometry import Point, Polygon
from sqlalchemy import LargeBinary, create_engine
from sqlalchemy.orm import sessionmaker

# 导入前先把空间列降级为普通二进制列（仅影响本测试模块内的 metadata）
from app import models as M
M.Scene.__table__.c.anchor.type = LargeBinary()
M.Building.__table__.c.footprint.type = LargeBinary()
M.MeasurePoint.__table__.c.geom.type = LargeBinary()

from app import main as api  # noqa: E402
from app.analysis import analyze_point, longest_sunlit_interval  # noqa: E402
from app.seed import LAT, LON, TZ, scene_specs  # noqa: E402


def _wkb(geom):
    return from_shape(geom, srid=0).data


def _wrap(blob):
    return WKBElement(bytes(blob), srid=0)


@pytest.fixture()
def db(monkeypatch):
    engine = create_engine("sqlite:///:memory:")
    M.Base.metadata.create_all(engine)
    Session = sessionmaker(bind=engine)
    s = Session()
    spec = scene_specs()[0]
    scene = M.Scene(
        name=spec["name"], description=spec["description"],
        latitude=LAT, longitude=LON, timezone=TZ,
        north_offset_deg=spec["north_offset_deg"],
        anchor=_wkb(Point(LON, LAT)),
        unmodeled_occluders="树木未建模")
    s.add(scene)
    s.flush()
    for b in spec["buildings"]:
        s.add(M.Building(
            scene_id=scene.id, name=b["name"], kind=b["kind"], color=b["color"],
            footprint=_wkb(Polygon(b["footprint"])),
            base_height=b["base_height"], top_height=b["top_height"]))
    for p in spec["points"]:
        s.add(M.MeasurePoint(
            scene_id=scene.id, name=p["name"], window_id=p["window_id"],
            geom=_wkb(Point(*p["position"])), normal=list(p["normal"])))
    s.commit()

    # 让 main 里的 to_shape 能处理裸字节
    import app.main as m
    monkeypatch.setattr(m, "to_shape", lambda v: to_shape(v if isinstance(v, WKBElement) else _wrap(v)))
    yield scene.id, s
    s.close()


def test_invalid_dates_rejected_before_execution(db):
    scene_id, s = db
    for bad in ["2026-02-30", "2026/03/20", "not-a-date", "2026-13-01"]:
        with pytest.raises(HTTPException) as ei:
            api.parse_local_date(bad, TZ)
        assert ei.value.status_code == 400 and bad in ei.value.detail

    # 整组：日期个数越界 / 重复 / 含非法日期都必须在任何写入之前失败
    snaps_before = s.query(M.Snapshot).count()
    with pytest.raises(HTTPException) as ei:
        api.create_group(api.GroupRequest(scene_id=scene_id,
                                          dates=["2026-03-20"]), db=s)
    assert ei.value.status_code == 400
    with pytest.raises(HTTPException) as ei:
        api.create_group(api.GroupRequest(scene_id=scene_id,
                                          dates=["2026-03-20", "2026-02-30"]),
                         db=s)
    assert ei.value.status_code == 400 and "2026-02-30" in ei.value.detail
    with pytest.raises(HTTPException) as ei:
        api.create_group(api.GroupRequest(scene_id=scene_id,
                                          dates=["2026-03-20", "2026-03-20"]),
                         db=s)
    assert ei.value.status_code == 400 and "重复" in ei.value.detail
    with pytest.raises(HTTPException) as ei:
        api.create_group(api.GroupRequest(scene_id=scene_id,
                                          dates=["2026-03-20", "2026-06-21"],
                                          step_minutes=13), db=s)
    assert ei.value.status_code == 400 and "整除 1440" in ei.value.detail
    # 没有任何快照/运行被创建
    assert s.query(M.Snapshot).count() == snaps_before
    assert s.query(M.Run).count() == 0


def test_group_matches_independent_single_day_results(db):
    scene_id, s = db
    dates = ["2026-12-22", "2026-03-20", "2026-06-21"]  # 冬至/春分/夏至
    resp = api.create_group(api.GroupRequest(
        scene_id=scene_id, dates=dates, step_minutes=10), db=s)

    g = s.get(M.AnalysisGroup, resp["group_id"])
    assert len(g.runs) == 3
    # 整组共用同一次提交时的一个快照
    assert {r.snapshot_id for r in g.runs} == {resp["snapshot_id"]}
    assert g.snapshot_id == resp["snapshot_id"]

    detail = api.get_group(g.id, db=s)
    # 成员顺序即提交时的日期顺序（冬至/春分/夏至），不做日历重排
    assert detail["dates"] == dates
    assert detail["step_minutes"] == 10

    # 独立重算（不走组管线）的单日结果必须与组汇总逐点一致
    scene, buildings, points = api._load_scene_bundle(s, scene_id)
    built, pts = api._geom_from_bundle(buildings, points)
    run_ids = {m["date"]: m["run_id"] for m in detail["members"]}
    for member in detail["members"]:
        solo = {p.id: analyze_point(built, pt, latitude=LAT, longitude=LON, tz=TZ,
                                    date=member["date"],
                                    north_offset_deg=0.0, step_minutes=10)
                for p, pt in zip(points, pts)}
        got = {p["point_id"]: p for p in member["points"]}
        assert set(got) == {p.id for p in points}
        for pid, row in got.items():
            r = solo[pid]
            assert row["sunlit_minutes"] == r["summary"]["sunlit_minutes"]
            assert row["longest_continuous_sunlit_minutes"] == \
                r["summary"]["longest_continuous_sunlit_minutes"]
            assert row["longest_sunlit_interval"] == longest_sunlit_interval(
                r["fine_samples"])

        # 组里的 run 就是普通单日 run：下钻接口返回完整逐日数据
        full = api.get_run(run_ids[member["date"]], db=s)
        assert full["group_id"] == g.id and full["date"] == member["date"]
        assert len(full["results"]) == len(points)
        assert len(full["results"][0]["hourly_samples"]) == 24  # 整点快览仍保留
        # trace 下钻可用
        tr = api.trace_point(run_ids[member["date"]], points[0].id, db=s)
        assert tr["queried"] == len(full["results"][0]["fine_samples"])


def test_editing_scene_after_submit_does_not_change_group(db):
    scene_id, s = db
    resp = api.create_group(api.GroupRequest(
        scene_id=scene_id, dates=["2026-12-22", "2026-06-21"],
        step_minutes=15), db=s)
    before = api.get_group(resp["group_id"], db=s)
    snap_id = resp["snapshot_id"]

    # 提交后再改场景几何：把南楼加高到 80 m（后来编辑的几何）
    b1 = s.query(M.Building).filter_by(scene_id=scene_id,
                                       name="B1_南侧板楼").one()
    b1.top_height = 80.0
    s.commit()

    after = api.get_group(resp["group_id"], db=s)
    assert after == before  # 已保存的组仍显示原有数据
    # 快照本身也是提交时的内容（南楼 35 m）
    snap_buildings = {b["name"]: b for b in
                      api.get_snapshot(snap_id, db=s)["payload"]["buildings"]}
    assert snap_buildings["B1_南侧板楼"]["top_height"] == 35

    # 组列表可重新打开
    listed = api.list_scene_groups(scene_id, db=s)
    assert [g["group_id"] for g in listed] == [resp["group_id"]]
    assert listed[0]["dates"] == ["2026-12-22", "2026-06-21"]


def test_independent_single_day_run_has_no_group(db):
    scene_id, s = db
    resp = api.run_analysis(api.RunRequest(
        scene_id=scene_id, date="2026-03-20", step_minutes=10), db=s)
    full = api.get_run(resp["run_id"], db=s)
    assert full["group_id"] is None

    # 单日入口同样在执行前拒绝不合法日期/步长（与多日期同一套 400 原因口径）
    with pytest.raises(HTTPException) as ei:
        api.run_analysis(api.RunRequest(
            scene_id=scene_id, date="2026-02-30", step_minutes=10), db=s)
    assert ei.value.status_code == 400 and "2026-02-30" in ei.value.detail
    with pytest.raises(HTTPException) as ei:
        api.run_analysis(api.RunRequest(
            scene_id=scene_id, date="2026-03-20", step_minutes=7), db=s)
    assert ei.value.status_code == 400
    assert s.query(M.Snapshot).count() == 1  # 只有上面那次成功运行的快照
