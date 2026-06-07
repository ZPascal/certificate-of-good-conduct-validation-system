# Helm, CI/CD & Integration Tests — Design

**Date:** 2026-06-06
**Project:** certificate-of-good-conduct-validation-system

---

## 1. Helm Chart

### Structure

```
helm/fuehrungszeugnis/
  Chart.yaml
  values.yaml
  charts/                  ← Bitnami postgresql pulled via `helm dependency update`
  templates/
    _helpers.tpl
    configmap.yaml
    secret.yaml
    deployment.yaml
    serviceaccount.yaml
```

### Config delivery

Non-secret settings (URLs, tag names, group IDs, general settings) go into a `ConfigMap`.
Secrets (passwords, API tokens) go into a Kubernetes `Secret`.

Both are exposed to the worker pod as environment variables. The `pydantic-settings` env-override layer (already in `src/config.py`) picks them up automatically, so no file-merge step is needed.

| ConfigMap keys | Secret keys |
|---|---|
| `PAPERLESS_BASE_URL`, `PAPERLESS_TAG_NAME`, `PAPERLESS_POLL_INTERVAL_SECONDS` | `PAPERLESS_TOKEN` |
| `HITOBITO_BASE_URL`, `HITOBITO_GROUP_ID`, `HITOBITO_EFZ_QUALIFICATION_KIND_LABEL` | `HITOBITO_TOKEN` |
| `EMAIL_SMTP_HOST`, `EMAIL_SMTP_PORT`, `EMAIL_SMTP_USE_TLS`, `EMAIL_SENDER`, `EMAIL_ALERT_RECIPIENTS` | `EMAIL_SMTP_PASSWORD` |
| `LOG_LEVEL`, `CERTIFICATE_MAX_AGE_YEARS`, `EXPIRY_ALERT_DAYS` | `DB_PASSWORD` |
| `DB_HOST`, `DB_PORT`, `DB_NAME`, `DB_USER` | |

### Worker Deployment

- **Replicas:** 1 (stateful scheduler; no horizontal scaling without coordination).
- **Init-container:** runs `python -m alembic upgrade head` before the worker starts. Uses the same image.
- **Liveness probe:** none — no HTTP port. Pod restart policy (`restartPolicy: Always`) handles crashes.
- **Resources:** configurable via `values.yaml`; sensible defaults provided (`requests.cpu: 100m`, `requests.memory: 128Mi`).
- **Service account:** dedicated `ServiceAccount` with no cluster permissions (worker makes no K8s API calls).

### PostgreSQL subchart

`bitnami/postgresql` declared as a Helm dependency in `Chart.yaml`. `values.yaml` exposes `postgresql.auth.password` which flows into the `Secret`. The worker's `DB_HOST` points to the in-cluster PostgreSQL service name.

### values.yaml top-level structure

```yaml
image:
  repository: ghcr.io/<owner>/certificate-of-good-conduct-validation-system
  tag: latest
  pullPolicy: IfNotPresent

resources:
  requests: { cpu: 100m, memory: 128Mi }
  limits:   { cpu: 500m, memory: 256Mi }

config:
  paperless:
    baseUrl: "http://paperless:8000"
    tagName: "Führungszeugnis"
    pollIntervalSeconds: 300
  hitobito:
    baseUrl: "http://hitobito:3000"
    groupId: 1
    efzQualificationKindLabel: "Erweitertes Führungszeugnis"
  email:
    smtpHost: "localhost"
    smtpPort: 587
    smtpUseTls: true
    sender: "noreply@example.com"
    alertRecipients: ""
  general:
    logLevel: "INFO"
    certificateMaxAgeYears: 5
    expiryAlertDays: 120

secrets:
  paperlessToken: ""
  hitobitoToken: ""
  emailSmtpPassword: ""
  dbPassword: "changeme"

postgresql:
  auth:
    database: certificate_validation
    username: postgres
    password: "changeme"
```

---

## 2. GitHub Actions CI/CD

### Workflows

| File | Trigger | Purpose |
|---|---|---|
| `.github/workflows/ci.yml` | push, pull_request | Lint + unit tests + integration tests |
| `.github/workflows/release.yml` | push to `main` | Build & push image, lint Helm chart |

### ci.yml — job graph

