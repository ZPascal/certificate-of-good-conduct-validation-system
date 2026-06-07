# Helm, CI/CD & Integration Tests — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a production-ready Helm chart, GitHub Actions CI/CD pipeline, and a docker-compose-based integration test suite to the certificate-of-good-conduct-validation-system.

**Architecture:** Three independent subsystems built in sequence — (1) Helm chart deploying the worker + Bitnami PostgreSQL with ConfigMap/Secret config split, (2) two GH Actions workflows for CI and release, (3) pytest integration tests running against real postgres + WireMock + MailHog containers. The `pydantic-settings` env-override layer already in `src/config.py` means all Helm env vars are picked up without changing application code.

**Tech Stack:** Helm 3, Bitnami postgresql subchart, GitHub Actions, docker/build-push-action, ghcr.io, pytest, WireMock 3, MailHog, SQLAlchemy, Alembic.

---

## Subsystem A — Helm Chart

### Task 1: Chart scaffold and dependencies

**Files:**
- Create: `helm/fuehrungszeugnis/Chart.yaml`
- Create: `helm/fuehrungszeugnis/values.yaml`
- Create: `helm/fuehrungszeugnis/templates/_helpers.tpl`

- [ ] **Step 1: Create Chart.yaml**

```yaml
# helm/fuehrungszeugnis/Chart.yaml
apiVersion: v2
name: fuehrungszeugnis
description: Certificate of Good Conduct Validation System
type: application
version: 0.1.0
appVersion: "0.1.0"

dependencies:
  - name: postgresql
    version: "16.x.x"
    repository: "https://charts.bitnami.com/bitnami"
    condition: postgresql.enabled
```

- [ ] **Step 2: Create values.yaml**

```yaml
# helm/fuehrungszeugnis/values.yaml
image:
  repository: ghcr.io/zpascal/certificate-of-good-conduct-validation-system
  tag: latest
  pullPolicy: IfNotPresent

resources:
  requests:
    cpu: 100m
    memory: 128Mi
  limits:
    cpu: 500m
    memory: 256Mi

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
  # dbPassword is taken from postgresql.auth.password when postgresql.enabled=true
  # Set secrets.dbPassword when using an external database
  dbPassword: ""

postgresql:
  enabled: true
  auth:
    database: certificate_validation
    username: postgres
    password: "changeme"
  primary:
    persistence:
      enabled: true
      size: 8Gi
```

- [ ] **Step 3: Create templates/_helpers.tpl**

```
{{/*
helm/fuehrungszeugnis/templates/_helpers.tpl
*/}}

{{- define "fuehrungszeugnis.name" -}}
{{- .Chart.Name | trunc 63 | trimSuffix "-" }}
{{- end }}

{{- define "fuehrungszeugnis.fullname" -}}
{{- if .Values.fullnameOverride }}
{{- .Values.fullnameOverride | trunc 63 | trimSuffix "-" }}
{{- else }}
{{- $name := default .Chart.Name .Values.nameOverride }}
{{- printf "%s-%s" .Release.Name $name | trunc 63 | trimSuffix "-" }}
{{- end }}
{{- end }}

{{- define "fuehrungszeugnis.labels" -}}
helm.sh/chart: {{ .Chart.Name }}-{{ .Chart.Version }}
app.kubernetes.io/name: {{ include "fuehrungszeugnis.name" . }}
app.kubernetes.io/instance: {{ .Release.Name }}
app.kubernetes.io/managed-by: {{ .Release.Service }}
{{- end }}

{{- define "fuehrungszeugnis.selectorLabels" -}}
app.kubernetes.io/name: {{ include "fuehrungszeugnis.name" . }}
app.kubernetes.io/instance: {{ .Release.Name }}
{{- end }}

{{- define "fuehrungszeugnis.serviceAccountName" -}}
{{ include "fuehrungszeugnis.fullname" . }}
{{- end }}

{{/* Resolve DB password: prefer postgresql subchart password when enabled */}}
{{- define "fuehrungszeugnis.dbPassword" -}}
{{- if .Values.postgresql.enabled }}
{{- .Values.postgresql.auth.password }}
{{- else }}
{{- .Values.secrets.dbPassword }}
{{- end }}
{{- end }}

{{/* DB host: in-cluster service when subchart enabled, else use config value */}}
{{- define "fuehrungszeugnis.dbHost" -}}
{{- if .Values.postgresql.enabled }}
{{- printf "%s-postgresql" .Release.Name }}
{{- else }}
{{- .Values.config.database.host | default "localhost" }}
{{- end }}
{{- end }}
```

