import { useState, useEffect } from 'react'
import { useToast } from './shared/Toast'

interface Props {
  open: boolean
  onClose: () => void
}

interface LLMInfo {
  provider: string
  api_key_set: boolean
  api_key_preview: string
  model: string
  base_url: string
  temperature: number
  available_providers: { id: string; name: string; env_key: string; needs_key: boolean }[]
}

interface TushareInfo {
  token_set: boolean
  token_preview: string
}

const inputStyle: React.CSSProperties = { backgroundColor: 'var(--bg-primary)', border: '1px solid var(--border)', color: 'var(--text-primary)', width: '100%', padding: '6px 10px', borderRadius: '4px', fontSize: '13px' }

export default function SettingsModal({ open, onClose }: Props) {
  const [llm, setLlm] = useState<LLMInfo | null>(null)
  const [tushare, setTushare] = useState<TushareInfo | null>(null)
  const [provider, setProvider] = useState('deepseek')
  const [apiKey, setApiKey] = useState('')
  const [model, setModel] = useState('')
  const [baseUrl, setBaseUrl] = useState('')
  const [temperature, setTemperature] = useState(0.3)
  const [tushareToken, setTushareToken] = useState('')
  const [saving, setSaving] = useState(false)
  const [status, setStatus] = useState('')
  const { showToast } = useToast()

  useEffect(() => {
    if (!open) return
    setStatus('')
    fetch('/api/settings/llm').then(r => r.json()).then(d => {
      setLlm(d)
      setProvider(d.provider)
      setModel(d.model === '(默认)' ? '' : d.model)
      setBaseUrl(d.base_url === '(默认)' ? '' : d.base_url)
      setTemperature(d.temperature)
    }).catch((e: unknown) => {
      const err = e as { message?: string }
      showToast('error', err?.message || '加载 LLM 配置失败')
    })
    fetch('/api/settings/tushare').then(r => r.json()).then(setTushare).catch((e: unknown) => {
      const err = e as { message?: string }
      showToast('error', err?.message || '加载 Tushare 配置失败')
    })
  }, [open])

  const saveLLM = async () => {
    setSaving(true)
    setStatus('')
    try {
      const res = await fetch('/api/settings/llm', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ provider, api_key: apiKey, model, base_url: baseUrl, temperature }),
      })
      if (res.ok) {
        setStatus('LLM 配置已保存')
        setApiKey('')
        // Refresh status
        const d = await fetch('/api/settings/llm').then(r => r.json())
        setLlm(d)
      } else {
        setStatus('保存失败')
      }
    } catch { setStatus('保存失败') }
    finally { setSaving(false) }
  }

  const saveTushare = async () => {
    setSaving(true)
    try {
      const res = await fetch('/api/settings/tushare', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ token: tushareToken }),
      })
      if (!res.ok) { setStatus('Tushare Token 保存失败'); setSaving(false); return }
      setStatus('Tushare Token 已保存')
      setTushareToken('')
      const d = await fetch('/api/settings/tushare').then(r => r.json())
      setTushare(d)
    } catch { setStatus('保存失败') }
    finally { setSaving(false) }
  }

  if (!open) return null

  return (
    <div style={{ position: 'fixed', inset: 0, zIndex: 50, display: 'flex', alignItems: 'center', justifyContent: 'center' }}>
      <div style={{ position: 'absolute', inset: 0, backgroundColor: 'rgba(0,0,0,0.6)' }} onClick={onClose} />
      <div style={{ position: 'relative', zIndex: 51, maxWidth: '500px', width: 'calc(100% - 32px)', maxHeight: '80vh', overflow: 'auto', borderRadius: '8px', backgroundColor: '#0f172a', padding: '20px' }}>
        <div className="flex justify-between items-center mb-4">
          <span style={{ color: 'var(--text-primary)', fontWeight: 700, fontSize: '16px' }}>系统设置</span>
          <button onClick={onClose} className="text-xs px-2 py-0.5 rounded" style={{ color: 'var(--text-secondary)', border: '1px solid var(--border)' }}>关闭</button>
        </div>

        {status && <div className="mb-3 text-xs px-3 py-2 rounded" style={{ backgroundColor: '#14532d20', color: '#22c55e' }}>{status}</div>}

        {/* LLM Settings */}
        <div className="mb-5">
          <div className="text-sm font-medium mb-3" style={{ color: 'var(--color-accent)' }}>LLM 配置（AI 助手）</div>

          <div className="space-y-3">
            <div>
              <label className="text-xs mb-1 block" style={{ color: 'var(--text-secondary)' }}>Provider</label>
              <select value={provider} onChange={e => setProvider(e.target.value)} style={inputStyle}>
                {llm?.available_providers.map(p => (
                  <option key={p.id} value={p.id}>{p.name}{p.needs_key ? '' : ' (无需 API Key)'}</option>
                ))}
              </select>
            </div>

            <div>
              <label className="text-xs mb-1 block" style={{ color: 'var(--text-secondary)' }}>
                API Key
                {llm?.api_key_set && <span style={{ color: '#22c55e', marginLeft: '8px' }}>已配置: {llm.api_key_preview}</span>}
                {!llm?.api_key_set && provider !== 'local' && <span style={{ color: '#ef4444', marginLeft: '8px' }}>未配置</span>}
              </label>
              <input type="password" value={apiKey} onChange={e => setApiKey(e.target.value)}
                placeholder={llm?.api_key_set ? '留空保持当前 Key' : '输入 API Key'} style={inputStyle} />
            </div>

            <div className="grid grid-cols-2 gap-3">
              <div>
                <label className="text-xs mb-1 block" style={{ color: 'var(--text-secondary)' }}>模型（可选）</label>
                <input value={model} onChange={e => setModel(e.target.value)}
                  placeholder="留空用默认" style={inputStyle} />
              </div>
              <div>
                <label className="text-xs mb-1 block" style={{ color: 'var(--text-secondary)' }}>Temperature</label>
                <input type="number" value={temperature} step={0.1} min={0} max={2}
                  onChange={e => setTemperature(Number(e.target.value))} style={inputStyle} />
              </div>
            </div>

            <div>
              <label className="text-xs mb-1 block" style={{ color: 'var(--text-secondary)' }}>自定义 Base URL（可选）</label>
              <input value={baseUrl} onChange={e => setBaseUrl(e.target.value)}
                placeholder="留空用默认，本地模型填 http://localhost:11434/v1" style={inputStyle} />
            </div>

            <button onClick={saveLLM} disabled={saving}
              className="text-xs px-4 py-2 rounded font-medium"
              style={{ backgroundColor: 'var(--color-accent)', color: '#fff', opacity: saving ? 0.5 : 1 }}>
              {saving ? '保存中...' : '保存 LLM 配置'}
            </button>
          </div>
        </div>

        {/* Divider */}
        <div style={{ borderTop: '1px solid var(--border)', margin: '16px 0' }} />

        {/* Tushare Settings */}
        <div>
          <div className="text-sm font-medium mb-3" style={{ color: 'var(--color-accent)' }}>Tushare 配置（A 股数据）</div>
          <div className="space-y-3">
            <div>
              <label className="text-xs mb-1 block" style={{ color: 'var(--text-secondary)' }}>
                Tushare Token
                {tushare?.token_set && <span style={{ color: '#22c55e', marginLeft: '8px' }}>已配置: {tushare.token_preview}</span>}
                {!tushare?.token_set && <span style={{ color: '#f59e0b', marginLeft: '8px' }}>未配置（将使用腾讯数据源）</span>}
              </label>
              <input type="password" value={tushareToken} onChange={e => setTushareToken(e.target.value)}
                placeholder={tushare?.token_set ? '留空保持当前 Token' : '输入 Tushare Token'} style={inputStyle} />
              <div className="text-xs mt-1" style={{ color: 'var(--text-secondary)' }}>
                获取 Token: <a href="https://tushare.pro/user/token" target="_blank" rel="noreferrer" style={{ color: 'var(--color-accent)' }}>tushare.pro/user/token</a>
              </div>
            </div>
            <button onClick={saveTushare} disabled={saving || !tushareToken}
              className="text-xs px-4 py-2 rounded font-medium"
              style={{ backgroundColor: 'var(--color-accent)', color: '#fff', opacity: saving || !tushareToken ? 0.5 : 1 }}>
              保存 Tushare Token
            </button>
          </div>
        </div>
      </div>
    </div>
  )
}
