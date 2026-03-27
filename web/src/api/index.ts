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

export default api
