/**
 * Shared date range picker with calendar dropdown + quick presets.
 * Reuses react-datepicker from SearchBar, provides consistent UX across panels.
 */
import { forwardRef } from 'react'
import DatePicker from 'react-datepicker'
import 'react-datepicker/dist/react-datepicker.css'

const DateButton = forwardRef<HTMLButtonElement, { value?: string; onClick?: () => void; label: string }>(
  ({ value, onClick, label }, ref) => (
    <button
      ref={ref} type="button" onClick={onClick}
      className="flex items-center gap-2 px-3 py-1.5 rounded text-sm cursor-pointer min-w-[140px]"
      style={{ backgroundColor: 'var(--bg-primary)', border: '1px solid var(--border)', color: 'var(--text-primary)' }}
    >
      <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" style={{ color: 'var(--text-secondary)', flexShrink: 0 }}>
        <rect x="3" y="4" width="18" height="18" rx="2" ry="2"/><line x1="16" y1="2" x2="16" y2="6"/><line x1="8" y1="2" x2="8" y2="6"/><line x1="3" y1="10" x2="21" y2="10"/>
      </svg>
      <span>{value || label}</span>
      <svg width="10" height="10" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" style={{ color: 'var(--text-secondary)', marginLeft: 'auto' }}>
        <polyline points="6 9 12 15 18 9"/>
      </svg>
    </button>
  )
)

interface Props {
  startDate: string  // 'YYYY-MM-DD'
  endDate: string
  onStartChange: (d: string) => void
  onEndChange: (d: string) => void
  showPresets?: boolean
}

const toDate = (s: string) => new Date(s + 'T00:00:00')
const toStr = (d: Date) => d.toISOString().slice(0, 10)

export default function DateRangePicker({ startDate, endDate, onStartChange, onEndChange, showPresets = true }: Props) {
  const start = toDate(startDate)
  const end = toDate(endDate)
  const today = new Date()

  const setPreset = (years: number) => {
    const e = new Date()
    const s = new Date()
    s.setFullYear(e.getFullYear() - years)
    onStartChange(toStr(s))
    onEndChange(toStr(e))
  }

  return (
    <div className="flex flex-wrap gap-3 items-end">
      <div className="flex flex-col gap-1">
        <label className="text-xs" style={{ color: 'var(--text-secondary)' }}>开始日期</label>
        <DatePicker
          selected={start}
          onChange={(d: Date | null) => { if (d) { onStartChange(toStr(d)); if (d > end) onEndChange(toStr(d)) } }}
          selectsStart startDate={start} endDate={end} maxDate={end}
          dateFormat="yyyy-MM-dd" showMonthDropdown showYearDropdown dropdownMode="select"
          customInput={<DateButton label="选择开始日期" />}
        />
      </div>
      <div className="flex flex-col gap-1">
        <label className="text-xs" style={{ color: 'var(--text-secondary)' }}>结束日期</label>
        <DatePicker
          selected={end}
          onChange={(d: Date | null) => { if (d) { onEndChange(toStr(d)); if (d < start) onStartChange(toStr(d)) } }}
          selectsEnd startDate={start} endDate={end} minDate={start} maxDate={today}
          dateFormat="yyyy-MM-dd" showMonthDropdown showYearDropdown dropdownMode="select"
          customInput={<DateButton label="选择结束日期" />}
        />
      </div>
      {showPresets && (
        <div className="flex gap-1 items-end">
          {[{ label: '近1年', y: 1 }, { label: '近3年', y: 3 }, { label: '近5年', y: 5 }, { label: '近10年', y: 10 }].map(p => (
            <button key={p.label} onClick={() => setPreset(p.y)}
              className="text-xs px-2 py-1.5 rounded" style={{ color: 'var(--color-accent)', border: '1px solid var(--border)' }}>
              {p.label}
            </button>
          ))}
        </div>
      )}
    </div>
  )
}
