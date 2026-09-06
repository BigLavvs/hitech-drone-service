# Hitech Drone Mapping Service

Assessment implementation of a Django-based drone survey management service for construction projects. The repository keeps the assessment context explicit: it implements the documented workflow deeply enough to demonstrate backend architecture, authorization, secure file handling, asynchronous processing, map/model delivery, approvals, and auditability without claiming to be a finished production product.

## Problem Solved

Construction teams need a controlled way to manage drone survey datasets across projects and sites. The service lets authorized roles create project/site/survey records, upload supported 2D and 3D survey files to private object storage, process those files asynchronously into browser-viewable outputs, review survey readiness, approve or reject submissions, and retain an immutable audit trail for sensitive actions.

## Source Documents

These assessment/source documents remain part of the project history and design record:

- `docs/System_Architecture.docx`
- `docs/System_Implementation.docx`
- `docs/Database_Schema.docx`
- `docs/Db_Schema_Converted_from_prisma_schema.txt`
- `docs/DEVELOPMENT_DECISIONS.md`
- `docs/BACKEND_BUILD_GUIDE.md`
- `docs/Sequence_Diagiams/*.svg`

## Implemented Capabilities

- Server-rendered Django pages for login, projects, project detail, site detail, survey workspace, and administrator user management.
- Versioned DRF API under `/api/v1`.
- External Hitech JWT validation from an HttpOnly cookie using RS256 and a configured public key.
- Assessment-only demo access flow gated by `ENABLE_DEMO_AUTH`.
- Role and project-scope authorization for Administrators, Project Managers, Survey Engineers, and Viewers.
- Project, site, survey, membership, local user, file, processing, approval, measurement, and audit models with migrations.
- Direct multipart upload endpoint with extension, MIME, content/signature, checksum, duplicate, path-safety, file-size, and survey-size validation.
- Private Cloudflare R2 storage for originals, related OBJ/GLTF assets, generated previews, map tiles, and model metadata.
- Celery processing dispatch through Redis, with transactional dispatch after upload admission.
- Processing support for COG/tile generation, mesh conversion/preview generation, browser-ready formats, and PotreeConverter-backed point-cloud conversion when configured.
- Assessment retry schedule of 2, 5, and 10 minutes for the three automatic processing retries.
- Survey submission, approval, rejection with mandatory reason, self-approval prevention, and archival workflow.
- Server-side distance and area measurement creation.
- Audit-log write and read APIs with serialization-time filtering of sensitive details.
- Public OpenAPI schema, Swagger UI, and Redoc views.
- `/health` liveness and `/ready` dependency readiness probes.
- Docker artifacts for web, migration, worker, beat, and Redis services.

Readiness is also the Compose web healthcheck: it sends `X-Forwarded-Proto: https` so
the trusted-proxy production redirect policy is exercised correctly while Gunicorn
continues to serve HTTP inside the container. `/ready` still reports dependency
failure as HTTP 503; redirects are not treated as healthy.

## Architecture

The application is a modular Django monolith. Django renders the browser pages and DRF exposes the REST API from the same project. Domain behavior is grouped into Django apps:

- `apps/access_control`: local user records, JWT authentication, demo-session support, and administrator user APIs.
- `apps/projects`: projects, project membership, sites, and project/site authorization.
- `apps/surveys`: survey records and lifecycle rules.
- `apps/files`: upload admission, file validation, object keys, private R2 storage, and original-download redirects.
- `apps/processing`: Celery task dispatch, processing lifecycle, retry handling, generated artefacts, raster tiling, mesh conversion, and point-cloud conversion hooks.
- `apps/approvals`: submission, approval, rejection, archive workflow, and approval history.
- `apps/maps`: map-layer descriptors, tile redirects, and measurements.
- `apps/models3d`: 3D model descriptors.
- `apps/audit`: audit log model, write service, async audit task, and scoped read APIs.

PostgreSQL/PostGIS stores relational and geospatial metadata. Redis is used by Celery as the broker/result backend and by runtime readiness checks. Cloudflare R2 is private object storage; the API returns short-lived signed redirects or signed source URLs for authorized access rather than granting public bucket access. Treat signed URLs as temporary credentials.

## Roles And Authorization

