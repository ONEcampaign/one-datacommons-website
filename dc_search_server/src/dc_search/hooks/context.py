"""Hook protocol and context types.

Defines ``HookContext`` (the immutable per-invocation context bag), the
``Hook`` Protocol, and the ``HookResult`` type alias.  No retrieval
dependency — this module is a leaf in the hooks sub-package DAG.
"""

from __future__ import annotations

from dataclasses import dataclass
from dataclasses import field as dataclasses_field
from typing import Protocol, runtime_checkable

from dc_search.extraction import ExtractedDate
from dc_search.predicate import (
    AnswerCollection,
    AskClarification,
    Predicate,
)
from dc_search.retrieval import StatVarFeatures


@dataclass(frozen=True, slots=True)
class HookContext:
    """Immutable context threaded through every hook invocation.

    ``place_availability`` is the union of ``variables_for_entity`` across all
    resolved place DCIDs.  A single set suffices for both subject-availability
    (Census) and donor-availability (CRS_DAC) — union semantics absorb the
    role distinction.

    ``retrieval_scores`` maps SV DCID → score from ``resolve_indicator``.
    Drives ``RetrievalQualityHook``.  An empty dict means scores were not
    populated (e.g. unit tests that bypass the retrieval step); the hook
    is a no-op in that case.

    ``dates`` carries extracted date references from the default endpoint's
    extraction step.  Empty list for the simple endpoint (no extraction LLM
    call).  ``DateFilterHook`` reads this field.
    """

    place_dcids: tuple[str, ...]
    """Already-resolved place DCIDs from ``extract_place_tokens``."""
    place_availability: frozenset[str] | None
    """Union of variables_for_entity across place_dcids; None = not computed."""
    retrieval_scores: dict[str, float]
    """SV DCID → retrieval score; empty dict when not available."""
    raw_candidates: tuple[StatVarFeatures, ...]
    """Candidate features as received by the materializer."""
    dates: list[ExtractedDate] = dataclasses_field(default_factory=list)
    """Extracted date references; empty list when not provided (simple endpoint)."""
    dcid_to_sentence: dict[str, str] = dataclasses_field(default_factory=dict)
    """Maps SV DCID → retrieval sentence that surfaced it; empty when not populated."""
    dcid_to_date_range: dict[str, tuple[str | None, str | None]] = dataclasses_field(
        default_factory=dict
    )
    """Maps SV DCID → (earliest, latest) observation dates; absent when unknown."""
    availability_degraded: bool = False
    """True when the availability re-rank fetch failed open (transient mixer error).

    Computed during the pre-rerank step (a separate ``asyncio.to_thread`` whose
    ContextVar copy cannot propagate back), so it is captured there and threaded
    in here.  ``materialize_via_hooks`` turns it — together with any in-hook
    degradation — into a ``filtering_degraded`` caveat.
    """
    hook_timings: dict[str, float] | None = None
    """Optional sink for per-hook wall-clock seconds written by materialize_via_hooks.

    When set to a dict, the dispatcher writes ``{"universal_filter": secs,
    "<hook.name>": secs, ...}`` for each phase that actually ran.  Hooks that
    did not apply are not recorded.  The reference is frozen; the dict contents
    are mutated in place by the dispatcher.
    """
    all_resolved_dcids: tuple[str, ...] = ()
    """Full set of place DCIDs resolved from the query, before donor-narrowing.

    ``place_dcids`` carries the *donor* subset (resolved places minus any place
    bound as a constraint value).  ``all_resolved_dcids`` carries the union, so
    downstream hooks can detect when the two differ (a recipient was bound) and
    re-derive availability against the donor set rather than the full set.

    Empty when no places were resolved.  Set by the orchestrator (defaults to
    empty for unit tests that don't exercise the post-materialize path)."""
    defaulted_recipient: bool = False
    """True when the recipient slot was assigned by the unqualified-place default.

    Set by the orchestrator from ``BindResult.defaulted_recipient``.  Drives the
    ``interpreted_place_as_recipient`` user-facing caveat in the projection
    enrichment hook."""


HookResult = AnswerCollection | AskClarification


@runtime_checkable
class Hook(Protocol):
    """Hook protocol.

    Each hook has a ``name`` for telemetry output, an ``applies`` check, and a
    ``run`` method that transforms the result."""

    name: str

    def applies(
        self,
        predicate: Predicate,
        candidates: tuple[StatVarFeatures, ...],
        ctx: HookContext,
    ) -> bool: ...

    def run(
        self,
        predicate: Predicate,
        result: AnswerCollection,
        ctx: HookContext,
    ) -> HookResult: ...
