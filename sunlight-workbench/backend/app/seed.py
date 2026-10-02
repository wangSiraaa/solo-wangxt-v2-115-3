"""合成场景种子数据（仅示例，不对应任何真实地块）。

- S1 邻楼遮挡：目标住宅楼 + 正南板式邻楼 + 东南塔楼，用于冬夏对比算例。
- S2 旋转场景：S1 全部几何逆时针旋转 30° 且 north_offset_deg=30，
  物理情形与 S1 完全等价，用于核对坐标旋转口径（两场景同日期结果应一致）。
所有坐标为模型局部米制坐标；经纬度/时区/朝北偏角统一挂在场景上。
"""
from __future__ import annotations

from geoalchemy2.shape import from_shape
from shapely.geometry import Point, Polygon

from . import models
from .geometry import box_footprint, rotate_footprint, rotate_point

LAT, LON, TZ = 39.9042, 116.4074, "Asia/Shanghai"

UNMODELED_NOTE = (
    "未建模遮挡：南侧沿街一排悬铃木（树高约 12 m，冠幅约 6 m）未进入几何模型，"
    "1–2 层窗面实际日照可能少于计算值；东南角有一处通信基站杆塔亦未建模。"
    "本场景为合成示例，输出仅为示例评价口径，不构成规划合规结论。")


def _base_buildings():
    return [
        dict(name="T_目标楼", kind="building",
             footprint=box_footprint(0, 0, 24, 14), base_height=0, top_height=18,
             color="#c8a06e"),
        dict(name="B1_南侧板楼", kind="building",
             footprint=box_footprint(0, -30, 60, 12), base_height=0, top_height=35,
             color="#8ea3b8"),
        dict(name="B2_东南塔楼", kind="building",
             footprint=box_footprint(35, -15, 18, 18), base_height=0, top_height=45,
             color="#7e93a8"),
    ]


def _base_points():
    """目标楼窗面测点：同一窗面布多个测点（W1 左/中/右）。"""
    pts = []
    # 南立面 y=-7，法线 (0,-1,0)；层高 3 m，窗台+窗中约 1.6+3k
    for i, x in enumerate((-8.0, 0.0, 8.0)):
        pts.append(dict(name=f"W1_一层南窗_{'左中右'[i]}",
                        position=(x, -7.0, 1.6), normal=(0, -1, 0),
                        window_id="W1", host="T_目标楼"))
    pts.append(dict(name="W2_三层南窗_中", position=(0.0, -7.0, 7.6),
                    normal=(0, -1, 0), window_id="W2", host="T_目标楼"))
    pts.append(dict(name="W3_六层南窗_中", position=(0.0, -7.0, 16.1),
                    normal=(0, -1, 0), window_id="W3", host="T_目标楼"))
    # 东立面 x=12，法线 (1,0,0)，用于展示方位差异
    pts.append(dict(name="E1_三层东窗_中", position=(12.0, 0.0, 7.6),
                    normal=(1, 0, 0), window_id="E1", host="T_目标楼"))
    return pts


def _rot(v, theta):
    return rotate_point(v, theta)


def scene_specs():
    theta = 30.0
    s1 = dict(name="S1_邻楼遮挡", description="正南板楼+东南塔楼对目标楼的遮挡（冬夏对比算例）",
              north_offset_deg=0.0, buildings=_base_buildings(),
              points=_base_points())
    s2 = dict(name="S2_旋转场景", description="S1 几何逆时针旋转 30°，north_offset=30°，物理等价",
              north_offset_deg=theta,
              buildings=[{**b, "footprint": rotate_footprint(b["footprint"], theta)}
                         for b in _base_buildings()],
              points=[{**p,
                       "position": (*_rot(p["position"][:2], theta), p["position"][2]),
                       "normal": (*_rot(p["normal"][:2], theta), p["normal"][2])}
                      for p in _base_points()])
    return [s1, s2]


def seed_database(db) -> list[int]:
    ids = []
    for spec in scene_specs():
        scene = models.Scene(
            name=spec["name"], description=spec["description"],
            latitude=LAT, longitude=LON, timezone=TZ,
            north_offset_deg=spec["north_offset_deg"],
            anchor=from_shape(Point(LON, LAT), srid=4326),
            unmodeled_occluders=UNMODELED_NOTE)
        db.add(scene)
        db.flush()
        bmap = {}
        for b in spec["buildings"]:
            row = models.Building(
                scene_id=scene.id, name=b["name"], kind=b["kind"],
                footprint=from_shape(Polygon(b["footprint"]), srid=0),
                base_height=b["base_height"], top_height=b["top_height"],
                color=b["color"])
            db.add(row)
            db.flush()
            bmap[b["name"]] = row.id
        for p in spec["points"]:
            db.add(models.MeasurePoint(
                scene_id=scene.id, building_id=bmap.get(p["host"]),
                name=p["name"], window_id=p["window_id"],
                geom=from_shape(Point(*p["position"]), srid=0),
                normal=list(p["normal"])))
        db.commit()
        ids.append(scene.id)
    return ids
