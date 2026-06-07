# Stamm/Diözese Grouping, Address Matching, and Async Hitobito Calls — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add Stamm/Diözese organisational fields to certificate records, improve hitobito person matching with street-address disambiguation, update existing EFZ qualifications when dates change, and move the hitobito API call to a background thread.

**Architecture:** Four independent tasks applied sequentially. Tasks 1–2 are pure additive changes (new config fields + DB columns + migration). Task 3 extends the validator and hitobito client with new methods. Task 4 wires the async threading model into the main worker and connects all the new methods. All tests are written before implementation (TDD).

**Tech Stack:** Python 3.12, SQLAlchemy 2.0 mapped columns, Alembic, pydantic-settings v2, `threading.Thread` (stdlib), `re` (stdlib), `responses` library for HTTP mocking in unit tests.

---

## Task 1: Stamm/Diözese config fields, model columns, and migration

**Files:**
- Modify: `src/config.py` — add `stamm_name`, `dioezese_name` to `HitobitoSettings`
- Modify: `src/database/models.py` — add two nullable `String(255)` columns
- Create: `migrations/versions/003_add_stamm_dioezese.py` — Alembic migration
- Modify: `helm/fuehrungszeugnis/values.yaml` — two new keys under `config.hitobito`
- Modify: `helm/fuehrungszeugnis/templates/configmap.yaml` — two new env var entries
- Modify: `tests/integration/test_migrations.py` — add columns to expected set

- [ ] **Step 1: Write the failing migration column test**

Open `tests/integration/test_migrations.py` and update the column assertion to include the two new columns:

```python
def test_certificate_validations_columns(migrated_engine):
    inspector = inspect(migrated_engine)
    cols = {c["name"] for c in inspector.get_columns("certificate_validations")}
    assert cols == {
        "id",
        "paperless_document_id",
        "person_name",
        "is_valid",
        "cancellation_date",
        "last_expiry_alert_at",
        "stamm_name",
        "dioezese_name",
        "created_at",
        "updated_at",
    }
```

- [ ] **Step 2: Add config fields to `HitobitoSettings`**

Open `src/config.py`. The current `HitobitoSettings` class is:

```python
class HitobitoSettings(BaseSettings):
    base_url: str = "http://localhost:3000"
    token: str = ""
    group_id: int = 1
    efz_qualification_kind_label: str = "Erweitertes Führungszeugnis"
```

Replace it with:

```python
class HitobitoSettings(BaseSettings):
    base_url: str = "http://localhost:3000"
    token: str = ""
    group_id: int = 1
    efz_qualification_kind_label: str = "Erweitertes Führungszeugnis"
    stamm_name: str = ""
    dioezese_name: str = ""
```

- [ ] **Step 3: Add model columns**

Open `src/database/models.py`. The current column block after `last_expiry_alert_at` is:

```python
    cancellation_date: Mapped[date | None] = mapped_column(Date)
    last_expiry_alert_at: Mapped[date | None] = mapped_column(Date)

    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
```

Replace it with:

```python
    cancellation_date: Mapped[date | None] = mapped_column(Date)
    last_expiry_alert_at: Mapped[date | None] = mapped_column(Date)
    stamm_name: Mapped[str | None] = mapped_column(String(255))
    dioezese_name: Mapped[str | None] = mapped_column(String(255))

    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
```

- [ ] **Step 4: Write migration 003**

Create `migrations/versions/003_add_stamm_dioezese.py` with the full content:

```python
"""Add stamm_name and dioezese_name columns to certificate_validations.

Revision ID: 003
Revises: 002
Create Date: 2026-06-06
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "003"
down_revision: str | None = "002"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "certificate_validations",
        sa.Column("stamm_name", sa.String(255), nullable=True),
    )
    op.add_column(
        "certificate_validations",
        sa.Column("dioezese_name", sa.String(255), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("certificate_validations", "dioezese_name")
    op.drop_column("certificate_validations", "stamm_name")
```

- [ ] **Step 5: Update Helm values and ConfigMap**

In `helm/fuehrungszeugnis/values.yaml`, add two keys under `config.hitobito`:

```yaml
config:
  hitobito:
    baseUrl: "http://hitobito:3000"
    groupId: 1
    efzQualificationKindLabel: "Erweitertes Führungszeugnis"
    stammName: ""
    dioezeseName: ""
```

