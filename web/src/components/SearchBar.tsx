import { useState } from 'react'

interface Props {
  onSearch: (symbol: string, market: string, startDate: string, endDate: string) => void
}

export default function SearchBar({ onSearch }: Props) {
  const [symbol, setSymbol] = useState('000001.SZ')
  const [market, setMarket] = useState('cn_stock')
  const [startDate, setStartDate] = useState('2024-01-01')
  const [endDate, setEndDate] = useState('2024-12-31')

  const handleSearch = () => {
    if (symbol.trim()) onSearch(symbol.trim(), market, startDate, endDate)
  }

  return (
    <div className="flex flex-wrap gap-3 items-end p-4" style={{ backgroundColor: 'var(--bg-secondary)', borderBottom: '1px solid var(--border)' }}>
      <div className="flex flex-col gap-1">
        <label className="text-xs" style={{ color: 'var(--text-secondary)' }}>Symbol</label>
        <input value={symbol} onChange={e => setSymbol(e.target.value)}
          className="px-3 py-1.5 rounded text-sm w-32" style={{ backgroundColor: 'var(--bg-primary)', border: '1px solid var(--border)', color: 'var(--text-primary)' }}
          onKeyDown={e => e.key === 'Enter' && handleSearch()} />
      </div>
      <div className="flex flex-col gap-1">
        <label className="text-xs" style={{ color: 'var(--text-secondary)' }}>Market</label>
        <select value={market} onChange={e => setMarket(e.target.value)}
          className="px-3 py-1.5 rounded text-sm" style={{ backgroundColor: 'var(--bg-primary)', border: '1px solid var(--border)', color: 'var(--text-primary)' }}>
          <option value="cn_stock">A-Shares</option>
          <option value="us_stock">US Stock</option>
          <option value="hk_stock">HK Stock</option>
        </select>
      </div>
      <div className="flex flex-col gap-1">
        <label className="text-xs" style={{ color: 'var(--text-secondary)' }}>Start</label>
        <input type="date" value={startDate} onChange={e => setStartDate(e.target.value)}
          className="px-3 py-1.5 rounded text-sm" style={{ backgroundColor: 'var(--bg-primary)', border: '1px solid var(--border)', color: 'var(--text-primary)' }} />
      </div>
      <div className="flex flex-col gap-1">
        <label className="text-xs" style={{ color: 'var(--text-secondary)' }}>End</label>
        <input type="date" value={endDate} onChange={e => setEndDate(e.target.value)}
          className="px-3 py-1.5 rounded text-sm" style={{ backgroundColor: 'var(--bg-primary)', border: '1px solid var(--border)', color: 'var(--text-primary)' }} />
      </div>
      <button onClick={handleSearch}
        className="px-4 py-1.5 rounded text-sm font-medium text-white" style={{ backgroundColor: 'var(--color-accent)' }}>
        Search
      </button>
    </div>
  )
}
