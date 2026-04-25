# 常见问题

---

## 数据源

**Q：如何获取 Tushare Token？**

访问 [tushare.pro](https://tushare.pro) 注册账号，注册完成后在个人中心的「接口 Token」页面可以看到你的 Token。将其填入项目根目录 `.env` 文件的 `TUSHARE_TOKEN` 字段，或在 UI 设置面板中配置。

---

**Q：Tushare 需要多少积分？**

基础行情接口（日线、复权因子等）对新注册用户免费开放，满足日常回测需求。部分高频数据或基本面数据接口需要更高积分，可在 Tushare 官网通过发帖贡献等方式积累。

如果你只是想快速体验平台功能，内置的 ETF 种子数据无需任何 Token 即可使用。

---

**Q：没有 Tushare Token 可以使用平台吗？**

可以。平台内置少量 ETF 历史数据（沪深 300 ETF 等主要品种），即使没有配置 Token 也可以运行回测，体验完整的功能流程。

需要扩充数据时，再配置 `TUSHARE_TOKEN` 并运行 `python scripts/build_data_cache.py` 构建完整缓存。

---

## A 股规则

**Q：为什么回测结果和其他平台不同？**

OpenTrading 对 A 股市场规则进行了严格模拟，这是与其他平台结果产生差异的主要原因：

- **T+1**：当日买入的股票次日才能卖出，平台严格执行此规则
- **涨跌停**：价格触及涨停时无法买入，触及跌停时无法卖出
- **印花税**：卖出时按成交金额的 0.1% 收取，计入交易成本
- **整手约束**：买入数量必须是 100 股的整数倍，不足部分自动舍去

许多简化的回测平台忽略了上述规则，导致回测结果过于乐观。OpenTrading 的设计目标是尽可能还原真实交易环境。

---

**Q：平台支持美股、港股回测吗？**

支持。平台根据标的所在市场自动适配交易规则：

- A 股（`.SZ` / `.SH`）：应用完整 A 股规则（T+1、涨跌停、印花税、整手）
- 美股：无 T+1 限制，无涨跌停，交易成本按美股标准
- 港股：应用港股规则（手数约束等）

美股数据需配置 `FMP_API_KEY`（Financial Modeling Prep），详见 [安装指南](installation.md)。

---

## 安装问题

**Q：Docker Compose 启动失败，怎么排查？**

常见原因及解决方法：

1. **Docker 版本过低**：确认 Docker 版本 ≥ 20.10，Docker Compose 版本 ≥ v2.0。运行 `docker --version` 和 `docker compose version` 检查。

2. **端口冲突**：默认使用 8000 端口，若已被占用，可在 `docker-compose.yml` 中修改端口映射，例如改为 `"8001:8000"`。

3. **`.env` 文件缺失**：首次启动前必须先运行 `cp .env.example .env`，否则服务无法读取配置。

4. **磁盘空间不足**：镜像构建需要约 2GB 空间，请确保有足够磁盘余量。

查看详细错误日志：

```bash
docker compose logs ez-trading
```

---

**Q：C++ 扩展编译失败，怎么处理？**

C++ 加速模块是可选组件，编译失败不影响任何功能使用。平台会自动检测 C++ 模块是否可用，不可用时自动回退到纯 Python 实现。

如果确实需要 C++ 加速（大规模回测场景），请检查：

- macOS：运行 `xcode-select --install` 安装 Command Line Tools
- Linux：安装 `gcc`、`g++`、`python3-dev`（Ubuntu：`sudo apt install build-essential python3-dev`）

然后重新运行：

```bash
pip install -e . --no-build-isolation
```

---

**Q：在 macOS 上运行 pytest 时出现段错误（segfault），怎么解决？**

这是 macOS 系统 `readline` 扩展与 pytest 之间的已知兼容性问题。请使用项目提供的安全脚本替代直接调用 pytest：

```bash
./scripts/run_pytest_safe.sh tests/
```

该脚本会注入本地 readline shim 并显式加载 `pytest_asyncio`，绕过段错误问题。此方案已在项目 CI 验证通过。

---

## 其他

**Q：包名是 `ez-trading`，但品牌名是 OpenTrading，为什么不一致？**

这是历史原因造成的命名分化：

- **品牌名 OpenTrading**：面向用户的产品名称，体现开源定位
- **Python 包名 `ez-trading`（命名空间 `ez`）**：开发早期使用的内部名称，已在代码中广泛使用，更改成本较高

在代码中你会看到 `import ez.xxx` 的形式，这是正常的，两者指向同一个项目。

---

**Q：模拟实盘和真实交易有什么区别？**

模拟实盘（Paper Trading）使用真实的历史和当日行情数据，按照 A 股规则模拟委托、成交、持仓变化，但不涉及真实资金。

如需对接真实券商交易，平台支持通过 QMT（迅投量化交易系统）接入，但需要额外配置并通过严格的部署门控验证。详情请参考 [功能总览 — 模拟实盘](features.md#6-模拟实盘) 章节。

---

**Q：回测结果存储在哪里？**

所有回测结果、策略配置、因子计算结果均存储在本地 DuckDB 数据库中，默认路径为 `data/ez_trading.db`。使用 Docker 时，该文件通过 volume 映射持久化在宿主机的 `./data/` 目录下，不会随容器删除而丢失。
