from typing import Any

import httpx
import pytest
from conftest import AUTH, ClientFactory, feature

from uranus_research_geocoder.normalization import normalize


def area(name: str, admin_level: str, code: str | None = None) -> dict[str, Any]:
    tags = {"admin_level": admin_level}
    if code is not None:
        tags["de:amtlicher_gemeindeschluessel"] = code
    return feature(
        name=name,
        osm_type="relation",
        category="boundary",
        type="administrative",
        extratags=tags,
    )


@pytest.mark.parametrize(
    "name,raw,code,expected",
    [
        ("Schleswig-Holstein", "4", "01", "state"),
        ("Schleswig-Flensburg", "6", "01059", "district"),
        ("Flensburg", "6", "01001000", "municipality"),
        ("Deutschland", "2", None, "country"),
        ("Gemeinde", "8", None, "municipality"),
        ("Kreis invented", "99", None, "unknown"),
    ],
)
def test_metadata(name: str, raw: str, code: str | None, expected: str) -> None:
    result = normalize([area(name, raw, code)], 5)[0]
    assert result.administrative_level == expected
    assert result.official_code == code
    if name == "Flensburg":
        assert set(result.administrative_levels) == {"municipality", "district"}


def test_country_specific_mapping_and_no_name_guess() -> None:
    candidate = area("Kreis Schleswig", "6")
    candidate["address"]["country_code"] = "dk"
    result = normalize([candidate], 5)[0]
    assert result.administrative_level == "unknown"
    candidate["address"] = {}
    assert normalize([candidate], 5)[0].administrative_level == "unknown"
    assert normalize([feature(name="Kreis Schleswig")], 5)[0].administrative_level == "unknown"


async def test_ambiguous_candidates_and_wrong_requested_level(api: ClientFactory) -> None:
    candidates = [area("Schleswig", "8"), area("Schleswig", "6")]
    async with api(lambda _: httpx.Response(200, json=candidates)) as client:
        response = await client.post("/search", headers=AUTH, json={"query": "Schleswig"})
        assert [p["administrative_level"] for p in response.json()["items"]] == [
            "municipality",
            "district",
        ]
        # The gateway describes candidates, never coerces a caller's expected level.
        response = await client.post(
            "/search", headers=AUTH, json={"query": "Schleswig", "area_level": "state"}
        )
        assert response.status_code == 422


async def test_boundary_is_explicit_lookup_only(api: ClientFactory) -> None:
    polygon = {"type": "Polygon", "coordinates": [[[9, 54], [10, 54], [10, 55], [9, 54]]]}
    calls: list[dict[str, str]] = []

    def respond(request: httpx.Request) -> httpx.Response:
        calls.append(dict(request.url.params))
        result = area("Testgebiet", "4")
        if "polygon_geojson" in request.url.params:
            result["geojson"] = polygon
        return httpx.Response(200, json=[result])

    async with api(respond) as client:
        plain = await client.post("/search", headers=AUTH, json={"query": "Testgebiet"})
        assert "boundary" not in plain.json()["items"][0]
        boundary = await client.post(
            "/lookup",
            headers=AUTH,
            json={"osm_type": "R", "osm_id": 123456, "include_boundary": True},
        )
        assert boundary.json()["boundary"] == polygon
    assert "polygon_geojson" not in calls[0]
    assert calls[1]["polygon_geojson"] == "1"


@pytest.mark.parametrize(
    "geometry",
    [
        {"type": "Point", "coordinates": [9, 54]},
        {"type": "Polygon", "coordinates": [[[9, 54], [10, 54], [10, 55], [9, 55]]]},
        {"type": "Polygon", "coordinates": [[[999, 54], [10, 54], [10, 55], [999, 54]]]},
    ],
)
async def test_invalid_boundary_not_reflected(api: ClientFactory, geometry: dict[str, Any]) -> None:
    candidate = area("private-provider-name", "4") | {"geojson": geometry}
    async with api(lambda _: httpx.Response(200, json=[candidate])) as client:
        response = await client.post(
            "/lookup",
            headers=AUTH,
            json={"osm_type": "R", "osm_id": 123456, "include_boundary": True},
        )
        assert response.status_code == 503
        assert "private-provider-name" not in response.text
