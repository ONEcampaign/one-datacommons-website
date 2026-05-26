"""Module-level LRU caches and the shared RLock that guards them.

Defined exactly once here; re-exported by retrieval/__init__.py so that
``dc_search.retrieval.<cache_name>`` resolves for test fixture cleanup.
"""

from __future__ import annotations

import threading

import cachetools

# RLock guards all module-level caches. cachetools.LRUCache is not thread-safe;
# asyncio.to_thread can cause concurrent access on different threads.
_cache_lock = threading.RLock()

# Module-level cache for place resolutions. Both ``resolve_place`` (single)
# and ``resolve_places_batch`` (many) consult and populate this dict, so the
# two functions share one source of truth — calling either warms the cache
# for the other. Empty results (negative resolutions) are cached so cold-cache
# re-queries are free; place names don't churn at process-lifetime granularity.
_resolve_cache: cachetools.LRUCache = cachetools.LRUCache(maxsize=2048)

# Module-level LRU cache for resolve_indicator results.
_resolve_indicator_cache_lru: cachetools.LRUCache = cachetools.LRUCache(maxsize=2048)

# Module-level cache for per-SV features. SV metadata (populationType,
# constraints, etc.) is essentially immutable over a process lifetime, so we
# memoize at SV granularity to share results across overlapping batches.
# Missing SVs are NOT cached — a re-request will retry the network fetch
# (handles transient DC API hiccups). Cleared by tests via `_features_cache.clear()`.
_features_cache: cachetools.LRUCache = cachetools.LRUCache(maxsize=4096)

# Module-level LRU cache for stat_var_features single-SV wrapper.
_stat_var_features_cache_lru: cachetools.LRUCache = cachetools.LRUCache(maxsize=2048)

# Module-level cache for per-entity SV inventories. Shared between
# ``variables_for_entity`` and ``variables_for_entities_batch`` so that
# a prior single-entity call warms the cache for future batch calls.
_entity_svs_cache: cachetools.LRUCache = cachetools.LRUCache(maxsize=512)

# Module-level LRU cache for variables_for_entity single-entity wrapper.
_variables_for_entity_cache_lru: cachetools.LRUCache = cachetools.LRUCache(maxsize=2048)

# Module-level cache for targeted presence checks. Keyed by
# (sorted_variable_dcids, sorted_entity_dcids) so the result is reused when
# the same SVxentity combination is queried again (e.g. on a second pipeline
# run within the same session). Cleared by tests via ``_presence_cache.clear()``.
_presence_cache: cachetools.LRUCache = cachetools.LRUCache(maxsize=1024)

# Module-level cache for coverage fetches. Keyed by
# (sorted_variable_dcids, sorted_entity_dcids). Cleared by tests.
_coverage_cache: cachetools.LRUCache = cachetools.LRUCache(maxsize=1024)

# Module-level cache for variable/info date ranges. Cleared by tests.
_variable_info_dates_cache: cachetools.LRUCache = cachetools.LRUCache(maxsize=1024)

# Module-level cache for facet-select observation ranges. Cleared by tests.
_observation_facet_ranges_cache: cachetools.LRUCache = cachetools.LRUCache(maxsize=1024)

# Module-level cache for observation date ranges. Cleared by tests.
_observation_dates_cache: cachetools.LRUCache = cachetools.LRUCache(maxsize=1024)

# Module-level LRU cache for child_vars_of_groups.
_child_vars_of_groups_cache_lru: cachetools.LRUCache = cachetools.LRUCache(maxsize=2048)

# Module-level arc cache for expand_topic's BFS walk.
# Maps (dcid, expression) → list of child node dicts parsed from the response.
# Consulted before each BFS level fetch; populated as responses arrive.
_topic_arc_cache: cachetools.LRUCache = cachetools.LRUCache(maxsize=2048)

# Module-level LRU cache for expand_topic results.
_expand_topic_cache_lru: cachetools.LRUCache = cachetools.LRUCache(maxsize=2048)

# Module-level LRU cache for topic_metadata_batch results.
_topic_metadata_batch_cache_lru: cachetools.LRUCache = cachetools.LRUCache(maxsize=2048)

# Module-level LRU cache for place_names_batch results.
_place_names_cache: cachetools.LRUCache = cachetools.LRUCache(maxsize=2048)

# Module-level cache for per-group info. Both ``variable_group`` (single) and
# ``variable_groups_batch`` (many) consult and populate this dict so the two
# functions share one source of truth — calling either warms the cache for the
# other. Cleared by tests via ``_vgroups_cache.clear()``.
_vgroups_cache: cachetools.LRUCache = cachetools.LRUCache(maxsize=1024)
