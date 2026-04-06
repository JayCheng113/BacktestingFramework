import { useState, useEffect } from 'react'
import type { ParamSchema } from '../types'

const inputStyle = { backgroundColor: 'var(--bg-primary)', border: '1px solid var(--border)', color: 'var(--text-primary)' }

const MODE_LABELS: Record<string, string> = {
  equal: '等权',
  manual: '手动权重',
  return_weighted: '收益加权',
  inverse_vol: '反向波动率',
}

export type EnsembleMode = 'equal' | 'manual' | 'return_weighted' | 'inverse_vol'

export interface SubStrategyDef {
  name: string
  params: Record<string, any>
}

export interface EnsembleConfig {
  mode: EnsembleMode
  sub_strategies: SubStrategyDef[]
  ensemble_weights?: number[]
  warmup_rebalances: number
  correlation_threshold: number
}

interface StrategyMeta {
  name: string
  description?: string
  parameters: Record<string, ParamSchema>
  is_ensemble?: boolean
}

interface Props {
  strategies: StrategyMeta[]
  factors: string[]
  onChange: (config: EnsembleConfig) => void
}

export default function EnsembleBuilder({ strategies, factors, onChange }: Props) {
  const [mode, setMode] = useState<EnsembleMode>('equal')
  const [subs, setSubs] = useState<SubStrategyDef[]>([])
  const [weights, setWeights] = useState<number[]>([])
  const [warmup, setWarmup] = useState(8)
  const [corrThreshold, setCorrThreshold] = useState(0.9)

  useEffect(() => {
    onChange({
      mode,
      sub_strategies: subs,
      ensemble_weights: mode === 'manual' ? weights : undefined,
      warmup_rebalances: warmup,
      correlation_threshold: corrThreshold,
    })
  }, [mode, subs, weights, warmup, corrThreshold])

  const availableStrategies = strategies.filter(s => !s.is_ensemble)

  const addStrategy = (name: string) => {
    if (subs.length >= 5 || !name) return
    const s = availableStrategies.find(s => s.name === name)
    const defaults: Record<string, any> = {}
    if (s) {
      for (const [k, v] of Object.entries(s.parameters)) {
        defaults[k] = v.default
      }
    }
    setSubs(prev => [...prev, { name, params: defaults }])
    setWeights(prev => [...prev, 1])
  }

  const removeStrategy = (idx: number) => {
    setSubs(prev => prev.filter((_, i) => i !== idx))
    setWeights(prev => prev.filter((_, i) => i !== idx))
  }

  const updateSubParam = (idx: number, key: string, value: any) => {
    setSubs(prev => prev.map((s, i) =>
      i === idx ? { ...s, params: { ...s.params, [key]: value } } : s
    ))
  }

  const updateWeight = (idx: number, val: number) => {
    setWeights(prev => prev.map((w, i) => i === idx ? val : w))
  }

  return (
    <div className="space-y-3">
      {/* Mode selection */}
      <div>
        <label className="text-xs" style={{ color: 'var(--text-secondary)' }}>组合模式</label>
        <div className="flex gap-3 mt-1 flex-wrap">
          {Object.entries(MODE_LABELS).map(([k, label]) => (
            <label key={k} className="flex items-center gap-1 text-xs" style={{ color: 'var(--text-secondary)' }}>
              <input type="radio" checked={mode === k} onChange={() => setMode(k as EnsembleMode)} /> {label}
            </label>
          ))}
        </div>
      </div>

      {/* Add sub-strategy */}
      <div>
        <label className="text-xs" style={{ color: 'var(--text-secondary)' }}>
          子策略 ({subs.length}/5)
        </label>
        <select onChange={e => { addStrategy(e.target.value); e.target.value = '' }}
          className="w-full px-2 py-1.5 rounded text-sm mt-1" style={inputStyle}
          disabled={subs.length >= 5}>
          <option value="">+ 添加子策略</option>
          {availableStrategies.map(s => (
            <option key={s.name} value={s.name}>{s.name}</option>
          ))}
        </select>
      </div>

      {/* Sub-strategy cards */}
      {subs.map((sub, idx) => {
        const meta = availableStrategies.find(s => s.name === sub.name)
        const dupeCount = subs.filter(x => x.name === sub.name).length
        const dupeIdx = subs.slice(0, idx).filter(x => x.name === sub.name).length + 1
        return (
          <div key={idx} className="p-3 rounded" style={{ backgroundColor: 'var(--bg-primary)', border: '1px solid var(--border)' }}>
            <div className="flex justify-between items-center mb-2">
              <span className="text-sm font-medium" style={{ color: 'var(--text-primary)' }}>
                {sub.name}{dupeCount > 1 ? ` #${dupeIdx}` : ''}
              </span>
              <div className="flex items-center gap-2">
                {mode === 'manual' && (
                  <div className="flex items-center gap-1">
                    <span className="text-xs" style={{ color: 'var(--text-secondary)' }}>权重</span>
                    <input type="number" value={weights[idx] ?? 1} min={0} step={0.1}
                      onChange={e => updateWeight(idx, Number(e.target.value))}
                      className="w-16 px-1 py-0.5 rounded text-xs" style={inputStyle} />
                  </div>
                )}
                <button onClick={() => removeStrategy(idx)}
                  className="text-xs px-1.5 py-0.5 rounded"
                  style={{ color: '#ef4444', border: '1px solid rgba(239,68,68,0.3)' }}>
                  移除
                </button>
              </div>
            </div>
            {/* Sub-strategy params */}
            {meta && Object.entries(meta.parameters).map(([key, schema]) => {
              const label = schema.label || key
              const val = sub.params[key] ?? schema.default
              if (schema.type === 'select' || schema.type === 'multi_select') {
                const options: string[] = schema.options ?? (factors.length > 0 ? factors : [String(schema.default ?? '')])
                return (
                  <div key={key} className="flex items-center gap-2 mt-1">
                    <label className="text-xs w-20 shrink-0" style={{ color: 'var(--text-secondary)' }}>{label}</label>
                    <select value={val ?? ''} onChange={e => updateSubParam(idx, key, e.target.value)}
                      className="flex-1 px-2 py-1 rounded text-xs" style={inputStyle}>
                      {options.map(o => <option key={o} value={o}>{o}</option>)}
                    </select>
                  </div>
                )
              }
              if (schema.type === 'bool' || typeof val === 'boolean') {
                return (
                  <div key={key} className="flex items-center gap-2 mt-1">
                    <label className="text-xs w-20 shrink-0" style={{ color: 'var(--text-secondary)' }}>{label}</label>
                    <input type="checkbox" checked={!!val}
                      onChange={e => updateSubParam(idx, key, e.target.checked)} />
                  </div>
                )
              }
              return (
                <div key={key} className="flex items-center gap-2 mt-1">
                  <label className="text-xs w-20 shrink-0" style={{ color: 'var(--text-secondary)' }}>{label}</label>
                  <input type="number" value={val ?? 0}
                    min={schema.min} max={schema.max}
                    onChange={e => updateSubParam(idx, key, Number(e.target.value))}
                    className="flex-1 px-2 py-1 rounded text-xs" style={inputStyle} />
                </div>
              )
            })}
          </div>
        )
      })}

      {/* Advanced settings */}
      <details>
        <summary className="text-xs cursor-pointer" style={{ color: 'var(--text-secondary)' }}>高级设置</summary>
        <div className="mt-2 flex gap-4 flex-wrap">
          <div className="flex items-center gap-1">
            <label className="text-xs" style={{ color: 'var(--text-secondary)' }}>预热次数</label>
            <input type="number" value={warmup} min={1} max={50}
              onChange={e => setWarmup(Number(e.target.value) || 8)}
              className="w-16 px-2 py-1 rounded text-xs" style={inputStyle} />
          </div>
          <div className="flex items-center gap-1">
            <label className="text-xs" style={{ color: 'var(--text-secondary)' }}>相关性阈值</label>
            <input type="number" value={corrThreshold} min={0} max={1} step={0.05}
              onChange={e => setCorrThreshold(Number(e.target.value) || 0.9)}
              className="w-16 px-2 py-1 rounded text-xs" style={inputStyle} />
          </div>
        </div>
      </details>

      {subs.length < 2 && (
        <p className="text-xs" style={{ color: '#f59e0b' }}>请至少选择 2 个子策略</p>
      )}
    </div>
  )
}
