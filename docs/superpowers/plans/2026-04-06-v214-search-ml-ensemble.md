# V2.14 — 搜索增强 + ML 扩展 + Ensemble UI 实施计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 实现 B1-B4 四个 backlog 项: bool/enum 参数搜索、multi_select 组合搜索、Ensemble UI、LightGBM/XGBoost 白名单扩展

**Architecture:** 分 4 个独立功能块, 各自可独立测试和交付. B1+B2 是前端为主的搜索 UX 增强, B3 是全栈 Ensemble 暴露, B4 是 ML Alpha 后端扩展.

**Tech Stack:** React 19 + TypeScript + FastAPI + Pydantic + sklearn + lightgbm + xgboost

**版本分组:** B1+B2 一起做 (搜索增强 sprint), B4 独立 (ML 扩展), B3 独立 (最复杂). 建议顺序: B1 → B2 → B4 → B3.

---

## 文件结构总览

| 功能 | 新建文件 | 修改文件 |
|------|---------|---------|
| B1 | 无 | `web/src/components/CandidateSearch.tsx`, `ez/api/routes/candidates.py` |
| B2 | 无 | `web/src/components/PortfolioPanel.tsx`, `web/src/components/PortfolioRunContent.tsx` |
| B3 | `web/src/components/EnsembleBuilder.tsx` | `ez/api/routes/portfolio.py`, `web/src/components/PortfolioRunContent.tsx`, `web/src/types/index.ts`, `web/src/api/index.ts` |
| B4 | 无 | `ez/portfolio/ml_alpha.py`, `tests/test_portfolio/test_ml_alpha_sklearn.py`, `pyproject.toml` |
| Docs | 无 | `web/src/pages/DocsPage.tsx`, `web/CLAUDE.md`, `CLAUDE.md` |

---

## B1: Bool/Enum 参数搜索

### 现状分析

**问题**: `CandidateSearch.tsx` (单股参数搜索) 的 `ParamRangeState` 只支持 `int`/`float` (min/max/step), 完全忽略 `bool`/`str`/`select` 类型参数. `BacktestPanel.tsx` 已经支持 bool checkbox 渲染, 但搜索组件没跟进.

**后端现状**: `ez/api/routes/candidates.py:20-22` 的 `ParamRangeRequest.values` 类型是 `list[int | float]`, 不接受 bool/str.

**关键发现**: 后端的 `grid_search()` 和 `random_search()` (在 `ez/agent/candidate_search.py`) 已经是类型无关的 — 它们只做 `itertools.product(*value_lists)`, 所以只需要放宽 API 类型 + 前端 UI.

### 涉及文件

| 文件 | 位置 | 当前代码 | 改动 |
|------|------|---------|------|
| `ez/api/routes/candidates.py:20-22` | `ParamRangeRequest` | `values: list[int \| float]` | 改为 `values: list[int \| float \| str \| bool]` |
| `web/src/components/CandidateSearch.tsx:10-17` | `ParamRangeState` | 只有 min/max/step/defaultVal (全 number) | 改为 discriminated union: numeric vs bool vs enum |
| `web/src/components/CandidateSearch.tsx:20-36` | `generateValues` / `countValues` | 仅数值公式 | 增加 bool/enum 分支: 直接返回 values 数组 |
| `web/src/components/CandidateSearch.tsx:69-81` | `handleStrategyChange` | 所有参数都映射为 min/max/step | 根据 schema.type 分支: bool→`{values:[true,false]}`, select→`{values:schema.options}` |
| `web/src/components/CandidateSearch.tsx:197-237` | 参数范围 UI | 只有 min/max/step 三列 number input | bool: checkbox 组; select/enum: 按钮组; 其余不变 |

### Task B1.1: 后端 ParamRangeRequest 放宽类型

**Files:**
- Modify: `ez/api/routes/candidates.py:20-22`
- Test: 手动验证 Pydantic 接受 bool/str 值

- [ ] **Step 1: 修改 ParamRangeRequest**

```python
# ez/api/routes/candidates.py:20-22
class ParamRangeRequest(BaseModel):
    name: str
    values: list[int | float | str | bool]
```

- [ ] **Step 2: 验证后端兼容性**

现有 `_build_search_config` (line 59) 把 `values` 传给 `ParamRange(name=..., values=...)`, 确认 `ParamRange` dataclass 也需要放宽:

```python
# ez/agent/candidate_search.py:18-22 — ParamRange dataclass
# 现有: values: list[int | float]
# 改为: values: list[int | float | str | bool]
```

- [ ] **Step 3: Commit**

```bash
git add ez/api/routes/candidates.py ez/agent/candidate_search.py
git commit -m "feat(B1): widen ParamRangeRequest to accept bool/str values"
```

### Task B1.2: 前端 ParamRangeState 改为 union type

**Files:**
- Modify: `web/src/components/CandidateSearch.tsx:10-36`

- [ ] **Step 1: 重构 ParamRangeState**

```typescript
// web/src/components/CandidateSearch.tsx

// 替换旧的 ParamRangeState (lines 10-17)
interface NumericParamRange {
  name: string
  type: 'int' | 'float'
  min: number
  max: number
  step: number
  defaultVal: number
}

interface BoolParamRange {
  name: string
  type: 'bool'
  values: boolean[]      // [true, false]
  defaultVal: boolean
}

interface EnumParamRange {
  name: string
  type: 'select' | 'str'
  values: string[]        // schema.options
  defaultVal: string
  label?: string
}

type ParamRangeState = NumericParamRange | BoolParamRange | EnumParamRange
```

- [ ] **Step 2: 修改 generateValues / countValues / totalCombinations**

```typescript
// 替换 lines 19-41
function generateValues(pr: ParamRangeState, limit: number = 50): (number | string | boolean)[] {
  if (pr.type === 'bool') return pr.values
  if (pr.type === 'select' || pr.type === 'str') return pr.values
  // numeric — 原逻辑
  if (pr.step <= 0 || pr.min > pr.max) return []
  const count = Math.floor((pr.max - pr.min) / pr.step) + 1
  const n = Math.min(count, limit)
  const vals: number[] = []
  for (let i = 0; i < n; i++) {
    const v = pr.min + i * pr.step
    vals.push(pr.type === 'int' ? Math.round(v) : Math.round(v * 1000) / 1000)
  }
  return [...new Set(vals)]
}

function countValues(pr: ParamRangeState): number {
  if (pr.type === 'bool') return pr.values.length
  if (pr.type === 'select' || pr.type === 'str') return pr.values.length
  if (pr.step <= 0 || pr.min > pr.max) return 0
  return Math.floor((pr.max - pr.min) / pr.step) + 1
}
// totalCombinations 不变 — 已经调用 countValues
```

- [ ] **Step 3: 修改 handleStrategyChange**

```typescript
// 替换 lines 69-81
const handleStrategyChange = (name: string, strats?: StrategyInfo[]) => {
  setStrategyName(name)
  const s = (strats || strategies).find(s => s.name === name)
  if (s) {
    setParamRanges(Object.entries(s.parameters).map(([k, v]: [string, any]): ParamRangeState => {
      const type = v.type || 'float'
      if (type === 'bool' || typeof v.default === 'boolean') {
        return { name: k, type: 'bool', values: [true, false], defaultVal: v.default ?? true }
      }
      if (type === 'select' || type === 'str') {
        const options = v.options ?? [String(v.default ?? '')]
        return { name: k, type: type as 'select' | 'str', values: options, defaultVal: v.default ?? options[0] ?? '' }
      }
      // numeric (int/float)
      const def = v.default ?? 0
      const min = v.min ?? (type === 'int' ? Math.max(1, def - 10) : def * 0.5)
      const max = v.max ?? (type === 'int' ? def + 20 : def * 2)
      const step = type === 'int' ? Math.max(1, Math.round((max - min) / 5)) : (max - min) / 5
      return { name: k, type: type as 'int' | 'float', min, max, step, defaultVal: def }
    }))
  }
}
```

