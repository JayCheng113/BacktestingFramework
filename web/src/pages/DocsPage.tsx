import { useState } from 'react'

const sections = [
  { id: 'quickstart', label: '快速开始' },
  { id: 'strategy', label: '策略开发' },
  { id: 'factors', label: '因子参考' },
  { id: 'signals', label: '信号与引擎' },
  { id: 'market-rules', label: 'A股规则' },
  { id: 'experiment', label: '实验流水线' },
  { id: 'data', label: '数据源' },
  { id: 'api', label: 'API 参考' },
  { id: 'ai', label: 'AI 助手' },
  { id: 'examples', label: '完整示例' },
  { id: 'faq', label: '常见问题' },
]

const code: React.CSSProperties = {
  backgroundColor: '#1e293b', padding: '12px 14px', borderRadius: '6px',
  fontSize: '12px', overflowX: 'auto', whiteSpace: 'pre', display: 'block',
  lineHeight: '1.6', fontFamily: 'monospace', margin: '8px 0',
}
const h2s: React.CSSProperties = { color: 'var(--color-accent)', fontSize: '16px', fontWeight: 700, margin: '24px 0 12px', borderBottom: '1px solid var(--border)', paddingBottom: '6px' }
const h3s: React.CSSProperties = { color: '#93c5fd', fontSize: '13px', fontWeight: 600, margin: '16px 0 8px' }
const ps: React.CSSProperties = { margin: '6px 0', lineHeight: '1.7' }
const tds: React.CSSProperties = { padding: '6px 10px', borderBottom: '1px solid var(--border)' }
const ths: React.CSSProperties = { ...tds, fontWeight: 600, textAlign: 'left', backgroundColor: 'var(--bg-secondary)' }
const note: React.CSSProperties = { padding: '10px 14px', margin: '10px 0', borderRadius: '6px', backgroundColor: '#1e293b', borderLeft: '3px solid var(--color-accent)', fontSize: '12px', lineHeight: '1.6' }
const warn: React.CSSProperties = { ...note, borderLeftColor: '#f59e0b', backgroundColor: '#1c1a11' }
const tbl: React.CSSProperties = { width: '100%', borderCollapse: 'collapse', margin: '8px 0' }

