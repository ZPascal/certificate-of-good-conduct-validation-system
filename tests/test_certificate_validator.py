"""Tests for the certificate validation logic."""

from datetime import UTC, datetime, timedelta

import pytest

from src.validation.certificate_validator import CertificateValidator, ValidationResult


@pytest.fixture
def validator():
    return CertificateValidator(max_age_years=5)


# ── Identification ─────────────────────────────────────────────────────────────


class TestFuehrungszeugnisIdentification:
    def test_identifies_fuehrungszeugnis_by_title(self, validator):
        result = validator.validate("Some random text", "Führungszeugnis_Mustermann.pdf")
        assert result.details["identified_as_fuehrungszeugnis"] is True

    def test_identifies_fuehrungszeugnis_by_content(self, validator):
        text = "Erweitertes Führungszeugnis gemäß §72a SGB VIII\nKeine Eintragungen"
        result = validator.validate(text, "scan.pdf")
        assert result.details["identified_as_fuehrungszeugnis"] is True

    def test_rejects_non_fuehrungszeugnis(self, validator):
        result = validator.validate("Invoice for services rendered", "invoice.pdf")
        assert result.is_valid is False
        assert result.status == "invalid"
        assert result.details["identified_as_fuehrungszeugnis"] is False

    def test_identifies_bundeszentralregister(self, validator):
        text = "Bundeszentralregisterauszug\nKeine Eintragungen\nAusgefertigt 01.06.2025"
        result = validator.validate(text, "doc.pdf")
        assert result.details["identified_as_fuehrungszeugnis"] is True


# ── Certificate type detection ─────────────────────────────────────────────────


class TestCertificateTypeDetection:
    def test_detects_erweitertes(self, validator):
        text = "Erweitertes Führungszeugnis\nKeine Eintragungen\nAusgestellt 01.06.2025"
        result = validator.validate(text, "doc.pdf")
        assert result.certificate_type == "erweitertes_fuehrungszeugnis"

    def test_detects_plain_fuehrungszeugnis(self, validator):
        text = "Führungszeugnis\nKeine Eintragungen\nAusgestellt 01.06.2025"
        result = validator.validate(text, "doc.pdf")
        assert result.certificate_type == "fuehrungszeugnis"


# ── Entry checks ───────────────────────────────────────────────────────────────


class TestEntryChecks:
    def test_clean_certificate_keine_eintragungen(self, validator):
        text = (
            "Erweitertes Führungszeugnis\n"
            "Es sind keine Eintragungen vorhanden.\n"
            "Ausgestellt 01.06.2025"
        )
        result = validator.validate(text, "doc.pdf")
        assert result.details["entries_status"] == "no_entries"

    def test_clean_certificate_enthalt_keine(self, validator):
        text = "Führungszeugnis\nenthält keine Eintragungen\nAusgestellt 01.06.2025"
        result = validator.validate(text, "doc.pdf")
        assert result.details["entries_status"] == "no_entries"

    def test_invalid_when_entry_indicators_present(self, validator):
        text = (
            "Führungszeugnis\n"
            "Eintragung: Verurteilung wegen Straftat\n"
            "Freiheitsstrafe 6 Monate\n"
            "Ausgestellt 01.06.2025"
        )
        result = validator.validate(text, "doc.pdf")
        assert result.details["entries_status"] == "has_entries"
        assert result.is_valid is False
        assert result.status == "invalid"

    def test_unknown_status_when_no_clear_indicator(self, validator):
        text = "Führungszeugnis\nAusgestellt 01.06.2025"
        result = validator.validate(text, "doc.pdf")
        assert result.details["entries_status"] == "unknown"


# ── Date extraction and expiry ─────────────────────────────────────────────────