In `helm/fuehrungszeugnis/templates/configmap.yaml`, add two lines after `HITOBITO__EFZ_QUALIFICATION_KIND_LABEL`:

```yaml
  HITOBITO__STAMM_NAME: {{ .Values.config.hitobito.stammName | quote }}
  HITOBITO__DIOEZESE_NAME: {{ .Values.config.hitobito.dioezeseName | quote }}
```

- [ ] **Step 6: Run unit tests to confirm no regressions**

```bash
uv run python -m pytest tests/ --ignore=tests/integration -q
```

Expected: all existing tests pass.

- [ ] **Step 7: Commit**

```bash
git add src/config.py src/database/models.py \
    migrations/versions/003_add_stamm_dioezese.py \
    helm/fuehrungszeugnis/values.yaml \
    helm/fuehrungszeugnis/templates/configmap.yaml \
    tests/integration/test_migrations.py
git commit -m "feat: add Stamm/Diözese config fields, model columns, and migration 003"
```

---

## Task 2: Stamp org fields onto CertificateValidation in the pipeline

**Files:**
- Modify: `src/main.py:114-120` — record creation in `_process_document`
- Modify: `tests/test_expiry_check.py` — the `_make_record` helper should be fine; no change needed

- [ ] **Step 1: Write a unit test for org-field stamping**

Open `tests/test_expiry_check.py`. The `worker` fixture currently patches settings with no `stamm_name`/`dioezese_name`. We'll add a focused unit test. At the bottom of the file, add a new class:

```python
class TestOrgFieldStamping:
    def test_stamm_and_dioezese_stamped_on_record(self):
        with (
            patch("src.main.get_settings") as mock_settings,
            patch("src.main.create_session_factory"),
            patch("src.main.PaperlessClient"),
            patch("src.main.CertificateValidator") as mock_validator_cls,
            patch("src.main.HitobitoClient"),
            patch("src.main.EmailNotifier"),
        ):
            settings = MagicMock()
            settings.certificate_max_age_years = 5
            settings.hitobito.stamm_name = "Stamm St. Georg"
            settings.hitobito.dioezese_name = "Diözese Freiburg"
            settings.email.alert_recipients_list = []
            settings.hitobito.group_id = 0
            mock_settings.return_value = settings

            mock_validator = MagicMock()
            mock_result = MagicMock()
            mock_result.is_valid = True
            mock_result.person_name = "Max Mustermann"
            mock_result.valid_until = None
            mock_result.warnings = []
            mock_validator.validate.return_value = mock_result
            mock_validator_cls.return_value = mock_validator

            from src.main import ValidationWorker

            w = ValidationWorker()

            saved_records = []

            session_mock = MagicMock()
            session_mock.query.return_value.filter_by.return_value.first.return_value = None

            def capture_add(record):
                saved_records.append(record)

            session_mock.add.side_effect = capture_add

            document = MagicMock()
            document.id = 1
            document.title = "Führungszeugnis"
            document.content = "Führungszeugnis\nKeine Eintragungen"

            with patch.object(w, "_db_session") as mock_ctx, \
                 patch.object(w, "_record_efz_in_hitobito_async"):
                mock_ctx.return_value.__enter__ = MagicMock(return_value=session_mock)
                mock_ctx.return_value.__exit__ = MagicMock(return_value=False)
                w._process_document(document)

            assert len(saved_records) == 1
            assert saved_records[0].stamm_name == "Stamm St. Georg"
            assert saved_records[0].dioezese_name == "Diözese Freiburg"
```

- [ ] **Step 2: Run test to verify it fails**

```bash
uv run python -m pytest tests/test_expiry_check.py::TestOrgFieldStamping -v
```

Expected: FAIL — `CertificateValidation` has no `stamm_name` attribute yet in the constructor call (or the test fails on `_record_efz_in_hitobito_async` not existing).

- [ ] **Step 3: Update `_process_document` in `src/main.py`**

Find the record creation block at line 114–120:

```python
            cancellation_date = result.valid_until.date() if result.valid_until else None
            record = CertificateValidation(
                paperless_document_id=document.id,
                person_name=result.person_name,
                is_valid=result.is_valid,
                cancellation_date=cancellation_date,
            )
            session.add(record)
            session.flush()

            if result.is_valid:
                self._record_efz_in_hitobito(record, result)

            self._send_alerts_for_result(record, result)
```