export default function DocsPage() {
  const [active, setActive] = useState('quickstart')

  return (
    <div className="flex" style={{ height: 'calc(100vh - 48px)' }}>
      {/* Sidebar nav */}
      <div className="w-48 border-r overflow-y-auto py-4 px-3" style={{ borderColor: 'var(--border)', backgroundColor: 'var(--bg-secondary)' }}>
        <div className="text-xs font-bold mb-3" style={{ color: 'var(--text-secondary)' }}>ez-trading 开发文档</div>
        {sections.map(s => (
          <button key={s.id} onClick={() => setActive(s.id)}
            className="block w-full text-left text-xs px-2 py-1.5 rounded mb-0.5"
            style={{ backgroundColor: active === s.id ? 'var(--color-accent)' : 'transparent', color: active === s.id ? '#fff' : 'var(--text-secondary)' }}>
            {s.label}
          </button>
        ))}
      </div>

      {/* Content */}
      <div className="flex-1 overflow-y-auto p-6 text-sm" style={{ color: 'var(--text-primary)', maxWidth: '900px' }}>

        {/* ================================================================ */}
        {/*  1. 快速开始                                                      */}
        {/* ================================================================ */}
        {active === 'quickstart' && <>
          <h1 style={{ fontSize: '20px', fontWeight: 700, marginBottom: '16px' }}>快速开始</h1>
          <p style={ps}>本文档涵盖 ez-trading 平台的所有功能。按照以下步骤，你可以在 5 分钟内创建并运行第一个交易策略。</p>

          <div style={h2s}>完整工作流程</div>
          <pre style={code}>{`1. 创建策略  ──→  2. 保存并测试  ──→  3. 运行回测  ──→  4. 查看结果
      │                   │                   │                │
  代码编辑器         Contract Test        看板 或 实验面板    权益曲线/指标
  AI 助手            自动校验接口          单次 或 批量       Gate 评分`}</pre>

          <div style={h2s}>第一步：创建策略</div>
          <p style={ps}>有 3 种方式创建策略：</p>

          <div style={h3s}>方式 A：模板生成（推荐新手）</div>
          <p style={ps}>进入 <b>代码编辑器</b> Tab，选择 "策略" → 输入类名（如 <code>RSIReversal</code>） → 点击 "生成模板"。系统会生成一个包含所有必要方法的策略骨架，你只需修改 <code>generate_signals()</code> 的逻辑。</p>

          <div style={h3s}>方式 B：手动编写</div>
          <p style={ps}>在代码编辑器中直接编写完整策略代码。需要继承 <code>Strategy</code> 基类，并实现 3 个方法。详见 "策略开发" 章节。</p>

          <div style={h3s}>方式 C：AI 助手</div>
          <p style={ps}>在代码编辑器中点击 "AI助手" 按钮，用自然语言描述策略，AI 会自动生成完整代码到编辑器中。</p>
          <pre style={code}>{`示例提示词:
"帮我写一个 RSI 超卖反转策略，RSI < 30 买入，> 70 卖出"
"写一个双均线交叉策略，5日线和20日线"
"把 MA 周期改成参数化的，默认20，范围5到60"`}</pre>

          <div style={h2s}>第二步：保存并测试</div>
          <p style={ps}>点击 "保存并测试" 按钮，系统会执行以下操作：</p>
          <ol style={{ paddingLeft: '20px', margin: '6px 0', lineHeight: '1.8' }}>
            <li>语法检查：Python 代码是否合法</li>
            <li>安全检查：是否使用了禁止的 import（如 os, subprocess 等）</li>
            <li>Contract Test：验证策略是否符合接口规范（4 项测试）</li>
            <li>测试通过后文件保存到 <code>strategies/</code> 目录</li>
          </ol>
          <div style={note}>Contract Test 检查的 4 项内容：(1) <code>required_factors</code> 返回 Factor 列表 (2) <code>generate_signals</code> 返回 pd.Series (3) 信号值在 0-1 范围内 (4) <code>get_parameters_schema</code> 返回合法 dict</div>

          <div style={h2s}>第三步：运行回测</div>
          <p style={ps}><b>方式 A：快速单次回测</b></p>
          <p style={ps}>切换到 <b>看板</b> Tab → 在回测面板选择策略 → 设置股票代码、日期范围 → 点击 "运行"。</p>

          <p style={ps}><b>方式 B：完整实验（推荐）</b></p>
          <p style={ps}>切换到 <b>实验</b> Tab → 运行完整实验，包含：回测 + 前推验证 + 显著性检验 + Research Gate 自动评分。实验结果会自动保存，可以随时查看和对比。</p>

          <div style={h2s}>常见问题排查</div>
          <div style={h3s}>策略保存失败怎么办？</div>
          <ol style={{ paddingLeft: '20px', margin: '6px 0', lineHeight: '1.8' }}>
            <li>检查是否有 Python 语法错误（编辑器会标红）</li>
            <li>检查是否使用了禁止的 import（如 <code>import os</code>）</li>
            <li>检查类名是否合法（只允许字母、数字、下划线，且不以数字开头）</li>
            <li>检查是否实现了所有必要方法（<code>required_factors</code>, <code>generate_signals</code>）</li>
          </ol>

          <div style={h3s}>回测结果 0 交易怎么办？</div>
          <ol style={{ paddingLeft: '20px', margin: '6px 0', lineHeight: '1.8' }}>
            <li>检查 <code>generate_signals()</code> 是否返回了全 0 信号（没有买入条件触发）</li>
            <li>检查因子列名大小写 — 必须是小写（如 <code>rsi_14</code> 而不是 <code>RSI_14</code>）</li>
            <li>检查因子 warmup 是否吃掉了所有数据（数据太短或 warmup 太长）</li>
            <li>如果开了 A 股规则，检查资金是否不够买 100 股整手</li>
          </ol>
        </>}

        {/* ================================================================ */}
        {/*  2. 策略开发                                                      */}
        {/* ================================================================ */}
        {active === 'strategy' && <>
          <h1 style={{ fontSize: '20px', fontWeight: 700, marginBottom: '16px' }}>策略开发</h1>

          <div style={h2s}>Strategy 基类概览</div>
          <p style={ps}>所有策略必须继承 <code>Strategy</code> 基类。完整接口如下：</p>
          <pre style={code}>{`from ez.strategy import Strategy
from ez.factor import Factor
from ez.factor.builtin.technical import MA, RSI, MACD, BOLL, EMA, Momentum, VWAP, OBV, ATR
import pandas as pd

class MyStrategy(Strategy):
    """策略说明文字"""

    def __init__(self, period: int = 14, threshold: float = 30.0):
        """构造函数 — 接受策略参数"""
        self.period = period
        self.threshold = threshold

    @classmethod
    def get_parameters_schema(cls) -> dict[str, dict]:
        """参数定义 — 用于前端表单自动渲染 + 参数搜索"""
        return { ... }

    @classmethod
    def get_description(cls) -> str:
        """策略描述 — 显示在前端策略选择器下方（可选方法）"""
        return "..."

    def required_factors(self) -> list[Factor]:
        """声明依赖的因子 — 引擎自动计算并注入 data"""
        return [ ... ]

    def generate_signals(self, data: pd.DataFrame) -> pd.Series:
        """核心逻辑 — 返回 0.0~1.0 的仓位信号"""
        return ...`}</pre>

          <div style={h2s}>__init__ 构造函数</div>
          <p style={ps}>构造函数接受策略参数。参数名必须与 <code>get_parameters_schema()</code> 中定义的 key 一致。引擎在实例化策略时，会将用户传入的参数直接传给构造函数。</p>
          <pre style={code}>{`# 前端传入 {"period": 20, "threshold": 25.0}
# 引擎调用: MyStrategy(period=20, threshold=25.0)

def __init__(self, period: int = 14, threshold: float = 30.0):
    self.period = period
    self.threshold = threshold`}</pre>
          <div style={warn}>参数类型必须严格匹配 schema 中定义的 type。如果 schema 定义 type 为 "int"，传入 3.5 会被拒绝。整数参数请确保默认值和范围都是整数。</div>

          <div style={h2s}>get_parameters_schema() 详解</div>
          <p style={ps}>返回字典，每个 key 是参数名，value 是描述对象。前端会根据 schema 自动渲染输入表单，参数搜索也依赖这些信息。</p>
          <table style={tbl}>
            <thead><tr><th style={ths}>字段</th><th style={ths}>类型</th><th style={ths}>必填</th><th style={ths}>说明</th><th style={ths}>示例</th></tr></thead>
            <tbody>
              <tr><td style={tds}><code>type</code></td><td style={tds}>string</td><td style={tds}>是</td><td style={tds}>参数类型，决定前端输入控件和校验规则</td><td style={tds}><code>"int"</code> / <code>"float"</code></td></tr>
              <tr><td style={tds}><code>default</code></td><td style={tds}>number</td><td style={tds}>是</td><td style={tds}>默认值，也是单次回测时的初始值</td><td style={tds}><code>14</code></td></tr>
              <tr><td style={tds}><code>min</code></td><td style={tds}>number</td><td style={tds}>是</td><td style={tds}>最小值，前端校验 + 参数搜索下界</td><td style={tds}><code>5</code></td></tr>
              <tr><td style={tds}><code>max</code></td><td style={tds}>number</td><td style={tds}>是</td><td style={tds}>最大值，前端校验 + 参数搜索上界</td><td style={tds}><code>50</code></td></tr>
              <tr><td style={tds}><code>label</code></td><td style={tds}>string</td><td style={tds}>否</td><td style={tds}>显示名称（中文），不填则显示参数名</td><td style={tds}><code>"RSI 周期"</code></td></tr>
              <tr><td style={tds}><code>step</code></td><td style={tds}>number</td><td style={tds}>否</td><td style={tds}>参数搜索步长（Grid Search 使用），不填则自动推算</td><td style={tds}><code>1</code></td></tr>
            </tbody>
          </table>

          <div style={h3s}>类型强制规则</div>
          <table style={tbl}>
            <thead><tr><th style={ths}>schema type</th><th style={ths}>Python 类型</th><th style={ths}>输入 3.5</th><th style={ths}>输入 3</th></tr></thead>
            <tbody>
              <tr><td style={tds}><code>"int"</code></td><td style={tds}><code>int</code></td><td style={tds}>拒绝 (422 错误)</td><td style={tds}>接受</td></tr>
              <tr><td style={tds}><code>"float"</code></td><td style={tds}><code>float</code></td><td style={tds}>接受</td><td style={tds}>接受 (自动转 3.0)</td></tr>
            </tbody>
          </table>
          <pre style={code}>{`@classmethod
def get_parameters_schema(cls) -> dict:
    return {
        "period":    {"type": "int",   "default": 14,   "min": 5,  "max": 50,  "label": "RSI 周期"},
        "threshold": {"type": "float", "default": 30.0, "min": 10, "max": 45,  "label": "超卖阈值"},
    }`}</pre>

          <div style={h2s}>get_description() 可选方法</div>
          <p style={ps}>不是抽象方法，无需强制实现。如果实现了，会在前端策略选择器下方显示描述文字。</p>
          <pre style={code}>{`@classmethod
def get_description(cls) -> str:
    return "RSI 超卖反转策略：RSI < 30 买入，> 70 卖出"`}</pre>

          <div style={h2s}>required_factors() 详解</div>
          <p style={ps}>返回因子实例列表。引擎在调用 <code>generate_signals()</code> 前，会按顺序对每个因子执行 <code>compute(data)</code>，将计算结果列追加到 <code>data</code> DataFrame 中。</p>
          <pre style={code}>{`def required_factors(self) -> list[Factor]:
    return [
        RSI(period=self.period),      # 追加列: rsi_14
        MA(period=20),                # 追加列: ma_20
    ]
# generate_signals 收到的 data 已包含 rsi_14, ma_20 列`}</pre>

          <div style={h3s}>Warmup 周期</div>
          <p style={ps}>每个因子都有 <code>warmup_period</code> 属性，表示需要多少根 K 线才能产生有效值。引擎会取所有因子中最大的 warmup 值，并在模拟前裁剪掉这些行。</p>
          <table style={tbl}>
            <thead><tr><th style={ths}>因子</th><th style={ths}>warmup_period</th><th style={ths}>说明</th></tr></thead>
            <tbody>
              <tr><td style={tds}>MA(20)</td><td style={tds}>20</td><td style={tds}>需要 20 根 K 线计算均值</td></tr>
              <tr><td style={tds}>RSI(14)</td><td style={tds}>15</td><td style={tds}>period + 1（需要 diff 操作）</td></tr>
              <tr><td style={tds}>MACD(12,26,9)</td><td style={tds}>35</td><td style={tds}>slow + signal = 26 + 9</td></tr>
              <tr><td style={tds}>ATR(14)</td><td style={tds}>15</td><td style={tds}>period + 1（需要 shift 操作）</td></tr>
            </tbody>
          </table>
          <div style={note}>如果你的数据只有 100 根 K 线，但因子 warmup 需要 35 根，那么实际可交易的只有 65 根。如果 warmup 超过数据总长度，引擎会返回一个空结果（0 交易）。</div>

          <div style={h2s}>generate_signals() 详解</div>

          <div style={h3s}>输入：data DataFrame 的列</div>
          <table style={tbl}>
            <thead><tr><th style={ths}>列名</th><th style={ths}>类型</th><th style={ths}>说明</th></tr></thead>
            <tbody>
              <tr><td style={{...tds, fontFamily:'monospace'}}>open</td><td style={tds}>float</td><td style={tds}>开盘价</td></tr>
              <tr><td style={{...tds, fontFamily:'monospace'}}>high</td><td style={tds}>float</td><td style={tds}>最高价</td></tr>
              <tr><td style={{...tds, fontFamily:'monospace'}}>low</td><td style={tds}>float</td><td style={tds}>最低价</td></tr>
              <tr><td style={{...tds, fontFamily:'monospace'}}>close</td><td style={tds}>float</td><td style={tds}>收盘价（未复权原始价格）</td></tr>
              <tr><td style={{...tds, fontFamily:'monospace'}}>adj_close</td><td style={tds}>float</td><td style={tds}>前复权收盘价（推荐用于信号计算）</td></tr>
              <tr><td style={{...tds, fontFamily:'monospace'}}>volume</td><td style={tds}>float</td><td style={tds}>成交量（股数）</td></tr>
              <tr><td style={{...tds, fontFamily:'monospace'}}>rsi_14, ma_20, ...</td><td style={tds}>float</td><td style={tds}>由 required_factors() 自动追加的因子列</td></tr>
            </tbody>
          </table>

          <div style={h3s}>输出格式</div>
          <p style={ps}>返回 <code>pd.Series</code>，长度与 data 一致，索引与 data 一致，值域 [0.0, 1.0]：</p>
          <table style={tbl}>
            <thead><tr><th style={ths}>信号值</th><th style={ths}>含义</th><th style={ths}>引擎行为</th></tr></thead>
            <tbody>
              <tr><td style={tds}><code>0.0</code></td><td style={tds}>空仓</td><td style={tds}>卖出全部持仓（如有）</td></tr>
              <tr><td style={tds}><code>1.0</code></td><td style={tds}>满仓</td><td style={tds}>用全部可用资金买入</td></tr>
              <tr><td style={tds}><code>0.5</code></td><td style={tds}>半仓</td><td style={tds}>调整仓位到总权益的 50%</td></tr>
              <tr><td style={tds}><code>0.3</code></td><td style={tds}>三成仓</td><td style={tds}>调整仓位到总权益的 30%</td></tr>
              <tr><td style={tds}><code>NaN</code></td><td style={tds}>无信号</td><td style={tds}>fillna(0) 后视为空仓</td></tr>
            </tbody>
          </table>

          <div style={h3s}>常见信号生成模式</div>
          <p style={ps}><b>模式 1：布尔条件（最简单）</b></p>
          <pre style={code}>{`# RSI < 30 → 满仓，否则空仓
return (data[f"rsi_{self.period}"] < 30).astype(float)`}</pre>

          <p style={ps}><b>模式 2：持仓保持（forward-fill）</b></p>
          <pre style={code}>{`# 条件触发后持续持仓，直到反向条件
signal = pd.Series(0.0, index=data.index)
signal[data["rsi_14"] < 30] = 1.0    # 买入条件
signal = signal.replace(0.0, pd.NA).ffill().fillna(0.0)
signal[data["rsi_14"] > 70] = 0.0    # 卖出条件
return signal`}</pre>

          <p style={ps}><b>模式 3：动态仓位</b></p>
          <pre style={code}>{`# 用 momentum 值作为仓位权重
mom = data[f"momentum_{self.period}"]
# 归一化到 0-1 范围
signal = (mom - mom.min()) / (mom.max() - mom.min())
return signal.clip(0.0, 1.0).fillna(0.0)`}</pre>

          <p style={ps}><b>模式 4：有状态循环（复杂逻辑）</b></p>
          <pre style={code}>{`# 逐 bar 循环，支持 entry_price 记忆等复杂逻辑
signal = pd.Series(0.0, index=data.index)
entry_price = 0.0
for i in range(len(data)):
    p = data["adj_close"].iloc[i]
    if signal.iloc[i-1] if i > 0 else 0 == 0:
        if p < data["boll_lower_20"].iloc[i]:  # 入场条件
            signal.iloc[i] = 1.0
            entry_price = p
    else:
        if p >= entry_price * 1.05:  # 止盈 5%
            signal.iloc[i] = 0.0
        else:
            signal.iloc[i] = 1.0
return signal`}</pre>

          <div style={h2s}>自动注册机制</div>
          <p style={ps}>Strategy 基类使用 <code>__init_subclass__</code> 钩子实现自动注册。只要你的类继承了 Strategy 并且不是抽象类，它就会自动注册到全局策略注册表中，无需手动注册。</p>
          <pre style={code}>{`# 这是自动发生的，不需要手动调用
class MyStrategy(Strategy):  # 继承即注册
    ...

# 注册 key = "模块路径.类名"
# 如: "strategies.my_strategy.MyStrategy"`}</pre>

          <div style={h2s}>文件位置与热重载</div>
          <p style={ps}>策略文件放在 <code>strategies/</code> 目录下。系统启动时自动扫描该目录，import 所有 <code>.py</code> 文件。通过代码编辑器保存的策略文件也放在这里。保存后无需重启后端，策略会自动重新加载。</p>

          <div style={h2s}>Contract Test 检查项</div>
          <table style={tbl}>
            <thead><tr><th style={ths}>测试</th><th style={ths}>检查内容</th><th style={ths}>常见失败原因</th></tr></thead>
            <tbody>
              <tr><td style={tds}>has_required_factors</td><td style={tds}>required_factors() 返回 Factor 实例列表</td><td style={tds}>返回了空列表或非 Factor 对象</td></tr>
              <tr><td style={tds}>generate_signals_returns_series</td><td style={tds}>generate_signals() 返回 pd.Series</td><td style={tds}>返回了 list 或 numpy array</td></tr>
              <tr><td style={tds}>signals_in_valid_range</td><td style={tds}>信号值全部在 [0.0, 1.0]</td><td style={tds}>使用了 -1/+1 信号范围</td></tr>
              <tr><td style={tds}>parameters_schema_valid</td><td style={tds}>get_parameters_schema() 返回合法 dict</td><td style={tds}>缺少 type/default/min/max 字段</td></tr>
            </tbody>
          </table>

          <div style={h2s}>常见坑点</div>
          <div style={warn}>
            <p><b>1. 因子列名必须小写</b> — <code>data["rsi_14"]</code> 而不是 <code>data["RSI_14"]</code>。所有因子列名由因子类的 <code>name</code> 属性决定，全部是小写加下划线。</p>
            <p style={{ marginTop: '6px' }}><b>2. 别忘了 import pandas</b> — <code>import pandas as pd</code> 是必须的，generate_signals 的返回类型是 <code>pd.Series</code>。</p>
            <p style={{ marginTop: '6px' }}><b>3. 信号范围必须是 0-1</b> — 不支持 -1 (做空) 信号。如果返回了超出 [0, 1] 的值，引擎会 clip 到这个范围。</p>
            <p style={{ marginTop: '6px' }}><b>4. 用 adj_close 而非 close</b> — <code>adj_close</code> 是前复权价格，去除了除权除息的影响，适合做技术分析。<code>close</code> 是原始价格，仅用于 A 股涨跌停判定。</p>
            <p style={{ marginTop: '6px' }}><b>5. BOLL 列名注意</b> — BOLL(period=20) 生成的列名是 <code>boll_mid_20</code>, <code>boll_upper_20</code>, <code>boll_lower_20</code>（含 period 后缀），不是 <code>boll_middle</code> 或 <code>boll_lower</code>。</p>
            <p style={{ marginTop: '6px' }}><b>6. MACD 列名固定</b> — MACD 生成 <code>macd_line</code>, <code>macd_signal</code>, <code>macd_hist</code>，不含参数后缀。</p>
          </div>
        </>}

        {/* ================================================================ */}
        {/*  3. 因子参考                                                      */}
        {/* ================================================================ */}
        {active === 'factors' && <>
          <h1 style={{ fontSize: '20px', fontWeight: 700, marginBottom: '16px' }}>因子参考</h1>
          <p style={ps}>所有因子在 <code>ez.factor.builtin.technical</code> 中。通过策略的 <code>required_factors()</code> 声明后，引擎自动计算并注入 data DataFrame。</p>

          {/* MA */}
          <div style={h2s}>MA — 简单移动平均线 (Simple Moving Average)</div>
          <p style={ps}>计算过去 N 根 K 线的收盘价（adj_close）算术平均值。是最基础的趋势跟踪指标。当短期均线在长期均线上方时视为多头趋势。常用于金叉/死叉策略、趋势判断、支撑/阻力位识别。</p>
          <table style={tbl}>
            <thead><tr><th style={ths}>属性</th><th style={ths}>值</th></tr></thead>
            <tbody>
              <tr><td style={{...tds, width:'140px', fontWeight: 600}}>构造参数</td><td style={{...tds, fontFamily: 'monospace'}}>period: int = 20</td></tr>
              <tr><td style={{...tds, fontWeight: 600}}>输出列名</td><td style={{...tds, fontFamily: 'monospace'}}>{`ma_{period}`}  (如 ma_5, ma_20, ma_60)</td></tr>
              <tr><td style={{...tds, fontWeight: 600}}>warmup</td><td style={{...tds, fontFamily: 'monospace'}}>period (如 period=20 则 warmup=20)</td></tr>
              <tr><td style={{...tds, fontWeight: 600}}>常用参数</td><td style={tds}>短线: 5, 10 | 中线: 20, 30 | 长线: 60, 120, 250</td></tr>
            </tbody>
          </table>
          <pre style={code}>{`MA(period=5), MA(period=20)  # 金叉: data["ma_5"] > data["ma_20"]`}</pre>
          <div style={note}>策略思路：双均线交叉 — 5 日线上穿 20 日线买入，下穿卖出。多均线排列 — MA5 {'>'} MA10 {'>'} MA20 为强势。</div>

          {/* EMA */}
          <div style={h2s}>EMA — 指数移动平均 (Exponential Moving Average)</div>
          <p style={ps}>对近期数据赋予更高权重的移动平均。比 MA 对价格变化的反应更灵敏。计算方式为 EMA = alpha * price + (1 - alpha) * prev_EMA，其中 alpha = 2 / (period + 1)。常用于构建 MACD 指标或替代 MA 做趋势跟踪。</p>
          <table style={tbl}>
            <thead><tr><th style={ths}>属性</th><th style={ths}>值</th></tr></thead>
            <tbody>
              <tr><td style={{...tds, width:'140px', fontWeight: 600}}>构造参数</td><td style={{...tds, fontFamily: 'monospace'}}>period: int = 12</td></tr>
              <tr><td style={{...tds, fontWeight: 600}}>输出列名</td><td style={{...tds, fontFamily: 'monospace'}}>{`ema_{period}`}  (如 ema_12, ema_26)</td></tr>
              <tr><td style={{...tds, fontWeight: 600}}>warmup</td><td style={{...tds, fontFamily: 'monospace'}}>period</td></tr>
              <tr><td style={{...tds, fontWeight: 600}}>常用参数</td><td style={tds}>12 (快线), 26 (慢线), 9 (信号线)</td></tr>
            </tbody>
          </table>
          <pre style={code}>{`EMA(period=12), EMA(period=26)  # data["ema_12"] > data["ema_26"] → 看多`}</pre>
          <div style={note}>策略思路：EMA 交叉与 MA 交叉类似，但信号更灵敏。也可直接用 MACD 因子（内部用 EMA 计算）。</div>

          {/* RSI */}
          <div style={h2s}>RSI — 相对强弱指标 (Relative Strength Index)</div>
          <p style={ps}>衡量一段时间内涨幅与跌幅的相对强度，输出 0-100 范围的震荡指标。RSI {'<'} 30 表示超卖（可能反弹），RSI {'>'} 70 表示超买（可能回调）。是最常用的反转信号指标。</p>
          <table style={tbl}>
            <thead><tr><th style={ths}>属性</th><th style={ths}>值</th></tr></thead>
            <tbody>
              <tr><td style={{...tds, width:'140px', fontWeight: 600}}>构造参数</td><td style={{...tds, fontFamily: 'monospace'}}>period: int = 14</td></tr>
              <tr><td style={{...tds, fontWeight: 600}}>输出列名</td><td style={{...tds, fontFamily: 'monospace'}}>{`rsi_{period}`}  (如 rsi_14, rsi_6)</td></tr>
              <tr><td style={{...tds, fontWeight: 600}}>warmup</td><td style={{...tds, fontFamily: 'monospace'}}>period + 1 (需要 diff 操作)</td></tr>
              <tr><td style={{...tds, fontWeight: 600}}>常用参数</td><td style={tds}>6 (短线激进), 14 (标准), 21 (保守)</td></tr>
            </tbody>
          </table>
          <pre style={code}>{`RSI(period=14)  # data["rsi_14"] < 30 → 超卖信号`}</pre>
          <div style={note}>策略思路：超卖反转 — RSI {'<'} 30 买入，{'>'} 70 卖出。配合趋势过滤 — 只在 MA 多头排列时做 RSI 反转。</div>

          {/* MACD */}
          <div style={h2s}>MACD — 指数平滑异同移动平均线</div>
          <p style={ps}>由快速 EMA 与慢速 EMA 的差值构成。MACD 线上穿信号线（金叉）表示多头动能增强。柱状图 (histogram) 反映动能的变化速度。兼具趋势跟踪和动量特性，是最流行的技术指标之一。</p>
          <table style={tbl}>
            <thead><tr><th style={ths}>属性</th><th style={ths}>值</th></tr></thead>
            <tbody>
              <tr><td style={{...tds, width:'140px', fontWeight: 600}}>构造参数</td><td style={{...tds, fontFamily: 'monospace'}}>fast: int = 12, slow: int = 26, signal: int = 9</td></tr>
              <tr><td style={{...tds, fontWeight: 600}}>输出列名</td><td style={{...tds, fontFamily: 'monospace'}}>macd_line, macd_signal, macd_hist</td></tr>
              <tr><td style={{...tds, fontWeight: 600}}>warmup</td><td style={{...tds, fontFamily: 'monospace'}}>slow + signal (默认 26 + 9 = 35)</td></tr>
              <tr><td style={{...tds, fontWeight: 600}}>常用参数</td><td style={tds}>标准: (12,26,9) | 快速: (6,13,5) | 慢速: (19,39,9)</td></tr>
            </tbody>
          </table>
          <pre style={code}>{`MACD()  # 金叉: data["macd_line"] > data["macd_signal"]`}</pre>
          <div style={note}>策略思路：金叉买入 — macd_line 上穿 macd_signal。零轴上金叉 — macd_line {'>'} 0 且金叉更可靠。柱状图缩短 — 趋势减弱信号。</div>

          {/* BOLL */}
          <div style={h2s}>BOLL — 布林带 (Bollinger Bands)</div>
          <p style={ps}>基于统计学标准差构建的价格通道。中轨是移动平均线，上下轨为中轨加减 N 倍标准差。约 95% 的价格会落在 2 倍标准差通道内。价格触及下轨可能超卖，触及上轨可能超买。</p>
          <table style={tbl}>
            <thead><tr><th style={ths}>属性</th><th style={ths}>值</th></tr></thead>
            <tbody>
              <tr><td style={{...tds, width:'140px', fontWeight: 600}}>构造参数</td><td style={{...tds, fontFamily: 'monospace'}}>period: int = 20, std_dev: float = 2.0</td></tr>
              <tr><td style={{...tds, fontWeight: 600}}>输出列名</td><td style={{...tds, fontFamily: 'monospace'}}>{`boll_mid_{period}, boll_upper_{period}, boll_lower_{period}`}</td></tr>
              <tr><td style={{...tds, fontWeight: 600}}>warmup</td><td style={{...tds, fontFamily: 'monospace'}}>period (默认 20)</td></tr>
              <tr><td style={{...tds, fontWeight: 600}}>常用参数</td><td style={tds}>标准: (20, 2.0) | 收窄: (20, 1.5) | 宽松: (20, 2.5)</td></tr>
            </tbody>
          </table>
          <pre style={code}>{`BOLL(period=20, std_dev=2.0)
# 触及下轨: data["adj_close"] < data["boll_lower_20"]`}</pre>
          <div style={warn}>注意列名格式：<code>boll_mid_20</code>, <code>boll_upper_20</code>, <code>boll_lower_20</code>（含 period 后缀），不是 <code>boll_middle</code>。</div>
          <div style={note}>策略思路：回归均值 — 跌破下轨买入，涨到中轨止盈。突破策略 — 价格突破上轨追多（趋势延续）。配合 ATR 做止损。</div>

          {/* Momentum */}
          <div style={h2s}>Momentum — 动量 (N-Day Return)</div>
          <p style={ps}>计算 N 日的收益率（百分比变化）。正值表示价格上涨趋势，负值表示下跌趋势。可以用动量值的大小作为仓位权重，实现动态仓位管理。</p>
          <table style={tbl}>
            <thead><tr><th style={ths}>属性</th><th style={ths}>值</th></tr></thead>
            <tbody>
              <tr><td style={{...tds, width:'140px', fontWeight: 600}}>构造参数</td><td style={{...tds, fontFamily: 'monospace'}}>period: int = 20</td></tr>
              <tr><td style={{...tds, fontWeight: 600}}>输出列名</td><td style={{...tds, fontFamily: 'monospace'}}>{`momentum_{period}`}  (如 momentum_20)</td></tr>
              <tr><td style={{...tds, fontWeight: 600}}>warmup</td><td style={{...tds, fontFamily: 'monospace'}}>period</td></tr>
              <tr><td style={{...tds, fontWeight: 600}}>常用参数</td><td style={tds}>短期: 5, 10 | 中期: 20 | 长期: 60, 120</td></tr>
            </tbody>
          </table>
          <pre style={code}>{`Momentum(period=20)  # data["momentum_20"] > 0 → 上涨趋势`}</pre>
          <div style={note}>策略思路：趋势跟踪 — 动量 {'>'} 0 买入，{'<'} 0 卖出。动态仓位 — 用归一化动量值作为信号权重。</div>

          {/* VWAP */}
          <div style={h2s}>VWAP — 成交量加权平均价 (Volume Weighted Average Price)</div>
          <p style={ps}>按成交量加权的滚动平均价格。公式为 sum(典型价格 * 成交量) / sum(成交量)，其中典型价格 = (high + low + adj_close) / 3。价格在 VWAP 上方说明买方力量占优。</p>
          <table style={tbl}>
            <thead><tr><th style={ths}>属性</th><th style={ths}>值</th></tr></thead>
            <tbody>
              <tr><td style={{...tds, width:'140px', fontWeight: 600}}>构造参数</td><td style={{...tds, fontFamily: 'monospace'}}>period: int = 20</td></tr>
              <tr><td style={{...tds, fontWeight: 600}}>输出列名</td><td style={{...tds, fontFamily: 'monospace'}}>{`vwap_{period}`}  (如 vwap_20)</td></tr>
              <tr><td style={{...tds, fontWeight: 600}}>warmup</td><td style={{...tds, fontFamily: 'monospace'}}>period</td></tr>
              <tr><td style={{...tds, fontWeight: 600}}>常用参数</td><td style={tds}>20 (标准), 10 (短线), 60 (长线)</td></tr>
            </tbody>
          </table>
          <pre style={code}>{`VWAP(period=20)  # data["adj_close"] > data["vwap_20"] → 强势`}</pre>
          <div style={note}>策略思路：趋势确认 — 价格在 VWAP 上方且 VWAP 向上 = 确认多头。配合其他指标 — RSI 超卖 + 价格在 VWAP 附近 = 买入良机。</div>

          {/* OBV */}
          <div style={h2s}>OBV — 能量潮 (On Balance Volume)</div>
          <p style={ps}>累计量能指标。价格上涨日加当日成交量，下跌日减当日成交量。OBV 的趋势方向比价格趋势更早反映供需变化。OBV 与价格方向不一致时为"背离"信号。</p>
          <table style={tbl}>
            <thead><tr><th style={ths}>属性</th><th style={ths}>值</th></tr></thead>
            <tbody>
              <tr><td style={{...tds, width:'140px', fontWeight: 600}}>构造参数</td><td style={{...tds, fontFamily: 'monospace'}}>（无参数）</td></tr>
              <tr><td style={{...tds, fontWeight: 600}}>输出列名</td><td style={{...tds, fontFamily: 'monospace'}}>obv</td></tr>
              <tr><td style={{...tds, fontWeight: 600}}>warmup</td><td style={{...tds, fontFamily: 'monospace'}}>1</td></tr>
              <tr><td style={{...tds, fontWeight: 600}}>常用参数</td><td style={tds}>无参数，直接使用</td></tr>
            </tbody>
          </table>
          <pre style={code}>{`OBV()  # 观察 data["obv"] 趋势与价格趋势是否一致`}</pre>
          <div style={note}>策略思路：背离检测 — 价格创新高但 OBV 未创新高，可能顶部。OBV 趋势方向 — 用 OBV 的 MA 交叉来判断量能趋势。</div>

          {/* ATR */}
          <div style={h2s}>ATR — 平均真实波幅 (Average True Range)</div>
          <p style={ps}>衡量市场波动性的指标。真实波幅 = max(最高价-最低价, |最高价-昨收|, |最低价-昨收|)，ATR 是真实波幅的滚动平均。常用于动态止损（如 2 倍 ATR 止损）和仓位管理。</p>
          <table style={tbl}>
            <thead><tr><th style={ths}>属性</th><th style={ths}>值</th></tr></thead>
            <tbody>
              <tr><td style={{...tds, width:'140px', fontWeight: 600}}>构造参数</td><td style={{...tds, fontFamily: 'monospace'}}>period: int = 14</td></tr>
              <tr><td style={{...tds, fontWeight: 600}}>输出列名</td><td style={{...tds, fontFamily: 'monospace'}}>{`atr_{period}`}  (如 atr_14)</td></tr>
              <tr><td style={{...tds, fontWeight: 600}}>warmup</td><td style={{...tds, fontFamily: 'monospace'}}>period + 1 (需要 shift 操作)</td></tr>
              <tr><td style={{...tds, fontWeight: 600}}>常用参数</td><td style={tds}>14 (标准), 7 (短线), 21 (长线)</td></tr>
            </tbody>
          </table>
          <pre style={code}>{`ATR(period=14)  # 止损价 = 买入价 - 2 * data["atr_14"]`}</pre>
          <div style={note}>策略思路：动态止损 — 买入后设 2-3 倍 ATR 止损，波动大时止损放宽。仓位管理 — ATR 大时减小仓位（风险平价）。</div>

          <div style={h2s}>如何组合多个因子</div>
          <p style={ps}>在 <code>required_factors()</code> 中返回多个因子，它们的计算结果列会全部注入 data。</p>
          <pre style={code}>{`def required_factors(self) -> list[Factor]:
    return [
        RSI(period=14),       # → rsi_14
        MACD(),               # → macd_line, macd_signal, macd_hist
        ATR(period=14),       # → atr_14
    ]

def generate_signals(self, data: pd.DataFrame) -> pd.Series:
    rsi_buy = data["rsi_14"] < 30
    macd_buy = data["macd_line"] > data["macd_signal"]
    # 两个条件同时满足才买入
    return (rsi_buy & macd_buy).astype(float)`}</pre>

          <div style={h2s}>Warmup 对交易天数的影响</div>
          <p style={ps}>引擎取所有因子中最大的 warmup 值，裁剪掉前 N 根 K 线。如果数据总天数不够，会导致可交易天数过少甚至为 0。</p>
          <pre style={code}>{`# 示例: 数据 250 天
# MA(60) warmup = 60, MACD() warmup = 35
# 最大 warmup = 60 → 可交易: 250 - 60 = 190 天

# 如果数据只有 30 天，MA(60) warmup = 60 > 30 → 0 可交易天`}</pre>
        </>}

        {/* ================================================================ */}
        {/*  4. 信号与引擎                                                    */}
        {/* ================================================================ */}
        {active === 'signals' && <>
          <h1 style={{ fontSize: '20px', fontWeight: 700, marginBottom: '16px' }}>信号与回测引擎</h1>

          <div style={h2s}>引擎完整工作流程</div>
          <p style={ps}>VectorizedBacktestEngine 的 <code>run()</code> 方法按以下 7 个步骤执行：</p>
          <pre style={code}>{`步骤 1: 因子计算     → 调用每个因子的 compute(data)，追加列到 DataFrame
步骤 2: 信号生成     → 调用策略的 generate_signals(data)
步骤 3: 信号偏移     → signals.shift(1) — 防止未来函数（当天生成的信号，次日才执行）
步骤 4: Warmup 裁剪  → 去掉前 N 根 K 线（因子计算不稳定区间）
步骤 5: 逐 Bar 模拟  → 按信号执行买卖，计算持仓和权益
步骤 6: 基准计算     → Buy & Hold 买入持有基准
步骤 7: 指标计算     → Sharpe, 最大回撤, Alpha/Beta 等 + 显著性检验`}</pre>

          <div style={h2s}>信号如何触发交易</div>
          <p style={ps}>引擎逐 Bar 遍历数据，比较当前信号与上一根 Bar 的信号。只有在信号发生变化时才执行交易：</p>
          <table style={tbl}>
            <thead><tr><th style={ths}>信号变化</th><th style={ths}>引擎行为</th><th style={ths}>执行价格</th></tr></thead>
            <tbody>
              <tr><td style={tds}>0 → 1</td><td style={tds}>买入（用全部可用资金）</td><td style={tds}>当日开盘价 (open)</td></tr>
              <tr><td style={tds}>1 → 0</td><td style={tds}>卖出全部持仓</td><td style={tds}>当日开盘价 (open)</td></tr>
              <tr><td style={tds}>0 → 0.5</td><td style={tds}>买入（用 50% 资金）</td><td style={tds}>当日开盘价 (open)</td></tr>
              <tr><td style={tds}>1 → 0.5</td><td style={tds}>卖出一半持仓</td><td style={tds}>当日开盘价 (open)</td></tr>
              <tr><td style={tds}>0.5 → 0.5</td><td style={tds}>无操作（仓位不变）</td><td style={tds}>—</td></tr>
            </tbody>
          </table>

          <div style={h3s}>信号偏移（防止未来函数）</div>
          <p style={ps}>引擎会自动将信号右移 1 根 K 线：<code>signals.shift(1)</code>。这意味着：</p>
          <div style={note}>
            <p>T 日的收盘数据 → 计算出信号 → 在 T+1 日的开盘价执行交易。</p>
            <p>你在 <code>generate_signals()</code> 中看到的是 "当天数据"，但实际交易发生在 "次日开盘"。不需要你手动 shift。</p>
          </div>

          <div style={h3s}>仓位调整机制</div>
          <p style={ps}>信号值代表目标仓位占总权益的比例。引擎计算当前持仓市值和目标市值的差额，执行买入或卖出来达到目标仓位。</p>
          <pre style={code}>{`# 信号值 = 0.5，总权益 = 100,000
# 目标市值 = 100,000 * 0.5 = 50,000
# 当前持仓市值 = 30,000 → 需要买入 20,000
# 当前持仓市值 = 70,000 → 需要卖出 20,000`}</pre>

          <div style={h2s}>交易成本模型</div>
          <table style={tbl}>
            <thead><tr><th style={ths}>参数</th><th style={ths}>默认值</th><th style={ths}>计算方式</th></tr></thead>
            <tbody>
              <tr><td style={tds}>手续费率 (commission_rate)</td><td style={tds}>0.03% (万三)</td><td style={tds}>每笔交易按成交金额计算</td></tr>
              <tr><td style={tds}>最低手续费 (min_commission)</td><td style={tds}>5 元</td><td style={tds}>每笔交易不低于此金额</td></tr>
              <tr><td style={tds}>滑点率 (slippage_rate)</td><td style={tds}>0%</td><td style={tds}>买入价上浮 / 卖出价下浮</td></tr>
            </tbody>
          </table>

          <div style={h3s}>手续费计算公式</div>
          <pre style={code}>{`commission = max(成交金额 * commission_rate, min_commission)

# 示例: 买入 50,000 元股票，费率 0.03%
# commission = max(50000 * 0.0003, 5) = max(15, 5) = 15 元

# 示例: 买入 1,000 元股票（小额交易）
# commission = max(1000 * 0.0003, 5) = max(0.3, 5) = 5 元（最低佣金兜底）`}</pre>

          <div style={h3s}>滑点计算</div>
          <pre style={code}>{`# 滑点模拟市场冲击: 买入推高价格，卖出压低价格
买入成交价 = open * (1 + slippage_rate)
卖出成交价 = open * (1 - slippage_rate)

# 示例: 开盘价 10.00，滑点率 0.1%
# 买入价 = 10.00 * 1.001 = 10.01
# 卖出价 = 10.00 * 0.999 = 9.99`}</pre>

          <div style={h3s}>NaN 价格处理</div>
          <p style={ps}>如果某天的价格是 NaN（停牌或数据缺失），引擎会跳过该天的交易，权益曲线保持不变。</p>

          <div style={h2s}>权益曲线计算</div>
          <pre style={code}>{`# 每根 Bar 的权益 = 现金 + 持仓市值
equity[i] = cash + shares * adj_close[i]

# 日收益率
daily_return[i] = equity[i] / equity[i-1] - 1`}</pre>

          <div style={h2s}>回测指标详解</div>
          <table style={tbl}>
            <thead><tr><th style={ths}>指标</th><th style={ths}>含义</th><th style={ths}>怎么看</th></tr></thead>
            <tbody>
              <tr><td style={{...tds, fontWeight: 600}}>Sharpe Ratio</td><td style={tds}>衡量承担每单位风险能获得多少超额收益。计算方式是日超额收益的均值除以标准差，再年化（乘以根号 252）。Sharpe 高说明风险收益比好。</td><td style={{...tds, fontFamily:'monospace'}}>{'>'} 1.0 优秀, {'>'} 0.5 可接受</td></tr>
              <tr><td style={{...tds, fontWeight: 600}}>Sortino Ratio</td><td style={tds}>与 Sharpe 类似，但只计算下行波动率（只惩罚亏损波动，不惩罚上涨波动）。如果策略偶尔大涨但很少大跌，Sortino 会比 Sharpe 好看。</td><td style={{...tds, fontFamily:'monospace'}}>{'>'} 1.0 优秀</td></tr>
              <tr><td style={{...tds, fontWeight: 600}}>Total Return</td><td style={tds}>整个回测期间的总收益率。如期初 10 万变成 13 万，总收益 30%。</td><td style={{...tds, fontFamily:'monospace'}}>{'>'} 0 盈利</td></tr>
              <tr><td style={{...tds, fontWeight: 600}}>Annualized Return</td><td style={tds}>将总收益率折算为年化收益。考虑了复利效应，方便不同时间段的策略互相比较。</td><td style={{...tds, fontFamily:'monospace'}}>{'>'} 无风险利率</td></tr>
              <tr><td style={{...tds, fontWeight: 600}}>Max Drawdown</td><td style={tds}>从历史最高点到最低点的最大跌幅。反映策略最坏情况下你要承受多大的浮亏。比如 -30% 表示你在最高点买入，最坏会亏 30%。</td><td style={{...tds, fontFamily:'monospace'}}>{'>'} -20% 较好, {'>'} -30% 可接受</td></tr>
              <tr><td style={{...tds, fontWeight: 600}}>Max DD Duration</td><td style={tds}>最大回撤持续时间（交易日数）。从开始亏损到恢复到前高所用天数。持续时间越长，心理压力越大。</td><td style={{...tds, fontFamily:'monospace'}}>{'<'} 60 天较好</td></tr>
              <tr><td style={{...tds, fontWeight: 600}}>Win Rate</td><td style={tds}>盈利交易笔数占总交易笔数的比例。胜率高不代表赚钱（可能赢多输少但每次输很多），需要配合盈亏比一起看。</td><td style={{...tds, fontFamily:'monospace'}}>{'>'} 50% 较好</td></tr>
              <tr><td style={{...tds, fontWeight: 600}}>Profit Factor</td><td style={tds}>平均盈利幅度除以平均亏损幅度。大于 1 说明平均每次赢的钱比输的多。配合胜率可以判断策略能否长期盈利。</td><td style={{...tds, fontFamily:'monospace'}}>{'>'} 1.5 较好</td></tr>
              <tr><td style={{...tds, fontWeight: 600}}>Alpha</td><td style={tds}>超额收益率。使用 CAPM 模型回归，Alpha 代表策略独立于市场的收益部分。Alpha {'>'} 0 说明策略有超越市场的能力。</td><td style={{...tds, fontFamily:'monospace'}}>{'>'} 0 有超额收益</td></tr>
              <tr><td style={{...tds, fontWeight: 600}}>Beta</td><td style={tds}>与市场（基准）的相关性。Beta = 1 完全跟随市场，Beta = 0 完全独立。低 Beta 策略受大盘影响小。</td><td style={{...tds, fontFamily:'monospace'}}>接近 0 为独立</td></tr>
              <tr><td style={{...tds, fontWeight: 600}}>Trade Count</td><td style={tds}>总交易次数（一买一卖算一次）。交易次数太少（{'<'} 10）统计意义不足，结果不可靠。</td><td style={{...tds, fontFamily:'monospace'}}>{'>'} 10 有统计意义</td></tr>
              <tr><td style={{...tds, fontWeight: 600}}>Avg Holding Days</td><td style={tds}>平均每笔交易的持仓天数。反映策略的交易频率和风格（短线 vs 长线）。</td><td style={{...tds, fontFamily:'monospace'}}>因策略而异</td></tr>
            </tbody>
          </table>

          <div style={h2s}>前推验证 (Walk-Forward Validation)</div>
          <p style={ps}>Walk-Forward 是验证策略稳健性的重要手段。核心思想：如果一个策略在历史样本外（未见过的数据）仍然有效，说明它不是过拟合的。</p>

          <div style={h3s}>数据划分方式</div>
          <pre style={code}>{`n_splits=3, train_ratio=0.7 时:

|---训练 70%---|--测试 30%--|---训练 70%---|--测试 30%--|---训练 70%---|--测试 30%--|
^  Split 0                  ^  Split 1                  ^  Split 2
   (IS)          (OOS)         (IS)          (OOS)         (IS)          (OOS)

IS  = In-Sample (样本内) — 用于评估训练集表现
OOS = Out-of-Sample (样本外) — 真正衡量预测能力
注意: 每个 Split 之间的数据严格不重叠，避免数据泄露`}</pre>

          <div style={h3s}>Walk-Forward 输出指标</div>
          <table style={tbl}>
            <thead><tr><th style={ths}>指标</th><th style={ths}>含义</th><th style={ths}>怎么看</th></tr></thead>
            <tbody>
              <tr><td style={tds}>OOS Sharpe</td><td style={tds}>所有 Split 样本外 Sharpe 的平均值，代表策略的真实预测能力</td><td style={{...tds, fontFamily:'monospace'}}>{'>'} 0.5 较好</td></tr>
              <tr><td style={tds}>过拟合评分 (Overfitting Score)</td><td style={tds}>IS Sharpe 与 OOS Sharpe 的衰减程度。越高说明策略在训练集表现好但测试集表现差（过拟合）</td><td style={{...tds, fontFamily:'monospace'}}>{'<'} 0.3 稳健</td></tr>
              <tr><td style={tds}>IS/OOS 衰减</td><td style={tds}>(IS均值 - OOS均值) / |IS均值| 的百分比</td><td style={{...tds, fontFamily:'monospace'}}>{'<'} 30% 较好</td></tr>
            </tbody>
          </table>

          <div style={h3s}>过拟合评分解读</div>
          <table style={tbl}>
            <thead><tr><th style={ths}>评分范围</th><th style={ths}>判定</th><th style={ths}>建议</th></tr></thead>
            <tbody>
              <tr><td style={tds}>0 - 0.3</td><td style={tds}>稳健</td><td style={tds}>策略可信，IS/OOS 表现一致</td></tr>
              <tr><td style={tds}>0.3 - 0.6</td><td style={tds}>中度过拟合</td><td style={tds}>谨慎使用，考虑简化参数</td></tr>
              <tr><td style={tds}>0.6 - 1.0+</td><td style={tds}>严重过拟合</td><td style={tds}>不建议使用，策略可能是曲线拟合</td></tr>
            </tbody>
          </table>

          <div style={h2s}>显著性检验 (Significance Test)</div>
          <p style={ps}>检验策略收益是否显著优于随机交易，排除 "运气好" 的可能。</p>

          <div style={h3s}>Monte Carlo 排列检验方法</div>
          <pre style={code}>{`1. 计算策略的 Sharpe Ratio = observed_sharpe
2. 将策略信号序列随机打乱 1000 次
3. 每次用打乱后的信号 × 资产收益率，计算新的 Sharpe
4. p-value = (打乱后 Sharpe >= observed_sharpe 的次数) / 1000
5. 如果 p < 0.05，说明策略的择时能力不是随机产生的`}</pre>

          <div style={h3s}>结果解读</div>
          <table style={tbl}>
            <thead><tr><th style={ths}>指标</th><th style={ths}>说明</th></tr></thead>
            <tbody>
              <tr><td style={tds}>p-value</td><td style={tds}>策略收益来自运气的概率。p {'<'} 0.05 = 显著（95% 置信度不是运气）。p {'>'} 0.05 = 不显著。</td></tr>
              <tr><td style={tds}>Sharpe CI (95%)</td><td style={tds}>Bootstrap 95% 置信区间。如果下界 {'>'} 0，说明策略大概率是正收益。</td></tr>
              <tr><td style={tds}>NaN</td><td style={tds}>当信号为常量（如始终满仓的 Buy & Hold）时，打乱信号无意义，返回 NaN。这不是错误。</td></tr>
            </tbody>
          </table>
          <div style={warn}>常量信号（如所有 bar 都是 1.0 的 Buy & Hold）会返回 p-value = NaN，因为打乱一个常量数组得到的仍然是同一个数组，无法做排列检验。这是正常行为。</div>
        </>}

        {/* ================================================================ */}
        {/*  5. A股规则                                                       */}
        {/* ================================================================ */}
        {active === 'market-rules' && <>
          <h1 style={{ fontSize: '20px', fontWeight: 700, marginBottom: '16px' }}>A 股市场规则</h1>
          <p style={ps}>在实验面板或回测时勾选 "A股规则" 后，回测引擎会通过 MarketRulesMatcher 模拟真实 A 股交易限制。这些规则会显著影响回测结果，使结果更接近真实交易。</p>

          <div style={h2s}>T+1 规则</div>
          <p style={ps}>A 股实行 T+1 交易制度，即买入当天不能卖出，最早次日才可卖出。</p>
          <table style={tbl}>
            <thead><tr><th style={ths}>场景</th><th style={ths}>行为</th></tr></thead>
            <tbody>
              <tr><td style={tds}>T 日买入 → T 日试图卖出</td><td style={tds}>卖出被拒绝，引擎会在 T+1 日自动重试</td></tr>
              <tr><td style={tds}>T 日买入 → T+1 日卖出</td><td style={tds}>正常卖出</td></tr>
              <tr><td style={tds}>T-1 日买入 → T 日卖出</td><td style={tds}>正常卖出（已持有 1 天以上）</td></tr>
            </tbody>
          </table>
          <div style={note}>T+1 对日内反转策略影响最大。如果你的策略在同一天先发出买入信号再发出卖出信号，卖出会被延迟到次日。</div>

          <div style={h2s}>涨跌停限制</div>
          <p style={ps}>A 股主板涨跌停幅度为 10%，以前一交易日的未复权收盘价（raw close）为基准。</p>
          <table style={tbl}>
            <thead><tr><th style={ths}>规则</th><th style={ths}>计算方式</th><th style={ths}>引擎行为</th></tr></thead>
            <tbody>
              <tr><td style={tds}>涨停不可买</td><td style={tds}>涨停价 = 昨收 * (1 + 10%)</td><td style={tds}>如果开盘价 {'>='} 涨停价，买入被拒绝</td></tr>
              <tr><td style={tds}>跌停不可卖</td><td style={tds}>跌停价 = 昨收 * (1 - 10%)</td><td style={tds}>如果开盘价 {'<='} 跌停价，卖出被拒绝</td></tr>
            </tbody>
          </table>
          <div style={warn}>
            <p><b>重要</b>：涨跌停判定使用的是未复权收盘价（raw close），不是前复权价（adj_close）。这是因为涨跌停是实际交易规则，必须用实际价格计算。</p>
            <p style={{ marginTop: '4px' }}>创业板/科创板的涨跌停幅度为 20%，可以通过 <code>price_limit_pct: 0.2</code> 参数设置。</p>
          </div>

          <div style={h2s}>整手限制</div>
          <p style={ps}>A 股买卖必须是 100 股（1 手）的整数倍。小于 100 股的零头无法交易。</p>
          <table style={tbl}>
            <thead><tr><th style={ths}>场景</th><th style={ths}>行为</th></tr></thead>
            <tbody>
              <tr><td style={tds}>计算出买 350 股</td><td style={tds}>向下取整为 300 股（3 手）</td></tr>
              <tr><td style={tds}>计算出买 80 股</td><td style={tds}>不够 1 手，买入被拒绝</td></tr>
              <tr><td style={tds}>计算出卖 250 股</td><td style={tds}>向下取整为 200 股（2 手）</td></tr>
            </tbody>
          </table>
          <div style={note}>整手限制对小资金影响很大。如果股价 50 元，买 100 股需要 5,000 元。如果你的初始资金是 10,000 元且想半仓，可能只能买 100 股而非精确的 50% 仓位。佣金也会根据整手后的实际成交金额重新计算。</div>

          <div style={h2s}>Fill-Retry 机制</div>
          <p style={ps}>当交易被拒绝（涨跌停、T+1、资金不够整手）时，引擎会保持上一个目标信号不变，在下一根 K 线自动重试。</p>
          <pre style={code}>{`# 信号从 0 变到 1（目标满仓），但当日涨停买入被拒
# 引擎记住 "目标 = 1"，下一根 K 线如果不再涨停，会继续尝试买入
# 直到买入成功，才更新 prev_weight = 1`}</pre>

          <div style={h2s}>何时启用/禁用 A 股规则</div>
          <table style={tbl}>
            <thead><tr><th style={ths}>场景</th><th style={ths}>建议</th></tr></thead>
            <tbody>
              <tr><td style={tds}>A 股策略实盘前最终验证</td><td style={tds}>启用 — 结果更接近真实交易</td></tr>
              <tr><td style={tds}>策略研究/对比阶段</td><td style={tds}>禁用 — 减少干扰因素，专注信号质量</td></tr>
              <tr><td style={tds}>美股/港股策略</td><td style={tds}>禁用 — 不适用 T+0 市场</td></tr>
              <tr><td style={tds}>参数搜索阶段</td><td style={tds}>禁用 — 加速搜索，最终确认时再启用</td></tr>
            </tbody>
          </table>
        </>}

        {/* ================================================================ */}
        {/*  6. 实验流水线                                                    */}
        {/* ================================================================ */}
        {active === 'experiment' && <>
          <h1 style={{ fontSize: '20px', fontWeight: 700, marginBottom: '16px' }}>实验流水线</h1>

          <div style={h2s}>运行实验时发生了什么</div>
          <p style={ps}>当你在实验面板点击 "运行实验"，系统会执行以下完整流水线：</p>
          <pre style={code}>{`1. RunSpec 创建      → 根据参数生成 spec_id (内容哈希)
      ↓                     如果 spec_id 已存在 → 返回 duplicate（去重）
2. 数据获取          → 从数据源拉取 K 线数据
      ↓
3. 回测执行          → VectorizedBacktestEngine.run()
      ↓                     因子计算 → 信号生成 → 逐 bar 模拟
4. 前推验证 (可选)   → WalkForwardValidator.validate()
      ↓                     N 折交叉验证，计算 OOS Sharpe 和过拟合评分
5. 显著性检验        → compute_significance()
      ↓                     Monte Carlo 排列检验 + Bootstrap CI
6. Research Gate     → ResearchGate.evaluate()
      ↓                     对照阈值逐条检查，判定 PASS/FAIL
7. 结果持久化        → ExperimentStore.save()
                            写入 DuckDB，可查询/对比/删除`}</pre>

          <div style={h2s}>Research Gate 规则</div>
          <p style={ps}>Research Gate 是自动化的策略质量评审。所有规则必须同时通过才能获得 PASS 评级。</p>
          <table style={tbl}>
            <thead><tr><th style={ths}>规则</th><th style={ths}>默认阈值</th><th style={ths}>判定逻辑</th><th style={ths}>含义</th></tr></thead>
            <tbody>
              <tr><td style={tds}>min_sharpe</td><td style={{...tds, fontFamily:'monospace'}}>0.5</td><td style={tds}>Sharpe {'>='} 0.5</td><td style={tds}>风险收益比达标</td></tr>
              <tr><td style={tds}>max_drawdown</td><td style={{...tds, fontFamily:'monospace'}}>30%</td><td style={tds}>|MaxDD| {'<='} 0.3</td><td style={tds}>最大回撤在可控范围</td></tr>
              <tr><td style={tds}>min_trades</td><td style={{...tds, fontFamily:'monospace'}}>10</td><td style={tds}>交易次数 {'>='} 10</td><td style={tds}>有足够统计样本</td></tr>
              <tr><td style={tds}>max_p_value</td><td style={{...tds, fontFamily:'monospace'}}>0.05</td><td style={tds}>p-value {'<='} 0.05</td><td style={tds}>收益统计显著（非运气）</td></tr>
              <tr><td style={tds}>max_overfitting</td><td style={{...tds, fontFamily:'monospace'}}>0.5</td><td style={tds}>过拟合评分 {'<='} 0.5</td><td style={tds}>样本外表现未严重衰减</td></tr>
            </tbody>
          </table>
          <div style={note}>Gate 阈值可在实验面板中自定义。例如对于高频策略，你可能想把 min_trades 提高到 50；对于保守配置，可以把 max_drawdown 降到 15%。</div>

          <div style={h3s}>Gate PASS/FAIL 含义</div>
          <table style={tbl}>
            <thead><tr><th style={ths}>结果</th><th style={ths}>含义</th><th style={ths}>建议</th></tr></thead>
            <tbody>
              <tr><td style={tds}><span style={{ color: '#22c55e', fontWeight: 600 }}>PASS</span></td><td style={tds}>所有规则全部通过</td><td style={tds}>策略质量达标，可以进一步研究或进入部署流程</td></tr>
              <tr><td style={tds}><span style={{ color: '#ef4444', fontWeight: 600 }}>FAIL</span></td><td style={tds}>至少一条规则未通过</td><td style={tds}>查看失败原因，针对性优化策略参数或逻辑</td></tr>
            </tbody>
          </table>

          <div style={h2s}>去重机制 (Idempotency)</div>
          <p style={ps}>RunSpec 使用内容哈希（SHA-256）生成 spec_id。相同的策略名、参数、股票、日期范围、交易成本等参数会生成相同的 spec_id。如果该 spec_id 已有完成的实验记录，系统会直接返回 "duplicate" 状态，跳过重复计算。</p>
          <pre style={code}>{`# 这两次调用会生成相同的 spec_id，第二次直接返回已有结果
实验1: strategy=MACross, params={fast:5, slow:20}, symbol=000001.SZ, 2020-2024
实验2: strategy=MACross, params={fast:5, slow:20}, symbol=000001.SZ, 2020-2024
# → 第二次返回 {"status": "duplicate", "existing_run_id": "..."}

# 修改任何一个参数，spec_id 就会不同
实验3: strategy=MACross, params={fast:10, slow:20}, symbol=000001.SZ, 2020-2024
# → 新的 spec_id，会执行完整流水线`}</pre>

          <div style={h2s}>批量参数搜索</div>
          <p style={ps}>实验面板的 "参数搜索" 功能可以批量测试不同参数组合，找到最优参数。</p>

          <div style={h3s}>Grid Search vs Random Search</div>
          <table style={tbl}>
            <thead><tr><th style={ths}>模式</th><th style={ths}>方式</th><th style={ths}>适用场景</th></tr></thead>
            <tbody>
              <tr><td style={tds}>Grid Search</td><td style={tds}>穷举所有参数组合（笛卡尔积）</td><td style={tds}>参数少（2-3 个），范围小，需要全面覆盖</td></tr>
              <tr><td style={tds}>Random Search</td><td style={tds}>从参数空间随机采样 N 个组合</td><td style={tds}>参数多，范围大，或计算资源有限</td></tr>
            </tbody>
          </table>

          <div style={h3s}>Pre-filter 预筛选</div>
          <p style={ps}>在完整实验之前，系统会对每个候选参数做一次快速回测（不含 Walk-Forward 和显著性检验），用简单规则淘汰明显不行的参数。</p>
          <pre style={code}>{`Pre-filter 规则:
- 交易次数 >= 5（太少直接淘汰）
- Sharpe > -1.0（明显亏损淘汰）
- 总收益 > -50%（大幅亏损淘汰）

通过 pre-filter 的候选 → 进入完整实验流水线（回测 + WFO + 显著性 + Gate）
未通过的候选 → 标记为 "pre-filtered"，跳过`}</pre>

          <div style={h3s}>FDR 校正 (Benjamini-Hochberg)</div>
          <p style={ps}>当你测试很多参数组合时，即使每个测试 p {'<'} 0.05，也可能有一些是"假阳性"（运气好恰好过了门槛）。FDR (False Discovery Rate) 校正用来控制假阳性比例。</p>
          <pre style={code}>{`# 假设你测试了 100 组参数
# 其中 20 组的 p-value < 0.05
# 但按概率，随机也会有 5 组 p < 0.05（假阳性）
#
# FDR 校正后的 fdr_adjusted_p 会更大
# 只有 fdr_adjusted_p < 0.05 的才是真正显著的`}</pre>
          <p style={ps}>批量搜索结果中的 <code>fdr_adjusted_p</code> 列就是 FDR 校正后的 p 值。优先关注这个值而非原始 p 值。</p>
        </>}

        {/* ================================================================ */}
        {/*  7. 数据源                                                        */}
        {/* ================================================================ */}
        {active === 'data' && <>
          <h1 style={{ fontSize: '20px', fontWeight: 700, marginBottom: '16px' }}>数据源</h1>

          <div style={h2s}>支持的市场</div>
          <table style={tbl}>
            <thead><tr><th style={ths}>市场标识</th><th style={ths}>名称</th><th style={ths}>数据源</th><th style={ths}>股票代码格式</th></tr></thead>
            <tbody>
              <tr><td style={{...tds, fontFamily:'monospace'}}>cn_stock</td><td style={tds}>A 股（中国大陆）</td><td style={tds}>Tushare (主) / 腾讯 (备)</td><td style={tds}>000001.SZ, 600519.SH</td></tr>
              <tr><td style={{...tds, fontFamily:'monospace'}}>us_stock</td><td style={tds}>美股</td><td style={tds}>FMP</td><td style={tds}>AAPL, MSFT, TSLA</td></tr>
              <tr><td style={{...tds, fontFamily:'monospace'}}>hk_stock</td><td style={tds}>港股</td><td style={tds}>FMP</td><td style={tds}>0700.HK, 9988.HK</td></tr>
            </tbody>
          </table>

          <div style={h2s}>数据提供商</div>

          <div style={h3s}>Tushare (A 股首选)</div>
          <table style={tbl}>
            <tbody>
              <tr><td style={{...tds, width:'140px', fontWeight:600}}>覆盖范围</td><td style={tds}>A 股全部上市公司，日线/周线/月线</td></tr>
              <tr><td style={{...tds, fontWeight:600}}>认证</td><td style={tds}>需要 <code>TUSHARE_TOKEN</code> 环境变量。在 <a href="https://tushare.pro" style={{color:'var(--color-accent)'}}>tushare.pro</a> 注册获取</td></tr>
              <tr><td style={{...tds, fontWeight:600}}>复权</td><td style={tds}>通过 adj_factor API 计算前复权价格</td></tr>
              <tr><td style={{...tds, fontWeight:600}}>速率限制</td><td style={tds}>内置 0.3 秒节流，防止触发频率限制</td></tr>
              <tr><td style={{...tds, fontWeight:600}}>无 SDK 依赖</td><td style={tds}>直接 HTTP 调用，不需要安装 tushare pip 包</td></tr>
            </tbody>
          </table>

          <div style={h3s}>腾讯财经 (A 股备用)</div>
          <table style={tbl}>
            <tbody>
              <tr><td style={{...tds, width:'140px', fontWeight:600}}>覆盖范围</td><td style={tds}>A 股主要股票</td></tr>
              <tr><td style={{...tds, fontWeight:600}}>认证</td><td style={tds}>免费，无需 Token</td></tr>
              <tr><td style={{...tds, fontWeight:600}}>适用场景</td><td style={tds}>没有 Tushare Token 时的后备方案</td></tr>
            </tbody>
          </table>

          <div style={h3s}>FMP (美股/港股)</div>
          <table style={tbl}>
            <tbody>
              <tr><td style={{...tds, width:'140px', fontWeight:600}}>覆盖范围</td><td style={tds}>美股、港股、全球主要市场</td></tr>
              <tr><td style={{...tds, fontWeight:600}}>认证</td><td style={tds}>需要 <code>FMP_API_KEY</code> 环境变量</td></tr>
              <tr><td style={{...tds, fontWeight:600}}>适用场景</td><td style={tds}>美股和港股数据获取</td></tr>
            </tbody>
          </table>

          <div style={h2s}>数据格式</div>
          <p style={ps}>所有数据源统一返回 OHLCV 格式的 K 线数据：</p>
          <table style={tbl}>
            <thead><tr><th style={ths}>字段</th><th style={ths}>类型</th><th style={ths}>说明</th></tr></thead>
            <tbody>
              <tr><td style={{...tds, fontFamily:'monospace'}}>time</td><td style={tds}>datetime</td><td style={tds}>交易日期（作为 DataFrame 的 index）</td></tr>
              <tr><td style={{...tds, fontFamily:'monospace'}}>open</td><td style={tds}>float</td><td style={tds}>开盘价</td></tr>
              <tr><td style={{...tds, fontFamily:'monospace'}}>high</td><td style={tds}>float</td><td style={tds}>最高价</td></tr>
              <tr><td style={{...tds, fontFamily:'monospace'}}>low</td><td style={tds}>float</td><td style={tds}>最低价</td></tr>
              <tr><td style={{...tds, fontFamily:'monospace'}}>close</td><td style={tds}>float</td><td style={tds}>收盘价（未复权）</td></tr>
              <tr><td style={{...tds, fontFamily:'monospace'}}>adj_close</td><td style={tds}>float</td><td style={tds}>前复权收盘价</td></tr>
              <tr><td style={{...tds, fontFamily:'monospace'}}>volume</td><td style={tds}>float</td><td style={tds}>成交量（股数）</td></tr>
            </tbody>
          </table>

          <div style={h2s}>数据周期</div>
          <table style={tbl}>
            <thead><tr><th style={ths}>周期标识</th><th style={ths}>说明</th></tr></thead>
            <tbody>
              <tr><td style={{...tds, fontFamily:'monospace'}}>daily</td><td style={tds}>日线（默认，最常用）</td></tr>
              <tr><td style={{...tds, fontFamily:'monospace'}}>weekly</td><td style={tds}>周线</td></tr>
              <tr><td style={{...tds, fontFamily:'monospace'}}>monthly</td><td style={tds}>月线</td></tr>
            </tbody>
          </table>

          <div style={h2s}>缓存机制</div>
          <p style={ps}>数据获取遵循以下优先级链：</p>
          <pre style={code}>{`1. DuckDB 本地缓存  → 有就直接用（最快）
2. 主数据源 API     → 缓存未命中，从 API 拉取（Tushare/FMP）
3. 备用数据源 API   → 主源失败，用备用源（腾讯）
4. 过期缓存         → 所有 API 都失败，用可能过期的缓存数据`}</pre>
          <p style={ps}>首次获取某只股票的数据会稍慢（需要 API 调用），之后会从本地 DuckDB 缓存直接读取。</p>

          <div style={h2s}>配置数据源</div>
          <p style={ps}>在项目根目录 <code>.env</code> 文件中配置 API Token：</p>
          <pre style={code}>{`# A 股数据 (Tushare)
TUSHARE_TOKEN=your_token_here

# 美股/港股数据 (FMP)
FMP_API_KEY=your_key_here`}</pre>
          <div style={note}>如果没有配置 Tushare Token，A 股数据会降级为腾讯数据源（免费但数据可能不全）。</div>
        </>}

        {/* ================================================================ */}
        {/*  8. API 参考                                                      */}
        {/* ================================================================ */}
        {active === 'api' && <>
          <h1 style={{ fontSize: '20px', fontWeight: 700, marginBottom: '16px' }}>API 参考</h1>
          <p style={ps}>后端运行在 <code>http://localhost:8000</code>，所有接口前缀 <code>/api</code>。</p>

          {/* Backtest Run */}
          <div style={h2s}>POST /api/backtest/run — 单次回测</div>
          <div style={h3s}>请求体</div>
          <table style={tbl}>
            <thead><tr><th style={ths}>字段</th><th style={ths}>类型</th><th style={ths}>必填</th><th style={ths}>默认值</th><th style={ths}>说明</th></tr></thead>
            <tbody>
              <tr><td style={{...tds, fontFamily:'monospace'}}>strategy_name</td><td style={tds}>string</td><td style={tds}>是</td><td style={tds}>—</td><td style={tds}>策略注册 key</td></tr>
              <tr><td style={{...tds, fontFamily:'monospace'}}>strategy_params</td><td style={tds}>object</td><td style={tds}>否</td><td style={{...tds, fontFamily:'monospace'}}>{'{}'}</td><td style={tds}>策略参数</td></tr>
              <tr><td style={{...tds, fontFamily:'monospace'}}>symbol</td><td style={tds}>string</td><td style={tds}>是</td><td style={tds}>—</td><td style={tds}>股票代码 (如 000001.SZ)</td></tr>
              <tr><td style={{...tds, fontFamily:'monospace'}}>market</td><td style={tds}>string</td><td style={tds}>否</td><td style={{...tds, fontFamily:'monospace'}}>cn_stock</td><td style={tds}>市场标识</td></tr>
              <tr><td style={{...tds, fontFamily:'monospace'}}>period</td><td style={tds}>string</td><td style={tds}>否</td><td style={{...tds, fontFamily:'monospace'}}>daily</td><td style={tds}>K 线周期</td></tr>
              <tr><td style={{...tds, fontFamily:'monospace'}}>start_date</td><td style={tds}>string</td><td style={tds}>是</td><td style={tds}>—</td><td style={tds}>开始日期 (YYYY-MM-DD)</td></tr>
              <tr><td style={{...tds, fontFamily:'monospace'}}>end_date</td><td style={tds}>string</td><td style={tds}>是</td><td style={tds}>—</td><td style={tds}>结束日期 (YYYY-MM-DD)</td></tr>
              <tr><td style={{...tds, fontFamily:'monospace'}}>initial_capital</td><td style={tds}>float</td><td style={tds}>否</td><td style={{...tds, fontFamily:'monospace'}}>100000</td><td style={tds}>初始资金</td></tr>
              <tr><td style={{...tds, fontFamily:'monospace'}}>commission_rate</td><td style={tds}>float</td><td style={tds}>否</td><td style={{...tds, fontFamily:'monospace'}}>0.0003</td><td style={tds}>手续费率 (万三)</td></tr>
              <tr><td style={{...tds, fontFamily:'monospace'}}>min_commission</td><td style={tds}>float</td><td style={tds}>否</td><td style={{...tds, fontFamily:'monospace'}}>5.0</td><td style={tds}>最低佣金 (元)</td></tr>
              <tr><td style={{...tds, fontFamily:'monospace'}}>slippage_rate</td><td style={tds}>float</td><td style={tds}>否</td><td style={{...tds, fontFamily:'monospace'}}>0.0</td><td style={tds}>滑点率 (0~0.1)</td></tr>
            </tbody>
          </table>
          <div style={h3s}>响应体</div>
          <pre style={code}>{`{
  "metrics": { "sharpe_ratio", "total_return", "max_drawdown", "win_rate", ... },
  "equity_curve": [100000, 100120, ...],
  "benchmark_curve": [100000, 100050, ...],
  "trades": [{ "entry_time", "exit_time", "entry_price", "exit_price", "pnl", ... }],
  "significance": { "monte_carlo_p_value", "sharpe_ci_lower", "sharpe_ci_upper", "is_significant" }
}`}</pre>
          <div style={h3s}>示例 curl</div>
          <pre style={code}>{`curl -X POST http://localhost:8000/api/backtest/run \\
  -H "Content-Type: application/json" \\
  -d '{
    "strategy_name": "ez.strategy.builtin.ma_cross.MACrossStrategy",
    "strategy_params": {"fast_period": 5, "slow_period": 20},
    "symbol": "000001.SZ",
    "start_date": "2020-01-01",
    "end_date": "2024-01-01"
  }'`}</pre>

          {/* Walk-Forward */}
          <div style={h2s}>POST /api/backtest/walk-forward — 前推验证</div>
          <div style={h3s}>请求体（在回测参数基础上增加）</div>
          <table style={tbl}>
            <thead><tr><th style={ths}>字段</th><th style={ths}>类型</th><th style={ths}>默认值</th><th style={ths}>约束</th><th style={ths}>说明</th></tr></thead>
            <tbody>
              <tr><td style={{...tds, fontFamily:'monospace'}}>n_splits</td><td style={tds}>int</td><td style={{...tds, fontFamily:'monospace'}}>5</td><td style={tds}>2~20</td><td style={tds}>数据分割段数</td></tr>
              <tr><td style={{...tds, fontFamily:'monospace'}}>train_ratio</td><td style={tds}>float</td><td style={{...tds, fontFamily:'monospace'}}>0.7</td><td style={tds}>0.5~0.9</td><td style={tds}>训练集比例</td></tr>
            </tbody>
          </table>
          <div style={h3s}>响应体</div>
          <pre style={code}>{`{
  "oos_metrics": { "sharpe_ratio": 0.45 },
  "overfitting_score": 0.25,
  "is_vs_oos_degradation": 0.25,
  "oos_equity_curve": [100000, ...]
}`}</pre>

          {/* Strategies List */}
          <div style={h2s}>GET /api/backtest/strategies — 策略列表</div>
          <pre style={code}>{`# 响应
[
  {
    "name": "MACrossStrategy",
    "key": "ez.strategy.builtin.ma_cross.MACrossStrategy",
    "parameters": { "fast_period": {"type":"int","default":5,...}, ... },
    "description": "双均线交叉策略"
  },
  ...
]`}</pre>

          {/* Experiments */}
          <div style={h2s}>POST /api/experiments — 运行完整实验</div>
          <div style={h3s}>请求体</div>
          <table style={tbl}>
            <thead><tr><th style={ths}>字段</th><th style={ths}>类型</th><th style={ths}>默认值</th><th style={ths}>说明</th></tr></thead>
            <tbody>
              <tr><td style={{...tds, fontFamily:'monospace'}}>strategy_name</td><td style={tds}>string</td><td style={tds}>—</td><td style={tds}>策略注册 key</td></tr>
              <tr><td style={{...tds, fontFamily:'monospace'}}>strategy_params</td><td style={tds}>object</td><td style={{...tds, fontFamily:'monospace'}}>{'{}'}</td><td style={tds}>策略参数</td></tr>
              <tr><td style={{...tds, fontFamily:'monospace'}}>symbol</td><td style={tds}>string</td><td style={tds}>—</td><td style={tds}>股票代码</td></tr>
              <tr><td style={{...tds, fontFamily:'monospace'}}>market</td><td style={tds}>string</td><td style={{...tds, fontFamily:'monospace'}}>cn_stock</td><td style={tds}>市场</td></tr>
              <tr><td style={{...tds, fontFamily:'monospace'}}>period</td><td style={tds}>string</td><td style={{...tds, fontFamily:'monospace'}}>daily</td><td style={tds}>周期</td></tr>
              <tr><td style={{...tds, fontFamily:'monospace'}}>start_date / end_date</td><td style={tds}>string</td><td style={tds}>—</td><td style={tds}>日期范围 (YYYY-MM-DD)</td></tr>
              <tr><td style={{...tds, fontFamily:'monospace'}}>initial_capital</td><td style={tds}>float</td><td style={{...tds, fontFamily:'monospace'}}>100000</td><td style={tds}>初始资金</td></tr>
              <tr><td style={{...tds, fontFamily:'monospace'}}>commission_rate</td><td style={tds}>float</td><td style={{...tds, fontFamily:'monospace'}}>0.0003</td><td style={tds}>手续费率</td></tr>
              <tr><td style={{...tds, fontFamily:'monospace'}}>slippage_rate</td><td style={tds}>float</td><td style={{...tds, fontFamily:'monospace'}}>0.0</td><td style={tds}>滑点率 (0~0.1)</td></tr>
              <tr><td style={{...tds, fontFamily:'monospace'}}>run_wfo</td><td style={tds}>bool</td><td style={{...tds, fontFamily:'monospace'}}>true</td><td style={tds}>是否运行 Walk-Forward</td></tr>
              <tr><td style={{...tds, fontFamily:'monospace'}}>wfo_n_splits</td><td style={tds}>int</td><td style={{...tds, fontFamily:'monospace'}}>5</td><td style={tds}>WFO 分割段数 (2~20)</td></tr>
              <tr><td style={{...tds, fontFamily:'monospace'}}>wfo_train_ratio</td><td style={tds}>float</td><td style={{...tds, fontFamily:'monospace'}}>0.7</td><td style={tds}>WFO 训练比例 (0.5~0.9)</td></tr>
              <tr><td style={{...tds, fontFamily:'monospace'}}>use_market_rules</td><td style={tds}>bool</td><td style={{...tds, fontFamily:'monospace'}}>false</td><td style={tds}>是否启用 A 股规则</td></tr>
              <tr><td style={{...tds, fontFamily:'monospace'}}>t_plus_1</td><td style={tds}>bool</td><td style={{...tds, fontFamily:'monospace'}}>true</td><td style={tds}>T+1 限制</td></tr>
              <tr><td style={{...tds, fontFamily:'monospace'}}>price_limit_pct</td><td style={tds}>float</td><td style={{...tds, fontFamily:'monospace'}}>0.1</td><td style={tds}>涨跌停比例 (0~0.5)</td></tr>
              <tr><td style={{...tds, fontFamily:'monospace'}}>lot_size</td><td style={tds}>int</td><td style={{...tds, fontFamily:'monospace'}}>100</td><td style={tds}>整手股数 (0=禁用)</td></tr>
              <tr><td style={{...tds, fontFamily:'monospace'}}>gate_min_sharpe</td><td style={tds}>float</td><td style={{...tds, fontFamily:'monospace'}}>0.5</td><td style={tds}>Gate: 最低 Sharpe</td></tr>
              <tr><td style={{...tds, fontFamily:'monospace'}}>gate_max_drawdown</td><td style={tds}>float</td><td style={{...tds, fontFamily:'monospace'}}>0.3</td><td style={tds}>Gate: 最大回撤</td></tr>
              <tr><td style={{...tds, fontFamily:'monospace'}}>gate_min_trades</td><td style={tds}>int</td><td style={{...tds, fontFamily:'monospace'}}>10</td><td style={tds}>Gate: 最低交易次数</td></tr>
              <tr><td style={{...tds, fontFamily:'monospace'}}>gate_max_p_value</td><td style={tds}>float</td><td style={{...tds, fontFamily:'monospace'}}>0.05</td><td style={tds}>Gate: 最大 p 值</td></tr>
            </tbody>
          </table>
          <div style={h3s}>响应体</div>
          <pre style={code}>{`{
  "run_id": "abc-123",
  "spec_id": "sha256-hash",
  "status": "completed",          // completed | duplicate | failed
  "strategy_name": "...",
  "sharpe_ratio": 1.23,
  "total_return": 0.45,
  "max_drawdown": -0.18,
  "gate_passed": true,
  "gate_summary": "PASS (5/5 rules passed)",
  "gate_reasons": [ { "rule": "min_sharpe", "passed": true, "value": 1.23, "threshold": 0.5 }, ... ]
}`}</pre>

          {/* Experiments List */}
          <div style={h2s}>GET /api/experiments — 实验记录列表</div>
          <pre style={code}>{`GET /api/experiments?limit=50&offset=0
# 响应: [{ run_id, strategy_name, sharpe_ratio, gate_passed, created_at, ... }]`}</pre>

          {/* Experiments Get/Delete */}
          <div style={h2s}>GET /api/experiments/{'{run_id}'} — 单个实验详情</div>
          <div style={h2s}>DELETE /api/experiments/{'{run_id}'} — 删除单个实验</div>
          <div style={h2s}>POST /api/experiments/cleanup — 清理旧实验</div>
          <pre style={code}>{`POST /api/experiments/cleanup?keep_last=200
# 保留最近 200 条，删除更早的记录`}</pre>

          {/* Candidates */}
          <div style={h2s}>POST /api/candidates/search — 批量参数搜索</div>
          <pre style={code}>{`# 请求
{
  "strategy_name": "...",
  "param_ranges": { "period": {"min":5,"max":30,"step":5}, ... },
  "symbol": "000001.SZ",
  "start_date": "2020-01-01", "end_date": "2024-01-01",
  "mode": "grid"    // "grid" | "random"
}
# 响应
{
  "total_specs": 144,
  "ranked": [
    { "params": {...}, "sharpe_ratio": 1.5, "gate_passed": true, "fdr_adjusted_p": 0.02 },
    ...
  ]
}`}</pre>

          {/* Code Editor */}
          <div style={h2s}>代码编辑器 API</div>
          {[
            { method: 'POST', path: '/api/code/template', desc: '生成模板', body: '{ kind: "strategy" | "factor", class_name?: string }', resp: '{ code: string }' },
            { method: 'POST', path: '/api/code/validate', desc: '语法 + 安全检查', body: '{ code: string }', resp: '{ valid: boolean, errors: string[] }' },
            { method: 'POST', path: '/api/code/save', desc: '保存并测试', body: '{ filename: string, code: string, overwrite?: boolean }', resp: '{ success: boolean, path: string, test_output: string }' },
            { method: 'GET', path: '/api/code/files', desc: '列出策略文件', body: '(无)', resp: '[{ filename, size, modified }]' },
            { method: 'GET', path: '/api/code/files/{filename}', desc: '读取策略文件', body: '(无)', resp: '{ filename, code }' },
            { method: 'DELETE', path: '/api/code/files/{filename}', desc: '删除策略文件', body: '(无)', resp: '{ status: "deleted" }' },
          ].map(a => (
            <div key={a.path + a.method} style={{ marginBottom: '10px', padding: '8px 12px', borderRadius: '6px', backgroundColor: 'var(--bg-secondary)', border: '1px solid var(--border)' }}>
              <div style={{ display: 'flex', gap: '8px', alignItems: 'center', marginBottom: '4px' }}>
                <span style={{ fontSize: '11px', fontWeight: 700, padding: '1px 6px', borderRadius: '3px', backgroundColor: a.method === 'GET' ? '#166534' : a.method === 'DELETE' ? '#7f1d1d' : '#1e40af', color: '#fff' }}>{a.method}</span>
                <code style={{ fontSize: '12px', color: 'var(--color-accent)' }}>{a.path}</code>
                <span style={{ fontSize: '11px', color: 'var(--text-secondary)' }}>— {a.desc}</span>
              </div>
              <div style={{ fontSize: '11px', color: 'var(--text-secondary)' }}>
                <div><b>请求:</b> <code>{a.body}</code></div>
                <div><b>响应:</b> <code>{a.resp}</code></div>
              </div>
            </div>
          ))}

          {/* Chat */}
          <div style={h2s}>AI 对话 API</div>
          {[
            { method: 'POST', path: '/api/chat/send', desc: 'AI 对话 (SSE 流式)', body: '{ messages: [{role,content}], editor_code?: string }', resp: 'SSE: event:content|tool_start|tool_result|done' },
            { method: 'GET', path: '/api/chat/status', desc: 'LLM 状态检查', body: '(无)', resp: '{ available: boolean, provider: string, model: string }' },
          ].map(a => (
            <div key={a.path + a.method} style={{ marginBottom: '10px', padding: '8px 12px', borderRadius: '6px', backgroundColor: 'var(--bg-secondary)', border: '1px solid var(--border)' }}>
              <div style={{ display: 'flex', gap: '8px', alignItems: 'center', marginBottom: '4px' }}>
                <span style={{ fontSize: '11px', fontWeight: 700, padding: '1px 6px', borderRadius: '3px', backgroundColor: a.method === 'GET' ? '#166534' : '#1e40af', color: '#fff' }}>{a.method}</span>
                <code style={{ fontSize: '12px', color: 'var(--color-accent)' }}>{a.path}</code>
                <span style={{ fontSize: '11px', color: 'var(--text-secondary)' }}>— {a.desc}</span>
              </div>
              <div style={{ fontSize: '11px', color: 'var(--text-secondary)' }}>
                <div><b>请求:</b> <code>{a.body}</code></div>
                <div><b>响应:</b> <code>{a.resp}</code></div>
              </div>
            </div>
          ))}

          <div style={h2s}>错误码</div>
          <table style={tbl}>
            <thead><tr><th style={ths}>HTTP 状态码</th><th style={ths}>含义</th><th style={ths}>常见原因</th></tr></thead>
            <tbody>
              <tr><td style={tds}>200</td><td style={tds}>成功</td><td style={tds}>—</td></tr>
              <tr><td style={tds}>404</td><td style={tds}>未找到</td><td style={tds}>股票数据不存在 / 实验 run_id 不存在</td></tr>
              <tr><td style={tds}>422</td><td style={tds}>参数校验失败</td><td style={tds}>参数类型错误 / 范围越界 / int 传了 float</td></tr>
              <tr><td style={tds}>503</td><td style={tds}>服务不可用</td><td style={tds}>数据源 API 不可达 / LLM 未配置</td></tr>
            </tbody>
          </table>
        </>}

        {/* ================================================================ */}
        {/*  9. AI 助手                                                       */}
        {/* ================================================================ */}
        {active === 'ai' && <>
          <h1 style={{ fontSize: '20px', fontWeight: 700, marginBottom: '16px' }}>AI 助手使用指南</h1>

          <div style={h2s}>工作原理</div>
          <p style={ps}>AI 助手是一个具有工具调用能力的 LLM Agent。完整流程如下：</p>
          <pre style={code}>{`用户消息 + 编辑器代码 (上下文)
      ↓
系统提示词 (含平台知识)
      ↓
LLM 推理 → 决定是否调用工具
      ↓
┌── 纯文字回复 → 直接返回给用户
└── 工具调用 → 执行工具 → 将结果返回给 LLM → LLM 生成最终回复
      ↓
SSE 流式输出到前端`}</pre>

          <div style={h2s}>AI 可以做什么</div>
          <table style={tbl}>
            <thead><tr><th style={ths}>能力</th><th style={ths}>示例提示词</th></tr></thead>
            <tbody>
              <tr><td style={tds}>创建策略</td><td style={tds}>"帮我写一个 MACD 金叉策略"</td></tr>
              <tr><td style={tds}>修改代码</td><td style={tds}>"把超卖阈值改成25，加一个 ATR 止损"</td></tr>
              <tr><td style={tds}>查看策略列表</td><td style={tds}>"列出所有可用策略"</td></tr>
              <tr><td style={tds}>读取源码</td><td style={tds}>"看一下 MACrossStrategy 的代码"</td></tr>
              <tr><td style={tds}>运行回测</td><td style={tds}>"用 000001.SZ 回测 2020-2024"</td></tr>
              <tr><td style={tds}>查看实验</td><td style={tds}>"最近的实验结果怎么样"</td></tr>
              <tr><td style={tds}>解释指标</td><td style={tds}>"什么是 Sharpe Ratio" / "为什么 Gate 没过"</td></tr>
            </tbody>
          </table>

          <div style={h2s}>工具列表详解</div>
          <table style={tbl}>
            <thead><tr><th style={ths}>工具名</th><th style={ths}>说明</th><th style={ths}>权限</th><th style={ths}>输入/输出</th></tr></thead>
            <tbody>
              <tr><td style={{...tds, fontFamily:'monospace'}}>list_strategies</td><td style={tds}>列出已注册策略及参数 schema</td><td style={tds}>只读</td><td style={tds}>无输入 → 策略名/参数列表</td></tr>
              <tr><td style={{...tds, fontFamily:'monospace'}}>list_factors</td><td style={tds}>列出可用因子及参数</td><td style={tds}>只读</td><td style={tds}>无输入 → 因子列表</td></tr>
              <tr><td style={{...tds, fontFamily:'monospace'}}>read_source</td><td style={tds}>读取策略/因子源码</td><td style={tds}>只读</td><td style={tds}>文件路径 → 源码文本</td></tr>
              <tr><td style={{...tds, fontFamily:'monospace'}}>create_strategy</td><td style={tds}>创建策略文件 + 自动 Contract Test</td><td style={tds}>写入 strategies/</td><td style={tds}>类名+代码 → 保存结果+测试输出</td></tr>
              <tr><td style={{...tds, fontFamily:'monospace'}}>update_strategy</td><td style={tds}>更新现有策略 + 自动 Contract Test</td><td style={tds}>写入 strategies/</td><td style={tds}>文件名+代码 → 保存结果+测试输出</td></tr>
              <tr><td style={{...tds, fontFamily:'monospace'}}>run_backtest</td><td style={tds}>执行单次回测</td><td style={tds}>执行（不修改数据）</td><td style={tds}>策略+参数+股票 → 回测结果</td></tr>
              <tr><td style={{...tds, fontFamily:'monospace'}}>run_experiment</td><td style={tds}>完整实验链路</td><td style={tds}>执行 + 持久化</td><td style={tds}>策略+参数+股票 → 实验报告</td></tr>
              <tr><td style={{...tds, fontFamily:'monospace'}}>list_experiments</td><td style={tds}>最近实验列表</td><td style={tds}>只读</td><td style={tds}>无输入 → 实验列表</td></tr>
              <tr><td style={{...tds, fontFamily:'monospace'}}>explain_metrics</td><td style={tds}>实验详情 + Gate 失败原因</td><td style={tds}>只读</td><td style={tds}>run_id → 详细指标+Gate 原因</td></tr>
            </tbody>
          </table>

          <div style={h2s}>LLM 提供商配置</div>
          <table style={tbl}>
            <thead><tr><th style={ths}>提供商</th><th style={ths}>环境变量</th><th style={ths}>推荐模型</th><th style={ths}>特点</th></tr></thead>
            <tbody>
              <tr><td style={tds}>DeepSeek</td><td style={{...tds, fontFamily:'monospace'}}>DEEPSEEK_API_KEY</td><td style={tds}>deepseek-chat</td><td style={tds}>国内直连，性价比高，代码能力强</td></tr>
              <tr><td style={tds}>Qwen (通义千问)</td><td style={{...tds, fontFamily:'monospace'}}>QWEN_API_KEY</td><td style={tds}>qwen-plus</td><td style={tds}>阿里云，中文能力强</td></tr>
              <tr><td style={tds}>OpenAI</td><td style={{...tds, fontFamily:'monospace'}}>OPENAI_API_KEY</td><td style={tds}>gpt-4o-mini</td><td style={tds}>需要网络代理，英文能力最强</td></tr>
              <tr><td style={tds}>本地模型</td><td style={{...tds, fontFamily:'monospace'}}>—</td><td style={tds}>—</td><td style={tds}>通过 local provider 接入 Ollama 等</td></tr>
            </tbody>
          </table>

          <div style={h3s}>配置方法</div>
          <pre style={code}>{`# 1. 在 .env 中设置 API Key
DEEPSEEK_API_KEY=sk-your-key-here

# 2. 在 configs/default.yaml 中配置 provider（可选）
llm:
  provider: deepseek    # deepseek | qwen | openai | local
  model: deepseek-chat
  temperature: 0.3      # 越低越确定性，越高越有创意`}</pre>

          <div style={h2s}>有效提示词技巧</div>
          <table style={tbl}>
            <thead><tr><th style={ths}>场景</th><th style={ths}>好的提示词</th><th style={ths}>差的提示词</th></tr></thead>
            <tbody>
              <tr><td style={tds}>创建策略</td><td style={tds}>"写一个 RSI 超卖反转策略，period 参数化，默认14，范围5-50"</td><td style={tds}>"写个策略"</td></tr>
              <tr><td style={tds}>修改代码</td><td style={tds}>"在当前代码基础上加一个 2 倍 ATR 止损"</td><td style={tds}>"加止损"</td></tr>
              <tr><td style={tds}>回测</td><td style={tds}>"用平安银行 000001.SZ 回测 2020-2024"</td><td style={tds}>"跑一下"</td></tr>
            </tbody>
          </table>

          <div style={h2s}>已知限制</div>
          <div style={warn}>
            <p><b>列名大小写</b> — LLM 有时会生成 <code>data["RSI_14"]</code> 而非 <code>data["rsi_14"]</code>。如果 Contract Test 报 KeyError，提醒 AI "因子列名是小写的"。</p>
            <p style={{ marginTop: '4px' }}><b>BOLL 列名</b> — AI 容易写成 <code>boll_lower</code> 而非 <code>boll_lower_20</code>。需要指出 "BOLL 列名含 period 后缀"。</p>
            <p style={{ marginTop: '4px' }}><b>重试</b> — 如果第一次生成的代码有错误，直接告诉 AI 错误信息，它通常能修正。</p>
          </div>

          <div style={h2s}>多会话管理</div>
          <p style={ps}>点击对话面板左上角按钮查看对话列表。点击 + 新建对话。所有对话自动保存在浏览器 localStorage 中，刷新页面不会丢失。每个对话独立维护消息历史。</p>

          <div style={h2s}>编辑器代码注入</div>
          <p style={ps}>当你在代码编辑器中有代码时，AI 助手能自动看到编辑器中的代码作为上下文。这意味着你可以：</p>
          <ol style={{ paddingLeft: '20px', margin: '6px 0', lineHeight: '1.8' }}>
            <li>先在编辑器中打开一个策略文件</li>
            <li>然后对 AI 说 "在当前代码的基础上加一个 ATR 止损"</li>
            <li>AI 会基于编辑器中的代码进行修改</li>
          </ol>
        </>}

        {/* ================================================================ */}
        {/*  10. 完整示例                                                     */}
        {/* ================================================================ */}
        {active === 'examples' && <>
          <h1 style={{ fontSize: '20px', fontWeight: 700, marginBottom: '16px' }}>完整示例</h1>

          {/* 示例 1 */}
          <div style={h2s}>示例 1：RSI 超卖反转策略</div>
          <div style={h3s}>策略思路</div>
          <p style={ps}>RSI 是经典的超买超卖指标。当 RSI 跌到 30 以下时，市场可能过度恐慌，存在反弹机会。当 RSI 涨到 70 以上时，市场可能过度乐观，是获利了结的时机。使用 forward-fill 保持持仓，避免频繁交易。</p>
          <div style={h3s}>适用场景</div>
          <p style={ps}>震荡市效果好（价格反复在区间内波动）。强趋势市可能过早卖出或抄底被套。适合中长线。</p>
          <pre style={code}>{`from ez.strategy import Strategy
from ez.factor import Factor
from ez.factor.builtin.technical import RSI
import pandas as pd

class RSIReversalStrategy(Strategy):
    def __init__(self, period: int = 14, oversold: float = 30.0, overbought: float = 70.0):
        self.period = period
        self.oversold = oversold
        self.overbought = overbought

    @classmethod
    def get_description(cls) -> str:
        return "RSI 超卖反转: RSI < 超卖阈值买入，> 超买阈值卖出"

    @classmethod
    def get_parameters_schema(cls) -> dict:
        return {
            "period":     {"type": "int",   "default": 14, "min": 5,  "max": 50, "label": "RSI 周期"},
            "oversold":   {"type": "float", "default": 30, "min": 10, "max": 40, "label": "超卖阈值"},
            "overbought": {"type": "float", "default": 70, "min": 60, "max": 90, "label": "超买阈值"},
        }

    def required_factors(self) -> list[Factor]:
        return [RSI(period=self.period)]

    def generate_signals(self, data: pd.DataFrame) -> pd.Series:
        rsi = data[f"rsi_{self.period}"]
        signal = pd.Series(0.0, index=data.index)
        signal[rsi < self.oversold] = 1.0       # 超卖 → 买入
        # 在非超买超卖区间保持当前仓位
        signal = signal.replace(0.0, pd.NA).ffill().fillna(0.0)
        signal[rsi > self.overbought] = 0.0     # 超买 → 卖出
        return signal`}</pre>
          <div style={note}>参数优化建议：period 范围 6-21，oversold 范围 20-35，overbought 范围 65-80。在参数搜索中使用 Grid Search，步长 period=2, oversold=5, overbought=5。</div>

          {/* 示例 2 */}
          <div style={h2s}>示例 2：双均线交叉策略</div>
          <div style={h3s}>策略思路</div>
          <p style={ps}>当短期均线上穿长期均线（金叉），说明短期趋势转强，买入。当短期均线下穿长期均线（死叉），说明短期趋势转弱，卖出。这是最经典的趋势跟踪策略。</p>
          <div style={h3s}>适用场景</div>
          <p style={ps}>趋势明显的市场效果好。震荡市会频繁发出假信号。快线和慢线周期差距越大，信号越少但越可靠。</p>
          <pre style={code}>{`from ez.strategy import Strategy
from ez.factor import Factor
from ez.factor.builtin.technical import MA
import pandas as pd

class DualMACross(Strategy):
    def __init__(self, fast: int = 5, slow: int = 20):
        self.fast = fast
        self.slow = slow

    @classmethod
    def get_description(cls) -> str:
        return "双均线交叉: 快线上穿慢线买入(金叉)，下穿卖出(死叉)"

    @classmethod
    def get_parameters_schema(cls) -> dict:
        return {
            "fast": {"type": "int", "default": 5,  "min": 2,  "max": 60,  "label": "快线周期"},
            "slow": {"type": "int", "default": 20, "min": 10, "max": 250, "label": "慢线周期"},
        }

    def required_factors(self) -> list[Factor]:
        return [MA(period=self.fast), MA(period=self.slow)]

    def generate_signals(self, data: pd.DataFrame) -> pd.Series:
        fast_col = f"ma_{self.fast}"
        slow_col = f"ma_{self.slow}"
        # 快线在慢线上方 → 满仓，否则空仓
        return (data[fast_col] > data[slow_col]).astype(float)`}</pre>
          <div style={note}>参数优化建议：fast 范围 3-20，slow 范围 20-120。保持 slow {'>'} fast * 2 避免交叉太频繁。经典组合：(5,20), (10,30), (20,60)。</div>

          {/* 示例 3 */}
          <div style={h2s}>示例 3：布林带回归 + ATR 止损</div>
          <div style={h3s}>策略思路</div>
          <p style={ps}>价格跌破布林带下轨时，可能处于超卖状态，买入等待回归均值。涨到中轨时止盈，同时设置 ATR 动态止损防止极端损失。ATR 止损会随市场波动性自适应调整。</p>
          <div style={h3s}>适用场景</div>
          <p style={ps}>震荡市和均值回归型股票效果好。趋势性下跌中可能反复触发买入信号（ATR 止损帮助控制损失）。使用了逐 bar 循环，可以实现复杂的状态逻辑。</p>
          <pre style={code}>{`from ez.strategy import Strategy
from ez.factor import Factor
from ez.factor.builtin.technical import BOLL, ATR
import pandas as pd

class BollATRStrategy(Strategy):
    def __init__(self, boll_period: int = 20, atr_period: int = 14, atr_mult: float = 2.0):
        self.boll_period = boll_period
        self.atr_period = atr_period
        self.atr_mult = atr_mult

    @classmethod
    def get_description(cls) -> str:
        return "布林带回归 + ATR 动态止损: 跌破下轨买入，中轨止盈或 ATR 止损"

    @classmethod
    def get_parameters_schema(cls) -> dict:
        return {
            "boll_period": {"type": "int",   "default": 20,  "min": 10, "max": 60, "label": "BOLL 周期"},
            "atr_period":  {"type": "int",   "default": 14,  "min": 5,  "max": 30, "label": "ATR 周期"},
            "atr_mult":    {"type": "float", "default": 2.0, "min": 0.5,"max": 5,  "label": "ATR 止损倍数"},
        }

    def required_factors(self) -> list[Factor]:
        return [BOLL(period=self.boll_period), ATR(period=self.atr_period)]

    def generate_signals(self, data: pd.DataFrame) -> pd.Series:
        price = data["adj_close"]
        lower = data[f"boll_lower_{self.boll_period}"]
        middle = data[f"boll_mid_{self.boll_period}"]
        atr = data[f"atr_{self.atr_period}"]

        signal = pd.Series(0.0, index=data.index)
        entry_price = 0.0

        for i in range(len(data)):
            if pd.isna(lower.iloc[i]) or pd.isna(atr.iloc[i]):
                continue
            p = price.iloc[i]
            prev_sig = signal.iloc[i - 1] if i > 0 else 0
            if prev_sig == 0:
                # 空仓：价格跌破下轨 → 买入
                if p < lower.iloc[i]:
                    signal.iloc[i] = 1.0
                    entry_price = p
            else:
                # 持仓：价格涨到中轨 → 止盈，或跌破 ATR 止损 → 止损
                stop_loss = entry_price - self.atr_mult * atr.iloc[i]
                if p >= middle.iloc[i] or p <= stop_loss:
                    signal.iloc[i] = 0.0
                else:
                    signal.iloc[i] = 1.0
        return signal`}</pre>
          <div style={note}>参数优化建议：boll_period 15-30，atr_period 10-20，atr_mult 1.5-3.0。atr_mult 越大止损越宽松，减少被假突破止损的概率但可能承受更大亏损。</div>

          {/* 示例 4 */}
          <div style={h2s}>示例 4：多因子策略 (RSI + MACD 联合确认)</div>
          <div style={h3s}>策略思路</div>
          <p style={ps}>单一指标容易产生假信号。同时使用 RSI 超卖条件和 MACD 金叉条件做确认，两个条件同时满足时买入，可以有效过滤噪音。卖出使用 RSI 超买或 MACD 死叉（任一触发即卖）。</p>
          <div style={h3s}>适用场景</div>
          <p style={ps}>交易频率较低，信号质量高。适合不想频繁操作的中长线投资者。在震荡市和趋势市都有一定的适应性。</p>
          <pre style={code}>{`from ez.strategy import Strategy
from ez.factor import Factor
from ez.factor.builtin.technical import RSI, MACD
import pandas as pd

class RSIMACDStrategy(Strategy):
    def __init__(self, rsi_period: int = 14, rsi_oversold: float = 35.0, rsi_overbought: float = 65.0):
        self.rsi_period = rsi_period
        self.rsi_oversold = rsi_oversold
        self.rsi_overbought = rsi_overbought

    @classmethod
    def get_description(cls) -> str:
        return "RSI + MACD 双确认: 两个信号同时满足才交易，过滤噪音"

    @classmethod
    def get_parameters_schema(cls) -> dict:
        return {
            "rsi_period":     {"type": "int",   "default": 14, "min": 5,  "max": 30, "label": "RSI 周期"},
            "rsi_oversold":   {"type": "float", "default": 35, "min": 15, "max": 45, "label": "RSI 超卖"},
            "rsi_overbought": {"type": "float", "default": 65, "min": 55, "max": 85, "label": "RSI 超买"},
        }

    def required_factors(self) -> list[Factor]:
        return [RSI(period=self.rsi_period), MACD()]

    def generate_signals(self, data: pd.DataFrame) -> pd.Series:
        rsi = data[f"rsi_{self.rsi_period}"]
        macd_line = data["macd_line"]
        macd_signal = data["macd_signal"]

        # 买入条件: RSI 超卖 且 MACD 金叉
        buy = (rsi < self.rsi_oversold) & (macd_line > macd_signal)
        # 卖出条件: RSI 超买 或 MACD 死叉
        sell = (rsi > self.rsi_overbought) | (macd_line < macd_signal)

        signal = pd.Series(0.0, index=data.index)
        signal[buy] = 1.0
        signal = signal.replace(0.0, pd.NA).ffill().fillna(0.0)
        signal[sell] = 0.0
        return signal`}</pre>
          <div style={note}>参数优化建议：rsi_period 10-20, rsi_oversold 25-40, rsi_overbought 60-80。MACD 参数保持默认 (12,26,9) 即可，不建议同时优化太多参数（维度诅咒）。</div>

          {/* 示例 5 */}
          <div style={h2s}>示例 5：动量策略 + 动态仓位</div>
          <div style={h3s}>策略思路</div>
          <p style={ps}>利用动量因子作为信号权重而非简单的 0/1 信号。动量越强，仓位越重；动量为负则清仓。这样在强势行情中加大仓位，在弱势行情中自动减仓，实现动态仓位管理。</p>
          <div style={h3s}>适用场景</div>
          <p style={ps}>趋势明显的市场效果最好。相比简单的满仓/空仓策略，动态仓位可以更灵活地管理风险。适合愿意承受一定波动的投资者。</p>
          <pre style={code}>{`from ez.strategy import Strategy
from ez.factor import Factor
from ez.factor.builtin.technical import Momentum, MA
import pandas as pd
import numpy as np

class DynamicMomentumStrategy(Strategy):
    def __init__(self, mom_period: int = 20, ma_period: int = 60):
        self.mom_period = mom_period
        self.ma_period = ma_period

    @classmethod
    def get_description(cls) -> str:
        return "动量策略 + 动态仓位: 动量值决定仓位权重，趋势越强仓位越重"

    @classmethod
    def get_parameters_schema(cls) -> dict:
        return {
            "mom_period": {"type": "int", "default": 20, "min": 5,  "max": 60,  "label": "动量周期"},
            "ma_period":  {"type": "int", "default": 60, "min": 20, "max": 250, "label": "趋势 MA 周期"},
        }

    def required_factors(self) -> list[Factor]:
        return [Momentum(period=self.mom_period), MA(period=self.ma_period)]

    def generate_signals(self, data: pd.DataFrame) -> pd.Series:
        mom = data[f"momentum_{self.mom_period}"]
        price = data["adj_close"]
        ma = data[f"ma_{self.ma_period}"]

        # 基础条件: 价格在长期均线上方（大趋势向上）
        trend_up = price > ma

        # 动态仓位: 将正动量归一化到 0-1
        # mom > 0 且在上升趋势中 → 仓位 = 归一化动量
        # mom <= 0 或下降趋势 → 空仓
        signal = pd.Series(0.0, index=data.index)
        positive_mom = mom.clip(lower=0)
        # 用 rolling 窗口归一化，避免未来函数
        roll_max = positive_mom.rolling(self.mom_period * 2, min_periods=1).max()
        roll_max = roll_max.replace(0, np.nan)
        normalized = (positive_mom / roll_max).fillna(0.0)

        signal[trend_up] = normalized[trend_up]
        return signal.clip(0.0, 1.0)`}</pre>
          <div style={note}>参数优化建议：mom_period 10-40, ma_period 40-120。mom_period 较短时信号更灵敏但噪音更多。ma_period 是趋势过滤器，值越大越保守。</div>
        </>}

        {/* ================================================================ */}
        {/*  11. 常见问题                                                     */}
        {/* ================================================================ */}
        {active === 'faq' && <>
          <h1 style={{ fontSize: '20px', fontWeight: 700, marginBottom: '16px' }}>常见问题 (FAQ)</h1>

          {[
            {
              q: '策略保存后在回测里看不到',
              a: '检查以下几点：\n1. Contract Test 是否通过 — 保存时看终端输出\n2. 类名是否正确 — 策略列表显示的是 "模块路径.类名"\n3. 是否有语法错误导致 import 失败 — 检查 Python 语法\n4. 是否继承了 Strategy — 必须 from ez.strategy import Strategy\n5. 尝试重启后端 — ./scripts/stop.sh && ./scripts/start.sh',
            },
            {
              q: '回测结果 0 笔交易',
              a: '最常见的原因：\n1. generate_signals() 返回的信号全部是 0 — 打印信号看看有没有非零值\n2. 因子列名大小写错误 — 必须是 rsi_14 而非 RSI_14，是 macd_line 而非 MACD_line\n3. BOLL 列名格式 — 是 boll_lower_20 而非 boll_lower（注意带 period 后缀）\n4. 数据天数不够 — warmup 吃掉了所有数据（如 MA(250) 需要 250 天数据）\n5. 开了 A 股规则但资金太少 — 不够买 100 股整手\n6. 信号条件太严格 — 在 5 年数据中可能只有 1-2 天满足条件',
            },
            {
              q: 'AI 助手提示"未配置"或无法使用',
              a: '需要配置 LLM API Key：\n1. 在项目根目录创建 .env 文件\n2. 添加 DEEPSEEK_API_KEY=sk-你的key（推荐 DeepSeek）\n3. 重启后端 ./scripts/stop.sh && ./scripts/start.sh\n4. 检查 http://localhost:8000/api/chat/status 是否返回 available: true',
            },
            {
              q: 'Contract Test 失败',
              a: '常见错误及解决方法：\n1. ImportError — 检查 import 路径是否正确\n2. KeyError (列名) — 因子列名必须小写，如 rsi_14\n3. 信号范围超出 [0, 1] — 不支持 -1 做空信号\n4. get_parameters_schema 格式错误 — 每个参数必须有 type, default, min, max\n5. required_factors 返回非 Factor 对象 — 确保返回 Factor 实例列表\n6. 使用了禁止的 import — 如 os, sys, subprocess 等被安全检查拦截',
            },
            {
              q: '为什么回测结果和实际交易不一样',
              a: '常见差异原因：\n1. 复权数据 — 回测用前复权价格，实际是真实价格，确认数据源一致\n2. 交易成本 — 调高 commission_rate 和 slippage_rate 使回测更贴近实际\n3. 滑点 — 实盘可能遇到更大的价格冲击，尤其是小盘股\n4. A 股规则 — 启用 T+1、涨跌停、整手限制来模拟真实限制\n5. 信号偏移 — 引擎自动做了 T+1 偏移，信号不会在当天执行\n6. 开盘价 vs 收盘价 — 实盘可能以收盘价下单但引擎用开盘价执行\n7. 停牌 — 回测跳过 NaN 价格日，实际可能影响你的交易计划',
            },
            {
              q: '如何添加自定义因子',
              a: '目前需要手动添加：\n1. 在 ez/factor/builtin/ 下创建新文件\n2. 继承 Factor 基类，实现 name, warmup_period, compute()\n3. 在 ez/api/routes/factors.py 的 _FACTOR_MAP 中注册\n4. 运行 pytest tests/test_factor/test_factor_contract.py 验证\n5. 重启后端后即可在策略中使用',
            },
            {
              q: '参数搜索结果显示 "pre-filtered"',
              a: 'pre-filter 阶段淘汰了该参数组合。解决方法：\n1. 放宽 pre-filter 阈值（默认是 Sharpe > -1, 交易 >= 5, 收益 > -50%）\n2. 检查参数范围是否合理 — 极端参数容易被淘汰\n3. 如果大量参数被淘汰，可能是策略本身逻辑有问题\n4. 检查数据日期范围是否足够长',
            },
            {
              q: 'Gate 没过怎么办',
              a: 'Gate 评分会告诉你哪条规则没通过：\n- min_sharpe 没过 → 策略收益风险比不够，考虑优化信号逻辑\n- max_drawdown 没过 → 最大回撤过大，考虑加止损或减小仓位\n- min_trades 没过 → 交易太少，放宽信号条件或增加数据时间范围\n- significance 没过 → 收益可能是运气，考虑增加数据量或简化策略\n- overfitting 没过 → 过拟合严重，减少参数数量或使用更稳健的策略逻辑',
            },
            {
              q: '数据获取失败',
              a: '按数据源检查：\n1. Tushare — 检查 TUSHARE_TOKEN 是否配置且有效\n2. FMP — 检查 FMP_API_KEY 是否配置\n3. 网络问题 — 确认能访问外部 API\n4. 股票代码格式 — A 股用 000001.SZ/600519.SH，美股直接用 AAPL\n5. 日期范围 — 确认股票在该日期范围内已上市',
            },
            {
              q: '后端启动失败',
              a: '常见排查步骤：\n1. 检查端口占用 — lsof -i :8000\n2. 先停止旧进程 — ./scripts/stop.sh\n3. 检查 Python 版本 — 需要 3.12+\n4. 重装依赖 — pip install -e . --no-build-isolation\n5. 查看日志 — 终端输出的错误信息',
            },
          ].map((item, idx) => (
            <div key={idx} style={{ marginBottom: '20px' }}>
              <div style={{ ...h2s, fontSize: '14px' }}>Q: {item.q}</div>
              <pre style={{ ...code, whiteSpace: 'pre-wrap', lineHeight: '1.8' }}>{item.a}</pre>
            </div>
          ))}
        </>}

      </div>
    </div>
  )
}