- [ ] **Step 4: 修改 updateRange 处理不同类型**

```typescript
// bool/enum 参数不用 updateRange (用 toggle),
// 只有 numeric 用 updateRange, 所以需要增加类型守卫
const updateRange = (i: number, field: string, val: number) => {
  const next = [...paramRanges]
  const pr = next[i]
  if (pr.type === 'int' || pr.type === 'float') {
    next[i] = { ...pr, [field]: val } as NumericParamRange
    setParamRanges(next)
  }
}

// 新增: bool toggle
const toggleBoolValue = (i: number, val: boolean) => {
  const next = [...paramRanges]
  const pr = next[i]
  if (pr.type === 'bool') {
    const cur = pr.values.includes(val) ? pr.values.filter(v => v !== val) : [...pr.values, val]
    next[i] = { ...pr, values: cur.length > 0 ? cur : [val] }
    setParamRanges(next)
  }
}

// 新增: enum toggle
const toggleEnumValue = (i: number, val: string) => {
  const next = [...paramRanges]
  const pr = next[i]
  if (pr.type === 'select' || pr.type === 'str') {
    const cur = pr.values.includes(val) ? pr.values.filter(v => v !== val) : [...pr.values, val]
    next[i] = { ...pr, values: cur.length > 0 ? cur : [val] }
    setParamRanges(next)
  }
}
```

- [ ] **Step 5: Commit**

```bash
git add web/src/components/CandidateSearch.tsx
git commit -m "feat(B1): CandidateSearch supports bool/enum param types"
```

### Task B1.3: 参数范围 UI 渲染

**Files:**
- Modify: `web/src/components/CandidateSearch.tsx:196-237` (参数范围 grid)

- [ ] **Step 1: 替换参数渲染逻辑**

现有代码 (lines 196-237) 对所有参数统一渲染 min/max/step/取值 四列.
改为根据 `pr.type` 分支渲染:

```tsx
{paramRanges.map((pr, i) => {
  if (pr.type === 'bool') {
    return (
      <div key={pr.name} className="flex items-center gap-3">
        <span className="text-sm font-medium w-[120px] truncate"
          style={{ color: 'var(--text-primary)' }} title={pr.name}>{pr.name}</span>
        <label className="flex items-center gap-1 text-xs" style={{ color: 'var(--text-secondary)' }}>
          <input type="checkbox" checked={pr.values.includes(true)}
            onChange={() => toggleBoolValue(i, true)} /> True
        </label>
        <label className="flex items-center gap-1 text-xs" style={{ color: 'var(--text-secondary)' }}>
          <input type="checkbox" checked={pr.values.includes(false)}
            onChange={() => toggleBoolValue(i, false)} /> False
        </label>
        <span className="text-xs" style={{ color: 'var(--text-secondary)' }}>
          [{pr.values.length}] {pr.values.map(String).join(', ')}
        </span>
      </div>
    )
  }

  if (pr.type === 'select' || pr.type === 'str') {
    // 可用选项: 从原始 schema options 获取
    const allOptions = pr.values  // 已经是候选值
    return (
      <div key={pr.name}>
        <span className="text-sm font-medium" style={{ color: 'var(--text-primary)' }}>{pr.name}</span>
        <div className="flex flex-wrap gap-1 mt-1">
          {allOptions.map(opt => (
            <button key={opt} onClick={() => toggleEnumValue(i, opt)}
              className="text-xs px-2 py-0.5 rounded"
              style={{
                backgroundColor: pr.values.includes(opt) ? 'var(--color-accent)' : 'var(--bg-primary)',
                color: pr.values.includes(opt) ? '#fff' : 'var(--text-secondary)',
                border: '1px solid var(--border)'
              }}>
              {opt}
            </button>
          ))}
        </div>
      </div>
    )
  }

  // numeric — 保持原有 min/max/step 渲染
  const count = countValues(pr)
  const preview = generateValues(pr, 6)
  const hasError = pr.min > pr.max || pr.step <= 0
  // ... 原逻辑不变
})}
```

**注意**: enum 类型需要另存一份 "所有可选值" 用于渲染按钮. `handleStrategyChange` 初始化时 `values` 就是全部可选值. toggle 操作在 `values` 上做增减. 但这里有语义冲突: `values` 既是 "所有可选项" 又是 "已选中项". 需要拆分:

```typescript
interface EnumParamRange {
  name: string
  type: 'select' | 'str'
  allOptions: string[]    // 所有可选值 (从 schema.options 来)
  selected: string[]      // 已选中值 (搜索时用)
  defaultVal: string
}
```

相应修改 `countValues`: 返回 `pr.selected.length`, `generateValues`: 返回 `pr.selected`.

- [ ] **Step 2: 修改 hasRangeErrors**

```typescript
// 原: paramRanges.some(pr => pr.min > pr.max || pr.step <= 0)
// 新: 增加 bool/enum 的 "至少选一个" 校验
const hasRangeErrors = paramRanges.some(pr => {
  if (pr.type === 'bool') return pr.values.length === 0
  if (pr.type === 'select' || pr.type === 'str') return pr.selected.length === 0
  return pr.min > pr.max || pr.step <= 0
})
```

- [ ] **Step 3: 修改 handleSearch 里的 values 生成**

```typescript
// 原: paramRanges.map(pr => ({ name: pr.name, values: generateValues(pr, 10000) }))
// 新: bool 和 enum 直接传 values
const ranges = paramRanges.map(pr => ({
  name: pr.name,
  values: generateValues(pr, 10000),
})).filter(pr => pr.values.length > 0)
```

`generateValues` 已经在 Step 2 处理了分支, 所以 handleSearch 不需要改.

- [ ] **Step 4: Commit**

```bash
git add web/src/components/CandidateSearch.tsx
git commit -m "feat(B1): bool/enum parameter UI in CandidateSearch"
```

### Task B1.4: 测试 + 回归

- [ ] **Step 1: 手动测试**

1. 创建一个包含 bool 参数的策略 (通过 CodeEditor 新建, 添加 `use_filter: bool = True` 到 `get_parameters_schema`)
2. 打开 CandidateSearch, 选择该策略, 验证 bool 参数显示 checkbox
3. 运行搜索, 验证 true/false 两种组合都被搜索

- [ ] **Step 2: 确认原有数值搜索不受影响**

选择内置策略 (如 MACrossStrategy), 验证 min/max/step 界面和搜索功能正常.

- [ ] **Step 3: Commit 最终调整 (如有)**

---

## B2: multi_select 组合搜索 (Power-Set + 64 Cap)

### 现状分析

**问题**: `PortfolioPanel.tsx` 的 multi_select 搜索用 `|` 分隔符手动输入子集, 但不支持自动生成 power-set (所有子集组合). 用户选了 4 个因子只能得到用户手动指定的组合, 不能自动搜索所有 2^4=16 种子集.

**涉及文件**:

