import { useState, useEffect, useRef, useCallback, forwardRef } from 'react'
import DatePicker from 'react-datepicker'
import 'react-datepicker/dist/react-datepicker.css'
import { searchSymbols } from '../api'
import type { SymbolInfo } from '../types'

/* Custom date trigger — looks like a button with calendar icon, not an editable input */
const DateButton = forwardRef<HTMLButtonElement, { value?: string; onClick?: () => void; label: string }>(
  ({ value, onClick, label }, ref) => (
    <button
      ref={ref}
      type="button"
      onClick={onClick}
      className="flex items-center gap-2 px-3 py-1.5 rounded text-sm cursor-pointer min-w-[150px]"
      style={{ backgroundColor: 'var(--bg-primary)', border: '1px solid var(--border)', color: 'var(--text-primary)' }}
    >
      <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" style={{ color: 'var(--text-secondary)', flexShrink: 0 }}>
        <rect x="3" y="4" width="18" height="18" rx="2" ry="2"/><line x1="16" y1="2" x2="16" y2="6"/><line x1="8" y1="2" x2="8" y2="6"/><line x1="3" y1="10" x2="21" y2="10"/>
      </svg>
      <span>{value || label}</span>
      <svg width="10" height="10" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" style={{ color: 'var(--text-secondary)', marginLeft: 'auto' }}>
        <polyline points="6 9 12 15 18 9"/>
      </svg>
    </button>
  )
)

interface Props {
  onSearch: (symbol: string, market: string, startDate: string, endDate: string) => void
}

const todayDate = new Date()
const oneYearAgoDate = new Date(Date.now() - 365 * 86400000)

function toStr(d: Date): string {
  return d.toISOString().slice(0, 10)
}

// Fallback popular stocks when API search is unavailable
const POPULAR_STOCKS: SymbolInfo[] = [
  { symbol: '000001.SZ', name: '平安银行', industry: '银行' },
  { symbol: '600519.SH', name: '贵州茅台', industry: '白酒' },
  { symbol: '000858.SZ', name: '五粮液', industry: '白酒' },
  { symbol: '601318.SH', name: '中国平安', industry: '保险' },
  { symbol: '000333.SZ', name: '美的集团', industry: '家电' },
  { symbol: '600036.SH', name: '招商银行', industry: '银行' },
  { symbol: '300750.SZ', name: '宁德时代', industry: '电池' },
  { symbol: '601899.SH', name: '紫金矿业', industry: '黄金' },
  { symbol: '600900.SH', name: '长江电力', industry: '电力' },
  { symbol: '000002.SZ', name: '万科A', industry: '房地产' },
]

const inputStyle = {
  backgroundColor: 'var(--bg-primary)',
  border: '1px solid var(--border)',
  color: 'var(--text-primary)',
}

