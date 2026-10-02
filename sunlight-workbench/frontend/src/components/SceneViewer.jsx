import React, { useMemo } from 'react'
import * as THREE from 'three'
import { Canvas } from '@react-three/fiber'
import { OrbitControls, Line, Html } from '@react-three/drei'
import { toThree } from '../util.js'

const RAY_LEN = 120
const SUN_R = 160

function Building({ b, highlighted, onClick }) {
  const geom = useMemo(() => {
    const shape = new THREE.Shape()
    b.footprint.forEach(([x, y], i) => (i ? shape.lineTo(x, y) : shape.moveTo(x, y)))
    const g = new THREE.ExtrudeGeometry(shape, {
      depth: b.top_height - b.base_height, bevelEnabled: false,
    })
    g.translate(0, 0, b.base_height)
    g.rotateX(-Math.PI / 2) // 模型 z-up → three y-up
    return g
  }, [b])
  return (
    <mesh geometry={geom} onClick={(e) => { e.stopPropagation(); onClick?.(b.name) }}>
      <meshStandardMaterial
        color={b.color}
        emissive={highlighted ? '#ff5722' : '#000'}
        emissiveIntensity={highlighted ? 0.7 : 0}
        transparent opacity={highlighted ? 0.95 : 0.85}
      />
    </mesh>
  )
}

function MeasurePoint({ p, status, selected, onClick }) {
  const color = selected ? '#ff4081' : status === 'shaded' ? '#4a6fa5'
    : status === 'sunlit' ? '#f6c453' : '#999'
  return (
    <mesh position={toThree(p.position)}
      onClick={(e) => { e.stopPropagation(); onClick?.(p.id) }}>
      <sphereGeometry args={[0.45, 16, 16]} />
      <meshStandardMaterial color={color} />
    </mesh>
  )
}

/** 真北箭头：模型 +y 轴（= 场景声明的模型北），红色；真北由 north_offset 决定，
    场景页已统一口径，这里画模型北并标注偏角。 */
function NorthArrow() {
  return (
    <group position={[0, 0.2, 0]}>
      <Line points={[toThree([0, 0, 0]), toThree([0, 18, 0])]} color="#d32f2f" lineWidth={3} />
      <Html position={toThree([0, 20, 0])} center>
        <div className="north-label">模型北 ↑</div>
      </Html>
    </group>
  )
}

export default function SceneViewer({
  payload, sunpath, timeIdx, pointStatus, selectedPointId,
  highlightOccluder, onSelectPoint, onSelectBuilding,
}) {
  const sun = sunpath?.points?.[timeIdx]
  const sunPos = sun ? toThree(sun.dir.map((v) => v * SUN_R)) : null
  return (
    <Canvas camera={{ position: [60, 70, 90], up: [0, 1, 0], fov: 45 }}
      style={{ background: '#10141c' }}>
      <ambientLight intensity={0.5} />
      <directionalLight position={sunPos ?? [50, 80, 30]} intensity={1.2} />
      <gridHelper args={[200, 40, '#2a3242', '#1c2330']} rotation={[0, 0, 0]} />
      <NorthArrow />
      {payload?.buildings.map((b) => (
        <Building key={b.id} b={b}
          highlighted={highlightOccluder === b.name}
          onClick={onSelectBuilding} />
      ))}
      {payload?.points.map((p) => (
        <MeasurePoint key={p.id} p={p}
          status={pointStatus?.[p.id]?.status}
          selected={p.id === selectedPointId}
          onClick={onSelectPoint} />
      ))}
      {/* 太阳路径（当日，模型坐标系） */}
      {sunpath && (
        <Line points={sunpath.points.map((s) => toThree(s.dir.map((v) => v * SUN_R)))}
          color="#f6c453" dashed dashSize={2} gapSize={1} />
      )}
      {sunPos && (
        <mesh position={sunPos}>
          <sphereGeometry args={[3, 16, 16]} />
          <meshBasicMaterial color="#ffeb3b" />
        </mesh>
      )}
      {/* 测点→太阳 射线：绿=晒到，红=被遮挡（遮挡物见右侧面板/点击建筑高亮） */}
      {sun && payload?.points.map((p) => {
        const st = pointStatus?.[p.id]
        if (!st || st.status === 'night') return null
        const o = p.position
        const e = [o[0] + sun.dir[0] * RAY_LEN, o[1] + sun.dir[1] * RAY_LEN,
                   o[2] + sun.dir[2] * RAY_LEN]
        return (
          <Line key={p.id} points={[toThree(o), toThree(e)]}
            color={st.status === 'sunlit' ? '#7ce38b' : '#ef5350'} lineWidth={2} />
        )
      })}
      <OrbitControls makeDefault />
    </Canvas>
  )
}
