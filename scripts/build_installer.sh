#!/bin/bash
set -e

# ez-trading Installer Builder
# Creates a standalone executable that users can double-click to run
# No Python/Node/Docker needed on target machine

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
PROJECT_DIR="$(dirname "$SCRIPT_DIR")"
cd "$PROJECT_DIR"

echo "=== Step 1: Build frontend ==="
cd web
npm ci
npm run build
cd ..

echo ""
echo "=== Step 2: Install PyInstaller ==="
pip install pyinstaller 2>/dev/null || pip install --user pyinstaller

echo ""
echo "=== Step 3: Bundle with PyInstaller ==="
pyinstaller \
    --name "ez-trading" \
    --onedir \
    --noconfirm \
    --clean \
    --add-data "web/dist:web/dist" \
    --add-data "configs:configs" \
    --add-data "strategies:strategies" \
    --add-data "ez:ez" \
    --hidden-import "ez.api.app" \
    --hidden-import "ez.api.routes.market_data" \
    --hidden-import "ez.api.routes.backtest" \
    --hidden-import "ez.api.routes.factors" \
    --hidden-import "ez.strategy.builtin.ma_cross" \
    --hidden-import "ez.factor.builtin.technical" \
    --hidden-import "ez.data.providers.tushare_provider" \
    --hidden-import "ez.data.providers.tencent_provider" \
    --hidden-import "ez.data.providers.fmp_provider" \
    --hidden-import "uvicorn.logging" \
    --hidden-import "uvicorn.loops.auto" \
    --hidden-import "uvicorn.protocols.http.auto" \
    --hidden-import "uvicorn.protocols.websockets.auto" \
    --hidden-import "uvicorn.lifespan.on" \
    --collect-submodules "uvicorn" \
    --collect-submodules "fastapi" \
    --collect-submodules "duckdb" \
    launcher.py

echo ""
echo "=== Step 4: Create distribution package ==="

DIST_DIR="dist/ez-trading"

# Create .env.example in dist
cp .env.example "$DIST_DIR/.env.example"

# Create README for users
cat > "$DIST_DIR/README.txt" << 'EOF'
ez-trading v0.1.0 — Agent-Native Quant Platform
================================================

QUICK START:
1. (Optional) Copy .env.example to .env and add your Tushare token
2. Double-click "ez-trading" to launch
3. Browser opens automatically at http://localhost:8000

TUSHARE TOKEN (optional, for A-share data):
- Register at https://tushare.pro
- Copy your token to .env file: TUSHARE_TOKEN=your_token_here
- Without token, system uses Tencent Finance API as backup

TO STOP:
- Close the terminal window, or press Ctrl+C

DATA:
- Market data is cached in the "data" folder next to this file
- Delete "data" folder to reset the cache
EOF

echo ""
echo "=== Done! ==="
echo "Distribution at: $DIST_DIR"
echo ""
echo "To distribute:"
echo "  zip -r ez-trading-$(uname -s | tr '[:upper:]' '[:lower:]').zip dist/ez-trading/"
echo ""
echo "Users just:"
echo "  1. Unzip"
echo "  2. (Optional) Add .env with TUSHARE_TOKEN"
echo "  3. Double-click ez-trading"
