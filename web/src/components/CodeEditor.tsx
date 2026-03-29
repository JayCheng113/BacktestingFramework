import { useState, useEffect, useRef } from 'react'
import Editor from '@monaco-editor/react'
import ChatPanel from './ChatPanel'

function HelpPanel({ onClose }: { onClose: () => void }) {
  const sectionStyle = { marginBottom: '16px' }
  const h2 = { color: 'var(--color-accent)', fontSize: '13px', fontWeight: 700, marginBottom: '6px' }
  const code = { backgroundColor: '#1e293b', padding: '8px 10px', borderRadius: '4px', fontSize: '11px', overflowX: 'auto' as const, whiteSpace: 'pre' as const, display: 'block', lineHeight: '1.5' }
  const li = { marginBottom: '4px' }

  return (
    <div className="border-b overflow-y-auto" style={{ borderColor: 'var(--border)', backgroundColor: '#0f172a', maxHeight: '50vh', padding: '12px 16px' }}>
      <div className="flex justify-between items-center mb-3">
        <span style={{ color: 'var(--text-primary)', fontWeight: 700, fontSize: '14px' }}>Strategy Development Guide</span>
        <button onClick={onClose} className="text-xs px-2 py-0.5 rounded" style={{ color: 'var(--text-secondary)', border: '1px solid var(--border)' }}>Close</button>
      </div>

      <div className="grid grid-cols-2 gap-4 text-xs" style={{ color: 'var(--text-primary)' }}>
        {/* Left column */}
        <div>
          <div style={sectionStyle}>
            <div style={h2}>Strategy Interface (required)</div>
            <pre style={code}>{`from ez.strategy import Strategy
from ez.factor import Factor
from ez.factor.builtin.technical import RSI, MA, EMA, MACD, BOLL

class MyStrategy(Strategy):

    # 1. Parameter schema (for UI form)
    @classmethod
    def get_parameters_schema(cls) -> dict:
        return {
            "period": {"type": "int", "default": 14,
                       "min": 5, "max": 50, "label": "RSI Period"},
        }

    # 2. Required factors (auto-computed by engine)
    def required_factors(self) -> list[Factor]:
        return [RSI(period=self.period)]

    # 3. Signal generation
    #    Return pd.Series: 0.0=no position, 1.0=full position
    def generate_signals(self, data: pd.DataFrame) -> pd.Series:
        return (data["rsi_14"] < 30).astype(float)`}</pre>
          </div>

          <div style={sectionStyle}>
            <div style={h2}>Signal Rules</div>
            <ul style={{ paddingLeft: '16px', listStyle: 'disc' }}>
              <li style={li}><b>0.0</b> = no position (sell / stay out)</li>
              <li style={li}><b>1.0</b> = full position (buy / hold)</li>
              <li style={li}><b>0.0-1.0</b> = fractional position</li>
              <li style={li}>First <code>warmup_period</code> rows can be NaN</li>
              <li style={li}>Engine handles entry/exit automatically based on signal changes</li>
            </ul>
          </div>
        </div>

        {/* Right column */}
        <div>
          <div style={sectionStyle}>
            <div style={h2}>Available Factors</div>
            <table style={{ width: '100%', borderCollapse: 'collapse' }}>
              <thead><tr style={{ borderBottom: '1px solid var(--border)' }}>
                <th style={{ textAlign: 'left', padding: '2px 6px' }}>Factor</th>
                <th style={{ textAlign: 'left', padding: '2px 6px' }}>Column Name</th>
                <th style={{ textAlign: 'left', padding: '2px 6px' }}>Usage</th>
              </tr></thead>
              <tbody style={{ color: 'var(--text-secondary)' }}>
                {[
                  ['MA(period=20)', 'ma_20', 'Moving Average'],
                  ['EMA(period=12)', 'ema_12', 'Exponential MA'],
                  ['RSI(period=14)', 'rsi_14', 'Relative Strength Index'],
                  ['MACD()', 'macd / macd_signal', 'MACD + Signal line'],
                  ['BOLL(period=20)', 'boll_upper / boll_lower', 'Bollinger Bands'],
                  ['Momentum(period=20)', 'momentum_20', 'N-day return'],
                  ['VWAP(period=20)', 'vwap_20', 'Volume-Weighted Avg Price'],
                  ['OBV()', 'obv', 'On-Balance Volume'],
                  ['ATR(period=14)', 'atr_14', 'Average True Range'],
                ].map(([factor, col, desc]) => (
                  <tr key={factor}><td style={{ padding: '2px 6px', fontFamily: 'monospace' }}>{factor}</td>
                  <td style={{ padding: '2px 6px', fontFamily: 'monospace' }}>{col}</td>
                  <td style={{ padding: '2px 6px' }}>{desc}</td></tr>
                ))}
              </tbody>
            </table>
          </div>

          <div style={sectionStyle}>
            <div style={h2}>AI Chat Examples</div>
            <ul style={{ paddingLeft: '16px', listStyle: 'disc', color: 'var(--text-secondary)' }}>
              <li style={li}>"Write an RSI reversal strategy, buy below 30, sell above 70"</li>
              <li style={li}>"Modify the current code to add a stop-loss at -5%"</li>
              <li style={li}>"Backtest MACrossStrategy on 000001.SZ from 2020 to 2024"</li>
              <li style={li}>"Explain what MACD factor does"</li>
              <li style={li}>"List all available strategies and their parameters"</li>
            </ul>
          </div>

          <div style={sectionStyle}>
            <div style={h2}>Complete Example: RSI Reversal</div>
            <pre style={code}>{`class RSIReversal(Strategy):
    def __init__(self, period=14, oversold=30, overbought=70):
        self.period = period
        self.oversold = oversold
        self.overbought = overbought

    @classmethod
    def get_parameters_schema(cls):
        return {
            "period":     {"type":"int",   "default":14,  "min":5, "max":50,  "label":"RSI Period"},
            "oversold":   {"type":"float", "default":30,  "min":10,"max":40,  "label":"Oversold"},
            "overbought": {"type":"float", "default":70,  "min":60,"max":90,  "label":"Overbought"},
        }

    def required_factors(self):
        return [RSI(period=self.period)]

    def generate_signals(self, data):
        rsi = data[f"rsi_{self.period}"]
        signal = pd.Series(0.0, index=data.index)
        signal[rsi < self.oversold] = 1.0
        # Forward-fill to hold position between signals
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
        setStatus(`Loaded ${fname}`)
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
          ? 'Factor template generated (reference only — factors must be placed in ez/factor/builtin/ manually)'
          : 'Template generated')
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
          setStatus('Syntax check passed')
          setErrors([])
        } else {
          setStatus('Syntax check failed')
          setErrors(data.errors)
        }
      }
    } catch (e: any) { setStatus(`Error: ${e.message}`) }
    finally { setValidating(false) }
  }

  const save = async (overwrite = false) => {
    if (!filename) { setStatus('Please set a filename'); return }
    setSaving(true)
    setErrors([])
    setTestOutput('')
    setStatus('Saving & running contract test...')
    try {
      const res = await api('/save', {
        method: 'POST',
        body: JSON.stringify({ filename, code, overwrite }),
      })
      const data = await res.json()
      if (res.ok) {
        setStatus(`Saved to ${data.path} — contract test passed!`)
        setErrors([])
        setTestOutput(data.test_output || '')
        loadFiles()
      } else {
        // 422 error from backend
        const detail = data.detail || data
        setStatus('Save failed')
        setErrors(detail.errors || [JSON.stringify(detail)])
        if (detail.test_output) setTestOutput(detail.test_output)
      }
    } catch (e: any) { setStatus(`Error: ${e.message}`) }
    finally { setSaving(false) }
  }

  const deleteFile = async (fname: string) => {
    if (!confirm(`Delete ${fname}?`)) return
    try {
      const res = await api(`/files/${fname}`, { method: 'DELETE' })
      if (res.ok) {
        loadFiles()
        if (fname === filename) { setCode(''); setFilename('') }
        setStatus(`Deleted ${fname}`)
      }
    } catch {}
  }

  return (
    <div className="flex" style={{ height: 'calc(100vh - 48px)' }}>
      {/* File sidebar */}
      <div className="flex flex-col w-56 border-r" style={{ borderColor: 'var(--border)', backgroundColor: 'var(--bg-secondary)' }}>
        <div className="p-3 border-b" style={{ borderColor: 'var(--border)' }}>
          <div className="text-sm font-medium mb-2" style={{ color: 'var(--text-primary)' }}>New File</div>
          <div className="flex gap-1 mb-2">
            <button onClick={() => setTemplateKind('strategy')}
              className="text-xs px-2 py-1 rounded"
              style={{ backgroundColor: templateKind === 'strategy' ? 'var(--color-accent)' : 'var(--bg-primary)', color: templateKind === 'strategy' ? '#fff' : 'var(--text-secondary)' }}>
              Strategy
            </button>
            <button onClick={() => setTemplateKind('factor')}
              className="text-xs px-2 py-1 rounded"
              style={{ backgroundColor: templateKind === 'factor' ? 'var(--color-accent)' : 'var(--bg-primary)', color: templateKind === 'factor' ? '#fff' : 'var(--text-secondary)' }}>
              Factor
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
            Generate Template
          </button>
        </div>
        <div className="flex-1 overflow-y-auto p-2">
          <div className="text-xs mb-1" style={{ color: 'var(--text-secondary)' }}>strategies/</div>
          {files.length === 0 && <div className="text-xs px-2" style={{ color: 'var(--text-secondary)' }}>No files yet</div>}
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
            {validating ? 'Checking...' : 'Validate'}
          </button>
          <button onClick={() => save(false)} disabled={saving || !code || !filename || isFactorCode}
            className="text-xs px-3 py-1 rounded"
            title={isFactorCode ? 'Factor files must be placed manually in ez/factor/builtin/' : ''}
            style={{ backgroundColor: '#16a34a', color: '#fff', opacity: saving || !code || !filename || isFactorCode ? 0.5 : 1 }}>
            {saving ? 'Testing...' : isFactorCode ? 'Save N/A (Factor)' : 'Save & Test'}
          </button>
          <button onClick={() => save(true)} disabled={saving || !code || !filename || isFactorCode}
            className="text-xs px-3 py-1 rounded"
            style={{ backgroundColor: '#d97706', color: '#fff', opacity: saving || !code || !filename || isFactorCode ? 0.5 : 1 }}>
            Overwrite
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
            AI Chat {showChat ? '<<' : '>>'}
          </button>
        </div>

        {/* Status bar */}
        {(status || errors.length > 0) && (
          <div className="px-3 py-1 text-xs border-b" style={{ borderColor: 'var(--border)', backgroundColor: errors.length ? '#7f1d1d20' : '#14532d20' }}>
            {status && <div style={{ color: errors.length ? '#ef4444' : '#22c55e' }}>{status}</div>}
            {errors.map((e, i) => <div key={i} style={{ color: '#ef4444' }}>{e}</div>)}
          </div>
        )}

        {/* Help panel */}
        {showHelp && <HelpPanel onClose={() => setShowHelp(false)} />}

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
            <div className="px-3 py-1 text-xs font-medium" style={{ color: 'var(--text-secondary)' }}>Contract Test Output</div>
            <pre className="px-3 pb-2 text-xs whitespace-pre-wrap" style={{ color: 'var(--text-primary)' }}>{testOutput}</pre>
          </div>
        )}
      </div>
    </div>
  )
}
