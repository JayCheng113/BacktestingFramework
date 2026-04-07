import { useState, useEffect } from 'react'
import DatePicker from 'react-datepicker'
import 'react-datepicker/dist/react-datepicker.css'
import { searchCandidates, listStrategies } from '../api'
import type { StrategyInfo, CandidateResult, SearchResult } from '../types'
import DateBtn from './shared/DateBtn'

const inputStyle = { backgroundColor: 'var(--bg-primary)', border: '1px solid var(--border)', color: 'var(--text-primary)' }

// --- Discriminated union for parameter range state ---

interface NumericParamRange {
  name: string
  type: 'int' | 'float'
  min: number
  max: number
  step: number
  defaultVal: number
}

interface BoolParamRange {
  name: string
  type: 'bool'
  selected: boolean[]     // which values are included in search
  defaultVal: boolean
}

interface EnumParamRange {
  name: string
  type: 'select' | 'str'
  allOptions: string[]    // all available options from schema
  selected: string[]      // which options are included in search
  defaultVal: string
}

type ParamRangeState = NumericParamRange | BoolParamRange | EnumParamRange

// --- Value generation ---

function isNumeric(pr: ParamRangeState): pr is NumericParamRange {
  return pr.type === 'int' || pr.type === 'float'
}

function generateValues(pr: ParamRangeState, limit: number = 50): (number | string | boolean)[] {
  if (pr.type === 'bool') return pr.selected
  if (pr.type === 'select' || pr.type === 'str') return pr.selected
  if (!isNumeric(pr)) return []
  if (pr.step <= 0 || pr.min > pr.max) return []
  const count = Math.floor((pr.max - pr.min) / pr.step) + 1
  const n = Math.min(count, limit)
  const vals: number[] = []
  for (let i = 0; i < n; i++) {
    const v = pr.min + i * pr.step
    vals.push(pr.type === 'int' ? Math.round(v) : Math.round(v * 1000) / 1000)
  }
  return [...new Set(vals)]
}

function countValues(pr: ParamRangeState): number {
  if (pr.type === 'bool') return pr.selected.length
  if (pr.type === 'select' || pr.type === 'str') return pr.selected.length
  if (!isNumeric(pr)) return 0
  if (pr.step <= 0 || pr.min > pr.max) return 0
  return Math.floor((pr.max - pr.min) / pr.step) + 1
}

function totalCombinations(ranges: ParamRangeState[]): number {
  if (ranges.length === 0) return 1
  return ranges.reduce((acc, pr) => acc * Math.max(countValues(pr), 1), 1)
}

function hasError(pr: ParamRangeState): boolean {
  if (pr.type === 'bool') return pr.selected.length === 0
  if (pr.type === 'select' || pr.type === 'str') return pr.selected.length === 0
  if (!isNumeric(pr)) return false
  return pr.min > pr.max || pr.step <= 0
}

