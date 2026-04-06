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
| CodeEditor | Monaco编辑器 + 5类新建(策略/因子/组合策略/截面因子/ML Alpha) + registry侧栏(系统内置折叠+用户文件+注册状态) + 刷新/清理研究 + AI对话 |
| ChatPanel | AI助手 — SSE流式, 中文工具标签, 文件绑定对话, 多会话 |
| SettingsModal | LLM + Tushare 配置面板 |
| DocsPage | 开发文档 — 13章 (V2.11: 基本面数据层 + 选股因子研究指南 + 18因子表 + PIT说明) |
| ResearchPanel | 自主研究: 目标表单 + SSE中文进度 + 任务列表 + 报告 + 注册到全局 |
| PortfolioPanel | 组合回测: 3-tab (组合回测/选股因子研究/历史记录). 组合回测: 策略参数+多因子合成UI+参数搜索面板+股票池预设+净值曲线+指标+持仓饼图+CSV+多回测对比. 选股因子研究: 因子分类(7大类中文标签)+行业中性化开关+选股能力表+时序图+信号持续性+分档收益+因子相关性+前推验证+数据质量报告 (V2.10+V2.11+V2.11.1) |
| EnsembleBuilder | 策略组合构建器: mode radio + 子策略卡片 + 权重输入 + 高级设置 (V2.14) |
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

## V2.12.2 post-release
- **PortfolioPanel `market` state 贯通**: 原本无 `market` state, 7 个 API 调用默认 backend cn_stock. 新增 state + 7 个 API 调用点 + `PortfolioRunContent`/`PortfolioFactorContent` 两个子组件选择器 UI (A股/美股/港股).
- **PortfolioHistoryContent 对比图按真实日期对齐**: 之前 `xAxis.type='value'` + `data: equity.map((v,i)=>[i,v])` 按序号硬拼. 现在 run 有 `dates` 字段时走 `type:'time'`, 空 dates 行 (V2.12.2 之前的历史数据) 降级 index 轴 + 黄色警告 banner.
- **BacktestPanel / FactorPanel 陈旧 result 清理**: `useEffect` 按 symbol/market/period/dates/factor 变化清 result/wfResult/trades, 用户切标的/日期后不再看到前一次指标.
- **ChatPanel AI 创建文件原子绑定**: 流式中 fileKey 绑定用 `targetId` (消息发送时捕获) 而不是 `activeId` 闭包值 (避免用户切会话串线); fetch 失败从 "只切 filename 留旧代码" 改为 "atomic: 要么三元组全更新要么仅刷新侧栏 + 用户警告" (CodeEditor 配套支持 `(undefined, undefined, undefined)` 仅刷新调用); AI 创建 fileKey 采用 `${kind}:${filename}` 格式和 CodeEditor 一致消除重复会话.
- **CodeEditor deleteFile kind 校验**: 删除文件清编辑器时需要 filename **和** kind 都匹配, 避免同名跨类型 (strategy/factor) 误清.

## V2.13.2 — ML Alpha Frontend (Phase 6)
- **CodeEditor `+ ML Alpha` 按钮**: `CodeKind` union 加 `'ml_alpha'`, `KIND_LABELS: 'ML Alpha'`, `KIND_COLORS: '#059669'` (emerald). `mlAlphaFiles` state + sidebar 5-group 展示 + 新建/save/delete routing. Navigate to portfolio includes ml_alpha.
- **PortfolioFactorContent ML 诊断面板**: `MLDiagnosticsPanel` component — ML Alpha 下拉 + "运行诊断" 按钮 + verdict badge (5 色) + IS/OOS IC ECharts 双线图 + feature importance CV table (color-coded) + warnings panel + retrain/turnover metrics. 输入变更 (symbols/market/dates) 自动 reset 结果.
- **TypeScript 类型**: `DiagnosticsResult` + `MLDiagnosticsRequest` interfaces (typed, not `any`).
- **API client**: `mlAlphaDiagnostics(data: MLDiagnosticsRequest) → api.post<DiagnosticsResult>(...)`.
- **共享 labels**: `CATEGORY_LABELS` / `FACTOR_LABELS` 从 3 个重复定义抽到 `shared/portfolioLabels.ts`.
- **Race token 全覆盖**: `evalTokenRef` / `fundaTokenRef` / `compareTokenRef` (PortfolioPanel) + `loadFileTokenRef` (CodeEditor).
- **ML Alpha 因子分类**: backend 自动 categorize via `issubclass(cls, MLAlpha)`, 前端 factorCategories data-driven.

## V2.14 — 搜索增强 + ML 扩展 + Ensemble UI
- **CandidateSearch bool/enum 参数**: ParamRangeState 改 discriminated union (NumericParamRange/BoolParamRange/EnumParamRange), bool 参数显示 checkbox, enum/select 显示按钮组, 后端 ParamRangeRequest 放宽 `list[int|float|str|bool]`.
- **multi_select 组合搜索**: PortfolioPanel "组合搜索" checkbox → 自动 power-set (bitmask 生成所有非空子集), 64 上限硬限 (>6 因子禁用按钮), 原 `|` 分隔模式保留.
- **StrategyEnsemble UI**: 新 `EnsembleBuilder.tsx` 组件 — 4 mode radio (等权/手动/收益加权/反向波动率) + 子策略卡片 (参数编辑+同名序号) + 手动权重输入 + 高级设置折叠. PortfolioRunContent 检测 `selected === 'StrategyEnsemble'` 切换渲染. 后端 `_create_strategy` 新增 Ensemble 分支 (列表格式 sub_strategies 避免同名 key 冲突).
- **LightGBM/XGBoost 白名单**: `_build_supported_estimator_set` 可选加载 `LGBMRegressor` + `XGBRegressor` (仅 regressor, classifier 待定义分类契约), GPU 拦截 (tree_method/device/device_type), `pyproject.toml` 新增 `[ml-extra]` group.

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
| components/shared/portfolioLabels.ts | 共享 CATEGORY_LABELS + FACTOR_LABELS (V2.13.2, 消除 3 处重复) |
| components/EnsembleBuilder.tsx | 策略组合构建器: mode/sub-strategies/weights/advanced (V2.14) |
