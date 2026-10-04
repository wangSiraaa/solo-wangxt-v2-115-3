# 日照分析工作台（合成场景 · 示例评价口径）

比较两组建筑体量对窗面日照影响的教学用工作台。**全部数据为合成场景，输出为示例评价口径，不构成任何规划合规结论。**

## 技术栈

- **前端** React + Three.js（@react-three/fiber）：三维场景、太阳路径、测点射线、遮挡物高亮
- **后端** FastAPI + **pvlib**（太阳位置）+ **trimesh**（射线相交）
- **数据库** PostgreSQL/PostGIS：建筑（Polygon）、测点（PointZ）、坐标基准（geography 锚点 + 时区 + 朝北偏角）、快照与分析结果

## 快速开始

```bash
docker compose up --build        # 前端 :8080，后端 :8000，PostGIS :5432
# 或本地开发：
cd backend && pip install -r requirements.txt && uvicorn app.main:app --reload
cd frontend && npm install && npm run dev
```

首次打开点击"初始化合成场景"，选择场景与日期后"运行当日分析"；或在"多日期分析"中勾选 2～5 个日期（预设冬至/春分/夏至/秋分，可自定义）一次跑出分析组。

> 数据库结构变更说明：本次新增 `run_groups` 表与 `runs.group_id` 列。
> `create_all` 只建新表、不改旧表——沿用旧数据卷时请重建
> （`docker compose down -v && docker compose up --build`）或自行迁移。

## 多日期分析组（冬至/春分/夏至一次对比）

- `POST /api/analysis/run-group`：一个场景 + **2～5 个本地日期** + **统一采样步长**。
  所有校验在**执行前**完成：日期格式/公历有效性、重复日期、日期个数、步长整除
  1440，任一不合法直接 400 并说明原因，不产生任何部分结果。
- 整组**只建一份场景快照**，各日期运行共享 `snapshot_id` 与 `group_id`——组内
  结果全部来自提交那一刻的场景配置，之后编辑场景不会混入；与单日入口共用同一
  执行函数，故组内某日结果与同参数单日运行逐样本一致。
- `GET /api/analysis-groups/{id}`：返回每个日期 × 测点的**连续口径**累计晒到
  分钟、最长连续晒到分钟与最长连续区间（本地起止时间）。
- `GET /api/scenes/{id}/analysis-groups`：列出场景已保存的组，页面刷新后由此
  重新打开；点击组内日期行/趋势图圆点下钻到对应单日运行（`GET /api/analysis/{run_id}`）
  及其遮挡追查 trace。
- 组汇总只用**连续采样口径**；整点快览（逐时采样）仍在单日运行视图内，两者页面
  上严格分区，不可混读。

## 坐标基准统一（关键约定）

| 项 | 约定 |
|---|---|
| 经纬度 | 场景锚点，WGS84，存 PostGIS `geography(Point,4326)` |
| 时区 | IANA 名称（如 `Asia/Shanghai`），采样先在本地时间生成再带时区入 pvlib |
| 模型朝北 | `north_offset_deg` = 模型 +y 轴相对真北的顺时针方位角 |
| 模型坐标 | x=模型东，y=模型北，z=上，单位米；太阳向量经 R_z(+offset) 旋入模型系 |

三者只挂在场景上，测点/建筑全部是该基准下的局部坐标，杜绝多套口径混用。

## 两套统计口径（不可混淆）

1. **逐时采样**：只在整点判定晒到/遮挡，用于快览。**几个整点晴亮 ≠ 日照小时数**。
2. **连续遮挡时段**：细步长（默认 5 min）扫描全天，输出最大连续晒到/遮挡区间及遮挡物，"日照时长"只从这个口径读数。

## 算例与手算核对（`backend/tests/`）

| 测试 | 核对方式 |
|---|---|
| `test_wall_shading_matches_handcalc` | 正南 10 m 处 4 m 高墙：晒到 ⟺ tan(el) > 2.8/9.99，命中点坐标逐分量对解析式 |
| `test_rotation_invariance_full_day` | 几何逆时针转 37° + `north_offset=37°`，全天逐样本判定完全一致 |
| `test_pvlib_known_anchor_equator_equinox` | 赤道春分正午≈90°、日出方位≈正东、夏至正午≈90°−23.44° |
| `test_interval_folding_handcalc` | 合成布尔序列折叠为连续区间 |
| `test_winter_summer_shaded_point` | 同一测点冬季日照 < 夏季；逐样本与方位修正后的解析阈值一致 |
| `test_api_logic` | 快照结构、几何重建、遮挡物归属 |
| `test_multiday` | 多日期组：执行前校验原因、组汇总==单日结果、整组一份快照、改场景后组数据不变、刷新重开与下钻 |

种子场景：**S1 邻楼遮挡**（正南板楼+东南塔楼 vs 目标楼，跨冬夏 2026-01-15 / 2026-07-15 对比）与 **S2 旋转场景**（S1 旋转 30°，物理等价，验证旋转口径——同日期结果逐样本一致）。

运行：`cd backend && python -m pytest tests/ -q`

## 结果追溯

- 每次运行先生成**场景快照**（`snapshots.payload` 含完整几何+坐标基准），结果关联快照 ID，场景后续被编辑不影响追溯（`GET /api/snapshots/{id}`）。
- **多日期分析组整组只建一份快照**（`run_groups.snapshot_id`），组内各日期运行共享；`GET /api/analysis-groups/{id}` 取组汇总，`GET /api/scenes/{id}/analysis-groups` 列出场景的组，刷新后可重新打开并下钻。
- 每个细样本都记录遮挡物名称/距离/命中点，`GET /api/analysis/{run}/points/{point}/trace[?time=...]` 支持单点追查；前端悬停遮挡时段即高亮对应建筑。

## 已知局限（必须阅读）

- **未建模遮挡**：种子场景说明中列明（南侧行道树、通信杆塔等），低层结果偏乐观；真实项目需把树木/构筑物建模或在结论中扣除。
- 射线为点采样，窗面日照以多测点近似；未做天空视域/散射日照，仅直射遮挡判定。
- 评价口径（累计日照分钟、最长连续日照分钟）是**示例口径**，不对应任何规范条文；本工具不输出规划合规结论。
