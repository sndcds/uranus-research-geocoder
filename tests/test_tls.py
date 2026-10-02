"""Real TLS over loopback: prove SNI, Host, CA verification and hostname checks."""

import asyncio
import ssl
from pathlib import Path

import pytest
import trustme
from conftest import KEY
from pydantic import SecretStr

from uranus_research_geocoder import client as client_module
from uranus_research_geocoder.client import NominatimClient
from uranus_research_geocoder.config import NOMINATIM_HOST, Settings
from uranus_research_geocoder.errors import UpstreamError


@pytest.mark.parametrize("certificate_case", ["valid", "wrong_hostname", "untrusted"])
async def test_real_tls(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, certificate_case: str
) -> None:
    ca = trustme.CA()
    ca_path = tmp_path / "ca.pem"
    ca.cert_pem.write_to_path(ca_path)
    monkeypatch.setattr("certifi.where", lambda: str(ca_path))
    issuer = trustme.CA() if certificate_case == "untrusted" else ca
    name = "wrong.invalid" if certificate_case == "wrong_hostname" else NOMINATIM_HOST
    cert = issuer.issue_cert(name)
    context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    cert.configure_cert(context)
    sni_names: list[str | None] = []
    requests: list[bytes] = []

    def sni_callback(
        socket: ssl.SSLSocket | ssl.SSLObject, server_name: str | None, ssl_context: object
    ) -> None:
        sni_names.append(server_name)

    context.set_servername_callback(sni_callback)

    async def handle(reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        try:
            requests.append(await reader.readuntil(b"\r\n\r\n"))
            writer.write(
                b"HTTP/1.1 200 OK\r\nContent-Type: application/json\r\n"
                b'Content-Length: 12\r\nConnection: close\r\n\r\n{"status":0}'
            )
            await writer.drain()
        finally:
            writer.close()
            await writer.wait_closed()

    server = await asyncio.start_server(handle, "127.0.0.1", 0, ssl=context)
    port = server.sockets[0].getsockname()[1]
    # Only the port is changed for the unprivileged local test server.
    monkeypatch.setattr(client_module, "LOCAL_ORIGIN", f"https://127.0.0.1:{port}")
    for key in ["HTTP_PROXY", "HTTPS_PROXY", "ALL_PROXY", "http_proxy", "https_proxy", "all_proxy"]:
        monkeypatch.setenv(key, "http://127.0.0.1:1")
    monkeypatch.setenv("NO_PROXY", "")
    monkeypatch.setenv("no_proxy", "")
    monkeypatch.setenv("SSL_CERT_FILE", "/nonexistent/ignored-ca.pem")
    monkeypatch.setenv("SSL_CERT_DIR", "/nonexistent/ignored-ca-dir")
    client = NominatimClient(Settings(api_key=SecretStr(KEY), timeout_seconds=2))
    async with server:
        try:
            if certificate_case == "valid":
                await client.ready()
                assert len(requests) == 1
                assert requests[0].startswith(b"GET /status?format=json HTTP/1.1\r\n")
                assert f"Host: {NOMINATIM_HOST}\r\n".encode() in requests[0]
            else:
                with pytest.raises(UpstreamError):
                    await client.ready()
                assert not requests
            assert sni_names == [NOMINATIM_HOST]
        finally:
            await client.close()
