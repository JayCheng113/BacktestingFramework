/**
 * Global test setup for vitest + React Testing Library.
 *
 * - Extends `expect` with jest-dom matchers (toBeInTheDocument, etc.)
 * - Cleanup after each test to avoid DOM leakage
 */
import '@testing-library/jest-dom/vitest'
import { afterEach } from 'vitest'
import { cleanup } from '@testing-library/react'

// RTL cleanup between tests so DOM doesn't accumulate
afterEach(() => {
  cleanup()
})

// Silence ECharts "Cannot read properties of undefined (reading 'getBoundingClientRect')"
// that appears in jsdom — tests for components containing ECharts should
// mock or assert on wrapper elements, not ECharts internals.
if (typeof Element !== 'undefined' && !Element.prototype.getBoundingClientRect) {
  Element.prototype.getBoundingClientRect = () => ({
    x: 0, y: 0, width: 0, height: 0, top: 0, left: 0, right: 0, bottom: 0,
    toJSON: () => ({}),
  }) as DOMRect
}

// ResizeObserver polyfill for charts that rely on it
class MockResizeObserver {
  observe() { /* noop */ }
  unobserve() { /* noop */ }
  disconnect() { /* noop */ }
}
;(globalThis as unknown as { ResizeObserver: typeof MockResizeObserver }).ResizeObserver = MockResizeObserver
