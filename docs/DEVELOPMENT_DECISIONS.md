# Hitech Drone Mapping Service: Development Decision Register

## Purpose

This register records implementation decisions confirmed during development when the assessment documents define the requirement but not the precise implementation mechanism.

The assessment documents and approved architecture remain the source of truth. Read this file with `AGENTS.md` and `docs/BACKEND_BUILD_GUIDE.md` before starting affected work.

## Confirmed Decisions

### 2026-08-09 — Browser JWT transport

**Decision:** Browser JWTs are delivered in an HttpOnly, Secure, SameSite cookie. They must not be stored in localStorage.

**Impact:** The later access-control implementation validates the JWT server-side on every protected request. Frontend role checks remain UX-only.

### 2026-08-09 — External Hitech Auth JWT validation boundary

**Decision:** The Drone Mapping Service validates external Hitech Auth JWTs locally with the RSA public key provided through `HITECH_AUTH_JWT_PUBLIC_KEY`. Browser access tokens are read only from the `hitech_access_token` HttpOnly, Secure, SameSite cookie, and only `RS256` is accepted.

**Impact:** The access-control layer requires `sub`, `email`, `role`, and `exp` claims, maps `sub` to the existing local `User.external_id`, rejects missing, expired, malformed, wrongly signed, unknown, or inactive users with `401`, and never auto-creates local users, stores passwords, creates Django sessions, uses localStorage, or issues tokens. After successful validation, the existing local user's cached `email` and `role` are refreshed from the trusted token claims when they differ.

### 2026-08-09 — API path convention

**Decision:** All API endpoints use the versioned `/api/v1` base path.

**Impact:** Documentation examples such as `/projects` are resource paths; every implemented API route and frontend request must use the versioned base path.

### 2026-08-09 — Site coordinate storage

**Decision:** Site coordinates use PostgreSQL/PostGIS through GeoDjango `PointField(srid=4326)`.

**Impact:** Do not substitute JSON coordinate fields in the Django schema.

### 2026-08-09 — Viewer measurements

**Decision:** Viewers with survey access may create and read measurements. Only Administrators and Project Managers may delete them.

**Impact:** Enforce this rule server-side when measurement endpoints and services are implemented.

### 2026-08-09 — Project Manager required when creating projects

**Decision:** Every newly created project must have a Project Manager assigned. This applies when an Administrator creates a project as well as when a Project Manager creates one.

**Reason:** The approved architecture defines a project as owned by a Project Manager. Requiring the assignment at creation prevents unowned projects and makes the documented ownership access rules immediately applicable.

**Impact:** The later project-creation service and API must reject creation without a Project Manager. A Project Manager creating a project must become its assigned Project Manager; an Administrator may assign the Project Manager explicitly.

### 2026-08-09 — Project ownership transfer

**Decision:** An Administrator or the current owning Project Manager may transfer a project's `project_manager` assignment to another active user with the `PROJECT_MANAGER` role.

**Impact:** The transfer must be enforced server-side, recorded as a project update audit event, and take effect immediately. After transfer, the former Project Manager no longer has owner-level project access; the new Project Manager does.

### 2026-08-09 — Project archive transition

**Decision:** Only an active project may be archived. Attempting to archive an already archived project must be rejected and must not create another audit event.

**Impact:** The project-archive service must enforce the one-way `active` to `archived` transition and write exactly one `PROJECT_ARCHIVED` audit event for a successful archive.

### 2026-08-09 — Archived-project site changes

**Decision:** Site creation, editing, and deletion are permitted only while the parent project is active. All of these operations must be rejected for an archived project and must not create an audit event.

**Impact:** The site-management service must enforce the parent-project status before making any site change, keeping an archived project and its related records frozen consistently.

### 2026-08-09 — Survey metadata updates and archived projects

**Decision:** Survey metadata may be updated after creation. Add the explicit `SURVEY_UPDATED` audit action and record it for successful survey-detail updates. Administrators, the owning Project Manager, and the Survey Engineer who created the survey while still assigned to its project may update it. Other Survey Engineers and Viewers may not. Survey creation and survey metadata updates are permitted only while the parent project is active; an archived project must reject both operations without creating an audit event.

**Impact:** Survey creation and update services must enforce the approved role and assignment rules, use the approved audit action, and preserve the append-only audit trail. Submission remains a separate workflow governed by file and processing readiness.

### 2026-08-09 — Local asynchronous-processing configuration