- [ ] **Step 4: Pull Helm dependencies**

```bash
cd helm/fuehrungszeugnis
helm dependency update
```

Expected: `charts/postgresql-*.tgz` downloaded into `helm/fuehrungszeugnis/charts/`.

- [ ] **Step 5: Commit**

```bash
git add helm/
git commit -m "feat(helm): scaffold chart with Bitnami postgresql dependency"
```

---

### Task 2: ConfigMap and Secret templates

**Files:**
- Create: `helm/fuehrungszeugnis/templates/configmap.yaml`
- Create: `helm/fuehrungszeugnis/templates/secret.yaml`

- [ ] **Step 1: Create configmap.yaml**

```yaml
# helm/fuehrungszeugnis/templates/configmap.yaml
apiVersion: v1
kind: ConfigMap
metadata:
  name: {{ include "fuehrungszeugnis.fullname" . }}
  labels:
    {{- include "fuehrungszeugnis.labels" . | nindent 4 }}
data:
  # Database (non-secret)
  DB_HOST: {{ include "fuehrungszeugnis.dbHost" . | quote }}
  DB_PORT: "5432"
  DB_NAME: {{ .Values.postgresql.auth.database | default "certificate_validation" | quote }}
  DB_USER: {{ .Values.postgresql.auth.username | default "postgres" | quote }}
  # Paperless
  PAPERLESS_BASE_URL: {{ .Values.config.paperless.baseUrl | quote }}
  PAPERLESS_TAG_NAME: {{ .Values.config.paperless.tagName | quote }}
  PAPERLESS_POLL_INTERVAL_SECONDS: {{ .Values.config.paperless.pollIntervalSeconds | toString | quote }}
  # Hitobito
  HITOBITO_BASE_URL: {{ .Values.config.hitobito.baseUrl | quote }}
  HITOBITO_GROUP_ID: {{ .Values.config.hitobito.groupId | toString | quote }}
  HITOBITO_EFZ_QUALIFICATION_KIND_LABEL: {{ .Values.config.hitobito.efzQualificationKindLabel | quote }}
  # Email
  EMAIL_SMTP_HOST: {{ .Values.config.email.smtpHost | quote }}
  EMAIL_SMTP_PORT: {{ .Values.config.email.smtpPort | toString | quote }}
  EMAIL_SMTP_USE_TLS: {{ .Values.config.email.smtpUseTls | toString | quote }}
  EMAIL_SENDER: {{ .Values.config.email.sender | quote }}
  EMAIL_ALERT_RECIPIENTS: {{ .Values.config.email.alertRecipients | quote }}
  # General
  LOG_LEVEL: {{ .Values.config.general.logLevel | quote }}
  CERTIFICATE_MAX_AGE_YEARS: {{ .Values.config.general.certificateMaxAgeYears | toString | quote }}
  EXPIRY_ALERT_DAYS: {{ .Values.config.general.expiryAlertDays | toString | quote }}
```

- [ ] **Step 2: Create secret.yaml**

```yaml
# helm/fuehrungszeugnis/templates/secret.yaml
apiVersion: v1
kind: Secret
metadata:
  name: {{ include "fuehrungszeugnis.fullname" . }}
  labels:
    {{- include "fuehrungszeugnis.labels" . | nindent 4 }}
type: Opaque
data:
  DB_PASSWORD: {{ include "fuehrungszeugnis.dbPassword" . | b64enc | quote }}
  PAPERLESS_TOKEN: {{ .Values.secrets.paperlessToken | b64enc | quote }}
  HITOBITO_TOKEN: {{ .Values.secrets.hitobitoToken | b64enc | quote }}
  EMAIL_SMTP_PASSWORD: {{ .Values.secrets.emailSmtpPassword | b64enc | quote }}
```

- [ ] **Step 3: Dry-run render to verify templates parse**

```bash
helm template test-release helm/fuehrungszeugnis \
  --set postgresql.enabled=true \
  --set postgresql.auth.password=testpw \
  2>&1 | grep -E "(kind:|name:|DB_HOST|DB_PASSWORD)"
```