export default function SearchBar({ onSearch }: Props) {
  const [query, setQuery] = useState('')
  const [selectedSymbol, setSelectedSymbol] = useState('000001.SZ')
  const [selectedName, setSelectedName] = useState('平安银行')
  const [market, setMarket] = useState('cn_stock')
  const [startDate, setStartDate] = useState<Date>(oneYearAgoDate)
  const [endDate, setEndDate] = useState<Date>(todayDate)
  const [suggestions, setSuggestions] = useState<SymbolInfo[]>([])
  const [showDropdown, setShowDropdown] = useState(false)
  const [loading, setLoading] = useState(false)
  const dropdownRef = useRef<HTMLDivElement>(null)
  const debounceRef = useRef<ReturnType<typeof setTimeout> | null>(null)

  // Close dropdown on outside click
  useEffect(() => {
    const handler = (e: MouseEvent) => {
      if (dropdownRef.current && !dropdownRef.current.contains(e.target as Node)) {
        setShowDropdown(false)
      }
    }
    document.addEventListener('mousedown', handler)
    return () => document.removeEventListener('mousedown', handler)
  }, [])

  const showInitialSuggestions = useCallback(() => {
    setSuggestions(POPULAR_STOCKS)
    setShowDropdown(true)
  }, [])

  // Debounced search
  const handleQueryChange = useCallback((value: string) => {
    setQuery(value)
    if (debounceRef.current) clearTimeout(debounceRef.current)
    if (value.trim().length < 1) {
      setSuggestions(POPULAR_STOCKS)
      return
    }
    debounceRef.current = setTimeout(async () => {
      setLoading(true)
      try {
        const res = await searchSymbols(value.trim(), market)
        const results = res.data.slice(0, 20)
        if (results.length > 0) {
          setSuggestions(results)
          setShowDropdown(true)
        } else {
          const filtered = POPULAR_STOCKS.filter(s =>
            s.symbol.includes(value.toUpperCase()) || s.name.includes(value)
          )
          setSuggestions(filtered.length > 0 ? filtered : POPULAR_STOCKS.slice(0, 5))
          setShowDropdown(true)
        }
      } catch {
        const filtered = POPULAR_STOCKS.filter(s =>
          s.symbol.includes(value.toUpperCase()) || s.name.includes(value)
        )
        setSuggestions(filtered.length > 0 ? filtered : POPULAR_STOCKS.slice(0, 5))
        setShowDropdown(true)
      } finally {
        setLoading(false)
      }
    }, 300)
  }, [market])

  const handleSelect = (item: SymbolInfo) => {
    setSelectedSymbol(item.symbol)
    setSelectedName(item.name)
    setQuery('')
    setShowDropdown(false)
  }

  const handleSearch = () => {
    if (selectedSymbol) onSearch(selectedSymbol, market, toStr(startDate), toStr(endDate))
  }

  return (
    <div className="flex flex-wrap gap-3 items-end p-4" style={{ backgroundColor: 'var(--bg-secondary)', borderBottom: '1px solid var(--border)' }}>
      {/* Symbol search with autocomplete */}
      <div className="flex flex-col gap-1 relative" ref={dropdownRef}>
        <label className="text-xs" style={{ color: 'var(--text-secondary)' }}>Symbol</label>
        <div
          className="px-3 py-1.5 rounded text-sm cursor-pointer flex items-center gap-2 min-w-[200px]"
          style={{ ...inputStyle, minHeight: '32px' }}
          onClick={() => { setQuery(''); showInitialSuggestions() }}
        >
          <span className="font-medium">{selectedSymbol}</span>
          <span className="text-xs" style={{ color: 'var(--text-secondary)' }}>{selectedName}</span>
        </div>

        {showDropdown && (
          <div className="absolute top-full left-0 right-0 z-50 mt-1 rounded shadow-lg max-h-64 overflow-y-auto"
            style={{ backgroundColor: 'var(--bg-primary)', border: '1px solid var(--border)', minWidth: '320px' }}>
            <input
              autoFocus
              value={query}
              onChange={e => handleQueryChange(e.target.value)}
              placeholder="输入代码或名称搜索..."
              className="w-full px-3 py-2 text-sm outline-none"
              style={{ ...inputStyle, borderBottom: '1px solid var(--border)', borderTop: 'none', borderLeft: 'none', borderRight: 'none' }}
              onKeyDown={e => {
                if (e.key === 'Escape') setShowDropdown(false)
                if (e.key === 'Enter' && suggestions.length > 0) handleSelect(suggestions[0])
              }}
            />
            {loading && <div className="px-3 py-2 text-xs" style={{ color: 'var(--text-secondary)' }}>搜索中...</div>}
            {!loading && suggestions.length === 0 && query.length > 0 && (
              <div className="px-3 py-2 text-xs" style={{ color: 'var(--text-secondary)' }}>无结果</div>
            )}
            {suggestions.map(item => (
              <div
                key={item.symbol}
                className="px-3 py-2 text-sm cursor-pointer flex justify-between items-center"
                style={{ borderBottom: '1px solid var(--border)' }}
                onClick={() => handleSelect(item)}
                onMouseEnter={e => (e.currentTarget.style.backgroundColor = 'var(--bg-secondary)')}
                onMouseLeave={e => (e.currentTarget.style.backgroundColor = 'transparent')}
              >
                <div>
                  <span className="font-medium">{item.symbol}</span>
                  <span className="ml-2">{item.name}</span>
                </div>
                {item.industry && (
                  <span className="text-xs" style={{ color: 'var(--text-secondary)' }}>{item.industry}</span>
                )}
              </div>
            ))}
          </div>
        )}
      </div>

      {/* Market selector */}
      <div className="flex flex-col gap-1">
        <label className="text-xs" style={{ color: 'var(--text-secondary)' }}>Market</label>
        <select value={market} onChange={e => setMarket(e.target.value)}
          className="px-3 py-1.5 rounded text-sm" style={inputStyle}>
          <option value="cn_stock">A-Shares</option>
          <option value="us_stock">US Stock</option>
          <option value="hk_stock">HK Stock</option>
        </select>
      </div>

      {/* Date range — calendar dropdown, click-only (no manual input) */}
      <div className="flex flex-col gap-1">
        <label className="text-xs" style={{ color: 'var(--text-secondary)' }}>Start Date</label>
        <DatePicker
          selected={startDate}
          onChange={(d: Date | null) => {
            if (!d) return
            setStartDate(d)
            if (d > endDate) setEndDate(d)
          }}
          selectsStart
          startDate={startDate}
          endDate={endDate}
          maxDate={endDate}
          dateFormat="yyyy-MM-dd"
          showMonthDropdown
          showYearDropdown
          dropdownMode="select"
          customInput={<DateButton label="Select start" />}
        />
      </div>
      <div className="flex flex-col gap-1">
        <label className="text-xs" style={{ color: 'var(--text-secondary)' }}>End Date</label>
        <DatePicker
          selected={endDate}
          onChange={(d: Date | null) => {
            if (!d) return
            setEndDate(d)
            if (d < startDate) setStartDate(d)
          }}
          selectsEnd
          startDate={startDate}
          endDate={endDate}
          minDate={startDate}
          maxDate={todayDate}
          dateFormat="yyyy-MM-dd"
          showMonthDropdown
          showYearDropdown
          dropdownMode="select"
          customInput={<DateButton label="Select end" />}
        />
      </div>

      <button onClick={handleSearch}
        className="px-4 py-1.5 rounded text-sm font-medium text-white" style={{ backgroundColor: 'var(--color-accent)' }}>
        Search
      </button>
    </div>
  )
}
