"""Validate GeocodeJSON and expose only explicitly supported fields."""

from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, FiniteFloat, ValidationError

from .errors import NoMatch, UpstreamError
from .models import Address, Latitude, Longitude, Place, PositiveID


class UpstreamModel(BaseModel):
    model_config = ConfigDict(extra="ignore", strict=True, hide_input_in_errors=True)


class Geocoding(UpstreamModel):
    label: str | None = None
    osm_type: str | None = None
    osm_id: PositiveID | None = None
    type: str | None = None
    country_code: str | None = None
    street: str | None = None
    housenumber: str | None = None
    city: str | None = None
    municipality: str | None = None
    locality: str | None = None
    district: str | None = None
    county: str | None = None
    state: str | None = None
    postcode: str | None = None
    country: str | None = None


class Properties(UpstreamModel):
    geocoding: Geocoding


class Point(UpstreamModel):
    type: Literal["Point"]
    coordinates: Annotated[list[FiniteFloat], Field(min_length=2, max_length=2)]


class Feature(UpstreamModel):
    type: Literal["Feature"]
    properties: Properties
    geometry: Point | None = None
    bbox: Annotated[list[float], Field(min_length=4, max_length=4)] | None = None


class FeatureCollection(UpstreamModel):
    type: Literal["FeatureCollection"]
    features: list[Feature] = Field(max_length=5)


class Status(UpstreamModel):
    status: int


def normalize(payload: object, maximum: int) -> list[Place]:
    try:
        collection = FeatureCollection.model_validate(payload)
        if len(collection.features) > maximum:
            raise UpstreamError
        items = [_place(feature) for feature in collection.features]
    except (ValidationError, ValueError, TypeError, OverflowError):
        raise UpstreamError from None
    if not items:
        raise NoMatch
    return items


def _place(feature: Feature) -> Place:
    geo = feature.properties.geocoding
    address = Address(
        road=geo.street,
        house_number=geo.housenumber,
        city=geo.city,
        municipality=geo.municipality,
        locality=geo.locality,
        district=geo.district,
        county=geo.county,
        state=geo.state,
        postcode=geo.postcode,
        country=geo.country,
    )
    bbox: tuple[Latitude, Longitude, Latitude, Longitude] | None = None
    if feature.bbox is not None:
        west, south, east, north = feature.bbox
        if south > north:
            raise UpstreamError
        bbox = (south, west, north, east)
    return Place(
        display_name=geo.label,
        latitude=feature.geometry.coordinates[1] if feature.geometry is not None else None,
        longitude=feature.geometry.coordinates[0] if feature.geometry is not None else None,
        osm_type=geo.osm_type,
        osm_id=geo.osm_id,
        place_type=geo.type,
        country_code=geo.country_code,
        address=address if address.model_dump(exclude_none=True) else None,
        bbox=bbox,
    )