Expected output includes lines like:
```
kind: ConfigMap
  DB_HOST: "test-release-postgresql"
kind: Secret
  DB_PASSWORD: <base64>
```

- [ ] **Step 4: Commit**

```bash
git add helm/fuehrungszeugnis/templates/configmap.yaml \
        helm/fuehrungszeugnis/templates/secret.yaml
git commit -m "feat(helm): add ConfigMap and Secret templates"
```

---

### Task 3: ServiceAccount and Deployment templates

**Files:**
- Create: `helm/fuehrungszeugnis/templates/serviceaccount.yaml`
- Create: `helm/fuehrungszeugnis/templates/deployment.yaml`

- [ ] **Step 1: Create serviceaccount.yaml**

```yaml
# helm/fuehrungszeugnis/templates/serviceaccount.yaml
apiVersion: v1
kind: ServiceAccount
metadata:
  name: {{ include "fuehrungszeugnis.serviceAccountName" . }}
  labels:
    {{- include "fuehrungszeugnis.labels" . | nindent 4 }}
```

- [ ] **Step 2: Create deployment.yaml**

```yaml
# helm/fuehrungszeugnis/templates/deployment.yaml
apiVersion: apps/v1
kind: Deployment
metadata:
  name: {{ include "fuehrungszeugnis.fullname" . }}
  labels:
    {{- include "fuehrungszeugnis.labels" . | nindent 4 }}
spec:
  replicas: 1
  selector:
    matchLabels:
      {{- include "fuehrungszeugnis.selectorLabels" . | nindent 6 }}
  template:
    metadata:
      labels:
        {{- include "fuehrungszeugnis.selectorLabels" . | nindent 8 }}
    spec:
      serviceAccountName: {{ include "fuehrungszeugnis.serviceAccountName" . }}
      initContainers:
        - name: migrate
          image: "{{ .Values.image.repository }}:{{ .Values.image.tag }}"
          imagePullPolicy: {{ .Values.image.pullPolicy }}
          command: ["python", "-m", "alembic", "upgrade", "head"]
          envFrom:
            - configMapRef:
                name: {{ include "fuehrungszeugnis.fullname" . }}
            - secretRef:
                name: {{ include "fuehrungszeugnis.fullname" . }}
      containers:
        - name: worker
          image: "{{ .Values.image.repository }}:{{ .Values.image.tag }}"
          imagePullPolicy: {{ .Values.image.pullPolicy }}
          command: ["python", "-m", "src.main"]
          envFrom:
            - configMapRef:
                name: {{ include "fuehrungszeugnis.fullname" . }}
            - secretRef:
                name: {{ include "fuehrungszeugnis.fullname" . }}
          resources:
            {{- toYaml .Values.resources | nindent 12 }}
```

- [ ] **Step 3: Full dry-run render and helm lint**

```bash
helm template test-release helm/fuehrungszeugnis \
  --set postgresql.enabled=true \
  --set postgresql.auth.password=testpw \
  > /dev/null && echo "render OK"

helm lint helm/fuehrungszeugnis \
  --set postgresql.enabled=true \
  --set postgresql.auth.password=testpw
```

Expected: `render OK` and `1 chart(s) linted, 0 chart(s) failed`.

- [ ] **Step 4: Commit**

```bash
git add helm/fuehrungszeugnis/templates/serviceaccount.yaml \
        helm/fuehrungszeugnis/templates/deployment.yaml
git commit -m "feat(helm): add ServiceAccount and Deployment with migration init-container"
```

---

## Subsystem B — GitHub Actions

### Task 4: CI workflow (lint + unit tests + integration tests)

**Files:**
- Create: `.github/workflows/ci.yml`

- [ ] **Step 1: Create .github/workflows/ci.yml**

```yaml
# .github/workflows/ci.yml
name: CI

on:
  push:
  pull_request:

jobs:
  lint:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
      - uses: astral-sh/setup-uv@v5
      - run: uv sync
      - run: uv run ruff check .
      - run: uv run ruff format --check .

  unit-tests:
    needs: lint
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
      - uses: astral-sh/setup-uv@v5
      - run: uv sync
      - run: uv run pytest tests/ --ignore=tests/integration -v

  integration-tests:
    needs: lint
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
      - uses: astral-sh/setup-uv@v5
      - run: uv sync
      - name: Start integration services
        run: docker compose -f tests/integration/docker-compose.integration.yml up -d --wait
      - name: Run integration tests
        run: uv run pytest tests/integration/ -v
      - name: Tear down services
        if: always()
        run: docker compose -f tests/integration/docker-compose.integration.yml down -v
```

