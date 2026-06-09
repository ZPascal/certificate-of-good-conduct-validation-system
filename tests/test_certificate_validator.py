"""Tests for the certificate validation logic."""

import unittest
from datetime import UTC, datetime, timedelta

from src.validation.certificate_validator import CertificateValidator, ValidationResult


class TestFuehrungszeugnisIdentification(unittest.TestCase):
    def setUp(self):
        self.validator = CertificateValidator(max_age_years=5)

    def test_identifies_fuehrungszeugnis_by_title(self):
        result = self.validator.validate("Some random text", "Führungszeugnis_Mustermann.pdf")
        self.assertTrue(result.details["identified_as_fuehrungszeugnis"])

    def test_identifies_fuehrungszeugnis_by_content(self):
        text = "Erweitertes Führungszeugnis gemäß §72a SGB VIII\nKeine Eintragungen"
        result = self.validator.validate(text, "scan.pdf")
        self.assertTrue(result.details["identified_as_fuehrungszeugnis"])

    def test_rejects_non_fuehrungszeugnis(self):
        result = self.validator.validate("Invoice for services rendered", "invoice.pdf")
        self.assertFalse(result.is_valid)
        self.assertEqual(result.status, "invalid")
        self.assertFalse(result.details["identified_as_fuehrungszeugnis"])

    def test_identifies_bundeszentralregister(self):
        text = "Bundeszentralregisterauszug\nKeine Eintragungen\nAusgefertigt 01.06.2025"
        result = self.validator.validate(text, "doc.pdf")
        self.assertTrue(result.details["identified_as_fuehrungszeugnis"])


class TestCertificateTypeDetection(unittest.TestCase):
    def setUp(self):
        self.validator = CertificateValidator(max_age_years=5)

    def test_detects_erweitertes(self):
        text = "Erweitertes Führungszeugnis\nKeine Eintragungen\nAusgestellt 01.06.2025"
        result = self.validator.validate(text, "doc.pdf")
        self.assertEqual(result.certificate_type, "erweitertes_fuehrungszeugnis")

    def test_detects_plain_fuehrungszeugnis(self):
        text = "Führungszeugnis\nKeine Eintragungen\nAusgestellt 01.06.2025"
        result = self.validator.validate(text, "doc.pdf")
        self.assertEqual(result.certificate_type, "fuehrungszeugnis")


class TestEntryChecks(unittest.TestCase):
    def setUp(self):
        self.validator = CertificateValidator(max_age_years=5)

    def test_clean_certificate_keine_eintragungen(self):
        text = (
            "Erweitertes Führungszeugnis\n"
            "Es sind keine Eintragungen vorhanden.\n"
            "Ausgestellt 01.06.2025"
        )
        result = self.validator.validate(text, "doc.pdf")
        self.assertEqual(result.details["entries_status"], "no_entries")

    def test_clean_certificate_enthalt_keine(self):
        text = "Führungszeugnis\nenthält keine Eintragungen\nAusgestellt 01.06.2025"
        result = self.validator.validate(text, "doc.pdf")
        self.assertEqual(result.details["entries_status"], "no_entries")

    def test_invalid_when_entry_indicators_present(self):
        text = (
            "Führungszeugnis\n"
            "Eintragung: Verurteilung wegen Straftat\n"
            "Freiheitsstrafe 6 Monate\n"
            "Ausgestellt 01.06.2025"
        )
        result = self.validator.validate(text, "doc.pdf")
        self.assertEqual(result.details["entries_status"], "has_entries")
        self.assertFalse(result.is_valid)
        self.assertEqual(result.status, "invalid")

    def test_unknown_status_when_no_clear_indicator(self):
        text = "Führungszeugnis\nAusgestellt 01.06.2025"
        result = self.validator.validate(text, "doc.pdf")
        self.assertEqual(result.details["entries_status"], "unknown")


