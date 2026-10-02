import asyncio
from collections.abc import AsyncIterator
from typing import Any

import httpx
import pytest
from conftest import AUTH, ClientFactory, assert_error, collection, feature


async def test_health_without_upstream(api: ClientFactory) -> None:
    def fail(_: httpx.Request) -> httpx.Response:
        pytest.fail("health must not call Nominatim")

    async with api(fail) as client:
        response = await client.get("/health")
        assert response.json() == {"status": "ok"}
        assert response.headers["cache-control"] == "no-store"


@pytest.mark.parametrize("path", ["/ready", "/search", "/reverse", "/lookup", "/docs"])
@pytest.mark.parametrize(
    "headers", [{}, {"Authorization": "Bearer wrong"}, {"Authorization": "Basic x"}]
)
async def test_auth(api: ClientFactory, path: str, headers: dict[str, str]) -> None:
    async with api() as client:
        response = await client.request(
            "GET" if path == "/ready" else "POST", path, headers=headers
        )
        assert_error(response, 401, "unauthorized")


async def test_duplicate_auth(api: ClientFactory) -> None:
    async with api() as client:
        response = await client.get("/ready", headers=list(AUTH.items()) * 2)
        assert_error(response, 401, "unauthorized")


@pytest.mark.parametrize("query", ["Bachstraße Flensburg", "Bachstraße", "https://evil.test/?q=x"])
async def test_search(api: ClientFactory, query: str) -> None:
    calls: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(request)
        return httpx.Response(200, json=collection(feature(), feature(osm_id=456)))

    async with api(handler) as client:
        result = await client.post("/search", headers=AUTH, json={"query": query, "limit": 5})
    assert result.status_code == 200
    assert result.json() == {
        "query": query,
        "items": [
            {
                "display_name": "Bachstraße, Flensburg, Schleswig-Holstein, Deutschland",
                "latitude": 54.78,
                "longitude": 9.43,
                "osm_type": "way",
                "osm_id": osm_id,
                "place_type": "street",
                "administrative_level": "unknown",
                "administrative_levels": [],
                "country_code": "de",
                "address": {
                    "road": "Bachstraße",
                    "city": "Flensburg",
                    "state": "Schleswig-Holstein",
                    "postcode": "24937",
                    "country": "Deutschland",
                },
                "bbox": [54.77, 9.42, 54.79, 9.44],
            }
            for osm_id in [123456, 456]
        ],
    }
    assert len(calls) == 1
    request = calls[0]
    assert request.method == "GET"
    assert request.url.scheme == "https"
    assert request.url.host == "127.0.0.1"
    assert request.url.port in (None, 443)  # HTTPX normalizes the HTTPS default port.
    assert request.url.path == "/search"
    assert dict(request.url.params) == {
        "q": query,
        "format": "jsonv2",
        "extratags": "1",
        "addressdetails": "1",
        "limit": "5",
    }
    assert request.headers["host"] == "nominatim.oklabflensburg.de"
    assert request.extensions["sni_hostname"] == "nominatim.oklabflensburg.de"
    assert request.extensions["timeout"] == dict.fromkeys(["connect", "read", "write", "pool"], 5)
    assert "authorization" not in request.headers
    assert result.headers["cache-control"] == "no-store"


@pytest.mark.parametrize(
    "body",
    [
        {"query": ""},
        {"query": "  \n\t"},
        {"query": "x" * 301},
        {"query": "x", "limit": 6},
        {"query": "x", "limit": 0},
        {"query": "x", "limit": True},
        {"query": "x", "limit": "2"},
        {"query": 123},
        {"query": "\ud800"},
        {},
    ],
)
async def test_search_invalid(api: ClientFactory, body: dict[str, Any]) -> None:
    import json

    async with api() as client:
        response = await client.post(
            "/search",
            headers={**AUTH, "Content-Type": "application/json"},
            content=json.dumps(body),
        )
        assert_error(response, 422, "invalid_request")


@pytest.mark.parametrize("lat,lon", [(54.79, 9.43), (-90, -180), (90, 180), (0, 0)])
async def test_reverse(api: ClientFactory, lat: float, lon: float) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/reverse"
        assert dict(request.url.params) == {
            "lat": str(float(lat)),
            "lon": str(float(lon)),
            "format": "jsonv2",
            "extratags": "1",
            "addressdetails": "1",
        }
        return httpx.Response(200, json=feature(housenumber="1", municipality="Flensburg"))

    async with api(handler) as client:
        response = await client.post(
            "/reverse", headers=AUTH, json={"latitude": lat, "longitude": lon}
        )
        assert response.status_code == 200
        # Coordinates come from the result, never synthesized from the request.
        assert response.json()["latitude"] == 54.78
        assert response.json()["address"]["house_number"] == "1"
        assert response.json()["address"]["municipality"] == "Flensburg"


