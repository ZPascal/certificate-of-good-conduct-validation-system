"""Email notification service for certificate validation alerts."""

import logging
import smtplib
from datetime import date
from email.message import EmailMessage

from src.database.models import CertificateValidation

logger = logging.getLogger(__name__)


class EmailNotifier:
    """Sends email alerts for certificate validation issues via SMTP."""

    def __init__(
        self,
        smtp_host: str,
        smtp_port: int,
        smtp_user: str,
        smtp_password: str,
        use_tls: bool,
        sender: str,
    ) -> None:
        self.smtp_host = smtp_host
        self.smtp_port = smtp_port
        self.smtp_user = smtp_user
        self.smtp_password = smtp_password
        self.use_tls = use_tls
        self.sender = sender

    def _build_message(
        self,
        recipients: list[str],
        subject: str,
        body: str,
    ) -> EmailMessage:
        msg = EmailMessage()
        msg["From"] = self.sender
        msg["To"] = ", ".join(recipients)
        msg["Subject"] = subject
        msg.set_content(body, charset="utf-8")
        return msg

    def _send(self, message: EmailMessage) -> bool:
        try:
            if self.use_tls:
                with smtplib.SMTP(self.smtp_host, self.smtp_port, timeout=30) as smtp:
                    smtp.ehlo()
                    smtp.starttls()
                    smtp.ehlo()
                    if self.smtp_user:
                        smtp.login(self.smtp_user, self.smtp_password)
                    smtp.send_message(message)
            else:
                with smtplib.SMTP(self.smtp_host, self.smtp_port, timeout=30) as smtp:
                    if self.smtp_user:
                        smtp.login(self.smtp_user, self.smtp_password)
                    smtp.send_message(message)
            return True
        except smtplib.SMTPException as exc:
            logger.error("Failed to send email: %s", exc)
            return False

    def send_alert(
        self,
        recipients: list[str],
        status: str,
        validation: CertificateValidation,
    ) -> bool:
        """Build and send an alert email for a validation issue.

        Returns True if the email was sent successfully.
        """
        if not recipients:
            logger.warning("No recipients configured, skipping alert email.")
            return False

        subject, body = self._compose_alert(status, validation)
        message = self._build_message(recipients, subject, body)
        sent = self._send(message)
        if sent:
            logger.info("Alert email '%s' sent to %s.", status, recipients)
        return sent

    def _compose_alert(
        self,
        status: str,
        validation: CertificateValidation,
    ) -> tuple[str, str]:
        person = validation.person_name or "Unbekannte Person"
        cancellation = self._format_date(validation.cancellation_date)
        footer = (
            "\nMit freundlichen Grüßen\n"
            "Führungszeugnis-Validierungssystem\n\n"
            "(Automatisch generierte Nachricht – bitte nicht antworten)\n"
        )

        if status == "invalid":
            subject = f"[Führungszeugnis] Ungültiges Führungszeugnis – {person}"
            body = (
                f"Sehr geehrte(r) Erfasser*in,\n\n"
                f"bei der automatischen Prüfung des Führungszeugnisses wurde ein "
                f"Problem festgestellt.\n\n"
                f"Person: {person}\n"
                f"Status: Ungültig\n\n"
                f"Bitte überprüfen Sie das Dokument manuell und ergreifen Sie die "
                f"erforderlichen Maßnahmen gemäß §72a SGB VIII."
            )
        elif status == "expired":
            subject = f"[Führungszeugnis] Abgelaufenes Führungszeugnis – {person}"
            body = (
                f"Sehr geehrte(r) Erfasser*in,\n\n"
                f"das folgende Führungszeugnis ist abgelaufen und muss erneuert werden.\n\n"
                f"Person: {person}\n"
                f"Ablaufdatum: {cancellation}\n\n"
                f"Bitte fordern Sie ein neues Führungszeugnis gemäß §72a SGB VIII an."
            )
        elif status == "expiring_soon":
            subject = f"[Führungszeugnis] Führungszeugnis läuft bald ab – {person}"
            body = (
                f"Sehr geehrte(r) Erfasser*in,\n\n"
                f"das folgende Führungszeugnis läuft in Kürze ab.\n\n"
                f"Person: {person}\n"
                f"Ablaufdatum: {cancellation}\n\n"
                f"Bitte beantragen Sie rechtzeitig ein neues Führungszeugnis."
            )
        else:
            subject = "[Führungszeugnis] Verarbeitungsfehler"
            body = (
                f"Sehr geehrte(r) Administrator*in,\n\n"
                f"bei der Verarbeitung eines Führungszeugnisses ist ein Fehler aufgetreten.\n\n"
                f"Person: {person}\n\n"
                f"Bitte prüfen Sie das System und das Dokument manuell."
            )

        return subject, body + footer

    @staticmethod
    def _format_date(d: date | None) -> str:
        if d is None:
            return "unbekannt"
        return d.strftime("%d.%m.%Y")