| 文件 | 位置 | 当前代码 | 改动 |
|------|------|---------|------|
| `web/src/components/PortfolioRunContent.tsx:362-366` | 搜索面板头 | `"为当前策略的每个参数设置多个候选值"` | 增加 "组合搜索" checkbox |
| `web/src/components/PortfolioPanel.tsx:451-464` | `handleSearch` multi_select 分支 | `raw.split('\|')` 手动分割 | 当 comboSearch=true 时自动生成 power-set |
| `web/src/components/PortfolioRunContent.tsx:373-420` | multi_select 按钮组 | 可视化选择因子 | 增加 "组合搜索" 开关 + 超限提示 |

**后端零改动**: `_generate_combinations()` (`ez/api/routes/portfolio.py:1058-1072`) 已经是 `itertools.product(*values)` 通用逻辑.

### Task B2.1: Power-set 生成函数 + comboSearch state

**Files:**
- Modify: `web/src/components/PortfolioPanel.tsx` (state + handleSearch)
- Modify: `web/src/components/PortfolioRunContent.tsx` (props + UI)

- [ ] **Step 1: 在 PortfolioPanel 添加 comboSearch state**

```typescript
// PortfolioPanel.tsx, 在 searchGrid state 旁边添加:
const [comboSearch, setComboSearch] = useState(false)
```

将 `comboSearch` 和 `setComboSearch` 通过 props 传给 `PortfolioRunContent`.

- [ ] **Step 2: 添加 power-set 工具函数**

```typescript
// PortfolioPanel.tsx 顶部 (或 PortfolioRunContent.tsx)
function generatePowerSet(items: string[]): string[][] {
  const n = items.length
  const subsets: string[][] = []
  // 从 1 开始, 跳过空集
  for (let i = 1; i < (1 << n); i++) {
    const subset: string[] = []
    for (let j = 0; j < n; j++) {
      if (i & (1 << j)) subset.push(items[j])
    }
    subsets.push(subset)
  }
  return subsets
}
```

- [ ] **Step 3: 修改 handleSearch 的 multi_select 分支**

```typescript
// PortfolioPanel.tsx:451-464, 替换 multi_select 分支:
} else if (schema.type === 'multi_select') {
  if (comboSearch) {
    // Power-set 模式: 自动生成所有非空子集
    const selected = raw.split(',').map(x => x.trim()).filter(Boolean)
    if (selected.length > 0) {
      const subsets = generatePowerSet(selected)
      // 64 硬限在 UI 层已经阻止, 这里做 defense
      if (subsets.length > 64) {
        alert(`因子组合数 ${subsets.length} 超过 64 上限，请减少选中因子数`)
        return
      }
      paramGrid[key] = subsets
      totalCombos *= subsets.length
    }
  } else {
    // 原有逻辑: 手动 `|` 分隔
    const subsets = raw.split('|')
      .map(s => s.split(',').map(x => x.trim()).filter(Boolean))
      .filter(a => a.length > 0)
    if (subsets.length > 0) {
      paramGrid[key] = subsets
      totalCombos *= subsets.length
    }
  }
}
```

- [ ] **Step 4: Commit**

```bash
git add web/src/components/PortfolioPanel.tsx
git commit -m "feat(B2): power-set generation for multi_select param search"
```

### Task B2.2: 组合搜索 UI + 64 Cap 校验

**Files:**
- Modify: `web/src/components/PortfolioRunContent.tsx:360-366` (搜索面板)

- [ ] **Step 1: 在搜索面板添加 "组合搜索" 开关**

在 `PortfolioRunContent.tsx` 的搜索面板标题下方 (line 365 附近) 添加:

```tsx
<div className="flex items-center gap-3 mb-2">
  <label className="flex items-center gap-1.5 text-xs" style={{ color: 'var(--text-secondary)' }}>
    <input type="checkbox" checked={comboSearch} onChange={e => setComboSearch(e.target.checked)} />
    组合搜索 (自动生成所有因子子集)
  </label>
  {comboSearch && (() => {
    // 计算当前选中因子数和 power-set 大小
    const multiSelectKey = Object.entries(currentSchema).find(([_, s]) => s.type === 'multi_select')?.[0]
    const selectedCount = multiSelectKey
      ? (searchGrid[multiSelectKey] || '').split(',').filter(Boolean).length
      : 0
    const powerSetSize = selectedCount > 0 ? (1 << selectedCount) - 1 : 0
    const overLimit = powerSetSize > 64
    return (
      <span className="text-xs" style={{ color: overLimit ? '#ef4444' : 'var(--text-secondary)' }}>
        {selectedCount > 0 ? `${selectedCount} 因子 → ${powerSetSize} 种组合` : '请先选择因子'}
        {overLimit && ' (超过 64 上限，请减少因子)'}
      </span>
    )
  })()}
</div>
```

- [ ] **Step 2: 搜索按钮加 overLimit 禁用**

```tsx
// 在 searchLoading 条件上增加 overLimit 检查
const multiSelectKey = Object.entries(currentSchema).find(([_, s]) => s.type === 'multi_select')?.[0]
const selectedFactorCount = multiSelectKey ? (searchGrid[multiSelectKey] || '').split(',').filter(Boolean).length : 0
const powerSetOverLimit = comboSearch && selectedFactorCount > 6  // 2^6 = 64

<button onClick={handleSearch} disabled={searchLoading || powerSetOverLimit}
  ...>
  {searchLoading ? '搜索中...' : powerSetOverLimit ? '因子过多' : '开始搜索'}
</button>
```

- [ ] **Step 3: multi_select 选择区提示文本更新**

```tsx
// PortfolioRunContent.tsx:386, 修改 label
<label className="text-xs block mb-1" style={{ color: 'var(--text-secondary)' }}>
  {label} {comboSearch
    ? '(选中因子将自动生成所有子集组合)'
    : schema.type === 'multi_select'
      ? '(多选，所有勾选作为一个组合)'
      : '(多选)'}
</label>
```

- [ ] **Step 4: Commit**

```bash
git add web/src/components/PortfolioRunContent.tsx web/src/components/PortfolioPanel.tsx
git commit -m "feat(B2): combo search UI with 64-subset hard cap"
```

### Task B2.3: 测试 + 回归

- [ ] **Step 1: 手动测试 power-set 模式**

1. 打开组合回测 → 参数搜索 → 勾选 "组合搜索"
2. 选中 3 个因子 (如 EP, BP, SP) → 应显示 "3 因子 → 7 种组合"
3. 选中 7 个因子 → 应显示 "7 因子 → 127 种组合 (超过 64 上限)" 且搜索按钮禁用
4. 取消勾选 "组合搜索" → 回到手动 `|` 分隔模式

- [ ] **Step 2: 确认原有手动模式不受影响**

不勾选 "组合搜索" 时, 用 `|` 分隔手动输入 "ep,bp|ep,sp" → 应产生 2 个子集.

---

## B3: StrategyEnsemble UI

### 现状分析

**问题**: `StrategyEnsemble` (`ez/portfolio/ensemble.py`) 故意不注册到 `PortfolioStrategy._registry` (需要 mandatory `strategies` 参数), 前端完全没有暴露入口. 需要:
1. API 端新增 ensemble 处理分支
2. 前端新增 EnsembleBuilder 组件
3. 和现有策略选择 UI 集成

**设计决策**:
- StrategyEnsemble 不走注册表发现, 前端硬编码 "策略组合" 选项
- 使用递归 `_create_strategy()` 实例化子策略
- `strategy_params` 使用约定格式: `{mode, sub_strategies: [{name, params}, ...], weights: [...]}`
- sub_strategies 用列表而非 dict, 避免同名策略 key 冲突 (如两个 TopNRotation 用不同参数)
- 不支持嵌套 ensemble (UI 层限制, 后端不限)

