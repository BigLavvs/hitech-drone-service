# Coolify Deployment Guide - Assessment Environment

This guide deploys the assessment on a VPS with Coolify: Django/Gunicorn, Celery worker, Celery beat, and internal Redis. Neon/PostGIS and private Cloudflare R2 remain external.

This is a temporary public assessment environment. After assessment review, set `ENABLE_DEMO_AUTH=False`, remove the demo private key from Coolify, and redeploy.

## 0. Complete the pending changes

1. Correct the updated sequence diagrams before replacing the originals.
2. Implement and verify the assessment-only demo role selector on `/login`.
3. It must preserve the external Hitech Auth explanation and CTA. Demo access is a separate, explicit assessment exception.
4. Demo role selection must set a short-lived `hitech_access_token` HttpOnly cookie server-side. With `DEBUG=False`, that cookie must be `Secure`. Never display a token or expose the private key to JavaScript, browser storage, HTML, URLs, or logs.
5. The deployed demo selector must read its RSA private signing key from the Coolify runtime secret `DEMO_AUTH_PRIVATE_KEY`. Do not rely on `.demo-auth/`, because it is excluded from Git and Docker builds. Keep `HITECH_AUTH_JWT_PUBLIC_KEY` configured with the matching public validation key.
6. Seed data must create only the four documented demo users: Administrator, Project Manager, Survey Engineer, and Viewer.

## 1. Correct the Coolify Compose health-check setup

The one-off `migrate` service exits successfully by design. In `docker-compose.yml`, add this field under that service:

```yaml
exclude_from_hc: true
```

This is a required repository change before the next commit and first Coolify deployment. The service begins:

```yaml
migrate:
  exclude_from_hc: true
  build:
    context: .
  command: python manage.py migrate --settings=config.settings_migrations
```

Keep the existing order:

1. Redis becomes healthy.
2. `migrate` runs `python manage.py migrate --settings=config.settings_migrations`.
3. `web`, `worker`, and `beat` wait for migration completion.

Do not publish Redis port `6379`. It must stay internal to the Compose/Coolify network.

## 2. Resolve the local migration/startup messages

Four audit/files migrations were created after the earlier migration run. Apply them once using the direct Neon connection:

```powershell
.\.venv\Scripts\python.exe manage.py migrate --settings=config.settings_migrations
```

WhiteNoise is now installed. Stop any old Django process with `Ctrl + Break`, then restart it:

```powershell
.\.venv\Scripts\python.exe manage.py runserver
```

Do not use `migrate --fake`.

## 3. Run the final local checks

```powershell
.\.venv\Scripts\python.exe manage.py check
.\.venv\Scripts\python.exe manage.py spectacular --file docs/openapi.yaml
```

Run the focused tests reported for the demo-login implementation. Do not run unrelated broad tests just for deployment.

Open and check:

- `/login`
- `/projects` after demo authentication
- `/docs`
- `/health`
- `/ready` â€” requires Neon, Redis, R2, and a Celery worker

## 4. Review what will be committed

Confirm `.gitignore` contains:

```text
.env
.venv/
.demo-auth/
staticfiles/
__pycache__/
```

Then review:

```powershell
git status
git diff --check
```

Never stage `.env`, private keys, demo key files, or credentials.

## 5. Initialise Git and make the first complete commit

If Git is not already initialised:

```powershell
git init
git add .
git status
git commit -m "Complete Hitech Drone Mapping assessment"
git branch -M main
```

If Git already exists:

```powershell
git add .
git status
git commit -m "Prepare assessment deployment"
```

Use `git status` before every commit. Stop if any secrets are staged.

## 6. Create and push the private GitHub repository

1. Create a new **private**, empty GitHub repository.
2. Do not add a GitHub README, licence, or `.gitignore`.
3. Copy its HTTPS URL.
4. Push:

```powershell
git remote add origin https://github.com/YOUR-USERNAME/YOUR-REPOSITORY.git
git push -u origin main
```

5. In GitHub, verify no `.env`, private key, or `.demo-auth` content exists. Confirm `docker-compose.yml`, `Dockerfile`, `.dockerignore`, `requirements.txt`, and `docs/openapi.yaml` are present.

## 7. Prepare Neon, R2, and DNS

### Neon/PostGIS

1. Use a dedicated assessment database, separate from development/test.
2. Enable PostGIS.
3. Copy its pooled connection string for `DATABASE_URL`.
4. Copy its direct connection string for `DIRECT_URL`.
5. Do not place test URLs in Coolify.

### Cloudflare R2

1. Create a dedicated **private** assessment bucket.
2. Create credentials restricted to that bucket.
3. Record its S3 endpoint, access key, secret, and bucket name.
4. Configure CORS to allow only the deployed application origin to fetch signed viewer assets.
5. Keep `R2_PUBLIC_URL` blank. This app uses private signed URLs, not a public bucket/CDN.

### DNS

1. Choose a hostname such as `drone-assessment.example.com`.
2. Add the DNS record required for the Coolify VPS, normally an A record to its public IP.
3. Wait until DNS resolves before attaching the domain in Coolify.

