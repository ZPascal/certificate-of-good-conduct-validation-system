# Certificate of Good Conduct Validation System

Automated validation system for German *Führungszeugnisse* (certificates of good conduct)
used in DPSG child care work, with integration for [Paperless-ngx](https://docs.paperless-ngx.com/)
and [hitobito](https://hitobito.com/) (DPSG wagon).

---

## Overview

Under **§72a SGB VIII** (Social Code Book VIII – Child and Youth Services), anyone working with
children or young people in a DPSG context must present a valid *Erweitertes Führungszeugnis*
(extended certificate of good conduct). This system automates:

1. **Polling** Paperless-ngx for documents tagged as *Führungszeugnis*
2. **Validating** each document via OCR text analysis:
   - Confirms the document is an *Erweitertes Führungszeugnis*
   - Checks for the absence of relevant criminal entries
   - Verifies the certificate is not older than the configured maximum age (default: 5 years)
3. **Storing** validation results in a PostgreSQL database
4. **Recording** valid certificates as qualifications in hitobito via the JSON:API
   (`POST /api/qualifications` with the *Erweitertes Führungszeugnis* qualification kind)
5. **Alerting** designated *Erfasser\*in Führungszeugnis* staff via email whenever a
   certificate is invalid, expired, or about to expire

---

## Architecture

```
Paperless-ngx  ──→  ValidationWorker  ──→  PostgreSQL
                         │
                         ├──→  hitobito JSON:API  (record EFZ qualification)
                         └──→  SMTP               (alert email)
```

The worker runs on a configurable polling interval (default: every 5 minutes).
Already-processed documents are skipped to avoid duplicate work.

---

## Prerequisites

- Docker and Docker Compose (recommended), **or** Python 3.12+ with a PostgreSQL 14+ instance
- A running [Paperless-ngx](https://docs.paperless-ngx.com/) instance with documents tagged
  as *Führungszeugnis*
- A running [hitobito](https://hitobito.com/) instance (DPSG wagon) with:
  - A service token with `people` and `qualifications` scopes enabled and
    `layer_and_below_read` (or `layer_and_below_full`) permission
  - A `QualificationKind` record labelled *Erweitertes Führungszeugnis*

---

## Quick Start (Docker Compose)

```bash
# 1. Copy the example environment file and fill in your values
cp config/.env.example .env
$EDITOR .env

# 2. Start the database, run migrations, and start the worker
docker compose up -d

# 3. Tail logs
docker compose logs -f worker
```

---

## Configuration

All settings are read from environment variables (or a `.env` file in the project root).

| Variable | Default | Description |
|---|---|---|
| `DB_HOST` | `localhost` | PostgreSQL host |
| `DB_PORT` | `5432` | PostgreSQL port |
| `DB_NAME` | `certificate_validation` | Database name |
| `DB_USER` | `postgres` | Database user |
| `DB_PASSWORD` | *(empty)* | Database password |
| `PAPERLESS_BASE_URL` | `http://localhost:8000` | Paperless-ngx base URL |
| `PAPERLESS_TOKEN` | *(empty)* | Paperless-ngx API token |
| `PAPERLESS_TAG_NAME` | `Führungszeugnis` | Tag applied to certificate documents |
| `PAPERLESS_POLL_INTERVAL_SECONDS` | `300` | Polling interval in seconds |
| `HITOBITO_BASE_URL` | `http://localhost:3000` | hitobito base URL |
| `HITOBITO_TOKEN` | *(empty)* | hitobito service token (X-TOKEN header) |
| `HITOBITO_GROUP_ID` | `1` | Layer group ID for alert recipient lookups |
| `HITOBITO_EFZ_QUALIFICATION_KIND_LABEL` | `Erweitertes Führungszeugnis` | Label of the EFZ qualification kind |
| `EMAIL_SMTP_HOST` | `localhost` | SMTP server hostname |
| `EMAIL_SMTP_PORT` | `587` | SMTP port |
| `EMAIL_SMTP_USER` | *(empty)* | SMTP username |
| `EMAIL_SMTP_PASSWORD` | *(empty)* | SMTP password |
| `EMAIL_SMTP_USE_TLS` | `true` | Use STARTTLS |
| `EMAIL_SENDER` | `noreply@example.com` | From address for alert emails |
| `EMAIL_ALERT_RECIPIENTS` | *(empty)* | Comma-separated alert recipients; if empty, fetched from hitobito |
| `LOG_LEVEL` | `INFO` | Logging level (`DEBUG`, `INFO`, `WARNING`, `ERROR`) |
| `CERTIFICATE_MAX_AGE_YEARS` | `5` | Maximum certificate age in years |

---

## Hitobito Integration

The system uses the hitobito [JSON:API](https://hitobito.com/en/api) to:

### Record validated certificates

When a certificate passes validation, the system calls:

```
POST /api/qualifications
Content-Type: application/vnd.api+json
X-TOKEN: <service-token>

{
  "data": {
    "type": "qualifications",
    "attributes": {
      "start_at": "<issue-date>",
      "finish_at": "<expiry-date>",
      "origin": "Automatisch validiert via Führungszeugnis-Validierungssystem (#<doc-id>)"
    },
    "relationships": {
      "person": { "data": { "type": "people", "id": "<hitobito-person-id>" } },
      "qualification_kind": { "data": { "type": "qualification_kinds", "id": "<efz-kind-id>" } }
    }
  }
}
```

The `qualification_kind_id` is resolved automatically by looking up the label
`Erweitertes Führungszeugnis` (configurable via `HITOBITO_EFZ_QUALIFICATION_KIND_LABEL`)
via `GET /api/qualification_kinds`.

### Service token requirements

The hitobito service token must have:
- **people**: `true`
- **qualifications**: `true`
- **permission**: `layer_and_below_read` (for alert lookups) or `layer_and_below_full` (for writes)

### Linking documents to hitobito people

The `certificate_validations` database table includes a `hitobito_person_id` column. To associate
a Paperless document with a hitobito person, populate this field (e.g. via a pre-processing step
that matches document metadata to person records) before or after the initial validation pass.

---

## Database Schema

### `certificate_validations`

| Column | Type | Description |
|---|---|---|
| `id` | `INTEGER` | Primary key |
| `paperless_document_id` | `INTEGER` | Paperless-ngx document ID (unique) |
| `paperless_document_title` | `VARCHAR` | Document title from Paperless |
| `person_name` | `VARCHAR` | Extracted person name |
| `hitobito_person_id` | `BIGINT` | hitobito person ID |
| `hitobito_group_id` | `BIGINT` | hitobito group ID |
| `issue_date` | `TIMESTAMPTZ` | Certificate issue date |
| `valid_until` | `TIMESTAMPTZ` | Certificate expiry date |
| `certificate_type` | `VARCHAR` | `erweitertes_fuehrungszeugnis` or `fuehrungszeugnis` |
| `validation_status` | `ENUM` | `pending`, `valid`, `invalid`, `expired`, `error` |
| `validation_details` | `JSON` | Details, errors, and warnings from validation |
| `raw_text` | `TEXT` | OCR text extracted by Paperless |
| `created_at` | `TIMESTAMPTZ` | Record creation timestamp |
| `updated_at` | `TIMESTAMPTZ` | Record last-updated timestamp |

### `validation_alerts`

| Column | Type | Description |
|---|---|---|
| `id` | `INTEGER` | Primary key |
| `certificate_validation_id` | `INTEGER` | FK → `certificate_validations.id` |
| `alert_type` | `ENUM` | `invalid_certificate`, `expired_certificate`, `expiring_soon`, `processing_error`, `missing_certificate` |
| `alert_message` | `TEXT` | Human-readable alert message |
| `email_sent_at` | `TIMESTAMPTZ` | When the alert email was sent |
| `email_recipient` | `VARCHAR` | Recipient email address |
| `created_at` | `TIMESTAMPTZ` | Record creation timestamp |

---

## Development

### Install dependencies

```bash
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

### Run tests

```bash
pytest
```

### Run database migrations

```bash
# Apply migrations
alembic upgrade head

# Create a new migration after changing models
alembic revision --autogenerate -m "description"
```

### Run the worker locally

```bash
cp config/.env.example .env
# edit .env with your settings
python -m src.main
```

---

## License

MIT – see [LICENSE](LICENSE).