Replace it with (the thread dispatch and method rename come in Task 4; for now just add the org fields and introduce the placeholder method name):

```python
            cancellation_date = result.valid_until.date() if result.valid_until else None
            record = CertificateValidation(
                paperless_document_id=document.id,
                person_name=result.person_name,
                is_valid=result.is_valid,
                cancellation_date=cancellation_date,
                stamm_name=self.settings.hitobito.stamm_name or None,
                dioezese_name=self.settings.hitobito.dioezese_name or None,
            )
            session.add(record)
            session.flush()

            if result.is_valid:
                self._record_efz_in_hitobito(record, result)

            self._send_alerts_for_result(record, result)
```

- [ ] **Step 4: Run the test again**

```bash
uv run python -m pytest tests/test_expiry_check.py::TestOrgFieldStamping -v
```

Expected: FAIL still — because the test references `_record_efz_in_hitobito_async` which doesn't exist yet. Adjust the patch target in the test to `_record_efz_in_hitobito` (the current method name) until Task 4 renames it:

```python
            with patch.object(w, "_db_session") as mock_ctx, \
                 patch.object(w, "_record_efz_in_hitobito"):
```

- [ ] **Step 5: Run test to verify it passes**

```bash
uv run python -m pytest tests/test_expiry_check.py::TestOrgFieldStamping -v
```

Expected: PASS.

- [ ] **Step 6: Run all unit tests**

```bash
uv run python -m pytest tests/ --ignore=tests/integration -q
```

Expected: all tests pass.

- [ ] **Step 7: Commit**

```bash
git add src/main.py tests/test_expiry_check.py
git commit -m "feat: stamp Stamm/Diözese org fields onto CertificateValidation records"
```

---

## Task 3: Street extraction in validator + address disambiguation + qualification update in hitobito client

**Files:**
- Modify: `src/validation/certificate_validator.py` — add `street` to `ValidationResult`, add `_STREET_PATTERN`, add `_extract_street_address()`
- Modify: `src/hitobito/client.py` — add `_normalize_street()`, `find_person_by_name_and_street()`, `delete_qualification()`
- Modify: `tests/test_certificate_validator.py` — street extraction tests
- Modify: `tests/test_hitobito_client.py` — disambiguation and delete tests

### 3a — Street extraction

- [ ] **Step 1: Write failing street extraction tests**

Open `tests/test_certificate_validator.py` and add a new test class at the bottom:

```python
class TestStreetExtraction:
    def test_extracts_street_from_wohnhaft_label(self, validator):
        text = "Führungszeugnis\nKeine Eintragungen\nwohnhaft: Musterstraße 12\nAusgestellt 01.06.2025"
        result = validator.validate(text, "doc.pdf")
        assert result.street == "Musterstraße 12"

    def test_extracts_street_from_anschrift_label(self, validator):
        text = "Führungszeugnis\nKeine Eintragungen\nAnschrift: Gartenweg 4a\nAusgestellt 01.06.2025"
        result = validator.validate(text, "doc.pdf")
        assert result.street == "Gartenweg 4a"

    def test_returns_none_when_no_street_found(self, validator):
        text = "Führungszeugnis\nKeine Eintragungen\nAusgestellt 01.06.2025"
        result = validator.validate(text, "doc.pdf")
        assert result.street is None
```

- [ ] **Step 2: Run to confirm failure**

```bash
uv run python -m pytest tests/test_certificate_validator.py::TestStreetExtraction -v
```

Expected: FAIL — `ValidationResult` has no `street` attribute.

- [ ] **Step 3: Add `street` to `ValidationResult` and implement extraction**

In `src/validation/certificate_validator.py`, update `ValidationResult`:

```python
@dataclass
class ValidationResult:
    is_valid: bool
    status: str
    person_name: str | None = None
    issue_date: datetime | None = None
    valid_until: datetime | None = None
    certificate_type: str = "erweitertes_fuehrungszeugnis"
    street: str | None = None
    details: dict = field(default_factory=dict)
    errors: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
```

After the `_NAME_LABEL` constant (line 64), add:

