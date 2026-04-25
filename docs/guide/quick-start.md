# 快速上手

本文帮助你在 5 分钟内完成第一次策略回测，体验 OpenTrading 的核心工作流。

**前提**：已按照 [安装指南](installation.md) 完成安装，浏览器已打开 `http://localhost:8000`。

---

## 第一步：编写策略

1. 在左侧导航栏点击 **代码编辑器**
2. 点击 **新建策略**，输入策略名称（例如 `my_momentum`）
3. 在编辑器中编写策略代码，或点击 **AI 助手** 按钮描述你的策略需求，让 AI 自动生成

<!-- 截图占位: 代码编辑器界面 — 新建策略对话框 -->

下面是一个简单的动量策略示例，可直接复制使用：

```python
from ez.strategy.base import Strategy

class MomentumStrategy(Strategy):
    """简单动量策略：价格突破 N 日高点时买入，跌破 M 日低点时卖出。"""

    def initialize(self):
        # 声明策略参数（支持在回测界面覆盖）
        self.lookback_high = self.params.get("lookback_high", 20)
        self.lookback_low = self.params.get("lookback_low", 10)

    def on_bar(self, data):
        close = data["adj_close"]
        high = data["high"]
        low = data["low"]

        # 数据不足时跳过
        if len(close) < self.lookback_high:
            return

        recent_high = high[-self.lookback_high:].max()
        recent_low = low[-self.lookback_low:].min()

        # 突破 N 日高点 → 买入
        if close[-1] > recent_high and not self.position:
            self.buy()

        # 跌破 M 日低点 → 卖出
        elif close[-1] < recent_low and self.position:
            self.sell()
```

编写完成后点击右上角 **保存** 按钮（快捷键 `Ctrl/Cmd + S`）。系统会自动对代码进行安全检查（防止前视偏差、NaN/Inf 等问题），检查通过后才能保存。

<!-- 截图占位: 代码编辑器界面 — 策略代码已保存状态 -->

---

## 第二步：运行回测

1. 在左侧导航栏点击 **回测**
2. 在策略下拉框中选择刚才保存的策略（`my_momentum`）
3. 输入股票代码，例如 `000001.SZ`（平安银行）
4. 设置回测区间，例如 **2020-01-01** 至 **2024-12-31**
5. 点击 **运行回测**

<!-- 截图占位: 回测配置面板 — 策略选择、股票代码、日期区间设置 -->

> **提示**：A 股代码格式为 `xxxxxx.SZ`（深交所）或 `xxxxxx.SH`（上交所）。

系统会自动应用 A 股完整规则进行模拟：
- **T+1**：当日买入的仓位次日才能卖出
- **涨跌停**：涨停时无法买入，跌停时无法卖出
- **印花税**：卖出时按 0.1% 收取
- **整手**：买入数量向下取整到 100 股的整数倍

---

## 第三步：查看结果

回测完成后，结果页面包含以下内容：

<!-- 截图占位: 回测结果页 — K线图与买卖信号 -->

**K 线图（买卖信号）**

在日线 K 线图上叠加显示买入（绿色向上箭头）和卖出（红色向下箭头）信号，直观观察策略的入场和出场时机。

<!-- 截图占位: 回测结果页 — 收益曲线对比基准 -->

**收益曲线**

策略净值曲线与基准（默认沪深 300）的对比折线图，可清楚看到超额收益的来源区间。

**绩效指标**

| 指标 | 说明 |
|---|---|
| 年化收益率 | 策略年化复利收益 |
| Sharpe 比率 | 风险调整后收益，越高越好 |
| Sortino 比率 | 仅考虑下行风险的风险调整收益 |
| 最大回撤 | 净值从峰值到谷值的最大跌幅 |
| 胜率 | 盈利交易占总交易次数的比例 |
| 盈亏比 | 平均盈利 / 平均亏损 |
| Alpha / Beta | 相对于基准的超额收益和系统性风险 |

<!-- 截图占位: 回测结果页 — 绩效指标汇总表格 -->

---

## 下一步

- 了解平台所有功能模块：[功能总览](features.md)
- 遇到问题？查看：[常见问题](faq.md)
- 深入学习 A 股规则与高级用法：[系统架构文档](../architecture/system-architecture.md)
