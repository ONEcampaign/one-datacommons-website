"""Unit tests for dc_search.place_role.

All tests are pure — no network calls, no LLM calls.  Each function is tested
independently; the module has no side effects.
"""

from __future__ import annotations

from dc_search.place_role import (
    classify_place_roles,
    offerable_places_for_slot,
    place_directional_role,
)
from dc_search.predicate import Predicate

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _predicate(
    *,
    population_type: str | None = "DevelopmentFinance",
    measured_property: str | None = None,
    constraints: dict[str, str | None] | None = None,
    constraint_sets: dict[str, frozenset[str]] | None = None,
) -> Predicate:
    return Predicate(
        population_type=population_type,
        measured_property=measured_property,
        constraints=constraints or {},
        constraint_sets=constraint_sets or {},
    )


# ---------------------------------------------------------------------------
# offerable_places_for_slot
# ---------------------------------------------------------------------------


class TestOfferablePlacesForSlot:
    def test_namespace_match_returns_dcid(self) -> None:
        """country/TGO is offerable to a slot whose values include country/JOR."""
        result = offerable_places_for_slot(
            resolved_places=(("country/TGO", "Togo", "togo", "ambiguous"),),
            slot_values=("country/JOR", "country/NGA"),
        )
        assert result == ("country/TGO",)

    def test_namespace_mismatch_excluded(self) -> None:
        """DAC/Health is NOT offerable to a country/* slot."""
        result = offerable_places_for_slot(
            resolved_places=(("DAC/Health", None, None, "ambiguous"),),
            slot_values=("country/JOR", "country/TGO"),
        )
        assert result == ()

    def test_multiple_resolved_filtered_correctly(self) -> None:
        """Only country-namespace places offered when slot has country/* values."""
        result = offerable_places_for_slot(
            resolved_places=(
                ("country/USA", "United States", "united states", "donor"),
                ("country/NGA", "Nigeria", "nigeria", "ambiguous"),
                ("region/AFRICA", "Africa", "africa", "ambiguous"),
            ),
            slot_values=("country/JOR", "country/TGO"),
        )
        assert result == ("country/USA", "country/NGA")

    def test_empty_resolved_places_returns_empty(self) -> None:
        result = offerable_places_for_slot(
            resolved_places=(),
            slot_values=("country/JOR",),
        )
        assert result == ()

    def test_empty_slot_values_returns_empty(self) -> None:
        result = offerable_places_for_slot(
            resolved_places=(("country/TGO", "Togo", "togo", "ambiguous"),),
            slot_values=(),
        )
        assert result == ()

    def test_no_slash_dcid_uses_whole_string_as_namespace(self) -> None:
        """A DCID with no '/' is its own namespace."""
        result = offerable_places_for_slot(
            resolved_places=(("NOGEO", None, None, "ambiguous"),),
            slot_values=("NOGEO",),
        )
        assert result == ("NOGEO",)

    def test_preserves_input_order(self) -> None:
        result = offerable_places_for_slot(
            resolved_places=(
                ("country/TGO", "Togo", "togo", "recipient"),
                ("country/USA", "United States", "united states", "donor"),
            ),
            slot_values=("country/JOR",),
        )
        assert result == ("country/TGO", "country/USA")


# ---------------------------------------------------------------------------
# classify_place_roles
# ---------------------------------------------------------------------------


