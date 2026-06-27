"""Tests for place-as-constraint (seam) behavior in the engine."""
from __future__ import annotations

from qre.models import (
    DefiniteResponse,
    RawTextInput,
    ResolveOptions,
    ResolveRequest,
)
from tests.engine._harness import offline_resolve


def make_request(query: str, pac: bool | None = None) -> ResolveRequest:
    options = ResolveOptions(place_as_constraint=pac) if pac is not None else None
    return ResolveRequest(input=RawTextInput(query=query), options=options)


class TestSeamBehavior:
    def test_seam_on_recipient_is_directional(self):
        """df-01 seam=ON: ETH is directional (to), USA is directional (from)."""
        result = offline_resolve(make_request("health ODA grants from USA to Ethiopia", pac=True))
        inner = result.root
        assert inner.status == "definite"
        assert isinstance(inner, DefiniteResponse)
        spec = inner.interpretation
        entity_roles = {e.ref.dcid: e.role for e in spec.entities}
        assert entity_roles["country/ETH"].kind == "directional"
        assert entity_roles["country/USA"].kind == "directional"
        assert entity_roles["country/USA"].direction == "from"
        assert entity_roles["country/USA"].role.dcid == "observationAbout"

    def test_seam_off_both_are_subjects(self):
        """df-01 seam=OFF: both ETH and USA become subject."""
        result = offline_resolve(make_request("health ODA grants from USA to Ethiopia", pac=False))
        inner = result.root
        assert inner.status == "definite"
        assert isinstance(inner, DefiniteResponse)
        spec = inner.interpretation
        entity_roles = {e.ref.dcid: e.role for e in spec.entities}
        for role in entity_roles.values():
            assert role.kind == "subject"

    def test_seam_off_emits_info_warning(self):
        """Seam=OFF should emit PLACE_CONSTRAINT_SEAM_OFF warning."""
        result = offline_resolve(make_request("health ODA grants from USA to Ethiopia", pac=False))
        codes = [w.code for w in result.root.diagnostics.warnings]
        assert "PLACE_CONSTRAINT_SEAM_OFF" in codes

    def test_seam_off_directional_detected_emits_role_disabled(self):
        """Seam=OFF with directional prepositions emits ENTITY_ROLE_DISABLED."""
        result = offline_resolve(make_request("health ODA grants from USA to Ethiopia", pac=False))
        codes = [w.code for w in result.root.diagnostics.warnings]
        assert "ENTITY_ROLE_DISABLED" in codes

    def test_noop_invariant_entity_sets_match(self):
        """The set of entity dcids is the same regardless of seam flag."""
        result_on = offline_resolve(
            make_request("health ODA grants from USA to Ethiopia", pac=True)
        )
        result_off = offline_resolve(
            make_request("health ODA grants from USA to Ethiopia", pac=False)
        )
        dcids_on = {e.ref.dcid for e in result_on.root.interpretation.entities}
        dcids_off = {e.ref.dcid for e in result_off.root.interpretation.entities}
        assert dcids_on == dcids_off