### Task B3.1: 后端 _create_strategy Ensemble 分支

**Files:**
- Modify: `ez/api/routes/portfolio.py:440-492` (`_create_strategy`)
- Modify: `ez/api/routes/portfolio.py:571-620` (`/strategies` endpoint)

- [ ] **Step 1: 在 _create_strategy 添加 StrategyEnsemble 分支**

在 `ez/api/routes/portfolio.py` 的 `_create_strategy` 函数中, 在 `elif name in PortfolioStrategy.get_registry()` 之前插入:

```python
elif name == "StrategyEnsemble":
    from ez.portfolio.ensemble import StrategyEnsemble, EnsembleMode
    # sub_strategies 是列表: [{name: "TopNRotation", params: {factor: "ep", top_n: 10}}, ...]
    # 用列表而非 dict 避免同名策略 key 冲突
    sub_strategy_defs: list[dict] = p.pop("sub_strategies", [])
    if not sub_strategy_defs or len(sub_strategy_defs) < 2:
        raise HTTPException(400, "StrategyEnsemble 需要至少 2 个子策略")
    mode: EnsembleMode = p.pop("mode", "equal")
    ensemble_weights = p.pop("ensemble_weights", None)
    warmup_rebalances = p.pop("warmup_rebalances", 8)
    correlation_threshold = p.pop("correlation_threshold", 0.9)

    sub_strategies = []
    all_warnings: list[str] = []
    for sub_def in sub_strategy_defs:
        sub_name = sub_def.get("name", "")
        sp = dict(sub_def.get("params", {}))
        if not sub_name:
            raise HTTPException(400, "子策略 name 不能为空")
        sub_strat, sub_warn = _create_strategy(
            sub_name, sp, symbols=symbols, start=start, end=end,
            market=market, skip_ensure=skip_ensure,
        )
        sub_strategies.append(sub_strat)
        all_warnings.extend(sub_warn)

    try:
        ensemble = StrategyEnsemble(
            strategies=sub_strategies,
            mode=mode,
            ensemble_weights=ensemble_weights,
            warmup_rebalances=warmup_rebalances,
            correlation_threshold=correlation_threshold,
        )
    except (ValueError, TypeError) as e:
        raise HTTPException(400, str(e))

    return ensemble, all_warnings
```

- [ ] **Step 2: /strategies endpoint 添加 Ensemble 元信息**

在 `/strategies` endpoint 返回值末尾追加 StrategyEnsemble 的描述 (因为它不在 registry 里):

```python
# 在 list_portfolio_strategies() 末尾 return 前:
result.append({
    "name": "StrategyEnsemble",
    "description": "多策略组合: 等权/手动权重/收益加权/反向波动率",
    "parameters": {
        "mode": {
            "type": "select",
            "options": ["equal", "manual", "return_weighted", "inverse_vol"],
            "default": "equal",
            "label": "组合模式",
        },
        "sub_strategies": {
            "type": "multi_strategy",
            "default": [],
            "label": "子策略列表",
        },
        "ensemble_weights": {
            "type": "weights",
            "default": None,
            "label": "手动权重 (mode=manual 时)",
        },
        "warmup_rebalances": {
            "type": "int",
            "default": 8, "min": 1, "max": 50,
            "label": "预热再平衡次数",
        },
        "correlation_threshold": {
            "type": "float",
            "default": 0.9, "min": 0.0, "max": 1.0,
            "label": "相关性警告阈值",
        },
    },
    "is_ensemble": True,  # 前端用来区分
})
```

- [ ] **Step 3: Commit**

```bash
git add ez/api/routes/portfolio.py
git commit -m "feat(B3): _create_strategy StrategyEnsemble branch + /strategies metadata"
```

### Task B3.2: TypeScript 类型 + API client

**Files:**
- Modify: `web/src/types/index.ts`
- Modify: `web/src/api/index.ts`

- [ ] **Step 1: 添加 EnsembleConfig 类型**

```typescript
// web/src/types/index.ts
export interface SubStrategyDef {
  name: string
  params: Record<string, any>
}

export interface EnsembleConfig {
  mode: 'equal' | 'manual' | 'return_weighted' | 'inverse_vol'
  sub_strategies: SubStrategyDef[]   // 列表, 允许同名不同参数
  ensemble_weights?: number[]
  warmup_rebalances?: number
  correlation_threshold?: number
}
```

无需新 API endpoint — ensemble 通过现有 `/run` + `/walk-forward` + `/search` 传 `strategy_name: "StrategyEnsemble"` + `strategy_params: EnsembleConfig`.

- [ ] **Step 2: Commit**

```bash
git add web/src/types/index.ts
git commit -m "feat(B3): EnsembleConfig TypeScript type"
```

### Task B3.3: EnsembleBuilder 组件

**Files:**
- Create: `web/src/components/EnsembleBuilder.tsx`

- [ ] **Step 1: 创建 EnsembleBuilder 组件**

功能:
- 子策略选择器: 从 `/strategies` 列表中选择 (排除 StrategyEnsemble 自身), 最多 5 个
- 每个子策略的参数输入 (复用 PortfolioRunContent 的 renderParamInput 逻辑)
- Mode 选择器: 4 个 radio (等权/手动权重/收益加权/反向波动率)
- Manual weights: mode=manual 时显示权重输入, 实时归一化
- warmup_rebalances / correlation_threshold 高级设置 (折叠面板)
- 输出: `EnsembleConfig` 对象通过 props 回调

