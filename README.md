# Tayal ERP

Cement & TMT distribution ERP for **Ishika Traders Daily**: orders, multi-godown inventory, dispatch & delivery, and payment collection, with read-only TallyPrime integration.

Design pack: [docs/00-index.md](docs/00-index.md) · Decisions: [docs/09-open-questions.md](docs/09-open-questions.md)

## Status

| Step | State |
|------|-------|
| 1 Architecture & planning | ✅ done |
| 2 Foundation | 🟡 Backend: sign-in, roles, company scoping, masters, dealers and godowns from Tally, customer rates, unit conversions, audit log, settings (41 tests). Web: sign-in, dealers, godowns with the ERP on/off switch, products, vehicles, users, business rules (browser smoke test). **Android app: not started** (Flutter SDK unavailable in the build environment). |
| 3–8 | not started |

## Run locally (Docker Desktop)

```bash
cp .env.example .env          # set ERP_JWT_SECRET to a long random string
./dev.sh up                   # postgres, redis, minio, api, worker, beat
./dev.sh seed                 # roles/permissions/units + Ishika Traders Daily + own fleet + product catalog
./dev.sh admin 98XXXXXXXX "Akhil"
./dev.sh seed-demo            # optional: separate "DEMO Trading Co" with fake dealers
./dev.sh test                 # backend tests
```

Web app: http://localhost:3000 · API docs: http://localhost:8000/api/v1/docs · Health: http://localhost:8000/health/ready

### Bring in customers and godowns from Tally

Tally must be open with *Ishika Traders Daily* loaded, and its HTTP port enabled (F1 Help → Settings → Connectivity → TallyPrime acts as *Both*, port 9000).

```bash
docker compose exec api python -m app.cli sync-tally-masters --tally-url http://<tally-pc-tailscale-ip>:9000 --dry-run   # look first
docker compose exec api python -m app.cli sync-tally-masters --tally-url http://<tally-pc-tailscale-ip>:9000
```

Safe to re-run. Godowns you switched off stay off. Anything deleted in Tally is flagged, never deleted here.

## Run the backend without Docker

Needs Python 3.12+, PostgreSQL 16, and (for background jobs) Redis.

```bash
cd backend
python3 -m venv .venv && . .venv/bin/activate
pip install -e ".[dev]"
export ERP_DATABASE_URL=postgresql+psycopg://USER:PASS@localhost:5432/tayal_erp
export ERP_JWT_SECRET=<long random string>
alembic upgrade head
python -m app.cli seed-base && python -m app.cli seed-company && python -m app.cli seed-catalog
python -m app.cli create-admin --phone 98XXXXXXXX --name "Akhil"
uvicorn app.main:app --reload
```

Tests (real Postgres; uses a separate `tayal_erp_test` database):

```bash
ERP_TEST_DATABASE_URL=postgresql+psycopg://USER:PASS@localhost:5432/tayal_erp_test pytest
```

## What the seeds contain

- **seed-company**: Ishika Traders Daily (non-GST), its own-stock owner, and an "Own fleet" transporter. Godowns are not seeded: they come from Tally via `sync-tally-masters`.
- **seed-catalog**: brands (Ambuja, Shree, JSW, Kamdhenu) and the TMT kg-per-metre chart, which uses **IS 1786 nominal values as a placeholder** until Kamdhenu's own chart is entered.
- **Dealers, godowns and products are not seeded.** They come from the Tally sync. `seed-catalog` only adds brands and the TMT weight chart. The demo company has fake ones only.

## Web app development

```bash
cd web && npm install
API_URL=http://localhost:8000 npm run dev        # http://localhost:3000
npm run gen:api       # after backend API changes: regenerate typed client (needs backend/openapi.json)
npm run typecheck && npm run build
E2E_PHONE=... E2E_PASSWORD=... npm run test:e2e   # browser smoke test against a running stack (needs Playwright)
```

Regenerate `backend/openapi.json` with `cd backend && python -c "import json; from app.main import app; json.dump(app.openapi(), open('openapi.json','w'), indent=1)"`.

## Layout

```
backend/            FastAPI + SQLAlchemy 2 + Alembic + Celery
  app/core/         config, db, auth/security, permissions, errors, audit, pagination, sequences
  app/modules/      identity · masters · platform  (orders, inventory, dispatch, collections, integrations next)
  alembic/          migrations
  tests/            pytest against real Postgres
docs/               design pack
infrastructure/     docker init scripts (deployment added in Step 8)
web/                Next.js dashboard (typed client generated from the API spec)
mobile/ integrations/tally-connector/   (coming in the next increments)
```
