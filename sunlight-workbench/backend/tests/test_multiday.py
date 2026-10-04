"""多日期分析组测试。

验收口径对应：
- 日期/步长不合法 → 执行前 400 并说明原因，且不产生任何落库记录；
- 三个日期的组汇总 == 各自单日运行结果（同一执行路径，逐样本一致）；
- 整组共享一份快照；提交后场景被编辑，已保存的组仍显示原有数据；
- 落库后可重新打开组（模拟刷新）并下钻到任一单日运行。

组/运行/结果表不含几何列，可在内存 SQLite 建表；场景包加载用
monkeypatch 替换（真实 PostGIS 读写由 docker-compose 集成环境覆盖）。
"""
from types import SimpleNamespace as NS

import pytest
from fastapi import HTTPException
from geoalchemy2.shape import from_shape
from shapely.geometry import Point
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

import app.main as main
from app import models
from app.main import (_parse_date, _validate_step, _validate_group_dates,
                      RunGroupRequest, RunRequest)
from tests.test_api_logic import _fake_rows

DATES3 = ["2025-12-21", "2026-03-20", "2026-06-21"]  # 冬至/春分/夏至


def _sqlite_session():
    # StaticPool + check_same_thread=False：全线程共享同一内存库
    #（TestClient 在线程池里执行端点，默认池会给它换一个空库）
    from sqlalchemy.pool import StaticPool
    engine = create_engine("sqlite+pysqlite:///:memory:",
                           poolclass=StaticPool,
                           connect_args={"check_same_thread": False})
    for t in (models.Snapshot.__table__, models.RunGroup.__table__,
              models.Run.__table__, models.RunPointResult.__table__):
        t.create(engine)
    return sessionmaker(bind=engine)()


def _pick_points(points):
    """南窗低层 / 南窗三层 / 东窗各取一个，覆盖方位与楼层差异。"""
    return [points[i] for i in (0, 3, 5)]


@pytest.fixture(scope="module")
def group_ctx():
    """执行一次三日期组分析（含三个对照单日运行），供多个断言复用。"""
    scene, buildings, points = _fake_rows()
    points = _pick_points(points)
    orig = main._load_scene_bundle
    main._load_scene_bundle = lambda db, sid: (scene, buildings, points)
    try:
        db = _sqlite_session()
        resp = main.run_analysis_group(
            RunGroupRequest(scene_id=scene.id, dates=DATES3, step_minutes=5),
            db)
        # 对照：同参数三个单日运行（走单日入口）
        singles = {}
        for d in DATES3:
            r = main.run_analysis(
                RunRequest(scene_id=scene.id, date=d, step_minutes=5), db)
            singles[d] = main.get_run(r["run_id"], db)
        yield NS(db=db, scene=scene, buildings=buildings, points=points,
                 resp=resp, singles=singles)
    finally:
        main._load_scene_bundle = orig


# ---------- 执行前校验（说明原因，不落库） ----------

@pytest.mark.parametrize("dates, needle", [
    (["2026-01-15"], "2～5"),                                  # 太少
    ([f"2026-01-1{i}" for i in range(6)], "2～5"),             # 太多
    (["2026-02-30", "2026-03-20"], "不存在"),                  # 公历不存在
    (["2026/01/15", "2026-03-20"], "YYYY-MM-DD"),              # 格式错
    (["2026-1-5", "2026-03-20"], "YYYY-MM-DD"),                # 未补零
    (["2026-01-15", "2026-01-15"], "重复"),                    # 重复
])
def test_validate_group_dates_rejects_with_reason(dates, needle):
    with pytest.raises(HTTPException) as ei:
        _validate_group_dates(dates)
    assert ei.value.status_code == 400
    assert needle in ei.value.detail


def test_validate_group_dates_sorted():
    got = _validate_group_dates(["2026-06-21", "2025-12-21", "2026-03-20"])
    assert [d.isoformat() for d in got] == DATES3


