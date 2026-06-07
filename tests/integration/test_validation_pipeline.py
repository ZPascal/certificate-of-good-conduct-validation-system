# tests/integration/test_validation_pipeline.py
"""End-to-end pipeline tests: run_once() → assert DB state."""

from src.database.models import CertificateValidation


class TestValidDocumentPipeline:
    def test_valid_certificate_creates_valid_row(self, worker, db_session):
        """A clean cert (keine Eintragungen, recent date) → is_valid=True."""
        worker.run_once()
        db_session.expire_all()

        row = (
            db_session.query(CertificateValidation).filter_by(paperless_document_id=1).one_or_none()
        )

        assert row is not None
        assert row.is_valid is True
        assert row.person_name == "Max Mustermann"
        assert row.cancellation_date is not None

    def test_valid_certificate_is_idempotent(self, worker, db_session):
        """Running run_once() twice for the same document must not create a duplicate row."""
        worker.run_once()
        worker.run_once()
        db_session.expire_all()

        count = db_session.query(CertificateValidation).filter_by(paperless_document_id=1).count()
        assert count == 1


class TestInvalidDocumentPipeline:
    def test_invalid_certificate_creates_invalid_row(self, worker, db_session, wiremock_invalid):
        """A cert with criminal entries → is_valid=False."""
        worker.run_once()
        db_session.expire_all()

        row = (
            db_session.query(CertificateValidation).filter_by(paperless_document_id=2).one_or_none()
        )

        assert row is not None
        assert row.is_valid is False
        assert row.person_name == "Erika Musterfrau"
