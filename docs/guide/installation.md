# 安装指南

本文介绍如何在本地或 Docker 环境中安装并启动 OpenTrading。

---

## 1. Docker 安装（推荐）

### 前提条件

- [Docker](https://docs.docker.com/get-docker/) 20.10+
- [Docker Compose](https://docs.docker.com/compose/install/) v2.0+

### 安装步骤

```bash
# 1. 克隆仓库
git clone https://github.com/your-org/opentrading.git
cd opentrading

# 2. 复制环境变量模板
cp .env.example .env

# 3. 编辑 .env，填入 Tushare Token（必填）
# TUSHARE_TOKEN=your_token_here

# 4. 启动服务
docker compose up
```

浏览器访问 `http://localhost:8000` 即可看到主界面。

<!-- 截图占位: Docker 启动成功后的浏览器主界面 -->

### 数据持久化

Docker Compose 已配置以下 volume 映射，停止或重建容器后数据不会丢失：

| 容器内路径 | 宿主机路径 | 说明 |
|---|---|---|
| `/app/data` | `./data` | DuckDB 数据库 + Parquet 缓存 |
| `/app/strategies` | `./strategies` | 用户单股策略代码 |
| `/app/portfolio_strategies` | `./portfolio_strategies` | 用户组合策略代码 |
| `/app/cross_factors` | `./cross_factors` | 用户截面因子代码 |
| `/app/factors` | `./factors` | 用户单股因子代码 |
| `/app/configs` | `./configs` | 用户配置覆盖文件 |

---

## 2. 本地安装

### 前提条件

- Python 3.12+
- Node.js 22+
- pip / uv

### 安装步骤

```bash
# 1. 克隆仓库
git clone https://github.com/your-org/opentrading.git
cd opentrading

# 2. 安装 Python 依赖（含所有可选依赖）
pip install -e ".[all]"

# 3. 构建前端
cd web && npm install && npm run build && cd ..

# 4. 复制并配置环境变量
cp .env.example .env
# 编辑 .env，至少填写 TUSHARE_TOKEN

# 5. 启动后端服务
uvicorn ez.api.app:app --host 0.0.0.0 --port 8000
```

浏览器访问 `http://localhost:8000`。

> **提示**：本地开发模式下，前端热重载可使用 `cd web && npm run dev`，此时 API 地址默认代理到 `http://localhost:8000`。

---

## 3. C++ 扩展（可选）

OpenTrading 包含基于 [nanobind](https://github.com/wjakob/nanobind) 的 C++ 加速模块，用于提升撮合引擎和时序运算的性能。

```bash
# 安装时自动编译（需要 C++ 编译器）
pip install -e . --no-build-isolation
```

**编译失败不影响使用**：C++ 模块是可选的，系统会自动回退到纯 Python 实现，所有功能均可正常运行，仅性能略有差异。

编译所需环境：

- macOS：Xcode Command Line Tools（`xcode-select --install`）
- Linux：`gcc` / `g++` 及 `python3-dev`
- Windows：Visual Studio Build Tools（暂未官方测试）

---

## 4. 环境变量说明

所有配置均通过根目录的 `.env` 文件设置（从 `.env.example` 复制）。主要变量如下：

| 变量名 | 是否必填 | 说明 |
|---|---|---|
| `TUSHARE_TOKEN` | **必填** | Tushare Pro Token，用于获取 A 股数据。注册地址：[tushare.pro](https://tushare.pro) |
| `FMP_API_KEY` | 可选 | Financial Modeling Prep API Key，用于美股数据 |
| `DEEPSEEK_API_KEY` | 可选 | DeepSeek API Key，用于 AI 编程助手（推荐） |
| `OPENAI_API_KEY` | 可选 | OpenAI 兼容接口 Key |
| `OPENAI_BASE_URL` | 可选 | OpenAI 兼容接口基础 URL（用于自部署或第三方代理） |
| `API_PORT` | 可选 | 服务监听端口，默认 `8000` |
| `DB_PATH` | 可选 | DuckDB 数据库路径，默认 `data/ez_trading.db` |

> **提示**：环境变量也可在 UI 设置面板（右上角齿轮图标）中动态配置，修改后立即生效，无需重启服务。

<!-- 截图占位: UI 设置面板 — 环境变量配置界面 -->

---

## 5. 数据缓存

### 内置种子数据

安装完成后，系统内置少量 ETF 种子数据（如沪深 300 ETF、中证 500 ETF 等），可直接用于快速体验回测功能，无需配置 Tushare Token。

### 完整数据缓存

如需完整的 A 股历史行情数据，配置好 `TUSHARE_TOKEN` 后运行：

```bash
python scripts/build_data_cache.py
```

该脚本会将常用标的的日线数据下载并存储为本地 Parquet 文件，后续回测优先从本地缓存读取，速度更快且不消耗 API 积分。

> **注意**：完整缓存构建可能需要较长时间（取决于 Tushare 积分和网络状况），建议在非交易时段运行。
