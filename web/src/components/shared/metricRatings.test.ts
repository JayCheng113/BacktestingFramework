/**
 * V2.25-Fe: Unit tests for shared rating helpers.
 *
 * These are pure functions; tests pin thresholds matching backend
 * `ez/research/verdict.py::VerdictThresholds` defaults. If backend
 * thresholds change, update both here and there.
 */
import { describe, it, expect } from 'vitest'
import {
  rateIc,
  rateIcir,
  rateDegradation,
  rateOverfit,
  ratePValue,
  rateDsr,
  rateMinBtl,
} from './metricRatings'

describe('rateIc', () => {
  it('strong for |ic| >= 0.05', () => {
    expect(rateIc(0.05)?.hint).toBe('强')
    expect(rateIc(0.08)?.hint).toBe('强')
    expect(rateIc(-0.06)?.hint).toBe('强')  // abs
  })
  it('medium for 0.03 <= |ic| < 0.05', () => {
    expect(rateIc(0.04)?.hint).toBe('中')
  })
  it('weak for 0.01 <= |ic| < 0.03', () => {
    expect(rateIc(0.02)?.hint).toBe('弱')
  })
  it('ineffective for |ic| < 0.01', () => {
    expect(rateIc(0.005)?.hint).toBe('无效')
  })
  it('null for null input', () => {
    expect(rateIc(null)).toBeNull()
  })
})

describe('rateIcir', () => {
  it('buckets by |icir|', () => {
    expect(rateIcir(0.6)?.hint).toBe('很稳定')
    expect(rateIcir(0.4)?.hint).toBe('较稳定')
    expect(rateIcir(0.2)?.hint).toBe('一般')
    expect(rateIcir(0.05)?.hint).toBe('不稳定')
  })
})

describe('rateDegradation (backend: max 0.40 warn, 0.70 fail)', () => {
  it('pass at 0.20', () => {
    expect(rateDegradation(0.2)).toBe('pass')
  })
  it('warn at 0.50', () => {
    expect(rateDegradation(0.5)).toBe('warn')
  })
  it('fail at 0.80', () => {
    expect(rateDegradation(0.8)).toBe('fail')
  })
  it('undefined on edge (0.40 exact → pass; slightly above → warn)', () => {
    expect(rateDegradation(0.40)).toBe('pass')
    expect(rateDegradation(0.41)).toBe('warn')
  })
  it('undefined for undefined input', () => {
    expect(rateDegradation(undefined)).toBeUndefined()
  })
})

describe('rateOverfit (backend: max 0.30 warn, 0.60 fail)', () => {
  it('pass at 0.10', () => {
    expect(rateOverfit(0.1)).toBe('pass')
  })
  it('warn at 0.40', () => {
    expect(rateOverfit(0.4)).toBe('warn')
  })
  it('fail at 0.80', () => {
    expect(rateOverfit(0.8)).toBe('fail')
  })
})

describe('ratePValue (backend: max 0.05 warn, 0.10 fail)', () => {
  it('pass at 0.01', () => {
    expect(ratePValue(0.01)).toBe('pass')
  })
  it('warn at 0.08', () => {
    expect(ratePValue(0.08)).toBe('warn')
  })
  it('fail at 0.50', () => {
    expect(ratePValue(0.5)).toBe('fail')
  })
  it('edge 0.05 → pass (strictly greater than is warn)', () => {
    expect(ratePValue(0.05)).toBe('pass')
  })
})

describe('rateDsr (backend: min 0.50 warn, 0.30 fail)', () => {
  it('pass at 0.80', () => {
    expect(rateDsr(0.8)).toBe('pass')
  })
  it('warn at 0.40', () => {
    expect(rateDsr(0.4)).toBe('warn')
  })
  it('fail at 0.20', () => {
    expect(rateDsr(0.2)).toBe('fail')
  })
})

describe('rateMinBtl', () => {
  it('undefined when required is null (cannot be significant)', () => {
    expect(rateMinBtl(5.0, null)).toBeUndefined()
  })
  it('pass when actual >= required', () => {
    expect(rateMinBtl(5.0, 3.0)).toBe('pass')
    expect(rateMinBtl(3.0, 3.0)).toBe('pass')
  })
  it('warn when actual >= 70% of required', () => {
    expect(rateMinBtl(2.5, 3.0)).toBe('warn')  // 2.5 / 3.0 = 0.83
    expect(rateMinBtl(2.1, 3.0)).toBe('warn')  // 2.1 / 3.0 = 0.70 exactly
  })
  it('fail when actual < 70% of required', () => {
    expect(rateMinBtl(1.0, 3.0)).toBe('fail')
    expect(rateMinBtl(2.0, 3.0)).toBe('fail')  // 2/3 = 0.67 < 0.70
  })
})
