# P2-P4: 模块子目录重组设计

> 日期: 2026-04-24
> 状态: 历史设计，模块重组已实施

## 目标

将三个大模块的内聚子组提取为子目录，提升导航清晰度。

## 方案

每个模块只提取**最自然的一个子目录**，其余保持平铺。使用 `__init__.py` re-export 保证零外部 import 断裂。

| 模块 | 新子目录 | 移入文件 | 外部 import 处理 |
|------|---------|---------|-----------------|
| `ez/portfolio/` | `ml/` | `ml_alpha.py` → `ml/alpha.py`, `ml_diagnostics.py` → `ml/diagnostics.py` | 更新全部~20条外部 import（量小可控） |
| `ez/agent/` | `research/` | 7 个 research_* 文件 | `ml/` 验证通过后再执行 |
| `ez/live/` | `qmt/` | 6 个 qmt_* 文件 | 同上 |

## P2: ez/portfolio/ml/

### 移动映射

| 原路径 | 新路径 |
|-------|--------|
| `ez/portfolio/ml_alpha.py` | `ez/portfolio/ml/alpha.py` |
| `ez/portfolio/ml_diagnostics.py` | `ez/portfolio/ml/diagnostics.py` |
| (新建) | `ez/portfolio/ml/__init__.py` |

### ez/portfolio/ml/__init__.py

```python
"""Machine learning alpha 因子框架。"""
from ez.portfolio.ml.alpha import (
    MLAlpha,
    ML_ALPHA_TEMPLATE,
    UnsupportedEstimatorError,
)
from ez.portfolio.ml.diagnostics import (
    MLDiagnostics,
    DiagnosticsResult,
    DiagnosticsConfig,
)

__all__ = [
    "MLAlpha", "ML_ALPHA_TEMPLATE", "UnsupportedEstimatorError",
    "MLDiagnostics", "DiagnosticsResult", "DiagnosticsConfig",
]
```

### 内部 import 更新

`ml/diagnostics.py` 中：
- `from ez.portfolio.ml_alpha import MLAlpha` → `from ez.portfolio.ml.alpha import MLAlpha`

`ml/alpha.py` 中自引用（doctest 或 template 字符串）：
- `from ez.portfolio.ml_alpha import MLAlpha` → `from ez.portfolio.ml.alpha import MLAlpha`

### ez/portfolio/__init__.py 更新

```python
from ez.portfolio.ml import MLAlpha, ML_ALPHA_TEMPLATE, UnsupportedEstimatorError
from ez.portfolio.ml import MLDiagnostics, DiagnosticsResult, DiagnosticsConfig
```

### 外部 import 更新（~20 处）

| 文件 | 旧 import | 新 import |
|------|----------|----------|
| `ez/agent/sandbox.py` | `from ez.portfolio.ml_alpha import ...` | `from ez.portfolio.ml.alpha import ...` |
| `ez/agent/assistant.py` | 字符串 `"from ez.portfolio.ml_alpha import MLAlpha"` | 更新字符串 |
| `ez/testing/guards/suite.py` | `from ez.portfolio.ml_alpha import MLAlpha` | `from ez.portfolio.ml.alpha import MLAlpha` |
| `ez/api/routes/portfolio.py` | lazy imports of ml_alpha/ml_diagnostics | 更新路径 |
| `tests/test_portfolio/test_ml_diagnostics.py` | ~15 处 lazy imports | 批量更新 |
| `tests/test_portfolio/test_ml_alpha.py` | imports | 批量更新 |

### 不做的事

- 不留 shim 文件（旧路径的兼容文件）
- 不改 ml_alpha.py / ml_diagnostics.py 的内部逻辑
- 不改其他 portfolio/ 文件

### 验收标准

1. `ls ez/portfolio/ml/` → `__init__.py alpha.py diagnostics.py`
2. `ls ez/portfolio/ml_alpha.py` → 不存在
3. `git grep "ez\.portfolio\.ml_alpha\|ez\.portfolio\.ml_diagnostics"` → 零匹配
4. `scripts/run_pytest_safe.sh tests/ -x -q` → 全量通过