```tsx
interface EnsembleBuilderProps {
  strategies: StrategyInfo[]          // 可选子策略列表 (已排除 StrategyEnsemble)
  factors: string[]                   // 可用因子列表
  factorCategories: any[]
  onChange: (config: EnsembleConfig) => void
}

const MODE_LABELS: Record<string, string> = {
  equal: '等权',
  manual: '手动权重',
  return_weighted: '收益加权',
  inverse_vol: '反向波动率',
}

export default function EnsembleBuilder({ strategies, factors, factorCategories, onChange }: EnsembleBuilderProps) {
  const [mode, setMode] = useState<EnsembleConfig['mode']>('equal')
  const [subStrategies, setSubStrategies] = useState<SubStrategyDef[]>([])
  const [weights, setWeights] = useState<number[]>([])
  const [warmup, setWarmup] = useState(8)
  const [corrThreshold, setCorrThreshold] = useState(0.9)
  const [showAdvanced, setShowAdvanced] = useState(false)

  // 每次状态变化通知父组件
  useEffect(() => {
    onChange({
      mode,
      sub_strategies: subStrategies,
      ensemble_weights: mode === 'manual' ? weights : undefined,
      warmup_rebalances: warmup,
      correlation_threshold: corrThreshold,
    })
  }, [mode, subStrategies, weights, warmup, corrThreshold])

  // 添加子策略 (允许同名不同参数)
  const addStrategy = (name: string) => {
    if (subStrategies.length >= 5) return
    const s = strategies.find(s => s.name === name)
    const defaults: Record<string, any> = {}
    if (s) {
      for (const [k, v] of Object.entries(s.parameters)) {
        defaults[k] = (v as any).default
      }
    }
    setSubStrategies([...subStrategies, { name, params: defaults }])
    setWeights(prev => [...prev, 1])
  }

  // 移除子策略
  const removeStrategy = (idx: number) => {
    setSubStrategies(subStrategies.filter((_, i) => i !== idx))
    setWeights(prev => prev.filter((_, i) => i !== idx))
  }

  // 更新子策略参数
  const updateSubParam = (idx: number, key: string, value: any) => {
    setSubStrategies(prev => prev.map((s, i) =>
      i === idx ? { ...s, params: { ...s.params, [key]: value } } : s
    ))
  }

  return (
    <div className="space-y-3">
      {/* 组合模式选择 */}
      <div>
        <label className="text-xs" style={{ color: 'var(--text-secondary)' }}>组合模式</label>
        <div className="flex gap-3 mt-1">
          {Object.entries(MODE_LABELS).map(([k, label]) => (
            <label key={k} className="flex items-center gap-1 text-xs" style={{ color: 'var(--text-secondary)' }}>
              <input type="radio" checked={mode === k}
                onChange={() => setMode(k as EnsembleConfig['mode'])} /> {label}
            </label>
          ))}
        </div>
      </div>

      {/* 子策略选择 — 允许同名 (不同参数) */}
      <div>
        <label className="text-xs" style={{ color: 'var(--text-secondary)' }}>
          子策略 ({subStrategies.length}/5)
        </label>
        <select onChange={e => { addStrategy(e.target.value); e.target.value = '' }}
          className="w-full px-2 py-1.5 rounded text-sm mt-1" style={inputStyle}>
          <option value="">+ 添加子策略</option>
          {strategies
            .filter(s => s.name !== 'StrategyEnsemble')
            .map(s => <option key={s.name} value={s.name}>{s.name}</option>)}
        </select>
      </div>

      {/* 已选子策略 + 参数 */}
      {subStrategies.map((sub, idx) => {
        const s = strategies.find(s => s.name === sub.name)
        return (
          <div key={idx} className="p-3 rounded" style={{ backgroundColor: 'var(--bg-primary)', border: '1px solid var(--border)' }}>
            <div className="flex justify-between items-center mb-2">
              <span className="text-sm font-medium" style={{ color: 'var(--text-primary)' }}>
                {sub.name} {subStrategies.filter(x => x.name === sub.name).length > 1 ? `#${subStrategies.slice(0, idx).filter(x => x.name === sub.name).length + 1}` : ''}
              </span>
              <div className="flex items-center gap-2">
                {mode === 'manual' && (
                  <input type="number" value={weights[idx] ?? 1} min={0} step={0.1}
                    onChange={e => {
                      const next = [...weights]; next[idx] = Number(e.target.value)
                      setWeights(next)
                    }}
                    className="w-16 px-1 py-0.5 rounded text-xs" style={inputStyle}
                    title="权重" />
                )}
                <button onClick={() => removeStrategy(idx)} className="text-xs px-1" style={{ color: '#ef4444' }}>移除</button>
              </div>
            </div>
            {/* 子策略参数 */}
            {s && Object.entries(s.parameters).map(([key, schema]: [string, any]) => (
              <div key={key} className="flex items-center gap-2 mt-1">
                <label className="text-xs w-20" style={{ color: 'var(--text-secondary)' }}>{schema.label || key}</label>
                {(schema.type === 'select' || schema.type === 'multi_select')
                  ? <select value={sub.params[key] ?? schema.default}
                      onChange={e => updateSubParam(idx, key, e.target.value)}
                      className="flex-1 px-2 py-1 rounded text-xs" style={inputStyle}>
                      {(schema.options ?? factors).map((o: string) => <option key={o} value={o}>{o}</option>)}
                    </select>
                  : <input type={schema.type === 'bool' ? 'checkbox' : 'number'}
                      value={sub.params[key] ?? schema.default}
                      onChange={e => updateSubParam(idx, key, schema.type === 'bool' ? e.target.checked : Number(e.target.value))}
                      className="flex-1 px-2 py-1 rounded text-xs" style={inputStyle} />
                }
              </div>
            ))}
          </div>
        )
      })}

      {/* 高级设置 */}
      <details open={showAdvanced} onToggle={(e: any) => setShowAdvanced(e.target.open)}>
        <summary className="text-xs cursor-pointer" style={{ color: 'var(--text-secondary)' }}>高级设置</summary>
        <div className="mt-2 flex gap-4">
          <div>
            <label className="text-xs" style={{ color: 'var(--text-secondary)' }}>预热次数</label>
            <input type="number" value={warmup} min={1} max={50}
              onChange={e => setWarmup(Number(e.target.value))}
              className="w-16 px-2 py-1 rounded text-xs ml-1" style={inputStyle} />
          </div>
          <div>
            <label className="text-xs" style={{ color: 'var(--text-secondary)' }}>相关性阈值</label>
            <input type="number" value={corrThreshold} min={0} max={1} step={0.05}
              onChange={e => setCorrThreshold(Number(e.target.value))}
              className="w-16 px-2 py-1 rounded text-xs ml-1" style={inputStyle} />
          </div>
        </div>
      </details>

      {subStrategies.length < 2 && (
        <p className="text-xs" style={{ color: '#f59e0b' }}>请至少选择 2 个子策略</p>
      )}
    </div>
  )
}
```

- [ ] **Step 2: Commit**

```bash
git add web/src/components/EnsembleBuilder.tsx
git commit -m "feat(B3): EnsembleBuilder component"
```

### Task B3.4: PortfolioRunContent 集成 EnsembleBuilder

**Files:**
- Modify: `web/src/components/PortfolioRunContent.tsx`

- [ ] **Step 1: 当策略为 StrategyEnsemble 时渲染 EnsembleBuilder**

在 `PortfolioRunContent.tsx` 的策略参数区域, 检测 `selected === 'StrategyEnsemble'`:

```tsx
import EnsembleBuilder from './EnsembleBuilder'
import type { EnsembleConfig } from '../types'

// 在 state 区域:
const [ensembleConfig, setEnsembleConfig] = useState<EnsembleConfig | null>(null)

// 在策略参数渲染区域 (大约 line 200 附近, 策略 dropdown 下方):
{selected === 'StrategyEnsemble' ? (
  <EnsembleBuilder
    strategies={strategies.filter(s => s.name !== 'StrategyEnsemble')}
    factors={factors}
    factorCategories={factorCategories}
    onChange={setEnsembleConfig}
  />
) : (
  // 原有参数渲染逻辑
  ...
)}
```

- [ ] **Step 2: handleRun / handleWalkForward 传 ensemble 参数**

```typescript
// 在 handleRun 中, 构建 strategy_params 时:
const params = selected === 'StrategyEnsemble' && ensembleConfig
  ? ensembleConfig
  : strategyParams
```

- [ ] **Step 3: Commit**

```bash
git add web/src/components/PortfolioRunContent.tsx
git commit -m "feat(B3): integrate EnsembleBuilder into PortfolioRunContent"
```

### Task B3.5: 测试

- [ ] **Step 1: 写后端 API 测试**

```python
# tests/test_api/test_ensemble_api.py
def test_ensemble_run_equal_mode():
    resp = client.post("/api/portfolio/run", json={
        "strategy_name": "StrategyEnsemble",
        "symbols": ["000001.SZ", "000002.SZ", "600000.SH"],
        "market": "cn_stock",
        "start_date": "2023-01-01",
        "end_date": "2024-01-01",
        "strategy_params": {
            "mode": "equal",
            "sub_strategies": [
                {"name": "TopNRotation", "params": {"factor": "momentum_rank_20", "top_n": 5}},
                {"name": "TopNRotation", "params": {"factor": "ep", "top_n": 10}},
            ],
        },
    })
    # 数据获取可能失败 (502), 但 Pydantic + _create_strategy 不应该报错
    assert resp.status_code in (200, 502)

