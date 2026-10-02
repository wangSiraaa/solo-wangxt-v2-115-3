"""已知几何手算核对测试。

每个算例都能用纸笔/解析式独立验证，不依赖"程序自己证明自己"：
1. 正南墙遮挡：tan(高度角) 与 (墙高-测点高)/水平距离 的解析阈值对比。
2. 旋转不变性：几何逆时针转 θ + north_offset=θ，全天判定逐样本一致。
3. pvlib 锚点：赤道春分正午高度角≈90°、日出方位≈正东。
4. 连续区间折叠逻辑：合成布尔序列手算核对。
5. 冬夏对比：同一被遮挡测点，冬季连续日照时长 < 夏季。
"""
import numpy as np
import pandas as pd
import pytest

from app.analysis import (MeasurePointGeom, analyze_point, to_intervals,
                          STATUS_SUNLIT, STATUS_SHADED)
from app.geometry import (BuildingGeom, box_footprint, build_scene,
                          cast_sun_ray, rotate_footprint, rotate_point)
from app.solar import (enu_to_model, sun_vector_enu, solar_positions,
                       local_time_grid)

# ---- 算例几何：测点正南 10 m 处有一堵 10×3×4 m 的墙 ----
POINT = MeasurePointGeom(id="P1", name="南窗中点",
                         position=(0.0, 0.0, 1.2), normal=(0.0, -1.0, 0.0))
WALL = BuildingGeom(name="wall_south",
                    footprint=box_footprint(0.0, -11.5, 10.0, 3.0),
                    base_height=0.0, top_height=4.0)
# 解析阈值：射线原点沿法线偏移 0.01 → 水平距离 9.99，高差 4.0-1.2=2.8
CRIT_ELEV = np.degrees(np.arctan2(2.8, 9.99))  # ≈ 15.65°


def test_wall_shading_matches_handcalc():
    scene = build_scene([WALL])
    origin = np.array(POINT.position) + np.array(POINT.normal) * 0.01
    # 太阳正南（az=180），三个手算高度角
    for elev, expect_shaded in [(10.0, True), (CRIT_ELEV - 1.0, True),
                                (CRIT_ELEV + 1.0, False), (30.0, False)]:
        d = enu_to_model(sun_vector_enu(elev, 180.0), 0.0)
        hit = cast_sun_ray(scene, origin, d)
        assert (hit is not None) == expect_shaded, f"elev={elev}"
        if expect_shaded:
            assert hit["occluder"] == "wall_south"
            # 命中点手算：y=-10 平面，z = 1.2 + 9.99*tan(elev)
            assert hit["hit_point"][1] == pytest.approx(-10.0, abs=1e-3)
            assert hit["hit_point"][2] == pytest.approx(
                1.2 + 9.99 * np.tan(np.radians(elev)), abs=1e-3)


def test_rotation_invariance_full_day():
    """几何逆时针转 37° 且 north_offset=37°，全天逐样本判定必须完全一致。"""
    theta = 37.0
    scene_a = build_scene([WALL])
    wall_b = BuildingGeom(name="wall_south",
                          footprint=rotate_footprint(WALL.footprint, theta),
                          base_height=0.0, top_height=4.0)
    scene_b = build_scene([wall_b])
    px, py = rotate_point(POINT.position[:2], theta)
    point_b = MeasurePointGeom(id="P1", name="南窗中点",
                               position=(px, py, POINT.position[2]),
                               normal=(*rotate_point(POINT.normal[:2], theta), 0.0))
    kw = dict(latitude=39.9, longitude=116.4, tz="Asia/Shanghai",
              date="2026-01-15", step_minutes=10)
    ra = analyze_point(scene_a, POINT, north_offset_deg=0.0, **kw)
    rb = analyze_point(scene_b, point_b, north_offset_deg=theta, **kw)
    for sa, sb in zip(ra["fine_samples"], rb["fine_samples"]):
        assert sa["status"] == sb["status"], sa["time"]
        assert sa["occluder"] == sb["occluder"]
    assert (ra["summary"]["sunlit_minutes"]
            == rb["summary"]["sunlit_minutes"])


def test_pvlib_known_anchor_equator_equinox():
    """赤道锚点：春分日正午高度角≈90°（±1，春分时刻未必落在当天正午），
    日出方位≈90°（正东，±1）；夏至正午高度角≈90°-23.44°（±0.3）。"""
    times = local_time_grid("2026-03-20", "UTC", 5)
    pos = solar_positions(0.0, 0.0, "UTC", times)
    noon_el = pos["apparent_elevation"].max()
    assert noon_el == pytest.approx(90.0, abs=1.0)
    morning = pos.between_time("05:30", "07:00")
    rising = morning[morning["apparent_elevation"] > 0]
    assert rising["azimuth"].iloc[0] == pytest.approx(90.0, abs=1.0)
    solstice = solar_positions(0.0, 0.0, "UTC",
                               local_time_grid("2026-06-21", "UTC", 5))
    assert solstice["apparent_elevation"].max() == pytest.approx(
        90.0 - 23.44, abs=0.3)


def test_interval_folding_handcalc():
    samples = [{"time": f"2026-01-15T0{h}:00:00+08:00", "status": s,
                "occluder": o}
               for h, (s, o) in enumerate([
                   ("night", None), ("night", None),
                   ("sunlit", None), ("shaded", "B2"), ("shaded", "B2"),
                   ("sunlit", None)])]
    iv = to_intervals(samples)
    assert [(i["status"], i["samples"]) for i in iv] == [
        ("night", 2), ("sunlit", 1), ("shaded", 2), ("sunlit", 1)]
    assert iv[2]["occluder"] == "B2"


def test_winter_summer_shaded_point():
    """同一测点（南墙遮挡）：冬季连续日照分钟数 < 夏季，且正午判定与
    解析阈值一致（用 pvlib 真实高度角对手算不等式）。"""
    scene = build_scene([WALL])
    kw = dict(latitude=39.9, longitude=116.4, tz="Asia/Shanghai",
              north_offset_deg=0.0, step_minutes=5)
    winter = analyze_point(scene, POINT, date="2026-01-15", **kw)
    summer = analyze_point(scene, POINT, date="2026-07-15", **kw)
    assert (winter["summary"]["sunlit_minutes"]
            < summer["summary"]["sunlit_minutes"])
    # 逐样本与解析不等式核对（用 pvlib 真实高度角/方位角对手算式）：
    # 命中 y=-10 平面时 x_hit = 9.99·tan(az-180°)，z_hit = 1.2 + 9.99·tan(el)/|cos az|
    for res in (winter, summer):
        for s in res["fine_samples"]:
            if s["status"] == "night":
                continue
            az, el = np.radians(s["azimuth"]), np.radians(s["elevation"])
            within = abs(np.sin(az)) < 5.0 / 9.99 * abs(np.cos(az)) and \
                np.cos(az) < 0  # 方位在南墙半平面内且 |x_hit| < 5
            crit = np.degrees(np.arctan2(2.8 * abs(np.cos(az)), 9.99))
            expect = (STATUS_SHADED if (within and s["elevation"] < crit)
                      else STATUS_SUNLIT)
            assert s["status"] == expect, (s["time"], s["azimuth"],
                                           s["elevation"], crit)