class TestDateExtractionAndExpiry(unittest.TestCase):
    def setUp(self):
        self.validator = CertificateValidator(max_age_years=5)

    def test_extracts_date_from_content(self):
        text = "Führungszeugnis\nKeine Eintragungen\nAusgestellt am 15.06.2023"
        result = self.validator.validate(text, "doc.pdf")
        self.assertIsNotNone(result.issue_date)
        self.assertEqual(result.issue_date.year, 2023)
        self.assertEqual(result.issue_date.month, 6)
        self.assertEqual(result.issue_date.day, 15)

    def test_recent_certificate_is_valid(self):
        recent = datetime.now(tz=UTC) - timedelta(days=100)
        date_str = recent.strftime("%d.%m.%Y")
        text = f"Führungszeugnis\nKeine Eintragungen\nAusgestellt {date_str}"
        result = self.validator.validate(text, "doc.pdf")
        self.assertTrue(result.is_valid)
        self.assertEqual(result.status, "valid")

    def test_expired_certificate_marked_expired(self):
        old = datetime.now(tz=UTC) - timedelta(days=365 * 6)
        date_str = old.strftime("%d.%m.%Y")
        text = f"Führungszeugnis\nKeine Eintragungen\nAusgestellt {date_str}"
        result = self.validator.validate(text, "doc.pdf")
        self.assertEqual(result.status, "expired")
        self.assertFalse(result.is_valid)
        self.assertTrue(any("older than" in e for e in result.errors))

    def test_expiring_soon_generates_warning(self):
        almost_expired = datetime.now(tz=UTC) - timedelta(days=365 * 4 + 200)
        date_str = almost_expired.strftime("%d.%m.%Y")
        text = f"Führungszeugnis\nKeine Eintragungen\nAusgestellt {date_str}"
        result = self.validator.validate(text, "doc.pdf")
        self.assertTrue(any("expire" in w.lower() for w in result.warnings))

    def test_iso_date_format(self):
        text = "Führungszeugnis\nKeine Eintragungen\nAusstellungsdatum 2024-03-20"
        result = self.validator.validate(text, "doc.pdf")
        self.assertIsNotNone(result.issue_date)
        self.assertEqual(result.issue_date.year, 2024)

    def test_valid_until_computed(self):
        text = "Führungszeugnis\nKeine Eintragungen\nAusgestellt 01.01.2024"
        result = self.validator.validate(text, "doc.pdf")
        self.assertIsNotNone(result.valid_until)
        self.assertEqual(result.valid_until.year, 2029)

    def test_no_date_generates_warning(self):
        text = "Führungszeugnis\nKeine Eintragungen"
        result = self.validator.validate(text, "doc.pdf")
        self.assertTrue(any("issue date" in w.lower() for w in result.warnings))


class TestNameExtraction(unittest.TestCase):
    def setUp(self):
        self.validator = CertificateValidator(max_age_years=5)

    def test_extracts_name(self):
        text = (
            "Erweitertes Führungszeugnis\n"
            "Name: Max Mustermann\n"
            "Keine Eintragungen\n"
            "Ausgestellt 01.06.2024"
        )
        result = self.validator.validate(text, "doc.pdf")
        self.assertEqual(result.person_name, "Max Mustermann")

    def test_no_name_returns_none(self):
        text = "Führungszeugnis\nKeine Eintragungen\nAusgestellt 01.06.2024"
        result = self.validator.validate(text, "doc.pdf")
        self.assertIsNone(result.person_name)


class TestEdgeCases(unittest.TestCase):
    def setUp(self):
        self.validator = CertificateValidator(max_age_years=5)

    def test_empty_text(self):
        result = self.validator.validate("", "doc.pdf")
        self.assertFalse(result.is_valid)

    def test_whitespace_only_text(self):
        result = self.validator.validate("   \n\t  ", "doc.pdf")
        self.assertFalse(result.is_valid)

    def test_result_is_dataclass(self):
        result = self.validator.validate(
            "Führungszeugnis\nKeine Eintragungen\nAusgestellt 01.01.2025",
            "doc.pdf",
        )
        self.assertIsInstance(result, ValidationResult)
        self.assertIsInstance(result.errors, list)
        self.assertIsInstance(result.warnings, list)
        self.assertIsInstance(result.details, dict)


class TestStreetExtraction(unittest.TestCase):
    def setUp(self):
        self.validator = CertificateValidator(max_age_years=5)

    def test_extracts_street_from_wohnhaft_label(self):
        text = (
            "Führungszeugnis\nKeine Eintragungen\nwohnhaft: Musterstraße 12\nAusgestellt 01.06.2025"
        )
        result = self.validator.validate(text, "doc.pdf")
        self.assertEqual(result.street, "Musterstraße 12")

    def test_extracts_street_from_anschrift_label(self):
        text = (
            "Führungszeugnis\nKeine Eintragungen\nAnschrift: Gartenweg 4a\nAusgestellt 01.06.2025"
        )
        result = self.validator.validate(text, "doc.pdf")
        self.assertEqual(result.street, "Gartenweg 4a")

    def test_returns_none_when_no_street_found(self):
        text = "Führungszeugnis\nKeine Eintragungen\nAusgestellt 01.06.2025"
        result = self.validator.validate(text, "doc.pdf")
        self.assertIsNone(result.street)