def test_ensemble_less_than_2_strategies_400():
    resp = client.post("/api/portfolio/run", json={
        "strategy_name": "StrategyEnsemble",
        "symbols": ["000001.SZ"],
        "strategy_params": {
            "mode": "equal",
            "sub_strategies": [
                {"name": "TopNRotation", "params": {"factor": "momentum_rank_20", "top_n": 5}},
            ],
        },
    })
    assert resp.status_code == 400
```

- [ ] **Step 2: 手动前端测试**

1. 组合回测 → 策略下拉选 "StrategyEnsemble" → 应显示 EnsembleBuilder
2. 添加 2 个子策略 → 设置参数 → 选 "等权" → 运行回测
3. 切换 "手动权重" → 应显示权重输入框
4. 选回其他策略 (如 TopNRotation) → 应恢复普通参数界面

- [ ] **Step 3: Commit**

```bash
git add tests/test_api/test_ensemble_api.py
git commit -m "test(B3): ensemble API integration tests"
```

---

## B4: LightGBM/XGBoost 白名单扩展

### 现状分析

**白名单机制** (`ez/portfolio/ml_alpha.py:96-169`):
- `_build_supported_estimator_set()`: lazy import sklearn 类, 构建 `frozenset[type]`
- `_assert_supported_estimator()`: `type(instance)` 身份检查 (非 isinstance) + `n_jobs=1` 强制
- 全局缓存 `_SUPPORTED_ESTIMATOR_CACHE`

**现有测试覆盖缺口** (`tests/test_portfolio/test_ml_alpha_sklearn.py`):

| Estimator | Deepcopy 测试 | 跨实例确定性 | E2E Backtest | E2E Walk-Forward |
|-----------|-------------|------------|-------------|-----------------|
| Ridge | ✅ (3 tests) | ✅ | ✅ | ✅ |
| RandomForest | ✅ (1 test) | ✅ | ✅ | ✅ |
| GradientBoosting | ✅ (1 test) | ❌ | ✅ | ✅ |
| Lasso | ❌ | ❌ | ❌ | ❌ |
| LinearRegression | ❌ | ❌ | ❌ | ❌ |
| ElasticNet | ❌ | ❌ | ❌ | ❌ |
| DecisionTree | ❌ | ❌ | ❌ | ❌ |

**B4 前置**: 补齐现有 sklearn estimator 的 deepcopy 测试, 再添加 LightGBM/XGBoost.

### Task B4.1: 补齐现有 sklearn deepcopy 测试

**Files:**
- Modify: `tests/test_portfolio/test_ml_alpha_sklearn.py`

- [ ] **Step 1: 添加 Lasso/LinearRegression/ElasticNet/DecisionTree deepcopy 测试**

```python
class TestMLAlphaLassoDeepcopy:
    def test_fit_alpha_deepcopy_preserves_predictions(self):
        from sklearn.linear_model import Lasso
        alpha = MLAlpha(
            name="_test_lasso_dc", model_factory=lambda: Lasso(alpha=0.01),
            feature_fn=_simple_feature_fn, target_fn=_forward_return_target,
            train_window=60, retrain_freq=20, purge_days=5,
        )
        _run_deepcopy_test(alpha)  # 抽取通用 helper

class TestMLAlphaLinearRegressionDeepcopy:
    def test_fit_alpha_deepcopy_preserves_predictions(self):
        from sklearn.linear_model import LinearRegression
        alpha = MLAlpha(
            name="_test_lr_dc", model_factory=lambda: LinearRegression(),
            feature_fn=_simple_feature_fn, target_fn=_forward_return_target,
            train_window=60, retrain_freq=20, purge_days=5,
        )
        _run_deepcopy_test(alpha)

class TestMLAlphaElasticNetDeepcopy:
    def test_fit_alpha_deepcopy_preserves_predictions(self):
        from sklearn.linear_model import ElasticNet
        alpha = MLAlpha(
            name="_test_en_dc", model_factory=lambda: ElasticNet(alpha=0.01),
            feature_fn=_simple_feature_fn, target_fn=_forward_return_target,
            train_window=60, retrain_freq=20, purge_days=5,
        )
        _run_deepcopy_test(alpha)

class TestMLAlphaDecisionTreeDeepcopy:
    def test_fit_alpha_deepcopy_preserves_predictions(self):
        from sklearn.tree import DecisionTreeRegressor
        alpha = MLAlpha(
            name="_test_dt_dc", model_factory=lambda: DecisionTreeRegressor(max_depth=3, random_state=42),
            feature_fn=_simple_feature_fn, target_fn=_forward_return_target,
            train_window=60, retrain_freq=20, purge_days=5,
        )
        _run_deepcopy_test(alpha)
```

其中 `_run_deepcopy_test` 是从现有 `TestMLAlphaRidgeDeepcopy` 提取的通用 helper:

```python
def _run_deepcopy_test(alpha: MLAlpha):
    """Train alpha on synthetic data, deepcopy, verify predictions identical."""
    import copy
    data = _make_data()
    sym = list(data.keys())[0]
    df = data[sym]
    # 触发至少一次训练
    for i in range(80, 120):
        alpha.compute(sym, df.iloc[:i], df.index[i-1])
    # deepcopy
    cloned = copy.deepcopy(alpha)
    # 验证两者产生相同预测
    for i in range(120, 140):
        orig_score = alpha.compute(sym, df.iloc[:i], df.index[i-1])
        clone_score = cloned.compute(sym, df.iloc[:i], df.index[i-1])
        if orig_score is not None and clone_score is not None:
            assert abs(orig_score - clone_score) < 1e-10
```

- [ ] **Step 2: 添加 GBR 跨实例确定性测试**

```python
# 在 TestMLAlphaCrossInstanceDeterminism 中添加:
def test_gbr_cross_instance_determinism(self):
    from sklearn.ensemble import GradientBoostingRegressor
    def factory():
        return GradientBoostingRegressor(n_estimators=10, max_depth=2, random_state=42)
    _run_cross_instance_determinism_test(factory, "_test_gbr_det")
