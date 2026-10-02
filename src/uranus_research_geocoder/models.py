from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator

Latitude = Annotated[float, Field(ge=-90, le=90, allow_inf_nan=False)]
Longitude = Annotated[float, Field(ge=-180, le=180, allow_inf_nan=False)]
PositiveID = Annotated[int, Field(gt=0)]


class ClosedModel(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True, hide_input_in_errors=True, frozen=True)


class SearchRequest(ClosedModel):
    query: str = Field(min_length=1, max_length=300)
    limit: int = Field(default=5, ge=1, le=5)

    @field_validator("query")
    @classmethod
    def not_blank(cls, value: str) -> str:
        if not value.strip() or any(0xD800 <= ord(char) <= 0xDFFF for char in value):
            raise ValueError("Invalid query")
        return value


class ReverseRequest(ClosedModel):
    latitude: Latitude
    longitude: Longitude


class LookupRequest(ClosedModel):
    osm_type: Literal["N", "W", "R"]
    osm_id: PositiveID


class Address(ClosedModel):
    road: str | None = None
    house_number: str | None = None
    city: str | None = None
    municipality: str | None = None
    locality: str | None = None
    district: str | None = None
    county: str | None = None
    state: str | None = None
    postcode: str | None = None
    country: str | None = None


class Place(ClosedModel):
    display_name: str | None = None
    latitude: Latitude | None = None
    longitude: Longitude | None = None
    osm_type: str | None = None
    osm_id: PositiveID | None = None
    place_type: str | None = None
    country_code: str | None = None
    address: Address | None = None
    # Public API order: south, west, north, east (not GeoJSON order).
    bbox: tuple[Latitude, Longitude, Latitude, Longitude] | None = None


class SearchResponse(ClosedModel):
    query: str
    items: list[Place]
