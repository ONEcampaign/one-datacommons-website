"""Module-level LRU caches and shared RLock.

Re-exported by retrieval/__init__.py for test fixture cleanup.
"""

from __future__ import annotations

import threading

import cachetools

# RLock guards all caches; LRUCache is not thread-safe.
_cache_lock = threading.RLock()

# resolve_place and resolve_places_batch share this cache; either call warms it.
# Negative resolutions cached; place names stable over process lifetime.
_resolve_cache: cachetools.LRUCache = cachetools.LRUCache(maxsize=2048)

# Module-level LRU cache for resolve_indicator results.
_resolve_indicator_cache_lru: cachetools.LRUCache = cachetools.LRUCache(maxsize=2048)

# SV metadata is immutable per process; memoized at SV granularity.
# Missing SVs not cached (retry on re-request).
_features_cache: cachetools.LRUCache = cachetools.LRUCache(maxsize=4096)

# Module-level LRU cache for stat_var_features single-SV wrapper.
_stat_var_features_cache_lru: cachetools.LRUCache = cachetools.LRUCache(maxsize=2048)

# variables_for_entity and variables_for_entities_batch share this cache.
_entity_svs_cache: cachetools.LRUCache = cachetools.LRUCache(maxsize=512)

# Module-level LRU cache for variables_for_entity single-entity wrapper.
_variables_for_entity_cache_lru: cachetools.LRUCache = cachetools.LRUCache(maxsize=2048)

# Presence checks keyed by (sorted SVs, sorted entities).
# Bumped to 2048 for child-place expansion (up to 300 entries, increasing entropy).
_presence_cache: cachetools.LRUCache = cachetools.LRUCache(maxsize=2048)

# Coverage fetches keyed by (sorted SVs, sorted entities).
# Bumped to 2048 for child-place expansion.
_coverage_cache: cachetools.LRUCache = cachetools.LRUCache(maxsize=2048)

# Module-level cache for variable/info date ranges. Cleared by tests.
_variable_info_dates_cache: cachetools.LRUCache = cachetools.LRUCache(maxsize=1024)

# Module-level cache for facet-select observation ranges. Cleared by tests.
_observation_facet_ranges_cache: cachetools.LRUCache = cachetools.LRUCache(maxsize=1024)

# Module-level cache for observation date ranges. Cleared by tests.
_observation_dates_cache: cachetools.LRUCache = cachetools.LRUCache(maxsize=1024)

# Module-level LRU cache for child_vars_of_groups.
_child_vars_of_groups_cache_lru: cachetools.LRUCache = cachetools.LRUCache(maxsize=2048)

# Arc cache for expand_topic BFS: (dcid, expression) → child nodes.
_topic_arc_cache: cachetools.LRUCache = cachetools.LRUCache(maxsize=2048)

# Module-level LRU cache for expand_topic results.
_expand_topic_cache_lru: cachetools.LRUCache = cachetools.LRUCache(maxsize=2048)

# Module-level LRU cache for topic_metadata_batch results.
_topic_metadata_batch_cache_lru: cachetools.LRUCache = cachetools.LRUCache(maxsize=2048)

# Module-level LRU cache for place_names_batch results.
_place_names_cache: cachetools.LRUCache = cachetools.LRUCache(maxsize=2048)

# child_places_batch results: keyed by (sorted parents, type, cap).
_child_places_cache_lru: cachetools.LRUCache = cachetools.LRUCache(maxsize=2048)

# parent_countries_batch results: keyed by sorted parents.
_parent_countries_cache_lru: cachetools.LRUCache = cachetools.LRUCache(maxsize=2048)

# svs_by_inverse_arcs results: keyed by (sorted values, sorted properties).
_inverse_arcs_cache_lru: cachetools.LRUCache = cachetools.LRUCache(maxsize=512)

# variable_group and variable_groups_batch share this cache.
_vgroups_cache: cachetools.LRUCache = cachetools.LRUCache(maxsize=1024)
