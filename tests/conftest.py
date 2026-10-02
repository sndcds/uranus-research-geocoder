from collections.abc import AsyncIterator, Callable, Coroutine
from contextlib import AbstractAsyncContextManager, asynccontextmanager
from typing import Any

import httpx
import pytest
from pydantic import SecretStr

from uranus_research_geocoder.app import create_app
from uranus_research_geocoder.client import NominatimClient
from uranus_research_geocoder.config import Settings

KEY = "test-only-key-0123456789abcdefghij"
AUTH = {"Authorization": f"Bearer {KEY}"}
Handler = (
    Callable[[httpx.Request], httpx.Response]
    | Callable[[httpx.Request], Coroutine[None, None, httpx.Response]]
)
ClientFactory = Callable[..., AbstractAsyncContextManager[httpx.AsyncClient]]


def feature(**values: Any) -> dict[str, Any]:
    address_keys = {
        "city",
        "municipality",
        "state",
        "country",
        "country_code",
        "postcode",
        "county",
        "district",
    }
    aliases = {"label": "display_name", "street": "road", "housenumber": "house_number"}
    result: dict[str, Any] = {
        "display_name": "Bachstraße, Flensburg, Schleswig-Holstein, Deutschland",
        "osm_type": "way",
        "osm_id": 123456,
        "type": "street",
        "lat": "54.78",
        "lon": "9.43",
        "boundingbox": ["54.77", "54.79", "9.42", "9.44"],
        "address": {
            "road": "Bachstraße",
            "city": "Flensburg",
            "state": "Schleswig-Holstein",
            "postcode": "24937",
            "country": "Deutschland",
            "country_code": "de",
        },
    }
    for key, value in values.items():
        key = aliases.get(key, key)
        if key in address_keys or key in {"road", "house_number"}:
            result["address"][key] = value
        else:
            result[key] = value
    return result


def collection(*features: dict[str, Any]) -> list[dict[str, Any]]:
    return list(features)


@pytest.fixture
def api() -> ClientFactory:
    @asynccontextmanager
    async def factory(
        handler: Handler | None = None, *, concurrency: int = 4, deadline: float = 5
    ) -> AsyncIterator[httpx.AsyncClient]:
        settings = Settings(
            api_key=SecretStr(KEY), concurrency=concurrency, timeout_seconds=deadline
        )
        if handler is None:

            def handler(_: httpx.Request) -> httpx.Response:
                return httpx.Response(
                    200, json=feature() if _.url.path == "/reverse" else collection(feature())
                )

        upstream = NominatimClient(settings, transport=httpx.MockTransport(handler))
        app = create_app(settings, upstream)
        async with app.router.lifespan_context(app):
            async with httpx.AsyncClient(
                transport=httpx.ASGITransport(app=app), base_url="http://geocoder.test"
            ) as client:
                yield client

    return factory


def assert_error(response: httpx.Response, status: int, code: str) -> None:
    assert response.status_code == status
    assert response.json() == {"error": {"code": code}}
    assert response.headers["cache-control"] == "no-store"
