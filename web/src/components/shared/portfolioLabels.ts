/**
 * Shared label maps for portfolio factor categories and factor names.
 * Single source of truth — imported by PortfolioPanel, PortfolioRunContent,
 * and PortfolioFactorContent.
 *
 * V2.13.2: extracted from 3 duplicate definitions per codex reviewer S2.
 */

export const CATEGORY_LABELS: Record<string, string> = {
  technical: '量价', value: '估值', quality: '质量', growth: '成长',
  size: '规模', liquidity: '流动性', leverage: '杠杆', industry: '行业',
  ml_alpha: 'ML Alpha', other: '其他',
}

export const FACTOR_LABELS: Record<string, string> = {
  momentum_rank_20: '20日动量', momentum_rank_10: '10日动量', momentum_rank_60: '60日动量',
  volume_rank_20: '成交量排名', reverse_vol_rank_20: '低波动',
  ep: '盈利收益率(EP)', bp: '市净率倒数(BP)', sp: '市销率倒数(SP)', dp: '股息率',
  roe: 'ROE', roa: 'ROA', gross_margin: '毛利率', net_profit_margin: '净利率',
  revenue_growth_yoy: '营收增速', profit_growth_yoy: '利润增速', roe_change: 'ROE变化',
  ln_market_cap: '总市值(小盘优先)', ln_circ_mv: '流通市值(小盘优先)',
  turnover_rate: '换手率', amihud_illiquidity: '流动性',
  debt_to_assets: '低负债率', current_ratio: '流动比率',
  industry_momentum: '行业动量',
  alpha_combiner: '多因子合成',
}