- [ ] **Step 2: Commit**

```bash
git add .github/workflows/ci.yml
git commit -m "feat(ci): add CI workflow for lint, unit tests, and integration tests"
```

---

### Task 5: Release workflow (build, push, helm lint)

**Files:**
- Create: `.github/workflows/release.yml`

- [ ] **Step 1: Create .github/workflows/release.yml**

```yaml
# .github/workflows/release.yml
name: Release

on:
  push:
    branches:
      - main

jobs:
  build-push:
    runs-on: ubuntu-latest
    permissions:
      contents: read
      packages: write
    steps:
      - uses: actions/checkout@v4
      - uses: docker/login-action@v3
        with:
          registry: ghcr.io
          username: ${{ github.actor }}
          password: ${{ secrets.GITHUB_TOKEN }}
      - uses: docker/build-push-action@v6
        with:
          context: .
          push: true
          tags: |
            ghcr.io/${{ github.repository }}:latest
            ghcr.io/${{ github.repository }}:sha-${{ github.sha }}

  helm-lint:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
      - uses: azure/setup-helm@v4
      - run: helm dependency update helm/fuehrungszeugnis
      - run: helm lint helm/fuehrungszeugnis --set postgresql.auth.password=testpw
      - run: >
          helm template test-release helm/fuehrungszeugnis
          --set postgresql.auth.password=testpw
          > /dev/null && echo "render OK"
```

- [ ] **Step 2: Commit**

```bash
git add .github/workflows/release.yml
git commit -m "feat(ci): add release workflow for image build/push and Helm lint"
```

---

## Subsystem C — Integration Tests

### Task 6: Docker Compose and WireMock stubs

**Files:**
- Create: `tests/integration/docker-compose.integration.yml`
- Create: `tests/integration/wiremock/mappings/paperless_tags.json`
- Create: `tests/integration/wiremock/mappings/paperless_documents_valid.json`
- Create: `tests/integration/wiremock/mappings/hitobito_qualification_kinds.json`
- Create: `tests/integration/wiremock/mappings/hitobito_qualifications_post.json`
- Create: `tests/integration/wiremock/mappings/hitobito_roles.json`

- [ ] **Step 1: Create docker-compose.integration.yml**

```yaml
# tests/integration/docker-compose.integration.yml
version: "3.9"

services:
  postgres:
    image: postgres:16-alpine
    environment:
      POSTGRES_DB: test_certificate_validation
      POSTGRES_USER: postgres
      POSTGRES_PASSWORD: testpw
    ports:
      - "15432:5432"
    healthcheck:
      test: ["CMD-SHELL", "pg_isready -U postgres"]
      interval: 3s
      timeout: 3s
      retries: 10

  wiremock:
    image: wiremock/wiremock:3.10.0
    ports:
      - "18080:8080"
    volumes:
      - ./wiremock/mappings:/home/wiremock/mappings:ro
    healthcheck:
      test: ["CMD-SHELL", "wget -q --spider http://localhost:8080/__admin/health || exit 1"]
      interval: 3s
      timeout: 3s
      retries: 10

  mailhog:
    image: mailhog/mailhog:v1.0.1
    ports:
      - "11025:1025"
      - "18025:8025"
    healthcheck:
      test: ["CMD-SHELL", "wget -q --spider http://localhost:8025 || exit 1"]
      interval: 3s
      timeout: 3s
      retries: 10
```

- [ ] **Step 2: Create paperless_tags.json WireMock stub**

```json
{
  "request": {
    "method": "GET",
    "urlPath": "/api/tags/",
    "queryParameters": {
      "name__iexact": { "equalTo": "Führungszeugnis" }
    }
  },
  "response": {
    "status": 200,
    "headers": { "Content-Type": "application/json" },
    "jsonBody": {
      "count": 1,
      "results": [{ "id": 1, "name": "Führungszeugnis" }]
    }
  }
}
```

- [ ] **Step 3: Create paperless_documents_valid.json WireMock stub**