- `ADMINISTRATOR`: can administer local user records, view projects across the service, and run review/archive actions while still respecting workflow readiness and state guards.
- `PROJECT_MANAGER`: manages owned projects, sites, memberships for Survey Engineers/Viewers, and survey review for owned projects.
- `SURVEY_ENGINEER`: works on assigned projects, creates surveys, uploads files, submits ready surveys, and creates measurements.
- `VIEWER`: reads assigned project/survey data and can create measurements where project visibility permits.

Browser-side role checks are presentation only. Protected API endpoints validate the JWT, enforce CSRF on unsafe cookie-authenticated requests, and apply server-side role and project-scope checks.

## Upload And Processing Flow

`POST /api/v1/surveys/{survey_id}/files` accepts one primary `file` field plus repeated `assets` fields for OBJ and GLTF bundles. Upload admission validates the content, streams bytes to private R2 staging storage, creates `SurveyFile` and `ProcessingJob` records in one transaction, moves objects to canonical private keys, and registers Celery dispatch with `transaction.on_commit()`.

Processing jobs move surveys through server-controlled states such as `UPLOADING`, `PROCESSING`, `READY`, and `FAILED`. GeoTIFF/TIFF files generate COG and private XYZ tile artefacts. OBJ/STL/PLY files convert to GLB and receive reduced GLB previews. GLB/GLTF browser-ready inputs receive preview metadata, while GLTF files with external assets are reconstructed and converted to GLB. LAS/LAZ processing depends on `POTREE_CONVERTER_PATH`.

### Recovery And Worker Ownership

- Failed dispatches remain observable on the existing job. Celery beat reconciles stale queued jobs and expired running leases; **both worker and beat must be running**.
- Each execution has a unique lease token, even when Celery redelivers the same message. Heartbeats renew only live leases. Failed ownership checks stop further storage operations and prevent stale completion, progress, retry or failure writes.
- Generated objects are written beneath `surveys/{survey_id}/files/{file_id}/attempts/{attempt_id}/`. The database publishes the successful attempt's paths only after checking ownership under a lock. An upload already in flight cannot overwrite another attempt's outputs. Readers retain compatibility with the previous storage layout.
- One cycle has an initial attempt plus three automatic retries, delayed 2, 5 and 10 minutes. Expired-worker recovery dispatches immediately and consumes a retry. An eligible active Administrator or assigned Survey Engineer can manually retry a failed job, explicitly starting a new cycle; the previous counter and operator action are audited.
- Loss of ownership does not forcibly terminate an already-running native converter. Its outputs remain unpublished. Abandoned attempt objects may remain in private storage; automatic garbage collection is not implemented. Do not apply a blanket expiry rule to all attempt folders, since successful outputs live there too.

For rollout, apply migrations, drain/stop old workers, and deploy web, workers and beat from the same revision. Older web code cannot resolve the new attempt-specific sidecars. Originals are unchanged; no data migration of already-published outputs is required.

## Approval And Audit Workflow
Survey Engineers submit only ready surveys. Administrators and owning Project Managers can approve or reject pending surveys, but cannot approve or reject a survey they created. Rejection requires a reason. Archival is exposed through `POST /api/v1/surveys/{survey_id}/archive` and does not delete survey data.

Audit records are append-only by model/service convention and are written for significant project, site, survey, upload, processing, approval, download, measurement, and administrator actions. Audit read endpoints are project-scoped and filter sensitive values such as storage paths, object keys, checksums, signed URLs, and JWT-like values at serialization time.

## Technology Stack

- Python 3.12 tested locally; source documents require Python 3.11+.
- Django 5.x, Django REST Framework, drf-spectacular.
- PostgreSQL 15+ with PostGIS.
- Redis and Celery.
- Cloudflare R2 via `boto3`.
- Rasterio/GDAL for raster processing and COG/tile generation.
- Trimesh and fast-simplification for 3D mesh conversion and preview generation.
- PotreeConverter hook for LAS/LAZ point-cloud conversion.
- Django templates, vanilla JavaScript, Leaflet-compatible map UI, and Three.js-compatible model UI.
- Gunicorn and WhiteNoise in the Docker web container.

## Repository Structure

