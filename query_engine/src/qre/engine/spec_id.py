"""Deterministic spec_id computation.

spec_id = f"spec_{sha1(shape_id + '|' + canonical)[:16]}"

Pure module: no I/O, no imports from the rest of the engine.
"""
from __future__ import annotations

import hashlib

from qre.models import BindingAbsent, BindingSet, BindingUnbound, BindingValue, Slot


def _slot_string(slot: Slot) -> str:
    """Encode one slot as a canonical string fragment."""
    axis = slot.key.axis
    prop = slot.key.property or None
    prop_str = prop.dcid if prop is not None else ""

    binding = slot.binding
    prefix = f"{axis}:{prop_str}"

    if isinstance(binding, BindingValue):
        sv = binding.value
        if sv.time_window is not None:
            tw = sv.time_window
            if tw.start_year is not None and tw.end_year is not None:
                window_part = f"t{tw.start_year}-{tw.end_year}"
            elif tw.start_year is not None:
                window_part = f"t{tw.start_year}-"
            else:
                window_part = f"t-{tw.end_year}"
            return f"{prefix}:when:{window_part}"
        if sv.ref is not None:
            value_key = sv.ref.dcid
        elif sv.literal is not None:
            value_key = sv.literal
        else:
            value_key = ""
        return f"{prefix}:value:{value_key}"

    if isinstance(binding, BindingSet):
        dcids = sorted(
            v.ref.dcid if v.ref is not None else (v.literal or "")
            for v in binding.values
        )
        return f"{prefix}:set:{'|'.join(dcids)}"

    if isinstance(binding, BindingUnbound):
        return f"{prefix}:unbound:#unbound"

    if isinstance(binding, BindingAbsent):
        return f"{prefix}:absent:#absent"

    # Unknown binding type — should not happen with the contract's closed union
    raise TypeError(f"Unknown binding type: {type(binding)!r}")


def compute_spec_id(shape_id: str, slots: list[Slot]) -> str:
    """Compute a deterministic spec_id for a (shape_id, slots) pair.

    Slot order in the input list does not matter: slots are sorted by
    (axis, property_dcid or "") before canonicalisation.

    Returns a string of the form "spec_<16 hex chars>".
    """
    sorted_slots = sorted(
        slots,
        key=lambda s: (s.key.axis, s.key.property.dcid if s.key.property else ""),
    )
    canonical_parts = [_slot_string(s) for s in sorted_slots]
    canonical = ";".join(canonical_parts)
    payload = f"{shape_id}|{canonical}"
    digest = hashlib.sha1(payload.encode()).hexdigest()
    return f"spec_{digest[:16]}"
