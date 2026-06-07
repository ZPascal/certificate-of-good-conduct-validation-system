"""Hitobito JSON:API client.

Integrates with the hitobito membership management system to:
- Fetch person records and their roles
- Read and create qualification records (used for EFZ / Führungszeugnis)
- Retrieve contact information for alert recipients (Erfasser*in Führungszeugnis roles)

The Führungszeugnis (EFZ) is stored in hitobito's `qualifications` table
associated with a QualificationKind labelled "Erweitertes Führungszeugnis".
The relevant API endpoints are:
  GET    /api/qualifications?filter[person_id]=:id
  POST   /api/qualifications
  DELETE /api/qualifications/:id
"""

import logging
import re
from dataclasses import dataclass, field
from datetime import date

import requests

logger = logging.getLogger(__name__)

_CONTENT_TYPE = "application/vnd.api+json"

_UMLAUT_MAP = str.maketrans(
    {"ä": "ae", "ö": "oe", "ü": "ue", "Ä": "Ae", "Ö": "Oe", "Ü": "Ue", "ß": "ss"}
)


def _normalize_umlauts(text: str) -> str:
    """Replace German umlauts with ASCII digraphs for fuzzy matching."""
    return text.translate(_UMLAUT_MAP)


def _normalize_street(s: str) -> str:
    """Normalise a German street string for comparison: umlauts, lowercase, abbreviations."""
    s = _normalize_umlauts(s).lower().strip()
    s = re.sub(r"\bstr\.?\b", "strasse", s)
    return re.sub(r"\s+", " ", s)


@dataclass
class HitobitoRole:
    id: int
    type: str
    group_id: int
    group_name: str | None = None


@dataclass
class HitobitoQualification:
    id: int
    person_id: int
    qualification_kind_id: int
    qualification_kind_label: str | None
    start_at: date | None
    finish_at: date | None
    origin: str | None


@dataclass
class HitobitoAttribution:
    """A person record resolved from hitobito."""

    person_id: int | None = None
    group_id: int | None = None
    first_name: str | None = None
    last_name: str | None = None
    email: str | None = None
    roles: list[HitobitoRole] = field(default_factory=list)