class TestDateExtractionAndExpiry:
    def test_extracts_date_from_content(self, validator):
        text = "Führungszeugnis\nKeine Eintragungen\nAusgestellt am 15.06.2023"
        result = validator.validate(text, "doc.pdf")
        assert result.issue_date is not None
        assert result.issue_date.year == 2023
        assert result.issue_date.month == 6
        assert result.issue_date.day == 15

    def test_recent_certificate_is_valid(self, validator):
        recent = datetime.now(tz=UTC) - timedelta(days=100)
        date_str = recent.strftime("%d.%m.%Y")
        text = f"Führungszeugnis\nKeine Eintragungen\nAusgestellt {date_str}"
        result = validator.validate(text, "doc.pdf")
        assert result.is_valid is True
        assert result.status == "valid"

    def test_expired_certificate_marked_expired(self):
        validator = CertificateValidator(max_age_years=5)
        old = datetime.now(tz=UTC) - timedelta(days=365 * 6)
        date_str = old.strftime("%d.%m.%Y")
        text = f"Führungszeugnis\nKeine Eintragungen\nAusgestellt {date_str}"
        result = validator.validate(text, "doc.pdf")
        assert result.status == "expired"
        assert result.is_valid is False
        assert any("older than" in e for e in result.errors)

    def test_expiring_soon_generates_warning(self):
        validator = CertificateValidator(max_age_years=5)
        almost_expired = datetime.now(tz=UTC) - timedelta(days=365 * 4 + 200)
        date_str = almost_expired.strftime("%d.%m.%Y")
        text = f"Führungszeugnis\nKeine Eintragungen\nAusgestellt {date_str}"
        result = validator.validate(text, "doc.pdf")
        assert any("expire" in w.lower() for w in result.warnings)

    def test_iso_date_format(self, validator):
        text = "Führungszeugnis\nKeine Eintragungen\nAusstellungsdatum 2024-03-20"
        result = validator.validate(text, "doc.pdf")
        assert result.issue_date is not None
        assert result.issue_date.year == 2024

    def test_valid_until_computed(self, validator):
        text = "Führungszeugnis\nKeine Eintragungen\nAusgestellt 01.01.2024"
        result = validator.validate(text, "doc.pdf")
        assert result.valid_until is not None
        assert result.valid_until.year == 2029

    def test_no_date_generates_warning(self, validator):
        text = "Führungszeugnis\nKeine Eintragungen"
        result = validator.validate(text, "doc.pdf")
        assert any("issue date" in w.lower() for w in result.warnings)


# ── Name extraction ────────────────────────────────────────────────────────────


class TestNameExtraction:
    def test_extracts_name(self, validator):
        text = (
            "Erweitertes Führungszeugnis\n"
            "Name: Max Mustermann\n"
            "Keine Eintragungen\n"
            "Ausgestellt 01.06.2024"
        )
        result = validator.validate(text, "doc.pdf")
        assert result.person_name == "Max Mustermann"

    def test_no_name_returns_none(self, validator):
        text = "Führungszeugnis\nKeine Eintragungen\nAusgestellt 01.06.2024"
        result = validator.validate(text, "doc.pdf")
        assert result.person_name is None


# ── Edge cases ─────────────────────────────────────────────────────────────────


class TestEdgeCases:
    def test_empty_text(self, validator):
        result = validator.validate("", "doc.pdf")
        assert result.is_valid is False

    def test_whitespace_only_text(self, validator):
        result = validator.validate("   \n\t  ", "doc.pdf")
        assert result.is_valid is False

    def test_result_is_dataclass(self, validator):
        result = validator.validate(
            "Führungszeugnis\nKeine Eintragungen\nAusgestellt 01.01.2025",
            "doc.pdf",
        )
        assert isinstance(result, ValidationResult)
        assert isinstance(result.errors, list)
        assert isinstance(result.warnings, list)
        assert isinstance(result.details, dict)


# ── Street extraction ──────────────────────────────────────────────────────────


class TestStreetExtraction:
    def test_extracts_street_from_wohnhaft_label(self, validator):
        text = "Führungszeugnis\nKeine Eintragungen\nwohnhaft: Musterstraße 12\nAusgestellt 01.06.2025"
        result = validator.validate(text, "doc.pdf")
        assert result.street == "Musterstraße 12"

    def test_extracts_street_from_anschrift_label(self, validator):
        text = "Führungszeugnis\nKeine Eintragungen\nAnschrift: Gartenweg 4a\nAusgestellt 01.06.2025"
        result = validator.validate(text, "doc.pdf")
        assert result.street == "Gartenweg 4a"

    def test_returns_none_when_no_street_found(self, validator):
        text = "Führungszeugnis\nKeine Eintragungen\nAusgestellt 01.06.2025"
        result = validator.validate(text, "doc.pdf")
        assert result.street is None
