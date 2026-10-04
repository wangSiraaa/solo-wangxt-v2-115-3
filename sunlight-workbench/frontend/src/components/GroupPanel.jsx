import React, { useMemo, useState } from 'react'
import { fmtMin, fmtTime } from '../util.js'

const COLORS = ['#d62728', '#1f77b4', '#2ca02c', '#ff7f0e', '#9467bd',
  '#8c564b', '#17becf', '#bcbd22']

/** 多日期分析组面板（连续采样口径）：汇总表格 / 趋势图，点击日期行下钻单日运行。 */
export default function GroupPanel({ group, points, activeRunId, onPickDate, onClose }) {
  const [view, setView] = useState('table')
  const pointName = useMemo(() => {
    const m = {}
    for (const p of points ?? []) m[p.id] = p.name
    return m
  }, [points])
  if (!group) return null
  const pointIds = group.runs[0]?.points.map((p) => p.point_id) ?? []
  const cellOf = (run, pid) => run.points.find((p) => p.point_id === pid)

  // 趋势图坐标：x=日期序，y=连续口径晒到分钟
  const W = 300, H = 150, PAD = 28
  const maxMin = Math.max(60, ...group.runs.flatMap((r) =>
    r.points.map((p) => p.sunlit_minutes)))
  const xAt = (i) => PAD + (group.runs.length === 1 ? W / 2
    : i * (W - 2 * PAD) / (group.runs.length - 1))
  const yAt = (m) => H - PAD - (m / maxMin) * (H - 2 * PAD)

  return (
    <div className="panel group-panel">
      <div className="group-head">
        <h3>多日期分析组 #{group.group_id}</h3>
        <button onClick={onClose}>关闭</button>
      </div>
      <div className="muted small">
        连续采样口径 · 步长 {group.step_minutes} min · 快照 #{group.snapshot_id}
        （提交时场景配置，后续编辑不影响本组）
      </div>
      <div className="disclaimer">{group.disclaimer}</div>
      <div className="view-toggle">
        <button className={view === 'table' ? 'active' : ''}
          onClick={() => setView('table')}>汇总表格</button>
        <button className={view === 'chart' ? 'active' : ''}
          onClick={() => setView('chart')}>趋势图</button>
      </div>

      {view === 'table' && (
        <table className="group-table">
          <thead>
            <tr>
              <th>日期</th>
              {pointIds.map((pid) => (
                <th key={pid} title={pointName[pid]}>{pointName[pid] ?? `#${pid}`}</th>
              ))}
            </tr>
          </thead>
          <tbody>
            {group.runs.map((r) => (
              <tr key={r.run_id}
                className={r.run_id === activeRunId ? 'active' : ''}
                title="点击下钻该日单日运行"
                onClick={() => onPickDate?.(r.run_id, r.date)}>
                <td>{r.date.slice(5)}</td>
                {pointIds.map((pid) => {
                  const c = cellOf(r, pid)
                  return (
                    <td key={pid}>
                      <div>{fmtMin(c.sunlit_minutes)}</div>
                      <div className="muted tiny">
                        最长{c.longest_sunlit_interval
                          ? `${fmtMin(c.longest_sunlit_interval.minutes)} ` +
                            `${fmtTime(c.longest_sunlit_interval.start)}–` +
                            `${fmtTime(c.longest_sunlit_interval.end)}`
                          : '0'}
                      </div>
                    </td>
                  )
                })}
              </tr>
            ))}
          </tbody>
        </table>
      )}

      {view === 'chart' && (
        <>
          <svg width={W} height={H} className="trend">
            <line x1={PAD} y1={H - PAD} x2={W - 4} y2={H - PAD} stroke="#999" />
            <line x1={PAD} y1={4} x2={PAD} y2={H - PAD} stroke="#999" />
            <text x={2} y={10} className="axis">{fmtMin(maxMin)}</text>
            <text x={2} y={H - PAD} className="axis">0</text>
            {group.runs.map((r, i) => (
              <text key={r.run_id} x={xAt(i)} y={H - 8} textAnchor="middle"
                className="axis">{r.date.slice(5)}</text>
            ))}
            {pointIds.map((pid, k) => {
              const pts = group.runs.map((r, i) =>
                [xAt(i), yAt(cellOf(r, pid).sunlit_minutes)])
              return (
                <g key={pid}>
                  <polyline fill="none" stroke={COLORS[k % COLORS.length]}
                    strokeWidth={2}
                    points={pts.map((p) => p.join(',')).join(' ')} />
                  {pts.map((p, i) => (
                    <circle key={i} cx={p[0]} cy={p[1]} r={3}
                      fill={COLORS[k % COLORS.length]}
                      onClick={() => onPickDate?.(group.runs[i].run_id, group.runs[i].date)}>
                      <title>{pointName[pid]} {group.runs[i].date}：
                        {fmtMin(cellOf(group.runs[i], pid).sunlit_minutes)}</title>
                    </circle>
                  ))}
                </g>
              )
            })}
          </svg>
          <div className="legend">
            {pointIds.map((pid, k) => (
              <span key={pid}>
                <i style={{ background: COLORS[k % COLORS.length] }} />
                {pointName[pid] ?? `#${pid}`}
              </span>
            ))}
          </div>
        </>
      )}

      <div className="muted small">
        单元格 = 连续口径累计晒到分钟 + 最长连续区间；整点快览（逐时采样）
        请点日期行/圆点下钻到对应单日运行查看。
      </div>
    </div>
  )
}
