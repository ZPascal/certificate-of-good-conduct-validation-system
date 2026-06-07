"""Main worker that orchestrates the certificate validation pipeline.

Workflow:
1. Poll Paperless-ngx for documents tagged as Führungszeugnis
2. Skip documents already processed (based on paperless_document_id)
3. Extract OCR text and validate the certificate
4. Persist the validation result (is_valid, person_name, cancellation_date) to the database
5. If the certificate is valid and a hitobito person ID can be resolved,
   record the EFZ as a qualification via POST /api/qualifications
6. Send email alerts for any validation issues
"""

import logging
import sys
import threading
import time
from contextlib import contextmanager
from datetime import date, timedelta

import schedule

from src.config import get_settings
from src.database.models import CertificateValidation
from src.database.session import create_session_factory
from src.hitobito.client import HitobitoClient
from src.notifications.email_notifier import EmailNotifier
from src.paperless.client import PaperlessClient
from src.validation.certificate_validator import CertificateValidator, ValidationResult

logger = logging.getLogger(__name__)


def setup_logging(level: str) -> None:
    logging.basicConfig(
        level=getattr(logging, level.upper(), logging.INFO),
        format="%(asctime)s %(levelname)-8s %(name)s %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
        stream=sys.stdout,
    )


class ValidationWorker:
    """Orchestrates the full validation pipeline."""

    def __init__(self) -> None:
        self.settings = get_settings()
        self.session_factory = create_session_factory(self.settings)
        self.paperless = PaperlessClient(
            base_url=self.settings.paperless.base_url,
            token=self.settings.paperless.token,
            tag_name=self.settings.paperless.tag_name,
        )
        self.validator = CertificateValidator(max_age_years=self.settings.certificate_max_age_years)
        self.hitobito = HitobitoClient(
            base_url=self.settings.hitobito.base_url,
            token=self.settings.hitobito.token,
        )
        self.notifier = EmailNotifier(
            smtp_host=self.settings.email.smtp_host,
            smtp_port=self.settings.email.smtp_port,
            smtp_user=self.settings.email.smtp_user,
            smtp_password=self.settings.email.smtp_password,
            use_tls=self.settings.email.smtp_use_tls,
            sender=self.settings.email.sender,
        )

    @contextmanager
    def _db_session(self):
        session = self.session_factory()
        try:
            yield session
            session.commit()
        except Exception:
            session.rollback()
            raise
        finally:
            session.close()

    def run_once(self) -> None:
        """Execute one full validation pass."""
        logger.info("Starting certificate validation pass.")
        try:
            for document in self.paperless.iter_fuehrungszeugnis_documents():
                self._process_document(document)
        except Exception as exc:
            logger.error("Unexpected error during validation pass: %s", exc, exc_info=True)
        logger.info("Validation pass complete.")

    def _process_document(self, document) -> None:
        result: ValidationResult | None = None
        record_id: int | None = None

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

        if result is not None and result.is_valid and record_id is not None:
            threading.Thread(
                target=self._record_efz_in_hitobito_async,
                args=(record_id, result),
                daemon=True,
            ).start()

    def run_expiry_check(self) -> None:
        """Send alerts for certificates expiring within the configured window."""
        alert_days = self.settings.expiry_alert_days
        threshold = date.today() + timedelta(days=alert_days)
        logger.info("Checking for certificates expiring before %s.", threshold)
        recipients = self._get_alert_recipients()
        if not recipients:
            logger.warning("No alert recipients configured, skipping expiry check.")
            return
        alert_cooldown = date.today() - timedelta(days=30)
        with self._db_session() as session:
            expiring = (
                session.query(CertificateValidation)
                .filter(
                    CertificateValidation.is_valid.is_(True),
                    CertificateValidation.cancellation_date.isnot(None),
                    CertificateValidation.cancellation_date <= threshold,
                    CertificateValidation.cancellation_date >= date.today(),
                )
                .all()
            )
            for record in expiring:
                if (
                    record.last_expiry_alert_at is not None
                    and record.last_expiry_alert_at >= alert_cooldown
                ):
                    logger.debug(
                        "Skipping expiry alert for document %d (last sent %s).",
                        record.paperless_document_id,
                        record.last_expiry_alert_at,
                    )
                    continue
                self.notifier.send_alert(
                    recipients=recipients,
                    status="expiring_soon",
                    validation=record,
                )
                record.last_expiry_alert_at = date.today()
                logger.info(
                    "Expiry alert sent for document %d (expires %s).",
                    record.paperless_document_id,
                    record.cancellation_date,
                )

    def _record_efz_in_hitobito_async(self, record_id: int, result: ValidationResult) -> None:
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

                existing_quals = self.hitobito.get_person_qualifications(person.person_id, kind_id)
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

    def _send_alerts_for_result(
        self,
        record: CertificateValidation,
        result: ValidationResult,
    ) -> None:
        if result.is_valid and not result.warnings:
            return

        recipients = self._get_alert_recipients()
        if not recipients:
            return

        if not result.is_valid:
            self.notifier.send_alert(
                recipients=recipients,
                status=result.status,
                validation=record,
            )
        elif result.warnings:
            expiring = [w for w in result.warnings if "expire" in w.lower()]
            if expiring:
                self.notifier.send_alert(
                    recipients=recipients,
                    status="expiring_soon",
                    validation=record,
                )

    def _get_alert_recipients(self) -> list[str]:
        recipients = self.settings.email.alert_recipients_list
        if not recipients and self.settings.hitobito.group_id:
            try:
                people = self.hitobito.find_people_by_role(
                    group_id=self.settings.hitobito.group_id,
                    role_type_contains="Führungszeugnis",
                )
                recipients = [p.email for p in people if p.email]
            except Exception as exc:
                logger.warning("Could not fetch alert recipients from hitobito: %s", exc)
        return recipients


def main() -> None:
    settings = get_settings()
    setup_logging(settings.log_level)
    logger.info("Certificate of Good Conduct Validation System starting up.")

    worker = ValidationWorker()
    worker.run_once()

    interval = settings.paperless.poll_interval_seconds
    logger.info("Scheduling validation every %d seconds.", interval)
    schedule.every(interval).seconds.do(worker.run_once)

    logger.info(
        "Scheduling daily expiry check (alert window: %d days).",
        settings.expiry_alert_days,
    )
    schedule.every().day.at("08:00").do(worker.run_expiry_check)

    while True:
        schedule.run_pending()
        time.sleep(1)


if __name__ == "__main__":
    main()
