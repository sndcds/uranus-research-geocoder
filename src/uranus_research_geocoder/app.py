from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from pydantic import ValidationError
from starlette.exceptions import HTTPException
from starlette.responses import JSONResponse

from .boundary import Boundary
from .client import NominatimClient
from .config import Settings
from .errors import NoMatch, UpstreamError, error
from .models import LookupRequest, Place, ReverseRequest, SearchRequest, SearchResponse


def create_app(settings: Settings | None = None, client: NominatimClient | None = None) -> FastAPI:
    try:
        settings = settings or Settings()
    except ValidationError:
        raise RuntimeError("Invalid geocoder configuration") from None
    upstream = client or NominatimClient(settings)

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        try:
            yield
        finally:
            await upstream.close()

    app = FastAPI(
        title="Kulturbytes Research Geocoder",
        lifespan=lifespan,
        docs_url=None,
        redoc_url=None,
        openapi_url=None,
        redirect_slashes=False,
    )
    app.add_middleware(Boundary, settings=settings)

    @app.exception_handler(RequestValidationError)
    async def invalid(_request: Request, _exc: RequestValidationError) -> JSONResponse:
        return error("invalid_request", 422)

    @app.exception_handler(HTTPException)
    async def invalid_route(_request: Request, _exc: HTTPException) -> JSONResponse:
        return error("invalid_request", 422)

    @app.exception_handler(UpstreamError)
    async def unavailable(_request: Request, _exc: UpstreamError) -> JSONResponse:
        return error("geocoder_unavailable", 503)

    @app.exception_handler(NoMatch)
    async def no_match(_request: Request, _exc: NoMatch) -> JSONResponse:
        return error("no_match", 404)

    @app.get("/health")
    async def health() -> dict[str, str]:
        return {"status": "ok"}

    @app.get("/ready")
    async def ready() -> dict[str, str]:
        await upstream.ready()
        return {"status": "ready"}

    @app.post("/search", response_model=SearchResponse, response_model_exclude_none=True)
    async def search(body: SearchRequest) -> SearchResponse:
        return SearchResponse(query=body.query, items=await upstream.search(body))

    @app.post("/reverse", response_model=Place, response_model_exclude_none=True)
    async def reverse(body: ReverseRequest) -> Place:
        return await upstream.reverse(body)

    @app.post("/lookup", response_model=Place, response_model_exclude_none=True)
    async def lookup(body: LookupRequest) -> Place:
        return await upstream.lookup(body)

    return app