class TestClassifyPlaceRoles:
    def test_recipient_bound_excluded_from_entity_set(self) -> None:
        """NGA bound as recipient constraint → not in donor set."""
        pred = _predicate(constraints={"DevelopmentFinanceRecipient": "country/NGA"})
        result = classify_place_roles(
            resolved_places=(("country/NGA", "Nigeria", "nigeria", "ambiguous"),),
            predicates=(pred,),
        )
        assert result == ()

    def test_donor_unbound_included_in_entity_set(self) -> None:
        """USA not appearing in any constraint → stays in donor (entity) set."""
        pred = _predicate(constraints={"DevelopmentFinanceRecipient": "country/NGA"})
        result = classify_place_roles(
            resolved_places=(
                ("country/USA", "United States", "united states", "donor"),
                ("country/NGA", "Nigeria", "nigeria", "recipient"),
            ),
            predicates=(pred,),
        )
        assert result == ("country/USA",)

    def test_multi_recipient_both_excluded(self) -> None:
        """Kenya and Togo both bound as recipients → both excluded; entity set empty."""
        pred = _predicate(
            constraints={
                "DevelopmentFinanceRecipient": "country/KEN",
                "DevelopmentFinancePurpose": "DAC/Malaria",
            }
        )
        pred2 = _predicate(
            constraints={
                "DevelopmentFinanceRecipient": "country/TGO",
            }
        )
        result = classify_place_roles(
            resolved_places=(
                ("country/KEN", "Kenya", "kenya", "recipient"),
                ("country/TGO", "Togo", "togo", "recipient"),
            ),
            predicates=(pred, pred2),
        )
        assert result == ()

    def test_empty_resolved_places_returns_empty(self) -> None:
        pred = _predicate(constraints={"DevelopmentFinanceRecipient": "country/NGA"})
        result = classify_place_roles(
            resolved_places=(),
            predicates=(pred,),
        )
        assert result == ()

    def test_empty_predicates_returns_all_places(self) -> None:
        """No predicates means no constraint values → every place is a donor."""
        result = classify_place_roles(
            resolved_places=(
                ("country/USA", "United States", "united states", "donor"),
                ("country/NGA", "Nigeria", "nigeria", "ambiguous"),
            ),
            predicates=(),
        )
        assert result == ("country/USA", "country/NGA")

    def test_none_constraint_value_ignored(self) -> None:
        """Wildcard slots (value=None) do not accidentally exclude any DCID."""
        pred = _predicate(constraints={"DevelopmentFinanceRecipient": None})
        result = classify_place_roles(
            resolved_places=(("country/NGA", "Nigeria", "nigeria", "ambiguous"),),
            predicates=(pred,),
        )
        assert result == ("country/NGA",)

    def test_preserves_input_order(self) -> None:
        """Donor set preserves the order of resolved_places, not alphabetical."""
        pred = _predicate(constraints={"slot": "country/ZAF"})
        result = classify_place_roles(
            resolved_places=(
                ("country/USA", "United States", "united states", "donor"),
                ("country/NGA", "Nigeria", "nigeria", "ambiguous"),
                ("country/ZAF", "South Africa", "south africa", "recipient"),
            ),
            predicates=(pred,),
        )
        assert result == ("country/USA", "country/NGA")

    def test_constraint_sets_excludes_set_bound_recipients(self) -> None:
        """KEN and TGO in constraint_sets → excluded from donor set; USA kept."""
        pred = _predicate(
            constraints={"DevelopmentFinanceRecipient": "DAC/Africa"},
            constraint_sets={
                "DevelopmentFinanceRecipient": frozenset({"country/KEN", "country/TGO"})
            },
        )
        result = classify_place_roles(
            resolved_places=(
                ("country/USA", "United States", "united states", "donor"),
                ("country/KEN", "Kenya", None, "ambiguous"),
                ("country/TGO", "Togo", None, "ambiguous"),
            ),
            predicates=(pred,),
        )
        assert result == ("country/USA",)

    def test_constraint_sets_excludes_all_children_entity_set_empty(self) -> None:
        """All resolved places are set-bound recipients → donor set is empty."""
        pred = _predicate(
            constraints={"DevelopmentFinanceRecipient": "DAC/Africa"},
            constraint_sets={
                "DevelopmentFinanceRecipient": frozenset({"country/KEN", "country/TGO"})
            },
        )
        result = classify_place_roles(
            resolved_places=(
                ("country/KEN", "Kenya", None, "ambiguous"),
                ("country/TGO", "Togo", None, "ambiguous"),
            ),
            predicates=(pred,),
        )
        assert result == ()

    def test_empty_constraint_sets_back_compat(self) -> None:
        """Predicate with empty constraint_sets behaves identically to today's scalar path."""
        pred_with_sets = _predicate(
            constraints={"DevelopmentFinanceRecipient": "country/NGA"},
            constraint_sets={},
        )
        pred_without_sets = _predicate(
            constraints={"DevelopmentFinanceRecipient": "country/NGA"},
        )
        resolved = (
            ("country/USA", "United States", "united states", "donor"),
            ("country/NGA", "Nigeria", "nigeria", "recipient"),
        )
        result_with = classify_place_roles(resolved_places=resolved, predicates=(pred_with_sets,))
        result_without = classify_place_roles(
            resolved_places=resolved, predicates=(pred_without_sets,)
        )
        assert result_with == result_without == ("country/USA",)


