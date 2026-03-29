import { useState, useEffect, useRef } from 'react'
import Editor from '@monaco-editor/react'
import ChatPanel from './ChatPanel'

function HelpPanel({ onClose }: { onClose: () => void }) {
  const sectionStyle = { marginBottom: '16px' }
  const h2 = { color: 'var(--color-accent)', fontSize: '13px', fontWeight: 700, marginBottom: '6px' }
  const code = { backgroundColor: '#1e293b', padding: '8px 10px', borderRadius: '4px', fontSize: '11px', overflowX: 'auto' as const, whiteSpace: 'pre' as const, display: 'block', lineHeight: '1.5' }
  const li = { marginBottom: '4px' }

  return (
    <div className="overflow-y-auto" style={{ backgroundColor: '#0f172a', padding: '16px 20px', borderRadius: '8px' }}>
      <div className="flex justify-between items-center mb-3">
        <span style={{ color: 'var(--text-primary)', fontWeight: 700, fontSize: '14px' }}>策略开发指南</span>
        <button onClick={onClose} className="text-xs px-2 py-0.5 rounded" style={{ color: 'var(--text-secondary)', border: '1px solid var(--border)' }}>关闭</button>
      </div>

      <div className="grid grid-cols-2 gap-4 text-xs" style={{ color: 'var(--text-primary)' }}>
        {/* 左栏 */}
        <div>
          <div style={sectionStyle}>
            <div style={h2}>策略接口（必须实现）</div>
            <pre style={code}>{`from ez.strategy import Strategy
from ez.factor import Factor
from ez.factor.builtin.technical import RSI, MA, EMA, MACD, BOLL

class MyStrategy(Strategy):

    # 1. 参数定义（用于前端表单自动渲染）
    @classmethod
    def get_parameters_schema(cls) -> dict:
        return {
            "period": {"type": "int", "default": 14,
                       "min": 5, "max": 50, "label": "RSI 周期"},
        }

    # 2. 依赖因子（引擎自动计算并注入 data）
    def required_factors(self) -> list[Factor]:
        return [RSI(period=self.period)]

    # 3. 信号生成
    #    返回 pd.Series: 0.0=空仓, 1.0=满仓
    def generate_signals(self, data: pd.DataFrame) -> pd.Series:
        return (data["rsi_14"] < 30).astype(float)`}</pre>
          </div>

          <div style={sectionStyle}>
            <div style={h2}>信号规则</div>
            <ul style={{ paddingLeft: '16px', listStyle: 'disc' }}>
              <li style={li}><b>0.0</b> = 空仓（卖出 / 不持仓）</li>
              <li style={li}><b>1.0</b> = 满仓（买入 / 持有）</li>
              <li style={li}><b>0.0~1.0</b> = 部分仓位（如 0.5 = 半仓）</li>
              <li style={li}>前 <code>warmup_period</code> 行可以为 NaN（因子预热期）</li>
              <li style={li}>引擎根据信号变化自动处理买卖，无需手动管理仓位</li>
            </ul>
          </div>
        </div>

        {/* 右栏 */}
        <div>
          <div style={sectionStyle}>
            <div style={h2}>可用因子</div>
            <table style={{ width: '100%', borderCollapse: 'collapse' }}>
              <thead><tr style={{ borderBottom: '1px solid var(--border)' }}>
                <th style={{ textAlign: 'left', padding: '2px 6px' }}>因子</th>
                <th style={{ textAlign: 'left', padding: '2px 6px' }}>列名</th>
                <th style={{ textAlign: 'left', padding: '2px 6px' }}>说明</th>
              </tr></thead>
              <tbody style={{ color: 'var(--text-secondary)' }}>
                {[
                  ['MA(period=20)', 'ma_20', '移动平均线'],
                  ['EMA(period=12)', 'ema_12', '指数移动平均'],
                  ['RSI(period=14)', 'rsi_14', '相对强弱指标'],
                  ['MACD()', 'macd / macd_signal', 'MACD 指标 + 信号线'],
                  ['BOLL(period=20)', 'boll_upper / boll_lower', '布林带上下轨'],
                  ['Momentum(period=20)', 'momentum_20', 'N日收益率（动量）'],
                  ['VWAP(period=20)', 'vwap_20', '成交量加权均价'],
                  ['OBV()', 'obv', '能量潮（累计量能）'],
                  ['ATR(period=14)', 'atr_14', '平均真实波幅'],
                ].map(([factor, col, desc]) => (
                  <tr key={factor}><td style={{ padding: '2px 6px', fontFamily: 'monospace' }}>{factor}</td>
                  <td style={{ padding: '2px 6px', fontFamily: 'monospace' }}>{col}</td>
                  <td style={{ padding: '2px 6px' }}>{desc}</td></tr>
                ))}
              </tbody>
            </table>
          </div>

          <div style={sectionStyle}>
            <div style={h2}>AI 助手用法示例</div>
            <ul style={{ paddingLeft: '16px', listStyle: 'disc', color: 'var(--text-secondary)' }}>
              <li style={li}>"帮我写一个 RSI 超卖反转策略，低于30买入，高于70卖出"</li>
              <li style={li}>"修改当前代码，加一个 -5% 止损逻辑"</li>
              <li style={li}>"用 MACrossStrategy 回测 000001.SZ 2020到2024年"</li>
              <li style={li}>"解释一下 MACD 因子是什么"</li>
              <li style={li}>"列出所有可用策略和它们的参数"</li>
            </ul>
          </div>

          <div style={sectionStyle}>
            <div style={h2}>完整示例：RSI 超卖反转</div>
            <pre style={code}>{`class RSIReversal(Strategy):
    def __init__(self, period=14, oversold=30, overbought=70):
        self.period = period
        self.oversold = oversold
        self.overbought = overbought

    @classmethod
    def get_parameters_schema(cls):
        return {
            "period":     {"type":"int",   "default":14,  "min":5, "max":50,  "label":"RSI 周期"},
            "oversold":   {"type":"float", "default":30,  "min":10,"max":40,  "label":"超卖阈值"},
            "overbought": {"type":"float", "default":70,  "min":60,"max":90,  "label":"超买阈值"},
        }

    def required_factors(self):
        return [RSI(period=self.period)]

    def generate_signals(self, data):
        rsi = data[f"rsi_{self.period}"]
        signal = pd.Series(0.0, index=data.index)
        signal[rsi < self.oversold] = 1.0  # 超卖买入
        # 前向填充：在非超买超卖区间保持当前仓位
        signal = signal.replace(0.0, pd.NA).ffill().fillna(0.0)
        return signal`}</pre>
          </div>
        </div>
      </div>
    </div>
  )
}

