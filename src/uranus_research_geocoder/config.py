from pydantic import Field, SecretStr, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

NOMINATIM_HOST = "nominatim.oklabflensburg.de"
NOMINATIM_BASE_URL = f"https://{NOMINATIM_HOST}"
LOCAL_ORIGIN = "https://127.0.0.1:443"


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_prefix="GEOCODER_", hide_input_in_errors=True, frozen=True
    )

    api_key: SecretStr = Field(repr=False)
    nominatim_base_url: str = NOMINATIM_BASE_URL
    timeout_seconds: float = Field(default=5, gt=0, le=30, allow_inf_nan=False)
    concurrency: int = Field(default=4, ge=1, le=32)

    @field_validator("api_key")
    @classmethod
    def validate_key(cls, value: SecretStr) -> SecretStr:
        key = value.get_secret_value()
        if not 32 <= len(key) <= 4096 or any(not 33 <= ord(char) <= 126 for char in key):
            raise ValueError("Invalid API key configuration")
        return value

    @field_validator("nominatim_base_url")
    @classmethod
    def validate_base_url(cls, value: str) -> str:
        # Deliberately compare the raw string: URL parsers may normalize forbidden input.
        if value != NOMINATIM_BASE_URL:
            raise ValueError("Invalid Nominatim base URL configuration")
        return value
