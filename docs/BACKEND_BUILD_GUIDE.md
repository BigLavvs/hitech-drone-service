# Hitech Drone Mapping Service: Backend Build Guide

## Purpose

This guide records the backend work deliberately deferred during the frontend-first steps. Use it before starting each backend task alongside:

- `Hitech_Drone_Service_System_Architecture.docx`
- `Hitech_Drone_Service_System_Implementation.docx`
- `Hitech_Drone_Service_Db_Schema.docx`
- `Db_Schema_Converted_from_prisma_schema.txt`
- `AGENTS.md`

The documents above remain the source of truth. This file is a build checklist and decision register, not a replacement architecture.

## Current State

Steps 1-4 created presentation-only Django template routes and shared UI:

- `/login`
- `/projects`
- `/projects/{id}`
- `/projects/{id}/sites/{site_id}`
- `/surveys/{id}`

They do not query a database, call APIs, authenticate users, enforce permissions, upload files, process data, or write audit records. That is intentional and must change only through documented backend work.

## Required Backend Foundations

### 1. Configuration and infrastructure

- Replace the temporary SQLite-only settings with the documented PostgreSQL + PostGIS/Neon configuration before adding domain models or migrations.
- Use environment variables for secrets and environment-specific configuration. The current hardcoded development `SECRET_KEY` is temporary and must not survive into authentication, deployment, or production-like work.
- Add the documented Redis, Celery worker, Celery beat, Cloudflare R2, Docker/Coolify, health-check, and static-asset setup only when their corresponding features are being implemented.
- Keep development and production database, Redis, R2 bucket, JWT key, and secret values isolated.

Status as of 2026-08-09: the Step 1 environment-based PostgreSQL/PostGIS configuration has replaced the temporary SQLite settings. Runtime database configuration now depends on `.env` values for `DJANGO_SECRET_KEY`, `DEBUG`, `ALLOWED_HOSTS`, `DATABASE_URL`, and `DIRECT_URL`. No migrations, domain apps, Redis, Celery, R2, or authentication components have been added in this step.

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

## Deferred Submission Items

Before final submission, ensure the repository also contains the documented Git/GitHub workflow, Docker configuration, environment template, CI workflow, automated tests, sample data, API documentation, and demonstration video. The current workspace is not a Git repository, so version-control setup remains outstanding.
