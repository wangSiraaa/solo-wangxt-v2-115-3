import React, { useEffect, useMemo, useRef, useState } from 'react'
import { api } from './api.js'
import SceneViewer from './components/SceneViewer.jsx'
import ResultsPanel from './components/ResultsPanel.jsx'
import GroupPanel from './components/GroupPanel.jsx'

const DATES = ['2026-01-15', '2026-03-20', '2026-07-15'] // 跨冬夏算例日期
const STEPS = [5, 10, 15, 30]

export default function App() {
  const [scenes, setScenes] = useState([])
  const [sceneId, setSceneId] = useState(null)
  const [payload, setPayload] = useState(null)   // 场景或快照内容
  const [mode, setMode] = useState('single')     // single=单日 / multi=多日期分析
  const [date, setDate] = useState(DATES[0])
  const [step, setStep] = useState(5)
  const [sunpath, setSunpath] = useState(null)
  const [timeIdx, setTimeIdx] = useState(30)
  const [run, setRun] = useState(null)
  const [selectedPointId, setSelectedPointId] = useState(null)
  const [highlightOccluder, setHighlightOccluder] = useState(null)
  const [trace, setTrace] = useState(null)
  const [error, setError] = useState(null)
  const [busy, setBusy] = useState(false)

  // 多日期分析
  const [multiDates, setMultiDates] = useState([
    '2026-12-22', '2026-03-20', '2026-06-21']) // 冬至 / 春分 / 夏至
  const [multiStep, setMultiStep] = useState(5)
  const [group, setGroup] = useState(null)
  const [savedGroups, setSavedGroups] = useState([])
  const loadSeq = useRef(0)  // 防止快速切换场景/下钻时旧请求覆盖新状态

  useEffect(() => { api.scenes().then(setScenes).catch((e) => setError(String(e))) }, [])

  useEffect(() => {
    if (!sceneId) return
    const seq = ++loadSeq.current
    setRun(null); setSelectedPointId(null); setTrace(null)
    setGroup(null); setMode('single')
    api.scene(sceneId).then((p) => { if (loadSeq.current === seq) setPayload(p) })
    api.sceneGroups(sceneId).then((gs) => { if (loadSeq.current === seq) setSavedGroups(gs) })
      .catch(() => { if (loadSeq.current === seq) setSavedGroups([]) })
    api.sunpath(sceneId, DATES[0]).then((sp) => {
      if (loadSeq.current === seq) { setSunpath(sp); setDate(DATES[0]) }
    })
  }, [sceneId])

  const pickDate = (d) => {
    const seq = ++loadSeq.current
    // 手动改日期：旧 run 绑定的是别的日期，清空结果并刷新该日太阳路径
    setDate(d); setRun(null); setSelectedPointId(null); setTrace(null)
    api.sunpath(sceneId, d).then((sp) => { if (loadSeq.current === seq) setSunpath(sp) })
  }

  const doSeed = async () => { await api.seed(); setScenes(await api.scenes()) }

  const applyRun = async (runId) => {
    const seq = ++loadSeq.current
    const full = await api.runResult(runId)
    setRun(full)
    setGroup(null)
    setMode('single')
    setDate(full.date)
    // 结果关联快照：渲染切换到快照内容，保证结果-场景一致可追溯
    const snap = await api.snapshot(full.snapshot_id)
    if (loadSeq.current !== seq) return
    setPayload(snap.payload)
    const sp = await api.sunpath(full.scene_id, full.date)
    if (loadSeq.current !== seq) return
    setSunpath(sp)
    setTimeIdx(Math.floor(sp.points.length / 2))
    setSelectedPointId(null)
    setTrace(null)
  }

  const doRun = async () => {
    setError(null); setBusy(true)
    try {
      const r = await api.run(sceneId, date, step)
      await applyRun(r.run_id)
    } catch (e) {
      setError(String(e))
    } finally { setBusy(false) }
  }

  const validateMultiDates = (ds) => {
    if (ds.length < 2 || ds.length > 5) return `多日期分析需要 2～5 个日期，当前 ${ds.length} 个`
    const seen = new Set()
    for (const d of ds) {
      if (!d) return '存在未填写的日期：请为每个日期槽选择本地日期'
      const t = new Date(d + 'T00:00:00')
      if (Number.isNaN(+t)) return `日期 ${d} 不合法：需要真实日历日期（YYYY-MM-DD）`
      if (seen.has(d)) return `日期 ${d} 重复：组内日期必须互不相同`
      seen.add(d)
    }
    return null
  }

  const doCreateGroup = async () => {
    setError(null)
    const problem = validateMultiDates(multiDates)
    if (problem) { setError(problem); return }
    setBusy(true)
    try {
      const r = await api.createGroup(sceneId, multiDates, multiStep)
      const g = await api.group(r.group_id)
      setGroup(g); setRun(null); setSelectedPointId(null); setTrace(null)
      setSavedGroups(await api.sceneGroups(sceneId))
    } catch (e) {
      setError(String(e)) // 后端在执行前校验，错误里带具体不合法日期与原因
    } finally { setBusy(false) }
  }

  const openGroup = async (groupId) => {
    setError(null)
    try {
      const g = await api.group(groupId)
      setGroup(g); setRun(null); setSelectedPointId(null); setTrace(null)
    } catch (e) { setError(String(e)) }
  }

  const setDateSlot = (i, v) => setMultiDates((ds) => ds.map((d, j) => (j === i ? v : d)))
  const addDateSlot = () => setMultiDates((ds) => (ds.length < 5 ? [...ds, ''] : ds))
  const dropDateSlot = (i) => setMultiDates((ds) => ds.filter((_, j) => j !== i))

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
    setTrace(await api.trace(run.run_id, pointId))
  }

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

        <div className="seg">
          <button className={mode === 'single' ? 'on' : ''}
            onClick={() => { setMode('single'); setGroup(null); setError(null) }}>
            单日分析
          </button>
          <button className={mode === 'multi' ? 'on' : ''}
            onClick={() => { setMode('multi'); setRun(null); setSelectedPointId(null); setTrace(null); setError(null) }}>
            多日期分析（2～5 日）
          </button>
        </div>

        {mode === 'single' && (
          <>
            <label>日期（跨冬夏算例）</label>
            <select value={date} onChange={(e) => pickDate(e.target.value)}>
              {DATES.map((d) => <option key={d}>{d}</option>)}
            </select>
            <label>连续采样步长（整点快览始终逐时独立）</label>
            <select value={step} onChange={(e) => setStep(+e.target.value)}>
              {STEPS.map((m) => <option key={m} value={m}>{m} min</option>)}
            </select>
            <button disabled={!sceneId || busy} onClick={doRun}>
              {busy ? '分析中…' : '运行当日分析'}
            </button>
            {run && (
              <div className="muted small">
                run #{run.run_id} · 快照 #{run.snapshot_id}
                {run.group_id ? ` · 来自分析组 #${run.group_id}` : ''}
                {run.group_id && (
                  <button className="link" onClick={() => openGroup(run.group_id)}>
                    返回分析组
                  </button>
                )}
              </div>
            )}
          </>
        )}

        {mode === 'multi' && (
          <>
            <label>本地日期（冬至 / 春分 / 夏至等，2～5 个，按场景时区采样）</label>
            {multiDates.map((d, i) => (
              <div key={i} className="daterow">
                <input type="date" value={d}
                  onChange={(e) => setDateSlot(i, e.target.value)} />
                {multiDates.length > 2 && (
                  <button title="删除该日期"
                    onClick={() => dropDateSlot(i)}>×</button>
                )}
              </div>
            ))}
            {multiDates.length < 5 && (
              <button onClick={addDateSlot}>＋ 增加日期</button>
            )}
            <label>统一连续采样步长（组内各日一致）</label>
            <select value={multiStep} onChange={(e) => setMultiStep(+e.target.value)}>
              {STEPS.map((m) => <option key={m} value={m}>{m} min</option>)}
            </select>
            <button disabled={!sceneId || busy} onClick={doCreateGroup}>
              {busy ? '分析中…' : '提交多日期分析（保存为分析组）'}
            </button>
            <div className="muted small">
              整组在一次提交内完成并共用同一快照；含不合法日期时会在执行前提示，
              不会产生任何运行。
            </div>
            {savedGroups.length > 0 && (
              <>
                <label>已保存的分析组（刷新后可重新打开）</label>
                <div className="grouplist">
                  {savedGroups.map((g) => (
                    <div key={g.group_id}
                      className={group?.group_id === g.group_id ? 'groupitem on' : 'groupitem'}
                      onClick={() => openGroup(g.group_id)}>
                      <b>组 #{g.group_id}</b>
                      <span>{g.dates.join('、')}</span>
                      <span className="muted">{g.step_minutes}min · 快照 #{g.snapshot_id}</span>
                    </div>
                  ))}
                </div>
              </>
            )}
          </>
        )}

        {error && <div className="error">{error}</div>}

        {mode === 'single' && sunpath && (
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
        {group
          ? <GroupPanel group={group} onDrill={applyRun} />
          : <ResultsPanel
              run={run} result={selectedResult}
              onHoverInterval={(iv) => setHighlightOccluder(iv?.occluder ?? null)}
              onTrace={doTrace} />}
      </aside>
    </div>
  )
}
