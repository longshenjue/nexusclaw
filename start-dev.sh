#!/bin/bash
# Start the development environment locally (without Docker)
# Prerequisites: Python 3.11+, Node 20+, PostgreSQL, Redis running

set -e

echo "=== NexusClaw - Dev Start ==="

# Check .env
if [ ! -f .env ]; then
  echo "Creating .env from .env.example..."
  cp .env.example .env
  echo "⚠️  Edit .env and set your ANTHROPIC_API_KEY before continuing"
  exit 1
fi

# Generate Fernet key if not set
if grep -q "change-this-to-a-valid-fernet-key" .env; then
  FERNET_KEY=$(python3 -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())")
  sed -i.bak "s|change-this-to-a-valid-fernet-key.*|FERNET_KEY=${FERNET_KEY}|" .env
  echo "✓ Generated Fernet key"
fi

# Generate secret key if not set
if grep -q "change-this-to-a-very-long" .env; then
  SECRET=$(python3 -c "import secrets; print(secrets.token_urlsafe(48))")
  sed -i.bak "s|change-this-to-a-very-long.*|SECRET_KEY=${SECRET}|" .env
  echo "✓ Generated secret key"
fi

# Backend
echo ""
echo "=== Starting Backend ==="
cd backend
if [ ! -d venv ]; then
  python3 -m venv venv
  echo "✓ Created virtualenv"
fi
source venv/bin/activate
pip install -q -r requirements.txt
echo "✓ Dependencies installed"

# Run DB migrations / auto-create tables on startup
echo "✓ Tables will be auto-created on first run"

# Start backend in background
uvicorn app.main:app --host 0.0.0.0 --port 8000 --reload &
BACKEND_PID=$!
echo "✓ Backend started (PID $BACKEND_PID)"

# Frontend
cd ../frontend
echo ""
echo "=== Starting Frontend ==="
if [ ! -d node_modules ]; then
  npm install
fi
npm run dev -- --port 3000 &
FRONTEND_PID=$!
echo "✓ Frontend started (PID $FRONTEND_PID)"

echo ""
echo "=== Ready ==="
echo "  Frontend: http://localhost:3000"
echo "  Backend:  http://localhost:8000"
echo "  API Docs: http://localhost:8000/docs"
echo ""
echo "Press Ctrl+C to stop all services"

# Wait and cleanup on exit
trap "kill $BACKEND_PID $FRONTEND_PID 2>/dev/null; exit" INT TERM
wait
