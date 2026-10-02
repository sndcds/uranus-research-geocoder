import asyncio
import json
import logging
from collections.abc import AsyncIterator
from typing import Any

import httpx
import pytest
from conftest import AUTH, ClientFactory, assert_error, collection, feature

from uranus_research_geocoder.client import MAX_RESPONSE_BYTES


@pytest.mark.parametrize(
    "payload,success",
    [
        ({"status": 0}, True),
        ({"status": 700}, False),
        ({"status": False}, False),
        ({"status": "0"}, False),
        ({}, False),
        ([], False),
    ],
)
async def test_ready(api: ClientFactory, payload: Any, success: bool) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/status"
        assert dict(request.url.params) == {"format": "json"}
        return httpx.Response(200, json=payload)

    async with api(handler) as client:
        response = await client.get("/ready", headers=AUTH)
        if success:
            assert response.status_code == 200
            assert response.json() == {"status": "ready"}
        else:
            assert_error(response, 503, "geocoder_unavailable")


@pytest.mark.parametrize(
    "status,headers,content",
    [
        (500, {"Content-Type": "application/json"}, b'{"secret":"upstream-secret"}'),
        (200, {"Content-Type": "application/json"}, b"upstream-secret"),
        (200, {"Content-Type": "text/html"}, b"upstream-secret"),
        (200, {}, b"{}"),
        (302, {"Location": "https://external.invalid/secret"}, b"upstream-secret"),
        (307, {"Location": "https://127.0.0.1/other"}, b"upstream-secret"),
        (
            200,
            {
                "Content-Type": "application/json",
                "Content-Encoding": "identity",
                "Content-Length": str(MAX_RESPONSE_BYTES + 1),
            },
            b"{}",
        ),
        (200, {"Content-Type": "application/json"}, b" " * (MAX_RESPONSE_BYTES + 1)),
        (200, {"Content-Type": "application/json"}, b'{"coordinates":NaN}'),
        (200, {"Content-Type": "application/json"}, b"[" * 2000 + b"]" * 2000),
        (200, {"Content-Type": "application/json", "Content-Length": "bogus"}, b"{}"),
        (404, {"Content-Type": "application/json"}, b'{"secret":"upstream-secret"}'),
    ],
)
async def test_upstream_failures(
    api: ClientFactory, status: int, headers: dict[str, str], content: bytes
) -> None:
    calls = 0

    def handler(_: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        return httpx.Response(status, headers=headers, content=content)

    async with api(handler) as client:
        response = await client.post("/search", headers=AUTH, json={"query": "private-query"})
        assert_error(response, 503, "geocoder_unavailable")
        assert "secret" not in response.text
        assert "private-query" not in response.text
        assert calls == 1


@pytest.mark.parametrize(
    "exception", [httpx.ReadTimeout, httpx.ConnectError, httpx.RemoteProtocolError]
)
async def test_transport_failures(api: ClientFactory, exception: type[httpx.HTTPError]) -> None:
    def handler(_: httpx.Request) -> httpx.Response:
        raise exception("private-query upstream-secret")

    async with api(handler) as client:
        assert_error(await client.get("/ready", headers=AUTH), 503, "geocoder_unavailable")


class SlowStream(httpx.AsyncByteStream):
    async def __aiter__(self) -> AsyncIterator[bytes]:
        while True:
            yield b" "
            await asyncio.sleep(0.01)


async def test_total_deadline(api: ClientFactory) -> None:
    def handler(_: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200, headers={"Content-Type": "application/json"}, stream=SlowStream()
        )

    async with api(handler, deadline=0.04, concurrency=1) as client:
        async with asyncio.timeout(1):
            assert_error(await client.get("/ready", headers=AUTH), 503, "geocoder_unavailable")
            # Also proves the semaphore was released after timeout.
            assert_error(await client.get("/ready", headers=AUTH), 503, "geocoder_unavailable")


class OversizedStream(httpx.AsyncByteStream):
    def __init__(self) -> None:
        self.chunks = 0
        self.closed = False

    async def __aiter__(self) -> AsyncIterator[bytes]:
        for _ in range(100):
            self.chunks += 1
            yield b" " * 16384

    async def aclose(self) -> None:
        self.closed = True


async def test_stream_size_is_bounded(api: ClientFactory) -> None:
    stream = OversizedStream()
    async with api(
        lambda _: httpx.Response(200, headers={"Content-Type": "application/json"}, stream=stream)
    ) as client:
        assert_error(await client.get("/ready", headers=AUTH), 503, "geocoder_unavailable")
    assert stream.chunks == 17
    assert stream.closed


async def test_compressed_upstream_rejected(api: ClientFactory) -> None:
    import gzip

    async with api(
        lambda _: httpx.Response(
            200,
            headers={"Content-Type": "application/json", "Content-Encoding": "gzip"},
            content=gzip.compress(b" " * (MAX_RESPONSE_BYTES * 2)),
        )
    ) as client:
        assert_error(await client.get("/ready", headers=AUTH), 503, "geocoder_unavailable")


@pytest.mark.parametrize(
    "path,body",
    [
        ("/search", {"query": "x"}),
        ("/reverse", {"latitude": 0, "longitude": 0}),
        ("/lookup", {"osm_type": "N", "osm_id": 1}),
    ],
)
async def test_empty_results(api: ClientFactory, path: str, body: dict[str, Any]) -> None:
    async with api(lambda _: httpx.Response(200, json=collection())) as client:
        assert_error(await client.post(path, headers=AUTH, json=body), 404, "no_match")


@pytest.mark.parametrize("status", [200, 404])
async def test_reverse_no_match(api: ClientFactory, status: int) -> None:
    async with api(lambda _: httpx.Response(status, json={"error": "Unable to geocode"})) as client:
        assert_error(
            await client.post("/reverse", headers=AUTH, json={"latitude": 0, "longitude": 0}),
            404,
            "no_match",
        )


@pytest.mark.parametrize(
    "payload",
    [
        None,
        [],
        {},
        {"error": "secret"},
        {"type": "FeatureCollection", "features": [{}]},
        collection(feature(osm_id=True)),
        collection(feature(label=123)),
        collection(*[feature()] * 6),
    ],
)
async def test_unexpected_schema(api: ClientFactory, payload: Any) -> None:
    async with api(lambda _: httpx.Response(200, json=payload)) as client:
        response = await client.post("/search", headers=AUTH, json={"query": "x"})
        assert_error(response, 503, "geocoder_unavailable")


@pytest.mark.parametrize(
    "key,value",
    [
        ("geometry", {"type": "Point", "coordinates": [181, 0]}),
        ("geometry", {"type": "Point", "coordinates": [0, 91]}),
        ("geometry", {"type": "Point", "coordinates": [True, 1]}),
        ("geometry", {"type": "Polygon", "coordinates": []}),
        ("bbox", [0, -91, 1, 90]),
        ("bbox", [0, 80, 1, 70]),
        ("bbox", [0, 1]),
    ],
)
async def test_invalid_geometry(api: ClientFactory, key: str, value: Any) -> None:
    item = feature()
    item[key] = value
    async with api(lambda _: httpx.Response(200, json=collection(item))) as client:
        assert_error(
            await client.post("/search", headers=AUTH, json={"query": "x"}),
            503,
            "geocoder_unavailable",
        )


async def test_missing_fields_and_unknown_fields(api: ClientFactory) -> None:
    payload = collection(
        {
            "type": "Feature",
            "properties": {
                "geocoding": {
                    "label": "Bachstraße",
                    "admin": {"level6": "Not a guessed municipality"},
                    "extra": {"private": "secret"},
                    "name": "Not a guessed street",
                }
            },
        }
    )
    async with api(lambda _: httpx.Response(200, json=payload)) as client:
        response = await client.post("/reverse", headers=AUTH, json={"latitude": 1, "longitude": 2})
        assert response.json() == {"display_name": "Bachstraße"}


async def test_result_count_respects_limit(api: ClientFactory) -> None:
    async with api(lambda _: httpx.Response(200, json=collection(feature(), feature()))) as client:
        response = await client.post("/search", headers=AUTH, json={"query": "x", "limit": 1})
        assert_error(response, 503, "geocoder_unavailable")


async def test_no_sensitive_logs(api: ClientFactory, caplog: pytest.LogCaptureFixture) -> None:
    caplog.set_level(logging.DEBUG)
    async with api() as client:
        await client.post("/search", headers=AUTH, json={"query": "private-query"})
        await client.post("/search", headers=AUTH, content="private-invalid-json")
        await client.post(
            "/reverse", headers=AUTH, json={"latitude": 54.790123, "longitude": 9.431234}
        )
    assert not caplog.records


async def test_malformed_inbound_json(api: ClientFactory) -> None:
    async with api() as client:
        for body in [
            b'{"query":',
            b"\xff",
            json.dumps({"latitude": float("nan"), "longitude": 0}).encode(),
        ]:
            response = await client.post(
                "/reverse", headers={**AUTH, "Content-Type": "application/json"}, content=body
            )
            assert_error(response, 422, "invalid_request")
