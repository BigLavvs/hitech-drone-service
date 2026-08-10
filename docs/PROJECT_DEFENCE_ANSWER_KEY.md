# Project Defence — Model Answers

Use these as concise model answers. Keep claims accurate to the current implementation.

## Product and scope

1. It gives authorised construction staff one workflow for projects/sites, drone surveys, private 2D/3D upload, asynchronous processing, viewing, measurement, approval, and audit history.
2. The assessment places it within a wider Hitech ecosystem. It owns drone-mapping workflows, not identity issuance or the whole construction platform.
3. PM creates project/site; SE creates survey/uploads; Celery processes; users inspect output; SE submits; PM/Admin approves or rejects; audit shows the trail.
4. I avoided a separate frontend framework, marketing UI, speculative optimisation, and unrelated product features to finish the assessed workflow correctly.
5. Django templates plus vanilla JavaScript are the approved architecture and keep the frontend lightweight while DRF owns data operations.
6. Projects is the primary authenticated route. The root route is only a foundation/template preview.
7. Live API-backed pages, role scope, seeded demo data, API docs, health probes, Docker Compose, and targeted tests make it demonstrable.

## Architecture

8. Django/Gunicorn serves pages/API; Celery worker processes files; beat schedules tasks; Redis is broker/result backend; Neon/PostGIS stores relational/geospatial data; R2 stores private payloads.
9. One deployable Django service is organised into explicit apps with service-layer rules: access control, projects, surveys, files, processing, approvals, audit, maps, models3d.
10. The approved deployment uses managed Neon with PostGIS, avoiding VPS database operation while providing correct geospatial support.
11. The design specifies private self-hosted Redis on the VPS for Celery coordination.
12. Celery runs CPU/I/O work such as metadata extraction, previews, tiles, conversion, and retries outside HTTP requests.
13. Web traffic and processing have different load patterns, so workers scale based on queue depth independently.
14. R2 keeps large originals/derived objects out of Postgres and ephemeral application disks while allowing private signed delivery.
15. CSS/JS are collected static assets served through WhiteNoise. Survey/map/model data stays in private R2.
16. Versioning prevents future API changes silently breaking clients.
17. Health proves the process is alive; readiness proves it can do the full service job.
18. Readiness checks database, Redis, R2, and a worker because those are required for the assessed end-to-end workflow.

## Authentication and authorisation

19. Hitech Auth owns credentials and token issuance. This service validates external identity and must not duplicate password storage.
20. The browser sends an externally issued hitech_access_token cookie. The service validates RS256 claims and resolves an active local user.
21. HttpOnly prevents JavaScript from reading the token, lowering XSS token-theft exposure.
22. Cookies are automatically attached to browser requests, so unsafe operations require CSRF validation.
23. Required claims are sub, email, role, and exp. Sub maps to the existing local external identity; values are validated and expiry prevents stale access.
24. Authentication fails with 401; users are never auto-created from arbitrary tokens.
25. Admin sees all. PM sees/manages owned projects. SE and Viewer require membership for project-scope read access. Only Admin/owning PM manage projects/sites, with further workflow restrictions.
26. JavaScript can be bypassed; backend services enforce permissions for every protected operation.
27. It is a clearly labelled, short-lived, assessment-only alternative when real Hitech Auth is unavailable. It selects only seeded roles and sets an HttpOnly cookie server-side.
28. Set ENABLE_DEMO_AUTH false, remove the demo private key from Coolify, and redeploy.

## Data and workflow

29. PointField with EPSG:4326 matches the schema and stores interoperable WGS84 coordinates in PostGIS.
30. Ownership drives management and approval permission; assignment at creation prevents unowned projects.
31. Archiving preserves workflow/audit evidence and prevents accidental loss.
32. Draft, uploading, processing, ready, pending approval, approved, rejected, archived. Server services, not clients, enforce transitions.
33. Active assigned SE submits; Admin or owning PM approves/rejects; only approved/rejected surveys archive.
34. It provides separation of duties and credible review.
35. Approval is the one current record; immutable ApprovalHistory records submitted/approved/rejected transitions.
36. Audit immutability preserves an evidential record of significant actions.

## Files and processing

37. Assigned SE uploads multipart data; service validates scope/type/MIME/magic/size/name, stages R2 data, records file/job transactionally, then dispatches Celery after commit.
38. Staging ensures incomplete or invalid objects never become normal assets; canonical keys are private and deterministic after admission.
39. A duplicate primary checksum in the same survey returns the existing file/job and creates no second processing job.
40. Direct multipart satisfies the assessment workflow. Resumable UploadSession APIs are deferred and documented rather than invented.
41. OBJ may have validated companion material/texture assets stored as SurveyFileAsset records under the same private scope.
42. on_commit prevents a worker observing a job whose transaction rolled back.
43. One job moves queued, running, completed, or failed. Automatic retries use 5/15/45-minute delays up to three times; repeated delivery does not repeat completed work.
44. PostgreSQL ProcessingJob is durable user-facing truth; Redis is infrastructure, not the permanent job record.
45. 2D creates browser-ready raster/tile/vector output where appropriate; 3D provides viewable GLB/GLTF or Potree-style output by source/tooling.
46. Ready derived output supports review; originals are more sensitive and only downloadable from approved surveys.

## Viewer, API, deployment, and trade-offs

47. Django authorises each tile, validates its metadata, and redirects to a short-lived private R2 signed URL.
48. The model catalog authorises scope and returns a short-lived signed source URL for ready output.
49. The agreed workflow allows project members to inspect ready derived data before formal acceptance; approval gates acceptance and original download.
50. API accepts type, name, and longitude/latitude arrays; it validates them, calculates values server-side, audits them, allows assigned users to create/read, and limits delete to Admin/owning PM.
51. API schema, Swagger, and Redoc make the versioned contract inspectable and satisfy the API documentation requirement.
52. Build images; Redis becomes healthy; one migration service uses direct Neon; then web Gunicorn, worker, and beat start. Only web gets a public domain.
53. Redis is an internal broker/result service, not a public API. Exposure risks queue/data access.
54. Runtime uses Neon pooled connections; migrations use the direct connection configured in settings_migrations.
55. Migration runs schema changes once before long-running services. It exits successfully, so exclude_from_hc prevents Coolify treating completion as unhealthy.
56. Tests prioritise auth, ownership, uploads, processing, approval, audit, measurements, and API contracts. CSS-only changes get cheap syntax/render checks.
57. Deferred work includes full resumable upload, real Auth-Service integration, deployed Potree converter where needed, richer observability, and CI/CD.
58. Docker was not locally executed because local virtualisation was unavailable; Coolify deployment must be verified. Real Auth is external validation, not an issuer. The public demo selector is temporary assessment scaffolding and must be removed/disabled.

