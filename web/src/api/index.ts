import axios from 'axios'

const api = axios.create({ baseURL: '/api' })

export const fetchKline = (params: {
  symbol: string; market: string; period: string;
  start_date: string; end_date: string;
}) => api.get('/market-data/kline', { params })

export const searchSymbols = (keyword: string, market: string = '') =>
  api.get('/market-data/symbols', { params: { keyword, market } })

export const runBacktest = (data: any) => api.post('/backtest/run', data)

export const runWalkForward = (data: any) => api.post('/backtest/walk-forward', data)

export const listStrategies = () => api.get('/backtest/strategies')

export const listFactors = () => api.get('/factors')

export const evaluateFactor = (data: any) => api.post('/factors/evaluate', data)

// Experiments
export const submitExperiment = (data: any) => api.post('/experiments', data)

export const listExperiments = (limit = 50, offset = 0) =>
  api.get('/experiments', { params: { limit, offset } })

export const getExperiment = (runId: string) => api.get(`/experiments/${runId}`)

export const deleteExperiment = (runId: string) => api.delete(`/experiments/${runId}`)

export const cleanupExperiments = (keepLast = 200) =>
  api.post('/experiments/cleanup', null, { params: { keep_last: keepLast } })

// Candidate search
export const searchCandidates = (data: any) => api.post('/candidates/search', data)

// Code editor (V2.7)
export const generateTemplate = (data: { kind: string; class_name?: string; description?: string }) =>
  api.post('/code/template', data)

export const validateCode = (code: string) => api.post('/code/validate', { code })

export const saveCode = (data: { filename: string; code: string; overwrite?: boolean }) =>
  api.post('/code/save', data)

export const listCodeFiles = () => api.get('/code/files')

export const readCodeFile = (filename: string) => api.get(`/code/files/${filename}`)

export const deleteCodeFile = (filename: string) => api.delete(`/code/files/${filename}`)

// Registry management (V2.11.1)
export const getCodeRegistry = () => api.get('/code/registry')
export const cleanupResearchStrategies = () => api.delete('/code/cleanup-research-strategies')
export const refreshRegistries = () => api.post('/code/refresh')

// Chat (V2.7)
export const chatStatus = () => api.get('/chat/status')

// Portfolio (V2.9)
export const listPortfolioStrategies = () => api.get('/portfolio/strategies')
export const runPortfolioBacktest = (data: any) => api.post('/portfolio/run', data)
export const listPortfolioRuns = (limit = 50, offset = 0) =>
  api.get('/portfolio/runs', { params: { limit, offset } })
export const getPortfolioRun = (runId: string) => api.get(`/portfolio/runs/${runId}`)
export const deletePortfolioRun = (runId: string) => api.delete(`/portfolio/runs/${runId}`)
export const portfolioWalkForward = (data: any) => api.post('/portfolio/walk-forward', data)

// Factor evaluation (V2.10)
export const evaluateFactors = (data: any) => api.post('/portfolio/evaluate-factors', data)
export const factorCorrelation = (data: any) => api.post('/portfolio/factor-correlation', data)

// Portfolio parameter search (V2.11.1)
export const portfolioSearch = (data: any) => api.post('/portfolio/search', data)

// Portfolio run weights history (V2.12.1)
export const getPortfolioRunWeights = (runId: string) => api.get(`/portfolio/runs/${runId}/weights`)
// V2.12.2 codex: /holdings returns actual post-execution weights_history
// (distinct from /weights which returns rebalance target weights). The
// "load full history" button must call /holdings so the displayed table
// matches the same semantic as the live pie chart.
export const getPortfolioRunHoldings = (runId: string) => api.get(`/portfolio/runs/${runId}/holdings`)
// Full trade list for a persisted run (V2.12.2)
export const getPortfolioRunTrades = (runId: string) => api.get(`/portfolio/runs/${runId}/trades`)

// Fundamental data (V2.11)
export const fetchFundamentalData = (data: any) => api.post('/fundamental/fetch', data)
export const fundamentalDataQuality = (data: any) => api.post('/fundamental/quality', data)
export const listFundamentalFactors = () => api.get('/fundamental/factors')

// ML Alpha Diagnostics (V2.13.2)
import type {
  MLDiagnosticsRequest,
  DiagnosticsResult,
  ValidationRequest,
  ValidationResult,
} from '../types'
export const mlAlphaDiagnostics = (data: MLDiagnosticsRequest) =>
  api.post<DiagnosticsResult>('/portfolio/ml-alpha/diagnostics', data)

// V2.22 — Unified OOS Validation
export const runValidation = (data: ValidationRequest) =>
  api.post<ValidationResult>('/validation/validate', data)

// V2.24 — Multi-sleeve weight optimization
import type { OptimizeWeightsRequest, OptimizeWeightsResponse } from '../types'
export const optimizeWeights = (data: OptimizeWeightsRequest) =>
  api.post<OptimizeWeightsResponse>('/validation/optimize-weights', data)

export default api
