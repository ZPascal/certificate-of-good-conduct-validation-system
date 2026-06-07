"""Tests for the Hitobito JSON:API client."""

import pytest
import responses as responses_lib

from src.hitobito.client import HitobitoAttribution, HitobitoClient, HitobitoQualification

HITOBITO_BASE = "http://hitobito.example.com"


@pytest.fixture
def client():
    return HitobitoClient(base_url=HITOBITO_BASE, token="test-token")


class TestGetPerson:
    @responses_lib.activate
    def test_get_person_returns_attribution(self, client):
        responses_lib.add(
            responses_lib.GET,
            f"{HITOBITO_BASE}/api/people/42",
            json={
                "data": {
                    "id": "42",
                    "type": "people",
                    "attributes": {
                        "first_name": "Max",
                        "last_name": "Mustermann",
                        "email": "max@example.com",
                    },
                    "relationships": {"roles": {"data": []}},
                },
                "included": [],
                "meta": {},
            },
            status=200,
        )
        result = client.get_person(42)
        assert result is not None
        assert isinstance(result, HitobitoAttribution)
        assert result.person_id == 42
        assert result.first_name == "Max"
        assert result.last_name == "Mustermann"
        assert result.email == "max@example.com"

    @responses_lib.activate
    def test_get_person_returns_none_for_404(self, client):
        responses_lib.add(
            responses_lib.GET,
            f"{HITOBITO_BASE}/api/people/999",
            json={"errors": [{"code": "not_found"}]},
            status=404,
        )
        result = client.get_person(999)
        assert result is None

    @responses_lib.activate
    def test_get_person_parses_roles(self, client):
        responses_lib.add(
            responses_lib.GET,
            f"{HITOBITO_BASE}/api/people/10",
            json={
                "data": {
                    "id": "10",
                    "type": "people",
                    "attributes": {
                        "first_name": "Anna",
                        "last_name": "Schmidt",
                        "email": "anna@example.com",
                    },
                    "relationships": {"roles": {"data": [{"type": "roles", "id": "55"}]}},
                },
                "included": [
                    {
                        "id": "55",
                        "type": "roles",
                        "attributes": {"type": "Group::Stamm::ErfassungFuehrungszeugnis"},
                        "relationships": {"group": {"data": {"type": "groups", "id": "7"}}},
                    }
                ],
                "meta": {},
            },
            status=200,
        )
        result = client.get_person(10)
        assert result is not None
        assert len(result.roles) == 1
        assert result.roles[0].type == "Group::Stamm::ErfassungFuehrungszeugnis"
        assert result.roles[0].group_id == 7


class TestFindPersonByName:
    @responses_lib.activate
    def test_finds_person_by_exact_name(self, client):
        responses_lib.add(
            responses_lib.GET,
            f"{HITOBITO_BASE}/api/people",
            json={
                "data": [
                    {
                        "id": "77",
                        "type": "people",
                        "attributes": {
                            "first_name": "Maria",
                            "last_name": "Muster",
                            "email": "maria@example.com",
                        },
                    }
                ],
                "meta": {},
            },
            status=200,
        )
        result = client.find_person_by_name("Maria Muster")
        assert result is not None
        assert result.person_id == 77
        assert result.email == "maria@example.com"

    @responses_lib.activate
    def test_returns_none_when_name_not_found(self, client):
        responses_lib.add(
            responses_lib.GET,
            f"{HITOBITO_BASE}/api/people",
            json={"data": [], "meta": {}},
            status=200,
        )
        result = client.find_person_by_name("Unbekannte Person")
        assert result is None

    @responses_lib.activate
    def test_matches_name_with_umlauts(self, client):
        responses_lib.add(
            responses_lib.GET,
            f"{HITOBITO_BASE}/api/people",
            json={
                "data": [
                    {
                        "id": "88",
                        "type": "people",
                        "attributes": {
                            "first_name": "Jürgen",
                            "last_name": "Müller",
                            "email": "jm@example.com",
                        },
                    }
                ],
                "meta": {},
            },
            status=200,
        )
        result = client.find_person_by_name("Jürgen Müller")
        assert result is not None
        assert result.person_id == 88


