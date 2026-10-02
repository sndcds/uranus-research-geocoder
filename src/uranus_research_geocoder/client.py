"""Only four fixed operations, using loopback TCP and verified hostname TLS."""

import asyncio
import json
import logging
import ssl

import httpx
from pydantic import ValidationError

from .config import LOCAL_ORIGIN, NOMINATIM_HOST, Settings
from .errors import NoMatch, UpstreamError
from .models import LookupRequest, Place, ReverseRequest, SearchRequest
from .normalization import Status, normalize

MAX_RESPONSE_BYTES = 256 * 1024
MAX_BOUNDARY_BYTES = 8 * 1024 * 1024


def _reject_constant(value: str) -> None:
    raise ValueError("Invalid JSON constant")


class NominatimClient:
    def __init__(
        self, settings: Settings, *, transport: httpx.AsyncBaseTransport | None = None
    ) -> None:
        self._timeout = settings.timeout_seconds
        # HTTPX logs request URLs at INFO; those contain location data.
        logging.getLogger("httpx").disabled = True
        logging.getLogger("httpcore").setLevel(logging.CRITICAL + 1)
        if transport is None:
            # Ignore SSL_CERT_FILE / SSL_CERT_DIR as well as proxy environment variables.
            import certifi

            context = ssl.create_default_context(cafile=certifi.where())
            transport = httpx.AsyncHTTPTransport(
                verify=context,
                trust_env=False,
                retries=0,
                limits=httpx.Limits(
                    max_connections=settings.concurrency,
                    max_keepalive_connections=settings.concurrency,
                ),
            )
        self._http = httpx.AsyncClient(
            transport=transport,
            trust_env=False,
            follow_redirects=False,
            timeout=httpx.Timeout(self._timeout),
            headers={
                "Host": NOMINATIM_HOST,
                "Accept": "application/json",
                "Accept-Encoding": "identity",
                "User-Agent": "uranus-research-geocoder/0.1.0",
            },
        )

    async def close(self) -> None:
        await self._http.aclose()

    async def ready(self) -> None:
        payload = await self._request(None)
        try:
            status = Status.model_validate(payload)
        except ValidationError:
            raise UpstreamError from None
        if status.status != 0:
            raise UpstreamError

    async def search(self, request: SearchRequest) -> list[Place]:
        return normalize(await self._request(request), request.limit)

    async def reverse(self, request: ReverseRequest) -> Place:
        return normalize(await self._request(request), 1, reverse=True)[0]

    async def lookup(self, request: LookupRequest) -> Place:
        return normalize(await self._request(request), 1, boundary=request.include_boundary)[0]

    async def _request(
        self, request: SearchRequest | ReverseRequest | LookupRequest | None
    ) -> object:
        # Paths and every parameter name are fixed here, never accepted from callers.
        params: dict[str, str | int | float] = {
            "format": "jsonv2",
            "addressdetails": 1,
            "extratags": 1,
        }
        match request:
            case SearchRequest():
                path = "/search"
                params.update(q=request.query, limit=request.limit)
            case ReverseRequest():
                path = "/reverse"
                params.update(lat=request.latitude, lon=request.longitude)
            case LookupRequest():
                path = "/lookup"
                params.update(osm_ids=f"{request.osm_type}{request.osm_id}")
                if request.include_boundary:
                    params["polygon_geojson"] = 1
            case None:
                path = "/status"
                params = {"format": "json"}
            case _:
                raise UpstreamError
        maximum = (
            MAX_BOUNDARY_BYTES
            if isinstance(request, LookupRequest) and request.include_boundary
            else MAX_RESPONSE_BYTES
        )
        try:
            # Total deadline, including headers and all chunks (not just idle read timeout).
            async with asyncio.timeout(self._timeout):
                self._http.cookies.clear()
                async with self._http.stream(
                    "GET",
                    LOCAL_ORIGIN + path,
                    params=params,
                    extensions={"sni_hostname": NOMINATIM_HOST},
                ) as response:
                    if response.status_code != 200 and not (
                        path == "/reverse" and response.status_code == 404
                    ):
                        raise UpstreamError
                    media_type = response.headers.get("content-type", "").split(";")[0]
                    if media_type.strip().lower() not in (
                        "application/json",
                        "application/geocode+json",
                        "application/geo+json",
                    ):
                        raise UpstreamError
                    if response.headers.get("content-encoding", "identity").lower() != "identity":
                        raise UpstreamError
                    length = response.headers.get("content-length")
                    if length is not None and (not length.isascii() or not length.isdecimal()):
                        raise UpstreamError
                    if length is not None and int(length) > maximum:
                        raise UpstreamError
                    body = bytearray()
                    async for chunk in response.aiter_bytes():
                        if len(body) + len(chunk) > maximum:
                            raise UpstreamError
                        body.extend(chunk)
                    payload: object = json.loads(body, parse_constant=_reject_constant)
                    # Nominatim's documented reverse failure is not a place response.
                    if path == "/reverse" and payload == {"error": "Unable to geocode"}:
                        raise NoMatch
                    if response.status_code != 200:
                        raise UpstreamError
                    return payload
        except (httpx.HTTPError, TimeoutError, ValueError, RecursionError):
            raise UpstreamError from None
        finally:
            self._http.cookies.clear()