interface FileInfo {
  filename: string
  class_name: string
  size: number
}

interface ValidationResult {
  valid: boolean
  errors: string[]
}

interface SaveResult {
  success: boolean
  errors: string[]
  path?: string
  test_output?: string
}

const api = (path: string, opts?: RequestInit) =>
  fetch(`/api/code${path}`, { headers: { 'Content-Type': 'application/json' }, ...opts })

export default function CodeEditor() {
  const [code, setCode] = useState('')
  const [filename, setFilename] = useState('')
  const [files, setFiles] = useState<FileInfo[]>([])
  const [status, setStatus] = useState<string>('')
  const [errors, setErrors] = useState<string[]>([])
  const [testOutput, setTestOutput] = useState('')
  const [saving, setSaving] = useState(false)
  const [validating, setValidating] = useState(false)
  const [templateKind, setTemplateKind] = useState<'strategy' | 'factor'>('strategy')
  const [isFactorCode, setIsFactorCode] = useState(false)
  const [className, setClassName] = useState('')
  const [showChat, setShowChat] = useState(false)
  const [showHelp, setShowHelp] = useState(false)
  const editorRef = useRef<any>(null)

  useEffect(() => { loadFiles() }, [])

  const loadFiles = async () => {
    try {
      const res = await api('/files')
      if (res.ok) setFiles(await res.json())
    } catch {}
  }

  const loadFile = async (fname: string) => {
    try {
      const res = await api(`/files/${fname}`)
      if (res.ok) {
        const data = await res.json()
        setCode(data.code)
        setFilename(fname)
        setIsFactorCode(false)
        setStatus(`已加载 ${fname}`)
        setErrors([])
        setTestOutput('')
      }
    } catch (e: any) { setStatus(`Error: ${e.message}`) }
  }

  const generateTemplate = async () => {
    try {
      const res = await api('/template', {
        method: 'POST',
        body: JSON.stringify({ kind: templateKind, class_name: className || '' }),
      })
      if (res.ok) {
        const data = await res.json()
        setCode(data.code)
        // Auto-generate filename from class name
        const name = className || (templateKind === 'strategy' ? 'MyStrategy' : 'MyFactor')
        const fn = name.replace(/([A-Z])/g, '_$1').toLowerCase().replace(/^_/, '') + '.py'
        setFilename(fn)
        setIsFactorCode(templateKind === 'factor')
        setStatus(templateKind === 'factor'
          ? '因子模板已生成（仅供参考 — 因子需手动放置到 ez/factor/builtin/）'
          : '模板已生成')
        setErrors([])
        setTestOutput('')
      }
    } catch (e: any) { setStatus(`Error: ${e.message}`) }
  }

  const validate = async () => {
    setValidating(true)
    setErrors([])
    try {
      const res = await api('/validate', {
        method: 'POST',
        body: JSON.stringify({ code }),
      })
      if (res.ok) {
        const data: ValidationResult = await res.json()
        if (data.valid) {
          setStatus('语法检查通过')
          setErrors([])
        } else {
          setStatus('语法检查失败')
          setErrors(data.errors)
        }
      }
    } catch (e: any) { setStatus(`Error: ${e.message}`) }
    finally { setValidating(false) }
  }

  const save = async (overwrite = false) => {
    if (!filename) { setStatus('请设置文件名'); return }
    setSaving(true)
    setErrors([])
    setTestOutput('')
    setStatus('保存中，正在运行合约测试...')
    try {
      const res = await api('/save', {
        method: 'POST',
        body: JSON.stringify({ filename, code, overwrite }),
      })
      const data = await res.json()
      if (res.ok) {
        setStatus(`已保存至 ${data.path} — 合约测试通过!`)
        setErrors([])
        setTestOutput(data.test_output || '')
        loadFiles()
      } else {
        // 422 error from backend
        const detail = data.detail || data
        setStatus('保存失败')
        setErrors(detail.errors || [JSON.stringify(detail)])
        if (detail.test_output) setTestOutput(detail.test_output)
      }
    } catch (e: any) { setStatus(`Error: ${e.message}`) }
    finally { setSaving(false) }
  }

  const deleteFile = async (fname: string) => {
    if (!confirm(`确认删除 ${fname}?`)) return
    try {
      const res = await api(`/files/${fname}`, { method: 'DELETE' })
      if (res.ok) {
        loadFiles()
        if (fname === filename) { setCode(''); setFilename('') }
        setStatus(`已删除 ${fname}`)
      }
    } catch {}
  }

  return (
    <div className="flex" style={{ height: 'calc(100vh - 48px)' }}>
      {/* Help modal overlay */}
      {showHelp && (
        <div style={{ position: 'fixed', inset: 0, zIndex: 50, display: 'flex', alignItems: 'center', justifyContent: 'center' }}>
          <div style={{ position: 'absolute', inset: 0, backgroundColor: 'rgba(0,0,0,0.6)' }} onClick={() => setShowHelp(false)} />
          <div style={{ position: 'relative', zIndex: 51, width: '90vw', maxWidth: '1000px', maxHeight: '80vh', overflow: 'auto', borderRadius: '8px' }}>
            <HelpPanel onClose={() => setShowHelp(false)} />
          </div>
        </div>
      )}

      {/* File sidebar */}
      <div className="flex flex-col w-56 border-r" style={{ borderColor: 'var(--border)', backgroundColor: 'var(--bg-secondary)' }}>
        <div className="p-3 border-b" style={{ borderColor: 'var(--border)' }}>
          <div className="text-sm font-medium mb-2" style={{ color: 'var(--text-primary)' }}>新建文件</div>
          <div className="flex gap-1 mb-2">
            <button onClick={() => setTemplateKind('strategy')}
              className="text-xs px-2 py-1 rounded"
              style={{ backgroundColor: templateKind === 'strategy' ? 'var(--color-accent)' : 'var(--bg-primary)', color: templateKind === 'strategy' ? '#fff' : 'var(--text-secondary)' }}>
              策略
            </button>
            <button onClick={() => setTemplateKind('factor')}
              className="text-xs px-2 py-1 rounded"
              style={{ backgroundColor: templateKind === 'factor' ? 'var(--color-accent)' : 'var(--bg-primary)', color: templateKind === 'factor' ? '#fff' : 'var(--text-secondary)' }}>
              因子
            </button>
          </div>
          <input
            type="text" placeholder="ClassName" value={className}
            onChange={e => setClassName(e.target.value)}
            className="w-full text-xs px-2 py-1 rounded mb-2"
            style={{ backgroundColor: 'var(--bg-primary)', color: 'var(--text-primary)', border: '1px solid var(--border)' }}
          />
          <button onClick={generateTemplate}
            className="w-full text-xs px-2 py-1 rounded"
            style={{ backgroundColor: 'var(--color-accent)', color: '#fff' }}>
            生成模板
          </button>
        </div>
        <div className="flex-1 overflow-y-auto p-2">
          <div className="text-xs mb-1" style={{ color: 'var(--text-secondary)' }}>strategies/</div>
          {files.length === 0 && <div className="text-xs px-2" style={{ color: 'var(--text-secondary)' }}>暂无文件</div>}
          {files.map(f => (
            <div key={f.filename}
              className="flex items-center justify-between px-2 py-1 rounded cursor-pointer text-xs group"
              style={{ backgroundColor: f.filename === filename ? 'var(--bg-primary)' : 'transparent', color: 'var(--text-primary)' }}
              onClick={() => loadFile(f.filename)}>
              <span className="truncate">{f.filename}</span>
              <button onClick={e => { e.stopPropagation(); deleteFile(f.filename) }}
                className="opacity-0 group-hover:opacity-100 text-red-400 ml-1">x</button>
            </div>
          ))}
        </div>
      </div>

      {/* Main editor area */}
      <div className="flex-1 flex flex-col">
        {/* Toolbar */}
        <div className="flex items-center gap-2 px-3 py-2 border-b" style={{ borderColor: 'var(--border)', backgroundColor: 'var(--bg-secondary)' }}>
          <input
            type="text" placeholder="filename.py" value={filename}
            onChange={e => setFilename(e.target.value)}
            className="text-sm px-2 py-1 rounded w-48"
            style={{ backgroundColor: 'var(--bg-primary)', color: 'var(--text-primary)', border: '1px solid var(--border)' }}
          />
          <button onClick={validate} disabled={validating || !code}
            className="text-xs px-3 py-1 rounded"
            style={{ backgroundColor: '#2563eb', color: '#fff', opacity: validating || !code ? 0.5 : 1 }}>
            {validating ? '检查中...' : '语法检查'}
          </button>
          <button onClick={() => save(false)} disabled={saving || !code || !filename || isFactorCode}
            className="text-xs px-3 py-1 rounded"
            title={isFactorCode ? 'Factor files must be placed manually in ez/factor/builtin/' : ''}
            style={{ backgroundColor: '#16a34a', color: '#fff', opacity: saving || !code || !filename || isFactorCode ? 0.5 : 1 }}>
            {saving ? '测试中...' : isFactorCode ? '因子不可保存' : '保存并测试'}
          </button>
          <button onClick={() => save(true)} disabled={saving || !code || !filename || isFactorCode}
            className="text-xs px-3 py-1 rounded"
            style={{ backgroundColor: '#d97706', color: '#fff', opacity: saving || !code || !filename || isFactorCode ? 0.5 : 1 }}>
            覆盖保存
          </button>
          <div className="flex-1" />
          <button onClick={() => setShowHelp(!showHelp)}
            className="text-xs px-3 py-1 rounded font-bold"
            style={{ backgroundColor: showHelp ? '#eab308' : 'var(--bg-primary)', color: showHelp ? '#000' : 'var(--text-secondary)', border: '1px solid var(--border)', minWidth: '28px' }}>
            ?
          </button>
          <button onClick={() => setShowChat(!showChat)}
            className="text-xs px-3 py-1 rounded"
            style={{ backgroundColor: showChat ? 'var(--color-accent)' : 'var(--bg-primary)', color: showChat ? '#fff' : 'var(--text-secondary)', border: '1px solid var(--border)' }}>
            AI助手 {showChat ? '<<' : '>>'}
          </button>
        </div>

        {/* Status bar */}
        {(status || errors.length > 0) && (
          <div className="px-3 py-1 text-xs border-b" style={{ borderColor: 'var(--border)', backgroundColor: errors.length ? '#7f1d1d20' : '#14532d20' }}>
            {status && <div style={{ color: errors.length ? '#ef4444' : '#22c55e' }}>{status}</div>}
            {errors.map((e, i) => <div key={i} style={{ color: '#ef4444' }}>{e}</div>)}
          </div>
        )}

        {/* Editor + Chat split */}
        <div className="flex-1 flex">
          <div className={showChat ? 'w-3/5' : 'w-full'} style={{ minHeight: 0 }}>
            <Editor
              height="100%"
              language="python"
              theme="vs-dark"
              value={code}
              onChange={v => setCode(v || '')}
              onMount={editor => { editorRef.current = editor }}
              options={{
                fontSize: 13,
                minimap: { enabled: false },
                lineNumbers: 'on',
                scrollBeyondLastLine: false,
                automaticLayout: true,
                tabSize: 4,
                wordWrap: 'on',
              }}
            />
          </div>
          {showChat && (
            <div className="w-2/5 border-l" style={{ borderColor: 'var(--border)', minHeight: 0 }}>
              <ChatPanel editorCode={code} />
            </div>
          )}
        </div>

        {/* Test output panel */}
        {testOutput && (
          <div className="border-t overflow-auto" style={{ borderColor: 'var(--border)', maxHeight: '200px', backgroundColor: 'var(--bg-primary)' }}>
            <div className="px-3 py-1 text-xs font-medium" style={{ color: 'var(--text-secondary)' }}>合约测试输出</div>
            <pre className="px-3 pb-2 text-xs whitespace-pre-wrap" style={{ color: 'var(--text-primary)' }}>{testOutput}</pre>
          </div>
        )}
      </div>
    </div>
  )
}
