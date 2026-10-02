import asyncio
import hmac

from starlette.types import ASGIApp, Message, Receive, Scope, Send

from .config import Settings
from .errors import error

MAX_BODY_BYTES = 16 * 1024
BODY_TIMEOUT_SECONDS = 5


class Boundary:
    def __init__(self, app: ASGIApp, settings: Settings) -> None:
        self.app = app
        self._expected = ("Bearer " + settings.api_key.get_secret_value()).encode("ascii")
        self._slots = asyncio.Semaphore(settings.concurrency)

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        started = False

        async def safe_send(message: Message) -> None:
            nonlocal started
            if message["type"] == "http.response.start":
                started = True
                message["headers"] = [
                    (key, value)
                    for key, value in message.get("headers", [])
                    if key.lower() != b"cache-control"
                ] + [(b"cache-control", b"no-store")]
            await send(message)

        headers: dict[bytes, list[bytes]] = {}
        for key, value in scope["headers"]:
            headers.setdefault(key.lower(), []).append(value)
        health = scope["path"] == "/health" and scope["method"] == "GET"
        auth = headers.get(b"authorization", [])
        if not health and (len(auth) != 1 or not hmac.compare_digest(auth[0], self._expected)):
            await error("unauthorized", 401)(scope, receive, safe_send)
            return
        if scope.get("query_string") or b"content-encoding" in headers:
            await error("invalid_request", 422)(scope, receive, safe_send)
            return
        if scope["method"] == "POST":
            types = headers.get(b"content-type", [])
            if len(types) != 1 or types[0].split(b";")[0].strip().lower() != b"application/json":
                await error("invalid_request", 422)(scope, receive, safe_send)
                return
        lengths = headers.get(b"content-length", [])
        if len(lengths) > 1 or (lengths and not lengths[0].isdigit()):
            await error("invalid_request", 422)(scope, receive, safe_send)
            return
        # Avoid converting attacker-controlled huge decimal strings to integers.
        if lengths and (len(lengths[0]) > 6 or int(lengths[0]) > MAX_BODY_BYTES):
            await error("request_too_large", 413)(scope, receive, safe_send)
            return
        if not health and self._slots.locked():
            await error("busy", 503)(scope, receive, safe_send)
            return
        if not health:
            # acquire() completes without yielding while a slot is available.
            await self._slots.acquire()
        try:
            body = bytearray()
            try:
                async with asyncio.timeout(BODY_TIMEOUT_SECONDS):
                    while True:
                        message = await receive()
                        if message["type"] == "http.disconnect":
                            return
                        chunk = message.get("body", b"")
                        if len(body) + len(chunk) > MAX_BODY_BYTES:
                            await error("request_too_large", 413)(scope, receive, safe_send)
                            return
                        body.extend(chunk)
                        if not message.get("more_body", False):
                            break
            except TimeoutError:
                await error("invalid_request", 422)(scope, receive, safe_send)
                return
            if (lengths and int(lengths[0]) != len(body)) or (scope["method"] != "POST" and body):
                await error("invalid_request", 422)(scope, receive, safe_send)
                return
            consumed = False

            async def replay() -> Message:
                nonlocal consumed
                if consumed:
                    return await receive()
                consumed = True
                return {"type": "http.request", "body": bytes(body), "more_body": False}

            try:
                await self.app(scope, replay, safe_send)
            except Exception:
                # No exception text/traceback: parser failures may contain sensitive inputs.
                if started:
                    raise
                await error("geocoder_unavailable", 503)(scope, receive, safe_send)
        finally:
            if not health:
                self._slots.release()
