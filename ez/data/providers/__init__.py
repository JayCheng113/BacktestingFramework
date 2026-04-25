"""数据源 provider 实现子包。

这里放置面向具体供应商的 `DataProvider` 实现，例如 Tushare、AKShare、
腾讯行情、FMP 和 JQData。调用方应通过 `ez.data.provider.DataProviderChain`
组合 provider，而不是直接依赖某个供应商模块的内部细节。
"""