# ---------------------------------------------------------------------------
# place_directional_role
# ---------------------------------------------------------------------------


class TestPlaceDirectionalRole:
    def test_input_surface_donor_us(self) -> None:
        """input_surface='us' + canonical_name='United States' in 'from us' → donor.

        This is the key case the old name/slug-only approach missed: the user
        types 'us' but the canonical name is 'United States' and the slug is
        'USA'.  The input_surface anchor finds 'us' immediately after 'from'.
        """
        result = place_directional_role(
            query="grants from us to togo",
            input_surface="us",
            canonical_name="United States",
            place_dcid="country/USA",
        )
        assert result == "donor"

    def test_to_recipient_explicit(self) -> None:
        """'to togo' with input_surface → recipient."""
        result = place_directional_role(
            query="grants to togo",
            input_surface="togo",
            canonical_name="Togo",
            place_dcid="country/TGO",
        )
        assert result == "recipient"

    def test_no_preposition_ambiguous(self) -> None:
        """'malaria grants nigeria' — no directional language → ambiguous."""
        result = place_directional_role(
            query="malaria grants nigeria",
            input_surface="nigeria",
            canonical_name="Nigeria",
            place_dcid="country/NGA",
        )
        assert result == "ambiguous"

    def test_input_surface_none_falls_back_to_canonical_name(self) -> None:
        """When input_surface is None, canonical_name is the anchor."""
        result = place_directional_role(
            query="from united states malaria grants",
            input_surface=None,
            canonical_name="United States",
            place_dcid="country/USA",
        )
        assert result == "donor"

    def test_both_none_falls_back_to_dcid_slug(self) -> None:
        """When both input_surface and canonical_name are None, the DCID slug is used."""
        result = place_directional_role(
            query="grants to nga",
            input_surface=None,
            canonical_name=None,
            place_dcid="country/NGA",
        )
        assert result == "recipient"

    def test_preposition_at_start_of_query(self) -> None:
        """Preposition right at the start of the query (no context before it)."""
        result = place_directional_role(
            query="from united states malaria grants to togo",
            input_surface=None,
            canonical_name="United States",
            place_dcid="country/USA",
        )
        assert result == "donor"

    def test_multiple_places_independent_decisions(self) -> None:
        """Each call is per-place; 'from us to togo' classifies US as donor, Togo as recipient."""
        usa = place_directional_role(
            query="grants from us to togo",
            input_surface="us",
            canonical_name="United States",
            place_dcid="country/USA",
        )
        togo = place_directional_role(
            query="grants from us to togo",
            input_surface="togo",
            canonical_name="Togo",
            place_dcid="country/TGO",
        )
        assert usa == "donor"
        assert togo == "recipient"

    def test_empty_query_is_ambiguous(self) -> None:
        result = place_directional_role(
            query="",
            input_surface="nigeria",
            canonical_name="Nigeria",
            place_dcid="country/NGA",
        )
        assert result == "ambiguous"

    def test_place_not_in_query_is_ambiguous(self) -> None:
        """No anchor appears in query → fail-open to ambiguous."""
        result = place_directional_role(
            query="malaria grants to togo",
            input_surface="united states",
            canonical_name="United States",
            place_dcid="country/USA",
        )
        assert result == "ambiguous"

    def test_input_surface_preferred_over_canonical_name(self) -> None:
        """input_surface is tried before canonical_name; first hit wins."""
        # input_surface 'us' appears after 'from'; canonical name 'United States'
        # would not be found in the query.  input_surface anchor gives "donor".
        result = place_directional_role(
            query="from us malaria grants",
            input_surface="us",
            canonical_name="United States",
            place_dcid="country/USA",
        )
        assert result == "donor"