```
lint ──┬── unit-tests
       └── integration-tests
```

1. **lint** — `uv run ruff check .` + `uv run ruff format --check .`
2. **unit-tests** — `uv run pytest tests/ --ignore=tests/integration` (no containers; all external calls mocked)
3. **integration-tests** — bring up `docker-compose.integration.yml`, run `uv run pytest tests/integration/`, tear down

Jobs 2 and 3 run in parallel after job 1 passes.

### release.yml — job graph (main only)

```
build-push  (parallel)  helm-lint
```

1. **build-push** — `docker/build-push-action` → `ghcr.io/<owner>/<repo>:sha-<short-sha>` + `:latest`
2. **helm-lint** — `helm dependency update` + `helm lint helm/fuehrungszeugnis` + `helm template` (dry-run render)

### Image tagging

- Every merge to `main` produces two tags: `sha-<7-char-sha>` and `latest`.
- Helm chart `values.yaml` defaults to `latest`; production deploys override with the SHA tag for reproducibility.

---

## 3. Integration Tests

### Layout

```
tests/integration/
  conftest.py
  docker-compose.integration.yml
  wiremock/
    mappings/
      paperless_documents.json
      paperless_tags.json
      hitobito_qualification_kinds.json
      hitobito_qualifications_post.json
      hitobito_roles.json
  test_validation_pipeline.py
  test_expiry_alerts.py
  test_migrations.py
```

### docker-compose.integration.yml services

| Service | Image | Purpose |
|---|---|---|
| `postgres` | `postgres:16-alpine` | Real DB — migrations + model writes verified against it |
| `wiremock` | `wiremock/wiremock:3` | HTTP stub for Paperless-ngx and hitobito APIs |
| `mailhog` | `mailhog/mailhog` | SMTP sink; exposes REST API at `:8025` for assertions |

All services on an isolated bridge network. Health checks on all three before tests start.

### conftest.py fixtures

| Fixture | Scope | Description |
|---|---|---|
| `pg_url` | session | DB URL from env (`TEST_DB_URL`), set by docker-compose |
| `db_session` | function | SQLAlchemy session; auto-rolled-back after each test |
| `wiremock_admin` | function | Calls `POST /__admin/reset` to clear all stubs and requests between tests, then re-registers the default mappings |
| `worker` | function | `ValidationWorker` pointing at WireMock + test DB + MailHog SMTP |

### Test files

**`test_migrations.py`**
- Runs `alembic upgrade head` against the test DB.
- Asserts `certificate_validations` table exists with columns: `id`, `paperless_document_id`, `person_name`, `is_valid`, `cancellation_date`, `created_at`, `updated_at`.

**`test_validation_pipeline.py`**
- Seeds WireMock with a Paperless tag response and a document containing valid cert OCR text (clean, recent issue date, person name present).
- Calls `worker.run_once()`.
- Asserts one `CertificateValidation` row: `is_valid=True`, `person_name` set, `cancellation_date` set.
- Second test: document with criminal entries → `is_valid=False`.
- Third test: calling `run_once()` again for the same document ID → no duplicate row (idempotency).

**`test_expiry_alerts.py`**
- Inserts a `CertificateValidation` row directly (`is_valid=True`, `cancellation_date = today + 30 days`).
- Calls `worker.run_expiry_check()`.
- Queries MailHog REST API (`GET /api/v2/messages`) and asserts one email delivered with correct recipient and German subject line containing "läuft bald ab".
- Second test: `cancellation_date = today + 200 days` (outside 120-day window) → no email sent.

### WireMock stub strategy

Each mapping file covers one endpoint. The valid-document mapping returns OCR text with a recognisable name (`"Name: Max Mustermann"`), a recent issue date, and "keine Eintragungen". The invalid-document mapping returns text with criminal entry indicators. Stubs are reset to their default state between tests via the WireMock admin API (`DELETE /__admin/scenarios`).

---

## Decisions not in scope

- Kubernetes Ingress / Service (worker has no HTTP port).
- Horizontal pod autoscaling.
- Helm chart CD (ArgoCD / Flux) — out of scope; chart is delivered, deploy tooling is the operator's choice.
- SMTP integration test (MailHog used only for expiry alerts; per-document alert emails tested at unit level).
