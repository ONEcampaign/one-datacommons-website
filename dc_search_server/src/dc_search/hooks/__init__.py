"""Composable hook pipeline for materialization.

Each hook implements applies() and run(). The dispatcher iterates HOOKS
in order and short-circuits on AskClarification.

All externally-consumed names are re-exported for backward compatibility.
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
from .crs_dac_recipient_set import CrsDacRecipientSetHook
from .date_helpers import _overlaps, _union_range, _year
from .materialization import _build_variables, materialize_many, materialize_via_hooks
from .projection_enrichment import ProjectionEnrichmentHook
from .registry import (
    HOOKS,
    CrsDacRetrievalRecoveryHook,
    CrsDacWildcardExpansionHook,
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
    "CrsDacRecipientSetHook",
    "CrsDacRetrievalRecoveryHook",
    "CrsDacWildcardExpansionHook",
    "DateFilterHook",
    "DenominatorImplicitHook",
    "DonorIsObservationFacetHook",
    "EmptyResultHook",
    "PlaceAvailabilityHook",
    "ProjectionEnrichmentHook",
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