```text
apps/                 Django domain apps
config/               Django settings, URLs, Celery app, health probes
docs/                 Assessment source docs, decision register, diagrams
static/               CSS and browser JavaScript
templates/            Server-rendered pages and shared template components
tests/                Project-level tests and browser-flow helper script
Dockerfile            Web/worker image definition
docker-compose.yml    Assessment deployment service topology
requirements.txt      Application dependencies
```

## Prerequisites

- Python 3.12 or another Python 3.11+ runtime compatible with the dependencies.
- PostgreSQL/PostGIS databases for runtime and tests.
- Redis reachable by Django and Celery.
- Cloudflare R2 bucket and credentials.
- GDAL libraries available to Python/Rasterio. On Windows, the settings module also checks `C:/Program Files/GDAL` for GeoDjango DLLs.
- PotreeConverter only if LAS/LAZ processing will be demonstrated.
- Docker only if validating the containerized deployment path.

## Local Setup

Create and activate a virtual environment with the tooling for your platform. PowerShell example:

```powershell
py -3.12 -m venv .venv
.\.venv\Scripts\python.exe -m pip install --upgrade pip
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
Copy-Item .env.example .env
```

POSIX shell example:

```sh
python3.12 -m venv .venv
. .venv/bin/activate
python -m pip install --upgrade pip
python -m pip install -r requirements.txt
cp .env.example .env
```

## Environment Configuration

`.env.example` is sanitized and grouped here by purpose. Do not commit real `.env` files, private keys, database URLs, R2 credentials, or generated demo keys.

Core Django:

- `DJANGO_SECRET_KEY`
- `DEBUG`
- `ALLOWED_HOSTS`
- `CSRF_TRUSTED_ORIGINS`

Database:

- `DATABASE_URL`: pooled/runtime PostgreSQL/PostGIS connection.
- `DIRECT_URL`: direct connection for migrations/management commands.

Authentication:

- `HITECH_AUTH_JWT_PUBLIC_KEY`
- `HITECH_AUTH_ACCESS_COOKIE_NAME`

Assessment demo access:

- `ENABLE_DEMO_AUTH`
- `DEMO_AUTH_PRIVATE_KEY`
- `DEMO_AUTH_PRIVATE_KEY_PATH`
- `DEMO_AUTH_PUBLIC_KEY_PATH`
- `DEMO_AUTH_TOKEN_TTL_SECONDS`

Object storage:

- `R2_ENDPOINT_URL`
- `R2_ACCESS_KEY_ID`
- `R2_SECRET_ACCESS_KEY`
- `R2_BUCKET_NAME`
- `R2_PUBLIC_URL` is intentionally blank unless a separate delivery domain is introduced.

Processing and limits:

- `POTREE_CONVERTER_PATH`
- `REDIS_URL`
- `REDIS_CACHE_KEY_PREFIX`: application-specific prefix for shared Redis cache keys;
  it must not be reused for Celery broker or result data.
- `CELERY_BROKER_URL`
- `CELERY_RESULT_BACKEND`
- `MAX_FILE_SIZE_BYTES`
- `MAX_SURVEY_TOTAL_SIZE_BYTES`
- `UPLOAD_CHUNK_SIZE_BYTES`
- `RATE_LIMIT_LOGIN`
- `RATE_LIMIT_GENERAL`
- `RATE_LIMIT_UPLOAD`
- `RATE_LIMIT_RETRY`
- `LOG_LEVEL`

The runtime cache uses the existing Redis deployment with the configured application
prefix. `config.settings_test` deliberately replaces it with an isolated in-memory
cache while retaining the normal `DATABASE_URL` and `DIRECT_URL` inputs for Django's
isolated test database.

The nonce-based CSP keeps scripts free of `unsafe-inline`, applies the narrowly scoped
Swagger/Redoc style compatibility to style elements only, and allows only the pinned
jsDelivr viewer resources plus configured private R2 delivery hosts. JSON logs redact
message arguments, nested structured values, request-like objects, exceptions, bearer
tokens, and signed URLs.

## Database And Demo Data

Run migrations with the direct connection settings module:

```powershell
.\.venv\Scripts\python.exe manage.py migrate --settings=config.settings_migrations
```

For local assessment demo data, enable `ENABLE_DEMO_AUTH=True`, generate gitignored demo RSA keys, and seed the four demo users plus project data:

```powershell
.\.venv\Scripts\python.exe manage.py init_demo_auth_keys
.\.venv\Scripts\python.exe manage.py seed_demo_assessment
```

Deployed assessment environments should provide `DEMO_AUTH_PRIVATE_KEY` and `HITECH_AUTH_JWT_PUBLIC_KEY` as runtime secrets instead of shipping `.demo-auth/`.

## Running The Service

Django:

```powershell
.\.venv\Scripts\python.exe manage.py runserver 127.0.0.1:8000
```

Celery worker:

```powershell
.\.venv\Scripts\celery.exe -A config worker -l info
```

Celery beat:

```powershell
.\.venv\Scripts\celery.exe -A config beat -l info
```

On POSIX systems, use the executables under `.venv/bin/` after activating the virtual environment.

## Testing

Use the dedicated test settings with non-production database credentials. `config.settings_test` reads the normal `DATABASE_URL` and `DIRECT_URL` variables, while Django creates its separate `test_<database-name>` database. The database role must be allowed to create test databases. Never use production credentials or point a custom test database name at valuable data. Do not run concurrent test processes against the same test database.

Django system check:

```powershell
.\.venv\Scripts\python.exe manage.py check --settings=config.settings_test
```

Full Django test suite:

```powershell
.\.venv\Scripts\python.exe manage.py test --settings=config.settings_test
```

Focused examples:

```powershell
.\.venv\Scripts\python.exe manage.py test tests.test_template_syntax --settings=config.settings_test
.\.venv\Scripts\python.exe manage.py test apps.processing.tests --settings=config.settings_test
```

The project also includes `tests/verify_gltf_folder_flow.mjs` for the GLTF folder-selection browser helper.

### Continuous Integration

[CI configuration](.github/workflows/ci.yml) runs on pushes to `main`, pull requests and manual dispatch. It provisions disposable PostGIS and Redis services with synthetic credentials, checks configuration and migrations, collects static assets, runs the full Django suite, checks JavaScript syntax and the GLTF helper, and builds the Docker image in a separate job. Tests mock object storage and external authentication; live R2 credentials and deployment secrets are not required.

The workflow uses pinned GitHub Action revisions; Dependabot checks action updates monthly. A workflow file is not evidence of a passing run: check the actual run for the revision being reviewed. Coolify remains the deployment mechanism; this workflow does not deploy or alter Coolify settings.

## API Documentation And Route Groups

- `GET /api/schema`: generated OpenAPI schema.
- `GET /docs`: Swagger UI.
- `GET /docs/redoc`: Redoc.

Principal API groups:

- `/api/v1/auth/validate`
- `/api/v1/demo-auth/session`
- `/api/v1/users`
- `/api/v1/projects`
- `/api/v1/projects/{project_id}/sites`
- `/api/v1/projects/{project_id}/members`
- `/api/v1/projects/{project_id}/available-members`
- `/api/v1/surveys`
- `/api/v1/surveys/{survey_id}/files`
- `/api/v1/surveys/{survey_id}/submit`
- `/api/v1/surveys/{survey_id}/approve`
- `/api/v1/surveys/{survey_id}/reject`
- `/api/v1/surveys/{survey_id}/archive`
- `/api/v1/surveys/{survey_id}/approvals`
- `/api/v1/surveys/{survey_id}/map-layers`
- `/api/v1/map-layers/{file_id}/tiles/{z}/{x}/{y}`
- `/api/v1/surveys/{survey_id}/models`
- `/api/v1/surveys/{survey_id}/measurements`
- `/api/v1/processing-jobs/{processing_job_id}`
- `/api/v1/processing-jobs/{processing_job_id}/retry`
- `/api/v1/audit-logs`

Browser routes:

- `/`
- `/login`
- `/projects`
- `/projects/{id}`
- `/projects/{id}/sites/{site_id}`
- `/surveys/{id}`
- `/admin`

## Health And Readiness

- `GET /health` is a lightweight liveness endpoint and returns `200` when the Django process can respond.
- `GET /ready` checks PostgreSQL/PostGIS, Redis, private R2 bucket access, and Celery worker reachability. It returns `200` only when all components are available and otherwise returns `503` with concise component states.

Raw provider errors, credentials, object paths, and connection strings are intentionally suppressed.