```python
# Street address extraction: label followed by "Streetname NN[a]"
_STREET_PATTERN = re.compile(
    r"(?:wohnhaft|adresse|anschrift|stra(?:ße|sse)|str\.|weg|allee|platz|gasse)"
    r"[^\n]{0,10}?([A-ZÄÖÜ][a-zäöüßA-ZÄÖÜ\-\.\ ]{3,40}?\s+\d{1,4}\s*[a-z]?)",
    re.IGNORECASE,
)
```

In the `CertificateValidator` class, after `_extract_person_name`, add:

```python
    def _extract_street_address(self, text: str) -> str | None:
        match = _STREET_PATTERN.search(text)
        if match:
            return match.group(1).strip()
        return None
```

In `validate()`, after `person_name = self._extract_person_name(text)`, add:

```python
        street = self._extract_street_address(text)
        details["street"] = street
```

In the `return ValidationResult(...)` call at the end of `validate()`, add `street=street`:

```python
        return ValidationResult(
            is_valid=is_valid,
            status=status,
            person_name=person_name,
            issue_date=issue_date,
            valid_until=valid_until,
            certificate_type=certificate_type,
            street=street,
            details=details,
            errors=errors,
            warnings=warnings,
        )
```

- [ ] **Step 4: Run street extraction tests**

```bash
uv run python -m pytest tests/test_certificate_validator.py::TestStreetExtraction -v
```

Expected: all 3 PASS.

- [ ] **Step 5: Run all validator tests**

```bash
uv run python -m pytest tests/test_certificate_validator.py -v
```

Expected: all pass.

### 3b — Hitobito client: `_normalize_street`, `find_person_by_name_and_street`, `delete_qualification`

- [ ] **Step 6: Write failing tests for new hitobito client methods**

Open `tests/test_hitobito_client.py` and add two new classes at the bottom:

