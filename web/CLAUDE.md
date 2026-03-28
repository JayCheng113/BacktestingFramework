# web/ — Frontend Dashboard

## Tech Stack
React 19 + TypeScript + Vite 7 + TailwindCSS 4 + ECharts 5

## Components
| Component | Role |
|-----------|------|
| Navbar | Top navigation |
| SearchBar | Symbol + market + date range |
| KlineChart | ECharts candlestick + volume |
| BacktestPanel | Strategy selection + trading cost controls (commission/min_commission/slippage) + results + trade table + CSV export |
| FactorPanel | Factor IC evaluation |
| Dashboard | Main page |

## Theme
Dark (#0d1117). Chinese convention: red = up, green = down.

## Running
```bash
cd web && npm run dev  # http://localhost:3000
```
API proxied to http://localhost:8000

## Key Files
| File | Role |
|------|------|
| api/index.ts | API client functions |
| types/index.ts | TypeScript type definitions |
| styles/global.css | Global styles and Tailwind imports |
