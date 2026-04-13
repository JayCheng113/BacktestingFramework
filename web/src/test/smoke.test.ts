/**
 * Smoke test to verify vitest infrastructure works.
 * If this fails, the whole suite is broken.
 */
import { describe, it, expect } from 'vitest'

describe('vitest smoke test', () => {
  it('runs', () => {
    expect(1 + 1).toBe(2)
  })

  it('has jest-dom matchers', () => {
    const div = document.createElement('div')
    div.textContent = 'hello'
    document.body.appendChild(div)
    expect(div).toBeInTheDocument()
    expect(div).toHaveTextContent('hello')
  })

  it('has jsdom environment', () => {
    expect(window).toBeDefined()
    expect(document).toBeDefined()
  })
})
