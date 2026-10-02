"""API 层逻辑测试（不依赖 PostgreSQL，用内存对象模拟 ORM 行）。

真实 PostGIS 读写由 docker-compose 集成环境覆盖；这里验证：
- 快照 payload 结构（结果-快照关联的数据源）
- 从 ORM 行重建几何 + 测点并跑通分析
- 场景坐标基准字段齐全（经纬度/时区/朝北偏角三元组）
"""
from types import SimpleNamespace as NS

from geoalchemy2.shape import from_shape
from shapely.geometry import Point, Polygon

from app.main import _bundle_payload, _geom_from_bundle
from app.analysis import analyze_point
from app.seed import scene_specs, LAT, LON, TZ


def _fake_rows():
    spec = scene_specs()[0]
    scene = NS(id=1, name=spec["name"], description=spec["description"],
               latitude=LAT, longitude=LON, timezone=TZ,
               north_offset_deg=spec["north_offset_deg"],
               unmodeled_occluders="树木未建模")
    buildings = [NS(id=i, name=b["name"], kind=b["kind"], color=b["color"],
                    footprint=from_shape(Polygon(b["footprint"]), srid=0),
                    base_height=b["base_height"], top_height=b["top_height"])
                 for i, b in enumerate(spec["buildings"], 1)]
    points = [NS(id=i, name=p["name"], window_id=p["window_id"],
                 geom=from_shape(Point(*p["position"]), srid=0),
                 normal=list(p["normal"]))
              for i, p in enumerate(spec["points"], 1)]
    return scene, buildings, points


def test_snapshot_payload_structure():
    scene, buildings, points = _fake_rows()
    payload = _bundle_payload(scene, buildings, points)
    sc = payload["scene"]
    # 坐标基准三元组必须在快照里完整保存
    assert (sc["latitude"], sc["longitude"]) == (LAT, LON)
    assert sc["timezone"] == TZ
    assert sc["north_offset_deg"] == 0.0
    assert len(payload["buildings"]) == 3
    assert len(payload["points"]) == 6
    p0 = payload["points"][0]
    assert len(p0["position"]) == 3 and len(p0["normal"]) == 3


def test_geom_rebuild_and_analyze():
    scene, buildings, points = _fake_rows()
    built, pts = _geom_from_bundle(buildings, points[:1])
    r = analyze_point(built, pts[0], latitude=scene.latitude,
                      longitude=scene.longitude, tz=scene.timezone,
                      date="2026-01-15",
                      north_offset_deg=scene.north_offset_deg, step_minutes=30)
    assert r["summary"]["sunlit_minutes"] >= 0
    assert any(iv["status"] == "shaded" for iv in r["continuous_intervals"])
    shaded = [s for s in r["fine_samples"] if s["status"] == "shaded"]
    assert all(s["occluder"] in {"B1_南侧板楼", "B2_东南塔楼", "T_目标楼"}
               for s in shaded)
