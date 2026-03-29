# web/ — Frontend Dashboard

## Tech Stack
React 19 + TypeScript + Vite 8 + TailwindCSS 4 + ECharts 5 + Monaco Editor

## Components
| Component | Role |
|-----------|------|
| Navbar | Top navigation with tab switching (Dashboard / Experiments / Code Editor) |
| SearchBar | Symbol + market + period + date range (calendar dropdown + stock search) |
| KlineChart | ECharts candlestick + volume + MA5/10/20/60 + BOLL bands + buy/sell markers |
| BacktestPanel | Strategy + params + trading costs + Single/WF mode + results + trade table + CSV export |
| FactorPanel | Factor IC evaluation + IC decay + distribution |
| ExperimentPanel | Single Run / Param Search sub-tabs + runs table + gate detail + delete/cleanup (V2.4+V2.5) |
| CandidateSearch | Parameter grid/random search form + ranked results table (V2.5) |
| CodeEditor | Monaco Python editor + template generation + syntax validation + save & contract test (V2.7) |
| ChatPanel | AI assistant sidebar — SSE streaming, tool action display (V2.7) |
| Dashboard | Main page, orchestrates chart/backtest/factor components |

## Theme
Dark (#0d1117). Chinese convention: red = up, green = down.

## Known Gaps
- **实验对比**: 多实验并排对比视图。

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
| components/CodeEditor.tsx | Monaco editor + template + validate/save (V2.7) |
| components/ChatPanel.tsx | AI assistant chat panel with SSE (V2.7) |
