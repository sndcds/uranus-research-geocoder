"""Country-aware interpretation of provider metadata, never names or caller hints."""

from .models import AdministrativeLevel

DE_LEVELS: dict[str, AdministrativeLevel] = {
    "2": "country",
    "4": "state",
    "6": "district",
    "8": "municipality",
}
PLACE_LEVELS: dict[str, AdministrativeLevel] = {
    "country": "country",
    "state": "state",
    "county": "district",
    "city": "municipality",
    "town": "municipality",
    "village": "municipality",
    "municipality": "municipality",
    "region": "region",
}
CODE_TAGS = ("de:amtlicher_gemeindeschluessel", "de:regionalschluessel")


def administrative_metadata(
    country: str | None,
    category: str | None,
    kind: str | None,
    address_type: str | None,
    tags: dict[str, str],
) -> tuple[AdministrativeLevel, list[AdministrativeLevel], str | None, str | None]:
    level: AdministrativeLevel = "unknown"
    levels: list[AdministrativeLevel] = []
    boundary = category == "boundary" and kind == "administrative"
    if country == "de":
        raw = tags.get("admin_level")
        if boundary and raw is not None:
            level = DE_LEVELS.get(raw, "unknown")
        elif boundary or category == "place":
            level = PLACE_LEVELS.get(address_type or kind or "", "unknown")
        if level != "unknown":
            levels.append(level)
        # Independent cities and city states have multiple roles. An actual full AGS
        # is evidence of a municipality; never derive or pad a code from a name.
        ags = tags.get("de:amtlicher_gemeindeschluessel", "")
        if boundary and raw in {"4", "6"} and ags.isascii() and ags.isdecimal() and len(ags) == 8:
            levels.append("municipality")
            if raw == "6":
                level = "municipality"
        for tag in CODE_TAGS:
            code = tags.get(tag)
            if code and code.isascii() and code.isdecimal() and len(code) in {2, 5, 8, 9, 12}:
                return level, levels, code, tag
    elif category == "place" and kind in {"country", "region"}:
        level = PLACE_LEVELS[kind]
        levels = [level]
    # No global numeric admin_level mapping. Further countries need audited adapters.
    return level, levels, None, None