export default function CandidateSearch() {
  const [strategies, setStrategies] = useState<StrategyInfo[]>([])
  const [searching, setSearching] = useState(false)
  const [result, setResult] = useState<SearchResult | null>(null)

  const [strategyName, setStrategyName] = useState('')
  const [symbol, setSymbol] = useState('000001.SZ')
  const [market, setMarket] = useState('cn_stock')
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
      const userStrategies = r.data.filter((s: StrategyInfo) => !s.key?.includes('research_'))
      setStrategies(userStrategies)
      if (userStrategies.length > 0) handleStrategyChange(userStrategies[0].name, userStrategies)
    }).catch(() => {})
  }, [])

  const handleStrategyChange = (name: string, strats?: StrategyInfo[]) => {
    setStrategyName(name)
    const s = (strats || strategies).find(s => s.name === name)
    if (s) {
      setParamRanges(Object.entries(s.parameters).map(([k, v]: [string, any]): ParamRangeState => {
        const type = v.type || 'float'
        if (type === 'bool' || typeof v.default === 'boolean') {
          return { name: k, type: 'bool', selected: [true, false], defaultVal: v.default ?? true }
        }
        if (type === 'select' || type === 'str') {
          const options: string[] = v.options ?? [String(v.default ?? '')]
          return { name: k, type: type as 'select' | 'str', allOptions: options, selected: [...options], defaultVal: v.default ?? options[0] ?? '' }
        }
        // numeric (int/float)
        const def = v.default ?? 0
        const min = v.min ?? (type === 'int' ? Math.max(1, def - 10) : def * 0.5)
        const max = v.max ?? (type === 'int' ? def + 20 : def * 2)
        const step = type === 'int' ? Math.max(1, Math.round((max - min) / 5)) : (max - min) / 5
        return { name: k, type: type as 'int' | 'float', min, max, step, defaultVal: def }
      }))
    }
  }

  const updateNumericRange = (i: number, field: 'min' | 'max' | 'step', val: number) => {
    const next = [...paramRanges]
    const pr = next[i]
    if (pr.type === 'int' || pr.type === 'float') {
      next[i] = { ...pr, [field]: val }
      setParamRanges(next)
    }
  }

  const toggleBoolValue = (i: number, val: boolean) => {
    const next = [...paramRanges]
    const pr = next[i]
    if (pr.type === 'bool') {
      const cur = pr.selected.includes(val) ? pr.selected.filter(v => v !== val) : [...pr.selected, val]
      next[i] = { ...pr, selected: cur.length > 0 ? cur : [val] }  // at least one
      setParamRanges(next)
    }
  }

  const toggleEnumValue = (i: number, val: string) => {
    const next = [...paramRanges]
    const pr = next[i]
    if (pr.type === 'select' || pr.type === 'str') {
      const cur = pr.selected.includes(val) ? pr.selected.filter(v => v !== val) : [...pr.selected, val]
      next[i] = { ...pr, selected: cur.length > 0 ? cur : [val] }  // at least one
      setParamRanges(next)
    }
  }

  const hasRangeErrors = paramRanges.some(hasError)
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
        market,
        period,
        start_date: toStr(startDate),
        end_date: toStr(endDate),
        mode,
        n_samples: nSamples,
        skip_prefilter: skipPrefilter,
      })
      setResult(res.data)
    } catch (e: any) {
      alert(e?.response?.data?.detail || '搜索失败')
    } finally {
      setSearching(false)
    }
  }

  return (
    <div className="space-y-4">
      <div className="rounded-lg p-4 space-y-4" style={{ backgroundColor: 'var(--bg-secondary)', border: '1px solid var(--border)' }}>
        <h3 className="text-lg font-semibold" style={{ color: 'var(--text-primary)' }}>参数搜索</h3>

        {/* Row 1: Strategy, Symbol, Period, Dates */}
        <div className="grid grid-cols-2 md:grid-cols-5 gap-3">
          <div>
            <label className="text-xs" style={{ color: 'var(--text-secondary)' }}>策略</label>
            <select value={strategyName} onChange={e => handleStrategyChange(e.target.value)}
              className="w-full px-2 py-1.5 rounded text-sm" style={inputStyle}>
              {strategies.map(s => <option key={s.name} value={s.name}>{s.name}</option>)}
            </select>
          </div>
          <div>
            <label className="text-xs" style={{ color: 'var(--text-secondary)' }}>股票代码</label>
            <input value={symbol} onChange={e => setSymbol(e.target.value)}
              className="w-full px-2 py-1.5 rounded text-sm" style={inputStyle} />
          </div>
          <div>
            <label className="text-xs" style={{ color: 'var(--text-secondary)' }}>市场</label>
            <select value={market} onChange={e => setMarket(e.target.value)}
              className="w-full px-2 py-1.5 rounded text-sm" style={inputStyle}>
              <option value="cn_stock">A股</option>
              <option value="us_stock">美股</option>
              <option value="hk_stock">港股</option>
            </select>
          </div>
          <div>
            <label className="text-xs" style={{ color: 'var(--text-secondary)' }}>周期</label>
            <select value={period} onChange={e => setPeriod(e.target.value)}
              className="w-full px-2 py-1.5 rounded text-sm" style={inputStyle}>
              <option value="daily">日线</option>
              <option value="weekly">周线</option>
              <option value="monthly">月线</option>
            </select>
          </div>
          <div>
            <label className="text-xs" style={{ color: 'var(--text-secondary)' }}>开始</label>
            <DatePicker selected={startDate} dateFormat="yyyy-MM-dd"
              onChange={(d: Date | null) => { if (d) { setStartDate(d); if (d > endDate) setEndDate(d) } }}
              maxDate={endDate} showMonthDropdown showYearDropdown dropdownMode="select"
              customInput={<DateBtn />} />
          </div>
          <div>
            <label className="text-xs" style={{ color: 'var(--text-secondary)' }}>结束</label>
            <DatePicker selected={endDate} dateFormat="yyyy-MM-dd"
              onChange={(d: Date | null) => { if (d) { setEndDate(d); if (d < startDate) setStartDate(d) } }}
              minDate={startDate} maxDate={new Date()} showMonthDropdown showYearDropdown dropdownMode="select"
              customInput={<DateBtn />} />
          </div>
        </div>

        {/* Row 2: Mode + Options */}
        <div className="flex gap-4 items-center flex-wrap">
          <label className="flex items-center gap-1.5 text-sm" style={{ color: 'var(--text-secondary)' }}>
            <input type="radio" checked={mode === 'grid'} onChange={() => setMode('grid')} /> 网格搜索
          </label>
          <label className="flex items-center gap-1.5 text-sm" style={{ color: 'var(--text-secondary)' }}>
            <input type="radio" checked={mode === 'random'} onChange={() => setMode('random')} /> 随机搜索
          </label>
          {mode === 'random' && (
            <div className="flex items-center gap-1.5">
              <label className="text-xs" style={{ color: 'var(--text-secondary)' }}>采样数</label>
              <input type="number" value={nSamples} min={1} max={1000}
                onChange={e => setNSamples(Number(e.target.value))}
                className="w-20 px-2 py-1 rounded text-sm" style={inputStyle} />
            </div>
          )}
          <label className="flex items-center gap-1.5 text-sm" style={{ color: 'var(--text-secondary)' }}>
            <input type="checkbox" checked={skipPrefilter} onChange={e => setSkipPrefilter(e.target.checked)} />
            跳过预筛选
          </label>
        </div>

        {/* Row 3: Parameter Ranges */}
        {paramRanges.length > 0 && (
          <div className="space-y-2">
            <label className="text-xs" style={{ color: 'var(--text-secondary)' }}>参数范围</label>
            <div className="rounded p-3 space-y-2" style={{ backgroundColor: 'var(--bg-primary)', border: '1px solid var(--border)' }}>
              {paramRanges.map((pr, i) => {
                // --- Bool parameter ---
                if (pr.type === 'bool') {
                  const err = pr.selected.length === 0
                  return (
                    <div key={pr.name} className="flex items-center gap-3">
                      <span className="text-sm font-medium w-[120px] truncate" style={{ color: 'var(--text-primary)' }} title={pr.name}>{pr.name}</span>
                      <label className="flex items-center gap-1 text-xs" style={{ color: 'var(--text-secondary)' }}>
                        <input type="checkbox" checked={pr.selected.includes(true)} onChange={() => toggleBoolValue(i, true)} /> True
                      </label>
                      <label className="flex items-center gap-1 text-xs" style={{ color: 'var(--text-secondary)' }}>
                        <input type="checkbox" checked={pr.selected.includes(false)} onChange={() => toggleBoolValue(i, false)} /> False
                      </label>
                      <span className="text-xs" style={{ color: err ? '#ef4444' : 'var(--text-secondary)' }}>
                        [{pr.selected.length}] {pr.selected.map(String).join(', ')}
                        {err && ' (至少选一个)'}
                      </span>
                    </div>
                  )
                }

                // --- Enum/select parameter ---
                if (pr.type === 'select' || pr.type === 'str') {
                  const err = pr.selected.length === 0
                  return (
                    <div key={pr.name}>
                      <div className="flex items-center gap-2 mb-1">
                        <span className="text-sm font-medium w-[120px] truncate" style={{ color: 'var(--text-primary)' }} title={pr.name}>{pr.name}</span>
                        <span className="text-xs" style={{ color: err ? '#ef4444' : 'var(--text-secondary)' }}>
                          [{pr.selected.length}/{pr.allOptions.length}]
                          {err && ' (至少选一个)'}
                        </span>
                      </div>
                      <div className="flex flex-wrap gap-1 ml-[120px]">
                        {pr.allOptions.map(opt => (
                          <button key={opt} onClick={() => toggleEnumValue(i, opt)}
                            className="text-xs px-2 py-0.5 rounded"
                            style={{
                              backgroundColor: pr.selected.includes(opt) ? 'var(--color-accent)' : 'var(--bg-secondary)',
                              color: pr.selected.includes(opt) ? '#fff' : 'var(--text-secondary)',
                              border: '1px solid var(--border)',
                            }}>
                            {opt}
                          </button>
                        ))}
                      </div>
                    </div>
                  )
                }

                // --- Numeric parameter (original) ---
                if (!isNumeric(pr)) return null
                const count = countValues(pr)
                const preview = generateValues(pr, 6) as number[]
                const numErr = pr.min > pr.max || pr.step <= 0
                const errStyle = numErr ? { ...inputStyle, border: '1px solid #ef4444' } : inputStyle
                return (
                  <div key={pr.name} className="grid grid-cols-[120px_1fr_1fr_1fr_1fr] gap-2 items-center">
                    <span className="text-sm font-medium truncate" style={{ color: 'var(--text-primary)' }}
                      title={pr.name}>{pr.name}</span>
                    <input type="number" value={pr.min} step={pr.type === 'int' ? 1 : 0.1}
                      onChange={e => updateNumericRange(i, 'min', Number(e.target.value))}
                      className="px-2 py-1 rounded text-sm w-full" style={pr.min > pr.max ? errStyle : inputStyle} />
                    <input type="number" value={pr.max} step={pr.type === 'int' ? 1 : 0.1}
                      onChange={e => updateNumericRange(i, 'max', Number(e.target.value))}
                      className="px-2 py-1 rounded text-sm w-full" style={pr.min > pr.max ? errStyle : inputStyle} />
                    <input type="number" value={pr.step} step={pr.type === 'int' ? 1 : 0.1}
                      min={pr.type === 'int' ? 1 : 0.01}
                      onChange={e => updateNumericRange(i, 'step', Math.max(pr.type === 'int' ? 1 : 0.01, Number(e.target.value)))}
                      className="px-2 py-1 rounded text-sm w-full" style={pr.step <= 0 ? errStyle : inputStyle} />
                    <span className="text-xs truncate"
                      style={{ color: numErr ? '#ef4444' : 'var(--text-secondary)' }}
                      title={numErr ? (pr.min > pr.max ? '最小值 > 最大值' : '步长必须 > 0') : preview.join(', ')}>
                      {numErr
                        ? (pr.min > pr.max ? '最小值 > 最大值' : '步长 > 0')
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
            {searching ? '搜索中...' : '搜索'}
          </button>
          <span className="text-xs" style={{ color: (combos > 1000 || hasRangeErrors) ? '#ef4444' : 'var(--text-secondary)' }}>
            {combos} 个组合
            {combos > 1000 ? '（最多 1000）' : ''}
          </span>
        </div>
      </div>

      {/* Results */}
      {result && (
        <div className="rounded-lg overflow-hidden" style={{ border: '1px solid var(--border)' }}>
          <div className="px-4 py-3" style={{ backgroundColor: 'var(--bg-secondary)' }}>
            <div className="flex justify-between items-center flex-wrap gap-2">
              <h3 className="font-semibold" style={{ color: 'var(--text-primary)' }}>
                搜索结果 — {result.passed_count} 通过 / {result.executed} 已执行
              </h3>
              <div className="flex gap-3 text-xs" style={{ color: 'var(--text-secondary)' }}>
                <span>总计: {result.total_specs}</span>
                {result.prefiltered > 0 && <span style={{ color: '#f59e0b' }}>预筛除: {result.prefiltered}</span>}
                {result.duplicates > 0 && <span>重复: {result.duplicates}</span>}
              </div>
            </div>
          </div>
          {result.ranked.length === 0 ? (
            <div className="px-4 py-8 text-center text-sm" style={{ color: 'var(--text-secondary)' }}>
              无候选执行。{result.prefiltered > 0 ? '全部被预筛除 — 勾选"跳过预筛选"重试。' : ''}
            </div>
          ) : (
            <div className="overflow-x-auto">
              <table className="w-full text-sm" style={{ color: 'var(--text-primary)' }}>
                <thead>
                  <tr style={{ backgroundColor: 'var(--bg-secondary)', color: 'var(--text-secondary)' }}>
                    <th className="px-3 py-2 text-left w-10">#</th>
                    <th className="px-3 py-2 text-left">参数</th>
                    <th className="px-3 py-2 text-right">夏普</th>
                    <th className="px-3 py-2 text-right">收益</th>
                    <th className="px-3 py-2 text-right">回撤</th>
                    <th className="px-3 py-2 text-right">交易数</th>
                    <th className="px-3 py-2 text-right">显著性</th>
                    <th className="px-3 py-2 text-center">门控</th>
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
                      <td className="px-3 py-2 text-right" style={{ color: c.fdr_significant ? '#22c55e' : 'var(--text-secondary)' }}>
                        {c.fdr_adjusted_p != null && !isNaN(c.fdr_adjusted_p) ? c.fdr_adjusted_p.toFixed(3) : '-'}
                      </td>
                      <td className="px-3 py-2 text-center">
                        <span className="px-2 py-0.5 rounded text-xs font-medium"
                          style={{ backgroundColor: c.gate_passed ? 'rgba(34,197,94,0.15)' : 'rgba(239,68,68,0.15)',
                                   color: c.gate_passed ? '#22c55e' : '#ef4444' }}>
                          {c.gate_passed ? '通过' : '未通过'}
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
