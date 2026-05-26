"""Composable hook pipeline for the universal materializer.

Each hook answers two questions:
  1. ``applies(predicate, candidates, ctx) -> bool`` — should this hook run?
  2. ``run(predicate, result, ctx) -> HookResult``   — what does it do?

The dispatcher in ``materialize_via_hooks`` iterates ``HOOKS`` in declaration
order, runs applicable hooks in sequence, and short-circuits on the first
``AskClarification`` result.

Hook ordering::

    _universal_materialize (NOT a hook — runs once before the hook chain)
    → TopicExpansionHook                fires for ``relevantTopic`` predicates
    → WeakRetrievalTopicDumpHook        fires after topic expansion when retrieval weak
    → SdgAskClarificationHook           fires for SDG with missing populationType
    → CrsDacSvgExpansionHook   fires for DevelopmentFinance predicates
    → DonorIsObservationFacetHook  fires for CRS_DAC wildcards
    → DenominatorImplicitHook  fires for Person/count without denominator
    → SetCapHook               universal post-hook (caveat at ≥5 SVs)
    → PlaceAvailabilityHook    universal post-hook (availability filtering)
    → RetrievalQualityHook     universal post-hook (data-driven confidence)
    → EmptyResultHook          terminal guard → AskClarification

This package replaces the flat hooks.py module. All externally-consumed names
are re-exported here so every existing import path keeps resolving.
"""

from __future__ import annotations

# The six retrieval names below are called via ``_hooks_pkg.<name>`` in
# registry.py and materialization.py (call-time deref), so tests can patch
# ``dc_search.hooks.<name>`` and the patch will be honored at runtime.
from dc_search.retrieval import (
    dc_call_was_degraded,
    observation_date_ranges,
    stat_var_features_batch,
    variable_date_coverage,
    variable_group,
    variable_groups_batch,
    variable_info_date_ranges,
)

from .context import Hook, HookContext, HookResult
from .date_helpers import _overlaps, _union_range, _year
from .materialization import _build_variables, materialize_many, materialize_via_hooks
from .registry import (
    HOOKS,
    CrsDacSvgExpansionHook,
    DateFilterHook,
    DenominatorImplicitHook,
    DonorIsObservationFacetHook,
    EmptyResultHook,
    PlaceAvailabilityHook,
    RetrievalQualityHook,
    SdgAskClarificationHook,
    SetCapHook,
    TopicExpansionHook,
    WeakRetrievalTopicDumpHook,
)

__all__ = [
    "dc_call_was_degraded",
    "observation_date_ranges",
    "stat_var_features_batch",
    "variable_date_coverage",
    "variable_group",
    "variable_groups_batch",
    "variable_info_date_ranges",
    "Hook",
    "HookContext",
    "HookResult",
    "HOOKS",
    "materialize_many",
    "materialize_via_hooks",
    "CrsDacSvgExpansionHook",
    "DateFilterHook",
    "DenominatorImplicitHook",
    "DonorIsObservationFacetHook",
    "EmptyResultHook",
    "PlaceAvailabilityHook",
    "RetrievalQualityHook",
    "SdgAskClarificationHook",
    "SetCapHook",
    "TopicExpansionHook",
    "WeakRetrievalTopicDumpHook",
    "_build_variables",
    "_overlaps",
    "_union_range",
    "_year",
]
