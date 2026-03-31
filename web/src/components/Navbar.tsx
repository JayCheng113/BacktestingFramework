import { useState, useEffect } from 'react'
import SettingsModal from './SettingsModal'

interface Props {
  activeTab: string
  onTabChange: (tab: string) => void
}

const tabs = [
  { id: 'dashboard', label: '看板' },
  { id: 'portfolio', label: '组合' },
  { id: 'experiments', label: '实验' },
  { id: 'editor', label: '代码编辑器' },
  { id: 'research', label: '研究助手' },
  { id: 'docs', label: '开发文档' },
]

export default function Navbar({ activeTab, onTabChange }: Props) {
  const [showSettings, setShowSettings] = useState(false)
  const [backendOk, setBackendOk] = useState(false)

  useEffect(() => {
    const check = () => {
      fetch('/api/health').then(r => setBackendOk(r.ok)).catch(() => setBackendOk(false))
    }
    check()
    const timer = setInterval(check, 10000) // check every 10s
    return () => clearInterval(timer)
  }, [])

  return (
    <>
      <nav className="flex items-center justify-between px-6 py-3 border-b" style={{ borderColor: 'var(--border)', backgroundColor: 'var(--bg-secondary)' }}>
        <div className="flex items-center gap-6">
          <div className="flex items-center gap-2">
            <span className="text-xl font-bold" style={{ color: 'var(--color-accent)' }}>ez-trading</span>
            <span style={{
              display: 'inline-block', width: 8, height: 8, borderRadius: '50%',
              backgroundColor: backendOk ? '#22c55e' : '#ef4444',
              boxShadow: backendOk ? '0 0 6px #22c55e80' : '0 0 6px #ef444480',
              animation: 'pulse 2s ease-in-out infinite',
            }} title={backendOk ? '后端运行中' : '后端未连接'} />
            <span className="text-sm" style={{ color: 'var(--text-secondary)' }}>v0.2.11.1</span>
          </div>
          <div className="flex gap-1">
            {tabs.map(t => (
              <button key={t.id} onClick={() => onTabChange(t.id)}
                className="px-3 py-1.5 rounded text-sm"
                style={{
                  backgroundColor: activeTab === t.id ? 'var(--color-accent)' : 'transparent',
                  color: activeTab === t.id ? '#fff' : 'var(--text-secondary)',
                }}>
                {t.label}
              </button>
            ))}
          </div>
        </div>
        <div className="flex items-center gap-3">
          <span className="text-sm" style={{ color: 'var(--text-secondary)' }}>Agent-Native 量化交易平台</span>
          <button onClick={() => setShowSettings(true)}
            className="p-1.5 rounded"
            style={{ color: 'var(--text-secondary)', border: '1px solid var(--border)' }}
            title="系统设置">
            <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
              <circle cx="12" cy="12" r="3"/><path d="M19.4 15a1.65 1.65 0 0 0 .33 1.82l.06.06a2 2 0 0 1 0 2.83 2 2 0 0 1-2.83 0l-.06-.06a1.65 1.65 0 0 0-1.82-.33 1.65 1.65 0 0 0-1 1.51V21a2 2 0 0 1-2 2 2 2 0 0 1-2-2v-.09A1.65 1.65 0 0 0 9 19.4a1.65 1.65 0 0 0-1.82.33l-.06.06a2 2 0 0 1-2.83 0 2 2 0 0 1 0-2.83l.06-.06A1.65 1.65 0 0 0 4.68 15a1.65 1.65 0 0 0-1.51-1H3a2 2 0 0 1-2-2 2 2 0 0 1 2-2h.09A1.65 1.65 0 0 0 4.6 9a1.65 1.65 0 0 0-.33-1.82l-.06-.06a2 2 0 0 1 0-2.83 2 2 0 0 1 2.83 0l.06.06A1.65 1.65 0 0 0 9 4.68a1.65 1.65 0 0 0 1-1.51V3a2 2 0 0 1 2-2 2 2 0 0 1 2 2v.09a1.65 1.65 0 0 0 1 1.51 1.65 1.65 0 0 0 1.82-.33l.06-.06a2 2 0 0 1 2.83 0 2 2 0 0 1 0 2.83l-.06.06A1.65 1.65 0 0 0 19.4 9a1.65 1.65 0 0 0 1.51 1H21a2 2 0 0 1 2 2 2 2 0 0 1-2 2h-.09a1.65 1.65 0 0 0-1.51 1z"/>
            </svg>
          </button>
        </div>
      </nav>
      <SettingsModal open={showSettings} onClose={() => setShowSettings(false)} />
    </>
  )
}