**Decision:** Local development uses the existing Memurai service at `127.0.0.1:6379`. Celery uses Redis database 0 as its broker and database 1 as its result backend. Cloudflare R2 remains private; `R2_PUBLIC_URL` is intentionally blank until a delivery domain is configured, and file access will use authorised signed URLs rather than public object URLs.

**Impact:** Local environment values must use the approved Redis URLs. The R2 integration must not require a public domain or expose object storage publicly.

### 2026-08-09 — Survey upload and processing state transitions

**Decision:** A successfully accepted file upload moves its survey to `UPLOADING` with `processing_status="queued"`. Processing start moves it to `PROCESSING` with `processing_status="processing"`. When all required files are ready, it moves to `READY` with `processing_status="completed"`. A file-processing failure moves it to `FAILED` with `processing_status="failed"`.

**Impact:** File admission and processing services must make these state changes server-side. A survey becomes eligible for the later submission workflow only when it is `READY`.

### 2026-08-09 — Upload format-validation policy

**Decision:** Secure upload validation permits only the documented formats: GeoTIFF/TIFF, PNG, JPEG, KML, and GeoJSON for 2D files; and OBJ, GLB, GLTF, LAS, LAZ, PLY, and STL for 3D files. Each upload must have a matching extension and MIME type, with binary signatures verified where reliable and textual formats validated as UTF-8 structured content. Ambiguous or mismatched files are rejected.

**Impact:** The files module must derive and verify the allowed format rather than trusting client-supplied type metadata. No unsupported format may reach R2 or processing.

### 2026-08-09 — Upload object-key lifecycle

**Decision:** Upload bytes first use a private, server-generated staging key. Once the upload is admitted and its `SurveyFile` record exists, the service must copy or move the object to the documented canonical key: `surveys/{survey_id}/files/{file_id}/raw.{ext}`. The staging object must then be removed.

**Reason:** The approved storage convention requires the database-generated `SurveyFile` identifier, which is unavailable before admission. A private staging key preserves streamed upload behaviour while allowing the final persisted object to use the documented hierarchy.

**Impact:** The upload-admission service must never persist a staging key as `SurveyFile.storage_path`; it must complete the staging-to-canonical transition and clean up staging on a failed admission.

### 2026-08-09 — Survey states that accept file uploads

**Decision:** File uploads are permitted while a survey is `DRAFT`, `UPLOADING`, `PROCESSING`, `FAILED`, or `READY`. They are rejected while it is `PENDING_APPROVAL`, `APPROVED`, `REJECTED`, or `ARCHIVED`. A successfully accepted upload moves the survey to `UPLOADING` with `processing_status="queued"`.

**Reason:** Additional datasets may be required while previous files process or after a survey is ready. Once a survey enters the approval or finalised states, its accepted dataset must remain stable.

**Impact:** The upload-admission service must enforce the allowed states server-side before storing bytes. A valid new upload during `PROCESSING`, `FAILED`, or `READY` returns the survey to the documented upload state.

### 2026-08-09 — 3D mesh conversion library

**Decision:** Use `trimesh` for server-side conversion of OBJ, STL, and PLY mesh uploads into browser-ready GLB output. Use the documented GDAL/Rasterio stack for 2D COG processing.

**Impact:** The processing task will use `trimesh` for the approved mesh formats. LAS/LAZ point-cloud conversion uses the externally installed PotreeConverter executable, configured through `POTREE_CONVERTER_PATH`, because the approved architecture specifies Potree or a similar dedicated converter.

### 2026-08-09 — OBJ related assets

**Decision:** OBJ support must preserve referenced MTL and texture assets together with the primary OBJ, as required by the assessment. Use a small `SurveyFileAsset` record owned by the files module for each related asset, linked to its primary OBJ `SurveyFile`. Store them privately beneath `surveys/{survey_id}/files/{file_id}/assets/`.

**Reason:** The approved upload whitelist alone cannot represent OBJ dependencies, while the approved architecture explicitly requires related OBJ, MTL, and texture files to be preserved together. A linked asset record keeps the documented primary `SurveyFile` contract unchanged while retaining required metadata, ownership, size, checksum, and storage-path tracking.

**Impact:** MTL and PNG/JPEG texture files are accepted only as declared assets of an OBJ bundle, not as independent survey datasets. Bundle size must count towards the existing 50 GB survey limit, and the later multipart endpoint and processing task must resolve the primary file and its assets together.

### 2026-08-09 — Browser-ready processing outputs

