import { useState, useEffect } from 'react'
import SearchBar from '../components/SearchBar'
import KlineChart from '../components/KlineChart'
import BacktestPanel from '../components/BacktestPanel'
import FactorPanel from '../components/FactorPanel'
import { fetchKline } from '../api'
import type { KlineBar, TradeRecord } from '../types'
import { useToast } from '../components/shared/Toast'

export default function Dashboard() {
  const { showToast } = useToast()
  const [klineData, setKlineData] = useState<KlineBar[]>([])
  const [currentSymbol, setCurrentSymbol] = useState('')
  const [currentMarket, setCurrentMarket] = useState('cn_stock')
  const [currentPeriod, setCurrentPeriod] = useState('daily')
  const [startDate, setStartDate] = useState(() => new Date(Date.now() - 365 * 86400000).toISOString().slice(0, 10))
  const [endDate, setEndDate] = useState(() => new Date().toISOString().slice(0, 10))
  const [loading, setLoading] = useState(false)
  const [trades, setTrades] = useState<TradeRecord[]>([])

  useEffect(() => {
    handleSearch('510300.SH', 'cn_stock', startDate, endDate, 'daily')
  }, [])  // run once on mount

  const handleSearch = async (symbol: string, market: string, start: string, end: string, period: string = 'daily') => {
    setLoading(true)
    setCurrentSymbol(symbol)
    setCurrentMarket(market)
    setCurrentPeriod(period)
    setStartDate(start)
    setEndDate(end)
    setTrades([])  // clear old trades on new search
    try {
      const res = await fetchKline({ symbol, market, period, start_date: start, end_date: end })
      setKlineData(res.data)
    } catch (e: unknown) {
      const err = e as { response?: { data?: { detail?: string } }; message?: string }
      showToast('error', err?.response?.data?.detail || err?.message || '获取行情数据失败')
    }
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
                period={currentPeriod}
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
