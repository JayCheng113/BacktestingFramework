import { useState, useEffect } from 'react'
import { searchCandidates, listStrategies } from '../api'
import type { StrategyInfo, CandidateResult, SearchResult } from '../types'

const inputStyle = { backgroundColor: 'var(--bg-primary)', border: '1px solid var(--border)', color: 'var(--text-primary)' }

export default function CandidateSearch() {
  const [strategies, setStrategies] = useState<StrategyInfo[]>([])
  const [searching, setSearching] = useState(false)
  const [result, setResult] = useState<SearchResult | null>(null)

  // Form
  const [strategyName, setStrategyName] = useState('')
  const [symbol, setSymbol] = useState('000001.SZ')
  const [period, setPeriod] = useState('daily')
  const [startDate, setStartDate] = useState('2020-01-01')
  const [endDate, setEndDate] = useState('2024-12-31')
  const [mode, setMode] = useState<'grid' | 'random'>('grid')
  const [nSamples, setNSamples] = useState(20)
  const [paramRanges, setParamRanges] = useState<{ name: string; values: string }[]>([])

  useEffect(() => {
    listStrategies().then(r => {
      setStrategies(r.data)
      if (r.data.length > 0) {
        handleStrategyChange(r.data[0].name, r.data)
      }
    }).catch(() => {})
  }, [])

  const handleStrategyChange = (name: string, strats?: StrategyInfo[]) => {
    setStrategyName(name)
    const list = strats || strategies
    const s = list.find(s => s.name === name)
    if (s) {
      setParamRanges(Object.entries(s.parameters).map(([k, v]) => ({
        name: k,
        values: String((v as any).default),
      })))
    }
  }

  const handleSearch = async () => {
    setSearching(true)
    setResult(null)
    try {
      const ranges = paramRanges.map(pr => ({
        name: pr.name,
        values: pr.values.split(',').map(v => Number(v.trim())).filter(v => !isNaN(v)),
      })).filter(pr => pr.values.length > 0)

      const res = await searchCandidates({
        strategy_name: strategyName,
        param_ranges: ranges,
        symbol,
        period,
        start_date: startDate,
        end_date: endDate,
        mode,
        n_samples: nSamples,
        skip_prefilter: false,
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
      {/* Search Form */}
      <div className="rounded-lg p-4 space-y-3" style={{ backgroundColor: 'var(--bg-secondary)', border: '1px solid var(--border)' }}>
        <h3 className="text-lg font-semibold" style={{ color: 'var(--text-primary)' }}>Parameter Search</h3>
        <div className="grid grid-cols-2 md:grid-cols-4 gap-3">
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
            <input type="date" value={startDate} onChange={e => setStartDate(e.target.value)}
              className="w-full px-2 py-1.5 rounded text-sm" style={inputStyle} />
          </div>
          <div>
            <label className="text-xs" style={{ color: 'var(--text-secondary)' }}>End</label>
            <input type="date" value={endDate} onChange={e => setEndDate(e.target.value)}
              className="w-full px-2 py-1.5 rounded text-sm" style={inputStyle} />
          </div>
        </div>

        {/* Mode */}
        <div className="flex gap-4 items-center">
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
        </div>

        {/* Param Ranges */}
        <div className="space-y-2">
          <label className="text-xs" style={{ color: 'var(--text-secondary)' }}>Parameter Ranges (comma-separated values)</label>
          {paramRanges.map((pr, i) => (
            <div key={pr.name} className="flex items-center gap-2">
              <span className="text-sm w-32" style={{ color: 'var(--text-primary)' }}>{pr.name}</span>
              <input value={pr.values}
                onChange={e => {
                  const next = [...paramRanges]
                  next[i] = { ...pr, values: e.target.value }
                  setParamRanges(next)
                }}
                placeholder="e.g. 3,5,10,20"
                className="flex-1 px-2 py-1.5 rounded text-sm" style={inputStyle} />
            </div>
          ))}
        </div>

        <button onClick={handleSearch} disabled={searching}
          className="px-4 py-2 rounded text-sm font-medium"
          style={{ backgroundColor: 'var(--color-accent)', color: '#fff', opacity: searching ? 0.5 : 1 }}>
          {searching ? 'Searching...' : 'Search'}
        </button>
      </div>

      {/* Results */}
      {result && (
        <div className="rounded-lg overflow-hidden" style={{ border: '1px solid var(--border)' }}>
          <div className="px-4 py-3" style={{ backgroundColor: 'var(--bg-secondary)' }}>
            <div className="flex justify-between items-center">
              <h3 className="font-semibold" style={{ color: 'var(--text-primary)' }}>Search Results</h3>
              <div className="flex gap-3 text-xs" style={{ color: 'var(--text-secondary)' }}>
                <span>Total: {result.total_specs}</span>
                <span>Pre-filtered: {result.prefiltered}</span>
                <span>Executed: {result.executed}</span>
                <span>Duplicates: {result.duplicates}</span>
                <span style={{ color: '#22c55e' }}>Passed: {result.passed_count}</span>
              </div>
            </div>
          </div>
          <div className="overflow-x-auto">
            <table className="w-full text-sm" style={{ color: 'var(--text-primary)' }}>
              <thead>
                <tr style={{ backgroundColor: 'var(--bg-secondary)', color: 'var(--text-secondary)' }}>
                  <th className="px-3 py-2 text-left">#</th>
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
                    <td className="px-3 py-2 text-xs">
                      {Object.entries(c.params).map(([k, v]) => `${k}=${v}`).join(', ')}
                    </td>
                    <td className="px-3 py-2 text-right">{c.sharpe?.toFixed(2) ?? '-'}</td>
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
        </div>
      )}
    </div>
  )
}
