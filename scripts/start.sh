#!/bin/bash
set -e
echo "Starting ez-trading..."
mkdir -p data
# Start backend
uvicorn ez.api.app:app --host 0.0.0.0 --port 8000 &
BACKEND_PID=$!
echo "Backend started (PID: $BACKEND_PID)"
# Start frontend
cd web && npm run dev &
FRONTEND_PID=$!
echo "Frontend started (PID: $FRONTEND_PID)"
echo "Backend: http://localhost:8000"
echo "Frontend: http://localhost:3000"
echo "$BACKEND_PID $FRONTEND_PID" > /tmp/ez-trading.pids
wait
