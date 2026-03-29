import { useState, useEffect, useRef } from 'react'
import Editor from '@monaco-editor/react'
import ChatPanel from './ChatPanel'

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
