"""Shape classification and ShapeDraft construction.

Pure module: no I/O, no LLM, no graph calls.
"""
from __future__ import annotations

from dataclasses import dataclass

from qre.engine.axis import AXIS_OVERRIDES
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
    """Ungrounded shape: five-tuple dcids + ordered slot keys."""

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