```python
class TestFindPersonByNameAndStreet:
    @responses_lib.activate
    def test_returns_single_match_without_address_fetch(self, client):
        """When exactly one candidate matches the name, no address fetch is needed."""
        responses_lib.add(
            responses_lib.GET,
            f"{HITOBITO_BASE}/api/people",
            json={
                "data": [
                    {
                        "id": "10",
                        "type": "people",
                        "attributes": {
                            "first_name": "Maria",
                            "last_name": "Muster",
                            "email": "maria@example.com",
                        },
                    }
                ],
                "meta": {},
            },
            status=200,
        )
        result = client.find_person_by_name_and_street("Maria Muster", "Musterstraße 12")
        assert result is not None
        assert result.person_id == 10
        assert len(responses_lib.calls) == 1  # no extra GET /people/:id

    @responses_lib.activate
    def test_disambiguates_by_street_when_multiple_candidates(self, client):
        """When two candidates share a name, the street is used to pick the right one."""
        responses_lib.add(
            responses_lib.GET,
            f"{HITOBITO_BASE}/api/people",
            json={
                "data": [
                    {
                        "id": "1",
                        "type": "people",
                        "attributes": {"first_name": "Anna", "last_name": "Schmidt", "email": "a@b.com"},
                    },
                    {
                        "id": "2",
                        "type": "people",
                        "attributes": {"first_name": "Anna", "last_name": "Schmidt", "email": "c@d.com"},
                    },
                ],
                "meta": {},
            },
            status=200,
        )
        # GET /people/1 → wrong street
        responses_lib.add(
            responses_lib.GET,
            f"{HITOBITO_BASE}/api/people/1",
            json={
                "data": {
                    "id": "1",
                    "type": "people",
                    "attributes": {
                        "first_name": "Anna",
                        "last_name": "Schmidt",
                        "email": "a@b.com",
                        "street": "Andere Straße 99",
                    },
                    "relationships": {"roles": {"data": []}},
                },
                "included": [],
            },
            status=200,
        )
        # GET /people/2 → matching street
        responses_lib.add(
            responses_lib.GET,
            f"{HITOBITO_BASE}/api/people/2",
            json={
                "data": {
                    "id": "2",
                    "type": "people",
                    "attributes": {
                        "first_name": "Anna",
                        "last_name": "Schmidt",
                        "email": "c@d.com",
                        "street": "Musterstraße 12",
                    },
                    "relationships": {"roles": {"data": []}},
                },
                "included": [],
            },
            status=200,
        )
        result = client.find_person_by_name_and_street("Anna Schmidt", "Musterstraße 12")
        assert result is not None
        assert result.person_id == 2

    @responses_lib.activate
    def test_returns_none_when_ambiguous_and_no_street(self, client):
        """Multiple candidates + no street provided → returns None."""
        responses_lib.add(
            responses_lib.GET,
            f"{HITOBITO_BASE}/api/people",
            json={
                "data": [
                    {
                        "id": "1",
                        "type": "people",
                        "attributes": {"first_name": "Hans", "last_name": "Meier", "email": "a@b.com"},
                    },
                    {
                        "id": "2",
                        "type": "people",
                        "attributes": {"first_name": "Hans", "last_name": "Meier", "email": "c@d.com"},
                    },
                ],
                "meta": {},
            },
            status=200,
        )
        result = client.find_person_by_name_and_street("Hans Meier", None)
        assert result is None

    @responses_lib.activate
    def test_returns_none_when_no_candidate_matches_street(self, client):
        """Multiple candidates, none match the provided street → returns None."""
        responses_lib.add(
            responses_lib.GET,
            f"{HITOBITO_BASE}/api/people",
            json={
                "data": [
                    {
                        "id": "1",
                        "type": "people",
                        "attributes": {"first_name": "Klaus", "last_name": "Weber", "email": "k@w.com"},
                    },
                    {
                        "id": "2",
                        "type": "people",
                        "attributes": {"first_name": "Klaus", "last_name": "Weber", "email": "k2@w.com"},
                    },
                ],
                "meta": {},
            },
            status=200,
        )
        responses_lib.add(
            responses_lib.GET,
            f"{HITOBITO_BASE}/api/people/1",
            json={
                "data": {
                    "id": "1",
                    "type": "people",
                    "attributes": {"first_name": "Klaus", "last_name": "Weber", "email": "k@w.com", "street": "Bergstraße 1"},
                    "relationships": {"roles": {"data": []}},
                },
                "included": [],
            },
            status=200,
        )
        responses_lib.add(
            responses_lib.GET,
            f"{HITOBITO_BASE}/api/people/2",
            json={
                "data": {
                    "id": "2",
                    "type": "people",
                    "attributes": {"first_name": "Klaus", "last_name": "Weber", "email": "k2@w.com", "street": "Talweg 5"},
                    "relationships": {"roles": {"data": []}},
                },
                "included": [],
            },
            status=200,
        )
        result = client.find_person_by_name_and_street("Klaus Weber", "Kirchstraße 3")
        assert result is None


class TestDeleteQualification:
    @responses_lib.activate
    def test_deletes_qualification_by_id(self, client):
        responses_lib.add(
            responses_lib.DELETE,
            f"{HITOBITO_BASE}/api/qualifications/55",
            status=204,
        )
        client.delete_qualification(55)
        assert len(responses_lib.calls) == 1
        assert responses_lib.calls[0].request.method == "DELETE"
```

- [ ] **Step 7: Run to confirm failures**

```bash
uv run python -m pytest tests/test_hitobito_client.py::TestFindPersonByNameAndStreet tests/test_hitobito_client.py::TestDeleteQualification -v
```

Expected: FAIL — methods don't exist yet.

- [ ] **Step 8: Implement `_normalize_street`, `find_person_by_name_and_street`, `delete_qualification`**

In `src/hitobito/client.py`, add `import re` at the top (after `import logging`):

```python
import logging
import re
from dataclasses import dataclass, field
from datetime import date
```

After the `_normalize_umlauts` function (around line 33), add:

```python
def _normalize_street(s: str) -> str:
    """Normalise a German street string for comparison: umlauts, lowercase, abbreviations."""
    s = _normalize_umlauts(s).lower().strip()
    s = re.sub(r"\bstr\.?\b", "strasse", s)
    return re.sub(r"\s+", " ", s)
```

In the `HitobitoClient` class, after `find_person_by_name` and before `find_people_by_role`, add:

