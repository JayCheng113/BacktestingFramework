import ReactECharts from 'echarts-for-react'
import type { KlineBar } from '../types'

interface Props {
  data: KlineBar[]
  symbol: string
}

export default function KlineChart({ data, symbol }: Props) {
  if (!data.length) return <div className="p-8 text-center" style={{ color: 'var(--text-secondary)' }}>Search a symbol to view K-line data</div>

  const dates = data.map(d => d.date)
  const ohlc = data.map(d => [d.open, d.close, d.low, d.high])
  const volumes = data.map(d => d.volume)
  const colors = data.map(d => d.close >= d.open ? '#ef4444' : '#22c55e')

  const option = {
    backgroundColor: '#0d1117',
    title: { text: symbol, left: 'center', top: 8, textStyle: { color: '#e6edf3', fontSize: 14 } },
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
    series: [
      {
        type: 'candlestick', data: ohlc, xAxisIndex: 0, yAxisIndex: 0,
        itemStyle: { color: '#ef4444', color0: '#22c55e', borderColor: '#ef4444', borderColor0: '#22c55e' },
      },
      {
        type: 'bar', data: volumes.map((v, i) => ({ value: v, itemStyle: { color: colors[i] + '80' } })),
        xAxisIndex: 1, yAxisIndex: 1,
      },
    ],
  }

  return <ReactECharts option={option} style={{ height: 500 }} />
}
