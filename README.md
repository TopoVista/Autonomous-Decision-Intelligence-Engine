# Decision Intelligence Agent

An autonomous decision intelligence platform that turns plain-English business questions into a multi-step analytical workflow:

- intent classification
- task planning
- SQL generation and correction
- execution against a user database
- statistical analysis and anomaly detection
- hypothesis validation
- human-readable insight synthesis

The repo is structured as a monorepo with:

- `backend/` - FastAPI service, agent pipeline, DB models, migrations, and tests
- `frontend/` - Next.js app with chat, results, schema exploration, and history views

## Local Setup

### Backend

```bash
cd backend
cp .env.example .env
python -m venv venv
venv\Scripts\activate
pip install -r requirements.txt -r requirements-dev.txt
alembic upgrade head
uvicorn app.main:app --reload --port 8000
```

### Frontend

```bash
cd frontend
cp .env.local.example .env.local
npm install
npm run dev
```

Frontend env notes:
- Leave `NEXT_PUBLIC_API_URL` empty to use the built-in Next.js `/api/*` proxy.
- Set only server-side `BACKEND_API_URL` to the FastAPI origin the proxy should forward to.
- For local development, use `BACKEND_API_URL=http://127.0.0.1:8000`.
- For Vercel, leave browser traffic on the same origin and set `BACKEND_API_URL` to your Render service URL (for example, `https://your-service.onrender.com`). Do not set a `NEXT_PUBLIC_BACKEND_API_URL`.
- Only set `NEXT_PUBLIC_USE_DIRECT_API=true` if you intentionally want the browser to call the backend origin directly.

## Notes

- SQLite is supported for local development only. Set `DATABASE_URL` to a managed PostgreSQL URL in Render; the application refuses to start in production with SQLite, preventing accidental loss of users, connections, and history on Render's ephemeral filesystem.
- Dataset uploads are temporary working files stored under `/tmp`; they are intentionally not advertised as durable storage and will be unavailable after a Render restart. Use a connected database for durable analysis.
- To use OpenAI-backed agents, set `OPENAI_API_KEY` in `backend/.env`. The backend already prefers OpenAI automatically when that key is present.
- For Neon connections, keep `ssl_mode=require` and use the database password from Neon connection details. The app now validates credentials before saving a connection.

## Deployment

Architecture: browser → Vercel Next.js `/api/*` proxy → Render FastAPI → managed PostgreSQL / AI provider.

### Render

Create a web service from `render.yaml`. Set `DATABASE_URL` to the managed PostgreSQL connection URL and `OPENAI_API_KEY` if OpenAI-backed responses are required. Set `ALLOWED_ORIGINS` to your local and production frontend origins. The container respects Render's `PORT`, uses one Uvicorn worker, and uses a conservative primary-database pool (2 connections plus one overflow). `GET /health` is a lightweight liveness check.

Run migrations as a one-off release/manual command before the first deploy:

```bash
cd backend
alembic upgrade head
```

### Vercel

Import `frontend/` as the Vercel project root. Set `BACKEND_API_URL=https://your-render-service.onrender.com` in Vercel's server-side environment variables, plus the Clerk values already required by the app. Do not expose backend URLs, database URLs, or provider keys through `NEXT_PUBLIC_*`. The catch-all proxy preserves request methods, status codes, headers, and streaming response bodies.
