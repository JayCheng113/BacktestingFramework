import { useState, useCallback, createContext, useContext, useRef } from 'react'

interface ToastMessage {
  id: number
  type: 'success' | 'error' | 'warning' | 'info'
  message: string
}

interface ToastContextValue {
  showToast: (type: ToastMessage['type'], message: string) => void
}

const ToastContext = createContext<ToastContextValue>({ showToast: () => {} })

export function useToast() {
  return useContext(ToastContext)
}

export function ToastProvider({ children }: { children: React.ReactNode }) {
  const [toasts, setToasts] = useState<ToastMessage[]>([])
  const nextId = useRef(0)

  const showToast = useCallback((type: ToastMessage['type'], message: string) => {
    const id = nextId.current++
    setToasts(prev => [...prev, { id, type, message }])
    setTimeout(() => {
      setToasts(prev => prev.filter(t => t.id !== id))
    }, type === 'error' ? 8000 : 4000)  // errors stay longer
  }, [])

  const removeToast = useCallback((id: number) => {
    setToasts(prev => prev.filter(t => t.id !== id))
  }, [])

  const colors: Record<string, { bg: string; border: string; text: string }> = {
    success: { bg: 'rgba(34,197,94,0.15)', border: '#22c55e', text: '#22c55e' },
    error: { bg: 'rgba(239,68,68,0.15)', border: '#ef4444', text: '#ef4444' },
    warning: { bg: 'rgba(245,158,11,0.15)', border: '#f59e0b', text: '#f59e0b' },
    info: { bg: 'rgba(59,130,246,0.15)', border: '#3b8cf6', text: '#3b8cf6' },
  }

  return (
    <ToastContext.Provider value={{ showToast }}>
      {children}
      {/* Keyframes for toast animation */}
      <style>{`@keyframes fadeIn { from { opacity: 0; transform: translateY(-8px); } to { opacity: 1; transform: translateY(0); } }`}</style>
      {/* Toast container — fixed top-right */}
      <div style={{ position: 'fixed', top: 16, right: 16, zIndex: 9999, display: 'flex', flexDirection: 'column', gap: 8, maxWidth: 400 }}>
        {toasts.map(t => {
          const c = colors[t.type] || colors.info
          return (
            <div key={t.id} onClick={() => removeToast(t.id)}
              style={{ padding: '10px 14px', borderRadius: 6, border: `1px solid ${c.border}`,
                       backgroundColor: c.bg, color: c.text, fontSize: 13,
                       cursor: 'pointer', animation: 'fadeIn 0.2s ease-in',
                       wordBreak: 'break-word', whiteSpace: 'pre-line' }}>
              {t.message}
            </div>
          )
        })}
      </div>
    </ToastContext.Provider>
  )
}