class TestFindPeopleByRole:
    @responses_lib.activate
    def test_finds_people_with_matching_role(self, client):
        responses_lib.add(
            responses_lib.GET,
            f"{HITOBITO_BASE}/api/roles",
            json={
                "data": [
                    {
                        "id": "1",
                        "type": "roles",
                        "attributes": {"type": "Group::Stamm::ErfassungFuehrungszeugnis"},
                        "relationships": {"person": {"data": {"type": "people", "id": "5"}}},
                    }
                ],
                "included": [
                    {
                        "id": "5",
                        "type": "people",
                        "attributes": {
                            "first_name": "Klaus",
                            "last_name": "Weber",
                            "email": "kw@example.com",
                        },
                    }
                ],
                "links": {},
                "meta": {},
            },
            status=200,
        )
        results = client.find_people_by_role(group_id=1, role_type_contains="Führungszeugnis")
        assert len(results) == 1
        assert results[0].email == "kw@example.com"

    @responses_lib.activate
    def test_filters_by_role_type(self, client):
        responses_lib.add(
            responses_lib.GET,
            f"{HITOBITO_BASE}/api/roles",
            json={
                "data": [
                    {
                        "id": "1",
                        "type": "roles",
                        "attributes": {"type": "Group::Stamm::Mitglied"},
                        "relationships": {"person": {"data": {"type": "people", "id": "5"}}},
                    }
                ],
                "included": [
                    {
                        "id": "5",
                        "type": "people",
                        "attributes": {
                            "first_name": "Klaus",
                            "last_name": "Weber",
                            "email": "kw@example.com",
                        },
                    }
                ],
                "links": {},
                "meta": {},
            },
            status=200,
        )
        results = client.find_people_by_role(group_id=1, role_type_contains="Führungszeugnis")
        assert results == []

    @responses_lib.activate
    def test_follows_pagination_links(self, client):
        responses_lib.add(
            responses_lib.GET,
            f"{HITOBITO_BASE}/api/roles",
            json={
                "data": [
                    {
                        "id": "1",
                        "type": "roles",
                        "attributes": {"type": "Group::Stamm::ErfassungFuehrungszeugnis"},
                        "relationships": {"person": {"data": {"type": "people", "id": "1"}}},
                    }
                ],
                "included": [
                    {
                        "id": "1",
                        "type": "people",
                        "attributes": {"first_name": "A", "last_name": "B", "email": "a@b.com"},
                    }
                ],
                "links": {"next": f"{HITOBITO_BASE}/api/roles?page[number]=2"},
                "meta": {},
            },
            status=200,
        )
        responses_lib.add(
            responses_lib.GET,
            f"{HITOBITO_BASE}/api/roles",
            json={
                "data": [
                    {
                        "id": "2",
                        "type": "roles",
                        "attributes": {"type": "Group::Stamm::ErfassungFuehrungszeugnis"},
                        "relationships": {"person": {"data": {"type": "people", "id": "2"}}},
                    }
                ],
                "included": [
                    {
                        "id": "2",
                        "type": "people",
                        "attributes": {"first_name": "C", "last_name": "D", "email": "c@d.com"},
                    }
                ],
                "links": {},
                "meta": {},
            },
            status=200,
        )
        results = client.find_people_by_role(group_id=1, role_type_contains="Führungszeugnis")
        assert len(results) == 2
        assert {r.email for r in results} == {"a@b.com", "c@d.com"}


