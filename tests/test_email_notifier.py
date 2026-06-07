"""Tests for the email notification service."""

import smtplib
from datetime import date
from unittest.mock import MagicMock, patch

import pytest

from src.database.models import CertificateValidation
from src.notifications.email_notifier import EmailNotifier


@pytest.fixture
def notifier():
    return EmailNotifier(
        smtp_host="smtp.example.com",
        smtp_port=587,
        smtp_user="user@example.com",
        smtp_password="secret",
        use_tls=True,
        sender="noreply@example.com",
    )


@pytest.fixture
def validation_record():
    return CertificateValidation(
        id=1,
        paperless_document_id=100,
        person_name="Max Mustermann",
        is_valid=False,
        cancellation_date=date(2029, 6, 1),
    )


class TestSendAlert:
    def test_returns_false_with_no_recipients(self, notifier, validation_record):
        result = notifier.send_alert(
            recipients=[],
            status="invalid",
            validation=validation_record,
        )
        assert result is False

    @patch("smtplib.SMTP")
    def test_sends_email_for_invalid_certificate(
        self, mock_smtp_class, notifier, validation_record
    ):
        mock_smtp = MagicMock()
        mock_smtp_class.return_value.__enter__ = MagicMock(return_value=mock_smtp)
        mock_smtp_class.return_value.__exit__ = MagicMock(return_value=False)

        result = notifier.send_alert(
            recipients=["admin@example.com"],
            status="invalid",
            validation=validation_record,
        )
        assert result is True
        mock_smtp.send_message.assert_called_once()

    @patch("smtplib.SMTP")
    def test_sends_email_for_expired_certificate(
        self, mock_smtp_class, notifier, validation_record
    ):
        mock_smtp = MagicMock()
        mock_smtp_class.return_value.__enter__ = MagicMock(return_value=mock_smtp)
        mock_smtp_class.return_value.__exit__ = MagicMock(return_value=False)

        result = notifier.send_alert(
            recipients=["admin@example.com"],
            status="expired",
            validation=validation_record,
        )
        assert result is True

    @patch("smtplib.SMTP")
    def test_returns_false_on_smtp_error(self, mock_smtp_class, notifier, validation_record):
        mock_smtp_class.side_effect = smtplib.SMTPException("Connection refused")

        result = notifier.send_alert(
            recipients=["admin@example.com"],
            status="invalid",
            validation=validation_record,
        )
        assert result is False


class TestComposeAlert:
    def test_invalid_subject_contains_person_name(self, notifier, validation_record):
        subject, body = notifier._compose_alert("invalid", validation_record)
        assert "Max Mustermann" in subject
        assert "Ungültig" in subject or "ungültig" in subject.lower()

    def test_expired_subject_contains_person_name(self, notifier, validation_record):
        subject, body = notifier._compose_alert("expired", validation_record)
        assert "Max Mustermann" in subject

    def test_expiring_soon_subject(self, notifier, validation_record):
        subject, body = notifier._compose_alert("expiring_soon", validation_record)
        assert "Max Mustermann" in subject

    def test_body_mentions_sgb_viii(self, notifier, validation_record):
        _, body = notifier._compose_alert("invalid", validation_record)
        assert "§72a SGB VIII" in body

    def test_processing_error_subject(self, notifier, validation_record):
        subject, _ = notifier._compose_alert("error", validation_record)
        assert "Fehler" in subject or "fehler" in subject.lower()

    def test_format_date_returns_german_format(self, notifier):
        d = date(2024, 6, 15)
        assert notifier._format_date(d) == "15.06.2024"

    def test_format_date_handles_none(self, notifier):
        assert notifier._format_date(None) == "unbekannt"

    def test_expired_body_contains_cancellation_date(self, notifier, validation_record):
        _, body = notifier._compose_alert("expired", validation_record)
        assert "01.06.2029" in body
