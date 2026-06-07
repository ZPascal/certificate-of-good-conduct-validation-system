"""Tests for the expiry alert logic in ValidationWorker."""

from datetime import date, timedelta
from unittest.mock import MagicMock, patch

import pytest

from src.database.models import CertificateValidation


@pytest.fixture
def worker():
    with (
        patch("src.main.get_settings") as mock_settings,
        patch("src.main.create_session_factory"),
        patch("src.main.PaperlessClient"),
        patch("src.main.CertificateValidator"),
        patch("src.main.HitobitoClient"),
        patch("src.main.EmailNotifier"),
    ):
        settings = MagicMock()
        settings.expiry_alert_days = 120
        settings.email.alert_recipients_list = ["admin@example.com"]
        settings.hitobito.group_id = 0
        mock_settings.return_value = settings

        from src.main import ValidationWorker

        w = ValidationWorker()
        w.notifier = MagicMock()
        return w


def _make_record(paperless_id: int, cancellation_date: date) -> CertificateValidation:
    r = CertificateValidation(
        paperless_document_id=paperless_id,
        person_name="Test Person",
        is_valid=True,
        cancellation_date=cancellation_date,
    )
    r.id = paperless_id
    return r


class TestRunExpiryCheck:
    def test_sends_alert_for_certificate_expiring_within_window(self, worker):
        soon = date.today() + timedelta(days=60)
        record = _make_record(1, soon)

        session_mock = MagicMock()
        session_mock.query.return_value.filter.return_value.all.return_value = [record]

        with patch.object(worker, "_db_session") as mock_ctx:
            mock_ctx.return_value.__enter__ = MagicMock(return_value=session_mock)
            mock_ctx.return_value.__exit__ = MagicMock(return_value=False)
            worker.run_expiry_check()

        worker.notifier.send_alert.assert_called_once_with(
            recipients=["admin@example.com"],
            status="expiring_soon",
            validation=record,
        )

    def test_no_alert_when_no_expiring_certificates(self, worker):
        session_mock = MagicMock()
        session_mock.query.return_value.filter.return_value.all.return_value = []

        with patch.object(worker, "_db_session") as mock_ctx:
            mock_ctx.return_value.__enter__ = MagicMock(return_value=session_mock)
            mock_ctx.return_value.__exit__ = MagicMock(return_value=False)
            worker.run_expiry_check()

        worker.notifier.send_alert.assert_not_called()

    def test_no_alert_when_no_recipients(self, worker):
        worker.settings.email.alert_recipients_list = []
        worker.settings.hitobito.group_id = 0

        with patch.object(worker, "_db_session") as mock_ctx:
            worker.run_expiry_check()
            mock_ctx.assert_not_called()

        worker.notifier.send_alert.assert_not_called()

    def test_no_alert_when_last_alert_within_cooldown(self, worker):
        soon = date.today() + timedelta(days=60)
        record = _make_record(1, soon)
        record.last_expiry_alert_at = date.today() - timedelta(days=5)

        session_mock = MagicMock()
        session_mock.query.return_value.filter.return_value.all.return_value = [record]

        with patch.object(worker, "_db_session") as mock_ctx:
            mock_ctx.return_value.__enter__ = MagicMock(return_value=session_mock)
            mock_ctx.return_value.__exit__ = MagicMock(return_value=False)
            worker.run_expiry_check()

        worker.notifier.send_alert.assert_not_called()

    def test_sends_alert_when_last_alert_outside_cooldown(self, worker):
        soon = date.today() + timedelta(days=60)
        record = _make_record(1, soon)
        record.last_expiry_alert_at = date.today() - timedelta(days=31)

        session_mock = MagicMock()
        session_mock.query.return_value.filter.return_value.all.return_value = [record]

        with patch.object(worker, "_db_session") as mock_ctx:
            mock_ctx.return_value.__enter__ = MagicMock(return_value=session_mock)
            mock_ctx.return_value.__exit__ = MagicMock(return_value=False)
            worker.run_expiry_check()

        worker.notifier.send_alert.assert_called_once()
        assert record.last_expiry_alert_at == date.today()

    def test_sends_alert_for_each_expiring_certificate(self, worker):
        records = [
            _make_record(1, date.today() + timedelta(days=30)),
            _make_record(2, date.today() + timedelta(days=90)),
        ]
        session_mock = MagicMock()
        session_mock.query.return_value.filter.return_value.all.return_value = records

        with patch.object(worker, "_db_session") as mock_ctx:
            mock_ctx.return_value.__enter__ = MagicMock(return_value=session_mock)
            mock_ctx.return_value.__exit__ = MagicMock(return_value=False)
            worker.run_expiry_check()

        assert worker.notifier.send_alert.call_count == 2


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

            with (
                patch.object(w, "_db_session") as mock_ctx,
                patch("src.main.threading") as mock_threading,
            ):
                mock_threading.Thread.return_value = MagicMock()
                mock_ctx.return_value.__enter__ = MagicMock(return_value=session_mock)
                mock_ctx.return_value.__exit__ = MagicMock(return_value=False)
                w._process_document(document)

            assert len(saved_records) == 1
            assert saved_records[0].stamm_name == "Stamm St. Georg"
            assert saved_records[0].dioezese_name == "Diözese Freiburg"
