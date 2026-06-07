# Stamm/Diözese Grouping, Address-Based Hitobito Matching, and Async Hitobito Calls

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Associate certificates with an organisational unit (Stamm/Diözese), improve hitobito person disambiguation using street address extracted from OCR text, and move the hitobito API call off the main processing thread.

**Architecture:** Three independent, additive changes to the existing pipeline. The DB write path is unchanged. A new street-extraction step is added to the validator output. The hitobito call is moved to a daemon thread that receives only the record ID and reloads the ORM object in its own session.

**Tech Stack:** Python 3.12, SQLAlchemy 2.0, Alembic, pydantic-settings v2, `threading.Thread` (stdlib), `re` (stdlib).

---

## Section 1 — Organisational fields (Stamm/Diözese)

### Config

`HitobitoSettings` in `src/config.py` gains two optional string fields:

```python
stamm_name: str = ""
dioezese_name: str = ""
```

Helm `values.yaml` gains two keys under `config.hitobito`:

```yaml
config:
  hitobito:
    stammName: ""
    dioezeseName: ""
```

`helm/fuehrungszeugnis/templates/configmap.yaml` maps them to env vars:

```
HITOBITO__STAMM_NAME
HITOBITO__DIOEZESE_NAME
```

### Database

`CertificateValidation` model gets two new nullable string columns:

```python
stamm_name: Mapped[str | None] = mapped_column(String(255))
dioezese_name: Mapped[str | None] = mapped_column(String(255))
```

Migration `003_add_stamm_dioezese.py` adds both columns with `op.add_column`.

### Pipeline

In `_process_document` (src/main.py), the record creation block stamps these from settings:

```python
record = CertificateValidation(
    paperless_document_id=document.id,
    person_name=result.person_name,
    is_valid=result.is_valid,
    cancellation_date=cancellation_date,
    stamm_name=self.settings.hitobito.stamm_name or None,
    dioezese_name=self.settings.hitobito.dioezese_name or None,
)
```

Empty string config values are normalised to `None`.

---

## Section 2 — Street address extraction and hitobito disambiguation

### Street extraction in CertificateValidator

`ValidationResult` gains a new field:

```python
street: str | None = None
```

`CertificateValidator` gains `_extract_street_address(text: str) -> str | None`:

```python
# Matches German address lines: "Musterstraße 12" / "Muster-Str. 4a" / "Am Hang 3"
_STREET_PATTERN = re.compile(
    r"(?:wohnhaft|adresse|anschrift|straße|strasse|str\.|weg|allee|platz|gasse)"
    r"[^\n]{0,10}?([A-ZÄÖÜ][a-zäöüßA-ZÄÖÜ\-\.\ ]{3,40}?\s+\d{1,4}\s*[a-z]?)",
    re.IGNORECASE,
)
```

The method searches the OCR text and returns the first match group, stripped. The result is assigned to `result.street` in `validate()`.

### New HitobitoClient method

`find_person_by_name_and_street(name: str, street: str | None) -> HitobitoAttribution | None`:

1. Call `GET /api/people?q=<name>&page[size]=25` — same as `find_person_by_name`.
2. Collect all candidates whose normalised full name matches `name` (existing umlaut-aware logic).
3. **If exactly one candidate** → return it immediately (no address fetch needed).
4. **If multiple candidates AND `street` is not None** → for each candidate, call `GET /api/people/:id` and compare `attrs.get("street", "")` normalised lowercase against the normalised extracted street. Return the first unambiguous match, or `None` if zero or multiple candidates match.
5. **If multiple candidates AND `street` is None** → log a warning listing the ambiguous person IDs, return `None`.

Street normalisation helper (private, reused from `_normalize_umlauts`):

```python
def _normalize_street(s: str) -> str:
    s = _normalize_umlauts(s).lower().strip()
    # Normalise common abbreviations
    s = re.sub(r"\bstr\b\.?", "strasse", s)
    return re.sub(r"\s+", " ", s)
```

