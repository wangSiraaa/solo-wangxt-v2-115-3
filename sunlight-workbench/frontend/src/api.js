const BASE = '/api'

async function req(path, options) {
  const r = await fetch(BASE + path, options)
  if (!r.ok) {
    // 后端 400 的 detail 即"执行前说明原因"的文案，直接抛给界面展示
    const text = await r.text()
    let detail = text
    try { detail = JSON.parse(text).detail ?? text } catch { /* 非 JSON */ }
    throw new Error(typeof detail === 'string' ? detail : JSON.stringify(detail))
  }
  return r.json()
}

const post = (path, body) => req(path, {
  method: 'POST',
  headers: { 'Content-Type': 'application/json' },
  body: JSON.stringify(body),
})

export const api = {
  scenes: () => req('/scenes'),
  seed: () => req('/scenes/seed', { method: 'POST' }),
  scene: (id) => req(`/scenes/${id}`),
  sunpath: (id, date) => req(`/scenes/${id}/sunpath?date=${date}`),
  run: (scene_id, date, step_minutes = 5) =>
    post('/analysis/run', { scene_id, date, step_minutes }),
  runResult: (runId) => req(`/analysis/${runId}`),
  trace: (runId, pointId, time) =>
    req(`/analysis/${runId}/points/${pointId}/trace` + (time ? `?time=${time}` : '')),
  snapshot: (id) => req(`/snapshots/${id}`),
  // 多日期分析组
  runGroup: (scene_id, dates, step_minutes = 5) =>
    post('/analysis/run-group', { scene_id, dates, step_minutes }),
  group: (groupId) => req(`/analysis-groups/${groupId}`),
  groups: (sceneId) => req(`/scenes/${sceneId}/analysis-groups`),
}
