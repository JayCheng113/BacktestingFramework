import ReactECharts from 'echarts-for-react'
import type { KlineBar, TradeRecord } from '../types'

interface Props {
  data: KlineBar[]
  symbol: string
  trades?: TradeRecord[]
}

function computeMA(data: KlineBar[], period: number): (number | null)[] {
  const result: (number | null)[] = []
  let sum = 0
  for (let i = 0; i < data.length; i++) {
    sum += data[i].close
    if (i >= period) sum -= data[i - period].close
    result.push(i >= period - 1 ? +(sum / period).toFixed(2) : null)
  }
  return result
}

function computeBOLL(data: KlineBar[], period: number = 20, mult: number = 2): {
  mid: (number | null)[]; upper: (number | null)[]; lower: (number | null)[]
} {
  const mid: (number | null)[] = []
  const upper: (number | null)[] = []
  const lower: (number | null)[] = []
  for (let i = 0; i < data.length; i++) {
    if (i < period - 1) { mid.push(null); upper.push(null); lower.push(null); continue }
    const slice = data.slice(i - period + 1, i + 1).map(d => d.close)
    const avg = slice.reduce((a, b) => a + b, 0) / period
    const std = Math.sqrt(slice.reduce((s, v) => s + (v - avg) ** 2, 0) / period)
    mid.push(+avg.toFixed(2))
    upper.push(+(avg + mult * std).toFixed(2))
    lower.push(+(avg - mult * std).toFixed(2))
  }
  return { mid, upper, lower }
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
  // Store trade reference with each marker for correct tooltip mapping
  const buyTrades: TradeRecord[] = []
  const sellTrades: TradeRecord[] = []
  if (trades.length > 0) {
    const dateSet = new Set(dates)
    for (const t of trades) {
      const entryDate = t.entry_time.slice(0, 10)
      const exitDate = t.exit_time.slice(0, 10)
      if (dateSet.has(entryDate)) {
        buyMarkers.push({
          coord: [entryDate, t.entry_price],
          itemStyle: { color: '#ef4444' },
        })
        buyTrades.push(t)
      }
      if (dateSet.has(exitDate)) {
        sellMarkers.push({
          coord: [exitDate, t.exit_price],
          itemStyle: { color: t.pnl >= 0 ? '#ef4444' : '#22c55e' },  // Chinese: red=profit, green=loss
        })
        sellTrades.push(t)
      }
    }
  }

  const boll = computeBOLL(data)

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
    // Moving Averages
    { name: 'MA5', type: 'line', data: computeMA(data, 5), xAxisIndex: 0, yAxisIndex: 0, lineStyle: { color: '#f59e0b', width: 1 }, showSymbol: false, z: 5 },
    { name: 'MA10', type: 'line', data: computeMA(data, 10), xAxisIndex: 0, yAxisIndex: 0, lineStyle: { color: '#3b82f6', width: 1 }, showSymbol: false, z: 5 },
    { name: 'MA20', type: 'line', data: computeMA(data, 20), xAxisIndex: 0, yAxisIndex: 0, lineStyle: { color: '#a855f7', width: 1 }, showSymbol: false, z: 5 },
    { name: 'MA60', type: 'line', data: computeMA(data, 60), xAxisIndex: 0, yAxisIndex: 0, lineStyle: { color: '#22c55e', width: 1 }, showSymbol: false, z: 5 },
    // Bollinger Bands
    { name: 'BOLL Upper', type: 'line', data: boll.upper, xAxisIndex: 0, yAxisIndex: 0, lineStyle: { color: '#64748b', width: 1, type: 'dashed' }, showSymbol: false, z: 4 },
    { name: 'BOLL Mid', type: 'line', data: boll.mid, xAxisIndex: 0, yAxisIndex: 0, lineStyle: { color: '#64748b', width: 1 }, showSymbol: false, z: 4 },
    { name: 'BOLL Lower', type: 'line', data: boll.lower, xAxisIndex: 0, yAxisIndex: 0, lineStyle: { color: '#64748b', width: 1, type: 'dashed' }, showSymbol: false, z: 4 },
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
          const t = buyTrades[p.dataIndex]
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
          const t = sellTrades[p.dataIndex]
          return t ? `<b>Sell</b><br/>Price: ${t.exit_price.toFixed(2)}<br/>PnL: ${t.pnl >= 0 ? '+' : ''}${t.pnl.toFixed(2)}` : ''
        }
      },
      z: 10,
    })
  }

  const option = {
    backgroundColor: '#0d1117',
    title: { text: symbol, left: 'center', top: 8, textStyle: { color: '#e6edf3', fontSize: 14 } },
    legend: {
      data: ['MA5', 'MA10', 'MA20', 'MA60', 'BOLL Upper', 'BOLL Mid', 'BOLL Lower'],
      selected: { 'MA5': true, 'MA10': true, 'MA20': true, 'MA60': false, 'BOLL Upper': false, 'BOLL Mid': false, 'BOLL Lower': false },
      textStyle: { color: '#8b949e', fontSize: 11 }, top: 8, right: 10, itemWidth: 14, itemHeight: 8, itemGap: 8,
    },
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