def test_validate_step_must_divide_day():
    for ok in (1, 2, 3, 5, 10, 15, 30, 60):
        _validate_step(ok)
    with pytest.raises(HTTPException) as ei:
        _validate_step(7)
    assert ei.value.status_code == 400 and "1440" in ei.value.detail


def test_parse_date_single_day_also_guarded():
    assert _parse_date("2026-01-15").isoformat() == "2026-01-15"
    with pytest.raises(HTTPException):
        _parse_date("2026-02-30")


def test_invalid_date_aborts_before_any_write():
    """任一日期不合法：执行前 400，且不产生组/快照/运行等任何记录。"""
    db = _sqlite_session()
    req = RunGroupRequest(scene_id=1,
                          dates=["2026-03-20", "2026-02-30", "2026-06-21"],
                          step_minutes=5)
    with pytest.raises(HTTPException) as ei:
        main.run_analysis_group(req, db)
    assert ei.value.status_code == 400
    assert "2026-02-30" in ei.value.detail
    for t in (models.RunGroup, models.Snapshot, models.Run,
              models.RunPointResult):
        assert db.query(t).count() == 0


def test_invalid_step_aborts_before_any_write():
    db = _sqlite_session()
    req = RunGroupRequest(scene_id=1, dates=["2026-03-20", "2026-06-21"],
                          step_minutes=7)
    with pytest.raises(HTTPException) as ei:
        main.run_analysis_group(req, db)
    assert "1440" in ei.value.detail
    assert db.query(models.RunGroup).count() == 0


# ---------- 组执行与单日入口一致性 ----------

def test_group_summary_matches_single_day_runs(group_ctx):
    """验收：三个日期的组汇总与各自单日结果一致（逐点逐指标）。"""
    g = main.get_analysis_group(group_ctx.resp["group_id"], group_ctx.db)
    assert [r["date"] for r in g["runs"]] == DATES3  # 升序
    for run in g["runs"]:
        single = group_ctx.singles[run["date"]]
        for p in run["points"]:
            s = next(r for r in single["results"]
                     if r["point_id"] == p["point_id"])
            assert p["sunlit_minutes"] == s["summary"]["sunlit_minutes"]
            assert p["daylight_minutes"] == s["summary"]["daylight_minutes"]
            assert (p["longest_continuous_sunlit_minutes"]
                    == s["summary"]["longest_continuous_sunlit_minutes"])
            iv = p["longest_sunlit_interval"]
            if p["longest_continuous_sunlit_minutes"] == 0:
                assert iv is None  # 全天无晒到（如冬至低层南窗）
                continue
            assert iv["minutes"] == p["longest_continuous_sunlit_minutes"]
            # 最长连续区间落在连续口径的晒到时段内
            assert any(iv["start"] >= x["start"] and iv["end"] <= x["end"]
                       for x in s["continuous_intervals"]
                       if x["status"] == "sunlit")


def test_group_runs_share_one_snapshot(group_ctx):
    """整组结果来自同一次提交：所有运行共享同一快照与组 ID。"""
    db = group_ctx.db
    runs = db.query(models.Run).filter_by(
        group_id=group_ctx.resp["group_id"]).all()
    assert len(runs) == 3
    assert {r.snapshot_id for r in runs} == {group_ctx.resp["snapshot_id"]}
    assert {r.group_id for r in runs} == {group_ctx.resp["group_id"]}
    # 组内快照与单日运行的快照不是同一份（各自提交各自快照）
    single_snap = {group_ctx.singles[d]["snapshot_id"] for d in DATES3}
    assert group_ctx.resp["snapshot_id"] not in single_snap


def test_group_run_drilldown_trace_available(group_ctx):
    """下钻：组内任一单日运行可按既有接口取全量结果与遮挡追查。"""
    run_id = group_ctx.resp["runs"][0]["run_id"]
    full = main.get_run(run_id, group_ctx.db)
    assert full["group_id"] == group_ctx.resp["group_id"]
    pid = full["results"][0]["point_id"]
    tr = main.trace_point(run_id, pid, db=group_ctx.db)
    assert tr["queried"] > 0