**Decision:** GeoTIFF/TIFF files are converted to COG through Rasterio/GDAL. OBJ, STL, and PLY meshes are converted to GLB through `trimesh`; referenced OBJ assets are made available during conversion. LAS/LAZ files are converted through PotreeConverter into private point-cloud output. PNG, JPEG, KML, GeoJSON, GLB, and GLTF are already browser-ready and retain their original private object as the viewer source rather than undergoing a redundant conversion.

**Impact:** `converted_path` is populated only when the worker creates a new converted artefact. Viewers later use the raw private path for already browser-ready formats and `converted_path` for generated COG, GLB, or Potree output. Processing still validates, checks integrity, extracts relevant metadata, records state, and creates previews or proxies where a generated processing path requires them.

### 2026-08-09 — Processing completion audit event

**Decision:** Add `PROCESSING_COMPLETED` to the audit action enumeration and record it when a processing job successfully completes.

**Reason:** The approved processing sequence diagram explicitly records a completion audit event. The older schema action list omits it, but omitting a successful terminal processing event would leave the required audit trail incomplete.

**Impact:** The processing worker writes `PROCESSING_STARTED`, `PROCESSING_RETRY`, `PROCESSING_FAILED`, and `PROCESSING_COMPLETED` events at the relevant server-side transitions. The required model migration must update the audit action choices.

### 2026-08-09 — Processing retry semantics

**Decision:** A processing job has one initial attempt and up to three automatic retries, for a maximum of four total attempts. `retry_count` counts retries only, beginning at zero. Automatic retry delays are 5, 15, and 45 minutes.

**Reason:** The approved implementation document explicitly specifies three retries and all three backoff delays. Treating those as only three total attempts would discard the documented 45-minute retry.

**Impact:** A job becomes permanently failed only after the initial attempt and all three retries fail. Manual retry is allowed only for a permanently failed job whose retry count remains below the configured limit, as required by the later retry workflow.

### 2026-08-09 — Project membership management

**Decision:** Administrators may add or remove Survey Engineers and Viewers from any project. The current owning Project Manager may add or remove Survey Engineers and Viewers from their own project.

**Impact:** Membership changes must be enforced server-side and audited with the existing `PROJECT_UPDATED` action, with the add/remove operation and member identity recorded in audit `details`. They do not change a user's role. Project Managers may not use membership management to assign Administrators or other Project Managers.

**Removal rule:** Removing a user who is not currently assigned to the project must be rejected and must not write an audit event.

**Archived projects:** Membership changes are permitted only while a project is active. Adding or removing a member on an archived project must be rejected and must not write an audit event.

### 2026-08-09 — Database connection used by Django migrations

**Decision:** Normal application runtime uses the pooled Neon connection in `DATABASE_URL`. Django migration-related commands use the direct Neon connection in `DIRECT_URL` through a separate, explicit migration settings module (for example, `config.settings_migrations`).

**Reason:** The approved implementation architecture distinguishes the pooled runtime connection from the direct connection required for Django migrations, but does not prescribe the switching mechanism. A dedicated settings module makes the distinction explicit and avoids hidden command-based configuration changes.

**Impact:** Before the first domain migration is created and applied, implement the dedicated migration settings module and document the exact migration command. Do not alter the normal runtime database configuration.

### 2026-08-09 — Local User superuser behaviour

**Decision:** The local `User` model remains based on `AbstractBaseUser` without Django's `PermissionsMixin`. `UserManager.create_superuser()` must not set an unsupported `is_superuser` field; it creates an active staff user with the documented `ADMINISTRATOR` role instead.

**Reason:** The approved database schema defines the role model and does not define `is_superuser`, groups, or Django permission tables. External Hitech Auth remains the identity authority.

**Impact:** Server-side application authorization will later use the documented role and project-assignment rules, not Django's generic model-permission system.

### 2026-08-09 — Database connection used by automated tests

**Decision:** Automated database tests use a separate, explicit `config.settings_test` settings module, backed by `DATABASE_URL_TEST` and `DIRECT_URL_TEST`. They must never use the development runtime database configuration.

**Reason:** The approved documentation requires a separate PostGIS test database. Mirroring the explicit migration-settings approach prevents accidental testing against the development database.

**Impact:** Database tests must be run with the test settings module. The test database configuration must preserve Django's PostGIS backend.

### 2026-08-09 — 3D preview proxy outputs

