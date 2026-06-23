"""Dev-finance family constants: five-tuple dcids, constraint properties, and SV construction.

Bespoke by design. This module is the dev-finance family adapter, the one
family-specific module in an otherwise family-agnostic engine. It hardcodes
three kinds of knowledge that the steady-state engine should derive from the
graph instead:

  1. The shape five-tuple (DevelopmentFinance / DevelopmentFinanceFlow /
     measuredValue). The general engine reads the five-tuple off the candidate
     StatVars that recall returns and groups by it, rather than hardcoding it.
  2. The scheme and purpose taxonomies offered to the binder. The general engine
     reads a constraint property's realizable values from the graph per shape,
     rather than carrying hand-listed tuples.
  3. construct_sv_dcid, the ONE/CRS_DAC/<Purpose>-<Scheme>-<Recipient> template.
     This is the only irreducibly bespoke piece. Dev-finance is ~16k flat
     enumerated custom StatVars where search is too noisy to trust, so the engine
     constructs the candidate dcid from bound slots and confirms it with a graph
     read. The standard graph has no uniform template, so this stays a
     per-namespace plugin even after generalization.

Next cut: replace (1) and (2) with graph-derived shape and slot discovery, and
keep only (3) behind the Family registry.

Pure module: no I/O, no LLM, no graph calls.
"""
from __future__ import annotations

from dataclasses import dataclass

# ---------------------------------------------------------------------------
# Family identity
# ---------------------------------------------------------------------------

FAMILY_ID = "dev_finance_crs_dac"

# The monomorphic five-tuple shared by all 16,464 dev-finance SVs.
POP_TYPE_DCID = "DevelopmentFinance"
MEAS_PROP_DCID = "DevelopmentFinanceFlow"
STAT_TYPE_DCID = "measuredValue"
MEAS_QUAL_DCID: str | None = None
MEAS_DENOM_DCID: str | None = None

# ---------------------------------------------------------------------------
# Constraint properties (three slots)
# ---------------------------------------------------------------------------

PROP_SCHEME = "DevelopmentFinanceScheme"     # what-axis
PROP_PURPOSE = "DevelopmentFinancePurpose"   # how-axis
PROP_RECIPIENT = "DevelopmentFinanceRecipient"  # where-axis

CONSTRAINT_PROPS = (PROP_SCHEME, PROP_PURPOSE, PROP_RECIPIENT)

# ---------------------------------------------------------------------------
# Slot labels (display names for SlotKey.label)
# ---------------------------------------------------------------------------

LABEL_SCHEME = "flow type"
LABEL_PURPOSE = "sector / purpose"
LABEL_RECIPIENT = "recipient country or region"

# ---------------------------------------------------------------------------
# Scheme taxonomy (7 values, verified live)
# ---------------------------------------------------------------------------

SCHEMES: tuple[str, ...] = (
    "ODAGrants",
    "ODALoans",
    "OfficialDevelopmentAssistance",   # aggregate (ODA = grants + loans)
    "OtherOfficialFlows",
    "PrivateDevelopmentFinance",
    "ODAEquityInvestment",
    "ODAPrivateSectorInstruments",
)

# ---------------------------------------------------------------------------
# Purpose taxonomy (29 DAC sector codes, verified live)
# The DAC/ prefix is canonical; these are DevelopmentFinancePurposeEnum nodes.
# ---------------------------------------------------------------------------

PURPOSES: tuple[str, ...] = (
    "DAC/Health",                          # 12000 — Health (Total) rollup
    "DAC/BasicHealth",                     # 12220
    "DAC/STDcontrolincludingHIVAIDS",      # 13040
    "DAC/Healtheducation",                 # 12261 — (used in education set)
    "DAC/Medicaleducationtraining",        # 12281 — (used in education set)
    "DAC/HealthPolicyandadministrativemanagement",  # 12110
    "DAC/BasicNutrition",                  # 12240
    "DAC/Malariacontrol",                  # 12262
    "DAC/Tuberculosiscontrol",             # 12263
    "DAC/COVIDRelatedHealth",              # 12264
    "DAC/Reproductivehealthcare",          # 13020
    "DAC/Familyplanning",                  # 13030
    "DAC/PersonneldevelopmentforpopulationandrHR",  # 13081
    "DAC/Education",                       # 11000 — top-level education rollup (no agg node)
    "DAC/BasicEducation",                  # 11220
    "DAC/SecondaryEducation",              # 11320
    "DAC/PostsecondaryEducation",          # 11420
    "DAC/Vocationaleducation",             # 11330
    "DAC/EducationPolicyandadministrativemanagement",  # 11110
    "DAC/Agriculture",                     # 31110
    "DAC/Water",                           # 14000
    "DAC/Energygeneral",                   # 23010
    "DAC/Govtcivilsociety",                # 15110
    "DAC/Humanitarianaid",                 # 72010
    "DAC/Othermultisector",                # 43010
    "DAC/Environmentalprotectiongeneral",  # 41010
    "DAC/TransportStorage",                # 21010
    "DAC/Communications",                  # 22010
    "DAC/Industryminingconstruction",      # 32110
)

