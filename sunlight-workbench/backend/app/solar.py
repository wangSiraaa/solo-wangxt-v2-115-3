"""太阳位置计算与坐标系统一约定。

坐标约定（全项目唯一口径，前端/后端/数据库必须一致）：
- 模型局部坐标为 ENU 系旋转而来：x = 模型东，y = 模型北，z = 向上，单位米。
- Scene.north_offset_deg = 模型 +y 轴相对真北的顺时针方位角（度）。
  0 表示模型 +y 即真北；90 表示模型 +y 指向真东。
- pvlib 方位角约定：0=北, 90=东, 180=南, 270=西（顺时针）。
- 时间：场景保存 IANA 时区名（如 Asia/Shanghai），所有采样先在本地时间
  生成，再带时区传入 pvlib；对外结果一律标注本地墙钟时间。
"""
from __future__ import annotations

import numpy as np
import pandas as pd
from pvlib import solarposition


def sun_vector_enu(elevation_deg: float, azimuth_deg: float) -> np.ndarray:
    """由高度角/方位角求指向太阳的单位向量（ENU 系）。"""
    el = np.radians(elevation_deg)
    az = np.radians(azimuth_deg)
    return np.array([
        np.cos(el) * np.sin(az),   # East
        np.cos(el) * np.cos(az),   # North
        np.sin(el),                # Up
    ])


def enu_to_model(vec_enu: np.ndarray, north_offset_deg: float) -> np.ndarray:
    """把 ENU 向量旋入模型局部坐标。

    推导：模型 +y 轴在 ENU 中的坐标为 (sinα, cosα)（α 为相对真北的顺时针
    方位角），模型 +x 轴为 (cosα, -sinα)。向量在模型系中的坐标即与两基
    向量点积，得 v_model = R_z(+α) @ v_enu。

    验证：α=90（模型 +y 指真东）时，真南 (0,-1,0) 应落在模型 +x：
    R_z(90) @ (0,-1,0) = (1, 0, 0) ✓
    推论：几何整体逆时针旋转 θ ⇔ north_offset_deg = θ（旋转不变性测试
    依据此关系构造）。
    """
    a = np.radians(north_offset_deg)
    c, s = np.cos(a), np.sin(a)
    R = np.array([[c, -s, 0.0], [s, c, 0.0], [0.0, 0.0, 1.0]])
    return R @ np.asarray(vec_enu, dtype=float)


def model_to_enu(vec_model: np.ndarray, north_offset_deg: float) -> np.ndarray:
    """enu_to_model 的逆变换（前端画真北箭头用）。"""
    a = np.radians(north_offset_deg)
    c, s = np.cos(a), np.sin(a)
    R = np.array([[c, s, 0.0], [-s, c, 0.0], [0.0, 0.0, 1.0]])
    return R @ np.asarray(vec_model, dtype=float)


def solar_positions(latitude: float, longitude: float, tz: str,
                    times_local: pd.DatetimeIndex) -> pd.DataFrame:
    """对一组带时区的本地时间求太阳位置（pvlib nrel_numpy 算法）。"""
    if times_local.tz is None:
        raise ValueError("times_local 必须带时区；经纬度/时区/朝北三者必须同源统一")
    return solarposition.get_solarposition(times_local, latitude, longitude)


def local_time_grid(date: str, tz: str, step_minutes: int) -> pd.DatetimeIndex:
    """生成某日 00:00–24:00 的本地时间网格（步长须整除 1440）。"""
    if 1440 % step_minutes != 0:
        raise ValueError("step_minutes 必须整除 1440")
    start = pd.Timestamp(date, tz=tz)
    return pd.date_range(start, start + pd.Timedelta(days=1),
                         freq=f"{step_minutes}min", inclusive="left")


def hourly_times(date: str, tz: str) -> pd.DatetimeIndex:
    """逐时口径：整点时刻（用于与连续遮挡时段严格区分）。"""
    start = pd.Timestamp(date, tz=tz)
    return pd.date_range(start, start + pd.Timedelta(hours=23), freq="1h")
