"""Tests for the Paperless-ngx client."""

import pytest
import responses as responses_lib

from src.paperless.client import PaperlessClient, PaperlessDocument

PAPERLESS_BASE = "http://paperless.example.com"


@pytest.fixture
def client():
    return PaperlessClient(
        base_url=PAPERLESS_BASE,
        token="test-token",
        tag_name="Führungszeugnis",
    )


class TestGetDocument:
    @responses_lib.activate
    def test_returns_document(self, client):
        responses_lib.add(
            responses_lib.GET,
            f"{PAPERLESS_BASE}/api/documents/1/",
            json={
                "id": 1,
                "title": "Führungszeugnis Max Mustermann",
                "content": "Erweitertes Führungszeugnis\nKeine Eintragungen",
                "created": "2024-01-15T00:00:00Z",
                "added": "2024-01-16T10:00:00Z",
                "tags": [],
                "original_file_name": "FZ_Mustermann.pdf",
            },
            status=200,
        )
        doc = client.get_document(1)
        assert isinstance(doc, PaperlessDocument)
        assert doc.id == 1
        assert doc.title == "Führungszeugnis Max Mustermann"
        assert "Keine Eintragungen" in doc.content
        assert doc.created is not None
        assert doc.created.year == 2024


class TestGetFuehrungszeugnisDocuments:
    @responses_lib.activate
    def test_returns_empty_when_tag_not_found(self, client):
        responses_lib.add(
            responses_lib.GET,
            f"{PAPERLESS_BASE}/api/tags/",
            json={"results": [], "count": 0},
            status=200,
        )
        docs, total = client.get_fuehrungszeugnis_documents()
        assert docs == []
        assert total == 0

    @responses_lib.activate
    def test_returns_documents_for_tag(self, client):
        responses_lib.add(
            responses_lib.GET,
            f"{PAPERLESS_BASE}/api/tags/",
            json={
                "results": [{"id": 5, "name": "Führungszeugnis"}],
                "count": 1,
            },
            status=200,
        )
        responses_lib.add(
            responses_lib.GET,
            f"{PAPERLESS_BASE}/api/documents/",
            json={
                "count": 1,
                "results": [
                    {
                        "id": 42,
                        "title": "FZ Mustermann",
                        "content": "Führungszeugnis\nKeine Eintragungen",
                        "created": "2024-06-01T00:00:00Z",
                        "added": "2024-06-01T12:00:00Z",
                        "tags": [],
                        "original_file_name": "FZ.pdf",
                    }
                ],
            },
            status=200,
        )
        docs, total = client.get_fuehrungszeugnis_documents()
        assert total == 1
        assert len(docs) == 1
        assert docs[0].id == 42

    @responses_lib.activate
    def test_tag_id_is_cached(self, client):
        responses_lib.add(
            responses_lib.GET,
            f"{PAPERLESS_BASE}/api/tags/",
            json={"results": [{"id": 5, "name": "Führungszeugnis"}], "count": 1},
            status=200,
        )
        responses_lib.add(
            responses_lib.GET,
            f"{PAPERLESS_BASE}/api/documents/",
            json={"count": 0, "results": []},
            status=200,
        )

        client.get_fuehrungszeugnis_documents()
        client.get_fuehrungszeugnis_documents()

        tag_calls = [c for c in responses_lib.calls if "/api/tags/" in c.request.url]
        assert len(tag_calls) == 1


class TestIterFuehrungszeugnisDocuments:
    @responses_lib.activate
    def test_iterates_all_pages(self, client):
        responses_lib.add(
            responses_lib.GET,
            f"{PAPERLESS_BASE}/api/tags/",
            json={"results": [{"id": 5, "name": "Führungszeugnis"}], "count": 1},
            status=200,
        )
        page_1_docs = [
            {
                "id": i,
                "title": f"FZ {i}",
                "content": "Führungszeugnis\nKeine Eintragungen",
                "created": "2024-01-01T00:00:00Z",
                "added": "2024-01-01T00:00:00Z",
                "tags": [],
                "original_file_name": f"fz{i}.pdf",
            }
            for i in range(1, 26)
        ]
        page_2_docs = [
            {
                "id": 26,
                "title": "FZ 26",
                "content": "Führungszeugnis\nKeine Eintragungen",
                "created": "2024-01-01T00:00:00Z",
                "added": "2024-01-01T00:00:00Z",
                "tags": [],
                "original_file_name": "fz26.pdf",
            }
        ]
        responses_lib.add(
            responses_lib.GET,
            f"{PAPERLESS_BASE}/api/documents/",
            json={"count": 26, "results": page_1_docs},
            status=200,
        )
        responses_lib.add(
            responses_lib.GET,
            f"{PAPERLESS_BASE}/api/documents/",
            json={"count": 26, "results": page_2_docs},
            status=200,
        )
        docs = list(client.iter_fuehrungszeugnis_documents())
        assert len(docs) == 26
