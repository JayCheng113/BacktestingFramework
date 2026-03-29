import { useState } from 'react'
import SettingsModal from './SettingsModal'

interface Props {
  activeTab: string
  onTabChange: (tab: string) => void
}

const tabs = [
  { id: 'dashboard', label: '看板' },
  { id: 'experiments', label: '实验' },
  { id: 'editor', label: '代码编辑器' },
  { id: 'docs', label: '开发文档' },
]

export default function Navbar({ activeTab, onTabChange }: Props) {
  const [showSettings, setShowSettings] = useState(false)

  return (
    <>
      <nav className="flex items-center justify-between px-6 py-3 border-b" style={{ borderColor: 'var(--border)', backgroundColor: 'var(--bg-secondary)' }}>
        <div className="flex items-center gap-6">
          <div className="flex items-center gap-2">
            <span className="text-xl font-bold" style={{ color: 'var(--color-accent)' }}>ez-trading</span>
            <span className="text-sm" style={{ color: 'var(--text-secondary)' }}>v0.2.7</span>
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
