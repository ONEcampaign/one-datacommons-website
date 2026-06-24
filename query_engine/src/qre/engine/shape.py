"""Shape classification and ShapeDraft construction.

Pure module: no I/O, no LLM, no graph calls.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING

from qre.engine.axis import AXIS_OVERRIDES, classify_axis
from qre.engine.families import (
    DEV_FINANCE_FAMILY,
    LABEL_PURPOSE,
    LABEL_RECIPIENT,
    LABEL_SCHEME,
    PROP_PURPOSE,
    PROP_RECIPIENT,
    PROP_SCHEME,
    Family,
    is_dev_finance_family,
)
from qre.models import Axis

if TYPE_CHECKING:
    from qre.engine.families.protocol import FamilyRule

# ShapeDraft — the ungrounded shape representation used inside the pipeline.


@dataclass(frozen=True)
class SlotKeyDraft:
    """Ungrounded slot identity: axis + property dcid + display label.

    The property_dcid may be None for the when/source axis slots (observation-facet layer).
    """

    axis: Axis
    property_dcid: str | None  # None for when/source
    label: str


@dataclass(frozen=True)
class ShapeDraft:
    """Ungrounded shape: five-tuple dcids + ordered slot keys.

    family_rule, sv_arc_facts, and slot_taxonomy are stamped by discover.derive_shapes
    to avoid resolvers re-reading node_arcs per SV.
    """

    shape_id: str
    label: str

    # Five-tuple (ungrounded dcids)
    pop_type_dcid: str
    meas_prop_dcid: str
    stat_type_dcid: str
    meas_qual_dcid: str | None
    meas_denom_dcid: str | None

    # Slot keys in display order
    slot_keys: tuple[SlotKeyDraft, ...]

    # Matched FamilyRule (if any), stamped by discover.derive_shapes for resolver dispatch.
    family_rule: "FamilyRule | None" = field(default=None, compare=False)
    # Per-SV arc facts dict (sv_dcid -> raw arcs), carried from derive_shapes.
    sv_arc_facts: "dict[str, dict] | None" = field(default=None, compare=False)
    # Per-shape bind taxonomy: "axis:property_dcid" -> realizable dcid lists.
    slot_taxonomy: "dict[str, list[str]] | None" = field(default=None, compare=False)
    # Cosine score of the representative SV (first SV in insertion order). Defaults to 1.0
    # when scores are absent (legacy paths, offline fixtures) to preserve backward compatibility.
    representative_score: float = field(default=1.0, compare=False)


def _make_slot_key(property_dcid: str, label: str) -> SlotKeyDraft:
    """Build a SlotKeyDraft for a constraint property, applying AXIS_OVERRIDES.

    The axis for a given property is looked up in AXIS_OVERRIDES first; if absent,
    the property defaults to "how".
    """
    axis: Axis = AXIS_OVERRIDES.get(property_dcid, "how")  # type: ignore[assignment]
    return SlotKeyDraft(axis=axis, property_dcid=property_dcid, label=label)


# Observation-facet slots
_WHEN_SLOT = SlotKeyDraft(axis="when", property_dcid=None, label="time window")
_SOURCE_SLOT = SlotKeyDraft(axis="source", property_dcid=None, label="data source")


def family_for(candidate_svs: list[str]) -> Family | None:
    """Return the recognised Family for a list of candidate SV dcids, or None.

    Currently recognises only the dev-finance family (ONE/CRS_DAC/* prefix).
    A single matching candidate is sufficient — detect is recall-only and may
    mix in non-family candidates.

    Future families are added here as additional elif branches.
    """
    if is_dev_finance_family(candidate_svs):
        return DEV_FINANCE_FAMILY
    return None


def build_shape(family: Family) -> ShapeDraft:
    """Build a ShapeDraft for a recognised family.

    For dev-finance the shape is monomorphic: all SVs share the single five-tuple.
    """
    if family.family_id == DEV_FINANCE_FAMILY.family_id:
        return _build_dev_finance_shape(family)
    raise ValueError(f"Unknown family: {family.family_id!r}")


def _build_dev_finance_shape(family: Family) -> ShapeDraft:
    scheme_slot = _make_slot_key(PROP_SCHEME, LABEL_SCHEME)
    purpose_slot = _make_slot_key(PROP_PURPOSE, LABEL_PURPOSE)
    recipient_slot = _make_slot_key(PROP_RECIPIENT, LABEL_RECIPIENT)

    return ShapeDraft(
        shape_id=family.family_id,
        label=family.label,
        pop_type_dcid=family.pop_type_dcid,
        meas_prop_dcid=family.meas_prop_dcid,
        stat_type_dcid=family.stat_type_dcid,
        meas_qual_dcid=family.meas_qual_dcid,
        meas_denom_dcid=family.meas_denom_dcid,
        slot_keys=(scheme_slot, purpose_slot, recipient_slot, _WHEN_SLOT, _SOURCE_SLOT),
    )


def shape_draft_from(
    *,
    shape_id: str,
    label: str,
    pop_type_dcid: str,
    meas_prop_dcid: str,
    stat_type_dcid: str,
    meas_qual_dcid: str | None,
    meas_denom_dcid: str | None,
    constraint_props: list[str],
    prop_labels: dict[str, str],
    prop_observed_values: dict[str, list[str]],
    family_rule: "FamilyRule | None" = None,
    sv_arc_facts: "dict[str, dict] | None" = None,
    slot_taxonomy: "dict[str, list[str]] | None" = None,
    representative_score: float = 1.0,
) -> ShapeDraft:
    """Build a ShapeDraft from five-tuple and constraint properties.

    Uses classify_axis(property_dcid, observed_values) to apply the date heuristic
    and place-fraction auto-rule (axis.py:72) alongside AXIS_OVERRIDES.

    Args:
        shape_id:            Shape identifier.
        label:               Human-readable label.
        pop_type_dcid:       populationType dcid.
        meas_prop_dcid:      measuredProperty dcid.
        stat_type_dcid:      statType dcid.
        meas_qual_dcid:      measurementQualifier dcid or None.
        meas_denom_dcid:     measurementDenominator dcid or None.
        constraint_props:    Ordered list of constraint property dcids.
        prop_labels:         Display label per property dcid.
        prop_observed_values: Sample values per property (for classify_axis).
        family_rule:         Matched FamilyRule.
        sv_arc_facts:        Per-SV arc facts dict.
        slot_taxonomy:       Per-shape bind taxonomy.

    Returns:
        ShapeDraft with constraint slots (from classify_axis) plus when/source slots.
    """
    constraint_slot_keys: list[SlotKeyDraft] = []
    for prop_dcid in constraint_props:
        observed = prop_observed_values.get(prop_dcid, [])
        axis = classify_axis(prop_dcid, observed)
        label_text = prop_labels.get(prop_dcid, prop_dcid)
        constraint_slot_keys.append(
            SlotKeyDraft(axis=axis, property_dcid=prop_dcid, label=label_text)
        )

    all_slots = tuple(constraint_slot_keys) + (_WHEN_SLOT, _SOURCE_SLOT)

    return ShapeDraft(
        shape_id=shape_id,
        label=label,
        pop_type_dcid=pop_type_dcid,
        meas_prop_dcid=meas_prop_dcid,
        stat_type_dcid=stat_type_dcid,
        meas_qual_dcid=meas_qual_dcid,
        meas_denom_dcid=meas_denom_dcid,
        slot_keys=all_slots,
        family_rule=family_rule,
        sv_arc_facts=sv_arc_facts,
        slot_taxonomy=slot_taxonomy,
        representative_score=representative_score,
    )