This stub returns a document list for the tag, then the document detail for doc ID 1 with valid OCR text.

```json
{
  "mappings": [
    {
      "request": {
        "method": "GET",
        "urlPath": "/api/documents/",
        "queryParameters": {
          "tags__id": { "equalTo": "1" }
        }
      },
      "response": {
        "status": 200,
        "headers": { "Content-Type": "application/json" },
        "jsonBody": {
          "count": 1,
          "results": [
            {
              "id": 1,
              "title": "Führungszeugnis Max Mustermann",
              "content": "Erweitertes Führungszeugnis\nName: Max Mustermann\nAusgestellt 01.01.2025\nEs sind keine Eintragungen vorhanden.",
              "created": "2025-01-01T00:00:00Z",
              "added": "2025-01-02T00:00:00Z",
              "tags": [],
              "original_file_name": "fz_mustermann.pdf"
            }
          ]
        }
      }
    }
  ]
}
```

- [ ] **Step 4: Create hitobito_qualification_kinds.json**

```json
{
  "request": {
    "method": "GET",
    "urlPath": "/api/qualification_kinds"
  },
  "response": {
    "status": 200,
    "headers": { "Content-Type": "application/vnd.api+json" },
    "jsonBody": {
      "data": [
        {
          "id": "7",
          "type": "qualification_kinds",
          "attributes": { "label": "Erweitertes Führungszeugnis" }
        }
      ],
      "meta": {}
    }
  }
}
```

- [ ] **Step 5: Create hitobito_qualifications_post.json**

```json
{
  "request": {
    "method": "POST",
    "urlPath": "/api/qualifications"
  },
  "response": {
    "status": 201,
    "headers": { "Content-Type": "application/vnd.api+json" },
    "jsonBody": {
      "data": {
        "id": "99",
        "type": "qualifications",
        "attributes": {
          "start_at": "2025-01-01",
          "finish_at": "2030-01-01",
          "origin": "Automatisch validiert"
        }
      }
    }
  }
}
```

- [ ] **Step 6: Create hitobito_roles.json**

```json
{
  "request": {
    "method": "GET",
    "urlPath": "/api/roles"
  },
  "response": {
    "status": 200,
    "headers": { "Content-Type": "application/vnd.api+json" },
    "jsonBody": {
      "data": [],
      "included": [],
      "meta": {}
    }
  }
}
```

- [ ] **Step 7: Verify docker-compose starts cleanly**

```bash
docker compose -f tests/integration/docker-compose.integration.yml up -d --wait
docker compose -f tests/integration/docker-compose.integration.yml ps
docker compose -f tests/integration/docker-compose.integration.yml down -v
```

Expected: all three services show `healthy` before `down`.

- [ ] **Step 8: Commit**

```bash
git add tests/integration/docker-compose.integration.yml \
        tests/integration/wiremock/
git commit -m "feat(integration): add docker-compose services and WireMock stubs"
```

---

### Task 7: Integration test fixtures (conftest.py)

**Files:**
- Create: `tests/integration/__init__.py`
- Create: `tests/integration/conftest.py`
- Modify: `pyproject.toml` — add `requests` to dev deps (already in main deps; add `pytest-docker` or rely on env vars)

- [ ] **Step 1: Add integration test env vars to pyproject.toml**

The integration tests read DB URL and service ports from env vars set by docker-compose. No new packages needed — `requests` is already a main dependency and available in the test env.

Open `pyproject.toml` and confirm `requests` is listed under `[project] dependencies`. It is. No change needed.

- [ ] **Step 2: Create tests/integration/__init__.py**

```python
# tests/integration/__init__.py
```

- [ ] **Step 3: Create tests/integration/conftest.py**

