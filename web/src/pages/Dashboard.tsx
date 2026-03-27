import { useState } from 'react'
import SearchBar from '../components/SearchBar'
import KlineChart from '../components/KlineChart'
import BacktestPanel from '../components/BacktestPanel'
import FactorPanel from '../components/FactorPanel'
import { fetchKline } from '../api'
import type { KlineBar, TradeRecord } from '../types'

export default function Dashboard() {
  const [klineData, setKlineData] = useState<KlineBar[]>([])
  const [currentSymbol, setCurrentSymbol] = useState('')
  const [currentMarket, setCurrentMarket] = useState('cn_stock')
  const [startDate, setStartDate] = useState('2024-01-01')
  const [endDate, setEndDate] = useState('2024-12-31')
  const [loading, setLoading] = useState(false)
  const [trades, setTrades] = useState<TradeRecord[]>([])

  const handleSearch = async (symbol: string, market: string, start: string, end: string) => {
    setLoading(true)
    setCurrentSymbol(symbol)
    setCurrentMarket(market)
    setStartDate(start)
    setEndDate(end)
    setTrades([])  // clear old trades on new search
    try {
      const res = await fetchKline({ symbol, market, period: 'daily', start_date: start, end_date: end })
      setKlineData(res.data)
    } catch (e: any) { alert(e?.response?.data?.detail || 'Failed to fetch data') }
    finally { setLoading(false) }
  }

  return (
    <div>
      <SearchBar onSearch={handleSearch} />
      {loading ? (
        <div className="p-8 text-center" style={{ color: 'var(--text-secondary)' }}>Loading...</div>
      ) : (
        <div className="p-4">
          <KlineChart data={klineData} symbol={currentSymbol} trades={trades} />
          {currentSymbol && (
            <>
              <BacktestPanel
                symbol={currentSymbol} market={currentMarket}
                startDate={startDate} endDate={endDate}
                onTradesUpdate={setTrades}
              />
              <FactorPanel symbol={currentSymbol} market={currentMarket} startDate={startDate} endDate={endDate} />
            </>
          )}
        </div>
      )}
    </div>
  )
}
