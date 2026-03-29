interface Props {
  activeTab: string
  onTabChange: (tab: string) => void
}

const tabs = [
  { id: 'dashboard', label: '看板' },
  { id: 'experiments', label: '实验' },
  { id: 'editor', label: '代码编辑器' },
]

export default function Navbar({ activeTab, onTabChange }: Props) {
  return (
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
      <span className="text-sm" style={{ color: 'var(--text-secondary)' }}>Agent-Native 量化交易平台</span>
    </nav>
  )
}
