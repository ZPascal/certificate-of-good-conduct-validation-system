"""Certificate validation logic for German Führungszeugnis (certificate of good conduct).

Validates whether an OCR-extracted Führungszeugnis is suitable for child care work
pursuant to §72a SGB VIII (Kinder- und Jugendhilfe).

Key rules:
- The document must be identified as a "Führungszeugnis" (preferably "erweitertes")
- The certificate must contain no relevant criminal entries (§72a SGB VIII)
- The certificate must not be older than the configured maximum age
"""

import logging
import re
from dataclasses import dataclass, field
from datetime import UTC, datetime

from dateutil import parser as dateutil_parser

logger = logging.getLogger(__name__)

# Terms that confirm this is a Führungszeugnis document
FUEHRUNGSZEUGNIS_INDICATORS = [
    "führungszeugnis",
    "fuehrungszeugnis",
    "bundeszentralregisterauszug",
    "bundeszentralregister",
    "§72a sgb viii",
    "sgb viii",
]

# Phrases indicating no criminal entries (clean certificate)
NO_ENTRIES_PHRASES = [
    "keine eintragungen",
    "enthält keine eintragungen",
    "es sind keine eintragungen",
    "nicht vorbestraft",
    "ohne eintragungen",
]

# Terms that indicate relevant criminal entries are present
ENTRY_INDICATORS = [
    "eintragung",
    "vorstrafe",
    "verurteilung",
    "freiheitsstrafe",
    "geldstrafe",
    "straftat",
]

# Date patterns used in German Führungszeugnis documents
_DATE_PATTERN = re.compile(r"\b(\d{1,2}[./ ]\d{1,2}[./ ]\d{4}|\d{4}-\d{2}-\d{2})\b")

# Issuing-date label patterns (non-greedy middle section to avoid consuming the date)
_ISSUE_DATE_LABELS = re.compile(
    r"(ausgestellt|ausstellungsdatum|datum|erstellt|"
    r"ausgefertigt)[^\n]{0,30}?(\d{1,2}[./ ]\d{1,2}[./ ]\d{4}|\d{4}-\d{2}-\d{2})",
    re.IGNORECASE,
)

# Name extraction: capture the remainder of the line after a name label keyword
_NAME_LABEL = re.compile(
    r"(?:vor- und nachname|vorname|nachname|personalien|name)\s*:?\s*([^\n]+)",
    re.IGNORECASE,
)

# Street address extraction: label followed by "Streetname NN[a]"
_STREET_PATTERN = re.compile(
    r"(?:wohnhaft|adresse|anschrift|stra(?:ße|sse)|str\.|weg|allee|platz|gasse)"
    r"[^\n]{0,10}?([A-ZÄÖÜ][a-zäöüßA-ZÄÖÜ\-\.][a-zäöüßA-ZÄÖÜ\-\. ]{2,38}?"
    r"\s+\d{1,4}\s*[a-z]?)(?:\n|$)",
    re.IGNORECASE,
)

# Pre-compiled word-boundary patterns for entry indicators (used in _check_entries)
_ENTRY_INDICATOR_PATTERNS = [
    re.compile(rf"\b{re.escape(ind)}\b", re.IGNORECASE) for ind in ENTRY_INDICATORS
]


@dataclass
class ValidationResult:
    is_valid: bool
    status: str
    person_name: str | None = None
    issue_date: datetime | None = None
    valid_until: datetime | None = None
    certificate_type: str = "erweitertes_fuehrungszeugnis"
    street: str | None = None
    details: dict = field(default_factory=dict)
    errors: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)