# ---------- 落库重开 + 场景编辑隔离 ----------

def test_reopen_group_after_refresh(group_ctx):
    """模拟页面刷新：新会话重新查询，组汇总逐字节一致。"""
    db = group_ctx.db
    gid = group_ctx.resp["group_id"]
    before = main.get_analysis_group(gid, db)
    db.expire_all()  # 强制重新从库中加载
    after = main.get_analysis_group(gid, db)
    assert before == after
    listed = main.list_analysis_groups(group_ctx.scene.id, db)
    assert any(g["group_id"] == gid and g["dates"] == DATES3
               for g in listed)


def test_group_isolated_from_later_scene_edits(group_ctx):
    """提交后再改场景（楼加高/测点移动）：已保存的组仍显示原有数据。"""
    db = group_ctx.db
    gid = group_ctx.resp["group_id"]
    saved = main.get_analysis_group(gid, db)
    snap_id = group_ctx.resp["snapshot_id"]
    snap_payload = dict(db.get(models.Snapshot, snap_id).payload)
    # 之后场景被编辑：只会影响新快照，不回写已保存的组
    group_ctx.buildings[0].top_height = 99.0
    group_ctx.points[0].geom = from_shape(Point(99.0, 99.0, 99.0), srid=0)
    new_payload = main._bundle_payload(group_ctx.scene,
                                       group_ctx.buildings,
                                       group_ctx.points)
    assert new_payload != snap_payload
    assert main.get_analysis_group(gid, db) == saved
    assert db.get(models.Snapshot, snap_id).payload == snap_payload


def test_longest_sunlit_interval_none_when_no_sunlit():
    assert main._longest_sunlit_interval(
        [{"status": "night", "samples": 10, "start": "t0", "end": "t1"},
         {"status": "shaded", "samples": 5, "start": "t1", "end": "t2"}],
        5) is None


# ---------- HTTP 层（路由/请求解析/JSON 序列化） ----------

def test_http_roundtrip(monkeypatch):
    """TestClient 不经 with 使用 → 不触发 startup 的 create_all（PostGIS
    集成由 compose 覆盖）；get_db 覆盖为内存 SQLite。"""
    from fastapi.testclient import TestClient
    from app.db import get_db

    scene, buildings, points = _fake_rows()
    points = _pick_points(points)
    monkeypatch.setattr(main, "_load_scene_bundle",
                        lambda db, sid: (scene, buildings, points))
    db = _sqlite_session()
    main.app.dependency_overrides[get_db] = lambda: db
    try:
        client = TestClient(main.app)
        bad = client.post("/api/analysis/run-group", json={
            "scene_id": 1, "dates": ["2026-02-30", "2026-03-20"],
            "step_minutes": 5})
        assert bad.status_code == 400
        assert "2026-02-30" in bad.json()["detail"]

        r = client.post("/api/analysis/run-group", json={
            "scene_id": 1, "dates": ["2026-06-21", "2025-12-21", "2026-03-20"],
            "step_minutes": 5})
        assert r.status_code == 200, r.text
        gid = r.json()["group_id"]
        assert [x["date"] for x in r.json()["runs"]] == DATES3

        g = client.get(f"/api/analysis-groups/{gid}")
        assert g.status_code == 200
        body = g.json()
        assert body["dates"] == DATES3 and body["step_minutes"] == 5
        run0 = body["runs"][0]
        p0 = run0["points"][0]
        assert {"sunlit_minutes", "longest_continuous_sunlit_minutes",
                "longest_sunlit_interval"} <= set(p0)

        listed = client.get("/api/scenes/1/analysis-groups")
        assert any(x["group_id"] == gid for x in listed.json())

        full = client.get(f"/api/analysis/{run0['run_id']}")
        assert full.status_code == 200
        assert full.json()["group_id"] == gid
        tr = client.get(f"/api/analysis/{run0['run_id']}"
                        f"/points/{p0['point_id']}/trace")
        assert tr.status_code == 200 and tr.json()["queried"] > 0
    finally:
        main.app.dependency_overrides.clear()
