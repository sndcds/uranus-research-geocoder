"""Bounded JSONv2 adapter; provider content is never forwarded as an opaque object."""

import json
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, TypeAdapter, ValidationError, field_validator

from .administrative import administrative_metadata
from .errors import NoMatch, UpstreamError
from .models import Address, BoundaryGeometry, Latitude, Longitude, Place, PositiveID


class UpstreamModel(BaseModel):
    model_config = ConfigDict(extra="ignore", strict=True, hide_input_in_errors=True)


class NominatimPlace(UpstreamModel):
    display_name: str | None = Field(default=None, max_length=2000)
    name: str | None = Field(default=None, max_length=300)
    osm_type: Literal["node", "way", "relation"] | None = None
    osm_id: PositiveID | None = None
    category: str | None = None
    type: str | None = None
    addresstype: str | None = None
    address: dict[str, str] = Field(default_factory=dict)
    extratags: dict[str, str] | None = None
    lat: Latitude | None = None
    lon: Longitude | None = None
    boundingbox: Annotated[list[float], Field(min_length=4, max_length=4)] | None = None
    geojson: BoundaryGeometry | None = None

    @field_validator("lat", "lon", mode="before")
    @classmethod
    def decimal_coordinate(cls, value: object) -> object:
        return float(value) if isinstance(value, str) else value

    @field_validator("boundingbox", mode="before")
    @classmethod
    def decimal_box(cls, value: object) -> object:
        return (
            [float(v) if isinstance(v, str) else v for v in value]
            if isinstance(value, list)
            else value
        )


class Status(UpstreamModel):
    status: int


def normalize(
    payload: object, maximum: int, *, reverse: bool = False, boundary: bool = False
) -> list[Place]:
    try:
        rows = TypeAdapter(list[NominatimPlace]).validate_json(
            json.dumps([payload] if reverse else payload)
        )
        if len(rows) > maximum:
            raise UpstreamError
        items = [_place(row, boundary=boundary) for row in rows]
    except (ValidationError, ValueError, TypeError, OverflowError):
        raise UpstreamError from None
    if not items:
        raise NoMatch
    return items


def _place(row: NominatimPlace, *, boundary: bool) -> Place:
    country = row.address.get("country_code")
    if country is not None:
        country = country.lower()
        if len(country) != 2 or not country.isascii() or not country.isalpha():
            raise UpstreamError
    level, levels, code, code_type = administrative_metadata(
        country, row.category, row.type, row.addresstype, row.extratags or {}
    )
    address = Address(
        **{key: value for key, value in row.address.items() if key in Address.model_fields}
    )
    bbox = None
    if row.boundingbox is not None:
        south, north, west, east = row.boundingbox
        if south > north or west > east:
            raise UpstreamError
        bbox = (south, west, north, east)
    if (row.lat is None) != (row.lon is None):
        raise UpstreamError
    if row.geojson is not None and (not boundary or level == "unknown"):
        raise UpstreamError
    return Place(
        name=row.name,
        display_name=row.display_name,
        latitude=row.lat,
        longitude=row.lon,
        osm_type=row.osm_type,
        osm_id=row.osm_id,
        place_type=row.addresstype or row.type,
        country_code=country,
        address=address if address.model_dump(exclude_none=True) else None,
        bbox=bbox,
        administrative_level=level,
        administrative_levels=levels,
        official_code=code,
        official_code_type=code_type,
        boundary=row.geojson,
    )
