# OpenTrading 多阶段 Docker 构建
# Stage 1 (node-builder): 构建 React 前端
# Stage 2 (cpp-builder): 编译 C++ nanobind 扩展
# Stage 3 (runtime): Python 运行环境 + 已构建的前端和扩展
# Stage 1: Build frontend
FROM node:22-alpine AS frontend-builder
WORKDIR /app/web
COPY web/package.json web/package-lock.json ./
RUN npm ci
COPY web/ ./
RUN npm run build

# Stage 2: Build C++ extensions
FROM python:3.12-slim AS builder
WORKDIR /app
RUN apt-get update && apt-get install -y --no-install-recommends gcc g++ && rm -rf /var/lib/apt/lists/*
COPY pyproject.toml CMakeLists.txt ./
COPY ez/core/ ./ez/core/
RUN pip install --no-cache-dir scikit-build-core nanobind && \
    pip install --no-cache-dir -e . --no-build-isolation 2>/dev/null || true

# Stage 3: Python runtime
FROM python:3.12-slim
WORKDIR /app

# Install all Python dependencies (including optional akshare/tushare)
COPY pyproject.toml ./
# Required dependencies (fail build if missing)
RUN pip install --no-cache-dir \
    "fastapi>=0.115" "uvicorn[standard]>=0.30" "duckdb>=1.0" \
    "pandas>=2.2" "numpy>=2.0" "httpx>=0.27" "pyyaml>=6.0" \
    "pydantic>=2.9" "pydantic-settings>=2.5" "scipy>=1.14"
# Optional data sources (ok if unavailable)
RUN pip install --no-cache-dir "akshare>=1.14" "tushare>=1.4" 2>/dev/null || true

# Copy C++ extensions from builder (directory always exists; .so may be absent)
COPY --from=builder /app/ez/core/ /app/ez/core/

# Copy source code (all modules)
COPY ez/ ./ez/
COPY configs/ ./configs/
COPY strategies/ ./strategies/
COPY portfolio_strategies/ ./portfolio_strategies/
COPY cross_factors/ ./cross_factors/
COPY factors/ ./factors/
COPY scripts/ ./scripts/
COPY launcher.py pyproject.toml CLAUDE.md ./
COPY .env.example ./.env.example

# Copy built frontend
COPY --from=frontend-builder /app/web/dist ./web/dist

# Create data directory
RUN mkdir -p data

# Expose port
EXPOSE 8000

# Environment variables
ENV TUSHARE_TOKEN=""
ENV FMP_API_KEY=""
ENV DEEPSEEK_API_KEY=""

# Start
CMD ["uvicorn", "ez.api.app:app", "--host", "0.0.0.0", "--port", "8000"]
