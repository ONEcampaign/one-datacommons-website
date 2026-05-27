"""Place-hierarchy tables and default child-type resolution.

Pure leaf module: no I/O, no ``server/lib`` import.  Ported from
``server/lib/nl/common/{utils,constants}.py`` (``get_default_child_place_type``
and ``admin_area_equiv_for_place``).  All type values are the ``.value``
strings from ``ContainedInPlaceType`` — plain string dicts, no Enum dependency.
"""

from __future__ import annotations

# ---------------------------------------------------------------------------
# Tables
# ---------------------------------------------------------------------------

# Earth entry point.
_EARTH_DCID = "Earth"

# Parent canonical type -> immediate child canonical type.
# (server CHILD_PLACE_TYPES, enum .value strings.)
_CHILD_PLACE_TYPES: dict[str, str] = {
    "Continent": "Country",
    "ContinentalUnion": "Country",
    "GeoRegion": "Country",
    "UNGeoRegion": "Country",
    "Country": "AdministrativeArea1",
    "AdministrativeArea1": "AdministrativeArea2",
    "AdministrativeArea2": "City",
}

# Non-canonical admin types -> canonical AA type.
# (server ADMIN_DIVISION_EQUIVALENTS, enum .value strings.)
_ADMIN_DIVISION_EQUIVALENTS: dict[str, str] = {
    "State": "AdministrativeArea1",
    "EurostatNUTS2": "AdministrativeArea1",
    "Province": "AdministrativeArea1",
    "Department": "AdministrativeArea1",
    "Division": "AdministrativeArea1",
    "AdministrativeArea1": "AdministrativeArea1",
    "EurostatNUTS3": "AdministrativeArea2",
    "County": "AdministrativeArea2",
    "District": "AdministrativeArea2",
    "Parish": "AdministrativeArea2",
    "Municipality": "AdministrativeArea2",
    "AdministrativeArea2": "AdministrativeArea2",
    "AdministrativeArea3": "AdministrativeArea2",
}

# Per-country remaps of a canonical AA child type to the country-specific type.
_USA_PLACE_TYPE_REMAP: dict[str, str] = {
    "AdministrativeArea1": "State",
    "AdministrativeArea2": "County",
}
_EU_PLACE_TYPE_REMAP: dict[str, str] = {
    "AdministrativeArea1": "EurostatNUTS2",
    "AdministrativeArea2": "EurostatNUTS3",
}
_PAK_PLACE_TYPE_REMAP: dict[str, str] = {
    "AdministrativeArea1": "AdministrativeArea1",
    "AdministrativeArea2": "AdministrativeArea3",
}

_EU_COUNTRIES: frozenset[str] = frozenset(
    {
        "country/ALB",
        "country/AUT",
        "country/BEL",
        "country/BGR",
        "country/CHE",
        "country/CYP",
        "country/CZE",
        "country/DEU",
        "country/DNK",
        "country/ESP",
        "country/EST",
        "country/FIN",
        "country/FRA",
        "country/FXX",
        "country/GBR",
        "country/GRC",
        "country/HRV",
        "country/HUN",
        "country/IRL",
        "country/ISL",
        "country/ITA",
        "country/LIE",
        "country/LTU",
        "country/LUX",
        "country/LVA",
        "country/MKD",
        "country/MLT",
        "country/MNE",
        "country/NLD",
        "country/NOR",
        "country/POL",
        "country/PRT",
        "country/ROU",
        "country/SRB",
        "country/SVK",
        "country/SVN",
        "country/SWE",
        "country/TUR",
    }
)


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------


def _admin_area_equiv(child_type: str, country: str | None) -> str:
    """Apply per-country remap to a canonical child type.

    If *child_type* is not an admin-area equivalent (e.g. ``"Country"`` or
    ``"City"``), it is returned unchanged — no remap applies.  When it is an
    admin-area type, pick the remap dict by *country* and return the remapped
    value (falling back to the canonical form when the country has no override).
    """
    # Re-canonicalize the input type.  Non-admin types (Country, City) are not
    # in the table → return as-is, no remap.
    canonical = _ADMIN_DIVISION_EQUIVALENTS.get(child_type)
    if canonical is None:
        return child_type

    # Select remap dict by country.
    if country == "country/USA":
        remap = _USA_PLACE_TYPE_REMAP
    elif country == "country/PAK":
        remap = _PAK_PLACE_TYPE_REMAP
    elif country in _EU_COUNTRIES:
        remap = _EU_PLACE_TYPE_REMAP
    else:
        remap = {}

    return remap.get(canonical, canonical)


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def default_child_type(
    *, parent_dcid: str, parent_type: str | None, parent_country: str | None
) -> str | None:
    """Return the immediate child place type for a parent, or None.

    Mirrors the website's get_default_child_place_type: Earth -> Country;
    canonicalize the parent type via the admin-division equivalents, step one
    level down the hierarchy, then apply the per-country remap (USA->State,
    EU->EurostatNUTS2, PAK->AdministrativeArea3) when the child is an admin-area
    type. Returns None when the parent type is unknown/None or has no child level.
    """
    if parent_dcid == _EARTH_DCID:
        return "Country"
    if parent_type is None:
        return None
    ptype = _ADMIN_DIVISION_EQUIVALENTS.get(parent_type, parent_type)
    child = _CHILD_PLACE_TYPES.get(ptype)
    if child is None:
        return None
    return _admin_area_equiv(child, parent_country)


def needs_parent_country(parent_type: str | None) -> bool:
    """True when a parent of this type needs its country resolved for the AA remap.

    Admin-area parents (State, County, EU NUTS, …) remap per country; country /
    continent / region / Earth parents do not.
    """
    return parent_type in _ADMIN_DIVISION_EQUIVALENTS
