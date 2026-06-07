# tests/integration/test_expiry_alerts.py
"""Integration tests for the 4-month expiry alert job."""

from datetime import date, timedelta
from email.header import decode_header

import requests as http_requests

from src.database.models import CertificateValidation
from tests.integration.conftest import TEST_MAILHOG_URL


def _mailhog_messages() -> list[dict]:
    resp = http_requests.get(f"{TEST_MAILHOG_URL}/api/v2/messages")
    resp.raise_for_status()
    return resp.json().get("items", [])


def _decode_subject(raw_subject: str) -> str:
    """Decode a MIME-encoded email subject (e.g. quoted-printable UTF-8) to plain text."""
    parts = decode_header(raw_subject)
    decoded = []
    for part, charset in parts:
        if isinstance(part, bytes):
            decoded.append(part.decode(charset or "ascii"))
        else:
            decoded.append(part)
    return "".join(decoded)


class TestExpiryAlerts:
    def test_sends_alert_for_certificate_expiring_within_window(self, worker, db_session):
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
        subject = _decode_subject(messages[0]["Content"]["Headers"]["Subject"][0])
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
