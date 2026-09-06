# Hitech Drone Mapping Service: Backend Build Guide

## Purpose

This guide records backend build history, confirmed decisions, and remaining assessment-relevant gaps. Use it before starting backend tasks alongside:

- `System_Architecture.docx`
- `System_Implementation.docx`
- `Database_Schema.docx`
- `Db_Schema_Converted_from_prisma_schema.txt`
- `DEVELOPMENT_DECISIONS.md`
- the local `AGENTS.md` guidance when available (gitignored workspace policy)

The documents above remain the source of truth. This file is a historical checklist and decision index, not a replacement architecture.

## Current State

Historical note: Steps 1-4 originally created presentation-only Django template routes and shared UI for:

- `/login`
- `/projects`
- `/projects/{id}`
- `/projects/{id}/sites/{site_id}`
- `/surveys/{id}`

That early state is no longer the current implementation.

Current implementation summary:

- Django renders the public/login, project, site, survey workspace, and administrator pages.
- DRF exposes the versioned `/api/v1` API for auth validation, local users, projects, memberships, sites, surveys, files, processing jobs, approvals, map layers, 3D model descriptors, measurements, and audit logs.
- PostgreSQL/PostGIS configuration, domain models, migrations, Redis/Celery integration, private R2 storage integration, health/readiness probes, Docker artifacts, and assessment demo-auth tooling are present.
- Server-side authorization, upload validation, processing dispatch, retry handling, approval workflow guards, measurements, and audit writes are implemented in service modules and covered by focused tests.
- Browser templates are still shells at render time and rely on JavaScript/API calls for data. Template defaults should not be mistaken for an authorization or data-access layer.
- GitHub Actions CI is configured with disposable PostGIS/Redis, full Django tests, frontend checks and a separate container build. Its run status must be verified for each revision. No license is selected; deployment dependencies still require owner-provided infrastructure.

## Backend Foundations Checklist

The checklist below records the original deferred work. The current-state notes
and the source documents are authoritative; completed items are not instructions
to reimplement them.

### 1. Configuration and infrastructure (implemented)

The original SQLite-only configuration was replaced by environment-based
PostgreSQL/PostGIS settings. Runtime, migration, test, Redis/Celery, R2,
Docker/Coolify, health-check, and static-asset configuration now live in the
repository. Keep development, test, and production resources and secrets
isolated when operating them.

Historical status as of 2026-08-09: the Step 1 environment-based PostgreSQL/PostGIS configuration replaced the temporary SQLite settings. Runtime database configuration depended on `.env` values for `DJANGO_SECRET_KEY`, `DEBUG`, `ALLOWED_HOSTS`, `DATABASE_URL`, and `DIRECT_URL`. No migrations, domain apps, Redis, Celery, R2, or authentication components had been added in that step.

Current status: the runtime and migration settings modules are present. Runtime uses `DATABASE_URL`; migration commands use `config.settings_migrations` and `DIRECT_URL`; automated tests use `config.settings_test` with the normal `DATABASE_URL` and `DIRECT_URL`, while Django applies its isolated test database semantics.

### 2. Modular Django application structure

Create the documented modular-monolith boundaries before implementing the affected domain logic:

- `access_control`
- `projects`
- `surveys`
- `files`
- `processing`
- `approvals`
- `audit`
- `maps`
- `models3d`

API views are controllers only. Domain rules belong in service-layer functions. Cross-module communication must use explicit service interfaces rather than direct ORM queries across app boundaries.

### 3. Database schema and migrations

Implement the approved User, Project, ProjectMembership, Site, Survey, SurveyFile, UploadSession, ProcessingJob, Approval, ApprovalHistory, Measurement, and AuditLog models with the documented relationships, indexes, and constraints.

Important schema rules include:

- external Auth Service remains the source of truth for user identity; the local user record is for foreign keys and role caching;
- project membership is unique per project/user;
- each SurveyFile has one ProcessingJob;
- file checksum is unique per survey;
- each Survey has one Approval;
- audit records are append-only;
- survey lifecycle statuses and role values must use the documented enumerations.

