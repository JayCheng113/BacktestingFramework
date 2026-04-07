/**
 * Shared ECharts dark theme constants.
 * Mirrors CSS variables from global.css so charts stay in sync with the app theme.
 */
export const CHART = {
  bg: '#0d1117',           // var(--bg-primary)
  bgSecondary: '#161b22',  // var(--bg-secondary)
  border: '#30363d',       // var(--border)
  text: '#e6edf3',         // var(--text-primary)
  textSecondary: '#8b949e',// var(--text-secondary)
  grid: '#21262d',         // grid lines
  accent: '#2563eb',       // var(--color-accent)
  up: '#ef4444',           // var(--color-up) — red = up (A-stock)
  down: '#22c55e',         // var(--color-down) — green = down
  warn: '#f59e0b',         // warning/amber
  ma5: '#f59e0b',
  ma10: '#3b82f6',
  ma20: '#a855f7',
  ma60: '#06b6d4',         // cyan — avoid conflict with down color
  boll: '#64748b',
} as const
