const BASE = '/api'

async function req(path, options) {
  const r = await fetch(BASE + path, options)
  if (!r.ok) throw new Error(`${r.status}: ${await r.text()}`)
  return r.json()
}

export const api = {
  scenes: () => req('/scenes'),
  seed: () => req('/scenes/seed', { method: 'POST' }),
  scene: (id) => req(`/scenes/${id}`),
  sunpath: (id, date) => req(`/scenes/${id}/sunpath?date=${date}`),
  run: (scene_id, date, step_minutes = 5) =>
    req('/analysis/run', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ scene_id, date, step_minutes }),
    }),
  runResult: (runId) => req(`/analysis/${runId}`),
  trace: (runId, pointId, time) =>
    req(`/analysis/${runId}/points/${pointId}/trace` + (time ? `?time=${time}` : '')),
  snapshot: (id) => req(`/snapshots/${id}`),

  // 多日期分析组：一次提交 2～5 个日期 + 统一步长，整组共用提交时快照
  createGroup: (scene_id, dates, step_minutes = 5) =>
    req('/analysis/groups', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ scene_id, dates, step_minutes }),
    }),
  group: (groupId) => req(`/analysis/groups/${groupId}`),
  sceneGroups: (sceneId) => req(`/scenes/${sceneId}/groups`),
}
