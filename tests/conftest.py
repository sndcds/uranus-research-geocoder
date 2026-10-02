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
    return {
        "type": "Feature",
        "geometry": {"type": "Point", "coordinates": [9.43, 54.78]},
        "bbox": [9.42, 54.77, 9.44, 54.79],
        "properties": {
            "geocoding": {
                "label": "Bachstraße, Flensburg, Schleswig-Holstein, Deutschland",
                "osm_type": "way",
                "osm_id": 123456,
                "type": "street",
                "country_code": "de",
                "street": "Bachstraße",
                "city": "Flensburg",
                "state": "Schleswig-Holstein",
                "postcode": "24937",
                "country": "Deutschland",
                **values,
            }
        },
    }


def collection(*features: dict[str, Any]) -> dict[str, Any]:
    return {"type": "FeatureCollection", "features": list(features)}


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
                return httpx.Response(200, json=collection(feature()))

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
