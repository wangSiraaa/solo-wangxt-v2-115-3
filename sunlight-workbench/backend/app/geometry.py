"""几何建模与射线相交（trimesh）。

- 建筑以拉伸多边形表示：footprint(模型坐标, 米) + base_height + top_height。
- 所有网格合并为一个 trimesh.Trimesh 做射线求交，face_owner 记录每个面
  属于哪栋建筑，从而支持"单点追查遮挡物"。
- 射线原点沿窗面法向外偏移 RAY_ORIGIN_OFFSET，避免与自身墙面自相交。
"""
from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np
import trimesh
from shapely.geometry import Polygon

RAY_ORIGIN_OFFSET = 0.01  # 米，沿窗面外法线


@dataclass
class BuildingGeom:
    name: str
    footprint: list[tuple[float, float]]  # 模型坐标多边形
    base_height: float = 0.0
    top_height: float = 10.0
    kind: str = "building"  # building / podium / ground_obstacle


@dataclass
class BuiltScene:
    mesh: trimesh.Trimesh
    face_owner: np.ndarray  # 每个三角面 -> 建筑名
    names: list[str] = field(default_factory=list)


def extrude_footprint(footprint: list[tuple[float, float]],
                      base_height: float, top_height: float) -> trimesh.Trimesh:
    poly = Polygon(footprint)
    if not poly.is_valid:
        poly = poly.buffer(0)
    mesh = trimesh.creation.extrude_polygon(poly, height=top_height - base_height)
    mesh.apply_translation([0, 0, base_height])
    return mesh


def box_footprint(cx: float, cy: float, w: float, d: float) -> list[tuple[float, float]]:
    """以 (cx,cy) 为中心的矩形 footprint（合成场景算例用）。"""
    return [(cx - w / 2, cy - d / 2), (cx + w / 2, cy - d / 2),
            (cx + w / 2, cy + d / 2), (cx - w / 2, cy + d / 2)]


def build_scene(buildings: list[BuildingGeom]) -> BuiltScene:
    meshes, owners, names = [], [], []
    for b in buildings:
        m = extrude_footprint(b.footprint, b.base_height, b.top_height)
        meshes.append(m)
        owners.extend([b.name] * len(m.faces))
        names.append(b.name)
    if not meshes:
        raise ValueError("场景至少需要一个几何体")
    combined = trimesh.util.concatenate(meshes)
    return BuiltScene(mesh=combined, face_owner=np.array(owners), names=names)


def cast_sun_ray(scene: BuiltScene, origin, direction) -> dict | None:
    """自 origin 沿 direction（指向太阳的单位向量）求最近遮挡。

    返回 None 表示无遮挡；否则返回遮挡物名称/距离/命中点。
    """
    origin = np.asarray(origin, dtype=float).reshape(1, 3)
    direction = np.asarray(direction, dtype=float).reshape(1, 3)
    locations, _, tri_ids = scene.mesh.ray.intersects_location(
        origin, direction, multiple_hits=True)
    if len(locations) == 0:
        return None
    dists = np.linalg.norm(locations - origin[0], axis=1)
    i = int(np.argmin(dists))
    return {
        "occluder": str(scene.face_owner[tri_ids[i]]),
        "distance": float(dists[i]),
        "hit_point": [round(float(v), 4) for v in locations[i]],
    }


def rotate_footprint(footprint, angle_deg: float) -> list[tuple[float, float]]:
    """绕原点旋转 footprint（构造"坐标旋转"算例用，逆时针为正）。"""
    a = np.radians(angle_deg)
    c, s = np.cos(a), np.sin(a)
    return [(round(c * x - s * y, 6), round(s * x + c * y, 6)) for x, y in footprint]


def rotate_point(p, angle_deg: float) -> tuple[float, float]:
    return rotate_footprint([p], angle_deg)[0]
