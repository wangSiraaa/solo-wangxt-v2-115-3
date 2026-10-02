import React from 'react'
import { fmtMin, fmtTime } from '../util.js'

/** 结果面板：逐时采样（快览）与连续遮挡时段（正式口径）严格分区展示。 */
export default function ResultsPanel({ run, result, onHoverInterval, onTrace }) {
  if (!run || !result) {
    return <div className="panel muted">选择测点后显示结果。运行分析后点击三维视图中的测点球。</div>
  }
  const s = result.summary
  return (
    <div className="panel">
      <h3>测点 #{result.point_id}</h3>
      <div className="disclaimer">{run.disclaimer}</div>
      <table className="summary">
        <tbody>
          <tr><td>白天时长</td><td>{fmtMin(s.daylight_minutes)}</td></tr>
          <tr><td>连续口径·累计日照</td><td><b>{fmtMin(s.sunlit_minutes)}</b></td></tr>
          <tr><td>连续口径·最长连续日照</td><td><b>{fmtMin(s.longest_continuous_sunlit_minutes)}</b></td></tr>
        </tbody>
      </table>
      <div className="muted small">{s.criterion}</div>

      <h4>连续遮挡/日照时段（步长 {run.step_minutes} min）</h4>
      <div className="intervals">
        {result.continuous_intervals
          .filter((iv) => iv.status !== 'night')
          .map((iv, i) => (
            <div key={i}
              className={`interval ${iv.status}`}
              onMouseEnter={() => onHoverInterval?.(iv)}
              onMouseLeave={() => onHoverInterval?.(null)}>
              {fmtTime(iv.start)}–{fmtTime(iv.end)}
              {iv.status === 'shaded' ? ` 遮挡:${iv.occluder}` : ' 日照'}
            </div>
          ))}
      </div>

      <h4>逐时采样（仅整点快览，<u>不可</u>累加为日照时长）</h4>
      <div className="hourly">
        {result.hourly_samples.map((h, i) => (
          <span key={i} className={`cell ${h.status}`}
            title={`${fmtTime(h.time)} ${h.status}${h.occluder ? ' ' + h.occluder : ''}`}>
            {fmtTime(h.time).slice(0, 2)}
          </span>
        ))}
      </div>
      <button onClick={() => onTrace?.(result.point_id)}>追查该点全部遮挡物</button>
    </div>
  )
}
