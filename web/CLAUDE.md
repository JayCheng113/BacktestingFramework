# web/ — Frontend Dashboard

## Tech Stack
React 19 + TypeScript + Vite 8 + TailwindCSS 4 + ECharts 5

## Components
| Component | Role |
|-----------|------|
| Navbar | Top navigation with tab switching (Dashboard / Experiments) |
| SearchBar | Symbol + market + period + date range (calendar dropdown + stock search) |
| KlineChart | ECharts candlestick + volume + MA5/10/20/60 + BOLL bands + buy/sell markers |
| BacktestPanel | Strategy + params + trading costs + Single/WF mode + results + trade table + CSV export |
| FactorPanel | Factor IC evaluation + IC decay + distribution |
| ExperimentPanel | Single Run / Param Search sub-tabs + runs table + gate detail + delete/cleanup (V2.4+V2.5) |
| CandidateSearch | Parameter grid/random search form + ranked results table (V2.5) |
| Dashboard | Main page, orchestrates chart/backtest/factor components |

## Theme
Dark (#0d1117). Chinese convention: red = up, green = down.

## Known Gaps
- **策略/因子代码编辑器**: Web 端在线写策略代码，模板初始化 + Python 编辑器 + 自动 contract test。计划 V2.7。
- **实验对比**: 多实验并排对比视图。
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
| components/ExperimentPanel.tsx | Experiment management UI (V2.4+V2.5) |
| components/CandidateSearch.tsx | Parameter search UI (V2.5) |
