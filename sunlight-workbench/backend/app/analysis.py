"""日照分析核心：逐时采样 与 连续遮挡时段 是两套独立口径。

- 逐时采样(hourly)：只在每个整点判定 晒到/遮挡/夜晚，用于快速浏览，
  **不能**把整点晴亮直接累加成"日照小时数"。
- 连续时段(continuous)：用细步长（默认 5 分钟）扫描全天，输出最大连续
  晒到/遮挡区间及其遮挡物集合，这才是"连续日照时段"口径。
每个采样都记录遮挡物名称，支持单点追查。
"""
from __future__ import annotations

from dataclasses import dataclass

import numpy as np
import pandas as pd

from .geometry import BuiltScene, cast_sun_ray, RAY_ORIGIN_OFFSET
from .solar import enu_to_model, sun_vector_enu, solar_positions

STATUS_NIGHT = "night"      # 太阳在地平线下，不参与日照统计
STATUS_SUNLIT = "sunlit"
STATUS_SHADED = "shaded"


@dataclass
class MeasurePointGeom:
    id: str
    name: str
    position: tuple[float, float, float]
    normal: tuple[float, float, float]  # 窗面外法线（模型坐标）
    host_building: str | None = None


def _flags_at_times(scene: BuiltScene, point: MeasurePointGeom,
                    times_local: pd.DatetimeIndex,
                    latitude: float, longitude: float,
                    north_offset_deg: float) -> list[dict]:
    pos = solar_positions(latitude, longitude, str(times_local.tz), times_local)
    origin = np.array(point.position) + np.array(point.normal) * RAY_ORIGIN_OFFSET
    out = []
    for t, row in zip(times_local, pos.itertuples()):
        el = float(row.apparent_elevation)
        az = float(row.azimuth)
        if el <= 0:
            out.append({"time": t.isoformat(), "status": STATUS_NIGHT,
                        "elevation": round(el, 3), "azimuth": round(az, 3),
                        "occluder": None})
            continue
        d = enu_to_model(sun_vector_enu(el, az), north_offset_deg)
        hit = cast_sun_ray(scene, origin, d)
        out.append({
            "time": t.isoformat(),
            "status": STATUS_SHADED if hit else STATUS_SUNLIT,
            "elevation": round(el, 3), "azimuth": round(az, 3),
            "occluder": hit["occluder"] if hit else None,
            "occluder_distance": hit["distance"] if hit else None,
            "hit_point": hit["hit_point"] if hit else None,
        })
    return out


def to_intervals(samples: list[dict]) -> list[dict]:
    """把细步长采样折叠成最大连续区间（含遮挡物集合）。"""
    intervals = []
    for s in samples:
        if (intervals and intervals[-1]["status"] == s["status"]
                and intervals[-1]["occluder"] == s["occluder"]):
            intervals[-1]["end"] = s["time"]
            intervals[-1]["samples"] += 1
        else:
            intervals.append({"start": s["time"], "end": s["time"],
                              "status": s["status"], "occluder": s["occluder"],
                              "samples": 1})
    return intervals


def summarize(samples: list[dict], step_minutes: int) -> dict:
    """示例评价口径（非规划合规结论）：

    - daylight_minutes: 白天（太阳在地平线上）总分钟数
    - sunlit_minutes:   连续口径下晒到太阳的分钟数
    - longest_continuous_sunlit_minutes: 最长连续日照时长
    """
    day = [s for s in samples if s["status"] != STATUS_NIGHT]
    sunlit = [s for s in day if s["status"] == STATUS_SUNLIT]
    longest = 0
    run = 0
    for s in day:
        run = run + 1 if s["status"] == STATUS_SUNLIT else 0
        longest = max(longest, run)
    return {
        "daylight_minutes": len(day) * step_minutes,
        "sunlit_minutes": len(sunlit) * step_minutes,
        "longest_continuous_sunlit_minutes": longest * step_minutes,
        "criterion": "示例口径：连续时段扫描，非任何规范条文",
    }


def analyze_point(scene: BuiltScene, point: MeasurePointGeom, *,
                  latitude: float, longitude: float, tz: str, date: str,
                  north_offset_deg: float, step_minutes: int = 5) -> dict:
    from .solar import local_time_grid, hourly_times
    fine_times = local_time_grid(date, tz, step_minutes)
    fine = _flags_at_times(scene, point, fine_times, latitude, longitude,
                           north_offset_deg)
    hourly = _flags_at_times(scene, point, hourly_times(date, tz),
                             latitude, longitude, north_offset_deg)
    return {
        "point_id": point.id,
        "point_name": point.name,
        "hourly_samples": hourly,          # 逐时口径（快览）
        "continuous_intervals": to_intervals(fine),  # 连续口径（时段）
        "fine_samples": fine,
        "step_minutes": step_minutes,
        "summary": summarize(fine, step_minutes),
    }