```python
    def find_person_by_name_and_street(
        self, name: str, street: str | None
    ) -> HitobitoAttribution | None:
        """Search for a person by full name; use street to disambiguate multiple matches.

        Returns the single unambiguous match, or None if zero or multiple candidates remain
        after disambiguation.
        """
        needle = _normalize_umlauts(name).lower()
        try:
            data = self._get("people", params={"q": name, "page[size]": 25})
            candidates = [
                item
                for item in data.get("data", [])
                if _normalize_umlauts(
                    f"{item.get('attributes', {}).get('first_name', '')} "
                    f"{item.get('attributes', {}).get('last_name', '')}".strip()
                ).lower()
                == needle
            ]
        except requests.RequestException as exc:
            logger.error("Failed to search person by name '%s': %s", name, exc)
            return None

        if len(candidates) == 1:
            attrs = candidates[0].get("attributes", {})
            return HitobitoAttribution(
                person_id=int(candidates[0]["id"]),
                first_name=attrs.get("first_name"),
                last_name=attrs.get("last_name"),
                email=attrs.get("email"),
            )

        if len(candidates) > 1 and street is None:
            ids = [c["id"] for c in candidates]
            logger.warning(
                "Ambiguous hitobito match for '%s' (IDs: %s); no street available.",
                name,
                ids,
            )
            return None

        if len(candidates) > 1 and street is not None:
            norm_street = _normalize_street(street)
            matches: list[HitobitoAttribution] = []
            for candidate in candidates:
                full = self.get_person(int(candidate["id"]))
                if full is None:
                    continue
                candidate_full = self._get(f"people/{candidate['id']}", params={"include": "roles"})
                candidate_street = (
                    candidate_full.get("data", {}).get("attributes", {}).get("street", "")
                )
                if _normalize_street(candidate_street) == norm_street:
                    matches.append(full)
            if len(matches) == 1:
                return matches[0]
            logger.warning(
                "Street disambiguation for '%s' found %d matches (street=%r).",
                name,
                len(matches),
                street,
            )
            return None

        return None
```

After `get_person_qualifications`, add:

```python
    def delete_qualification(self, qualification_id: int) -> None:
        """Delete a qualification record by ID."""
        self._delete(f"qualifications/{qualification_id}")
```

- [ ] **Step 9: Run the new hitobito tests**

```bash
uv run python -m pytest tests/test_hitobito_client.py::TestFindPersonByNameAndStreet tests/test_hitobito_client.py::TestDeleteQualification -v
```

Expected: all PASS.

- [ ] **Step 10: Run all hitobito tests**

```bash
uv run python -m pytest tests/test_hitobito_client.py -v
```

Expected: all pass.

- [ ] **Step 11: Run all unit tests**

```bash
uv run python -m pytest tests/ --ignore=tests/integration -q
```

Expected: all pass.

- [ ] **Step 12: Commit**

```bash
git add src/validation/certificate_validator.py src/hitobito/client.py \
    tests/test_certificate_validator.py tests/test_hitobito_client.py
git commit -m "feat: add street extraction to validator and address-based hitobito disambiguation"
```

---

## Task 4: Wire async threading, use `find_person_by_name_and_street`, add qualification update

**Files:**
- Modify: `src/main.py` — add `import threading`, rename `_record_efz_in_hitobito` → `_record_efz_in_hitobito_async`, restructure `_process_document` for async dispatch, use `find_person_by_name_and_street`, add qualification update logic
- Modify: `tests/test_expiry_check.py` — fix patch target from Task 2

- [ ] **Step 1: Write a failing unit test for async dispatch**

Add a new class to `tests/test_expiry_check.py`:

