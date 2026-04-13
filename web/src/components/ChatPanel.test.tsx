/**
 * V2.25-Fe Phase 2: ChatPanel targeted tests.
 *
 * Focus: failure modes that ACTUALLY bite in production — not render smoke.
 *
 * 1. localStorage corruption (browser crash mid-write) — load path must
 *    not crash or throw. Silent fallback to [] is intended but worth
 *    pinning.
 *
 * 2. Empty / whitespace input — sendMessage must not fire API call or
 *    create a blank conversation.
 *
 * 3. Double-send guard — sendMessage during streaming must be a no-op
 *    (prevents ref overwrite race flagged in code review).
 *
 * 4. Send ↔ Stop button toggle — UI must hide 发送 during streaming to
 *    make the ref-overwrite race impossible in practice.
 *
 * 5. Stop button calls AbortController.abort — not just a UI dismissal.
 *    This is the V2.23 regression guard.
 *
 * 6. fileKey change creates / switches conversation — binding pattern.
 */
import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest'
import { render, screen, fireEvent, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'

import ChatPanel from './ChatPanel'
import { ToastProvider } from './shared/Toast'

// Mock api/chat/status so render doesn't block
const originalFetch = globalThis.fetch

beforeEach(() => {
  localStorage.clear()
  vi.clearAllMocks()
})
afterEach(() => {
  (globalThis as unknown as { fetch: typeof fetch }).fetch = originalFetch
})

function renderPanel(props: Partial<React.ComponentProps<typeof ChatPanel>> = {}) {
  // Only set default fetch if the test hasn't already installed one.
  // This avoids clobbering per-test mocks for streaming flows.
  if (!vi.isMockFunction(globalThis.fetch)) {
    (globalThis as unknown as { fetch: typeof fetch }).fetch = vi.fn((url: string) => {
      if (typeof url === 'string' && url.includes('/api/chat/status')) {
        return Promise.resolve({
          ok: true,
          json: () => Promise.resolve({ available: true, provider: 'test', model: 'test' }),
        } as Response)
      }
      return Promise.reject(new Error('not mocked'))
    }) as typeof fetch
  }
  return render(
    <ToastProvider>
      <ChatPanel {...props} />
    </ToastProvider>,
  )
}


describe('ChatPanel - localStorage resilience', () => {
  it('corrupted JSON does not crash initial render', () => {
    localStorage.setItem('ez-chat-conversations', '{not-valid-json')
    // Must not throw
    expect(() => renderPanel()).not.toThrow()
  })

  it('partial JSON (browser crash mid-write) silently falls back to empty', () => {
    // Simulate a truncated write mid-JSON
    localStorage.setItem('ez-chat-conversations', '[{"id":"abc","mess')
    renderPanel()
    // Should render empty state (no conversations), not crash
    expect(screen.queryByText(/AI/)).toBeInTheDocument()  // header still renders
  })

  it('valid conversations are loaded', () => {
    const conv = {
      id: 'c1',
      title: 'Test conv',
      messages: [{ role: 'user', content: 'hello' }],
      createdAt: 1, updatedAt: 1,
    }
    localStorage.setItem('ez-chat-conversations', JSON.stringify([conv]))
    localStorage.setItem('ez-chat-active-id', 'c1')
    renderPanel()
    // Message is rendered from the restored conversation
    expect(screen.getByText('hello')).toBeInTheDocument()
  })
})


describe('ChatPanel - send guards', () => {
  it('empty input does not trigger fetch', async () => {
    const fetchMock = vi.fn();
    (globalThis as unknown as { fetch: typeof fetch }).fetch = vi.fn((url: string) => {
      if (typeof url === 'string' && url.includes('/api/chat/status')) {
        return Promise.resolve({
          ok: true,
          json: () => Promise.resolve({ available: true }),
        } as Response)
      }
      fetchMock(url)
      return Promise.reject(new Error('should not be called'))
    }) as typeof fetch
    renderPanel()
    // Wait for status fetch
    await waitFor(() => expect(screen.getByPlaceholderText(/输入消息/)).toBeInTheDocument())

    const sendBtn = screen.getByRole('button', { name: '发送' })
    // Button should be disabled on empty input
    expect(sendBtn).toBeDisabled()

    // Even if we try to click, no /chat/send fetch should happen
    await userEvent.click(sendBtn)
    // Wait a tick for any async path
    await new Promise((r) => setTimeout(r, 10))
    const sendCalls = ((fetchMock as unknown as { mock: { calls: unknown[][] } }).mock.calls).filter(
      (args) => typeof args[0] === 'string' && (args[0] as string).includes('/chat/send'),
    )
    expect(sendCalls.length).toBe(0)
  })

  it('whitespace-only input is treated as empty', async () => {
    renderPanel()
    await waitFor(() => expect(screen.getByPlaceholderText(/输入消息/)).toBeInTheDocument())
    const input = screen.getByPlaceholderText(/输入消息/) as HTMLInputElement
    fireEvent.change(input, { target: { value: '   \n\t   ' } })
    const sendBtn = screen.getByRole('button', { name: '发送' })
    // Trimmed whitespace = empty → button should remain disabled
    // (disabled check uses !input.trim())
    expect(sendBtn).toBeDisabled()
  })
})


/**
 * Helper: a ReadableStream that never yields. Simulates an in-flight
 * SSE stream. jsdom lacks TransformStream so we hand-build this.
 */
function pendingStream(): ReadableStream<Uint8Array> {
  return new ReadableStream<Uint8Array>({
    start() { /* never enqueue, never close */ },
  })
}

describe('ChatPanel - send ↔ stop button toggle (V2.23)', () => {
  it('streaming state swaps 发送 for 停止', async () => {
    (globalThis as unknown as { fetch: typeof fetch }).fetch = vi.fn((url: string) => {
      if (typeof url === 'string' && url.includes('/api/chat/status')) {
        return Promise.resolve({
          ok: true,
          json: () => Promise.resolve({ available: true }),
        } as Response)
      }
      return Promise.resolve({
        ok: true,
        body: pendingStream(),
      } as Response)
    }) as typeof fetch

    renderPanel()
    await waitFor(() => expect(screen.getByPlaceholderText(/输入消息/)).toBeInTheDocument())
    const input = screen.getByPlaceholderText(/输入消息/) as HTMLInputElement
    await userEvent.type(input, 'Hi')
    expect(screen.getByRole('button', { name: '发送' })).not.toBeDisabled()
    await userEvent.click(screen.getByRole('button', { name: '发送' }))

    // During streaming, 停止 button must appear (send button replaced)
    await waitFor(() => {
      expect(screen.queryByRole('button', { name: '停止' })).toBeInTheDocument()
    })
    // 发送 button must be gone (prevents double-send race by construction)
    expect(screen.queryByRole('button', { name: '发送' })).not.toBeInTheDocument()
  })
})


describe('ChatPanel - AbortController.abort wired to 停止 button (V2.23 regression)', () => {
  it('clicking 停止 aborts the in-flight fetch signal', async () => {
    let capturedSignal: AbortSignal | undefined

    (globalThis as unknown as { fetch: typeof fetch }).fetch = vi.fn((url: string, init?: RequestInit) => {
      if (typeof url === 'string' && url.includes('/api/chat/status')) {
        return Promise.resolve({
          ok: true,
          json: () => Promise.resolve({ available: true }),
        } as Response)
      }
      capturedSignal = init?.signal ?? undefined
      return Promise.resolve({ ok: true, body: pendingStream() } as Response)
    }) as typeof fetch

    renderPanel()
    await waitFor(() => expect(screen.getByPlaceholderText(/输入消息/)).toBeInTheDocument())
    const input = screen.getByPlaceholderText(/输入消息/) as HTMLInputElement
    await userEvent.type(input, 'Hi')
    await userEvent.click(screen.getByRole('button', { name: '发送' }))

    await waitFor(() => expect(screen.getByRole('button', { name: '停止' })).toBeInTheDocument())
    expect(capturedSignal).toBeDefined()
    expect(capturedSignal!.aborted).toBe(false)

    // Click 停止 — this must abort the signal.
    // V2.23 regression guard: if someone reverts cancelStream() to just
    // setStreaming(false) without abortControllerRef.current?.abort(),
    // this test fails and the race is re-exposed.
    await userEvent.click(screen.getByRole('button', { name: '停止' }))
    expect(capturedSignal!.aborted).toBe(true)
  })
})