# SV dcid construction: ONE/CRS_DAC/<Purpose-slug>-<Scheme>-<Recipient-suffix>
# The engine constructs candidates; confirmation via graph is required before emission.

def purpose_slug(purpose_dcid: str) -> str:
    """Strip the 'DAC/' prefix from a purpose dcid.

    "DAC/Health" → "Health"
    "DAC/STDcontrolincludingHIVAIDS" → "STDcontrolincludingHIVAIDS"
    """
    if purpose_dcid.startswith("DAC/"):
        return purpose_dcid[4:]
    return purpose_dcid


def recipient_suffix(recipient_dcid: str) -> str:
    """Extract the recipient suffix for SV construction.

    "country/ETH" → "ETH"
    "undata-geo/X..." → the last path segment
    Bare strings (e.g. "africa") → returned as-is.
    """
    slash = recipient_dcid.rfind("/")
    return recipient_dcid[slash + 1:] if slash != -1 else recipient_dcid


def construct_sv_dcid(scheme: str, purpose: str, recipient: str) -> str:
    """Construct the candidate SV dcid from bound slot values.

    Pattern: ONE/CRS_DAC/<purpose-slug>-<scheme>-<recipient-suffix>

    The engine confirms this result via a graph node read before emitting a GraphRef.
    Never trust this dcid without graph confirmation.

    Args:
        scheme: DevelopmentFinanceScheme dcid (e.g. "ODAGrants").
        purpose: DevelopmentFinancePurpose dcid (e.g. "DAC/Health").
        recipient: DevelopmentFinanceRecipient dcid (e.g. "country/ETH").

    Returns:
        The candidate SV dcid string.
    """
    return f"ONE/CRS_DAC/{purpose_slug(purpose)}-{scheme}-{recipient_suffix(recipient)}"


# ---------------------------------------------------------------------------
# Family detection helpers
# ---------------------------------------------------------------------------

_SV_PREFIX = "ONE/CRS_DAC/"


def is_dev_finance_sv(dcid: str) -> bool:
    """Return True when dcid looks like a dev-finance SV."""
    return dcid.startswith(_SV_PREFIX)


def is_dev_finance_family(candidate_svs: list[str]) -> bool:
    """Return True when ANY candidate SV belongs to the dev-finance family.

    Even one dev-finance candidate is enough to trigger family recognition,
    because detect is recall-only and may mix in non-family candidates.
    """
    return any(is_dev_finance_sv(sv) for sv in candidate_svs)


# ---------------------------------------------------------------------------
# Recipient-role dcid (the EntityRoleDirectional.role GraphRef target)
# ---------------------------------------------------------------------------

RECIPIENT_ROLE_DCID = "DevelopmentFinanceRecipient"
# Label confirmed live: "Development Finance Recipient"
RECIPIENT_ROLE_LABEL = "Development Finance Recipient"


# ---------------------------------------------------------------------------
# Typed family record
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class Family:
    """A recognized statistical family with its five-tuple and metadata."""

    family_id: str
    label: str
    pop_type_dcid: str
    meas_prop_dcid: str
    stat_type_dcid: str
    meas_qual_dcid: str | None
    meas_denom_dcid: str | None


DEV_FINANCE_FAMILY = Family(
    family_id=FAMILY_ID,
    label="development finance flows",
    pop_type_dcid=POP_TYPE_DCID,
    meas_prop_dcid=MEAS_PROP_DCID,
    stat_type_dcid=STAT_TYPE_DCID,
    meas_qual_dcid=MEAS_QUAL_DCID,
    meas_denom_dcid=MEAS_DENOM_DCID,
)
