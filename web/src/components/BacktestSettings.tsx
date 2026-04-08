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
    <div className="p-4 rounded" style={{ backgroundColor: 'var(--bg-primary)', border: '1px solid var(--border)' }}>
      <div className="text-xs font-medium mb-2" style={{ color: 'var(--text-secondary)' }}>回测设置 <span className="font-normal" style={{ color: 'var(--text-secondary)', opacity: 0.7 }}>(费率为小数: 0.0003 = 0.03%)</span></div>
      <div className="flex flex-wrap gap-3 items-end mb-2">
        {showInitialCash && (
          <div className="flex flex-col gap-1">
            <label className="text-xs" style={{ color: 'var(--text-secondary)' }}>初始资金</label>
            <input type="number" value={value.initial_cash} step={100000} min={10000}
              onChange={e => set('initial_cash', Number(e.target.value))}
              className="px-3 py-1.5 rounded text-xs w-28" style={inputStyle} />
          </div>
        )}
        {showBenchmark && (
          <div className="flex flex-col gap-1">
            <label className="text-xs" style={{ color: 'var(--text-secondary)' }}>基准 (留空=现金)</label>
            <input type="text" value={value.benchmark} placeholder="510300.SH"
              onChange={e => set('benchmark', e.target.value)}
              className="px-3 py-1.5 rounded text-xs w-24" style={inputStyle} />
          </div>
        )}
        <div className="flex flex-col gap-1">
          <label className="text-xs" style={{ color: 'var(--text-secondary)' }}>买入佣金率</label>
          <input type="number" value={value.buy_commission_rate} step={0.0001} min={0}
            onChange={e => set('buy_commission_rate', Number(e.target.value))}
            className="px-3 py-1.5 rounded text-xs w-24" style={inputStyle} />
        </div>
        {showSellCommission && (
          <div className="flex flex-col gap-1">
            <label className="text-xs" style={{ color: 'var(--text-secondary)' }}>卖出佣金率</label>
            <input type="number" value={value.sell_commission_rate} step={0.0001} min={0}
              onChange={e => set('sell_commission_rate', Number(e.target.value))}
              className="px-3 py-1.5 rounded text-xs w-24" style={inputStyle} />
          </div>
        )}
        <div className="flex flex-col gap-1">
          <label className="text-xs" style={{ color: 'var(--text-secondary)' }}>最低佣金</label>
          <input type="number" value={value.min_commission} step={1} min={0}
            onChange={e => set('min_commission', Number(e.target.value))}
            className="px-3 py-1.5 rounded text-xs w-16" style={inputStyle} />
        </div>
      </div>
      <div className="flex flex-wrap gap-3 items-end">
        {showStampTax && (
          <div className="flex flex-col gap-1">
            <label className="text-xs" style={{ color: 'var(--text-secondary)' }}>印花税率(卖)</label>
            <input type="number" value={value.stamp_tax_rate} step={0.0001} min={0}
              onChange={e => set('stamp_tax_rate', Number(e.target.value))}
              className="px-3 py-1.5 rounded text-xs w-24" style={inputStyle} />
          </div>
        )}
        <div className="flex flex-col gap-1">
          <label className="text-xs" style={{ color: 'var(--text-secondary)' }}>滑点率 (模拟买卖价差)</label>
          <input type="number" value={value.slippage_rate} step={0.001} min={0} max={0.1}
            onChange={e => set('slippage_rate', Number(e.target.value))}
            className="px-3 py-1.5 rounded text-xs w-20" style={inputStyle} />
        </div>
        {showLotSize && (
          <div className="flex flex-col gap-1">
            <label className="text-xs" style={{ color: 'var(--text-secondary)' }}>最小交易单位(股)</label>
            <input type="number" value={value.lot_size} step={100} min={1}
              onChange={e => set('lot_size', Number(e.target.value))}
              className="px-3 py-1.5 rounded text-xs w-16" style={inputStyle} />
          </div>
        )}
        {showLimitPct && (
          <div className="flex flex-col gap-1">
            <label className="text-xs" style={{ color: 'var(--text-secondary)' }}>涨跌停限制%</label>
            <input type="number" value={Math.round(value.limit_pct * 100)} step={1} min={0} max={30}
              onChange={e => set('limit_pct', Number(e.target.value) / 100)}
              className="px-3 py-1.5 rounded text-xs w-16" style={inputStyle} />
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

/** Market-aware defaults: A-share rules only for cn_stock */
export function getDefaultSettings(market: string): BacktestSettingsValue {
  if (market === 'cn_stock') return { ...DEFAULT_SETTINGS }
  // US/HK/other: no stamp tax, no lot size restriction, no limit price
  return {
    ...DEFAULT_SETTINGS,
    benchmark: '',
    stamp_tax_rate: 0,
    lot_size: 1,
    limit_pct: 0,
    min_commission: 0,
  }
}
