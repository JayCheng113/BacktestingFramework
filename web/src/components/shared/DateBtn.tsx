import { forwardRef } from 'react'

const inputStyle = { backgroundColor: 'var(--bg-primary)', border: '1px solid var(--border)', color: 'var(--text-primary)' }

const DateBtn = forwardRef<HTMLButtonElement, { value?: string; onClick?: () => void }>(
  ({ value, onClick }, ref) => (
    <button ref={ref} type="button" onClick={onClick}
      className="w-full px-2 py-1.5 rounded text-sm text-left" style={inputStyle}>
      {value || '选择日期'}
    </button>
  )
)

export default DateBtn
