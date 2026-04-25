"""FastAPI 路由子包。

各文件按业务域拆分 APIRouter：回测、组合、数据、因子、代码编辑、研究、
模拟盘和配置。`ez.api.app` 是唯一负责挂载这些 router 的入口。
"""