```python
# tests/integration/conftest.py
"""Fixtures for integration tests.

Required environment variables (set by docker-compose.integration.yml ports):
  TEST_DB_URL       - e.g. postgresql+psycopg2://postgres:testpw@localhost:15432/test_certificate_validation
  TEST_WIREMOCK_URL - e.g. http://localhost:18080
  TEST_MAILHOG_URL  - e.g. http://localhost:18025
  TEST_SMTP_PORT    - e.g. 11025
"""

import os
import subprocess
from unittest.mock import patch

import pytest
import requests
from sqlalchemy import create_engine, text
from sqlalchemy.orm import sessionmaker

from src.database.models import Base

TEST_DB_URL = os.environ.get(
    "TEST_DB_URL",
    "postgresql+psycopg2://postgres:testpw@localhost:15432/test_certificate_validation",
)
TEST_WIREMOCK_URL = os.environ.get("TEST_WIREMOCK_URL", "http://localhost:18080")
TEST_MAILHOG_URL = os.environ.get("TEST_MAILHOG_URL", "http://localhost:18025")
TEST_SMTP_PORT = int(os.environ.get("TEST_SMTP_PORT", "11025"))


@pytest.fixture(scope="session")
def db_engine():
    engine = create_engine(TEST_DB_URL, pool_pre_ping=True)
    Base.metadata.create_all(engine)
    yield engine
    Base.metadata.drop_all(engine)
    engine.dispose()


@pytest.fixture
def db_session(db_engine):
    connection = db_engine.connect()
    transaction = connection.begin()
    Session = sessionmaker(bind=connection)
    session = Session()
    yield session
    session.close()
    transaction.rollback()
    connection.close()


@pytest.fixture(autouse=True)
def wiremock_reset():
    """Reset WireMock request journal between tests (stubs defined in mappings/ are permanent)."""
    requests.delete(f"{TEST_WIREMOCK_URL}/__admin/requests")
    yield
    requests.delete(f"{TEST_WIREMOCK_URL}/__admin/requests")


@pytest.fixture(autouse=True)
def mailhog_reset():
    """Delete all MailHog messages between tests."""
    requests.delete(f"{TEST_MAILHOG_URL}/api/v1/messages")
    yield


@pytest.fixture
def worker(db_engine):
    """A ValidationWorker instance wired to the integration test services."""
    from src.config import (
        DatabaseSettings,
        EmailSettings,
        HitobitoSettings,
        PaperlessSettings,
        Settings,
    )
    from src.main import ValidationWorker

    settings = Settings(
        database=DatabaseSettings(
            host="localhost",
            port=15432,
            name="test_certificate_validation",
            user="postgres",
            password="testpw",
        ),
        paperless=PaperlessSettings(
            base_url=TEST_WIREMOCK_URL,
            token="test-token",
            tag_name="Führungszeugnis",
            poll_interval_seconds=300,
        ),
        hitobito=HitobitoSettings(
            base_url=TEST_WIREMOCK_URL,
            token="test-token",
            group_id=1,
            efz_qualification_kind_label="Erweitertes Führungszeugnis",
        ),
        email=EmailSettings(
            smtp_host="localhost",
            smtp_port=TEST_SMTP_PORT,
            smtp_user="",
            smtp_password="",
            smtp_use_tls=False,
            sender="noreply@test.local",
            alert_recipients="admin@test.local",
        ),
        log_level="DEBUG",
        certificate_max_age_years=5,
        expiry_alert_days=120,
    )
    with patch("src.main.get_settings", return_value=settings):
        w = ValidationWorker()
    # Override session_factory to use the test engine
    from sqlalchemy.orm import sessionmaker as sm
    w.session_factory = sm(bind=db_engine, autoflush=False, autocommit=False)
    return w
```

- [ ] **Step 4: Commit**

```bash
git add tests/integration/__init__.py tests/integration/conftest.py
git commit -m "feat(integration): add pytest fixtures for db, wiremock, mailhog, and worker"
```

---

### Task 8: Migration integration test

**Files:**
- Create: `tests/integration/test_migrations.py`

- [ ] **Step 1: Write the test**

