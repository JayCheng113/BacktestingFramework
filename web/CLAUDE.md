# web/ — Frontend Dashboard

## Tech Stack
React 19 + TypeScript + Vite 8 + TailwindCSS 4 + ECharts 5 + Monaco Editor

## Components
| Component | Role |
|-----------|------|
| Navbar | 顶部导航 + 后端状态指示灯(绿=运行/红=断开, 10s轮询) (v0.2.11.1) |
| SearchBar | 股票搜索 + 市场 + 周期 + 日期范围 |
| KlineChart | ECharts K线图 + 成交量 + MA5/10/20/60 + BOLL + 买卖标记 |
| BacktestPanel | 策略下拉 + 参数(数值/布尔/文本) + 交易成本 + 结果 + 交易表 + CSV导出 |
| FactorPanel | 技术指标评估(单股): 预测能力(IC) + 稳定性(ICIR) + 信号持续性 + 分布 |
| ExperimentPanel | 3 sub-tabs (单次运行/参数搜索/组合实验→跳转组合历史) + 运行表 + 门控详情 |
| CandidateSearch | 参数网格/随机搜索 + 排名结果表 (全中文: 夏普/显著性/门控) |
| CodeEditor | Monaco编辑器 + 4类新建 + registry侧栏(系统内置折叠+用户文件+注册状态) + 刷新/清理研究 + AI对话 |
| ChatPanel | AI助手 — SSE流式, 中文工具标签, 文件绑定对话, 多会话 |
| SettingsModal | LLM + Tushare 配置面板 |
| DocsPage | 开发文档 — 13章 (V2.11: 基本面数据层 + 选股因子研究指南 + 18因子表 + PIT说明) |
| ResearchPanel | 自主研究: 目标表单 + SSE中文进度 + 任务列表 + 报告 + 注册到全局 |
| PortfolioPanel | 组合回测: 3-tab (组合回测/选股因子研究/历史记录). 组合回测: 策略参数+多因子合成UI+参数搜索面板+股票池预设+净值曲线+指标+持仓饼图+CSV+多回测对比. 选股因子研究: 因子分类(7大类中文标签)+行业中性化开关+选股能力表+时序图+信号持续性+分档收益+因子相关性+前推验证+数据质量报告 (V2.10+V2.11+V2.11.1) |
| DateRangePicker | 日期范围选择器 (react-datepicker): 开始/结束 + 快捷按钮 |
| BacktestSettings | 交易成本/规则: 买卖佣金, 印花税, 滑点率(模拟买卖价差), 最小交易单位, 涨跌停限制 |
| Dashboard | 主页: K线图 + 单股回测 + 技术指标评估 |

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

## V2.12 / V2.12.1 Key Changes
- **PortfolioPanel split**: 1281→574 行, 拆出 OptimizerPanel/RiskPanel/AttributionPanel/EventLogPanel 子组件 (V2.12.1 S5)
- **Types 补全**: 0 处 as any, strict TypeScript 全覆盖 (V2.12.1 S6)
- **ChatPanel 竞态修复**: aiCreatedFileRef 区分 AI 创建 vs 用户点击新建, 防止 conversation 丢失 (V2.12.1 post)
- **ChatPanel onCodeUpdate**: AI 创建 strategy/portfolio/factor 后自动刷新 CodeEditor 侧栏, kind 从 path 前缀推断
- **ChatPanel localStorage 清理**: "清空全部对话" 按钮防止重装残留
- **CodeEditor auto-overwrite**: save() 遇 "already exists" 自动用 overwrite=true 重试 (AI 创建场景)
- **指数增强 UI**: PortfolioPanel 组合 tab 添加 benchmark_index + max_tracking_error 表单字段 (V2.12.1 S4)

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
| components/DateRangePicker.tsx | Shared date range picker with preset buttons (V2.10) |
| components/PortfolioPanel.tsx | 组合回测: 3-tab+中性化+多因子合成+参数搜索 (V2.9+V2.10+V2.11.1) |
| pages/DocsPage.tsx | 开发文档: 13章 (V2.11: 基本面数据层 + 选股因子研究指南 + 18因子表 + PIT说明 + 数据获取流程) |
