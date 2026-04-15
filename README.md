# NexusClaw

An open-source, self-hosted AI agent platform that connects your team's AI assistant to real data sources — databases, logs, code repositories, and a Python sandbox — through a familiar chat interface.

![NexusClaw Chat Interface]
![](README_files/1.jpg)
## Features

| Feature | Description |
|---------|-------------|
| **Multi-model AI** | Claude (Anthropic), OpenAI, and any OpenAI-compatible API |
| **MySQL Integration** | Natural language → SQL with per-user table-level permissions |
| **Log Analysis** | Search file logs, Elasticsearch, and Grafana Loki |
| **Knowledge Base** | Upload documents (PDF/MD/TXT) or connect Git repositories |
| **Python Sandbox** | AI executes code in isolated Docker containers with DB access |
| **Skills** | Inject custom system prompts to specialize the AI for your workflows |
| **Admin Panel** | User management, resource permissions, and audit logs |
| **Real-time Streaming** | WebSocket streaming with visible tool call steps |

## Architecture

```
Frontend (React/TS :3000)
  └─ WebSocket /api/v1/chat/ws/{id}    ← streaming responses
  └─ HTTP /api/v1/*                    ← REST API

Backend (FastAPI :8000)
  └─ chat_service.py  (agentic loop orchestrator)
       ├─ llm_service.py           → Anthropic / OpenAI streaming
       ├─ mysql_service.py         → MySQL queries with permission checks
       ├─ knowledge_service.py     → Git clone + grep/read/list/log tools
       ├─ log_service.py           → file logs + Elasticsearch + Loki
       └─ code_execution_service.py→ Python sandbox (Docker + Jupyter kernel)

Infrastructure
  └─ PostgreSQL :5432  (app data store)
  └─ Redis :6379       (cache)
  └─ claw-sandbox containers (Docker pool, managed at runtime)
```

## Quick Start

### Prerequisites

- Docker and Docker Compose
- An Anthropic API key (or OpenAI-compatible key)

### 1. Clone and configure

```bash
git clone https://github.com/your-org/nexusclaw.git
cd nexusclaw
cp .env.example .env
```

Edit `.env` and set the required values:

```bash
# Required
SECRET_KEY=    # python3 -c "import secrets; print(secrets.token_urlsafe(48))"
FERNET_KEY=    # python3 -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"
ANTHROPIC_API_KEY=your-key-here
```

### 2. Build the sandbox image

The Python sandbox requires a custom Docker image to be built before first run:

```bash
make build
```

### 3. Start all services

```bash
make dev
```

Services will be available at:

| Service | URL |
|---------|-----|
| Frontend | http://localhost:3000 |
| Backend API | http://localhost:8000 |
| API Docs (Swagger) | http://localhost:8000/docs |

### 4. First login

Open http://localhost:3000 → click **Register**.

> The first registered user automatically becomes **admin**.

---

## Make Commands

```bash
make dev        # Start with hot reload (dev mode)
make sandbox    # Start with 2 workers (production-like)
make down       # Stop all services
make build      # Rebuild backend + sandbox images (run after dependency changes)
make logs       # Stream backend logs
make ps         # Service status
```

> Always use `make dev` instead of `docker compose up` directly. The dev override mounts
> source files into containers, enabling hot reload without a full rebuild.

---

## Configuration Reference

All settings are read from `.env` at startup. See `.env.example` for the full list.

| Variable | Required | Description |
|----------|----------|-------------|
| `SECRET_KEY` | Yes | JWT signing key |
| `FERNET_KEY` | Yes | Encryption key for stored secrets |
| `ANTHROPIC_API_KEY` | Yes* | Anthropic API key |
| `DATABASE_URL` | Yes | PostgreSQL connection string |
| `REDIS_URL` | Yes | Redis connection string |
| `ANTHROPIC_BASE_URL` | No | Proxy URL for Anthropic API |
| `OPENAI_API_KEY` | No | OpenAI API key (for OpenAI models) |

*Or set an OpenAI key if using OpenAI models.

---

## Setup Guide

### Adding an AI Model

1. Go to **Admin → Models → Add**
2. Choose provider: `anthropic`, `openai`, or `openai_compatible`
3. Enter API key and model ID (e.g. `claude-opus-4-5`, `gpt-4o`)
4. Check **Set as default** for the primary model

### Connecting a MySQL Database

1. **Admin → Datasources → Add**
2. Fill in host, port, database, username, password
3. Click **Test Connection** — schema is cached automatically
4. **Admin → Users → [user] → Permissions** — assign the datasource and optionally restrict to specific tables

### Adding Log Sources

1. **Admin → Log Sources → Add**
2. Choose type: `file`, `elasticsearch`, or `loki`
3. For Loki, fill the **Description** field with LogQL label selectors (e.g. `server="api", env="prod"`) — this tells the AI which stream to query

### Adding a Knowledge Base

