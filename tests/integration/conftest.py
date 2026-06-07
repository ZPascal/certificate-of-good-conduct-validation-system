# tests/integration/conftest.py
"""Fixtures for integration tests.

Required services (started by docker-compose.integration.yml):
  postgres  - localhost:15432 (POSTGRES_DB=test_certificate_validation, user=postgres, pw=testpw)
  wiremock  - localhost:18080
  mailhog   - localhost:11025 (SMTP), localhost:18025 (HTTP API)
"""

import os
from unittest.mock import patch

import pytest
import requests
from sqlalchemy import create_engine
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
    Session = sessionmaker(bind=connection, autoflush=False, autocommit=False)
    session = Session()
    yield session
    session.close()
    transaction.rollback()
    connection.close()


@pytest.fixture(autouse=True)
def wiremock_reset():
    """Reset WireMock request journal between tests (stubs in mappings/ are permanent)."""
    requests.delete(f"{TEST_WIREMOCK_URL}/__admin/requests")
    yield
    requests.delete(f"{TEST_WIREMOCK_URL}/__admin/requests")


@pytest.fixture(autouse=True)
def mailhog_reset():
    """Delete all MailHog messages between tests."""
    requests.delete(f"{TEST_MAILHOG_URL}/api/v1/messages")
    yield


@pytest.fixture
def worker(db_session):
    """A ValidationWorker wired to integration test services, sharing db_session's connection."""
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
    # Override session_factory to share the same connection as db_session
    # so worker's DB writes are visible in db_session and rolled back together
    w.session_factory = sessionmaker(bind=db_session.bind, autoflush=False, autocommit=False)
    return w


@pytest.fixture
def wiremock_invalid():
    """Override the documents stub to return an invalid certificate document."""
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
    resp = requests.post(f"{TEST_WIREMOCK_URL}/__admin/mappings", json=mapping)
    stub_id = resp.json().get("id")
    yield
    if stub_id:
        requests.delete(f"{TEST_WIREMOCK_URL}/__admin/mappings/{stub_id}")
