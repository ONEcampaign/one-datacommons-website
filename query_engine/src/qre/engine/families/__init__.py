"""Family registry package for the QRE engine.

Public surface:
  FamilyRule, FamilyResolver  — protocol and record from families.protocol
  REGISTRY, rule_for          — ordered registry and lookup from families.registry

To add a family:
  1. Create families/<name>.py with a FamilyResolver implementation and a FamilyRule.
  2. Import the FamilyRule in families/registry.py and insert it before STANDARD_RULE
     (the catch-all must remain last).
  3. Write golden tests under tests/engine/.
"""
from __future__ import annotations

# --- Dev-finance adapter symbols ---
# Re-exported for backward compat with shape.py, core.py, and tests/.
# These remain transitional until shape.py reads from the resolver directly.
from qre.engine.families.dev_finance import (
    DEV_FINANCE_FAMILY,
    DONOR_ROLE_DCID,
    LABEL_PURPOSE,
    LABEL_RECIPIENT,
    LABEL_SCHEME,
    MEAS_DENOM_DCID,
    MEAS_PROP_DCID,
    MEAS_QUAL_DCID,
    POP_TYPE_DCID,
    PROP_PURPOSE,
    PROP_RECIPIENT,
    PROP_SCHEME,
    RECIPIENT_ROLE_DCID,
    STAT_TYPE_DCID,
    Family,
    is_dev_finance_family,
)

# --- Stable public surface ---
from qre.engine.families.protocol import FamilyResolver, FamilyRule
from qre.engine.families.registry import REGISTRY, rule_for, rule_for_shape_id

__all__ = [
    # Stable
    "FamilyResolver",
    "FamilyRule",
    "REGISTRY",
    "rule_for",
    "rule_for_shape_id",
    # Dev-finance transitional (consumed by shape.py, core.py, tests/)
    "DEV_FINANCE_FAMILY",
    "Family",
    "LABEL_PURPOSE",
    "LABEL_RECIPIENT",
    "LABEL_SCHEME",
    "MEAS_DENOM_DCID",
    "MEAS_PROP_DCID",
    "MEAS_QUAL_DCID",
    "POP_TYPE_DCID",
    "PROP_PURPOSE",
    "DONOR_ROLE_DCID",
    "PROP_RECIPIENT",
    "PROP_SCHEME",
    "RECIPIENT_ROLE_DCID",
    "STAT_TYPE_DCID",
    "is_dev_finance_family",
]
