import React, { useState } from 'react'
import { fmtMin, fmtTime } from '../util.js'

/**
 * 多日期分析组面板：
 * - 表格：行=测点，列=各日期（连续口径累计日照 / 最长连续日照）；
 * - 趋势图：每测点一条折线，横轴为组内日期，纵轴为连续口径累计分钟；
 * - 点日期列/数据点即下钻到该次普通单日运行（trace/整点快览在单日面板）。
 * 汇总只使用连续采样口径；整点快览不下放到组级，避免两套口径混淆。
 */
export default function GroupPanel({ group, onDrill }) {
  const [view, setView] = useState('table')
  const [metric, setMetric] = useState('sunlit_minutes')
  const members = group.members
  const pointIds = members[0]?.points.map((p) => p.point_id) ?? []
  const nameOf = Object.fromEntries(
    (members[0]?.points ?? []).map((p) => [p.point_id, p.point_name]))

  return (
    <div className="panel">
      <h3>多日期分析组 #{group.group_id}</h3>
      <div className="disclaimer">{group.disclaimer}</div>
      <div className="muted small">
        快照 #{group.snapshot_id} · 统一连续采样步长 {group.step_minutes} min ·
        共 {members.length} 个日期，均来自同一次提交时的场景配置
      </div>
      <div className="seg">
        <button className={view === 'table' ? 'on' : ''}
          onClick={() => setView('table')}>汇总表</button>
        <button className={view === 'trend' ? 'on' : ''}
          onClick={() => setView('trend')}>趋势图</button>
      </div>

      {view === 'table' && (
        <table className="group-table">
          <thead>
            <tr>
              <th>测点</th>
              {members.map((m) => (
                <th key={m.run_id} className="drill"
                  title="下钻到该日期单日运行"
                  onClick={() => onDrill?.(m.run_id)}>
                  {m.date} ↓
                </th>
              ))}
            </tr>
          </thead>
          <tbody>
            {pointIds.map((pid) => (
              <tr key={pid}>
                <td title={nameOf[pid]}>{nameOf[pid] ?? `#${pid}`}</td>
                {members.map((m) => {
                  const p = m.points.find((x) => x.point_id === pid)
                  const iv = p.longest_sunlit_interval
                  return (
                    <td key={m.run_id} className="drill"
                      title={iv
                        ? `最长连续日照 ${fmtMin(p.longest_continuous_sunlit_minutes)}（${fmtTime(iv.start)}–${fmtTime(iv.end)}）；点击下钻 ${m.date}`
                        : `点击下钻 ${m.date}`}
                      onClick={() => onDrill?.(m.run_id)}>
                      <b>{fmtMin(p.sunlit_minutes)}</b>
                      <span className="muted small">
                        {' / 最长 '}{fmtMin(p.longest_continuous_sunlit_minutes)}
                      </span>
                    </td>
                  )
                })}
              </tr>
            ))}
          </tbody>
        </table>
      )}
      {view === 'trend' && <TrendChart group={group} metric={metric}
        setMetric={setMetric} onDrill={onDrill} />}

      <div className="muted small">
        数字均为<b>连续采样口径</b>（步长 {group.step_minutes} min 全天扫描）；
        整点快览、连续时段明细与单点 trace 请点任一列下钻到单日运行。
      </div>
    </div>
  )
}

function TrendChart({ group, metric, setMetric, onDrill }) {
  const [W, H, PAD_L, PAD_R, PAD_T, PAD_B] = [310, 260, 44, 12, 14, 34]
  const members = group.members
  const pointIds = members[0]?.points.map((p) => p.point_id) ?? []
  const palette = ['#e65100', '#1565c0', '#2e7d32', '#6a1b9a', '#00838f', '#ad1457']
  const maxV = Math.max(1, ...members.flatMap((m) =>
    m.points.map((p) => p[metric])))
  const xAt = (i) => PAD_L + (members.length === 1
    ? 0 : i * (W - PAD_L - PAD_R) / (members.length - 1))
  const yAt = (v) => H - PAD_B - v / maxV * (H - PAD_T - PAD_B)
  const label = metric === 'sunlit_minutes' ? '累计日照' : '最长连续日照'
  return (
    <div>
      <div className="seg">
        <button className={metric === 'sunlit_minutes' ? 'on' : ''}
          onClick={() => setMetric('sunlit_minutes')}>累计日照分钟</button>
        <button className={metric === 'longest_continuous_sunlit_minutes' ? 'on' : ''}
          onClick={() => setMetric('longest_continuous_sunlit_minutes')}>
          最长连续日照分钟
        </button>
      </div>
      <svg width="100%" viewBox={`0 0 ${W} ${H}`} className="trend">
        {[0, 0.25, 0.5, 0.75, 1].map((f) => (
          <g key={f}>
            <line x1={PAD_L} x2={W - PAD_R}
              y1={PAD_T + f * (H - PAD_T - PAD_B)}
              y2={PAD_T + f * (H - PAD_T - PAD_B)}
              stroke="#e3e6ea" />
            <text x={PAD_L - 4} y={PAD_T + f * (H - PAD_T - PAD_B) + 3}
              textAnchor="end" fontSize="9" fill="#888">
              {Math.round(maxV * (1 - f))}
            </text>
          </g>
        ))}
        {pointIds.map((pid, pi) => {
          const pts = members.map((m, i) => {
            const p = m.points.find((x) => x.point_id === pid)
            return [xAt(i), yAt(p[metric]), m, p]
          })
          const color = palette[pi % palette.length]
          return (
            <g key={pid}>
              <polyline fill="none" stroke={color} strokeWidth="1.8"
                points={pts.map((q) => `${q[0]},${q[1]}`).join(' ')} />
              {pts.map(([x, y, m, p], i) => (
                <circle key={i} cx={x} cy={y} r="3.5" fill={color}
                  style={{ cursor: 'pointer' }}
                  onClick={() => onDrill?.(m.run_id)}>
                  <title>
                    {`${p.point_name} · ${m.date} · ${label} ${fmtMin(p[metric])}（点击下钻）`}
                  </title>
                </circle>
              ))}
            </g>
          )
        })}
        {members.map((m, i) => (
          <text key={m.run_id} x={xAt(i)} y={H - PAD_B + 14}
            textAnchor="middle" fontSize="9" fill="#555"
            style={{ cursor: 'pointer' }}
            onClick={() => onDrill?.(m.run_id)}>
            {m.date.slice(5)}
          </text>
        ))}
        <text x={PAD_L - 34} y={PAD_T - 2} fontSize="9" fill="#888">分钟</text>
      </svg>
      <div className="legend">
        {pointIds.map((pid, i) => (
          <span key={pid} className="legend-item">
            <i style={{ background: palette[i % palette.length] }} />
            {members[0].points.find((x) => x.point_id === pid)?.point_name ?? `#${pid}`}
          </span>
        ))}
      </div>
    </div>
  )
}