## Docker And Coolify Deployment

`docker-compose.yml` models the assessment deployment topology:

- `migrate`: runs Django migrations with `config.settings_migrations`.
- `web`: runs `collectstatic` and Gunicorn, exposes port `8000`, and uses `/ready` as the container health check.
- `worker`: runs the Celery worker.
- `beat`: runs Celery beat.
- `redis`: provides Redis for Celery.

Compose does not provision PostgreSQL/PostGIS or Cloudflare R2. Those remain external services configured through environment variables. The Dockerfile installs GDAL/PROJ system packages needed by GeoDjango/Rasterio and uses WhiteNoise for collected static assets.

## Security Controls Implemented

- RS256-only JWT validation from the `hitech_access_token` HttpOnly cookie.
- Required JWT claims: `sub`, `email`, `role`, and `exp`.
- No local password authentication for service users and no token storage in localStorage.
- CSRF enforcement for unsafe cookie-authenticated API requests.
- Server-side role and project-scope authorization.
- Private R2 storage with signed redirects/URLs for authorized file, tile, and model access.
- Upload format allow-list, MIME/content checks, filename sanitization, checksum persistence, duplicate detection, path-traversal guards, and configured size limits.
- Workflow guards for survey submission, review, rejection, archive, and processing retry.
- Audit serialization filters for sensitive details.
- Assessment demo access is disabled by default and controlled by explicit environment variables.
- Production mode (`DEBUG=False`) enables secure CSRF/session cookies and HTTPS redirects behind the configured proxy SSL header.
- Configurable rate limits are applied to demo session creation, general authenticated API requests, uploads, and processing retries.
- Responses include a nonce-based Content Security Policy for same-origin assets/API calls, pinned jsDelivr viewer libraries, and configured private R2 delivery hosts.
- Application and Celery logs are emitted as dependency-free JSON through standard Python logging.

## Assessment Demo Boundaries

The `/login` demo selector is an assessment-only exception. It does not connect to Hitech's real Auth Service and must be disabled after review:

```powershell
ENABLE_DEMO_AUTH=False
```

For deployed demos, keep `DEBUG=False`, serve over HTTPS, configure the public validation key, and inject the demo private key as a runtime secret. Demo tokens are short-lived, bounded to 30 minutes by default, and set only through the HttpOnly access-token cookie.

## Known Limitations

- No license file is present; public reuse terms are therefore unspecified until the owner chooses a license.
- Assessment/source-document redistribution permission must be confirmed by the repository owner. Repository visibility is not evidence of permission to reuse third-party assessment material.
- Docker artifacts are included, but local runtime validation depends on Docker availability and live external service credentials.
- `docker-compose.yml` depends on external PostgreSQL/PostGIS and R2 services rather than provisioning them locally.
- The external Hitech Auth Service handoff URL is not configured in settings; the implemented authentication boundary is JWT validation, plus the gated assessment demo session path.
- Resumable `UploadSession` chunk endpoints remain deferred. The implemented upload path is direct multipart admission.
- LAS/LAZ point-cloud processing requires an installed PotreeConverter executable configured with `POTREE_CONVERTER_PATH`.
- Generated 2D tile output is capped at zoom 12 for assessment-time processing speed.
- External uptime alerting and production monitoring integrations are deployment prerequisites, not implemented in this repository.
- High-availability operation, large-file load testing, real converter/R2 integration and disaster-recovery drills are not established by the mocked automated suite.

For private vulnerability reporting, see [SECURITY.md](SECURITY.md).

## Review Or Demonstration Path

1. Review `docs/DEVELOPMENT_DECISIONS.md` for intentional assessment deviations.
2. Configure `.env` from `.env.example` with runtime, test, Redis, R2, and JWT values.
3. Run migrations with `config.settings_migrations`.
4. Seed demo users/data if using the assessment demo selector.
5. Start Django, Redis, a Celery worker, and Celery beat.
6. Open `/login`, start a demo session if enabled, and demonstrate `/projects`, project/site navigation, survey upload, processing status, map/model tabs, approval workflow, measurements, audit logs, and `/admin`.
7. Run `manage.py check` and the focused/full tests with `config.settings_test`; Django will create/use its isolated test database from the normal database configuration.
