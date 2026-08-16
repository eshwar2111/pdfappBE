# Deployment

Repositories:

- Backend — <https://github.com/eshwar2111/pdfappBE>
- Frontend — <https://github.com/eshwar2111/pdfappFE>

Target architecture:

```
            Browser
               │
               ▼
   Azure Static Web Apps  ────────► Azure App Service (Linux, Python 3.11)
        React SPA              HTTPS         FastAPI
                                                │
                        ┌───────────────────────┼────────────────────┐
                        ▼                       ▼                    ▼
                  Neon Postgres          Azure Blob Storage        Gemini
                  (+ pgvector)            (private + SAS)          Resend
```

The database is already deployed — Neon is reachable from anywhere, so nothing
needs to change for it.

---

## 1. Blob storage (do this first)

**App Service has an ephemeral filesystem.** Anything written to disk is lost on
restart, redeploy, or scale event. `STORAGE_BACKEND=local` will appear to work
and then start returning 404s for PDFs uploaded before the last restart, so
production must use Azure Blob Storage.

Portal → **Create a resource → Storage account**

| Setting | Value |
|---|---|
| Name | `pdfintelligencestore` (must be globally unique, lowercase) |
| Region | Central India (or nearest) |
| Performance | Standard |
| Redundancy | **LRS** — cheapest, sufficient here |

After it is created: **Security + networking → Access keys → Show keys**, and
copy **Connection string**.

Do *not* create the container by hand or set public access on it. The app
creates it at startup with default (private) access, and serves every read
through a short-lived SAS.

---

## 2. Backend — Azure App Service

Portal → **Create a resource → Web App**

| Setting | Value |
|---|---|
| Name | `pdf-intelligence-api` (becomes `pdf-intelligence-api.azurewebsites.net`) |
| Publish | Code |
| Runtime stack | **Python 3.11** |
| Operating System | **Linux** |
| Region | Central India |
| Pricing plan | **B1 Basic** |

> **Not F1 Free.** F1 has no Always On, caps CPU at 60 minutes/day, and cold
> starts take 30+ seconds — a reviewer opening the URL would see a timeout.
> B1 is covered comfortably by the student credit.

### Startup command

**Configuration → General settings**

- **Startup Command**: `bash startup.sh`
- **Always On**: **On**
- **HTTP version**: 2.0

`startup.sh` runs `alembic upgrade head` before starting gunicorn, so migrations
apply automatically on deploy and a schema failure aborts the start rather than
letting the app serve against a schema it does not understand.

### Application settings

**Configuration → Application settings → New application setting**, once per row:

| Name | Value |
|---|---|
| `ENVIRONMENT` | `production` |
| `LOG_LEVEL` | `INFO` |
| `DATABASE_URL` | your Neon URL — `postgresql+asyncpg://…?ssl=require` |
| `JWT_SECRET` | a **new** 64-byte secret, not the local one |
| `CORS_ORIGINS` | the Static Web Apps URL from step 3 |
| `STORAGE_BACKEND` | `azure` |
| `AZURE_STORAGE_CONNECTION_STRING` | from step 1 |
| `AZURE_STORAGE_CONTAINER` | `documents` |
| `GEMINI_API_KEY` | your key |
| `GEMINI_CHAT_MODEL` | `gemini-3.7-flash` |
| `GEMINI_EMBEDDING_MODEL` | `gemini-embedding-001` |
| `GEMINI_EMBEDDING_DIMENSIONS` | `768` |
| `EMAIL_BACKEND` | `resend` |
| `RESEND_API_KEY` | your key |
| `EMAIL_FROM` | `PDF Intelligence <onboarding@resend.dev>` |
| `SCM_DO_BUILD_DURING_DEPLOYMENT` | `false` |
| `WEBSITES_PORT` | `8000` |

Generate the production secret with:

```bash
python -c "import secrets; print(secrets.token_urlsafe(64))"
```

Use a different `JWT_SECRET` from local. It signs user tokens, guest tokens and
file-download tokens — a leaked development secret should not mint valid
production credentials.

`CORS_ORIGINS` is chicken-and-egg with step 3: create the Static Web App first
to learn its URL, or set this afterwards and restart.

### Connect the deployment

**Deployment Center → GitHub → `eshwar2111/pdfappBE` → `main`.**

