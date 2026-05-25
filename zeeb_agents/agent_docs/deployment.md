# Deployment Guide

How to prepare and deploy a Zeeb BaaS project to production.

> **Tool name prefix**: Tool calls below use `{prefix}` as a placeholder.
> It is replaced with the prefix your MCP server registered these tools under
> (e.g. `zeeb_`, `myapp_`, or empty string).

## Step 1 — Production Readiness Check

Run this first to identify all issues before deploying:

```
{prefix}check_production_readiness()
# result.success == True  → ready to deploy
# result.data["issues"] — list of problems to fix
# result.data["passed"] — list of passing checks
```

Checks performed:
| Check | Requirement |
|---|---|
| `DEBUG` | Must be `False` |
| `SECRET_KEY` | Must be set and not a placeholder |
| Database | Must not be SQLite |
| `requirements.txt` | Must exist |
| `Dockerfile` | Must exist |

## Step 2 — Configure Production Settings

```
# In settings.py
{prefix}manage_settings(key="DEBUG", value=False)

# In .env (keep secrets out of settings.py)
{prefix}set_env(key="SECRET_KEY",   value="your-strong-random-secret-key-here")
{prefix}set_env(key="DATABASE_URL", value="postgresql+asyncpg://user:pass@host/dbname")
{prefix}set_env(key="ALLOWED_HOSTS",value="myapi.example.com")
```

Example production `settings.py`:
```python
import os

DEBUG = False
SECRET_KEY = os.environ["SECRET_KEY"]
ALLOWED_HOSTS = os.environ.get("ALLOWED_HOSTS", "").split(",")

DATABASE = {
    "url": os.environ["DATABASE_URL"],
    "pool_size": 20,
    "max_overflow": 10,
}

INSTALLED_APPS = [
    "zeeb_api.auth",
    "apps.blog",
    "apps.shop",
]

MIDDLEWARE = [
    "zeeb_api.middleware.CORSMiddleware",
]

CORS_ALLOW_ORIGINS = os.environ.get("CORS_ORIGINS", "").split(",")
CORS_ALLOW_CREDENTIALS = True
```

## Step 3 — Generate Dockerfile

```
{prefix}generate_dockerfile(python_version="3.12", port=8000)
# Writes Dockerfile (multi-stage) and .dockerignore
```

Generated `Dockerfile` structure:
```dockerfile
FROM python:3.12-slim AS builder
# install dependencies into builder layer

FROM python:3.12-slim AS runner
# copy only site-packages from builder (lean image)
EXPOSE 8000
CMD ["python", "manage.py", "runserver", "0.0.0.0:8000"]
```

For a production-grade CMD, replace with **Gunicorn + Uvicorn**:
```dockerfile
CMD ["gunicorn", "config.asgi:application", "-k", "uvicorn.workers.UvicornWorker",
     "-b", "0.0.0.0:8000", "--workers", "4"]
```

## Step 4 — Generate requirements.txt

```
{prefix}generate_requirements()
# Runs pip freeze, filters editable installs, writes requirements.txt
```

## Step 5 — Add Health Endpoints

```
{prefix}create_health_endpoint()
# GET /health  → liveness probe  (always 200)
# GET /ready   → readiness probe (200 / 503 based on DB)
```

Configure in Kubernetes / Docker Compose:
```yaml
# docker-compose.yml
healthcheck:
  test: ["CMD", "curl", "-f", "http://localhost:8000/health"]
  interval: 30s
  timeout: 10s
  retries: 3
```

```yaml
# k8s deployment.yaml
livenessProbe:
  httpGet:
    path: /health
    port: 8000
  initialDelaySeconds: 10
readinessProbe:
  httpGet:
    path: /ready
    port: 8000
  initialDelaySeconds: 5
```

## Step 6 — Apply Migrations in CI/CD

In your deployment pipeline (before starting the container):
```bash
python manage.py migrate --run-syncdb
```

Or via tool:
```
{prefix}run_migrations()
```

## Step 7 — Docker Build & Push

```bash
docker build -t myapi:latest .
docker push registry.example.com/myapi:latest
```

## Step 8 — Runtime Health Check

After deployment, verify with:
```
{prefix}check_system_health()
# result.data["checks"]["db"]       == "ok"
# result.data["checks"]["settings"] == "ok"
# result.data["checks"]["overall"]  == "healthy"
```

## Recommended Stack

| Layer | Technology |
|---|---|
| ASGI server | Uvicorn / Gunicorn+Uvicorn |
| Database | PostgreSQL 15+ |
| Container | Docker |
| Orchestration | Kubernetes or Fly.io |
| Reverse proxy | Nginx or Caddy |
| Secrets | HashiCorp Vault / AWS Secrets Manager / .env |
| Monitoring | Prometheus + Grafana |
| Logging | stdout → Loki / CloudWatch |

## Environment Variables Reference

| Variable | Required | Description |
|---|---|---|
| `SECRET_KEY` | ✅ | Django-style secret key |
| `DATABASE_URL` | ✅ | Async DB URL (e.g. `postgresql+asyncpg://...`) |
| `DEBUG` | — | Default `False` in prod |
| `ALLOWED_HOSTS` | — | Comma-separated hostnames |
| `CORS_ORIGINS` | — | Comma-separated allowed origins |
| `JWT_ACCESS_TOKEN_LIFETIME_MINUTES` | — | Default 60 |
| `JWT_REFRESH_TOKEN_LIFETIME_DAYS` | — | Default 7 |
