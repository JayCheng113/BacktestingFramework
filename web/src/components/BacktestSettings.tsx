/**
 * V2.9: Shared backtest settings component.
 * Used by BacktestPanel and PortfolioPanel for consistent cost/rules configuration.
 */

interface BacktestSettingsValue {
  initial_cash: number
  benchmark: string
  buy_commission_rate: number
  sell_commission_rate: number
  min_commission: number
  stamp_tax_rate: number
  slippage_rate: number
  lot_size: number
  limit_pct: number
}

interface Props {
  value: BacktestSettingsValue
  onChange: (v: BacktestSettingsValue) => void
  showBenchmark?: boolean
  showInitialCash?: boolean
  showSellCommission?: boolean  // hide if backend doesn't support buy/sell split
  showStampTax?: boolean
  showLotSize?: boolean
  showLimitPct?: boolean
}

const inputStyle = { backgroundColor: 'var(--bg-primary)', border: '1px solid var(--border)', color: 'var(--text-primary)' }

export type { BacktestSettingsValue }

export default function BacktestSettings({ value, onChange, showBenchmark = true, showInitialCash = true, showSellCommission = true, showStampTax = true, showLotSize = true, showLimitPct = true }: Props) {
  const set = (key: keyof BacktestSettingsValue, v: number | string) =>
    onChange({ ...value, [key]: v })

  return (
    <div className="p-3 rounded" style={{ backgroundColor: 'var(--bg-primary)', border: '1px solid var(--border)' }}>
      <div className="text-xs font-medium mb-2" style={{ color: 'var(--text-secondary)' }}>回测设置</div>
      <div className="flex flex-wrap gap-3 items-end mb-2">
        {showInitialCash && (
          <div className="flex flex-col gap-1">
            <label className="text-xs" style={{ color: 'var(--text-secondary)' }}>初始资金</label>
            <input type="number" value={value.initial_cash} step={100000} min={10000}
              onChange={e => set('initial_cash', Number(e.target.value))}
              className="px-2 py-1 rounded text-xs w-28" style={inputStyle} />
          </div>
        )}
        {showBenchmark && (
          <div className="flex flex-col gap-1">
            <label className="text-xs" style={{ color: 'var(--text-secondary)' }}>基准 (留空=现金)</label>
            <input type="text" value={value.benchmark} placeholder="510300.SH"
              onChange={e => set('benchmark', e.target.value)}
              className="px-2 py-1 rounded text-xs w-24" style={inputStyle} />
          </div>
        )}
        <div className="flex flex-col gap-1">
          <label className="text-xs" style={{ color: 'var(--text-secondary)' }}>买入佣金</label>
          <input type="number" value={value.buy_commission_rate} step={0.0001} min={0}
            onChange={e => set('buy_commission_rate', Number(e.target.value))}
            className="px-2 py-1 rounded text-xs w-24" style={inputStyle} />
        </div>
        {showSellCommission && (
          <div className="flex flex-col gap-1">
            <label className="text-xs" style={{ color: 'var(--text-secondary)' }}>卖出佣金</label>
            <input type="number" value={value.sell_commission_rate} step={0.0001} min={0}
              onChange={e => set('sell_commission_rate', Number(e.target.value))}
              className="px-2 py-1 rounded text-xs w-24" style={inputStyle} />
          </div>
        )}
        <div className="flex flex-col gap-1">
          <label className="text-xs" style={{ color: 'var(--text-secondary)' }}>最低佣金</label>
          <input type="number" value={value.min_commission} step={1} min={0}
            onChange={e => set('min_commission', Number(e.target.value))}
            className="px-2 py-1 rounded text-xs w-16" style={inputStyle} />
        </div>
      </div>
      <div className="flex flex-wrap gap-3 items-end">
        {showStampTax && (
          <div className="flex flex-col gap-1">
            <label className="text-xs" style={{ color: 'var(--text-secondary)' }}>印花税(卖)</label>
            <input type="number" value={value.stamp_tax_rate} step={0.0001} min={0}
              onChange={e => set('stamp_tax_rate', Number(e.target.value))}
              className="px-2 py-1 rounded text-xs w-24" style={inputStyle} />
          </div>
        )}
        <div className="flex flex-col gap-1">
          <label className="text-xs" style={{ color: 'var(--text-secondary)' }}>滑点率</label>
          <input type="number" value={value.slippage_rate} step={0.001} min={0} max={0.1}
            onChange={e => set('slippage_rate', Number(e.target.value))}
            className="px-2 py-1 rounded text-xs w-20" style={inputStyle} />
        </div>
        {showLotSize && (
          <div className="flex flex-col gap-1">
            <label className="text-xs" style={{ color: 'var(--text-secondary)' }}>整手</label>
            <input type="number" value={value.lot_size} step={100} min={1}
              onChange={e => set('lot_size', Number(e.target.value))}
              className="px-2 py-1 rounded text-xs w-16" style={inputStyle} />
          </div>
        )}
        {showLimitPct && (
          <div className="flex flex-col gap-1">
            <label className="text-xs" style={{ color: 'var(--text-secondary)' }}>涨跌停%</label>
            <input type="number" value={Math.round(value.limit_pct * 100)} step={1} min={0} max={30}
              onChange={e => set('limit_pct', Number(e.target.value) / 100)}
              className="px-2 py-1 rounded text-xs w-16" style={inputStyle} />
          </div>
        )}
      </div>
    </div>
  )
}

export const DEFAULT_SETTINGS: BacktestSettingsValue = {
  initial_cash: 1_000_000,
  benchmark: '510300.SH',
  buy_commission_rate: 0.0003,
  sell_commission_rate: 0.0003,
  min_commission: 5,
  stamp_tax_rate: 0.0005,
  slippage_rate: 0,
  lot_size: 100,
  limit_pct: 0.10,
}