Azure commits a workflow file of its own. Delete it and keep
`.github/workflows/deploy.yml` from this repo — it installs dependencies in CI
so the deployed artifact is exactly what was tested, and it polls
`/health/ready` afterwards so a broken `DATABASE_URL` fails the deploy instead
of surfacing to a user.

For that workflow you need one secret: **Deployment Center → Manage publish
profile → Download**, then in GitHub → Settings → Secrets and variables →
Actions → New repository secret:

- Name: `AZURE_WEBAPP_PUBLISH_PROFILE`
- Value: the entire contents of the downloaded `.PublishSettings` file

---

## 3. Frontend — Azure Static Web Apps

Portal → **Create a resource → Static Web App**

| Setting | Value |
|---|---|
| Name | `pdf-intelligence` |
| Plan type | **Free** |
| Source | GitHub → `eshwar2111/pdfappFE` → `main` |
| Build presets | **Custom** |
| App location | `/` |
| Api location | *(leave empty)* |
| Output location | `dist` |

Azure adds its own workflow file to the repo. **Delete it** and keep
`.github/workflows/deploy.yml` — Azure's version rebuilds with Oryx and would
drop `VITE_API_BASE_URL`, shipping a bundle that calls its own origin for the
API. Ours builds with the variable set and passes `skip_app_build: true`.

Keep the deployment token Azure created (it appears in the repo's secrets as
`AZURE_STATIC_WEB_APPS_API_TOKEN_*`). Copy its value into a secret named
exactly:

- `AZURE_STATIC_WEB_APPS_API_TOKEN`

Then add a repository **variable** (Settings → Secrets and variables → Actions
→ **Variables** tab):

- Name: `VITE_API_BASE_URL`
- Value: `https://pdf-intelligence-api.azurewebsites.net`

A variable rather than a secret because it is a public URL that appears in the
shipped bundle — marking it secret would imply a confidentiality it does not
have.

`staticwebapp.config.json` in the repo root rewrites unknown paths to
`index.html`. Without it, refreshing on `/s/<token>` returns 404 — which is
precisely the guest flow a reviewer will exercise.

---

## 4. Close the loop

Once the Static Web App URL exists (e.g. `https://ashy-sand-01234.5.azurestaticapps.net`):

1. Set the backend's `CORS_ORIGINS` to that exact origin — **no trailing
   slash**, and `https://`, not `http://`.
2. **Restart** the App Service. Settings are read once at startup.

`CORS_ORIGINS` does double duty: it is also the base URL for generated share
links. Get it wrong and shares will point at `localhost:5173`.

---

## 5. Verify

```bash
# Liveness, then a real database round trip
curl https://pdf-intelligence-api.azurewebsites.net/health
curl https://pdf-intelligence-api.azurewebsites.net/health/ready
```

Then, in the browser, walk the full path — this is also your video script:

1. Sign up
2. Upload a PDF, watch it go `Processing → Ready` with a summary
3. Search for a rare term from inside the document (hybrid retrieval)
4. Open it, ask a question, click a page citation
5. Share it, open the link in a **private window** — no account required
6. Comment as the guest, see it appear as the owner
7. Delete the document, confirm the share link stops working

Live backend logs while you do it:

```bash
az webapp log tail --name pdf-intelligence-api --resource-group <your-rg>
```

---

## Troubleshooting

**CORS errors in the browser console.** `CORS_ORIGINS` does not exactly match
the frontend origin. Compare scheme, host and trailing slash character by
character, then restart the App Service.

**PDF viewer shows "could not be displayed" in production.** The blob SAS URL
is on a different origin, so the storage account needs CORS of its own:
Storage account → **Resource sharing (CORS)** → Blob service →
allowed origins = your frontend URL, allowed methods = `GET, HEAD`, allowed
headers = `*`.

**Uploads succeed, then PDFs 404 later.** `STORAGE_BACKEND` is still `local`.
App Service disks do not survive a restart.

**Everything 500s right after deploy.** Check `/health/ready`. Almost always
`DATABASE_URL` — confirm `postgresql+asyncpg://` and `?ssl=require`, with no
`channel_binding` parameter.

**First request after idle is slow.** Neon's compute auto-suspends. Disable
auto-suspend on the Neon branch before recording, or open the app a minute
early.

**Deploy succeeds but the site 503s.** Read the startup log — usually a failed
migration. `alembic upgrade head` runs before gunicorn binds, so the container
never starts if the schema cannot be brought up to date. That is deliberate.
