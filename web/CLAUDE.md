# web/ — Frontend Dashboard

## Tech Stack
React 19 + TypeScript + Vite 8 + TailwindCSS 4 + ECharts 5

## Components
| Component | Role |
|-----------|------|
| Navbar | Top navigation |
| SearchBar | Symbol + market + date range (calendar dropdown + stock search) |
| KlineChart | ECharts candlestick + volume + MA5/10/20/60 + BOLL bands + buy/sell markers |
| BacktestPanel | Strategy + params + trading costs (commission/min_commission/slippage) + Single/WF mode + results + trade table + CSV export |
| FactorPanel | Factor IC evaluation + IC decay + distribution |
| Dashboard | Main page, orchestrates all components |

## Theme
Dark (#0d1117). Chinese convention: red = up, green = down.

## Known Gaps
- **Period selector**: K线和回测硬编码 `period: 'daily'`。后端已支持 weekly/monthly，前端缺选择器。计划 V2.5。
- **因子/策略创建页**: 人类研究员上传自定义代码。计划 V2.5。
- **实验管理页**: Agent/人工实验列表+对比。计划 V2.4。

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