@pytest.mark.parametrize(
    "lat,lon", [(90.01, 0), (-90.01, 0), (0, 180.01), (0, -180.01), (True, 0), ("54", 0), (None, 0)]
)
async def test_reverse_invalid(api: ClientFactory, lat: Any, lon: Any) -> None:
    async with api() as client:
        response = await client.post(
            "/reverse", headers=AUTH, json={"latitude": lat, "longitude": lon}
        )
        assert_error(response, 422, "invalid_request")


@pytest.mark.parametrize("kind", ["N", "W", "R"])
async def test_lookup(api: ClientFactory, kind: str) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/lookup"
        assert dict(request.url.params) == {
            "osm_ids": f"{kind}123456",
            "format": "jsonv2",
            "extratags": "1",
            "addressdetails": "1",
        }
        return httpx.Response(200, json=collection(feature()))

    async with api(handler) as client:
        response = await client.post(
            "/lookup", headers=AUTH, json={"osm_type": kind, "osm_id": 123456}
        )
        assert response.status_code == 200
        assert response.json()["osm_id"] == 123456


@pytest.mark.parametrize(
    "kind,identifier",
    [
        ("X", 1),
        ("w", 1),
        ("way", 1),
        ("W", 0),
        ("W", -1),
        ("W", 1.1),
        ("W", True),
        ("W", "123"),
        ("W", 1.0),
    ],
)
async def test_lookup_invalid(api: ClientFactory, kind: str, identifier: Any) -> None:
    async with api() as client:
        response = await client.post(
            "/lookup", headers=AUTH, json={"osm_type": kind, "osm_id": identifier}
        )
        assert_error(response, 422, "invalid_request")


@pytest.mark.parametrize("extra", ["url", "host", "path", "format", "osm_ids", "params", "limitx"])
async def test_no_free_parameters(api: ClientFactory, extra: str) -> None:
    async with api() as client:
        response = await client.post(
            "/search", headers=AUTH, json={"query": "x", extra: "https://evil.test"}
        )
        assert_error(response, 422, "invalid_request")


@pytest.mark.parametrize("path", ["/health", "/ready", "/search", "/reverse", "/lookup"])
async def test_no_query_strings(api: ClientFactory, path: str) -> None:
    async with api() as client:
        response = await client.request(
            "GET" if path in ("/health", "/ready") else "POST",
            path + "?q=secret",
            headers=AUTH,
            json={},
        )
        assert_error(response, 422, "invalid_request")


@pytest.mark.parametrize("encoding", ["gzip", "identity", "br"])
async def test_no_content_encoding(api: ClientFactory, encoding: str) -> None:
    async with api() as client:
        response = await client.post(
            "/search", headers={**AUTH, "Content-Encoding": encoding}, json={"query": "x"}
        )
        assert_error(response, 422, "invalid_request")


@pytest.mark.parametrize("content_type", [None, "text/plain", "application/x-www-form-urlencoded"])
async def test_json_required(api: ClientFactory, content_type: str | None) -> None:
    headers = dict(AUTH)
    if content_type:
        headers["Content-Type"] = content_type
    async with api() as client:
        response = await client.post("/search", headers=headers, content=b'{"query":"x"}')
        assert_error(response, 422, "invalid_request")


async def test_body_limit_streamed_and_declared(api: ClientFactory) -> None:
    async def chunks() -> AsyncIterator[bytes]:
        for _ in range(17):
            yield b" " * 1024

    headers = {**AUTH, "Content-Type": "application/json"}
    async with api() as client:
        bodies: list[bytes | AsyncIterator[bytes]] = [b" " * 16385, chunks()]
        for body in bodies:
            response = await client.post("/search", headers=headers, content=body)
            assert_error(response, 413, "request_too_large")
        body_at_limit = b'{"query":"x"}' + b" " * (16384 - len(b'{"query":"x"}'))
        assert (
            await client.post("/search", headers=headers, content=body_at_limit)
        ).status_code == 200


@pytest.mark.parametrize("path", ["/search/", "/docs", "/openapi.json", "/missing"])
async def test_no_redirect_or_extra_routes(api: ClientFactory, path: str) -> None:
    async with api() as client:
        response = await client.get(path, headers=AUTH)
        assert_error(response, 422, "invalid_request")
        assert "location" not in response.headers


async def test_concurrency_and_recovery(api: ClientFactory) -> None:
    entered, release = asyncio.Event(), asyncio.Event()

    async def handler(_: httpx.Request) -> httpx.Response:
        entered.set()
        await release.wait()
        return httpx.Response(200, json=collection(feature()))

    async with api(handler, concurrency=1) as client:
        task = asyncio.create_task(client.post("/search", headers=AUTH, json={"query": "x"}))
        try:
            await asyncio.wait_for(entered.wait(), 2)
            assert_error(await client.get("/ready", headers=AUTH), 503, "busy")
            assert (await client.get("/health")).status_code == 200
        finally:
            release.set()
            assert (await task).status_code == 200
        assert (await client.post("/search", headers=AUTH, json={"query": "x"})).status_code == 200
