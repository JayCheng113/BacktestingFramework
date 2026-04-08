/**
 * Shared IC/ICIR rating functions for factor evaluation.
 * Used by both FactorPanel (single-stock) and PortfolioFactorContent (cross-sectional).
 * Single source of truth — change thresholds here only.
 */
import { CHART } from './chartTheme'

export type Rating = { color: string; hint: string }

/** |IC| thresholds: >=0.05 strong, >=0.03 medium, >=0.01 weak, <0.01 ineffective */
export const rateIc = (v: number | null): Rating | null => {
  if (v == null) return null
  const a = Math.abs(v)
  if (a >= 0.05) return { color: CHART.success, hint: '强' }
  if (a >= 0.03) return { color: CHART.accent, hint: '中' }
  if (a >= 0.01) return { color: CHART.warn, hint: '弱' }
  return { color: CHART.error, hint: '无效' }
}

/** |ICIR| thresholds: >=0.5 very stable, >=0.3 stable, >=0.1 average, <0.1 unstable */
export const rateIcir = (v: number | null): Rating | null => {
  if (v == null) return null
  const a = Math.abs(v)
  if (a >= 0.5) return { color: CHART.success, hint: '很稳定' }
  if (a >= 0.3) return { color: CHART.accent, hint: '较稳定' }
  if (a >= 0.1) return { color: CHART.warn, hint: '一般' }
  return { color: CHART.error, hint: '不稳定' }
}