**Documents** (PDF, Markdown, TXT):
1. **Knowledge → Upload Document**
2. File is available immediately for AI to read in full

**Git Repositories**:
1. **Knowledge → Add Git Repo**
2. Enter the repo URL and an optional access token for private repos
3. The system clones the repo in the background (~1-3 min)
4. AI can then grep, read files, and query git log

### Creating Skills

Skills inject custom system prompts into every chat session to specialize the AI.

1. **Admin → Skills → Create**
2. Type: `system_prompt`
3. Set **Public** to inject it into all users' sessions automatically

Example skill for database analysis:
```
You are a database analyst. When the user asks about data:
1. Always check the schema before writing queries
2. Use LIMIT clauses on exploratory queries
3. Present results as tables when there are multiple rows
```

### Assigning User Permissions

1. **Admin → Users → [username] → Permissions**
2. Grant access to datasources, log sources, and knowledge bases
3. For datasources, optionally restrict to specific tables
4. Tools are enabled automatically based on what resources the user can access

---

## Python Sandbox

The AI can write and execute Python code in isolated Docker containers. Each container:
- Has 512 MB memory limit
- Runs a Jupyter kernel (state persists within a conversation)
- Has database credentials injected as `DS_<NAME>` environment variables

Example AI-generated code:
```python
import pandas as pd
import os

conn_str = os.environ['DS_MY_DATABASE']
df = pd.read_sql("SELECT * FROM orders LIMIT 100", conn_str)
df.to_csv('/output/orders.csv')
```

Output files saved to `/output/` are returned as downloadable artifacts.

**Requirements**: The `claw-sandbox` Docker image must be built with `make build` before first run.

---

## Production Deployment

### Single Server (HTTP)

```bash
make build
make sandbox
```

Users access via `http://<server-ip>:3000`. Suitable for internal/intranet use.

### With HTTPS (Nginx Reverse Proxy)

Recommended setup — serves both frontend and backend on one domain:

```nginx
server {
    listen 443 ssl;
    server_name your-domain.com;

    # Frontend
    location / {
        proxy_pass http://localhost:3000;
    }

    # Backend API + WebSocket
    location /api/ {
        proxy_pass http://localhost:8000;
        proxy_http_version 1.1;
        proxy_set_header Upgrade $http_upgrade;
        proxy_set_header Connection "upgrade";
    }
}
```

Set build-time env vars in `.env` before running `make build`:
```bash
VITE_API_URL=https://your-domain.com
VITE_WS_URL=wss://your-domain.com
```

### Environment Variables for Production

```bash
APP_ENV=sandbox
SECRET_KEY=<generate a strong key>
FERNET_KEY=<generate a Fernet key>
DATABASE_URL=postgresql+asyncpg://user:pass@db-host:5432/nexusclaw
REDIS_URL=redis://redis:6379/0
ANTHROPIC_API_KEY=<your key>
```

---

## Development

### Local Development (without Docker)

**Backend**:
```bash
cd backend
python3 -m venv venv
source venv/bin/activate
pip install -r requirements.txt
uvicorn app.main:app --reload --port 8000
```

**Frontend**:
```bash
cd frontend
npm install
npm run dev
```

Requires PostgreSQL 16 and Redis running locally.

### Project Structure

```
nexusclaw/
├── backend/
│   ├── app/
│   │   ├── api/          # Route handlers (auth, chat, datasources, knowledge, logs, skills)
│   │   ├── models/       # SQLAlchemy ORM models
│   │   ├── services/     # Business logic
│   │   │   ├── chat_service.py        # Agentic loop orchestrator
│   │   │   ├── llm_service.py         # Multi-model LLM abstraction
│   │   │   ├── mysql_service.py       # MySQL with permission checks
│   │   │   ├── log_service.py         # Log search (file + ES + Loki)
│   │   │   ├── knowledge_service.py   # Git repo + document tools
│   │   │   └── code_execution_service.py  # Docker sandbox pool
│   │   ├── utils/
│   │   │   └── security.py            # Fernet encryption helpers
│   │   └── config.py
│   └── requirements.txt
├── frontend/
│   └── src/
│       ├── pages/        # Route pages (Chat, Admin, Logs, Knowledge)
│       ├── components/   # UI components
│       ├── stores/       # Zustand state (auth, chat, results)
│       ├── hooks/        # useChat WebSocket hook
│       └── api/          # API clients
├── docker/
│   └── sandbox/          # claw-sandbox image (Jupyter kernel + FastAPI)
├── docker-compose.yml
├── docker-compose.dev.yml
├── docker-compose.sandbox.yml
├── Makefile
└── .env.example
```

### Database Schema

The app uses PostgreSQL with SQLAlchemy async models. Schema is managed via `CREATE TABLE IF NOT EXISTS` on startup — no migration tool needed.

Key models: `User`, `Conversation`, `Message`, `AIModel`, `Datasource`, `KnowledgeSource`, `LogSource`, `Skill`

---

## License

MIT
