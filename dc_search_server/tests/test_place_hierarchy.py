"""Unit tests for dc_search.place_hierarchy.

All tests are pure — no network calls, no LLM calls.  The module has no side
effects beyond its lookup tables.
"""

from __future__ import annotations

from dc_search.place_hierarchy import default_child_type, needs_parent_country

# ---------------------------------------------------------------------------
# default_child_type
# ---------------------------------------------------------------------------


class TestDefaultChildType:
    # --- Earth entry point ---

    def test_earth_any_type_returns_country(self) -> None:
        """Earth with any parent_type always yields Country (special-cased DCID)."""
        assert (
            default_child_type(parent_dcid="Earth", parent_type=None, parent_country=None)
            == "Country"
        )

    def test_earth_with_type_still_returns_country(self) -> None:
        """Earth's DCID short-circuits before the type lookup."""
        assert (
            default_child_type(parent_dcid="Earth", parent_type="GeoRegion", parent_country=None)
            == "Country"
        )

    # --- Country parents ---

    def test_usa_country_returns_state(self) -> None:
        """USA as a Country parent → State (per-country remap).

        The caller (_expand_children) passes parent_country=parent_dcid for
        Country-type parents, so parent_country="country/USA" here.
        """
        assert (
            default_child_type(
                parent_dcid="country/USA",
                parent_type="Country",
                parent_country="country/USA",
            )
            == "State"
        )

    def test_eu_member_country_returns_eurostat_nuts2(self) -> None:
        """An EU member country (DEU) → EurostatNUTS2.

        Caller passes parent_country=parent_dcid for Country-type parents.
        """
        assert (
            default_child_type(
                parent_dcid="country/DEU",
                parent_type="Country",
                parent_country="country/DEU",
            )
            == "EurostatNUTS2"
        )

    def test_generic_country_returns_aa1(self) -> None:
        """A generic country (KEN) → AdministrativeArea1 (no country remap)."""
        assert (
            default_child_type(
                parent_dcid="country/KEN",
                parent_type="Country",
                parent_country="country/KEN",
            )
            == "AdministrativeArea1"
        )

    def test_pak_country_returns_aa1(self) -> None:
        """Pakistan as a country parent → AdministrativeArea1 (PAK remap maps AA1→AA1)."""
        assert (
            default_child_type(
                parent_dcid="country/PAK",
                parent_type="Country",
                parent_country="country/PAK",
            )
            == "AdministrativeArea1"
        )

    # --- Sub-country parents (need parent_country for remap) ---

    def test_usa_state_parent_returns_county(self) -> None:
        """California (State) with country=USA → County."""
        assert (
            default_child_type(
                parent_dcid="geoId/06",
                parent_type="State",
                parent_country="country/USA",
            )
            == "County"
        )

    def test_eu_nuts2_parent_returns_nuts3(self) -> None:
        """A EurostatNUTS2 parent in a German context → EurostatNUTS3."""
        assert (
            default_child_type(
                parent_dcid="nuts/DE1",
                parent_type="EurostatNUTS2",
                parent_country="country/DEU",
            )
            == "EurostatNUTS3"
        )

    def test_pak_aa1_parent_returns_aa3(self) -> None:
        """A Pakistan AA1 parent → AdministrativeArea3 (PAK remap maps AA2→AA3)."""
        assert (
            default_child_type(
                parent_dcid="wikidataId/Q11169",
                parent_type="AdministrativeArea1",
                parent_country="country/PAK",
            )
            == "AdministrativeArea3"
        )

    # --- Continent / region parents ---

    def test_continent_returns_country(self) -> None:
        """A Continent parent → Country (no per-country remap; Country is not an AA type)."""
        assert (
            default_child_type(
                parent_dcid="africa",
                parent_type="Continent",
                parent_country=None,
            )
            == "Country"
        )

    def test_geo_region_returns_country(self) -> None:
        """GeoRegion → Country."""
        assert (
            default_child_type(
                parent_dcid="region/SSAFR",
                parent_type="GeoRegion",
                parent_country=None,
            )
            == "Country"
        )

    # --- None / unknown / bottom-level ---

    def test_none_type_returns_none(self) -> None:
        """Unknown parent type (None) → None; no expansion."""
        assert (
            default_child_type(parent_dcid="country/KEN", parent_type=None, parent_country=None)
            is None
        )

    def test_unknown_type_returns_none(self) -> None:
        """'School' is not in any hierarchy table → None."""
        assert (
            default_child_type(parent_dcid="some/dcid", parent_type="School", parent_country=None)
            is None
        )

    def test_city_parent_returns_none(self) -> None:
        """City is not in _CHILD_PLACE_TYPES (no child level) → None."""
        assert (
            default_child_type(
                parent_dcid="geoId/0644000",
                parent_type="City",
                parent_country="country/USA",
            )
            is None
        )


# ---------------------------------------------------------------------------
# needs_parent_country
# ---------------------------------------------------------------------------


class TestNeedsParentCountry:
    def test_state_needs_country(self) -> None:
        assert needs_parent_country("State") is True

    def test_aa1_needs_country(self) -> None:
        assert needs_parent_country("AdministrativeArea1") is True

    def test_county_needs_country(self) -> None:
        assert needs_parent_country("County") is True

    def test_eurostat_nuts2_needs_country(self) -> None:
        assert needs_parent_country("EurostatNUTS2") is True

    def test_aa3_needs_country(self) -> None:
        assert needs_parent_country("AdministrativeArea3") is True

    def test_country_does_not_need_country(self) -> None:
        assert needs_parent_country("Country") is False

    def test_continent_does_not_need_country(self) -> None:
        assert needs_parent_country("Continent") is False

    def test_none_does_not_need_country(self) -> None:
        assert needs_parent_country(None) is False

    def test_city_does_not_need_country(self) -> None:
        assert needs_parent_country("City") is False

    def test_unknown_type_does_not_need_country(self) -> None:
        assert needs_parent_country("School") is False
