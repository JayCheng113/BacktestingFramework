# Stage 1: Build frontend
FROM node:22-alpine AS frontend-builder
WORKDIR /app/web
COPY web/package.json web/package-lock.json ./
RUN npm ci
COPY web/ ./
RUN npm run build

# Stage 2: Python runtime
FROM python:3.12-slim
WORKDIR /app

# Install Python dependencies
COPY pyproject.toml ./
RUN pip install --no-cache-dir . 2>/dev/null || pip install --no-cache-dir \
    "fastapi>=0.115" "uvicorn[standard]>=0.30" "duckdb>=1.0" \
    "pandas>=2.2" "numpy>=2.0" "httpx>=0.27" "pyyaml>=6.0" \
    "pydantic>=2.9" "pydantic-settings>=2.5" "scipy>=1.14"

# Copy source code
COPY ez/ ./ez/
COPY configs/ ./configs/
COPY strategies/ ./strategies/
COPY scripts/ ./scripts/
COPY CLAUDE.md pyproject.toml ./

# Copy built frontend
COPY --from=frontend-builder /app/web/dist ./web/dist

# Create data directory
RUN mkdir -p data

# Expose port
EXPOSE 8000

# Environment variables (override with docker run -e or .env)
ENV TUSHARE_TOKEN=""
ENV FMP_API_KEY=""

# Start FastAPI serving both API and frontend
CMD ["uvicorn", "ez.api.app:app", "--host", "0.0.0.0", "--port", "8000"]
