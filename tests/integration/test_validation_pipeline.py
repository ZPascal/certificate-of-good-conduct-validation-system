# tests/integration/test_validation_pipeline.py
"""End-to-end pipeline tests: run_once() → assert DB state."""

import unittest

import pytest

from src.database.models import CertificateValidation


class TestValidDocumentPipeline(unittest.TestCase):
    @pytest.fixture(autouse=True)
    def inject_fixtures(self, worker, db_session):
        self.worker = worker
        self.db_session = db_session

    def test_valid_certificate_creates_valid_row(self):
        """A clean cert (keine Eintragungen, recent date) → is_valid=True."""
        self.worker.run_once()
        self.db_session.expire_all()

        row = (
            self.db_session.query(CertificateValidation)
            .filter_by(paperless_document_id=1)
            .one_or_none()
        )

        self.assertIsNotNone(row)
        self.assertTrue(row.is_valid)
        self.assertEqual(row.person_name, "Max Mustermann")
        self.assertIsNotNone(row.cancellation_date)

    def test_valid_certificate_is_idempotent(self):
        """Running run_once() twice for the same document must not create a duplicate row."""
        self.worker.run_once()
        self.worker.run_once()
        self.db_session.expire_all()

        count = (
            self.db_session.query(CertificateValidation).filter_by(paperless_document_id=1).count()
        )
        self.assertEqual(count, 1)


class TestInvalidDocumentPipeline(unittest.TestCase):
    @pytest.fixture(autouse=True)
    def inject_fixtures(self, worker, db_session, wiremock_invalid):
        self.worker = worker
        self.db_session = db_session

    def test_invalid_certificate_creates_invalid_row(self):
        """A cert with criminal entries → is_valid=False."""
        self.worker.run_once()
        self.db_session.expire_all()

        row = (
            self.db_session.query(CertificateValidation)
            .filter_by(paperless_document_id=2)
            .one_or_none()
        )

        self.assertIsNotNone(row)
        self.assertFalse(row.is_valid)
        self.assertEqual(row.person_name, "Erika Musterfrau")
