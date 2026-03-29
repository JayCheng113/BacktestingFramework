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

export default api