```python
# tests/integration/test_migrations.py
"""Verify alembic migrations produce the expected schema."""

import os
import subprocess

import pytest
from sqlalchemy import inspect


@pytest.fixture(scope="module")
def migrated_engine():
    """Run alembic upgrade head against the test DB, yield the engine."""
    from sqlalchemy import create_engine

    from src.database.models import Base

    db_url = os.environ.get(
        "TEST_DB_URL",
        "postgresql+psycopg2://postgres:testpw@localhost:15432/test_certificate_validation",
    )
    engine = create_engine(db_url)
    # Drop all first so we test a clean migration run
    Base.metadata.drop_all(engine)
    env = {**os.environ, "DB_HOST": "localhost", "DB_PORT": "15432",
           "DB_NAME": "test_certificate_validation", "DB_USER": "postgres",
           "DB_PASSWORD": "testpw"}
    result = subprocess.run(
        ["python", "-m", "alembic", "upgrade", "head"],
        capture_output=True, text=True, env=env,
    )
    assert result.returncode == 0, f"alembic failed:\n{result.stderr}"
    yield engine
    engine.dispose()


def test_certificate_validations_table_exists(migrated_engine):
    inspector = inspect(migrated_engine)
    assert "certificate_validations" in inspector.get_table_names()


def test_certificate_validations_columns(migrated_engine):
    inspector = inspect(migrated_engine)
    cols = {c["name"] for c in inspector.get_columns("certificate_validations")}
    assert cols == {"id", "paperless_document_id", "person_name",
                    "is_valid", "cancellation_date", "created_at", "updated_at"}


def test_paperless_document_id_unique_index(migrated_engine):
    inspector = inspect(migrated_engine)
    indexes = inspector.get_indexes("certificate_validations")
    unique_indexes = [i for i in indexes if i["unique"]]
    indexed_cols = [col for i in unique_indexes for col in i["column_names"]]
    assert "paperless_document_id" in indexed_cols
```

- [ ] **Step 2: Start services and run the test**

```bash
docker compose -f tests/integration/docker-compose.integration.yml up -d --wait
uv run pytest tests/integration/test_migrations.py -v
```

Expected: 3 tests pass.

- [ ] **Step 3: Commit**

```bash
git add tests/integration/test_migrations.py
git commit -m "test(integration): add migration schema assertion tests"
```

---

### Task 9: Validation pipeline integration test

**Files:**
- Create: `tests/integration/test_validation_pipeline.py`

- [ ] **Step 1: Write the tests**

```python
# tests/integration/test_validation_pipeline.py
"""End-to-end pipeline tests: run_once() → assert DB state."""

from src.database.models import CertificateValidation


class TestValidDocumentPipeline:
    def test_valid_certificate_creates_valid_row(self, worker, db_session):
        """A clean cert (keine Eintragungen, recent date) → is_valid=True."""
        # WireMock returns doc ID 1 with valid OCR text (see paperless_documents_valid.json)
        worker.run_once()

        row = db_session.query(CertificateValidation).filter_by(
            paperless_document_id=1
        ).one_or_none()

        assert row is not None
        assert row.is_valid is True
        assert row.person_name == "Max Mustermann"
        assert row.cancellation_date is not None

    def test_valid_certificate_is_idempotent(self, worker, db_session):
        """Running run_once() twice for the same document must not create a duplicate row."""
        worker.run_once()
        worker.run_once()

        count = db_session.query(CertificateValidation).filter_by(
            paperless_document_id=1
        ).count()
        assert count == 1


class TestInvalidDocumentPipeline:
    def test_invalid_certificate_creates_invalid_row(self, worker, db_session, wiremock_invalid):
        """A cert with criminal entries → is_valid=False."""
        worker.run_once()

        row = db_session.query(CertificateValidation).filter_by(
            paperless_document_id=2
        ).one_or_none()

        assert row is not None
        assert row.is_valid is False
        assert row.person_name == "Erika Musterfrau"
```

Note: `wiremock_invalid` is a fixture (added in the next step) that swaps the documents stub to return the invalid document.

- [ ] **Step 2: Add wiremock_invalid fixture to conftest.py**

Append to `tests/integration/conftest.py`:

```python
@pytest.fixture
def wiremock_invalid():
    """Override the documents stub to return the invalid certificate document."""
    import json

    mapping = {
        "request": {
            "method": "GET",
            "urlPath": "/api/documents/",
            "queryParameters": {"tags__id": {"equalTo": "1"}},
        },
        "response": {
            "status": 200,
            "headers": {"Content-Type": "application/json"},
            "jsonBody": {
                "count": 1,
                "results": [
                    {
                        "id": 2,
                        "title": "Führungszeugnis Erika Musterfrau",
                        "content": (
                            "Erweitertes Führungszeugnis\n"
                            "Name: Erika Musterfrau\n"
                            "Ausgestellt 01.01.2025\n"
                            "Eintragung: Verurteilung wegen Straftat\n"
                            "Freiheitsstrafe 6 Monate\n"
                            "Verurteilung rechtskräftig."
                        ),
                        "created": "2025-01-01T00:00:00Z",
                        "added": "2025-01-02T00:00:00Z",
                        "tags": [],
                        "original_file_name": "fz_musterfrau.pdf",
                    }
                ],
            },
        },
        "priority": 1,
    }
    resp = requests.post(
        f"{TEST_WIREMOCK_URL}/__admin/mappings", json=mapping
    )
    stub_id = resp.json().get("id")
    yield
    if stub_id:
        requests.delete(f"{TEST_WIREMOCK_URL}/__admin/mappings/{stub_id}")
```

