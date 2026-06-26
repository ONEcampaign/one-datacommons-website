"""Family resolver protocol and FamilyRule dataclass.

Pure module: no I/O, no LLM, no graph calls. Nothing imports back from
this file — it is the base of the package's import DAG.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Protocol, runtime_checkable

if TYPE_CHECKING:
    from qre.engine.bind import SlotBindingDraft
    from qre.engine.extract import DateRequest
    from qre.engine.graph import EngineGraphClient
    from qre.engine.retrieve import Materialised, MaterialisedCandidates, NoDataDraft
    from qre.engine.shape import ShapeDraft


@runtime_checkable
class FamilyResolver(Protocol):
    """Contract that every family resolver must satisfy.

    A resolver is a stateless object (usually a singleton module-level instance)
    that knows how to:
      - recognise its own namespace in a candidate SV list (``matches``), and
      - convert a confirmed ShapeDraft + bound slots into a Materialised result
        or a NoDataDraft (``resolve``).

    The resolver receives a pre-confirmed ShapeDraft from ``discover.derive_shapes``
    that already carries per-SV arc facts.  It MUST NOT re-issue ``node_arcs`` per SV.
    """

    namespace: str

    def matches(self, *, candidate_svs: list[str]) -> bool:
        """Return True when these candidates belong to this family.

        One matching SV is enough — detect is recall-only and may mix namespaces.
        """
        ...

    def resolve(
        self,
        *,
        shape: "ShapeDraft",
        bindings: "list[SlotBindingDraft]",
        recipient_dcid: str | None,
        donor_dcid: str | None,
        graph: "EngineGraphClient",
        date_request: "DateRequest | None" = None,
    ) -> "Materialised | NoDataDraft | MaterialisedCandidates":
        """Resolve confirmed shape + bindings to an observation result.

        Args:
            shape:        The confirmed ShapeDraft (carries arc facts from derive_shapes).
            bindings:     Slot bindings from the LLM bind stage.
            recipient_dcid: Resolved recipient entity dcid, or None.
            donor_dcid:   Resolved donor entity dcid, or None.
            graph:        Graph client (injected; use FakeGraph in tests).

        Returns:
            Materialised on success, NoDataDraft on data absence,
            MaterialisedCandidates when multiple plausible shapes survive.
        """
        ...


@dataclass(frozen=True)
class FamilyRule:
    """A registered family: identity metadata + its resolver.

    Fields:
        label:      Human-readable name for diagnostics and logs.
        namespace:  Canonical SV dcid prefix (e.g. ``"ONE/CRS_DAC/"``).
        resolver:   The FamilyResolver instance for this family.
        shape_id:   The stable shape_id string used in spec_id computation and
                    back-references on StatVar.  Must be unique per family and
                    stable across deploys.  For dev-finance this is
                    ``"dev_finance_crs_dac"`` (the historic value from Family.family_id).
                    Defaults to the empty string; derive_shapes falls back to the
                    five-tuple string when not set.  Namespaced families SHOULD set a
                    stable non-empty shape_id to keep spec_id stable across deploys
                    (the empty-string → five-tuple fallback is intended for the
                    standard catch-all only).
        axis_pins:  Hand-pinned property→axis overrides specific to this family
                    (complements the global AXIS_OVERRIDES).  Keyed by property
                    dcid, values are one of "what", "how", "where", "when".
    """

    label: str
    namespace: str
    resolver: FamilyResolver = field(compare=False)
    shape_id: str = ""
    axis_pins: dict[str, str] = field(default_factory=dict)