class CertificateValidator:
    """Validates German Führungszeugnis documents for §72a SGB VIII compliance."""

    def __init__(self, max_age_years: int = 5) -> None:
        self.max_age_years = max_age_years

    def validate(self, text: str, document_title: str = "") -> ValidationResult:
        """Validate the OCR text extracted from a Führungszeugnis PDF.

        Returns a ValidationResult describing whether the certificate is valid
        for child care work under §72a SGB VIII.
        """
        text_lower = text.lower()
        errors: list[str] = []
        warnings: list[str] = []
        details: dict = {}

        if not self._is_fuehrungszeugnis(text_lower, document_title):
            return ValidationResult(
                is_valid=False,
                status="invalid",
                errors=[
                    "Document does not appear to be a Führungszeugnis. "
                    "Could not find identifying keywords."
                ],
                details={"identified_as_fuehrungszeugnis": False},
            )
        details["identified_as_fuehrungszeugnis"] = True

        certificate_type = self._detect_certificate_type(text_lower)
        details["certificate_type"] = certificate_type

        person_name = self._extract_person_name(text)
        details["person_name"] = person_name

        street = self._extract_street_address(text)
        details["street"] = street

        issue_date = self._extract_issue_date(text)
        details["issue_date"] = issue_date.isoformat() if issue_date else None

        valid_until = None
        age_valid = True
        if issue_date:
            valid_until = self._compute_valid_until(issue_date)
            details["valid_until"] = valid_until.isoformat()
            today = datetime.now(tz=UTC)
            if issue_date.tzinfo is None:
                issue_date = issue_date.replace(tzinfo=UTC)
            age_years = (today - issue_date).days / 365.25
            if age_years > self.max_age_years:
                age_valid = False
                errors.append(
                    f"Certificate is older than {self.max_age_years} years "
                    f"(issued {issue_date.date()}, today {today.date()})."
                )
            elif age_years > (self.max_age_years - 1):
                warnings.append(
                    f"Certificate will expire within one year (issued {issue_date.date()})."
                )
        else:
            warnings.append("Could not determine the issue date of the certificate.")

        entries_status = self._check_entries(text_lower)
        details["entries_status"] = entries_status
        if entries_status == "has_entries":
            errors.append(
                "Certificate contains criminal entries that may disqualify the holder "
                "from child care work under §72a SGB VIII."
            )
        elif entries_status == "unknown":
            errors.append(
                "Could not clearly determine whether the certificate contains entries. "
                "Manual review required."
            )

        is_valid = not errors and age_valid and entries_status == "no_entries"
        if is_valid:
            status = "valid"
        elif entries_status == "has_entries":
            status = "invalid"
        elif not age_valid:
            status = "expired"
        else:
            status = "invalid"

        return ValidationResult(
            is_valid=is_valid,
            status=status,
            person_name=person_name,
            issue_date=issue_date,
            valid_until=valid_until,
            certificate_type=certificate_type,
            street=street,
            details=details,
            errors=errors,
            warnings=warnings,
        )

    def _is_fuehrungszeugnis(self, text_lower: str, title: str) -> bool:
        title_lower = title.lower()
        combined = text_lower + " " + title_lower
        return any(ind in combined for ind in FUEHRUNGSZEUGNIS_INDICATORS)

    def _detect_certificate_type(self, text_lower: str) -> str:
        if "erweitertes führungszeugnis" in text_lower or "erweitertes" in text_lower:
            return "erweitertes_fuehrungszeugnis"
        return "fuehrungszeugnis"

    def _extract_person_name(self, text: str) -> str | None:
        match = _NAME_LABEL.search(text)
        if match:
            name = match.group(1).strip()
            if name:
                return name
        return None

    def _extract_street_address(self, text: str) -> str | None:
        match = _STREET_PATTERN.search(text)
        if match:
            return match.group(1).strip()
        return None

    def _extract_issue_date(self, text: str) -> datetime | None:
        match = _ISSUE_DATE_LABELS.search(text)
        if match:
            date_str = match.group(2)
            return self._parse_german_date(date_str)

        dates = _DATE_PATTERN.findall(text)
        if dates:
            return self._parse_german_date(dates[-1])
        return None

    def _parse_german_date(self, date_str: str) -> datetime | None:
        date_str = date_str.replace(" ", ".")
        try:
            parsed = dateutil_parser.parse(date_str, dayfirst=True)
            return parsed.replace(tzinfo=UTC)
        except (ValueError, OverflowError):
            return None

    def _compute_valid_until(self, issue_date: datetime) -> datetime:
        try:
            return issue_date.replace(year=issue_date.year + self.max_age_years)
        except ValueError:
            return issue_date.replace(year=issue_date.year + self.max_age_years, day=28)

    def _check_entries(self, text_lower: str) -> str:
        """Return 'no_entries', 'has_entries', or 'unknown'."""
        if any(phrase in text_lower for phrase in NO_ENTRIES_PHRASES):
            for pattern in _ENTRY_INDICATOR_PATTERNS:
                if len(pattern.findall(text_lower)) > 2:
                    return "has_entries"
            return "no_entries"

        for pattern in _ENTRY_INDICATOR_PATTERNS:
            if pattern.search(text_lower):
                return "has_entries"

        return "unknown"