### Qualification update

`HitobitoClient` gains:

```python
def delete_qualification(self, qualification_id: int) -> None:
    self._delete(f"qualifications/{qualification_id}")
```

`_record_efz_in_hitobito` updated flow (after resolving `person` and `kind_id`):

1. Call `get_person_qualifications(person.person_id, kind_id)`.
2. If a matching qualification exists:
   - Compare `existing.start_at == start_at` and `existing.finish_at == record.cancellation_date`.
   - If dates match → log and skip (idempotent, no API call).
   - If dates differ → call `delete_qualification(existing.id)`, then `create_efz_qualification(...)`.
3. If no existing qualification → call `create_efz_qualification(...)` as today.

---

## Section 3 — Async hitobito call

### Threading model

`_process_document` calls `session.flush()` inside the `with self._db_session()` block to assign the record `id`. The `with` block then exits, committing the row. The thread is started **after** the `with` block closes — never inside it:

```python
def _process_document(self, document) -> None:
    record_id: int | None = None
    is_valid: bool = False
    with self._db_session() as session:
        # ... existing deduplication, validation, record creation, flush ...
        record_id = record.id
        is_valid = result.is_valid

    # Session is committed and closed before the thread starts
    if is_valid and record_id is not None:
        threading.Thread(
            target=self._record_efz_in_hitobito_async,
            args=(record_id, result),
            daemon=True,
        ).start()
```

`_record_efz_in_hitobito` is renamed to `_record_efz_in_hitobito_async` and its signature changes from `(record: CertificateValidation, result: ValidationResult)` to `(record_id: int, result: ValidationResult)`. It opens its own session at the start:

```python
def _record_efz_in_hitobito_async(self, record_id: int, result: ValidationResult) -> None:
    with self._db_session() as session:
        record = session.get(CertificateValidation, record_id)
        if record is None:
            logger.error("Record %d not found for EFZ qualification.", record_id)
            return
        # ... existing hitobito logic using `record` and `result` ...
```

Errors inside the thread are caught at the top level of the method and logged — they never propagate to the calling thread.

### Deduplication safety

The main thread commits the `CertificateValidation` row before the background thread starts. If `run_once()` fires again before the thread completes, the deduplication check (`filter_by(paperless_document_id=...)`) finds the committed row and skips the document — no duplicate DB row is possible. A duplicate hitobito qualification is possible only if the pod restarts mid-thread; the qualification update logic (Section 2) handles that by detecting an existing qualification and skipping if dates match.

---

## File Map

| File | Change |
|------|--------|
| `src/config.py` | Add `stamm_name`, `dioezese_name` to `HitobitoSettings` |
| `src/database/models.py` | Add `stamm_name`, `dioezese_name` columns |
| `migrations/versions/003_add_stamm_dioezese.py` | New migration |
| `src/validation/certificate_validator.py` | Add `street` to `ValidationResult`; add `_extract_street_address()` |
| `src/hitobito/client.py` | Add `find_person_by_name_and_street()`, `delete_qualification()`, `_normalize_street()` |
| `src/main.py` | Use `find_person_by_name_and_street()`; rename to `_record_efz_in_hitobito_async`; thread dispatch; stamp org fields on record |
| `helm/fuehrungszeugnis/values.yaml` | Add `stammName`, `dioezeseName` under `config.hitobito` |
| `helm/fuehrungszeugnis/templates/configmap.yaml` | Add `HITOBITO__STAMM_NAME`, `HITOBITO__DIOEZESE_NAME` |
| `tests/test_hitobito_client.py` | Tests for `find_person_by_name_and_street`, `delete_qualification` |
| `tests/test_certificate_validator.py` | Tests for `_extract_street_address`, `street` in `ValidationResult` |
| `tests/test_expiry_check.py` | No change needed |
| `tests/integration/test_migrations.py` | Add `stamm_name`, `dioezese_name` to expected column set |
