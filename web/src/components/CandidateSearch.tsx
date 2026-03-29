import { useState, useEffect, forwardRef } from 'react'
import DatePicker from 'react-datepicker'
import 'react-datepicker/dist/react-datepicker.css'
import { searchCandidates, listStrategies } from '../api'
import type { StrategyInfo, CandidateResult, SearchResult } from '../types'

const inputStyle = { backgroundColor: 'var(--bg-primary)', border: '1px solid var(--border)', color: 'var(--text-primary)' }

const DateBtn = forwardRef<HTMLButtonElement, { value?: string; onClick?: () => void }>(
  ({ value, onClick }, ref) => (
    <button ref={ref} type="button" onClick={onClick}
      className="w-full px-2 py-1.5 rounded text-sm text-left" style={inputStyle}>
      {value || 'Select'}
    </button>
  )
)

interface ParamRangeState {
  name: string
  type: string       // "int" | "float"
  min: number
  max: number
  step: number
  defaultVal: number
}

/** Count values in range WITHOUT allocating an array. O(1). */
function countValues(pr: ParamRangeState): number {
  if (pr.step <= 0 || pr.min > pr.max) return 0
  return Math.floor((pr.max - pr.min) / pr.step) + 1
}

/** Generate values — only for preview (capped at `limit`). */
function generateValues(pr: ParamRangeState, limit: number = 50): number[] {
  if (pr.step <= 0 || pr.min > pr.max) return []
  const vals: number[] = []
  for (let v = pr.min; v <= pr.max + pr.step * 0.001 && vals.length < limit; v += pr.step) {
    vals.push(pr.type === 'int' ? Math.round(v) : Math.round(v * 1000) / 1000)
  }
  return [...new Set(vals)]
}

function totalCombinations(ranges: ParamRangeState[]): number {
  if (ranges.length === 0) return 1
  return ranges.reduce((acc, pr) => acc * Math.max(countValues(pr), 1), 1)
}