**Decision:** Use `fast-simplification` as the direct helper for the approved `trimesh` processing stack to create low-poly GLB previews. Mesh uploads store that reduced private GLB in `preview_path` while retaining the existing full GLB conversion in `converted_path`. LAS/LAZ files use Potree's private `metadata.json` entry point as both `preview_path` and `converted_path`. Raw GLB/GLTF uploads remain unconverted and store only a separate reduced GLB preview proxy.

**Impact:** 3D viewer flows keep the documented original-versus-converted behaviour while always exposing a lightweight private preview/proxy artefact for supported model formats.

### 2026-08-09 â€” External-auth assessment demo

**Decision:** No fake in-application login, token issuer, or password flow will be added for the assessment. If Hitech Auth is unavailable during the demo, any signed-token workaround must remain clearly labelled, development-only, and out of band from the application itself.

**Impact:** The Django application continues to validate externally issued Hitech JWTs only. Any temporary demo token setup is operational scaffolding, not application functionality.

### 2026-08-09 â€” Cookie-JWT CSRF

**Decision:** Unsafe same-origin API requests use Django's readable `csrftoken` cookie together with the `X-CSRFToken` header, while the Hitech JWT remains in an HttpOnly cookie.

**Impact:** Protected DRF endpoints using cookie-based JWT authentication must enforce standard Django CSRF checks for `POST`, `PATCH`, and `DELETE`, and template pages that call same-origin APIs must issue the normal CSRF cookie.

### 2026-08-09 â€” Collection pagination

**Decision:** Versioned project and site collection endpoints use the assessment's `limit` and `offset` query parameters with DRF limit-offset pagination, a default limit of 20, and a maximum limit of 100.

**Impact:** `GET /api/v1/projects` and `GET /api/v1/projects/{project_id}/sites` return paginated responses with consistent bounded collection sizes.

### 2026-08-09 - Survey collection pagination

**Decision:** The Survey API collection endpoint uses DRF limit-offset pagination with `limit` and `offset`, a default limit of 20, and a maximum limit of 100.

**Impact:** `GET /api/v1/surveys` returns bounded paginated results consistent with the approved versioned API convention.

### 2026-08-09 - Survey status casing

**Decision:** Where a high-level API document shows lowercase survey statuses but the approved database schema defines uppercase enums, the Survey API uses the database schema's exact uppercase `SurveyStatus` values for survey representations and status filters.

**Impact:** Survey responses and the `status` filter return and accept uppercase enum values such as `DRAFT`, `READY`, and `PENDING_APPROVAL` without lowercase translation.

### 2026-08-10 - Survey archival endpoint and retention

**Decision:** Survey archival uses `POST /api/v1/surveys/{survey_id}/archive` only. It transitions surveys only from `APPROVED` or `REJECTED` to `ARCHIVED` and never deletes survey data.

**Impact:** Do not implement `DELETE /surveys/{id}` for the Survey API. Archival remains the only documented terminal state transition exposed by this workflow.

### 2026-08-10 - Administrator cross-project approval workflow authority

**Decision:** Administrators may run survey approval, rejection, and archive actions across all projects, but they must obey the same readiness and state-transition guards as the owning Project Manager.

**Impact:** Administrator workflow actions do not bypass processing completeness, file readiness, pending-state requirements, rejection-reason validation, or archive-state guards.

### 2026-08-10 - Deferred download restriction

**Decision:** The private original-file download endpoint is now implemented at `GET /api/v1/surveys/{survey_id}/files/{file_id}/download`. Downloads remain restricted to approved surveys for every role.

**Impact:** The endpoint resolves the file through the supplied survey, enforces normal project visibility first, denies every non-`APPROVED` survey state including `ARCHIVED`, returns a short-lived private signed redirect for `SurveyFile.storage_path`, and must not expose converted delivery, preview delivery, public object URLs, or private storage paths in API payloads.

### 2026-08-10 - Direct multipart upload first, resumable session deferred

**Decision:** Implement the direct streaming multipart upload endpoint first, following the detailed file-upload processing sequence. The documented resumable `UploadSession` API remains deferred and must not be partially substituted in this task.

**Impact:** `POST /api/v1/surveys/{survey_id}/files` admits uploads directly to private R2 and dispatches processing after commit, while `UploadSession` create/chunk/complete endpoints remain unimplemented.

### 2026-08-10 - OBJ multipart field contract

**Decision:** The direct multipart upload contract uses one required `file` field plus repeated optional `assets` file fields. `assets` are permitted only when the primary upload is OBJ and are limited to its related MTL and PNG/JPEG texture files.