### 4. Authentication, authorization, and privacy

- Validate Hitech Auth Service JWTs on every protected request.
- Enforce role permissions and project-assignment ownership on the server for every protected endpoint.
- Treat frontend role checks only as UX; never rely on them for authorization.
- Do not store user passwords or issue tokens in this service.
- Ensure users cannot access projects, sites, surveys, files, measurements, or audit records outside their permitted project scope.

### 5. Versioned DRF API

Implement serializers, DRF API views, service calls, structured success/error responses, pagination, filtering, and server-side validation.

Before writing the first endpoint, resolve the API base-path ambiguity recorded below. Apply the chosen convention consistently to every API route and frontend request.

### 6. File upload and asynchronous processing

- Stream files to private Cloudflare R2 storage; do not load large files into application memory or store file bytes in PostgreSQL.
- Enforce the documented extension, MIME, magic-byte, size, SHA-256, duplicate, filename-sanitization, and path-traversal protections.
- Apply the documented 10 GB single-file and 50 GB per-survey limits.
- Create SurveyFile and ProcessingJob in one transaction; dispatch the Celery task with `transaction.on_commit()` only after the transaction commits.
- Track progress, retries, failure details, and idempotent processing on the existing job record.
- Produce browser-ready COG/tile outputs for 2D and GLB/GLTF outputs for 3D while retaining originals.

### 7. Survey workflow, approvals, measurements, and audit

- Keep survey status transitions server-driven; clients cannot force invalid transitions.
- Survey Engineers submit; Project Managers approve or reject; rejection requires a reason; self-approval is prohibited.
- Block approval when required files are missing, processing is active or failed, or validation fails.
- Calculate and persist measurements server-side.
- Generate immutable audit events for the documented project, survey, file, processing, approval, download, measurement, and admin actions.

Status as of 2026-08-10:

- Implemented: survey-scoped measurement list/create/detail/delete API routes under `/api/v1/surveys/{survey_id}/measurements` with existing JWT cookie authentication, CSRF enforcement for unsafe requests, project-scope authorization, server-side distance/area calculation, and `MEASUREMENT_CREATED` / `MEASUREMENT_DELETED` audit writes.
- Implemented: audit-log read API routes at `/api/v1/audit-logs` and `/api/v1/audit-logs/{audit_log_id}` with project-scoped authorization, documented filters, limit-offset pagination, newest-first ordering, and response-time filtering of sensitive audit details.
- Remaining rule: keep audit storage immutable. Safety filtering applies only in API serialization and must not mutate stored audit payloads.

### 8. Testing

Before considering backend features complete, add proportionate tests for:

- JWT validation, role enforcement, and project ownership;
- project/site/survey state and API behavior;
- file validation and upload security;
- processing dispatch, idempotency, retries, and progress;
- approval guards and mandatory rejection reasons;
- measurement calculations and permissions;
- audit immutability;
- API response and error contracts.

Use the documented PostgreSQL/PostGIS test configuration for integration work. Template-only checks are not a substitute for these tests.

## Confirmed Backend Decisions

Confirmed by the project owner on 2026-08-09. Apply these consistently; do not reopen them unless the user explicitly changes them.

1. **JWT browser transport**: use an HttpOnly, Secure, SameSite cookie. Do not use localStorage. The backend authentication layer must validate the JWT while preserving the documented server-side authorization rules.
2. **API path convention**: all API endpoints use the versioned `/api/v1` base path. Resource examples such as `/projects` are relative resource paths in the documentation.
3. **Site coordinates**: use PostgreSQL/PostGIS with `PointField(srid=4326)`, as shown in the converted Django schema. Do not use JSON coordinates for the Django implementation.
4. **Viewer measurements**: Viewers with access to the survey may create and read measurements. Only Administrators and Project Managers may delete them.

## Submission And Repository Items

Current repository state:

