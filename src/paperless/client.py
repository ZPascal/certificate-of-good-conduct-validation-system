"""Paperless-ngx REST API client."""

import logging
from dataclasses import dataclass, field
from datetime import datetime

import requests

logger = logging.getLogger(__name__)


@dataclass
class PaperlessDocument:
    id: int
    title: str
    content: str
    created: datetime | None
    added: datetime | None
    correspondent: str | None
    tags: list[str] = field(default_factory=list)
    original_file_name: str | None = None


class PaperlessClient:
    """Client for the Paperless-ngx REST API.

    Fetches documents tagged with the configured Führungszeugnis tag
    and retrieves their OCR-extracted text content.
    """

    def __init__(self, base_url: str, token: str, tag_name: str) -> None:
        self.base_url = base_url.rstrip("/")
        self.token = token
        self.tag_name = tag_name
        self._session = requests.Session()
        self._session.headers.update(
            {
                "Authorization": f"Token {token}",
                "Accept": "application/json; version=7",
            }
        )
        self._tag_id: int | None = None

    def _get(self, path: str, params: dict | None = None) -> dict:
        url = f"{self.base_url}/api/{path.lstrip('/')}"
        response = self._session.get(url, params=params, timeout=30)
        response.raise_for_status()
        return response.json()

    def _resolve_tag_id(self) -> int | None:
        """Resolve the configured tag name to its Paperless internal ID."""
        try:
            data = self._get("tags/", params={"name__iexact": self.tag_name})
            results = data.get("results", [])
            if results:
                return results[0]["id"]
        except requests.RequestException as exc:
            logger.warning("Failed to resolve tag '%s': %s", self.tag_name, exc)
        return None

    def _get_tag_id(self) -> int | None:
        if self._tag_id is None:
            self._tag_id = self._resolve_tag_id()
        return self._tag_id

    def _parse_datetime(self, value: str | None) -> datetime | None:
        if not value:
            return None
        try:
            return datetime.fromisoformat(value.replace("Z", "+00:00"))
        except ValueError:
            return None

    def _parse_document(self, raw: dict) -> PaperlessDocument:
        tag_ids = raw.get("tags", [])
        tag_names: list[str] = []
        for tag_id in tag_ids:
            try:
                tag_data = self._get(f"tags/{tag_id}/")
                tag_names.append(tag_data.get("name", str(tag_id)))
            except requests.RequestException:
                tag_names.append(str(tag_id))

        return PaperlessDocument(
            id=raw["id"],
            title=raw.get("title", ""),
            content=raw.get("content", ""),
            created=self._parse_datetime(raw.get("created")),
            added=self._parse_datetime(raw.get("added")),
            correspondent=raw.get("correspondent__name") or raw.get("correspondent"),
            tags=tag_names,
            original_file_name=raw.get("original_file_name"),
        )

    def get_document(self, document_id: int) -> PaperlessDocument:
        """Fetch a single document by its ID."""
        raw = self._get(f"documents/{document_id}/")
        return self._parse_document(raw)

    def get_fuehrungszeugnis_documents(
        self, page: int = 1, page_size: int = 25
    ) -> tuple[list[PaperlessDocument], int]:
        """Return documents tagged with the Führungszeugnis tag.

        Returns a tuple of (documents, total_count).
        """
        tag_id = self._get_tag_id()
        if tag_id is None:
            logger.warning(
                "Tag '%s' not found in Paperless. Returning empty list.",
                self.tag_name,
            )
            return [], 0

        params: dict = {"tags__id": tag_id, "page": page, "page_size": page_size}
        data = self._get("documents/", params=params)
        documents = [self._parse_document(doc) for doc in data.get("results", [])]
        total_count = data.get("count", len(documents))
        return documents, total_count

    def iter_fuehrungszeugnis_documents(self):
        """Yield all Führungszeugnis documents, handling pagination."""
        page = 1
        page_size = 25
        while True:
            documents, total = self.get_fuehrungszeugnis_documents(page=page, page_size=page_size)
            if not documents:
                break
            yield from documents
            if len(documents) < page_size:
                break
            page += 1
