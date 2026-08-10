# Hitech Drone Mapping Service

This repository contains the assessment implementation for the Hitech Drone Mapping Service. The source-of-truth design documents in this workspace are:

- `docs/Hitech_Drone_Service_System_Architecture.docx`
- `docs/Hitech_Drone_Service_System_Implementation.docx`
- `docs/Hitech_Drone_Service_Db_Schema.docx`
- `docs/Db_Schema_Converted_from_prisma_schema.txt`
- `docs/DEVELOPMENT_DECISIONS.md`
- `docs/BACKEND_BUILD_GUIDE.md`

The primary authenticated browser route is `/projects`. The administrator panel is `/admin`. Public API documentation is available at `/docs`, `/docs/redoc`, and `/api/schema`.

## Local Setup

### Prerequisites

- Windows with PowerShell
- Python 3.12+
- PostgreSQL/PostGIS reachable through `DATABASE_URL`
- A separate PostGIS test database reachable through `DATABASE_URL_TEST`
- Memurai or another Redis-compatible local service on `127.0.0.1:6379`
- Cloudflare R2 credentials in `.env`

### Install

```powershell
py -3.12 -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
Copy-Item .env.example .env
```

Fill in `.env` with the documented values from `docs/Hitech_Drone_Service_System_Implementation.docx`, including:

- `DJANGO_SECRET_KEY`
- `DATABASE_URL`
- `DIRECT_URL`
- `DATABASE_URL_TEST`
- `DIRECT_URL_TEST`
- `CELERY_BROKER_URL`
- `CELERY_RESULT_BACKEND`
- `R2_ENDPOINT_URL`
- `R2_ACCESS_KEY_ID`
- `R2_SECRET_ACCESS_KEY`
- `R2_BUCKET_NAME`
- `HITECH_AUTH_JWT_PUBLIC_KEY`

### Database Commands

Runtime migrations use the direct database connection through `config.settings_migrations`:

```powershell
.\.venv\Scripts\python.exe manage.py migrate --settings=config.settings_migrations
```

Run the Django system check against the dedicated test settings:

```powershell
.\.venv\Scripts\python.exe manage.py check --settings=config.settings_test
```

## Local Run Commands

### Django

```powershell
.\.venv\Scripts\python.exe manage.py runserver 127.0.0.1:8000
```

### Memurai or Local Redis

If Memurai is installed as a Windows service:

```powershell
Start-Service Memurai
```

If you are using another local Redis-compatible process instead, start it so that `127.0.0.1:6379` matches the configured broker and result-backend URLs.

### Celery Worker

```powershell
.\.venv\Scripts\celery.exe -A config worker -l info
```

### Celery Beat

```powershell
.\.venv\Scripts\celery.exe -A config beat -l info
```

## Routes

### Browser Routes

- `/login` - external-auth handoff page
- `/projects` - primary authenticated application route
- `/projects/{id}`
- `/projects/{id}/sites/{site_id}`
- `/surveys/{id}`
- `/admin` - administrator-only local user-management page

### Public API Documentation

- `GET /api/schema` - generated OpenAPI schema
- `GET /docs` - Swagger UI
- `GET /docs/redoc` - Redoc

### Health Probes

- `GET /health` - lightweight liveness probe
- `GET /ready` - readiness probe for PostgreSQL/PostGIS, Redis, private R2, and Celery worker reachability

## OpenAPI Export

Export a static schema artifact for submission with:

```powershell
.\.venv\Scripts\python.exe manage.py spectacular --file docs/openapi.yaml
```

## Development-Only Demo Access

This is operational scaffolding for the assessment only. It is not an in-application login flow and it does not issue production credentials.

1. Enable the local demo guards in `.env`:

```powershell
DEBUG=True
ENABLE_DEMO_AUTH=True
```

2. Generate local RSA demo keys:

```powershell
.\.venv\Scripts\python.exe manage.py init_demo_auth_keys
```

3. Restart the Django server after generating demo keys so the generated public key can be loaded when `HITECH_AUTH_JWT_PUBLIC_KEY` is otherwise unset.

4. Seed the assessment demo data:

```powershell
.\.venv\Scripts\python.exe manage.py seed_demo_assessment
```

5. Issue a short-lived demo token for a role:

```powershell
.\.venv\Scripts\python.exe manage.py issue_demo_token --role administrator
.\.venv\Scripts\python.exe manage.py issue_demo_token --role project_manager
.\.venv\Scripts\python.exe manage.py issue_demo_token --role survey_engineer
.\.venv\Scripts\python.exe manage.py issue_demo_token --role viewer
```

6. Add the emitted token value to the browser's `hitech_access_token` cookie for the local app origin, then refresh the page.

## Implemented Features

- Hitech JWT cookie authentication with RS256 validation against the external Hitech Auth subject mapping
- Server-side authorization for administrators, project managers, survey engineers, and viewers
- Versioned `/api/v1` project, site, survey, upload, processing, approval, map-layer, 3D-model, measurement, audit-log, and user-management APIs
- Public Swagger/OpenAPI documentation generated with DRF-compatible schema generation
- Administrator-only `/admin` page for local user records plus cross-project oversight through the existing APIs
- `/health` and `/ready` probes with bounded dependency checks
- Celery worker and beat integration using Redis
- Development-only demo token/key/seed commands
- Docker configuration artifacts for web, worker, beat, and Redis

## Known Limitations

- The service does not implement a fake in-application login flow. It validates only externally issued Hitech JWTs, with optional development-only demo tokens generated out of band.
- Docker configuration is included but unverified locally because virtualization is unavailable in this environment.
- The Docker web container is configured to run `collectstatic` and then start Gunicorn; Django/admin static assets are served from the collected output via WhiteNoise, while survey/map/model file payloads remain on private R2.
- Docker Compose does not provision PostgreSQL/PostGIS locally. It expects the documented external database configuration through environment variables.
- The admin panel is intentionally plain and assessment-scoped. It manages only local user records used by this service and basic cross-project oversight.
- The readiness endpoint returns only concise component states and intentionally suppresses raw provider errors, credentials, object paths, and connection details.
- The repository is not a Git checkout here, so GitHub workflow and demo-video submission items remain outside this workspace.

## Deliverables Checklist

- [x] Source code
- [x] Assessment architecture documents in `docs/`
- [x] Database schema documents in `docs/`
- [x] Versioned DRF API implementation
- [x] Public Swagger/OpenAPI routes (`/docs`, `/docs/redoc`, `/api/schema`)
- [x] Static schema export command
- [x] Targeted automated tests for implemented backend risk areas
- [x] Development-only assessment demo seed and token tooling
- [x] Dockerfile and `docker-compose.yml` included
- [ ] Docker configuration verified locally
- [ ] Demonstration video