```python
class TestAsyncHitobitoDispatch:
    def test_hitobito_called_in_background_thread(self):
        """Valid document: EFZ method is called in a background thread, not blocking."""
        with (
            patch("src.main.get_settings") as mock_settings,
            patch("src.main.create_session_factory"),
            patch("src.main.PaperlessClient"),
            patch("src.main.CertificateValidator") as mock_validator_cls,
            patch("src.main.HitobitoClient"),
            patch("src.main.EmailNotifier"),
            patch("src.main.threading") as mock_threading,
        ):
            settings = MagicMock()
            settings.certificate_max_age_years = 5
            settings.hitobito.stamm_name = ""
            settings.hitobito.dioezese_name = ""
            settings.email.alert_recipients_list = []
            settings.hitobito.group_id = 0
            mock_settings.return_value = settings

            mock_validator = MagicMock()
            mock_result = MagicMock()
            mock_result.is_valid = True
            mock_result.person_name = "Max Mustermann"
            mock_result.valid_until = None
            mock_result.warnings = []
            mock_validator.validate.return_value = mock_result
            mock_validator_cls.return_value = mock_validator

            from src.main import ValidationWorker

            w = ValidationWorker()

            session_mock = MagicMock()
            session_mock.query.return_value.filter_by.return_value.first.return_value = None
            mock_record = MagicMock()
            mock_record.id = 42
            session_mock.flush.side_effect = lambda: None

            def capture_add(r):
                r.id = 42

            session_mock.add.side_effect = capture_add

            document = MagicMock()
            document.id = 1
            document.title = "Führungszeugnis"
            document.content = "Führungszeugnis\nKeine Eintragungen"

            mock_thread_instance = MagicMock()
            mock_threading.Thread.return_value = mock_thread_instance

            with patch.object(w, "_db_session") as mock_ctx:
                mock_ctx.return_value.__enter__ = MagicMock(return_value=session_mock)
                mock_ctx.return_value.__exit__ = MagicMock(return_value=False)
                w._process_document(document)

            mock_threading.Thread.assert_called_once()
            call_kwargs = mock_threading.Thread.call_args
            assert call_kwargs.kwargs["target"] == w._record_efz_in_hitobito_async
            assert call_kwargs.kwargs["daemon"] is True
            mock_thread_instance.start.assert_called_once()
```

- [ ] **Step 2: Run to confirm failure**

```bash
uv run python -m pytest "tests/test_expiry_check.py::TestAsyncHitobitoDispatch" -v
```

Expected: FAIL — `_record_efz_in_hitobito_async` doesn't exist yet.

- [ ] **Step 3: Rewrite `_process_document` and `_record_efz_in_hitobito_async` in `src/main.py`**

Add `import threading` to the imports block:

```python
import logging
import sys
import threading
import time
from contextlib import contextmanager
from datetime import date, timedelta
```

Replace `_process_document` with the async-dispatching version:

```python
    def _process_document(self, document) -> None:
        record_id: int | None = None
        result: ValidationResult | None = None
        with self._db_session() as session:
            existing = (
                session.query(CertificateValidation)
                .filter_by(paperless_document_id=document.id)
                .first()
            )
            if existing:
                logger.debug(
                    "Document %d already processed (valid=%s), skipping.",
                    document.id,
                    existing.is_valid,
                )
                return

            logger.info("Processing document %d: %s", document.id, document.title)

            try:
                result = self.validator.validate(
                    text=document.content,
                    document_title=document.title,
                )
            except Exception as exc:
                logger.error("Validation error for document %d: %s", document.id, exc)
                return

            cancellation_date = result.valid_until.date() if result.valid_until else None
            record = CertificateValidation(
                paperless_document_id=document.id,
                person_name=result.person_name,
                is_valid=result.is_valid,
                cancellation_date=cancellation_date,
                stamm_name=self.settings.hitobito.stamm_name or None,
                dioezese_name=self.settings.hitobito.dioezese_name or None,
            )
            session.add(record)
            session.flush()
            record_id = record.id

            self._send_alerts_for_result(record, result)

        # Session committed and closed — start background thread for hitobito
        if result is not None and result.is_valid and record_id is not None:
            threading.Thread(
                target=self._record_efz_in_hitobito_async,
                args=(record_id, result),
                daemon=True,
            ).start()
```

Replace `_record_efz_in_hitobito` with `_record_efz_in_hitobito_async`:

