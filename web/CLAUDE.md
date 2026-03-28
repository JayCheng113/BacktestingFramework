# web/ — Frontend Dashboard

## Tech Stack
React 19 + TypeScript + Vite 8 + TailwindCSS 4 + ECharts 5

## Components
| Component | Role |
|-----------|------|
| Navbar | Top navigation with tab switching (Dashboard / Experiments) |
| SearchBar | Symbol + market + date range (calendar dropdown + stock search) |
| KlineChart | ECharts candlestick + volume + MA5/10/20/60 + BOLL bands + buy/sell markers |
| BacktestPanel | Strategy + params + trading costs (commission/min_commission/slippage) + Single/WF mode + results + trade table + CSV export |
| FactorPanel | Factor IC evaluation + IC decay + distribution |
| ExperimentPanel | Submit experiments + runs table + gate detail + metrics grid (V2.4) |
| Dashboard | Main page, orchestrates chart/backtest/factor components |

## Theme
Dark (#0d1117). Chinese convention: red = up, green = down.

## Known Gaps
- **Period selector**: K线和回测硬编码 `period: 'daily'`。后端已支持 weekly/monthly，前端缺选择器。计划 V2.5。
- **策略/因子代码编辑器**: Web 端在线写策略代码，模板初始化 + Python 编辑器 + 自动 contract test。计划 V2.5。
- **实验对比**: 多实验并排对比视图。计划 V2.4.1。
- **Duplicate 提示**: 提交重复实验时前端无提示。计划 V2.4.1。
- **AI Agent 对话框**: Web 端对话式策略开发助手（实现想法、找 bug、解释结果）。计划 V2.7。

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
| components/ExperimentPanel.tsx | Experiment management UI (V2.4) |
