# web/ — Frontend Dashboard

## Tech Stack
React 19 + TypeScript + Vite 8 + TailwindCSS 4 + ECharts 5 + Monaco Editor

## Components
| Component | Role |
|-----------|------|
| Navbar | Top navigation: 看板 / 组合 / 实验 / 代码编辑器 / 研究助手 / 开发文档 (v0.2.9) |
| SearchBar | Symbol + market + period + date range (calendar dropdown + stock search) |
| KlineChart | ECharts candlestick + volume + MA5/10/20/60 + BOLL bands + buy/sell markers |
| BacktestPanel | Strategy dropdown (filters research_) + params (number/bool/str) + costs + results + trade table + CSV (V2.8.1: multi-type params) |
| FactorPanel | Factor IC evaluation + IC decay + distribution (V2.8.1: dynamic factor list from API) |
| ExperimentPanel | Single Run / Param Search sub-tabs + runs table (filters research_) + gate detail + date range column (V2.8) |
| CandidateSearch | Parameter grid/random search form + ranked results table (filters research_) |
| CodeEditor | Monaco editor + "新建策略/因子/组合策略/截面因子" buttons + file-bound AI chat + 4-group sidebar (V2.9.1) |
| ChatPanel | AI assistant — SSE streaming, Chinese tool labels, file-bound conversations, multi-conversation + localStorage (V2.8) |
| SettingsModal | LLM + Tushare config modal, writes to .env via API (V2.7) |
| DocsPage | Comprehensive documentation — 12 sections, 1658 lines (V2.8) |
| ResearchPanel | Autonomous research: goal form + date presets + SSE Chinese progress + task list + report + "注册到全局" promote button (V2.8) |
| PortfolioPanel | Portfolio backtest: dynamic strategy params from schema + ETF pool + BacktestSettings + equity curve vs benchmark + metrics + trades + history (V2.9.1) |
| BacktestSettings | Shared cost/rules component: buy/sell commission, stamp tax, slippage, lot size, limit price (V2.9) |
| Dashboard | Main page, orchestrates chart/backtest/factor components |

## Theme
Dark (#0d1117). Chinese convention: red = up, green = down.

## V2.8 Key Changes
- **Research isolation**: research_ prefixed strategies filtered from BacktestPanel/ExperimentPanel/CandidateSearch/CodeEditor via `key.includes('research_')`
- **Promote workflow**: ResearchPanel → "注册到全局" button → POST /api/code/promote → copies to user strategies
- **File-bound conversations**: ChatPanel binds to current filename, auto-switch on file change
- **CodeEditor simplification**: Removed old template form, replaced with one-click "新建策略/因子" buttons
- **Experiment date range**: ExperimentPanel shows start_date ~ end_date column
- **SSE Chinese formatting**: ResearchPanel formats events as readable Chinese (💡假设/✓成功/📊回测/🧠分析)
- **Tool labels**: ChatPanel shows ⏳/✓ Chinese labels instead of raw JSON for tool calls

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
| components/ExperimentPanel.tsx | Experiment management + research isolation + date range (V2.4+V2.5+V2.8) |
| components/CandidateSearch.tsx | Parameter search + research isolation (V2.5+V2.8) |
| components/CodeEditor.tsx | Monaco editor + new file buttons + file-bound chat (V2.7+V2.8) |
| components/ChatPanel.tsx | AI chat: file-bound conversations + Chinese tool labels (V2.7+V2.8) |
| components/SettingsModal.tsx | Settings modal for API keys (V2.7) |
| components/ResearchPanel.tsx | Research: goal form + SSE progress + report + promote (V2.8) |
| pages/DocsPage.tsx | Documentation: 12 sections, 1658 lines (V2.8) |
