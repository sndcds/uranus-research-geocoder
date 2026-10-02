import asyncio
from collections.abc import AsyncIterator

import httpx
import pytest
from conftest import AUTH, ClientFactory, assert_error, collection, feature

from uranus_research_geocoder import boundary


@pytest.mark.parametrize("length", ["-1", "abc", "1, 1"])
async def test_invalid_content_length(api: ClientFactory, length: str) -> None:
    async with api() as client:
        response = await client.post(
            "/search",
            headers={**AUTH, "Content-Length": length, "Content-Type": "application/json"},
            content=b"{}",
        )
        assert_error(response, 422, "invalid_request")


async def test_duplicate_headers(api: ClientFactory) -> None:
    async with api() as client:
        for extra in [
            [("Content-Length", "2"), ("Content-Length", "2")],
            [("Content-Type", "application/json"), ("Content-Type", "application/json")],
        ]:
            headers = list(AUTH.items()) + extra
            if extra[0][0] == "Content-Length":
                headers.append(("Content-Type", "application/json"))
            assert_error(
                await client.post("/search", headers=headers, content=b"{}"), 422, "invalid_request"
            )


async def test_slow_body_and_slot_recovery(
    api: ClientFactory, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(boundary, "BODY_TIMEOUT_SECONDS", 0.02)

    async def slow_body() -> AsyncIterator[bytes]:
        yield b'{"query":'
        await asyncio.Event().wait()

    async with api(concurrency=1) as client:
        async with asyncio.timeout(1):
            response = await client.post(
                "/search", headers={**AUTH, "Content-Type": "application/json"}, content=slow_body()
            )
        assert_error(response, 422, "invalid_request")
        assert (await client.post("/search", headers=AUTH, json={"query": "x"})).status_code == 200


async def test_cancelled_request_releases_slot(api: ClientFactory) -> None:
    entered = asyncio.Event()
    calls = 0

    async def handler(_: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        if calls == 1:
            entered.set()
            await asyncio.Event().wait()
        return httpx.Response(200, json=collection(feature()))

    async with api(handler, concurrency=1) as client:
        task = asyncio.create_task(client.post("/search", headers=AUTH, json={"query": "x"}))
        await asyncio.wait_for(entered.wait(), 1)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
        assert (await client.post("/search", headers=AUTH, json={"query": "x"})).status_code == 200