export default function CandidateSearch() {
  const [strategies, setStrategies] = useState<StrategyInfo[]>([])
  const [searching, setSearching] = useState(false)
  const [result, setResult] = useState<SearchResult | null>(null)

  const [strategyName, setStrategyName] = useState('')
  const [symbol, setSymbol] = useState('000001.SZ')
  const [period, setPeriod] = useState('daily')
  const [startDate, setStartDate] = useState<Date>(new Date(2020, 0, 1))
  const [endDate, setEndDate] = useState<Date>(new Date(2024, 11, 31))
  const [mode, setMode] = useState<'grid' | 'random'>('grid')
  const [nSamples, setNSamples] = useState(20)
  const [skipPrefilter, setSkipPrefilter] = useState(false)
  const [paramRanges, setParamRanges] = useState<ParamRangeState[]>([])

  const toStr = (d: Date) => `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, '0')}-${String(d.getDate()).padStart(2, '0')}`

  useEffect(() => {
    listStrategies().then(r => {
      setStrategies(r.data)
      if (r.data.length > 0) handleStrategyChange(r.data[0].name, r.data)
    }).catch(() => {})
  }, [])

  const handleStrategyChange = (name: string, strats?: StrategyInfo[]) => {
    setStrategyName(name)
    const s = (strats || strategies).find(s => s.name === name)
    if (s) {
      setParamRanges(Object.entries(s.parameters).map(([k, v]: [string, any]) => {
        const def = v.default ?? 0
        const type = v.type || 'float'
        const min = v.min ?? (type === 'int' ? Math.max(1, def - 10) : def * 0.5)
        const max = v.max ?? (type === 'int' ? def + 20 : def * 2)
        const step = type === 'int' ? Math.max(1, Math.round((max - min) / 5)) : (max - min) / 5
        return { name: k, type, min, max, step, defaultVal: def }
      }))
    }
  }

  const updateRange = (i: number, field: keyof ParamRangeState, val: number) => {
    const next = [...paramRanges]
    next[i] = { ...next[i], [field]: val }
    setParamRanges(next)
  }

  const hasRangeErrors = paramRanges.some(pr => pr.min > pr.max || pr.step <= 0)
  const combos = mode === 'grid' ? totalCombinations(paramRanges) : Math.min(nSamples, totalCombinations(paramRanges))

  const handleSearch = async () => {
    setSearching(true)
    setResult(null)
    try {
      const ranges = paramRanges.map(pr => ({
        name: pr.name,
        values: generateValues(pr, 10000),
      })).filter(pr => pr.values.length > 0)

      const res = await searchCandidates({
        strategy_name: strategyName,
        param_ranges: ranges,
        symbol,
        period,
        start_date: toStr(startDate),
        end_date: toStr(endDate),
        mode,
        n_samples: nSamples,
        skip_prefilter: skipPrefilter,
      })
      setResult(res.data)
    } catch (e: any) {
      alert(e?.response?.data?.detail || 'Search failed')
    } finally {
      setSearching(false)
    }
  }

  return (
    <div className="space-y-4">
      <div className="rounded-lg p-4 space-y-4" style={{ backgroundColor: 'var(--bg-secondary)', border: '1px solid var(--border)' }}>
        <h3 className="text-lg font-semibold" style={{ color: 'var(--text-primary)' }}>Parameter Search</h3>

        {/* Row 1: Strategy, Symbol, Period, Dates */}
        <div className="grid grid-cols-2 md:grid-cols-5 gap-3">
          <div>
            <label className="text-xs" style={{ color: 'var(--text-secondary)' }}>Strategy</label>
            <select value={strategyName} onChange={e => handleStrategyChange(e.target.value)}
              className="w-full px-2 py-1.5 rounded text-sm" style={inputStyle}>
              {strategies.map(s => <option key={s.name} value={s.name}>{s.name}</option>)}
            </select>
          </div>
          <div>
            <label className="text-xs" style={{ color: 'var(--text-secondary)' }}>Symbol</label>
            <input value={symbol} onChange={e => setSymbol(e.target.value)}
              className="w-full px-2 py-1.5 rounded text-sm" style={inputStyle} />
          </div>
          <div>
            <label className="text-xs" style={{ color: 'var(--text-secondary)' }}>Period</label>
            <select value={period} onChange={e => setPeriod(e.target.value)}
              className="w-full px-2 py-1.5 rounded text-sm" style={inputStyle}>
              <option value="daily">Daily</option>
              <option value="weekly">Weekly</option>
              <option value="monthly">Monthly</option>
            </select>
          </div>
          <div>
            <label className="text-xs" style={{ color: 'var(--text-secondary)' }}>Start</label>
            <DatePicker selected={startDate} dateFormat="yyyy-MM-dd"
              onChange={(d: Date | null) => { if (d) { setStartDate(d); if (d > endDate) setEndDate(d) } }}
              maxDate={endDate} showMonthDropdown showYearDropdown dropdownMode="select"
              customInput={<DateBtn />} />
          </div>
          <div>
            <label className="text-xs" style={{ color: 'var(--text-secondary)' }}>End</label>
            <DatePicker selected={endDate} dateFormat="yyyy-MM-dd"
              onChange={(d: Date | null) => { if (d) { setEndDate(d); if (d < startDate) setStartDate(d) } }}
              minDate={startDate} maxDate={new Date()} showMonthDropdown showYearDropdown dropdownMode="select"
              customInput={<DateBtn />} />
          </div>
        </div>

        {/* Row 2: Mode + Options */}
        <div className="flex gap-4 items-center flex-wrap">
          <label className="flex items-center gap-1.5 text-sm" style={{ color: 'var(--text-secondary)' }}>
            <input type="radio" checked={mode === 'grid'} onChange={() => setMode('grid')} /> Grid
          </label>
          <label className="flex items-center gap-1.5 text-sm" style={{ color: 'var(--text-secondary)' }}>
            <input type="radio" checked={mode === 'random'} onChange={() => setMode('random')} /> Random
          </label>
          {mode === 'random' && (
            <div className="flex items-center gap-1.5">
              <label className="text-xs" style={{ color: 'var(--text-secondary)' }}>Samples</label>
              <input type="number" value={nSamples} min={1} max={1000}
                onChange={e => setNSamples(Number(e.target.value))}
                className="w-20 px-2 py-1 rounded text-sm" style={inputStyle} />
            </div>
          )}
          <label className="flex items-center gap-1.5 text-sm" style={{ color: 'var(--text-secondary)' }}>
            <input type="checkbox" checked={skipPrefilter} onChange={e => setSkipPrefilter(e.target.checked)} />
            Skip Pre-filter
          </label>
        </div>

        {/* Row 3: Parameter Ranges — Min/Max/Step */}
        {paramRanges.length > 0 && (
          <div className="space-y-2">
            <label className="text-xs" style={{ color: 'var(--text-secondary)' }}>Parameter Ranges</label>
            <div className="rounded p-3 space-y-2" style={{ backgroundColor: 'var(--bg-primary)', border: '1px solid var(--border)' }}>
              {/* Header */}
              <div className="grid grid-cols-[120px_1fr_1fr_1fr_1fr] gap-2 text-xs" style={{ color: 'var(--text-secondary)' }}>
                <span>Param</span><span>Min</span><span>Max</span><span>Step</span><span>Values</span>
              </div>
              {paramRanges.map((pr, i) => {
                const count = countValues(pr)
                const preview = generateValues(pr, 6)  // only 6 for display
                const hasError = pr.min > pr.max || pr.step <= 0
                const errStyle = hasError ? { ...inputStyle, border: '1px solid #ef4444' } : inputStyle
                return (
                  <div key={pr.name} className="grid grid-cols-[120px_1fr_1fr_1fr_1fr] gap-2 items-center">
                    <span className="text-sm font-medium truncate" style={{ color: 'var(--text-primary)' }}
                      title={pr.name}>{pr.name}</span>
                    <input type="number" value={pr.min} step={pr.type === 'int' ? 1 : 0.1}
                      onChange={e => updateRange(i, 'min', Number(e.target.value))}
                      className="px-2 py-1 rounded text-sm w-full" style={pr.min > pr.max ? errStyle : inputStyle} />
                    <input type="number" value={pr.max} step={pr.type === 'int' ? 1 : 0.1}
                      onChange={e => updateRange(i, 'max', Number(e.target.value))}
                      className="px-2 py-1 rounded text-sm w-full" style={pr.min > pr.max ? errStyle : inputStyle} />
                    <input type="number" value={pr.step} step={pr.type === 'int' ? 1 : 0.1}
                      min={pr.type === 'int' ? 1 : 0.01}
                      onChange={e => updateRange(i, 'step', Math.max(pr.type === 'int' ? 1 : 0.01, Number(e.target.value)))}
                      className="px-2 py-1 rounded text-sm w-full" style={pr.step <= 0 ? errStyle : inputStyle} />
                    <span className="text-xs truncate"
                      style={{ color: hasError ? '#ef4444' : 'var(--text-secondary)' }}
                      title={hasError ? (pr.min > pr.max ? 'Min > Max' : 'Step must be > 0') : preview.join(', ')}>
                      {hasError
                        ? (pr.min > pr.max ? 'Min > Max' : 'Step > 0')
                        : `[${count}] ${preview.join(', ')}${count > 6 ? '...' : ''}`}
                    </span>
                  </div>
                )
              })}
            </div>
          </div>
        )}

        {/* Row 4: Search button + combo count */}
        <div className="flex items-center gap-3">
          <button onClick={handleSearch} disabled={searching || combos > 1000 || hasRangeErrors || !strategyName}
            className="px-4 py-2 rounded text-sm font-medium"
            style={{ backgroundColor: 'var(--color-accent)', color: '#fff', opacity: (searching || combos > 1000 || hasRangeErrors || !strategyName) ? 0.5 : 1 }}>
            {searching ? 'Searching...' : 'Search'}
          </button>
          <span className="text-xs" style={{ color: (combos > 1000 || hasRangeErrors) ? '#ef4444' : 'var(--text-secondary)' }}>
            {combos} combination{combos !== 1 ? 's' : ''}
            {combos > 1000 ? ' (max 1000)' : ''}
          </span>
        </div>
      </div>

      {/* Results */}
      {result && (
        <div className="rounded-lg overflow-hidden" style={{ border: '1px solid var(--border)' }}>
          <div className="px-4 py-3" style={{ backgroundColor: 'var(--bg-secondary)' }}>
            <div className="flex justify-between items-center flex-wrap gap-2">
              <h3 className="font-semibold" style={{ color: 'var(--text-primary)' }}>
                Results — {result.passed_count} passed / {result.executed} executed
              </h3>
              <div className="flex gap-3 text-xs" style={{ color: 'var(--text-secondary)' }}>
                <span>Total: {result.total_specs}</span>
                {result.prefiltered > 0 && <span style={{ color: '#f59e0b' }}>Pre-filtered: {result.prefiltered}</span>}
                {result.duplicates > 0 && <span>Duplicates: {result.duplicates}</span>}
              </div>
            </div>
          </div>
          {result.ranked.length === 0 ? (
            <div className="px-4 py-8 text-center text-sm" style={{ color: 'var(--text-secondary)' }}>
              No candidates executed. {result.prefiltered > 0 ? 'All pre-filtered — try enabling "Skip Pre-filter".' : ''}
            </div>
          ) : (
            <div className="overflow-x-auto">
              <table className="w-full text-sm" style={{ color: 'var(--text-primary)' }}>
                <thead>
                  <tr style={{ backgroundColor: 'var(--bg-secondary)', color: 'var(--text-secondary)' }}>
                    <th className="px-3 py-2 text-left w-10">#</th>
                    <th className="px-3 py-2 text-left">Params</th>
                    <th className="px-3 py-2 text-right">Sharpe</th>
                    <th className="px-3 py-2 text-right">Return</th>
                    <th className="px-3 py-2 text-right">MaxDD</th>
                    <th className="px-3 py-2 text-right">Trades</th>
                    <th className="px-3 py-2 text-center">Gate</th>
                  </tr>
                </thead>
                <tbody>
                  {result.ranked.map((c: CandidateResult, i: number) => (
                    <tr key={c.spec_id} style={{ borderTop: '1px solid var(--border)' }}>
                      <td className="px-3 py-2 text-xs" style={{ color: 'var(--text-secondary)' }}>{i + 1}</td>
                      <td className="px-3 py-2 text-xs font-mono">
                        {Object.entries(c.params).map(([k, v]) => `${k}=${v}`).join('  ')}
                      </td>
                      <td className="px-3 py-2 text-right font-medium"
                        style={{ color: (c.sharpe ?? 0) >= 0.5 ? '#22c55e' : (c.sharpe ?? 0) >= 0 ? 'var(--text-primary)' : '#ef4444' }}>
                        {c.sharpe?.toFixed(2) ?? '-'}
                      </td>
                      <td className="px-3 py-2 text-right" style={{ color: (c.total_return ?? 0) >= 0 ? 'var(--color-up)' : 'var(--color-down)' }}>
                        {c.total_return != null ? (c.total_return * 100).toFixed(1) + '%' : '-'}
                      </td>
                      <td className="px-3 py-2 text-right">{c.max_drawdown != null ? (c.max_drawdown * 100).toFixed(1) + '%' : '-'}</td>
                      <td className="px-3 py-2 text-right">{c.trade_count}</td>
                      <td className="px-3 py-2 text-center">
                        <span className="px-2 py-0.5 rounded text-xs font-medium"
                          style={{ backgroundColor: c.gate_passed ? 'rgba(34,197,94,0.15)' : 'rgba(239,68,68,0.15)',
                                   color: c.gate_passed ? '#22c55e' : '#ef4444' }}>
                          {c.gate_passed ? 'PASS' : 'FAIL'}
                        </span>
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          )}
        </div>
      )}
    </div>
  )
}
