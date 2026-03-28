import ReactECharts from 'echarts-for-react'
import type { KlineBar, TradeRecord } from '../types'

interface Props {
  data: KlineBar[]
  symbol: string
  trades?: TradeRecord[]
}

function computeMA(data: KlineBar[], period: number): (number | null)[] {
  return data.map((_, i) => {
    if (i < period - 1) return null
    const sum = data.slice(i - period + 1, i + 1).reduce((s, d) => s + d.adj_close, 0)
    return +(sum / period).toFixed(2)
  })
}

export default function KlineChart({ data, symbol, trades = [] }: Props) {
  if (!data.length) return <div className="p-8 text-center" style={{ color: 'var(--text-secondary)' }}>Search a symbol to view K-line data</div>

  const dates = data.map(d => d.date)
  const ohlc = data.map(d => [d.open, d.close, d.low, d.high])
  const volumes = data.map(d => d.volume)
  const colors = data.map(d => d.close >= d.open ? '#ef4444' : '#22c55e')

  // Build buy/sell markers from trades
  const buyMarkers: any[] = []
  const sellMarkers: any[] = []
  if (trades.length > 0) {
    const dateSet = new Set(dates)
    for (const t of trades) {
      const entryDate = t.entry_time.slice(0, 10)
      const exitDate = t.exit_time.slice(0, 10)
      if (dateSet.has(entryDate)) {
        buyMarkers.push({
          coord: [entryDate, t.entry_price],
          value: `Buy\n${t.entry_price.toFixed(2)}`,
          itemStyle: { color: '#ef4444' },
        })
      }
      if (dateSet.has(exitDate)) {
        sellMarkers.push({
          coord: [exitDate, t.exit_price],
          value: `Sell\n${t.exit_price.toFixed(2)}\nPnL: ${t.pnl >= 0 ? '+' : ''}${t.pnl.toFixed(0)}`,
          itemStyle: { color: t.pnl >= 0 ? '#22c55e' : '#ef4444' },
        })
      }
    }
  }

  const series: any[] = [
    {
      type: 'candlestick', data: ohlc, xAxisIndex: 0, yAxisIndex: 0,
      itemStyle: { color: '#ef4444', color0: '#22c55e', borderColor: '#ef4444', borderColor0: '#22c55e' },
      markPoint: (buyMarkers.length > 0 || sellMarkers.length > 0) ? {
        symbol: 'triangle',
        symbolSize: 10,
        data: [
          ...buyMarkers.map(m => ({
            ...m,
            symbol: 'triangle',
            symbolSize: 12,
            symbolRotate: 0,
            label: { show: false },
          })),
          ...sellMarkers.map(m => ({
            ...m,
            symbol: 'triangle',
            symbolSize: 12,
            symbolRotate: 180,
            label: { show: false },
          })),
        ],
      } : undefined,
    },
    {
      type: 'bar', data: volumes.map((v, i) => ({ value: v, itemStyle: { color: colors[i] + '80' } })),
      xAxisIndex: 1, yAxisIndex: 1,
    },
    { name: 'MA5', type: 'line', data: computeMA(data, 5), xAxisIndex: 0, yAxisIndex: 0, lineStyle: { color: '#f59e0b', width: 1 }, showSymbol: false, z: 5 },
    { name: 'MA20', type: 'line', data: computeMA(data, 20), xAxisIndex: 0, yAxisIndex: 0, lineStyle: { color: '#a855f7', width: 1 }, showSymbol: false, z: 5 },
  ]

  // Add buy/sell scatter for clear visibility + tooltip
  if (buyMarkers.length > 0) {
    series.push({
      type: 'scatter', xAxisIndex: 0, yAxisIndex: 0,
      symbolSize: 14, symbol: 'arrow', symbolRotate: 0,
      itemStyle: { color: '#ef4444', borderColor: '#fff', borderWidth: 1 },
      data: buyMarkers.map(m => ({
        value: [m.coord[0], m.coord[1] * 0.98],  // slightly below price
        label: { show: true, position: 'bottom', formatter: 'B', color: '#ef4444', fontSize: 10, fontWeight: 'bold' },
      })),
      tooltip: {
        formatter: (p: any) => {
          const idx = p.dataIndex
          const t = trades.filter(t => dates.includes(t.entry_time.slice(0, 10)))[idx]
          return t ? `<b>Buy</b><br/>Price: ${t.entry_price.toFixed(2)}` : ''
        }
      },
      z: 10,
    })
  }
  if (sellMarkers.length > 0) {
    series.push({
      type: 'scatter', xAxisIndex: 0, yAxisIndex: 0,
      symbolSize: 14, symbol: 'arrow', symbolRotate: 180,
      itemStyle: { color: '#22c55e', borderColor: '#fff', borderWidth: 1 },
      data: sellMarkers.map(m => ({
        value: [m.coord[0], m.coord[1] * 1.02],  // slightly above price
        label: { show: true, position: 'top', formatter: 'S', color: '#22c55e', fontSize: 10, fontWeight: 'bold' },
      })),
      tooltip: {
        formatter: (p: any) => {
          const idx = p.dataIndex
          const t = trades.filter(t => dates.includes(t.exit_time.slice(0, 10)))[idx]
          return t ? `<b>Sell</b><br/>Price: ${t.exit_price.toFixed(2)}<br/>PnL: ${t.pnl >= 0 ? '+' : ''}${t.pnl.toFixed(2)}` : ''
        }
      },
      z: 10,
    })
  }

  const option = {
    backgroundColor: '#0d1117',
    title: { text: symbol, left: 'center', top: 8, textStyle: { color: '#e6edf3', fontSize: 14 } },
    legend: { data: ['MA5', 'MA20'], textStyle: { color: '#8b949e' }, top: 8, right: 20 },
    tooltip: { trigger: 'axis', axisPointer: { type: 'cross' } },
    grid: [
      { left: 60, right: 20, top: 50, height: '55%' },
      { left: 60, right: 20, top: '72%', height: '18%' },
    ],
    xAxis: [
      { type: 'category', data: dates, gridIndex: 0, axisLine: { lineStyle: { color: '#30363d' } }, axisLabel: { color: '#8b949e' } },
      { type: 'category', data: dates, gridIndex: 1, axisLine: { lineStyle: { color: '#30363d' } }, axisLabel: { color: '#8b949e' } },
    ],
    yAxis: [
      { scale: true, gridIndex: 0, splitLine: { lineStyle: { color: '#21262d' } }, axisLabel: { color: '#8b949e' } },
      { scale: true, gridIndex: 1, splitLine: { show: false }, axisLabel: { color: '#8b949e' } },
    ],
    dataZoom: [
      { type: 'inside', xAxisIndex: [0, 1], start: 0, end: 100 },
      { type: 'slider', xAxisIndex: [0, 1], bottom: 5, height: 20, borderColor: '#30363d', fillerColor: 'rgba(37,99,235,0.2)' },
    ],
    series,
  }

  return <ReactECharts option={option} style={{ height: 500 }} notMerge={true} />
}