**Impact:** The API rejects unexpected multipart fields, rejects `assets` for non-OBJ primary uploads, and stores accepted OBJ related files as dependent `SurveyFileAsset` records under the primary `SurveyFile`.

### 2026-08-10 - Private map and 3D delivery contract

**Decision:** Processed 2D map layers and 3D viewer outputs become viewable to any user with normal project visibility as soon as the relevant `SurveyFile` reaches `ready`, even before survey approval. This does not change the existing original-download rule: `GET /api/v1/surveys/{survey_id}/files/{file_id}/download` remains restricted to surveys in the exact `APPROVED` state.

**Impact:** The authenticated `GET /api/v1/surveys/{survey_id}/map-layers`, `GET /api/v1/map-layers/{file_id}/tiles/{z}/{x}/{y}`, and `GET /api/v1/surveys/{survey_id}/models` routes use the existing Hitech JWT and server-side project scope checks. Django does not proxy tile or model bytes. The tile route authorises the request, validates the deterministic private tile sidecar, and returns a short-lived signed `302` redirect for the exact private R2 tile object. Catalog responses may include short-lived signed source URLs for browser-ready private objects but must not expose storage paths, checksums, worker errors, or raw internal metadata.

**Storage metadata decision:** Generated map and model delivery metadata is stored as deterministic private JSON sidecars in R2 instead of new database fields or reuse of `SurveyFileAsset`. Raster tiles use `surveys/{survey_id}/files/{file_id}/tiles/{z}/{x}/{y}.png` with private metadata at `surveys/{survey_id}/files/{file_id}/tiles/metadata.json`. 3D viewer metadata uses `surveys/{survey_id}/files/{file_id}/model-metadata.json`.

### 2026-08-10 - Measurement API payload and units

**Decision:** `POST /api/v1/surveys/{survey_id}/measurements` accepts only `type`, `name`, and WGS84 `coordinates` as `[longitude, latitude]` pairs. `DISTANCE` stores and returns metres as `m`; `AREA` stores and returns square metres as `m²`. Clients must not supply calculated values, units, ownership, survey identifiers, or timestamps.

**Impact:** The Maps API validates coordinate shape, numeric finiteness, and longitude/latitude bounds server-side, rejects unknown write fields, auto-closes area polygons when calculating, and persists the calculated value as the only source of truth.

### 2026-08-10 - Audit log read API scope and serialization

**Decision:** Audit-log read access is implemented only through `GET /api/v1/audit-logs` and `GET /api/v1/audit-logs/{audit_log_id}` with `project_id`, `survey_id`, `action`, `from_date`, and `to_date` filters plus limit-offset pagination. Administrators read all logs; Project Managers read logs for projects they manage; Survey Engineers and Viewers read logs only for projects where they are explicit members.

**Impact:** Audit responses are ordered newest first with a deterministic `-timestamp, -id` tie-break, and the serializer must hide sensitive storage paths, object keys, checksums, presigned URLs, JWTs, and similar secrets without mutating the immutable stored audit record.

### 2026-08-10 - Development-only demo JWT and assessment seed tooling

**Decision:** Assessment demo access uses out-of-band management commands only. When `DEBUG=True` and `ENABLE_DEMO_AUTH=True`, the local environment may load a gitignored demo RSA public key file for JWT validation if `HITECH_AUTH_JWT_PUBLIC_KEY` is otherwise unset. Private key material remains local-only and is never exposed through HTTP routes, stored in the database, or committed to source control.

**Impact:** Demo access stays operational scaffolding rather than application authentication. The Django service still validates RS256 JWTs through the normal `HitechJWTAuthentication` path, while `init_demo_auth_keys`, `seed_demo_assessment`, and `issue_demo_token` remain unavailable unless both development guards are enabled.

### 2026-08-10 - Readiness probe route

**Decision:** Keep the documented liveness/readiness split, but expose the readiness route at `GET /ready` instead of `GET /health/ready`.

**Reason:** The source architecture document describes the readiness behavior but the final assessment-completion scope explicitly approves `GET /ready` and requires that path to be recorded here. This keeps the split intact without introducing a second readiness URL.

**Impact:** `GET /health` remains the lightweight process liveness probe. `GET /ready` is the only implemented readiness endpoint and returns `200` only when PostgreSQL/PostGIS, Redis, private R2 connectivity, and a reachable Celery worker are all ready.
