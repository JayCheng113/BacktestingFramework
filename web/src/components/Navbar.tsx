export default function Navbar() {
  return (
    <nav className="flex items-center justify-between px-6 py-3 border-b" style={{ borderColor: 'var(--border)', backgroundColor: 'var(--bg-secondary)' }}>
      <div className="flex items-center gap-2">
        <span className="text-xl font-bold" style={{ color: 'var(--color-accent)' }}>ez-trading</span>
        <span className="text-sm" style={{ color: 'var(--text-secondary)' }}>v0.1.0</span>
      </div>
      <span className="text-sm" style={{ color: 'var(--text-secondary)' }}>Agent-Native Quant Platform</span>
    </nav>
  )
}
