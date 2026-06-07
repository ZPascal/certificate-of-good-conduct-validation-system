"""Verify alembic migrations produce the expected schema."""

import os
import subprocess
from pathlib import Path
from urllib.parse import urlparse

import pytest
from sqlalchemy import create_engine, inspect

_REPO_ROOT = Path(__file__).parents[2]


def _db_env_from_url(db_url: str) -> dict:
    """Parse a psycopg2 URL into DB_* env vars for the alembic subprocess."""
    parsed = urlparse(db_url)
    return {
        **os.environ,
        "DB_HOST": parsed.hostname or "localhost",
        "DB_PORT": str(parsed.port or 5432),
        "DB_NAME": parsed.path.lstrip("/"),
        "DB_USER": parsed.username or "postgres",
        "DB_PASSWORD": parsed.password or "",
    }


@pytest.fixture(scope="module")
def migrated_engine():
    """Run alembic upgrade head against the test DB, yield the engine."""
    db_url = os.environ.get(
        "TEST_DB_URL",
        "postgresql+psycopg2://postgres:testpw@localhost:15432/test_certificate_validation",
    )
    engine = create_engine(db_url)
    from src.database.models import Base

    env = _db_env_from_url(db_url)
    subprocess.run(
        ["python", "-m", "alembic", "downgrade", "base"],
        capture_output=True,
        text=True,
        env=env,
        cwd=_REPO_ROOT,
    )
    Base.metadata.drop_all(engine)
    result = subprocess.run(
        ["python", "-m", "alembic", "upgrade", "head"],
        capture_output=True,
        text=True,
        env=env,
        cwd=_REPO_ROOT,
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


def test_paperless_document_id_unique_index(migrated_engine):
    inspector = inspect(migrated_engine)
    indexes = inspector.get_indexes("certificate_validations")
    unique_indexes = [i for i in indexes if i["unique"]]
    indexed_cols = [col for i in unique_indexes for col in i["column_names"]]
    assert "paperless_document_id" in indexed_cols
