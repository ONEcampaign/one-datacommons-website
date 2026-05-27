"""High-level helpers for data discrimination.

Wraps the official datacommons-client and reshapes responses into
compact Python objects for LLM consumption. Implemented over V2.

The four main primitives:
1. resolve_place — name → candidate DCIDs
2. stat_var_features — SV DCID → structured graph features
3. variables_for_entity — entity DCID → SV DCIDs with observations
4. variable_group — StatVarGroup traversal

All externally-consumed names are re-exported for backward compatibility.
"""

from __future__ import annotations

# get_client is a package attribute so that submodule network functions
# calling ``graph.get_client()`` honor ``patch("dc_search.retrieval.get_client", ...)``.
from dc_search.client import get_client

# Caches and lock — defined exactly once in _cache.py and re-exported so
# ``dc_search.retrieval.<cache_name>`` resolves for test fixture cleanup.
from ._cache import (
    _cache_lock,
    _child_places_cache_lru,
    _child_vars_of_groups_cache_lru,
    _coverage_cache,
    _entity_svs_cache,
    _expand_topic_cache_lru,
    _features_cache,
    _inverse_arcs_cache_lru,
    _observation_dates_cache,
    _observation_facet_ranges_cache,
    _parent_countries_cache_lru,
    _place_names_cache,
    _presence_cache,
    _resolve_cache,
    _resolve_indicator_cache_lru,
    _stat_var_features_cache_lru,
    _topic_arc_cache,
    _topic_metadata_batch_cache_lru,
    _variable_info_dates_cache,
    _variables_for_entity_cache_lru,
    _vgroups_cache,
)

# Degraded-call ContextVar and helpers — defined exactly once in _degraded.py
# and re-exported here so import paths remain stable.
from ._degraded import _dc_call_degraded, dc_call_was_degraded, reset_dc_call_degraded

# Indicators / stat-var features / variable groups
from .indicator import (
    _BATCH_PROPS,
    SV_DEFINING_PROPS,
    IndicatorCandidate,
    StatVarFeatures,
    VariableGroupInfo,
    resolve_indicator,
    stat_var_features,
    stat_var_features_batch,
    svs_by_inverse_arcs,
    variable_group,
    variable_groups_batch,
)

# Observation / date-coverage / variable discovery
from .observation import (
    _VARIABLE_INFO_DATE_CAP,
    DateCoverage,
    _parse_observation,
    observation_date_ranges,
    observation_facet_ranges,
    presence_for_entities,
    variable_date_coverage,
    variable_info_date_ranges,
    variables_for_entities_batch,
    variables_for_entity,
)

# ---------------------------------------------------------------------------
# Concern submodule symbols
# ---------------------------------------------------------------------------
# Places
from .places import (
    PlaceCandidate,
    child_places_batch,
    parent_countries_batch,
    place_names_batch,
    resolve_place,
    resolve_places_batch,
)

# Topics / group expansion
from .topics import (
    TopicMetadata,
    child_vars_of_groups,
    expand_topic,
    topic_metadata_batch,
)

__all__ = [
    "get_client",
    "_cache_lock",
    "_child_places_cache_lru",
    "_child_vars_of_groups_cache_lru",
    "_coverage_cache",
    "_entity_svs_cache",
    "_expand_topic_cache_lru",
    "_features_cache",
    "_inverse_arcs_cache_lru",
    "_observation_dates_cache",
    "_observation_facet_ranges_cache",
    "_parent_countries_cache_lru",
    "_place_names_cache",
    "_presence_cache",
    "_resolve_cache",
    "_resolve_indicator_cache_lru",
    "_stat_var_features_cache_lru",
    "_topic_arc_cache",
    "_topic_metadata_batch_cache_lru",
    "_variable_info_dates_cache",
    "_variables_for_entity_cache_lru",
    "_vgroups_cache",
    "_dc_call_degraded",
    "dc_call_was_degraded",
    "reset_dc_call_degraded",
    "PlaceCandidate",
    "child_places_batch",
    "parent_countries_batch",
    "place_names_batch",
    "resolve_place",
    "resolve_places_batch",
    "_BATCH_PROPS",
    "IndicatorCandidate",
    "StatVarFeatures",
    "SV_DEFINING_PROPS",
    "VariableGroupInfo",
    "resolve_indicator",
    "stat_var_features",
    "stat_var_features_batch",
    "svs_by_inverse_arcs",
    "variable_group",
    "variable_groups_batch",
    "DateCoverage",
    "_VARIABLE_INFO_DATE_CAP",
    "_parse_observation",
    "observation_date_ranges",
    "observation_facet_ranges",
    "presence_for_entities",
    "variable_date_coverage",
    "variable_info_date_ranges",
    "variables_for_entities_batch",
    "variables_for_entity",
    "TopicMetadata",
    "child_vars_of_groups",
    "expand_topic",
    "topic_metadata_batch",
]
