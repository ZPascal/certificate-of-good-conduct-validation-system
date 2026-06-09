"""Tests for the email notification service."""

import smtplib
import unittest
from datetime import date
from unittest.mock import MagicMock, patch

from src.database.models import CertificateValidation
from src.notifications.email_notifier import EmailNotifier


def _make_notifier():
    return EmailNotifier(
        smtp_host="smtp.example.com",
        smtp_port=587,
        smtp_user="user@example.com",
        smtp_password="secret",
        use_tls=True,
        sender="noreply@example.com",
    )


def _make_record():
    return CertificateValidation(
        id=1,
        paperless_document_id=100,
        person_name="Max Mustermann",
        is_valid=False,
        cancellation_date=date(2029, 6, 1),
    )


class TestSendAlert(unittest.TestCase):
    def setUp(self):
        self.notifier = _make_notifier()
        self.record = _make_record()

    def test_returns_false_with_no_recipients(self):
        result = self.notifier.send_alert(
            recipients=[],
            status="invalid",
            validation=self.record,
        )
        self.assertFalse(result)

    @patch("smtplib.SMTP")
    def test_sends_email_for_invalid_certificate(self, mock_smtp_class):
        mock_smtp = MagicMock()
        mock_smtp_class.return_value.__enter__ = MagicMock(return_value=mock_smtp)
        mock_smtp_class.return_value.__exit__ = MagicMock(return_value=False)

        result = self.notifier.send_alert(
            recipients=["admin@example.com"],
            status="invalid",
            validation=self.record,
        )
        self.assertTrue(result)
        mock_smtp.send_message.assert_called_once()

    @patch("smtplib.SMTP")
    def test_sends_email_for_expired_certificate(self, mock_smtp_class):
        mock_smtp = MagicMock()
        mock_smtp_class.return_value.__enter__ = MagicMock(return_value=mock_smtp)
        mock_smtp_class.return_value.__exit__ = MagicMock(return_value=False)

        result = self.notifier.send_alert(
            recipients=["admin@example.com"],
            status="expired",
            validation=self.record,
        )
        self.assertTrue(result)

    @patch("smtplib.SMTP")
    def test_returns_false_on_smtp_error(self, mock_smtp_class):
        mock_smtp_class.side_effect = smtplib.SMTPException("Connection refused")

        result = self.notifier.send_alert(
            recipients=["admin@example.com"],
            status="invalid",
            validation=self.record,
        )
        self.assertFalse(result)


class TestComposeAlert(unittest.TestCase):
    def setUp(self):
        self.notifier = _make_notifier()
        self.record = _make_record()

    def test_invalid_subject_contains_person_name(self):
        subject, body = self.notifier._compose_alert("invalid", self.record)
        self.assertIn("Max Mustermann", subject)
        self.assertIn("ngültig", subject)

    def test_expired_subject_contains_person_name(self):
        subject, body = self.notifier._compose_alert("expired", self.record)
        self.assertIn("Max Mustermann", subject)

    def test_expiring_soon_subject(self):
        subject, body = self.notifier._compose_alert("expiring_soon", self.record)
        self.assertIn("Max Mustermann", subject)

    def test_body_mentions_sgb_viii(self):
        _, body = self.notifier._compose_alert("invalid", self.record)
        self.assertIn("§72a SGB VIII", body)

    def test_processing_error_subject(self):
        subject, _ = self.notifier._compose_alert("error", self.record)
        self.assertIn("ehler", subject)

    def test_format_date_returns_german_format(self):
        d = date(2024, 6, 15)
        self.assertEqual(self.notifier._format_date(d), "15.06.2024")

    def test_format_date_handles_none(self):
        self.assertEqual(self.notifier._format_date(None), "unbekannt")

    def test_expired_body_contains_cancellation_date(self):
        _, body = self.notifier._compose_alert("expired", self.record)
        self.assertIn("01.06.2029", body)