class HitobitoClient:
    """Client for the hitobito JSON:API (v1.1).

    Service token requirements:
      - people: true
      - qualifications: true
      - permission: layer_and_below_read (or layer_and_below_full for writes)
    """

    def __init__(self, base_url: str, token: str) -> None:
        self.base_url = base_url.rstrip("/")
        self._session = requests.Session()
        self._session.headers.update(
            {
                "X-TOKEN": token,
                "Accept": "application/vnd.api+json",
                "Content-Type": _CONTENT_TYPE,
            }
        )
        self._efz_kind_id_cache: int | None = None

    # ------------------------------------------------------------------
    # Low-level helpers
    # ------------------------------------------------------------------

    def _get(self, path: str, params: dict | None = None) -> dict:
        url = f"{self.base_url}/api/{path.lstrip('/')}"
        response = self._session.get(url, params=params, timeout=30)
        response.raise_for_status()
        return response.json()

    def _post(self, path: str, payload: dict) -> dict:
        url = f"{self.base_url}/api/{path.lstrip('/')}"
        response = self._session.post(url, json=payload, timeout=30)
        response.raise_for_status()
        return response.json()

    def _delete(self, path: str) -> None:
        url = f"{self.base_url}/api/{path.lstrip('/')}"
        response = self._session.delete(url, timeout=30)
        response.raise_for_status()

    @staticmethod
    def _parse_date(value: str | None) -> date | None:
        if not value:
            return None
        try:
            return date.fromisoformat(value[:10])
        except ValueError:
            return None

    # ------------------------------------------------------------------
    # People
    # ------------------------------------------------------------------

    def get_person(self, person_id: int) -> HitobitoAttribution | None:
        """Fetch a single person by their hitobito ID."""
        try:
            data = self._get(f"people/{person_id}", params={"include": "roles"})
            return self._parse_person(data.get("data", {}), data.get("included", []))
        except requests.HTTPError as exc:
            if exc.response is not None and exc.response.status_code == 404:
                return None
            raise

    def find_person_by_name(self, name: str) -> HitobitoAttribution | None:
        """Search for a person by full name (first + last).

        Tries an exact match first, then falls back to normalised umlaut comparison.
        Returns the first match or None if no person is found.
        """
        needle = _normalize_umlauts(name).lower()
        try:
            data = self._get("people", params={"q": name, "page[size]": 25})
            for item in data.get("data", []):
                attrs = item.get("attributes", {})
                full_name = f"{attrs.get('first_name', '')} {attrs.get('last_name', '')}".strip()
                if _normalize_umlauts(full_name).lower() == needle:
                    return HitobitoAttribution(
                        person_id=int(item["id"]),
                        first_name=attrs.get("first_name"),
                        last_name=attrs.get("last_name"),
                        email=attrs.get("email"),
                    )
        except requests.RequestException as exc:
            logger.error("Failed to search person by name '%s': %s", name, exc)
        return None

    def find_person_by_name_and_street(
        self, name: str, street: str | None
    ) -> HitobitoAttribution | None:
        """Search for a person by full name, using street to break ties between multiple matches.

        If exactly one candidate matches the name, return it immediately without address lookup.
        If multiple candidates match and ``street`` is provided, fetch each candidate's detail and
        return the single unambiguous street match, or ``None`` if zero or multiple match.
        If multiple candidates match and ``street`` is ``None``, log a warning and return ``None``.
        """
        needle = _normalize_umlauts(name).lower()
        try:
            data = self._get("people", params={"q": name, "page[size]": 25})
            candidates = [
                item
                for item in data.get("data", [])
                if _normalize_umlauts(
                    f"{item.get('attributes', {}).get('first_name', '')} "
                    f"{item.get('attributes', {}).get('last_name', '')}".strip()
                ).lower()
                == needle
            ]
        except requests.RequestException as exc:
            logger.error("Failed to search person by name '%s': %s", name, exc)
            return None

        if len(candidates) == 1:
            attrs = candidates[0].get("attributes", {})
            return HitobitoAttribution(
                person_id=int(candidates[0]["id"]),
                first_name=attrs.get("first_name"),
                last_name=attrs.get("last_name"),
                email=attrs.get("email"),
            )

        if len(candidates) > 1 and street is None:
            ids = [c["id"] for c in candidates]
            logger.warning(
                "Ambiguous hitobito match for '%s' (IDs: %s); no street available.",
                name,
                ids,
            )
            return None

        if len(candidates) > 1 and street is not None:
            norm_street = _normalize_street(street)
            matches: list[HitobitoAttribution] = []
            for candidate in candidates:
                try:
                    candidate_data = self._get(
                        f"people/{candidate['id']}", params={"include": "roles"}
                    )
                except requests.RequestException as exc:
                    logger.warning(
                        "Failed to fetch person %s for street disambiguation: %s",
                        candidate["id"],
                        exc,
                    )
                    continue
                candidate_street = (
                    candidate_data.get("data", {}).get("attributes", {}).get("street", "")
                )
                if _normalize_street(candidate_street) == norm_street:
                    attrs = candidate_data.get("data", {}).get("attributes", {})
                    matches.append(HitobitoAttribution(
                        person_id=int(candidate["id"]),
                        first_name=attrs.get("first_name"),
                        last_name=attrs.get("last_name"),
                        email=attrs.get("email"),
                    ))
            if len(matches) == 1:
                return matches[0]
            logger.warning(
                "Street disambiguation for '%s' found %d matches (street=%r).",
                name,
                len(matches),
                street,
            )
            return None

        return None

    def find_people_by_role(
        self, group_id: int, role_type_contains: str
    ) -> list[HitobitoAttribution]:
        """Find people in a group whose role type contains the given substring.

        The comparison is case-insensitive and normalises German umlauts to their
        ASCII equivalents so that, e.g., "Führungszeugnis" matches the Ruby STI
        class name "ErfassungFuehrungszeugnis".

        Used to look up "Erfasser*in Führungszeugnis" recipients for alerts.
        """
        try:
            results: list[HitobitoAttribution] = []
            next_url: str | None = f"{self.base_url}/api/roles"
            params: dict = {
                "filter[group_id]": group_id,
                "include": "person",
                "page[size]": 100,
            }
            needle = _normalize_umlauts(role_type_contains).lower()
            while next_url:
                response = self._session.get(next_url, params=params, timeout=30)
                response.raise_for_status()
                data = response.json()
                params = {}  # subsequent pages use the full URL from links.next
                included = {(item["type"], item["id"]): item for item in data.get("included", [])}
                for role in data.get("data", []):
                    role_type = role.get("attributes", {}).get("type", "")
                    if needle not in _normalize_umlauts(role_type).lower():
                        continue
                    person_rel = role.get("relationships", {}).get("person", {}).get("data", {})
                    if not person_rel:
                        continue
                    person_key = ("people", person_rel["id"])
                    person_raw = included.get(person_key, {})
                    attrs = person_raw.get("attributes", {})
                    results.append(
                        HitobitoAttribution(
                            person_id=int(person_rel["id"]),
                            group_id=group_id,
                            first_name=attrs.get("first_name"),
                            last_name=attrs.get("last_name"),
                            email=attrs.get("email"),
                        )
                    )
                next_url = data.get("links", {}).get("next")
            return results
        except requests.RequestException as exc:
            logger.error("Failed to fetch roles in group %d: %s", group_id, exc)
            return []

    # ------------------------------------------------------------------
    # Qualification kinds
    # ------------------------------------------------------------------

    def get_efz_qualification_kind_id(self, label: str) -> int | None:
        """Resolve the qualification_kind_id for the EFZ by label.

        Caches the result in memory to avoid repeated API calls.
        """
        if self._efz_kind_id_cache is not None:
            return self._efz_kind_id_cache
        try:
            data = self._get("qualification_kinds", params={"page[size]": 200})
            for item in data.get("data", []):
                attrs = item.get("attributes", {})
                if attrs.get("label", "").lower() == label.lower():
                    self._efz_kind_id_cache = int(item["id"])
                    return self._efz_kind_id_cache
            logger.warning("QualificationKind '%s' not found in hitobito.", label)
        except requests.RequestException as exc:
            logger.error("Failed to fetch qualification kinds: %s", exc)
        return None

    # ------------------------------------------------------------------
    # Qualifications (EFZ records)
    # ------------------------------------------------------------------

    def get_person_qualifications(
        self, person_id: int, qualification_kind_id: int | None = None
    ) -> list[HitobitoQualification]:
        """Return qualifications for a person, optionally filtered by kind."""
        params: dict = {
            "filter[person_id]": person_id,
            "include": "qualification_kind",
            "page[size]": 100,
        }
        if qualification_kind_id is not None:
            params["filter[qualification_kind_id]"] = qualification_kind_id
        try:
            data = self._get("qualifications", params=params)
            included_kinds = {
                item["id"]: item.get("attributes", {}).get("label")
                for item in data.get("included", [])
                if item.get("type") == "qualification_kinds"
            }
            results: list[HitobitoQualification] = []
            for item in data.get("data", []):
                attrs = item.get("attributes", {})
                kind_rel = (
                    item.get("relationships", {}).get("qualification_kind", {}).get("data", {})
                )
                kind_id = int(kind_rel.get("id", 0)) if kind_rel else 0
                results.append(
                    HitobitoQualification(
                        id=int(item["id"]),
                        person_id=person_id,
                        qualification_kind_id=kind_id,
                        qualification_kind_label=included_kinds.get(kind_rel.get("id", "")),
                        start_at=self._parse_date(attrs.get("start_at")),
                        finish_at=self._parse_date(attrs.get("finish_at")),
                        origin=attrs.get("origin"),
                    )
                )
            return results
        except requests.RequestException as exc:
            logger.error("Failed to fetch qualifications for person %d: %s", person_id, exc)
            return []

    def create_efz_qualification(
        self,
        person_id: int,
        qualification_kind_id: int,
        start_at: date,
        finish_at: date | None,
        origin: str = "",
    ) -> HitobitoQualification | None:
        """Record a validated EFZ as a qualification in hitobito.

        This creates a new entry in hitobito's qualifications table,
        linking the person to the 'Erweitertes Führungszeugnis' kind
        with the certificate's issue date and expiry date.
        """
        attributes: dict = {"start_at": start_at.isoformat(), "origin": origin}
        if finish_at:
            attributes["finish_at"] = finish_at.isoformat()

        payload = {
            "data": {
                "type": "qualifications",
                "attributes": attributes,
                "relationships": {
                    "person": {"data": {"type": "people", "id": str(person_id)}},
                    "qualification_kind": {
                        "data": {
                            "type": "qualification_kinds",
                            "id": str(qualification_kind_id),
                        }
                    },
                },
            }
        }
        try:
            data = self._post("qualifications", payload)
            item = data.get("data", {})
            attrs = item.get("attributes", {})
            logger.info(
                "Created EFZ qualification %s for person %d in hitobito.",
                item.get("id"),
                person_id,
            )
            return HitobitoQualification(
                id=int(item["id"]),
                person_id=person_id,
                qualification_kind_id=qualification_kind_id,
                qualification_kind_label=None,
                start_at=self._parse_date(attrs.get("start_at")),
                finish_at=self._parse_date(attrs.get("finish_at")),
                origin=attrs.get("origin"),
            )
        except requests.RequestException as exc:
            logger.error(
                "Failed to create EFZ qualification for person %d: %s",
                person_id,
                exc,
            )
            return None

    def delete_qualification(self, qualification_id: int) -> None:
        """Delete a qualification record by ID."""
        self._delete(f"qualifications/{qualification_id}")

    # ------------------------------------------------------------------
    # Private helpers
    # ------------------------------------------------------------------

    def _parse_person(self, data: dict, included: list[dict]) -> HitobitoAttribution | None:
        if not data:
            return None
        attrs = data.get("attributes", {})
        attribution = HitobitoAttribution(
            person_id=int(data["id"]),
            first_name=attrs.get("first_name"),
            last_name=attrs.get("last_name"),
            email=attrs.get("email"),
        )
        roles_data = data.get("relationships", {}).get("roles", {}).get("data", [])
        included_map = {(item["type"], item["id"]): item for item in included}
        for role_ref in roles_data:
            role_raw = included_map.get(("roles", role_ref["id"]))
            if role_raw:
                role_attrs = role_raw.get("attributes", {})
                group_rel = role_raw.get("relationships", {}).get("group", {}).get("data", {})
                attribution.roles.append(
                    HitobitoRole(
                        id=int(role_raw["id"]),
                        type=role_attrs.get("type", ""),
                        group_id=int(group_rel.get("id", 0)),
                    )
                )
        return attribution