class TestGetEfzQualificationKindId:
    @responses_lib.activate
    def test_resolves_kind_id_by_label(self, client):
        responses_lib.add(
            responses_lib.GET,
            f"{HITOBITO_BASE}/api/qualification_kinds",
            json={
                "data": [
                    {
                        "id": "7",
                        "type": "qualification_kinds",
                        "attributes": {"label": "Erweitertes Führungszeugnis"},
                    }
                ],
                "meta": {},
            },
            status=200,
        )
        kind_id = client.get_efz_qualification_kind_id("Erweitertes Führungszeugnis")
        assert kind_id == 7

    @responses_lib.activate
    def test_returns_none_when_label_not_found(self, client):
        responses_lib.add(
            responses_lib.GET,
            f"{HITOBITO_BASE}/api/qualification_kinds",
            json={"data": [], "meta": {}},
            status=200,
        )
        kind_id = client.get_efz_qualification_kind_id("Nonexistent Label")
        assert kind_id is None

    @responses_lib.activate
    def test_caches_kind_id(self, client):
        responses_lib.add(
            responses_lib.GET,
            f"{HITOBITO_BASE}/api/qualification_kinds",
            json={
                "data": [
                    {
                        "id": "3",
                        "type": "qualification_kinds",
                        "attributes": {"label": "Erweitertes Führungszeugnis"},
                    }
                ],
                "meta": {},
            },
            status=200,
        )
        first = client.get_efz_qualification_kind_id("Erweitertes Führungszeugnis")
        second = client.get_efz_qualification_kind_id("Erweitertes Führungszeugnis")
        assert first == second == 3
        assert len(responses_lib.calls) == 1


class TestCreateEfzQualification:
    @responses_lib.activate
    def test_creates_qualification_record(self, client):
        from datetime import date

        responses_lib.add(
            responses_lib.POST,
            f"{HITOBITO_BASE}/api/qualifications",
            json={
                "data": {
                    "id": "99",
                    "type": "qualifications",
                    "attributes": {
                        "start_at": "2024-01-15",
                        "finish_at": "2029-01-15",
                        "origin": "Test origin",
                    },
                }
            },
            status=201,
        )
        result = client.create_efz_qualification(
            person_id=42,
            qualification_kind_id=7,
            start_at=date(2024, 1, 15),
            finish_at=date(2029, 1, 15),
            origin="Test origin",
        )
        assert result is not None
        assert isinstance(result, HitobitoQualification)
        assert result.id == 99
        assert result.person_id == 42

    @responses_lib.activate
    def test_returns_none_on_api_error(self, client):
        from datetime import date

        responses_lib.add(
            responses_lib.POST,
            f"{HITOBITO_BASE}/api/qualifications",
            json={"errors": [{"code": "forbidden"}]},
            status=403,
        )
        result = client.create_efz_qualification(
            person_id=42,
            qualification_kind_id=7,
            start_at=date(2024, 1, 15),
            finish_at=None,
        )
        assert result is None


class TestGetPersonQualifications:
    @responses_lib.activate
    def test_returns_qualifications_for_person(self, client):
        responses_lib.add(
            responses_lib.GET,
            f"{HITOBITO_BASE}/api/qualifications",
            json={
                "data": [
                    {
                        "id": "11",
                        "type": "qualifications",
                        "attributes": {
                            "start_at": "2023-05-01",
                            "finish_at": "2028-05-01",
                            "origin": "Vorgelegt",
                        },
                        "relationships": {
                            "qualification_kind": {
                                "data": {"type": "qualification_kinds", "id": "7"}
                            }
                        },
                    }
                ],
                "included": [
                    {
                        "id": "7",
                        "type": "qualification_kinds",
                        "attributes": {"label": "Erweitertes Führungszeugnis"},
                    }
                ],
                "meta": {},
            },
            status=200,
        )
        results = client.get_person_qualifications(person_id=42)
        assert len(results) == 1
        assert results[0].qualification_kind_label == "Erweitertes Führungszeugnis"
        assert results[0].start_at is not None
        assert results[0].start_at.year == 2023


