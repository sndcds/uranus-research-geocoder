from typing import Literal

from starlette.responses import JSONResponse

ErrorCode = Literal[
    "unauthorized",
    "invalid_request",
    "request_too_large",
    "busy",
    "geocoder_unavailable",
    "no_match",
]


class UpstreamError(Exception):
    """Upstream unavailable; never contains upstream payloads or request data."""


class NoMatch(Exception):
    """A valid upstream response contained no place."""


def error(code: ErrorCode, status: int) -> JSONResponse:
    headers = {"Cache-Control": "no-store"}
    if status == 401:
        headers["WWW-Authenticate"] = "Bearer"
    return JSONResponse({"error": {"code": code}}, status_code=status, headers=headers)
