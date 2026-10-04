import React, { useEffect, useMemo, useState } from 'react'
import { api } from './api.js'
import SceneViewer from './components/SceneViewer.jsx'
import ResultsPanel from './components/ResultsPanel.jsx'
import GroupPanel from './components/GroupPanel.jsx'

const DATES = ['2026-01-15', '2026-03-20', '2026-07-15'] // 跨冬夏算例日期
// 多日期分析预设（二分二至，教师可一次对比冬至/春分/夏至）
const SOLAR_PRESETS = [
  { d: '2025-12-21', tag: '冬至' },
  { d: '2026-03-20', tag: '春分' },
  { d: '2026-06-21', tag: '夏至' },
  { d: '2026-09-23', tag: '秋分' },
]
const STEPS = [1, 2, 3, 5, 6, 10, 15, 20, 30, 60] // 均整除 1440

export default function App() {
  const [scenes, setScenes] = useState([])
  const [sceneId, setSceneId] = useState(null)
  const [payload, setPayload] = useState(null)   // 场景或快照内容
  const [date, setDate] = useState(DATES[0])
  const [sunpath, setSunpath] = useState(null)
  const [timeIdx, setTimeIdx] = useState(30)
  const [run, setRun] = useState(null)
  const [selectedPointId, setSelectedPointId] = useState(null)
  const [highlightOccluder, setHighlightOccluder] = useState(null)
  const [trace, setTrace] = useState(null)
  const [error, setError] = useState(null)
  // 多日期分析组
  const [groupDates, setGroupDates] = useState(SOLAR_PRESETS.slice(0, 3).map((p) => p.d))
  const [customDate, setCustomDate] = useState('2026-06-21')
  const [step, setStep] = useState(5)
  const [group, setGroup] = useState(null)
  const [groups, setGroups] = useState([])

  useEffect(() => { api.scenes().then(setScenes).catch((e) => setError(String(e))) }, [])

  useEffect(() => {
    if (!sceneId) return
    setRun(null); setGroup(null); setSelectedPointId(null); setTrace(null)
    api.scene(sceneId).then(setPayload)
    api.groups(sceneId).then(setGroups).catch(() => setGroups([]))
  }, [sceneId])

  useEffect(() => {
    if (!sceneId) return
    api.sunpath(sceneId, date).then(setSunpath)
  }, [sceneId, date])

  const doSeed = async () => { await api.seed(); setScenes(await api.scenes()) }

  const doRun = async () => {
    setError(null)
    try {
      const r = await api.run(sceneId, date, 5)
      const full = await api.runResult(r.run_id)
      setGroup(null)
      setRun(full)
      // 结果关联快照：渲染切换到快照内容，保证结果-场景一致可追溯
      const snap = await api.snapshot(full.snapshot_id)
      setPayload(snap.payload)
    } catch (e) { setError(e.message) }
  }

  // ---- 多日期分析组 ----

  const toggleDate = (d) => {
    setError(null)
    if (groupDates.includes(d)) setGroupDates(groupDates.filter((x) => x !== d))
    else if (groupDates.length >= 5) setError('多日期分析最多 5 个日期')
    else setGroupDates([...groupDates, d].sort())
  }

  const addCustomDate = () => {
    setError(null)
    if (!customDate) return
    if (groupDates.includes(customDate)) return setError(`日期 ${customDate} 已在列表中`)
    if (groupDates.length >= 5) return setError('多日期分析最多 5 个日期')
    setGroupDates([...groupDates, customDate].sort())
  }

  const openGroup = async (groupId) => {
    setError(null)
    try {
      const full = await api.group(groupId)
      setGroup(full)
      setRun(null); setSelectedPointId(null); setTrace(null)
      // 整组关联提交时的同一份快照：之后编辑场景不影响本组展示
      const snap = await api.snapshot(full.snapshot_id)
      setPayload(snap.payload)
      if (full.runs[0]) setDate(full.runs[0].date)
    } catch (e) { setError(e.message) }
  }

  const doRunGroup = async () => {
    setError(null)
    try {
      const r = await api.runGroup(sceneId, groupDates, step)
      await openGroup(r.group_id)
      setGroups(await api.groups(sceneId))
    } catch (e) { setError(e.message) }  // 后端在执行前说明日期不合法的原因
  }

  // 下钻组内任一单日运行（及其 trace）
  const openRun = async (runId, runDate) => {
    setError(null)
    try {
      const full = await api.runResult(runId)
      setRun(full)
      setSelectedPointId(null); setTrace(null)
      if (runDate) setDate(runDate)   // 太阳路径/时刻滑块对齐该日
    } catch (e) { setError(e.message) }
  }

  // 当前时刻各测点状态（取最近细样本）
  const pointStatus = useMemo(() => {
    if (!run || !sunpath) return null
    const t = sunpath.points[timeIdx]?.time
    if (!t) return null
    const out = {}
    for (const res of run.results) {
      let best = null
      for (const s of res.fine_samples) {
        if (!best || Math.abs(s.time.localeCompare(t)) < Math.abs(best.time.localeCompare(t))) best = s
      }
      out[res.point_id] = best
    }
    return out
  }, [run, sunpath, timeIdx])

  const selectedResult = run?.results.find((r) => r.point_id === selectedPointId)

  const doTrace = async (pointId) => {
    setError(null)
    try { setTrace(await api.trace(run.run_id, pointId)) }
    catch (e) { setError(e.message) }
  }

  // 单日日期下拉：算例日期 ∪ 当前日期（下钻组内日期后也能正确显示）
  const dateOptions = [...new Set([...DATES, date])].sort()

  return (
    <div className="layout">
      <header>
        <b>日照分析工作台</b>
        <span className="badge">合成场景 · 示例评价口径 · 非规划合规结论</span>
      </header>
      <aside>
        <label>场景（坐标基准统一挂在场景上）</label>
        <select value={sceneId ?? ''} onChange={(e) => setSceneId(+e.target.value)}>
          <option value="" disabled>选择场景</option>
          {scenes.map((s) => <option key={s.id} value={s.id}>{s.name}</option>)}
        </select>
        {!scenes.length && <button onClick={doSeed}>初始化合成场景</button>}
        {payload && (
          <div className="meta">
            <div>经纬度 {payload.scene.latitude}, {payload.scene.longitude}</div>
            <div>时区 {payload.scene.timezone}</div>
            <div>模型北偏角 {payload.scene.north_offset_deg}°</div>
            <div className="warn">未建模遮挡：{payload.scene.unmodeled_occluders}</div>
          </div>
        )}
        <label>日期（单日 · 跨冬夏算例）</label>
        <select value={date} onChange={(e) => setDate(e.target.value)}>
          {dateOptions.map((d) => <option key={d}>{d}</option>)}
        </select>
        <button disabled={!sceneId} onClick={doRun}>运行当日分析（5min 步长）</button>
        {run && (
          <div className="muted small">
            run #{run.run_id} · 快照 #{run.snapshot_id}
            {run.group_id ? ` · 组 #${run.group_id}` : ''}
          </div>
        )}

        <details className="multiday" open>
          <summary>多日期分析（2～5 个日期 · 统一连续采样步长）</summary>
          <div className="chips">
            {groupDates.map((d) => (
              <span key={d} className="chip">{d}
                <b title="移除" onClick={() => toggleDate(d)}>×</b>
              </span>
            ))}
          </div>
          {SOLAR_PRESETS.map(({ d, tag }) => (
            <label className="check" key={d}>
              <input type="checkbox" checked={groupDates.includes(d)}
                onChange={() => toggleDate(d)} />
              {tag} {d}
            </label>
          ))}
          <div className="row">
            <input type="date" value={customDate}
              onChange={(e) => setCustomDate(e.target.value)} />
            <button onClick={addCustomDate}>添加日期</button>
          </div>
          <label>统一采样步长（连续口径）</label>
          <select value={step} onChange={(e) => setStep(+e.target.value)}>
            {STEPS.map((s) => <option key={s} value={s}>{s} min</option>)}
          </select>
          <button disabled={!sceneId || groupDates.length < 2} onClick={doRunGroup}>
            运行多日期分析（{groupDates.length} 个日期）
          </button>
        </details>

        {groups.length > 0 && (
          <>
            <label>已保存的多日期分析组（点击重新打开）</label>
            {groups.map((g) => (
              <button key={g.group_id} className="group-item"
                onClick={() => openGroup(g.group_id)}>
                组#{g.group_id} · {g.dates.map((d) => d.slice(5)).join(' / ')}
                · {g.step_minutes}min
              </button>
            ))}
          </>
        )}

        {error && <div className="error">{error}</div>}
        {sunpath && (
          <>
            <label>时刻 {sunpath.points[timeIdx]?.time.slice(11, 16)}</label>
            <input type="range" min={0} max={sunpath.points.length - 1}
              value={timeIdx} onChange={(e) => setTimeIdx(+e.target.value)} />
          </>
        )}
        {trace && (
          <div className="trace">
            <h4>遮挡物追查（测点 #{trace.point_id}）</h4>
            <div>遮挡物：{trace.occluders.join('、') || '无'}</div>
            <div className="muted small">{trace.note}</div>
            <button onClick={() => setTrace(null)}>关闭</button>
          </div>
        )}
      </aside>
      <main>
        <SceneViewer
          payload={payload} sunpath={sunpath} timeIdx={timeIdx}
          pointStatus={pointStatus} selectedPointId={selectedPointId}
          highlightOccluder={highlightOccluder}
          onSelectPoint={setSelectedPointId}
          onSelectBuilding={setHighlightOccluder} />
      </main>
      <aside className="right">
        {group && (
          <GroupPanel group={group} points={payload?.points}
            activeRunId={run?.run_id} onPickDate={openRun}
            onClose={() => setGroup(null)} />
        )}
        <ResultsPanel
          run={run} result={selectedResult}
          onHoverInterval={(iv) => setHighlightOccluder(iv?.occluder ?? null)}
          onTrace={doTrace} />
      </aside>
    </div>
  )
}