```

- [ ] **Step 3: 运行测试验证**

```bash
pytest tests/test_portfolio/test_ml_alpha_sklearn.py -v
```

- [ ] **Step 4: Commit**

```bash
git add tests/test_portfolio/test_ml_alpha_sklearn.py
git commit -m "test(B4): complete deepcopy + determinism tests for all 7 V1 estimators"
```

### Task B4.2: pyproject.toml 新增可选依赖

**Files:**
- Modify: `pyproject.toml:23-34`

- [ ] **Step 1: 添加 lightgbm 和 xgboost optional groups**

```toml
[project.optional-dependencies]
dev = [
    "pytest>=8.3",
    "pytest-asyncio>=0.24",
    "ruff>=0.8",
]
tushare = ["tushare>=1.4"]
akshare = ["akshare>=1.14"]
ml = ["scikit-learn>=1.5"]
ml-extra = ["scikit-learn>=1.5", "lightgbm>=4.0", "xgboost>=2.0"]
all = ["tushare>=1.4", "akshare>=1.14", "scikit-learn>=1.5", "lightgbm>=4.0", "xgboost>=2.0"]
```

- [ ] **Step 2: Commit**

```bash
git add pyproject.toml
git commit -m "feat(B4): add lightgbm/xgboost optional dependency groups"
```

### Task B4.3: 扩展白名单

**Files:**
- Modify: `ez/portfolio/ml_alpha.py:96-134`

- [ ] **Step 1: 修改 _build_supported_estimator_set**

```python
def _build_supported_estimator_set() -> frozenset[type]:
    """Construct the estimator whitelist.

    V1: 7 sklearn classes (always required).
    V2 extensions: LightGBM + XGBoost (optional, graceful skip).
    """
    try:
        from sklearn.linear_model import Ridge, Lasso, LinearRegression, ElasticNet
        from sklearn.tree import DecisionTreeRegressor
        from sklearn.ensemble import RandomForestRegressor, GradientBoostingRegressor
    except ImportError as e:
        raise ImportError(
            "scikit-learn>=1.5 is required for MLAlpha. "
            "Install with: pip install -e '.[ml]'"
        ) from e

    estimators = {
        Ridge, Lasso, LinearRegression, ElasticNet,
        DecisionTreeRegressor, RandomForestRegressor, GradientBoostingRegressor,
    }

    # V2.14: LightGBM (optional)
    try:
        from lightgbm import LGBMRegressor, LGBMClassifier
        estimators.update({LGBMRegressor, LGBMClassifier})
    except ImportError:
        pass  # lightgbm not installed — skip

    # V2.14: XGBoost (optional)
    try:
        from xgboost import XGBRegressor, XGBClassifier
        estimators.update({XGBRegressor, XGBClassifier})
    except ImportError:
        pass  # xgboost not installed — skip

    return frozenset(estimators)
```

- [ ] **Step 2: 更新 _assert_supported_estimator 错误信息**

```python
def _assert_supported_estimator(instance: Any) -> None:
    # ... 现有逻辑不变, 只修改错误信息:
    if cls not in _SUPPORTED_ESTIMATOR_CACHE:
        allowed = sorted(c.__name__ for c in _SUPPORTED_ESTIMATOR_CACHE)
        raise UnsupportedEstimatorError(
            f"Estimator class {cls.__module__}.{cls.__name__} is not on "
            f"the MLAlpha whitelist. Currently allowed: {allowed}. "
            f"Install lightgbm/xgboost for additional estimators: "
            f"pip install -e '.[ml-extra]'"
        )
    # n_jobs 检查: lightgbm 用 n_jobs, xgboost 用 n_jobs
    n_jobs = getattr(instance, "n_jobs", None)
    if n_jobs is not None and n_jobs != 1:
        raise UnsupportedEstimatorError(
            f"Estimator {cls.__name__} has n_jobs={n_jobs}, but the "
            f"sandbox blocks multiprocessing. Construct with n_jobs=1."
        )
    # XGBoost: 检查 tree_method 不是 gpu_hist
    tree_method = getattr(instance, "tree_method", None)
    if tree_method and "gpu" in str(tree_method).lower():
        raise UnsupportedEstimatorError(
            f"Estimator {cls.__name__} uses GPU tree_method='{tree_method}'. "
            f"Only CPU methods are allowed in the sandbox."
        )
```

- [ ] **Step 3: 更新 ML_ALPHA_TEMPLATE**

在模板注释中添加 LightGBM/XGBoost 示例:

```python
# 在 ML_ALPHA_TEMPLATE 的注释部分追加:
# V2.14 新增支持:
# - lightgbm.LGBMRegressor / LGBMClassifier
# - xgboost.XGBRegressor / XGBClassifier
# 安装: pip install -e '.[ml-extra]'
```

- [ ] **Step 4: Commit**

```bash
git add ez/portfolio/ml_alpha.py
git commit -m "feat(B4): expand whitelist with LightGBM/XGBoost (optional)"
```

### Task B4.4: LightGBM/XGBoost 测试

**Files:**
- Modify: `tests/test_portfolio/test_ml_alpha_sklearn.py`

- [ ] **Step 1: 添加 LightGBM 测试 (条件跳过)**

```python
class TestMLAlphaLightGBMDeepcopy:
    @pytest.fixture(autouse=True)
    def _skip_without_lgbm(self):
        pytest.importorskip("lightgbm", reason="LightGBM tests need lightgbm")

    def test_lgbm_regressor_deepcopy(self):
        from lightgbm import LGBMRegressor
        alpha = MLAlpha(
            name="_test_lgbm_dc",
            model_factory=lambda: LGBMRegressor(
                n_estimators=10, max_depth=3, n_jobs=1,
                random_state=42, verbose=-1,
            ),
            feature_fn=_simple_feature_fn,
            target_fn=_forward_return_target,
            train_window=60, retrain_freq=20, purge_days=5,
        )
        _run_deepcopy_test(alpha)

    def test_lgbm_e2e_backtest(self):
        from lightgbm import LGBMRegressor
        alpha = MLAlpha(
            name="_test_lgbm_e2e",
            model_factory=lambda: LGBMRegressor(
                n_estimators=10, max_depth=3, n_jobs=1,
                random_state=42, verbose=-1,
            ),
            feature_fn=_simple_feature_fn,
            target_fn=_forward_return_target,
            train_window=60, retrain_freq=20, purge_days=5,
        )
        _run_e2e_backtest_test(alpha)  # 提取通用 helper
```

- [ ] **Step 2: 添加 XGBoost 测试 (条件跳过)**

```python
class TestMLAlphaXGBoostDeepcopy:
    @pytest.fixture(autouse=True)
    def _skip_without_xgb(self):
        pytest.importorskip("xgboost", reason="XGBoost tests need xgboost")

    def test_xgb_regressor_deepcopy(self):
        from xgboost import XGBRegressor
        alpha = MLAlpha(
            name="_test_xgb_dc",
            model_factory=lambda: XGBRegressor(
                n_estimators=10, max_depth=3, n_jobs=1,
                random_state=42, verbosity=0,
            ),
            feature_fn=_simple_feature_fn,
            target_fn=_forward_return_target,
            train_window=60, retrain_freq=20, purge_days=5,
        )
        _run_deepcopy_test(alpha)

    def test_xgb_gpu_tree_method_rejected(self):
        from xgboost import XGBRegressor
        with pytest.raises(UnsupportedEstimatorError, match="GPU"):
            MLAlpha(
                name="_test_xgb_gpu",
                model_factory=lambda: XGBRegressor(tree_method="gpu_hist", n_jobs=1),
                feature_fn=_simple_feature_fn,
                target_fn=_forward_return_target,
                train_window=60, retrain_freq=20, purge_days=5,
            )

    def test_xgb_e2e_backtest(self):
        from xgboost import XGBRegressor
        alpha = MLAlpha(
            name="_test_xgb_e2e",
            model_factory=lambda: XGBRegressor(
                n_estimators=10, max_depth=3, n_jobs=1,
                random_state=42, verbosity=0,
            ),
            feature_fn=_simple_feature_fn,
            target_fn=_forward_return_target,
            train_window=60, retrain_freq=20, purge_days=5,
        )
        _run_e2e_backtest_test(alpha)
```

- [ ] **Step 3: CI 配置更新**

在 `.github/workflows/ci.yml` 中, 全量测试 job 添加 `pip install -e '.[ml-extra]'`:

```yaml
# 在 install 步骤中:
- name: Install dependencies
  run: |
    pip install -e '.[dev,ml-extra]'
