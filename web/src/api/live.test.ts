/**
 * V2.25-Fe Phase 3: live.ts API client targeted tests.
 *
 * Focus: parameter serialization contracts with /api/live backend.
 *
 * 1. stopDeployment(id, reason, liquidate=true) MUST send
 *    `?liquidate=true` as a STRING (not boolean, not omitted).
 *    Backend parses query string; if this regresses to
 *    `{ liquidate: true }` (boolean) or drops it entirely,
 *    positions will not be liquidated on stop — real money risk.
 *
 * 2. stopDeployment with liquidate=false (default) MUST NOT include
 *    the flag at all. Sending `?liquidate=false` would still be
 *    falsy server-side but is contract drift.
 */
import { describe, it, expect, vi, beforeEach } from 'vitest'

// Mock axios.create so we can intercept the per-instance post calls.
// vi.mock is hoisted; we use vi.hoisted() so mock fns are available inside the factory.
const { postMock, getMock } = vi.hoisted(() => ({
  postMock: vi.fn(() => Promise.resolve({ data: {} })),
  getMock: vi.fn(() => Promise.resolve({ data: {} })),
}))

vi.mock('axios', () => ({
  default: {
    create: () => ({ post: postMock, get: getMock }),
  },
}))

// Import AFTER mock so the module-level `api = axios.create(...)` picks up the stub.
import { stopDeployment } from './live'

beforeEach(() => {
  postMock.mockClear()
  getMock.mockClear()
})

describe('live.ts - stopDeployment liquidate parameter contract', () => {
  it('liquidate=true serializes to query param ?liquidate=true (string)', async () => {
    await stopDeployment('dep-123', '清仓停止', true)

    expect(postMock).toHaveBeenCalledTimes(1)
    const [url, body, config] = postMock.mock.calls[0] as unknown as [string, unknown, { params?: Record<string, unknown> }]
    expect(url).toBe('/deployments/dep-123/stop')
    expect(body).toEqual({ reason: '清仓停止' })
    // V2.16 contract: must be the STRING 'true'. Backend parses query
    // strings; boolean true would serialize differently and could be
    // rejected or silently ignored by some server frameworks.
    expect(config?.params?.liquidate).toBe('true')
    expect(typeof config?.params?.liquidate).toBe('string')
  })

  it('liquidate=false omits the param entirely', async () => {
    await stopDeployment('dep-123', '手动停止', false)

    const [, , config] = postMock.mock.calls[0] as unknown as [string, unknown, { params?: Record<string, unknown> }]
    // Must not send `liquidate=false` — contract is "omit when not liquidating".
    // Regression to `{ liquidate: 'false' }` would be a false positive some
    // backends would treat as truthy (PHP-style).
    expect(config?.params).toEqual({})
  })

  it('liquidate undefined defaults to omit', async () => {
    await stopDeployment('dep-123')

    const [, body, config] = postMock.mock.calls[0] as unknown as [string, unknown, { params?: Record<string, unknown> }]
    expect(body).toEqual({ reason: '手动停止' })
    expect(config?.params).toEqual({})
  })
})
