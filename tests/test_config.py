import pytest
from conftest import KEY
from pydantic import SecretStr, ValidationError

from uranus_research_geocoder.app import create_app
from uranus_research_geocoder.config import NOMINATIM_BASE_URL, Settings


@pytest.mark.parametrize(
    "url",
    [
        "http://nominatim.oklabflensburg.de",
        "https://evil.test",
        "https://127.0.0.1",
        NOMINATIM_BASE_URL + ":443",
        NOMINATIM_BASE_URL + ":444",
        NOMINATIM_BASE_URL + "/",
        NOMINATIM_BASE_URL + "/prefix",
        NOMINATIM_BASE_URL + "?",
        NOMINATIM_BASE_URL + "?a=1",
        NOMINATIM_BASE_URL + "#",
        NOMINATIM_BASE_URL + "#a",
        NOMINATIM_BASE_URL + ".",
        "https://user:password@nominatim.oklabflensburg.de",
        "https://NOMINATIM.oklabflensburg.de",
        " " + NOMINATIM_BASE_URL,
        NOMINATIM_BASE_URL + "\n",
        "https://nominatim.oklabflensburg.de@evil.test",
    ],
)
def test_base_url_exact(url: str) -> None:
    with pytest.raises(ValidationError):
        Settings(api_key=SecretStr(KEY), nominatim_base_url=url)


@pytest.mark.parametrize(
    "key",
    [
        "x" * 31,
        "x" * 4097,
        "x" * 31 + " ",
        "x" * 31 + "\t",
        "x" * 31 + "\n",
        "x" * 31 + "ä",
        "x" * 31 + "\x7f",
    ],
)
def test_invalid_key_redacted(key: str) -> None:
    with pytest.raises(ValidationError) as caught:
        Settings(api_key=key)
    assert key not in str(caught.value)
    assert "input_value" not in str(caught.value)


@pytest.mark.parametrize("length", [32, 4096])
def test_valid_keys(length: int) -> None:
    settings = Settings(api_key=SecretStr("!" * length))
    assert "!" * length not in repr(settings)


def test_environment(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("GEOCODER_API_KEY", KEY)
    monkeypatch.setenv("GEOCODER_TIMEOUT_SECONDS", "2")
    monkeypatch.setenv("GEOCODER_CONCURRENCY", "3")
    monkeypatch.setenv("GEOCODER_NOMINATIM_BASE_URL", NOMINATIM_BASE_URL)
    settings = Settings()
    assert settings.timeout_seconds == 2
    assert settings.concurrency == 3
    assert settings.api_key.get_secret_value() == KEY


def test_startup_failure_redacted(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("GEOCODER_API_KEY", "secret-invalid")
    with pytest.raises(RuntimeError, match="^Invalid geocoder configuration$"):
        create_app()


@pytest.mark.parametrize(
    "field,value",
    [
        ("timeout_seconds", 0),
        ("timeout_seconds", 31),
        ("timeout_seconds", float("nan")),
        ("concurrency", 0),
        ("concurrency", 33),
    ],
)
def test_settings_bounds(field: str, value: float) -> None:
    with pytest.raises(ValidationError):
        Settings.model_validate({"api_key": KEY, field: value})