```python
    def _record_efz_in_hitobito_async(
        self,
        record_id: int,
        result: ValidationResult,
    ) -> None:
        try:
            with self._db_session() as session:
                record = session.get(CertificateValidation, record_id)
                if record is None:
                    logger.error("Record %d not found for EFZ qualification.", record_id)
                    return

                if not record.person_name:
                    logger.warning(
                        "No person name for document %d; skipping EFZ qualification.",
                        record.paperless_document_id,
                    )
                    return

                person = self.hitobito.find_person_by_name_and_street(
                    record.person_name, result.street
                )
                if person is None:
                    logger.warning(
                        "Person '%s' not found in hitobito; skipping EFZ qualification.",
                        record.person_name,
                    )
                    return

                kind_label = self.settings.hitobito.efz_qualification_kind_label
                kind_id = self.hitobito.get_efz_qualification_kind_id(kind_label)
                if kind_id is None:
                    logger.warning(
                        "EFZ qualification kind '%s' not found in hitobito.",
                        kind_label,
                    )
                    return

                start_at = result.issue_date.date() if result.issue_date else None
                if start_at is None:
                    logger.warning(
                        "No issue date for document %d; skipping EFZ qualification.",
                        record.paperless_document_id,
                    )
                    return

                origin = (
                    f"Automatisch validiert via Führungszeugnis-Validierungssystem "
                    f"(Dokument #{record.paperless_document_id})"
                )

                existing_quals = self.hitobito.get_person_qualifications(
                    person.person_id, kind_id
                )
                if existing_quals:
                    existing = existing_quals[0]
                    if (
                        existing.start_at == start_at
                        and existing.finish_at == record.cancellation_date
                    ):
                        logger.info(
                            "EFZ qualification for person %d already up to date; skipping.",
                            person.person_id,
                        )
                        return
                    logger.info(
                        "Updating EFZ qualification %d for person %d.",
                        existing.id,
                        person.person_id,
                    )
                    self.hitobito.delete_qualification(existing.id)

                self.hitobito.create_efz_qualification(
                    person_id=person.person_id,
                    qualification_kind_id=kind_id,
                    start_at=start_at,
                    finish_at=record.cancellation_date,
                    origin=origin,
                )
        except Exception as exc:
            logger.error(
                "Background EFZ recording failed for record %d: %s",
                record_id,
                exc,
                exc_info=True,
            )
```

- [ ] **Step 4: Fix the Task 2 test patch target**

In `tests/test_expiry_check.py`, find the `TestOrgFieldStamping` test (added in Task 2). It currently patches `_record_efz_in_hitobito`. Update it to patch `_record_efz_in_hitobito_async`:

```python
            with patch.object(w, "_db_session") as mock_ctx, \
                 patch.object(w, "_record_efz_in_hitobito_async"):
```

Also update the `_process_document` call path — since the thread dispatch now happens *after* the `with _db_session()` block, and uses `threading.Thread`, also patch `threading.Thread` to prevent actual thread spawning in the test:

```python
            with patch.object(w, "_db_session") as mock_ctx, \
                 patch("src.main.threading") as mock_threading:
                mock_ctx.return_value.__enter__ = MagicMock(return_value=session_mock)
                mock_ctx.return_value.__exit__ = MagicMock(return_value=False)
                mock_threading.Thread.return_value = MagicMock()
                w._process_document(document)
```

- [ ] **Step 5: Run all unit tests**

```bash
uv run python -m pytest tests/ --ignore=tests/integration -q
```

Expected: all tests pass.

- [ ] **Step 6: Run ruff to check formatting**

```bash
uv run ruff check src/ tests/ --select=E,W,F,I
uv run ruff format --check src/ tests/
```

Fix any issues with `uv run ruff format src/ tests/`.

- [ ] **Step 7: Commit**

```bash
git add src/main.py tests/test_expiry_check.py
git commit -m "feat: async hitobito EFZ recording with address disambiguation and qualification update"
```

---

## Self-Review

**Spec coverage check:**
- ✅ Section 1 (Stamm/Diözese config + columns + migration) → Task 1
- ✅ Section 1 (stamp on record in pipeline) → Task 2
- ✅ Section 2 (street extraction) → Task 3 step 3a
- ✅ Section 2 (`find_person_by_name_and_street`) → Task 3 step 3b
- ✅ Section 2 (`delete_qualification`, qualification update) → Task 3 step 3b + Task 4
- ✅ Section 3 (async threading, `_record_efz_in_hitobito_async`) → Task 4
- ✅ Helm values + ConfigMap → Task 1
- ✅ `tests/integration/test_migrations.py` column set update → Task 1

**Type consistency:**
- `find_person_by_name_and_street(name: str, street: str | None)` defined in Task 3, called in Task 4 with `result.street` — consistent.
- `_record_efz_in_hitobito_async(record_id: int, result: ValidationResult)` defined and called consistently in Task 4.
- `delete_qualification(qualification_id: int)` defined in Task 3, called in Task 4 — consistent.
- `result.street` added to `ValidationResult` in Task 3, referenced in Task 4 — consistent.
