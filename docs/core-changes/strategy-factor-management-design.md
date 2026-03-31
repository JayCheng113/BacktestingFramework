# 策略与因子管理 — 设计文档

## 现状分析

### 4 类可扩展对象

| 类型 | 基类 | 内置 | 用户目录 | 注册机制 | 前端入口 |
|------|------|------|---------|---------|---------|
| 单股策略 | Strategy | 3 (MA交叉/动量/布林) | strategies/ | `__init_subclass__` | 看板回测下拉 |
| 单股因子 | Factor | 9 (MA/EMA/RSI/MACD...) | factors/ | `__init_subclass__` | 看板因子下拉 |
| 组合策略 | PortfolioStrategy | 5 (TopN/MultiFactor/3 ETF) | portfolio_strategies/ | `__init_subclass__` | 组合回测下拉 |
| 截面因子 | CrossSectionalFactor | 3+18 (量价3+基本面18) | cross_factors/ | `__init_subclass__` | 组合因子下拉 |

### 用户旅程（当前）

**新建**：代码编辑器 → 4 个"新建"按钮 → 写代码 → 保存 → 自动注册 → 对应下拉可选 ✅

**查看**：
- 代码编辑器侧栏：按 4 组列出**用户文件**（不含内置）
- 各回测/评估下拉：列出**内置+用户注册的**（不区分来源）
- 没有统一的"所有已注册对象"视图 ❌

**删除**：
- 代码编辑器侧栏：可删除用户文件 → 同时清理 registry ✅
- 内置的不能删 ✅
- 但只能在代码编辑器里操作，其他地方看到的策略/因子无法管理 ❌

**修改**：代码编辑器 → 打开文件 → 编辑 → 保存 → 热重载 ✅

### 问题

1. **看不到全局** — 用户不知道系统里一共注册了多少策略/因子，哪些是内置哪些是自定义
2. **管理分散** — 新建在编辑器，使用在看板/组合，没有统一视图
3. **无法从使用处跳转到编辑** — 在组合下拉里看到一个策略，想看它的代码，没有直达路径
4. **研究助手生成的策略堆积** — research_ 前缀的策略越来越多，无法批量清理
5. **内置和用户策略混在一起** — 下拉列表里分不清哪些是系统自带、哪些是自己写的

---

## 设计原则

1. **代码编辑器是管理中心** — 策略/因子本质是代码文件，管理它们就是管理文件+注册状态
2. **不加新 tab** — 避免 tab 膨胀。增强现有编辑器侧栏即可
3. **内置只读、用户可编辑** — 内置策略显示但标记"系统"，不可删除
4. **注册状态可见** — 每个文件旁显示是否成功注册
5. **跨 tab 跳转** — 从任何下拉列表点击可跳转到编辑器查看代码

---

## 方案：增强代码编辑器侧栏

### 侧栏改造

当前侧栏：
```
策略
  my_strategy.py        [删除]
因子
  (空)
组合策略
  (空)
截面因子
  (空)
```

改造后：
```
策略 (6)                      [+ 新建]
  系统
    MACrossStrategy          只读
    MomentumStrategy         只读
    BollReversionStrategy    只读
  用户
    boll_macd_breakout.py    ● 已注册  [编辑] [删除]
    research_xxx.py          ● 已注册  [编辑] [删除]

因子 (9)                      [+ 新建]
  系统
    MA, EMA, RSI, MACD...    只读 (折叠显示)
  用户
    (无自定义因子)

组合策略 (5)                   [+ 新建]
  系统
    TopNRotation             只读
    MultiFactorRotation      只读
    EtfMacdRotation          只读
    EtfSectorSwitch          只读
    EtfStockEnhance          只读
  用户
    (无自定义组合策略)

截面因子 (21)                  [+ 新建]
  系统
    MomentumRank             只读
    VolumeRank               只读
    ReverseVolatilityRank    只读
    EP, BP, SP, DP...        只读 (18 基本面因子折叠)
  用户
    (无自定义截面因子)
```

### 需要的后端改动

**新增 API**: `GET /api/code/registry`

返回 4 类对象的完整注册信息：

```json
{
  "strategy": {
    "builtin": [
      {"name": "MACrossStrategy", "module": "ez.strategy.builtin.ma_cross", "description": "...", "editable": false}
    ],
    "user": [
      {"name": "BollMacdBreakout", "module": "strategies.boll_macd_breakout", "filename": "boll_macd_breakout.py", "editable": true}
    ]
  },
  "factor": { "builtin": [...], "user": [...] },
  "portfolio_strategy": { "builtin": [...], "user": [...] },
  "cross_factor": { "builtin": [...], "user": [...] }
}
```

判断 builtin vs user：
- `module` 以 `ez.` 开头 → builtin
- `module` 以 `strategies.` / `factors.` / `portfolio_strategies.` / `cross_factors.` 开头 → user

### 需要的前端改动

**代码编辑器侧栏**:
1. 启动时调 `/api/code/registry` 获取完整列表
2. 每组显示"系统"和"用户"两个子分组
3. 系统项：只读标记，点击可查看代码（只读模式）
4. 用户项：显示注册状态（● 绿=已注册），支持编辑/删除
5. 每组标题显示总数

**跨 tab 跳转**（可选增强）:
- 组合回测策略下拉 → hover 显示"查看代码"图标 → 点击跳转编辑器
- 因子下拉同理

### 需要的批量操作

- **清理研究策略**：一键删除所有 `research_` 前缀的策略文件
- 后端: `DELETE /api/code/cleanup-research` → 删所有 research_*.py + 清理 registry
- 前端: 用户文件列表顶部"清理研究策略"按钮

---

## 不做的事

1. **不加新 tab** — 代码编辑器已是"写+管"的地方
2. **不改注册机制** — `__init_subclass__` 自动注册很好，不需要手动注册/注销
3. **不做"禁用"功能** — 删除文件就是禁用，复杂度不值得
4. **不让内置策略可编辑** — 内置策略是框架的一部分，改了可能破坏系统

---

## 实施顺序

1. 后端: `/api/code/registry` 端点
2. 前端: 侧栏改造（系统/用户分组 + 注册状态 + 总数）
3. 后端+前端: 批量清理研究策略
4. （可选）跨 tab "查看代码" 跳转

---

## 工作量估算

| 项 | 后端 | 前端 | 测试 |
|----|------|------|------|
| registry API | 1 端点 | — | 2 tests |
| 侧栏改造 | — | CodeEditor 组件 | 手动 |
| 清理研究策略 | 1 端点 | 1 按钮 | 1 test |
| 合计 | ~50 行 | ~100 行 | ~3 tests |
