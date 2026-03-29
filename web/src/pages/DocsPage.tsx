import { useState } from 'react'

const sections = [
  { id: 'quickstart', label: '快速开始' },
  { id: 'strategy', label: '策略开发' },
  { id: 'factors', label: '因子参考' },
  { id: 'signals', label: '信号与引擎' },
  { id: 'market-rules', label: 'A股规则' },
  { id: 'api', label: 'API 参考' },
  { id: 'ai', label: 'AI 助手' },
  { id: 'examples', label: '完整示例' },
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

        {active === 'quickstart' && <>
          <h1 style={{ fontSize: '20px', fontWeight: 700, marginBottom: '16px' }}>快速开始</h1>
          <p style={ps}>3 步创建并运行一个交易策略：</p>
          <div style={h2s}>第一步：创建策略文件</div>
          <p style={ps}>进入 <b>代码编辑器</b> Tab，选择 "策略" → 输入类名（如 <code>RSIReversal</code>） → 点击 "生成模板"。系统会生成一个可运行的策略骨架。</p>
          <div style={h2s}>第二步：编写策略逻辑</div>
          <p style={ps}>修改 <code>generate_signals()</code> 方法，返回一个 0~1 的信号序列。例如：</p>
          <pre style={code}>{`def generate_signals(self, data: pd.DataFrame) -> pd.Series:
    rsi = data[f"rsi_{self.period}"]
    # RSI < 30 时满仓买入，否则空仓
    return (rsi < 30).astype(float)`}</pre>
          <p style={ps}>点击 "保存并测试" → 系统自动运行 Contract Test 验证策略是否符合接口规范。</p>
          <div style={h2s}>第三步：回测</div>
          <p style={ps}>切换到 <b>看板</b> Tab → 在回测面板选择你的策略 → 点击 "运行"。</p>
          <p style={ps}>或切换到 <b>实验</b> Tab 运行完整实验（回测 + 前推验证 + 显著性检验 + Gate 自动评分）。</p>
          <div style={h2s}>AI 助手快捷方式</div>
          <p style={ps}>在代码编辑器中点击 "AI助手" 按钮，直接用自然语言描述策略，AI 会自动写代码到编辑器中。</p>
          <pre style={code}>{`示例提示词:
"帮我写一个 RSI 超卖反转策略，RSI < 30 买入，> 70 卖出"
"修改当前代码，加一个 ATR 止损"
"把 MA 周期改成参数化的，默认20，范围5到60"`}</pre>
        </>}

        {active === 'strategy' && <>
          <h1 style={{ fontSize: '20px', fontWeight: 700, marginBottom: '16px' }}>策略开发</h1>

          <div style={h2s}>Strategy 基类</div>
          <p style={ps}>所有策略必须继承 <code>Strategy</code>，实现以下 3 个方法：</p>
          <pre style={code}>{`from ez.strategy import Strategy
from ez.factor import Factor
from ez.factor.builtin.technical import MA, RSI
import pandas as pd

class MyStrategy(Strategy):
    def __init__(self, period: int = 14):
        self.period = period

    @classmethod
    def get_parameters_schema(cls) -> dict[str, dict]:
        """参数定义 — 用于前端表单自动渲染"""
        ...

    def required_factors(self) -> list[Factor]:
        """声明依赖的因子 — 引擎自动计算并注入 data"""
        ...

    def generate_signals(self, data: pd.DataFrame) -> pd.Series:
        """核心逻辑 — 返回 0.0~1.0 的仓位信号"""
        ...`}</pre>

          <div style={h3s}>get_parameters_schema() 详解</div>
          <p style={ps}>返回字典，每个 key 是参数名，value 包含：</p>
          <table style={{ width: '100%', borderCollapse: 'collapse', margin: '8px 0' }}>
            <thead><tr><th style={ths}>字段</th><th style={ths}>类型</th><th style={ths}>说明</th><th style={ths}>示例</th></tr></thead>
            <tbody>
              <tr><td style={tds}><code>type</code></td><td style={tds}>string</td><td style={tds}>参数类型</td><td style={tds}><code>"int"</code> / <code>"float"</code></td></tr>
              <tr><td style={tds}><code>default</code></td><td style={tds}>number</td><td style={tds}>默认值</td><td style={tds}><code>14</code></td></tr>
              <tr><td style={tds}><code>min</code></td><td style={tds}>number</td><td style={tds}>最小值（前端校验）</td><td style={tds}><code>5</code></td></tr>
              <tr><td style={tds}><code>max</code></td><td style={tds}>number</td><td style={tds}>最大值（前端校验）</td><td style={tds}><code>50</code></td></tr>
              <tr><td style={tds}><code>label</code></td><td style={tds}>string</td><td style={tds}>显示名称</td><td style={tds}><code>"RSI 周期"</code></td></tr>
            </tbody>
          </table>
          <pre style={code}>{`@classmethod
def get_parameters_schema(cls) -> dict:
    return {
        "period":    {"type": "int",   "default": 14,  "min": 5,  "max": 50,  "label": "RSI 周期"},
        "threshold": {"type": "float", "default": 30.0,"min": 10, "max": 45,  "label": "超卖阈值"},
    }`}</pre>

          <div style={h3s}>required_factors() 详解</div>
          <p style={ps}>返回因子实例列表。引擎在调用 <code>generate_signals()</code> 前，会自动对每个因子执行 <code>compute(data)</code>，将计算结果列追加到 <code>data</code> 中。</p>
          <pre style={code}>{`def required_factors(self) -> list[Factor]:
    return [
        RSI(period=self.period),      # 追加列 rsi_14
        MA(period=20),                # 追加列 ma_20
    ]
# generate_signals 收到的 data 已包含 rsi_14, ma_20 列`}</pre>

          <div style={h3s}>generate_signals() 详解</div>
          <p style={ps}>输入：<code>data</code> 是 DataFrame，包含 OHLCV + 所有因子列。</p>
          <p style={ps}>输出：<code>pd.Series</code>，与 data 等长，值域 [0.0, 1.0]。</p>
          <table style={{ width: '100%', borderCollapse: 'collapse', margin: '8px 0' }}>
            <thead><tr><th style={ths}>信号值</th><th style={ths}>含义</th><th style={ths}>引擎行为</th></tr></thead>
            <tbody>
              <tr><td style={tds}><code>0.0</code></td><td style={tds}>空仓</td><td style={tds}>卖出全部持仓（如有）</td></tr>
              <tr><td style={tds}><code>1.0</code></td><td style={tds}>满仓</td><td style={tds}>用全部可用资金买入</td></tr>
              <tr><td style={tds}><code>0.5</code></td><td style={tds}>半仓</td><td style={tds}>调整仓位到 50% 资金</td></tr>
              <tr><td style={tds}><code>NaN</code></td><td style={tds}>无信号</td><td style={tds}>保持当前仓位不变</td></tr>
            </tbody>
          </table>

          <div style={h3s}>data 中可用的列</div>
          <table style={{ width: '100%', borderCollapse: 'collapse', margin: '8px 0' }}>
            <thead><tr><th style={ths}>列名</th><th style={ths}>说明</th></tr></thead>
            <tbody>
              {[['open','开盘价'],['high','最高价'],['low','最低价'],['close','收盘价（未复权）'],['adj_close','复权收盘价（推荐用这个）'],['volume','成交量'],['+ 因子列','由 required_factors 自动追加']].map(([k,v])=>(
                <tr key={k}><td style={{...tds, fontFamily:'monospace'}}>{k}</td><td style={tds}>{v}</td></tr>
              ))}
            </tbody>
          </table>

          <div style={h3s}>可选方法</div>
          <pre style={code}>{`@classmethod
def get_description(cls) -> str:
    """策略描述 — 显示在前端策略选择器下方"""
    return "RSI 超卖反转策略：RSI < 30 买入，> 70 卖出"`}</pre>
        </>}

        {active === 'factors' && <>
          <h1 style={{ fontSize: '20px', fontWeight: 700, marginBottom: '16px' }}>因子参考</h1>
          <p style={ps}>所有因子在 <code>ez.factor.builtin.technical</code> 中，通过 <code>required_factors()</code> 声明后自动计算。</p>
          {[
            { name: 'MA', cn: '移动平均线', params: 'period: int = 20', col: 'ma_{period}', desc: '简单移动平均。常用于趋势判断和交叉策略。', ex: 'MA(period=5), MA(period=20)  # 金叉/死叉' },
            { name: 'EMA', cn: '指数移动平均', params: 'period: int = 12', col: 'ema_{period}', desc: '指数加权移动平均，对近期数据赋予更高权重。反应更灵敏。', ex: 'EMA(period=12), EMA(period=26)  # 用于 MACD 手动计算' },
            { name: 'RSI', cn: '相对强弱指标', params: 'period: int = 14', col: 'rsi_{period}', desc: '衡量涨跌动能的震荡指标。0~100 范围，< 30 超卖，> 70 超买。', ex: 'RSI(period=14)  # data["rsi_14"] < 30 → 超卖信号' },
            { name: 'MACD', cn: 'MACD 指标', params: 'fast=12, slow=26, signal=9', col: 'macd, macd_signal, macd_hist', desc: '趋势跟踪 + 动量指标。macd 线上穿 signal 线为金叉。macd_hist 为柱状图。', ex: 'MACD()  # data["macd"] > data["macd_signal"] → 买入' },
            { name: 'BOLL', cn: '布林带', params: 'period=20, std_dev=2.0', col: 'boll_upper, boll_middle, boll_lower', desc: '基于标准差的通道指标。价格触及下轨可能超卖，触及上轨可能超买。', ex: 'BOLL(period=20, std_dev=2.0)  # data["adj_close"] < data["boll_lower"]' },
            { name: 'Momentum', cn: '动量', params: 'period: int = 20', col: 'momentum_{period}', desc: 'N 日收益率 (pct_change)。正值为上涨动量，负值为下跌动量。', ex: 'Momentum(period=20)  # data["momentum_20"] > 0 → 趋势向上' },
            { name: 'VWAP', cn: '成交量加权均价', params: 'period: int = 20', col: 'vwap_{period}', desc: '按成交量加权的平均价格。价格在 VWAP 上方为强势。', ex: 'VWAP(period=20)  # data["adj_close"] > data["vwap_20"]' },
            { name: 'OBV', cn: '能量潮', params: '(无参数)', col: 'obv', desc: '累计量能指标。价格上涨日加成交量，下跌日减成交量。', ex: 'OBV()  # 观察 OBV 趋势与价格趋势是否背离' },
            { name: 'ATR', cn: '平均真实波幅', params: 'period: int = 14', col: 'atr_{period}', desc: '衡量波动性的指标。可用于动态止损（如 2 倍 ATR 止损）。', ex: 'ATR(period=14)  # 止损价 = 买入价 - 2 * data["atr_14"]' },
          ].map(f => (
            <div key={f.name} style={{ marginBottom: '20px' }}>
              <div style={h2s}>{f.name} — {f.cn}</div>
              <p style={ps}>{f.desc}</p>
              <table style={{ width: '100%', borderCollapse: 'collapse', margin: '8px 0' }}>
                <tbody>
                  <tr><td style={{...tds, width: '120px', fontWeight: 600}}>参数</td><td style={{...tds, fontFamily: 'monospace'}}>{f.params}</td></tr>
                  <tr><td style={{...tds, fontWeight: 600}}>输出列名</td><td style={{...tds, fontFamily: 'monospace'}}>{f.col}</td></tr>
                </tbody>
              </table>
              <pre style={code}>{f.ex}</pre>
            </div>
          ))}
        </>}

        {active === 'signals' && <>
          <h1 style={{ fontSize: '20px', fontWeight: 700, marginBottom: '16px' }}>信号与回测引擎</h1>

          <div style={h2s}>引擎工作流程</div>
          <pre style={code}>{`数据加载 → 因子计算 → 信号生成 → 逐 bar 模拟交易
                                         ↓
                              信号变化时执行买卖
                              0→1: 买入 (用当日开盘价)
                              1→0: 卖出 (用当日开盘价)
                              不变: 持仓不动`}</pre>

          <div style={h2s}>交易成本</div>
          <table style={{ width: '100%', borderCollapse: 'collapse', margin: '8px 0' }}>
            <thead><tr><th style={ths}>参数</th><th style={ths}>默认值</th><th style={ths}>说明</th></tr></thead>
            <tbody>
              <tr><td style={tds}>手续费率</td><td style={tds}>0.03%</td><td style={tds}>每笔交易按成交金额计算</td></tr>
              <tr><td style={tds}>最低手续费</td><td style={tds}>5 元</td><td style={tds}>每笔交易不低于此金额</td></tr>
              <tr><td style={tds}>滑点率</td><td style={tds}>0%</td><td style={tds}>模拟买入价上浮/卖出价下浮</td></tr>
            </tbody>
          </table>

          <div style={h2s}>回测指标说明</div>
          <table style={{ width: '100%', borderCollapse: 'collapse', margin: '8px 0' }}>
            <thead><tr><th style={ths}>指标</th><th style={ths}>说明</th><th style={ths}>好的范围</th></tr></thead>
            <tbody>
              {[
                ['Sharpe Ratio', '风险调整收益（超额收益/波动率）', '> 1.0'],
                ['Total Return', '总收益率', '> 0'],
                ['Max Drawdown', '最大回撤（峰值到谷值的最大跌幅）', '< -20%'],
                ['Win Rate', '胜率（盈利交易占比）', '> 50%'],
                ['Profit Factor', '盈亏比（总盈利/总亏损）', '> 1.5'],
                ['Trade Count', '交易次数', '> 10（太少不可靠）'],
                ['Sortino Ratio', '下行风险调整收益（只惩罚亏损波动）', '> 1.0'],
                ['Alpha', '超额收益（相对于基准买入持有）', '> 0'],
                ['Beta', '与基准的相关性', '接近 0 为独立'],
              ].map(([k, v, g]) => (
                <tr key={k}><td style={{...tds, fontWeight: 600}}>{k}</td><td style={tds}>{v}</td><td style={{...tds, fontFamily: 'monospace'}}>{g}</td></tr>
              ))}
            </tbody>
          </table>

          <div style={h2s}>前推验证 (Walk-Forward)</div>
          <p style={ps}>将数据分成多段，每段用前 70% 训练、后 30% 测试。防止过拟合。</p>
          <table style={{ width: '100%', borderCollapse: 'collapse', margin: '8px 0' }}>
            <thead><tr><th style={ths}>指标</th><th style={ths}>说明</th></tr></thead>
            <tbody>
              <tr><td style={tds}>OOS Sharpe</td><td style={tds}>样本外 Sharpe（真实预测能力）</td></tr>
              <tr><td style={tds}>过拟合评分</td><td style={tds}>0~1，越高越可能过拟合。&lt; 0.3 稳健</td></tr>
              <tr><td style={tds}>样本内外衰减</td><td style={tds}>训练集 vs 测试集的 Sharpe 差异百分比</td></tr>
            </tbody>
          </table>

          <div style={h2s}>显著性检验</div>
          <p style={ps}>Monte Carlo 排列检验：随机打乱信号 1000 次，计算策略 Sharpe 超过随机结果的概率。</p>
          <p style={ps}><b>p &lt; 0.05</b>：策略收益显著（不太可能是运气）。<b>p &gt; 0.05</b>：不显著。</p>
        </>}

        {active === 'market-rules' && <>
          <h1 style={{ fontSize: '20px', fontWeight: 700, marginBottom: '16px' }}>A 股市场规则</h1>
          <p style={ps}>在实验面板勾选 "A股规则" 后，回测引擎会模拟真实 A 股交易限制：</p>
          <table style={{ width: '100%', borderCollapse: 'collapse', margin: '12px 0' }}>
            <thead><tr><th style={ths}>规则</th><th style={ths}>说明</th><th style={ths}>影响</th></tr></thead>
            <tbody>
              <tr><td style={{...tds, fontWeight: 600}}>T+1</td><td style={tds}>买入当天不能卖出，次日才可卖</td><td style={tds}>日内反转策略无法执行</td></tr>
              <tr><td style={{...tds, fontWeight: 600}}>涨跌停 10%</td><td style={tds}>涨停价不可买入，跌停价不可卖出</td><td style={tds}>追涨/割肉可能被拒</td></tr>
              <tr><td style={{...tds, fontWeight: 600}}>整手 100 股</td><td style={tds}>买卖必须是 100 股整数倍</td><td style={tds}>小资金可能无法分仓</td></tr>
            </tbody>
          </table>
          <p style={ps}>如果买入/卖出被拒（涨跌停或 T+1），引擎会在下一个交易日自动重试。</p>
        </>}

        {active === 'api' && <>
          <h1 style={{ fontSize: '20px', fontWeight: 700, marginBottom: '16px' }}>API 参考</h1>
          <p style={ps}>后端运行在 <code>http://localhost:8000</code>，所有接口前缀 <code>/api</code>。</p>

          {[
            { method: 'POST', path: '/api/backtest/run', desc: '单次回测', body: '{ strategy_name, strategy_params, symbol, market, period, start_date, end_date, commission_rate?, slippage_rate? }', resp: '{ metrics, equity_curve, benchmark_curve, trades, significance }' },
            { method: 'POST', path: '/api/backtest/walk-forward', desc: '前推验证', body: '{ ...同上, n_splits, train_ratio }', resp: '{ oos_metrics, overfitting_score, oos_equity_curve }' },
            { method: 'GET', path: '/api/backtest/strategies', desc: '策略列表', body: '(无)', resp: '[{ name, key, parameters, description }]' },
            { method: 'POST', path: '/api/experiments', desc: '运行完整实验', body: '{ strategy_name, symbol, start_date, end_date, run_wfo?, use_market_rules? }', resp: '{ run_id, status, sharpe_ratio, gate_passed, ... }' },
            { method: 'GET', path: '/api/experiments', desc: '实验记录列表', body: '?limit=50&offset=0', resp: '[{ run_id, strategy_name, sharpe_ratio, gate_passed, ... }]' },
            { method: 'POST', path: '/api/candidates/search', desc: '批量参数搜索', body: '{ strategy_name, param_ranges, symbol, mode:"grid"|"random" }', resp: '{ total_specs, ranked: [{ params, sharpe, gate_passed, fdr_adjusted_p }] }' },
            { method: 'POST', path: '/api/code/template', desc: '生成模板', body: '{ kind:"strategy"|"factor", class_name? }', resp: '{ code }' },
            { method: 'POST', path: '/api/code/save', desc: '保存并测试', body: '{ filename, code, overwrite? }', resp: '{ success, path, test_output }' },
            { method: 'POST', path: '/api/code/validate', desc: '语法检查', body: '{ code }', resp: '{ valid, errors }' },
            { method: 'POST', path: '/api/chat/send', desc: 'AI 对话 (SSE)', body: '{ messages, editor_code? }', resp: 'SSE: event:content/tool_start/tool_result/done' },
            { method: 'GET', path: '/api/chat/status', desc: 'LLM 状态', body: '(无)', resp: '{ available, provider, model }' },
          ].map(a => (
            <div key={a.path + a.method} style={{ marginBottom: '16px', padding: '10px 12px', borderRadius: '6px', backgroundColor: 'var(--bg-secondary)', border: '1px solid var(--border)' }}>
              <div style={{ display: 'flex', gap: '8px', alignItems: 'center', marginBottom: '6px' }}>
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
        </>}

        {active === 'ai' && <>
          <h1 style={{ fontSize: '20px', fontWeight: 700, marginBottom: '16px' }}>AI 助手使用指南</h1>

          <div style={h2s}>基本用法</div>
          <p style={ps}>在代码编辑器中点击 "AI助手" 按钮打开对话面板。AI 能看到当前编辑器中的代码。</p>

          <div style={h2s}>AI 可以做什么</div>
          <table style={{ width: '100%', borderCollapse: 'collapse', margin: '8px 0' }}>
            <thead><tr><th style={ths}>能力</th><th style={ths}>示例</th></tr></thead>
            <tbody>
              <tr><td style={tds}>创建策略</td><td style={tds}>"帮我写一个 MACD 金叉策略"</td></tr>
              <tr><td style={tds}>修改代码</td><td style={tds}>"把超卖阈值改成25，加一个止损"</td></tr>
              <tr><td style={tds}>查看策略列表</td><td style={tds}>"列出所有可用策略"</td></tr>
              <tr><td style={tds}>读取源码</td><td style={tds}>"看一下 MACrossStrategy 的代码"</td></tr>
              <tr><td style={tds}>运行回测</td><td style={tds}>"用 000001.SZ 回测 2020-2024"（需要你明确要求）</td></tr>
              <tr><td style={tds}>查看实验</td><td style={tds}>"最近的实验结果怎么样"</td></tr>
              <tr><td style={tds}>解释指标</td><td style={tds}>"什么是 Sharpe Ratio"</td></tr>
            </tbody>
          </table>

          <div style={h2s}>AI 工具列表</div>
          <table style={{ width: '100%', borderCollapse: 'collapse', margin: '8px 0' }}>
            <thead><tr><th style={ths}>工具名</th><th style={ths}>说明</th><th style={ths}>权限</th></tr></thead>
            <tbody>
              {[
                ['list_strategies', '列出已注册策略及参数', '只读'],
                ['list_factors', '列出可用因子', '只读'],
                ['read_source', '读取策略/因子源码', '只读 (strategies/ + builtin/)'],
                ['create_strategy', '创建策略文件 + 自动测试', '写入 strategies/'],
                ['update_strategy', '更新策略文件 + 自动测试', '写入 strategies/'],
                ['run_backtest', '执行单次回测', '执行（不修改数据）'],
                ['run_experiment', '完整实验链路', '执行 + 持久化'],
                ['list_experiments', '最近实验列表', '只读'],
                ['explain_metrics', '实验详情 + Gate 原因', '只读'],
              ].map(([n, d, p]) => (
                <tr key={n}><td style={{...tds, fontFamily: 'monospace'}}>{n}</td><td style={tds}>{d}</td><td style={tds}>{p}</td></tr>
              ))}
            </tbody>
          </table>

          <div style={h2s}>多会话管理</div>
          <p style={ps}>点击对话面板左上角 ☰ 按钮查看对话列表。点击 + 新建对话。所有对话自动保存在浏览器中（localStorage），刷新页面不会丢失。</p>

          <div style={h2s}>配置 LLM</div>
          <p style={ps}>在项目根目录 <code>.env</code> 文件中配置 API Key：</p>
          <pre style={code}>{`# DeepSeek (推荐，国内直连)
DEEPSEEK_API_KEY=sk-your-key-here

# 或在 configs/default.yaml 中配置:
llm:
  provider: deepseek    # deepseek | qwen | openai | local
  model: deepseek-chat
  temperature: 0.3`}</pre>
        </>}

        {active === 'examples' && <>
          <h1 style={{ fontSize: '20px', fontWeight: 700, marginBottom: '16px' }}>完整示例</h1>

          <div style={h2s}>示例 1：RSI 超卖反转策略</div>
          <p style={ps}>当 RSI 跌到 30 以下时买入（超卖），涨到 70 以上时卖出（超买）。</p>
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

          <div style={h2s}>示例 2：双均线交叉策略</div>
          <p style={ps}>短期均线上穿长期均线时买入（金叉），下穿时卖出（死叉）。</p>
          <pre style={code}>{`from ez.strategy import Strategy
from ez.factor import Factor
from ez.factor.builtin.technical import MA
import pandas as pd

class DualMACross(Strategy):
    def __init__(self, fast: int = 5, slow: int = 20):
        self.fast = fast
        self.slow = slow

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

          <div style={h2s}>示例 3：布林带回归 + ATR 止损</div>
          <p style={ps}>价格跌破布林带下轨买入，涨到中轨卖出。同时用 ATR 做动态止损。</p>
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
        lower = data[f"boll_lower"]
        middle = data[f"boll_middle"]
        atr = data[f"atr_{self.atr_period}"]

        signal = pd.Series(0.0, index=data.index)
        entry_price = 0.0

        for i in range(len(data)):
            if pd.isna(lower.iloc[i]) or pd.isna(atr.iloc[i]):
                continue
            p = price.iloc[i]
            if signal.iloc[i - 1] if i > 0 else 0 == 0:
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
        </>}
      </div>
    </div>
  )
}