```

不安装 `ml-extra` 的环境 (如 minimal CI) 会自动跳过 LightGBM/XGBoost 测试 (pytest.importorskip).

- [ ] **Step 4: 运行测试**

```bash
pip install lightgbm xgboost
pytest tests/test_portfolio/test_ml_alpha_sklearn.py -v
```

- [ ] **Step 5: Commit**

```bash
git add tests/test_portfolio/test_ml_alpha_sklearn.py .github/workflows/ci.yml
git commit -m "test(B4): LightGBM/XGBoost deepcopy + e2e + GPU rejection tests"
```

---

## Docs: DocsPage 更新

### 现状

`web/src/pages/DocsPage.tsx` 有 13 章, 2057 行. 以下内容需要新增或更新:

| 章节 | 当前状态 | 需要更新 |
|------|---------|---------|
| Ch6 实验流水线 (line 733) | 只说数值参数搜索 | 新增 bool/enum 参数说明 |
| Ch8 组合回测 (line 921) | 无 "组合搜索" 说明 | 新增 power-set 组合搜索 + Ensemble UI |
| 新增 Ch14 | 无 ML Alpha 文档 | ML Alpha 完整章节 (框架概述 + 白名单 + 诊断 + 模板) |

### Task Docs.1: 更新 Ch6 参数搜索说明

**Files:**
- Modify: `web/src/pages/DocsPage.tsx` (Ch6 实验流水线, ~line 790)

- [ ] **Step 1: 在参数搜索部分新增 bool/enum 说明**

在 Grid/Random 搜索说明之后 (~line 841), 添加:

```
V2.14 新增: 参数搜索支持布尔和枚举类型参数。
对于布尔参数 (如 use_filter), 界面自动展示 True/False 勾选框。
对于枚举参数 (如 mode), 界面展示所有选项按钮。
搜索时自动组合所有勾选的值。
```

### Task Docs.2: 更新 Ch8 组合回测

**Files:**
- Modify: `web/src/pages/DocsPage.tsx` (Ch8 组合回测, ~line 1083)

- [ ] **Step 1: 在参数搜索工具后添加 "组合搜索" 说明**

```
组合搜索: 勾选 "组合搜索" 后, 系统自动生成所有因子子集组合 (power-set).
例如选中 EP/BP/SP 三个因子, 将自动生成 7 种组合
(EP、BP、SP、EP+BP、EP+SP、BP+SP、EP+BP+SP).
硬限: 最多 6 个因子 (2^6=64 种组合). 超过时搜索按钮禁用.
```

- [ ] **Step 2: 新增 "策略组合" (Ensemble) 说明**

在组合回测章节的策略介绍部分添加:

```
策略组合 (StrategyEnsemble):
在策略下拉中选择 "StrategyEnsemble", 即可将多个子策略组合使用。
提供 4 种组合模式:
- 等权: 所有子策略权重相同
- 手动权重: 用户指定各子策略权重
- 收益加权: 按历史假想收益自动调整权重 (需预热期)
- 反向波动率: 波动率低的子策略获得更高权重

最多 5 个子策略, 各子策略独立设置参数.
手动权重模式下需要输入各子策略权重值.
```

### Task Docs.3: 新增 ML Alpha 章节

**Files:**
- Modify: `web/src/pages/DocsPage.tsx`

- [ ] **Step 1: 在 sections 数组中添加新章节**

```typescript
// line 15 之后:
{ id: 'ml-alpha', label: 'ML Alpha' },  // Chapter 14
```

- [ ] **Step 2: 编写 ML Alpha 章节内容**

内容应覆盖:
1. **框架概述**: MLAlpha 是 CrossSectionalFactor 的子类, 内置 walk-forward 训练
2. **支持的模型**: V1 白名单 (7 sklearn) + V2.14 扩展 (LightGBM/XGBoost)
3. **创建 ML Alpha**: CodeEditor → "+ ML Alpha" → 编辑模板 → 保存
4. **关键参数**: model_factory, feature_fn, target_fn, train_window, retrain_freq, purge_days, feature_warmup_days
5. **诊断工具**: 组合回测 → 选股因子研究 → ML 诊断面板 → verdict/IC/importance
6. **安全限制**: n_jobs=1, 无 GPU, 白名单限制
7. **模板示例**: 展示标准 Ridge 模板

### Task Docs.4: 更新 CLAUDE.md 和 web/CLAUDE.md

- [ ] **Step 1: CLAUDE.md V2.14 进度更新**

在 `CLAUDE.md` 的 "Current Version Progress" 末尾追加 V2.14 条目.

- [ ] **Step 2: web/CLAUDE.md 更新**

更新组件表和 Key Changes 部分.

- [ ] **Step 3: Commit**

```bash
git add web/src/pages/DocsPage.tsx web/CLAUDE.md CLAUDE.md
git commit -m "docs(V2.14): update DocsPage + CLAUDE.md for B1-B4 features"
```

---

## Code Review 检查清单

每个功能完成后, 通过 `superpowers:requesting-code-review` 做代码审查. 审查重点:

### B1 审查项
- [ ] `ParamRangeRequest.values` 类型放宽后, 后端 grid/random search 是否正确处理 bool/str
- [ ] 前端 discriminated union 类型是否覆盖所有分支
- [ ] bool 参数至少选 1 个的校验
- [ ] 原有数值搜索的回归

### B2 审查项
- [ ] power-set 生成是否从 1 开始 (跳过空集)
- [ ] 64 cap 的计算: `2^N - 1` (排除空集) 还是 `2^N`
- [ ] comboSearch=false 时原有 `|` 逻辑不受影响
- [ ] 子集在后端是作为 `list[str]` 传递还是 join 后的 `str`

### B3 审查项
- [ ] `_create_strategy` 递归调用是否传 skip_ensure (避免重复 fundamental 数据获取)
- [ ] sub_strategies 列表格式: 同名子策略 (两个 TopNRotation 不同参数) 通过列表索引区分, 不再有 key 冲突
- [ ] StrategyEnsemble 的 deepcopy 是否和 walk-forward factory 兼容 (ensemble 内部已 deepcopy 子策略, WF factory 每折 fresh ensemble 即可)
- [ ] EnsembleBuilder onChange 频繁触发是否导致性能问题 (useEffect deps 可能过于宽泛)
- [ ] 前端 EnsembleBuilder key 用 idx 而非 name, 移除时 React reconciliation 是否正确

### B4 审查项
- [ ] lightgbm/xgboost 的 importorskip 是否正确 (安装了但跳过 = bug)
- [ ] `_SUPPORTED_ESTIMATOR_CACHE` 在添加新库后是否需要 invalidate
- [ ] XGBoost GPU tree_method 拦截是否覆盖 `cuda` 和 `gpu_hist`
- [ ] LightGBM `verbose=-1` 是否在所有版本有效
- [ ] CI `ml-extra` group 是否只在 full matrix (tag trigger) 安装

---

## 实施顺序总结

```
B1.1 → B1.2 → B1.3 → B1.4 → B2.1 → B2.2 → B2.3
                                                    → code review (搜索增强)
B4.1 → B4.2 → B4.3 → B4.4
                        → code review (ML 扩展)
B3.1 → B3.2 → B3.3 → B3.4 → B3.5
                               → code review (Ensemble UI)
Docs.1 → Docs.2 → Docs.3 → Docs.4
                               → 最终 code review (全量)
```

每组完成后 retag:
- B1+B2 完成: `v0.2.14-alpha` (搜索增强)
- B4 完成: `v0.2.14-beta` (ML 扩展)
- B3 完成: `v0.2.14-rc` (Ensemble UI)
- Docs 完成 + 全量 review 通过: `v0.2.14` (正式版)
