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
                  ['MACD()', 'macd_line / macd_signal / macd_hist', 'MACD 指标'],
                  ['BOLL(period=20)', 'boll_mid_20 / boll_upper_20 / boll_lower_20', '布林带'],
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
            <pre style={code}>{`import pandas as pd
from ez.strategy import Strategy
from ez.factor.builtin.technical import RSI

class RSIReversal(Strategy):
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

type CodeKind = 'strategy' | 'factor' | 'portfolio_strategy' | 'cross_factor'

interface FileInfo {
  filename: string
  class_name: string
  size?: number
  kind?: CodeKind
}

interface ValidationResult {
  valid: boolean
  errors: string[]
}

const KIND_LABELS: Record<CodeKind, string> = {
  strategy: '策略',
  factor: '因子',
  portfolio_strategy: '组合策略',
  cross_factor: '截面因子',
}

const KIND_COLORS: Record<CodeKind, string> = {
  strategy: 'var(--color-accent)',
  factor: '#7c3aed',
  portfolio_strategy: '#0891b2',
  cross_factor: '#d97706',
}

const api = (path: string, opts?: RequestInit) =>
  fetch(`/api/code${path}`, { headers: { 'Content-Type': 'application/json' }, ...opts })

export default function CodeEditor({ onNavigate }: { onNavigate?: (tab: string) => void }) {
  const [code, setCode] = useState('')
  const [filename, setFilename] = useState('')
  const [currentKind, setCurrentKind] = useState<CodeKind>('strategy')
  const [files, setFiles] = useState<FileInfo[]>([])
  const [factorFiles, setFactorFiles] = useState<FileInfo[]>([])
  const [portfolioFiles, setPortfolioFiles] = useState<FileInfo[]>([])
  const [crossFactorFiles, setCrossFactorFiles] = useState<FileInfo[]>([])
  const [registry, setRegistry] = useState<Record<string, { builtin: any[]; user: any[] }>>({})
  const [status, setStatus] = useState<string>('')
  const [errors, setErrors] = useState<string[]>([])
  const [testOutput, setTestOutput] = useState('')
  const [saving, setSaving] = useState(false)
  const [validating, setValidating] = useState(false)
  const [showChat, setShowChat] = useState(false)
  const [showHelp, setShowHelp] = useState(false)
  const editorRef = useRef<any>(null)

  useEffect(() => { loadAllFiles() }, [])

  const loadAllFiles = async () => {
    // Load strategy/factor files (default)
    try {
      const res = await api('/files')
      if (res.ok) setFiles(await res.json())
    } catch {}
    // Load user factor files
    try {
      const res = await api('/files?kind=factor')
      if (res.ok) setFactorFiles(await res.json())
    } catch {}
    // Load portfolio strategy files
    try {
      const res = await api('/files?kind=portfolio_strategy')
      if (res.ok) setPortfolioFiles(await res.json())
    } catch {}
    // Load cross factor files
    try {
      const res = await api('/files?kind=cross_factor')
      if (res.ok) setCrossFactorFiles(await res.json())
    } catch {}
    // Load registry (builtin + user registered objects)
    try {
      const res = await api('/registry')
      if (res.ok) setRegistry(await res.json())
    } catch {}
  }

  const loadFile = async (fname: string, kind: CodeKind = 'strategy') => {
    try {
      const res = await api(`/files/${fname}?kind=${kind}`)
      if (res.ok) {
        const data = await res.json()
        setCode(data.code)
        setFilename(fname)
        setCurrentKind(kind)
        setStatus(`已加载 ${fname}`)
        setErrors([])
        setTestOutput('')
      }
    } catch (e: any) { setStatus(`Error: ${e.message}`) }
  }

  const newFile = async (kind: CodeKind) => {
    const prefixMap: Record<CodeKind, string> = {
      strategy: 'MyStrategy', factor: 'MyFactor',
      portfolio_strategy: 'MyPortfolioStrategy', cross_factor: 'MyCrossFactor',
    }
    const prefix = prefixMap[kind]
    const allFiles = [...files, ...factorFiles, ...portfolioFiles, ...crossFactorFiles]
    const existing = allFiles.map(f => f.filename)
    let name = prefix
    let n = 1
    while (existing.includes(name.replace(/([A-Z])/g, '_$1').toLowerCase().replace(/^_/, '') + '.py')) {
      name = `${prefix}${++n}`
    }
    try {
      const res = await api('/template', { method: 'POST', body: JSON.stringify({ kind, class_name: name }) })
      if (res.ok) {
        const data = await res.json()
        setCode(data.code)
        const fn = name.replace(/([A-Z])/g, '_$1').toLowerCase().replace(/^_/, '') + '.py'
        setFilename(fn)
        setCurrentKind(kind)
        setStatus(`新建${KIND_LABELS[kind]}: ${fn}`)
        setErrors([])
        setTestOutput('')
      }
    } catch {}
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
        body: JSON.stringify({ filename, code, overwrite, kind: currentKind }),
      })
      const data = await res.json()
      if (res.ok) {
        setStatus(`已保存至 ${data.path} — 合约测试通过!`)
        setErrors([])
        setTestOutput(data.test_output || '')
        loadAllFiles()
      } else {
        const detail = data.detail || data
        const errs = detail.errors || [JSON.stringify(detail)]
        // Auto-retry with overwrite if file already exists (e.g., AI created it)
        if (!overwrite && errs.some((e: string) => e.includes('already exists'))) {
          setSaving(false)
          return save(true)
        }
        setStatus('保存失败')
        setErrors(errs)
        if (detail.test_output) setTestOutput(detail.test_output)
      }
    } catch (e: any) { setStatus(`Error: ${e.message}`) }
    finally { setSaving(false) }
  }

  const deleteFile = async (fname: string, kind: CodeKind = 'strategy') => {
    if (!confirm(`确认删除 ${fname}?`)) return
    try {
      const res = await api(`/files/${fname}?kind=${kind}`, { method: 'DELETE' })
      if (res.ok) {
        const data = await res.json()
        loadAllFiles()
        if (fname === filename) { setCode(''); setFilename('') }
        setStatus(data.warning ? `已删除 ${fname}（${data.warning}）` : `已删除 ${fname}`)
      } else {
        const err = await res.json().catch(() => ({ detail: '未知错误' }))
        setStatus(`删除失败: ${err.detail || res.statusText}`)
      }
    } catch (e: any) {
      setStatus(`删除失败: ${e?.message || '网络错误'}`)
    }
  }

  // Sidebar file item renderer
  const renderFileItem = (f: FileInfo, kind: CodeKind) => (
    <div key={`${kind}:${f.filename}`}
      className="flex items-center justify-between px-2 py-1 rounded cursor-pointer text-xs group"
      style={{ backgroundColor: f.filename === filename && currentKind === kind ? 'var(--bg-primary)' : 'transparent', color: 'var(--text-primary)' }}
      onClick={() => loadFile(f.filename, kind)}>
      <span className="truncate" title={f.class_name || f.filename}>{f.class_name || f.filename}</span>
      <button onClick={e => { e.stopPropagation(); deleteFile(f.filename, kind) }}
        className="opacity-0 group-hover:opacity-100 text-red-400 ml-1">x</button>
    </div>
  )

  // Filter strategy files (exclude research_), factor files come from separate state
  const strategyFiles = files.filter(f => f.class_name && !f.filename.startsWith('research_'))
  const otherFiles = files.filter(f => !f.class_name && !f.filename.startsWith('research_'))
  // (allEmpty removed — sidebar now always shows registry groups)

  return (
    <div className="flex" style={{ height: '100%', width: '100%', overflow: 'hidden' }}>
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
        <div className="p-2 border-b flex flex-wrap gap-1" style={{ borderColor: 'var(--border)' }}>
          <button onClick={() => newFile('strategy')}
            className="flex-1 text-xs px-1 py-1.5 rounded font-medium" style={{ backgroundColor: KIND_COLORS.strategy, color: '#fff', minWidth: '45%' }}>
            + 策略
          </button>
          <button onClick={() => newFile('factor')}
            className="flex-1 text-xs px-1 py-1.5 rounded font-medium" style={{ backgroundColor: KIND_COLORS.factor, color: '#fff', minWidth: '45%' }}>
            + 因子
          </button>
          <button onClick={() => newFile('portfolio_strategy')}
            className="flex-1 text-xs px-1 py-1.5 rounded font-medium" style={{ backgroundColor: KIND_COLORS.portfolio_strategy, color: '#fff', minWidth: '45%' }}>
            + 组合策略
          </button>
          <button onClick={() => newFile('cross_factor')}
            className="flex-1 text-xs px-1 py-1.5 rounded font-medium" style={{ backgroundColor: KIND_COLORS.cross_factor, color: '#fff', minWidth: '45%' }}>
            + 截面因子
          </button>
        </div>
        <div className="flex-1 overflow-y-auto p-2">
          {/* Toolbar: refresh + cleanup */}
          <div className="flex gap-1 mb-2">
            <button onClick={async () => {
              try { await api('/refresh', { method: 'POST' }); await loadAllFiles(); setStatus('已刷新') } catch {}
            }} className="text-xs px-2 py-0.5 rounded flex-1" style={{ color: 'var(--text-secondary)', border: '1px solid var(--border)' }}
              title="重新扫描用户目录并刷新注册表">刷新</button>
            <button onClick={async () => {
              if (!confirm('删除所有 research_* 开头的策略文件及其注册？\n（已通过"注册到全局"复制出去的副本不受影响）')) return
              try {
                const res = await api('/cleanup-research-strategies', { method: 'DELETE' })
                if (res.ok) { const d = await res.json(); setStatus(`清理了 ${d.count} 个研究策略`); await loadAllFiles() }
              } catch {}
            }} className="text-xs px-2 py-0.5 rounded flex-1" style={{ color: '#f59e0b', border: '1px solid var(--border)' }}
              title="清理研究助手生成的 research_* 策略">清理研究</button>
          </div>

          {/* 4 groups: strategy, factor, portfolio_strategy, cross_factor */}
          {(['strategy', 'factor', 'portfolio_strategy', 'cross_factor'] as CodeKind[]).map(kind => {
            const label = KIND_LABELS[kind]
            const color = KIND_COLORS[kind]
            const reg = registry[kind] || { builtin: [], user: [] }
            const userFiles = kind === 'strategy' ? strategyFiles
              : kind === 'factor' ? factorFiles
              : kind === 'portfolio_strategy' ? portfolioFiles
              : crossFactorFiles
            const total = reg.builtin.length + reg.user.length

            return (
              <div key={kind} className="mb-2">
                <div className="text-xs font-medium px-2 py-1 flex items-center justify-between" style={{ color }}>
                  <span>{label} ({total} 已注册)</span>
                </div>
                {/* Builtin items (collapsed) */}
                {reg.builtin.length > 0 && (
                  <div className="px-2">
                    <details>
                      <summary className="text-xs cursor-pointer" style={{ color: 'var(--text-muted)' }}>
                        系统内置 ({reg.builtin.length})
                      </summary>
                      <div className="ml-2 mt-1 space-y-0.5">
                        {reg.builtin.map(b => (
                          <div key={b.name} className="text-xs px-1 py-0.5 truncate" style={{ color: 'var(--text-secondary)' }}
                            title={b.description || b.name}>
                            {b.name}
                          </div>
                        ))}
                      </div>
                    </details>
                  </div>
                )}
                {/* User items (always visible) */}
                {userFiles.map(f => renderFileItem(f, kind))}
                {userFiles.length === 0 && reg.builtin.length === 0 && (
                  <div className="text-xs px-3 py-0.5" style={{ color: 'var(--text-muted)' }}>无</div>
                )}
              </div>
            )
          })}
          {/* Uncategorized user files */}
          {otherFiles.length > 0 && (
            <>
              <div className="text-xs font-medium px-2 py-1 mt-2" style={{ color: 'var(--text-secondary)' }}>其他</div>
              {otherFiles.map(f => renderFileItem(f, 'strategy'))}
            </>
          )}
        </div>
      </div>

      {/* Main editor area */}
      <div className="flex-1 flex flex-col">
        {/* Toolbar */}
        <div className="flex items-center gap-2 px-3 py-2 border-b" style={{ borderColor: 'var(--border)', backgroundColor: 'var(--bg-secondary)' }}>
          <span className="text-xs px-1.5 py-0.5 rounded" style={{ backgroundColor: KIND_COLORS[currentKind], color: '#fff' }}>
            {KIND_LABELS[currentKind]}
          </span>
          <input
            type="text" placeholder="filename.py" value={filename}
            onChange={e => setFilename(e.target.value)}
            className="text-sm px-2 py-1 rounded w-44"
            style={{ backgroundColor: 'var(--bg-primary)', color: 'var(--text-primary)', border: '1px solid var(--border)' }}
          />
          <button onClick={validate} disabled={validating || !code}
            className="text-xs px-3 py-1 rounded"
            style={{ backgroundColor: '#2563eb', color: '#fff', opacity: validating || !code ? 0.5 : 1 }}>
            {validating ? '检查中...' : '语法检查'}
          </button>
          <button onClick={() => save(false)} disabled={saving || !code || !filename}
            className="text-xs px-3 py-1 rounded"
            style={{ backgroundColor: '#16a34a', color: '#fff', opacity: saving || !code || !filename ? 0.5 : 1 }}>
            {saving ? '测试中...' : '保存并测试'}
          </button>
          <button onClick={() => save(true)} disabled={saving || !code || !filename}
            className="text-xs px-3 py-1 rounded"
            style={{ backgroundColor: '#d97706', color: '#fff', opacity: saving || !code || !filename ? 0.5 : 1 }}>
            覆盖保存
          </button>
          <div className="flex-1" />
          <button onClick={() => setShowHelp(!showHelp)}
            className="text-xs px-3 py-1 rounded font-bold"
            style={{ backgroundColor: showHelp ? '#eab308' : 'var(--bg-primary)', color: showHelp ? '#000' : 'var(--text-secondary)', border: '1px solid var(--border)', minWidth: '28px' }}>
            ?
          </button>
          {onNavigate && (currentKind === 'portfolio_strategy' || currentKind === 'cross_factor') && (
            <button onClick={() => onNavigate('portfolio')}
              className="text-xs px-3 py-1 rounded"
              style={{ backgroundColor: '#0891b2', color: '#fff' }}>
              去组合回测
            </button>
          )}
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
        <div className="flex-1 flex" style={{ minHeight: 0, overflow: 'hidden' }}>
          <div style={{ flex: showChat ? '0 0 60%' : '1 1 100%', minWidth: 0, minHeight: 0, overflow: 'hidden' }}>
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
            <div className="border-l" style={{ flex: '0 0 40%', borderColor: 'var(--border)', minWidth: 0, minHeight: 0, overflow: 'hidden' }}>
              <ChatPanel editorCode={code} fileKey={filename} onCodeUpdate={(c, f) => {
                if (c !== undefined && c !== null) setCode(c)
                if (f) {
                  setFilename(f)
                  // AI creates strategies via create_strategy tool → kind is always 'strategy'
                  setCurrentKind('strategy')
                  // Refresh sidebar file list
                  loadAllFiles()
                }
              }} />
            </div>
          )}
        </div>

        {/* Test output panel */}
        {testOutput && (
          <div className="border-t overflow-auto" style={{ borderColor: 'var(--border)', maxHeight: '200px', backgroundColor: 'var(--bg-primary)' }}>
            <div className="flex justify-between items-center px-3 py-1">
              <span className="text-xs font-medium" style={{ color: 'var(--text-secondary)' }}>合约测试输出</span>
              <button onClick={() => setTestOutput('')} className="text-xs px-1.5 rounded hover:opacity-80"
                style={{ color: 'var(--text-secondary)', border: '1px solid var(--border)' }}>✕</button>
            </div>
            <pre className="px-3 pb-2 text-xs whitespace-pre-wrap" style={{ color: 'var(--text-primary)' }}>{testOutput}</pre>
          </div>
        )}
      </div>
    </div>
  )
}