class TestFindPersonByNameAndStreet:
    @responses_lib.activate
    def test_returns_single_match_without_address_fetch(self, client):
        responses_lib.add(
            responses_lib.GET,
            f"{HITOBITO_BASE}/api/people",
            json={
                "data": [
                    {
                        "id": "10",
                        "type": "people",
                        "attributes": {
                            "first_name": "Maria",
                            "last_name": "Muster",
                            "email": "maria@example.com",
                        },
                    }
                ],
                "links": {},
                "meta": {},
            },
            status=200,
        )
        result = client.find_person_by_name_and_street("Maria Muster", "Musterstraße 12")
        assert result is not None
        assert result.person_id == 10
        assert len(responses_lib.calls) == 1

    @responses_lib.activate
    def test_disambiguates_by_street_when_multiple_candidates(self, client):
        responses_lib.add(
            responses_lib.GET,
            f"{HITOBITO_BASE}/api/people",
            json={
                "data": [
                    {
                        "id": "1",
                        "type": "people",
                        "attributes": {
                            "first_name": "Anna",
                            "last_name": "Schmidt",
                            "email": "anna1@example.com",
                        },
                    },
                    {
                        "id": "2",
                        "type": "people",
                        "attributes": {
                            "first_name": "Anna",
                            "last_name": "Schmidt",
                            "email": "anna2@example.com",
                        },
                    },
                ],
                "links": {},
                "meta": {},
            },
            status=200,
        )
        responses_lib.add(
            responses_lib.GET,
            f"{HITOBITO_BASE}/api/people/1",
            json={
                "data": {
                    "id": "1",
                    "type": "people",
                    "attributes": {
                        "first_name": "Anna",
                        "last_name": "Schmidt",
                        "email": "anna1@example.com",
                        "street": "Andere Straße 99",
                    },
                }
            },
            status=200,
        )
        responses_lib.add(
            responses_lib.GET,
            f"{HITOBITO_BASE}/api/people/2",
            json={
                "data": {
                    "id": "2",
                    "type": "people",
                    "attributes": {
                        "first_name": "Anna",
                        "last_name": "Schmidt",
                        "email": "anna2@example.com",
                        "street": "Musterstraße 12",
                    },
                }
            },
            status=200,
        )
        result = client.find_person_by_name_and_street("Anna Schmidt", "Musterstraße 12")
        assert result is not None
        assert result.person_id == 2

    @responses_lib.activate
    def test_returns_none_when_ambiguous_and_no_street(self, client):
        responses_lib.add(
            responses_lib.GET,
            f"{HITOBITO_BASE}/api/people",
            json={
                "data": [
                    {
                        "id": "1",
                        "type": "people",
                        "attributes": {
                            "first_name": "Hans",
                            "last_name": "Meier",
                            "email": "hans1@example.com",
                        },
                    },
                    {
                        "id": "2",
                        "type": "people",
                        "attributes": {
                            "first_name": "Hans",
                            "last_name": "Meier",
                            "email": "hans2@example.com",
                        },
                    },
                ],
                "links": {},
                "meta": {},
            },
            status=200,
        )
        result = client.find_person_by_name_and_street("Hans Meier", None)
        assert result is None

    @responses_lib.activate
    def test_returns_none_when_no_candidate_matches_street(self, client):
        responses_lib.add(
            responses_lib.GET,
            f"{HITOBITO_BASE}/api/people",
            json={
                "data": [
                    {
                        "id": "1",
                        "type": "people",
                        "attributes": {
                            "first_name": "Klaus",
                            "last_name": "Weber",
                            "email": "kw1@example.com",
                        },
                    },
                    {
                        "id": "2",
                        "type": "people",
                        "attributes": {
                            "first_name": "Klaus",
                            "last_name": "Weber",
                            "email": "kw2@example.com",
                        },
                    },
                ],
                "links": {},
                "meta": {},
            },
            status=200,
        )
        responses_lib.add(
            responses_lib.GET,
            f"{HITOBITO_BASE}/api/people/1",
            json={
                "data": {
                    "id": "1",
                    "type": "people",
                    "attributes": {
                        "first_name": "Klaus",
                        "last_name": "Weber",
                        "street": "Bergstraße 1",
                    },
                }
            },
            status=200,
        )
        responses_lib.add(
            responses_lib.GET,
            f"{HITOBITO_BASE}/api/people/2",
            json={
                "data": {
                    "id": "2",
                    "type": "people",
                    "attributes": {
                        "first_name": "Klaus",
                        "last_name": "Weber",
                        "street": "Talweg 5",
                    },
                }
            },
            status=200,
        )
        result = client.find_person_by_name_and_street("Klaus Weber", "Kirchstraße 3")
        assert result is None


class TestDeleteQualification:
    @responses_lib.activate
    def test_deletes_qualification_by_id(self, client):
        responses_lib.add(
            responses_lib.DELETE,
            f"{HITOBITO_BASE}/api/qualifications/55",
            status=204,
        )
        client.delete_qualification(55)
        assert len(responses_lib.calls) == 1
        assert responses_lib.calls[0].request.method == "DELETE"