- [ ] **Step 3: Run the tests**

```bash
uv run pytest tests/integration/test_validation_pipeline.py -v
```

Expected: all 3 tests pass.

- [ ] **Step 4: Commit**

```bash
git add tests/integration/test_validation_pipeline.py \
        tests/integration/conftest.py
git commit -m "test(integration): add end-to-end validation pipeline tests"
```

---

### Task 10: Expiry alert integration test

**Files:**
- Create: `tests/integration/test_expiry_alerts.py`

- [ ] **Step 1: Write the tests**

```python
# tests/integration/test_expiry_alerts.py
"""Integration tests for the 4-month expiry alert job."""

from datetime import date, timedelta

import requests as http_requests

from src.database.models import CertificateValidation

TEST_MAILHOG_URL = "http://localhost:18025"


def _mailhog_messages() -> list[dict]:
    resp = http_requests.get(f"{TEST_MAILHOG_URL}/api/v2/messages")
    resp.raise_for_status()
    return resp.json().get("items", [])


class TestExpiryAlerts:
    def test_sends_alert_for_certificate_expiring_within_window(
        self, worker, db_session
    ):
        """Certificate expiring in 30 days → expiry alert email sent."""
        record = CertificateValidation(
            paperless_document_id=900,
            person_name="Fritz Fischer",
            is_valid=True,
            cancellation_date=date.today() + timedelta(days=30),
        )
        db_session.add(record)
        db_session.flush()

        worker.run_expiry_check()

        messages = _mailhog_messages()
        assert len(messages) == 1
        # MailHog stores subject in Raw.Data header block; check decoded subject
        subject = messages[0]["Content"]["Headers"]["Subject"][0]
        assert "läuft bald ab" in subject
        assert "Fritz Fischer" in subject

    def test_no_alert_for_certificate_outside_window(self, worker, db_session):
        """Certificate expiring in 200 days (outside 120-day window) → no email."""
        record = CertificateValidation(
            paperless_document_id=901,
            person_name="Greta Grün",
            is_valid=True,
            cancellation_date=date.today() + timedelta(days=200),
        )
        db_session.add(record)
        db_session.flush()

        worker.run_expiry_check()

        assert _mailhog_messages() == []

    def test_no_alert_for_invalid_certificate(self, worker, db_session):
        """Invalid cert even with imminent cancellation_date → no expiry alert."""
        record = CertificateValidation(
            paperless_document_id=902,
            person_name="Hans Huber",
            is_valid=False,
            cancellation_date=date.today() + timedelta(days=10),
        )
        db_session.add(record)
        db_session.flush()

        worker.run_expiry_check()

        assert _mailhog_messages() == []
```

- [ ] **Step 2: Run the tests**

```bash
uv run pytest tests/integration/test_expiry_alerts.py -v
```

Expected: all 3 tests pass.

- [ ] **Step 3: Commit**

```bash
git add tests/integration/test_expiry_alerts.py
git commit -m "test(integration): add expiry alert email integration tests"
```

---

### Task 11: Full integration test run and CI smoke-check

- [ ] **Step 1: Run the full integration suite locally**

```bash
docker compose -f tests/integration/docker-compose.integration.yml up -d --wait
uv run pytest tests/integration/ -v
docker compose -f tests/integration/docker-compose.integration.yml down -v
```

Expected: all integration tests pass.

- [ ] **Step 2: Run the unit suite with the ignore flag**

```bash
uv run pytest tests/ --ignore=tests/integration -v
```

Expected: 54 existing tests all pass (no regressions).

- [ ] **Step 3: Run ruff**

```bash
uv run ruff check . && uv run ruff format --check .
```

Expected: `All checks passed!`

- [ ] **Step 4: Final commit**

```bash
git add .
git commit -m "feat: complete Helm chart, CI/CD workflows, and integration tests"
```