- This workspace is a Git repository.
- Dockerfile, Compose configuration, sanitized `.env.example`, automated Django tests, assessment seed tooling, public API documentation routes, and source/decision documents are present.
- `.github/workflows/ci.yml` supplies PostGIS, Redis, required geospatial libraries and synthetic settings; it does not deploy or require Coolify secrets.
- No license file is present; do not add one until the owner chooses a license.
- `SECURITY.md` uses the owner-supplied contact `hello@oluwapelumi.xyz` for private reports.
- The owner still needs to confirm permission to redistribute assessment/source documents. Do not infer permission from public repository visibility.
- A demonstration video remains an external submission artefact, not a repository feature.

Correction batch status (2026-09-05): the internal Compose readiness request now
forwards `X-Forwarded-Proto: https`, preserving the production HTTPS redirect and
the `/ready` 200/503 dependency semantics. The runtime Redis cache uses the existing
Redis deployment with `REDIS_CACHE_KEY_PREFIX`; test settings use an isolated local
cache and do not share rate-limit state with live Redis.

Processing, files, surveys, projects, and approvals now exchange explicit snapshots
and operation-specific service calls for workflow state, relation reads, file
readiness, processing leases, and cross-module persistence. Controllers and
serializers consume those results rather than continuing ORM traversal across module
boundaries. The processing retry schedule remains the approved 2/5/10-minute cycle.

Owner-service map for the corrected runtime paths:

- `projects`: Project, Site, and ProjectMembership queries/writes; project visibility
  snapshots and member-ID reads used by access control.
- `surveys`: Survey lifecycle locks/transitions and workflow snapshots; survey
  metadata and authorization remain survey-owned.
- `files`: SurveyFile and SurveyFileAsset admission, processing snapshots, file
  readiness, delivery snapshots, and file-state writes.
- `processing`: ProcessingJob dispatch, leases, retries, job summaries, and job
  readiness; it consumes file snapshots and calls file/survey state interfaces.
- `approvals`: Approval and ApprovalHistory persistence, approval summaries, and
  archive-history writes; it calls survey workflow interfaces.
- `maps` and `models3d`: their own Measurement reads/writes and delivery catalogues;
  ready-file data comes from the files snapshot interface.
- `audit`: AuditLog writes/reads and the asynchronous download event; cross-domain
  references are passed as scalar IDs. `select_related` in audit read services is
  for audit response hydration only.

Remaining direct ORM usage is limited to each owning module, foreign-key schema
declarations, explicit service inputs, audit response hydration, and test/fixture
construction. No runtime service/controller/task relies on a foreign reverse manager
or saves a foreign module's model; no generic cross-module repository or unrestricted
field-update service was introduced.

## Current Security Configuration Notes

- `DEBUG=False` enables `CSRF_COOKIE_SECURE=True`, `SESSION_COOKIE_SECURE=True`, and `SECURE_SSL_REDIRECT=True`; `SECURE_PROXY_SSL_HEADER` remains configured for Coolify/Traefik.
- `CSRF_COOKIE_HTTPONLY=False` is intentional because browser JavaScript reads the CSRF cookie and echoes it in `X-CSRFToken`; the JWT cookie remains HttpOnly.
- Rate limits are environment-configurable through `RATE_LIMIT_LOGIN` (`5/m` default), `RATE_LIMIT_GENERAL` (`100/m` default), `RATE_LIMIT_UPLOAD`, and `RATE_LIMIT_RETRY`.
- The application emits a nonce-based CSP for the inline import map and permits only same-origin resources, pinned jsDelivr viewer libraries, and configured R2 delivery hosts. Swagger and Redoc templates receive the response nonce and propagate it to library-generated style elements; `style-src-elem` is nonce-based without `unsafe-inline`, and script `unsafe-inline`/`unsafe-eval` remain prohibited.
- Application, Django request, and Celery logging use dependency-free JSON formatting via Python's standard logging library. Messages, arguments, nested extras, request-like values, exception text, bearer tokens, and signed URLs are sanitized.
- External uptime monitoring and alerting remain deployment prerequisites; this repository does not implement an external monitoring provider.