## 8. Create the Coolify application

1. In Coolify, choose the target project and VPS server.
2. Create an Application from the private GitHub repository and select branch `main`.
3. Choose **Docker Compose** deployment and use the repository-root `docker-compose.yml`.
4. Keep all services in the same Compose deployment: `web`, `worker`, `beat`, `redis`, and one-off `migrate`.
5. Assign the public domain only to `web`, port `8000`, for example:

```text
https://drone-assessment.example.com:8000
```

6. Do not assign a public domain or host port to Redis, worker, beat, or migrate.

Coolifyâ€™s proxy supplies HTTPS routing for the web service.

## 9. Configure Coolify runtime-only environment variables

Add these in Coolifyâ€™s environment-variable UI as runtime variables, never Git files or build variables.

| Name | Value |
| --- | --- |
| `DJANGO_SECRET_KEY` | New strong random secret |
| `DEBUG` | `False` |
| `ALLOWED_HOSTS` | Deployed hostname only, no `https://` |
| `DATABASE_URL` | Neon pooled runtime URL |
| `DIRECT_URL` | Neon direct URL |
| `HITECH_AUTH_JWT_PUBLIC_KEY` | Matching assessment-demo public validation key or real external Auth public key |
| `HITECH_AUTH_ACCESS_COOKIE_NAME` | `hitech_access_token` |
| `ENABLE_DEMO_AUTH` | `True` during assessment review only |
| `DEMO_AUTH_PRIVATE_KEY` | Assessment-only private RSA signing key as a single runtime secret value; escaped `\\n` PEM line breaks are supported |
| `DEMO_AUTH_TOKEN_TTL_SECONDS` | `900` |
| `R2_ENDPOINT_URL` | R2 S3 endpoint |
| `R2_ACCESS_KEY_ID` | Restricted R2 access key |
| `R2_SECRET_ACCESS_KEY` | Restricted R2 secret |
| `R2_BUCKET_NAME` | Private assessment bucket |
| `R2_PUBLIC_URL` | Blank |
| `REDIS_URL` | `redis://redis:6379/0`, if used by completed code |
| `CELERY_BROKER_URL` | `redis://redis:6379/0` |
| `CELERY_RESULT_BACKEND` | `redis://redis:6379/1` |
| `MAX_FILE_SIZE_BYTES` | `10737418240` |
| `MAX_SURVEY_TOTAL_SIZE_BYTES` | `53687091200` |
| `UPLOAD_CHUNK_SIZE_BYTES` | `8388608` |
| `RATE_LIMIT_UPLOAD` | `10/m` |
| `RATE_LIMIT_RETRY` | `5/m` |
| `POTREE_CONVERTER_PATH` | Blank unless installed in the worker image |

Do not add `DATABASE_URL_TEST`, `DIRECT_URL_TEST`, any local `127.0.0.1` Redis URL, or `DEMO_AUTH_PRIVATE_KEY_PATH`.

## 10. Deploy

1. Save the Coolify variables and deploy from `main`.
2. Read logs in order:
   - image build;
   - Redis healthy;
   - one migration run succeeds;
   - web collects static files and starts Gunicorn;
   - worker connects to Redis;
   - beat starts.
3. If migration fails, correct the direct Neon URL/PostGIS setup. Never bypass it with `--fake`.
4. If web is unhealthy, read the concise response from `/ready` and Coolify logs. Do not expose Redis to troubleshoot.

## 11. Seed assessment data

After deployment is healthy, open a terminal for the running **web** container in Coolify and run once:

```bash
python manage.py seed_demo_assessment
```

Do not generate demo keys inside the container. Use `DEMO_AUTH_PRIVATE_KEY` in Coolify runtime secrets and the matching `HITECH_AUTH_JWT_PUBLIC_KEY` value for validation.

## 12. Verify the deployed assessment

Open:

- `https://YOUR-DOMAIN/health` â€” `200`, `{"status":"ok"}`
- `https://YOUR-DOMAIN/ready` â€” `200` with all required components `ok`
- `https://YOUR-DOMAIN/docs`
- `https://YOUR-DOMAIN/docs/redoc`
- `https://YOUR-DOMAIN/login`

Test every role with the assessment selector:

1. Administrator: user management and cross-project access.
2. Project Manager: owned project/site management and approval.
3. Survey Engineer: survey/file workflow and approval submission.
4. Viewer: read access and permitted measurement creation, but no management/approval.

Confirm:

- cookie is HttpOnly and Secure;
- no JWT appears in UI, source, browser storage, URL, or logs;
- original downloads remain restricted to approved surveys;
- Redis/R2 are not publicly exposed;
- the public login page labels the role selector as temporary assessment-only access.

## 13. Final handoff and shutdown plan

Commit final docs/fixes and push:

```powershell
git add .
git status
git commit -m "Document assessment deployment"
git push
```

Give reviewers the assessment URL and explain that role selection is temporary demo access only.

After assessment review:

1. Set `ENABLE_DEMO_AUTH=False`.
2. Remove `DEMO_AUTH_PRIVATE_KEY` from Coolify.
3. Redeploy.
