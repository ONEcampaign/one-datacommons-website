"""Dev-finance CRS DAC family adapter.

Bespoke by design.  This module is the dev-finance family adapter — the one
family-specific module in an otherwise family-agnostic engine.  It isolates
three kinds of knowledge that the general engine does not need:

  1. The shape five-tuple (DevelopmentFinance / DevelopmentFinanceFlow /
     measuredValue).  The general engine reads the five-tuple off the candidate
     StatVars that recall returns and groups by it rather than hardcoding it.
     Dev-finance retains the five-tuple as constants for use in tests and as
     the axis-pin seed; discover.py reads the same values off live SVs.

  2. The scheme and purpose taxonomies offered to the binder.  The general
     engine reads constraint-property realizable values from the graph per
     shape; dev-finance keeps hand-verified seed tuples (SCHEMES, PURPOSES)
     because the ~16 k flat CRS lattice means a single detected SV produces a
     collapsed taxonomy that under-covers the full scheme space.

  3. construct_sv_dcid — the ONE/CRS_DAC/<Purpose>-<Scheme>-<Recipient>
     template. The general detect-confirm path cannot reliably reproduce every
     dev-finance golden (df-04 aggregate scheme, df-09 unbound-scheme probing,
     df-12 no-observations to Nauru). construct_sv_dcid thus remains a
     registered per-namespace constructor pre-step inside DevFinanceResolver,
     called before the general observation probe. The standard family has no
     template and routes exclusively through detect-confirm.

Pure module (data + helpers): no I/O, no LLM, no graph calls in this module.
The resolver's resolve() method receives a graph and calls it, but the module
itself imports no graph client.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

from qre.engine.families.protocol import FamilyRule

if TYPE_CHECKING:
    from qre.engine.bind import SlotBindingDraft
    from qre.engine.extract import DateRequest
    from qre.engine.graph import EngineGraphClient
    from qre.engine.retrieve import Materialised, NoDataDraft
    from qre.engine.shape import ShapeDraft

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

PROP_SCHEME = "DevelopmentFinanceScheme"      # what-axis
PROP_PURPOSE = "DevelopmentFinancePurpose"    # how-axis
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
    "OfficialDevelopmentAssistance",    # aggregate (ODA = grants + loans)
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
    "DAC/Health",                           # 12000 — Health (Total) rollup
    "DAC/BasicHealth",                      # 12220
    "DAC/STDcontrolincludingHIVAIDS",       # 13040
    "DAC/Healtheducation",                  # 12261 — (used in education set)
    "DAC/Medicaleducationtraining",         # 12281 — (used in education set)
    "DAC/HealthPolicyandadministrativemanagement",  # 12110
    "DAC/BasicNutrition",                   # 12240
    "DAC/Malariacontrol",                   # 12262
    "DAC/Tuberculosiscontrol",              # 12263
    "DAC/COVIDRelatedHealth",               # 12264
    "DAC/Reproductivehealthcare",           # 13020
    "DAC/Familyplanning",                   # 13030
    "DAC/PersonneldevelopmentforpopulationandrHR",  # 13081
    "DAC/Education",                        # 11000 — top-level education rollup
    "DAC/BasicEducation",                   # 11220
    "DAC/SecondaryEducation",               # 11320
    "DAC/PostsecondaryEducation",           # 11420
    "DAC/Vocationaleducation",              # 11330
    "DAC/EducationPolicyandadministrativemanagement",  # 11110
    "DAC/Agriculture",                      # 31110
    "DAC/Water",                            # 14000
    "DAC/Energygeneral",                    # 23010
    "DAC/Govtcivilsociety",                 # 15110
    "DAC/Humanitarianaid",                  # 72010
    "DAC/Othermultisector",                 # 43010
    "DAC/Environmentalprotectiongeneral",   # 41010
    "DAC/TransportStorage",                 # 21010
    "DAC/Communications",                   # 22010
    "DAC/Industryminingconstruction",       # 32110
)

# ---------------------------------------------------------------------------
# SV dcid construction
# ONE/CRS_DAC/<Purpose-slug>-<Scheme>-<Recipient-suffix>
# The engine constructs candidates; graph confirmation is required before emission.
# ---------------------------------------------------------------------------


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
# Donor-role dcid (the EntityRoleDirectional.role GraphRef target for a named
# "from" donor). The donor is the observation's observationAbout entity, not a
# constraint property (see .design/place-as-constraint-seam.md), so its role
# GraphRef is observationAbout — the live DC property that sources the directional
# role, parallel to DevelopmentFinanceRecipient for the recipient. render.py reads
# entity.ref.label, never this ref; it exists only to satisfy the contract field
# and the eval groundedness walk (which requires a live node).
# ---------------------------------------------------------------------------

DONOR_ROLE_DCID = "observationAbout"
DONOR_ROLE_LABEL = "observation about"


# ---------------------------------------------------------------------------
# Typed family record
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class Family:
    """A recognized statistical family with its five-tuple and metadata.

    Consumed by shape.build_shape and the shape/retrieve/assemble tests via
    the DEV_FINANCE_FAMILY instance.
    """

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


# ---------------------------------------------------------------------------
# Axis pins for dev-finance (complements global AXIS_OVERRIDES)
# ---------------------------------------------------------------------------

DEV_FINANCE_AXIS_PINS: dict[str, str] = {
    "DevelopmentFinanceRecipient": "where",
    "DevelopmentFinanceScheme": "what",
    # DevelopmentFinancePurpose is "how" by default (no override needed)
}


# ---------------------------------------------------------------------------
# Construct-path helper (the primary dev-finance resolution logic)
# ---------------------------------------------------------------------------

# Default donor for has_data probes when no named donor is provided.
_DEFAULT_PROBE_DONOR = "country/USA"


def _find_binding(
    bindings: "list[SlotBindingDraft]", property_dcid: str
) -> "SlotBindingDraft | None":
    for b in bindings:
        if b.property_dcid == property_dcid:
            return b
    return None


def _construct_resolve(
    *,
    bindings: "list[SlotBindingDraft]",
    recipient_dcid: str | None,
    donor_dcid: str | None,
    graph: "EngineGraphClient",
    date_request: "DateRequest | None" = None,
) -> "Materialised | NoDataDraft | None":
    """Run the construct_sv_dcid path and return a result, or None to fall through.

    Returns None when no slot bindings are present at all (signals: try the
    general graph_confirm_resolve fallback instead).  Returns a Materialised or
    NoDataDraft for all other cases — including the unbound-scheme probe path
    (df-09) and the no-observations path (df-12).
    """
    # Import here to avoid a circular import at module level.
    from qre.engine.coverage import coverage_from_facets  # noqa: PLC0415
    from qre.engine.graph import Facet  # noqa: PLC0415
    from qre.engine.retrieve import Materialised, NoDataDraft  # noqa: PLC0415

    scheme_binding = _find_binding(bindings, PROP_SCHEME)
    purpose_binding = _find_binding(bindings, PROP_PURPOSE)

    if recipient_dcid is None:
        return NoDataDraft(reason="variable_not_resolved")

    probe_donor = donor_dcid or _DEFAULT_PROBE_DONOR

    # Unbound-scheme path (df-09): all schemes open; probe one member for has_data.
    scheme_kind = scheme_binding.kind if scheme_binding else "unbound"
    if scheme_kind in ("unbound", "absent"):
        if (
            purpose_binding
            and purpose_binding.kind in ("value", "set")
            and purpose_binding.value_dcids
        ):
            probe_purpose_dcids = list(purpose_binding.value_dcids)
        else:
            probe_purpose_dcids = ["DAC/Health"]

        probe_scheme = SCHEMES[0]
        probe_facets: list[Facet] = []
        for purpose_dcid in probe_purpose_dcids:
            probe_sv = construct_sv_dcid(probe_scheme, purpose_dcid, recipient_dcid)
            probe_facets.extend(
                graph.observation_facets(stat_var=probe_sv, entity=probe_donor)
            )

        if not probe_facets or not any(f.obs_count > 0 for f in probe_facets):
            return NoDataDraft(reason="no_observations")

        # This probes a single scheme (SCHEMES[0]) as a has-data sentinel for a spec
        # that spans all schemes, so an exact count would understate the real footprint.
        # Emit the breadth lens, not a misleading exact count.
        coverage = coverage_from_facets(
            probe_facets,
            date_request=date_request,
            facet_label="donors",
            obs_label="years",
            allow_exact=False,
        )
        return Materialised(
            sv_dcids=[],
            facets=probe_facets,
            has_data=True,
            coverage=coverage,
        )

    # Purpose must be bound (value or set) to construct SVs.
    if purpose_binding is None or purpose_binding.kind == "unbound":
        # Neither scheme nor purpose is bound — no construct path available.
        # Return None to signal the fallback path.
        return None

    # Collect scheme and purpose dcids.
    # scheme_binding is non-None here (scheme_kind is "value" or "set" only when bound).
    if scheme_kind == "value":
        scheme_dcids = scheme_binding.value_dcids[:1] if scheme_binding.value_dcids else []
    elif scheme_kind == "set":
        scheme_dcids = list(scheme_binding.value_dcids)
    else:
        scheme_dcids = []

    if purpose_binding.kind == "value":
        purpose_dcids = purpose_binding.value_dcids[:1] if purpose_binding.value_dcids else []
    elif purpose_binding.kind == "set":
        purpose_dcids = list(purpose_binding.value_dcids)
    else:
        purpose_dcids = []

    if not scheme_dcids or not purpose_dcids:
        return NoDataDraft(reason="variable_not_resolved")

    # Construct and confirm each (scheme × purpose) × recipient combination.
    confirmed_svs: list[str] = []
    all_facets: list[Facet] = []
    for scheme in scheme_dcids:
        for purpose in purpose_dcids:
            sv_dcid = construct_sv_dcid(scheme, purpose, recipient_dcid)
            if graph.node_label(sv_dcid) is None:
                continue
            confirmed_svs.append(sv_dcid)
            facets = graph.observation_facets(stat_var=sv_dcid, entity=probe_donor)
            all_facets.extend(facets)

    if not confirmed_svs:
        return NoDataDraft(reason="no_observations")

    has_data = any(f.obs_count > 0 for f in all_facets)
    if not has_data:
        return NoDataDraft(reason="no_observations")

    coverage = coverage_from_facets(
        all_facets, date_request=date_request, facet_label="donors", obs_label="years"
    )
    return Materialised(
        sv_dcids=confirmed_svs,
        facets=all_facets,
        has_data=has_data,
        coverage=coverage,
    )


# ---------------------------------------------------------------------------
# DevFinanceResolver
# ---------------------------------------------------------------------------

class _DevFinanceResolver:
    """Resolver for the ONE/CRS_DAC/* family.

    Resolution strategy: try construct_sv_dcid(scheme, purpose, recipient) + confirm
    first — this is the exact materialise path preserving df-04/df-09/df-12 behaviour.
    Falls through to discover.graph_confirm_resolve only when construct yields nothing
    (e.g. unbound purpose, which cannot be templated).

    Why construct_sv_dcid survives: detect returns 35-44 noisy SVs per dev-finance
    query; the expected recipient-specific SV is often absent from detect results; and
    the 16k flat CRS lattice is not enumerable via detect alone. construct_sv_dcid
    thus remains a registered per-namespace constructor pre-step.

    Instantiated once at module level (``DEV_FINANCE_RESOLVER``).

    ``slot_taxonomy_seed`` is the private adapter copy of the scheme and purpose
    taxonomies. discover.read_slot_taxonomy consumes it so the bind prompt always
    receives the full hand-verified taxonomy, not just the subset a single detected
    SV happens to carry.
    """

    namespace: str = _SV_PREFIX

    # Bind taxonomy seed: full hand-verified scheme and purpose values,
    # keyed by constraint-property dcid.  read_slot_taxonomy uses this
    # instead of the observed-union (B1) to keep dev-finance bind byte-identical.
    slot_taxonomy_seed: dict[str, list[str]] = {
        PROP_SCHEME: list(SCHEMES),
        PROP_PURPOSE: list(PURPOSES),
    }

    def matches(self, *, candidate_svs: list[str]) -> bool:
        return is_dev_finance_family(candidate_svs)

    def score(self, *, candidate_svs: list[str], resolved_short: set[str]) -> int:
        """Disambiguation score for routing when multiple families match.

        Tier 1 — any dev-finance SV whose recipient suffix is in resolved_short
        (entity-specific SV in the raw recall) → 2 (unambiguously dev-finance).
        Tier 2 — raw recall has many CRS_DAC SVs (aggregate query) → 1.
        Else → 0 (let the registry order decide; standard wins by default).

        Args:
            candidate_svs:  The raw recall SV list (before confirm capping).
            resolved_short: Set of short forms of resolved entity dcids (both
                            the dcid itself and its last path segment).
        """
        from qre.engine.config import QRE_MAX_CONFIRM_CANDIDATES  # noqa: PLC0415

        df_svs = [sv for sv in candidate_svs if is_dev_finance_sv(sv)]
        # Tier 1: entity-specific SV encodes the resolved entity as a suffix
        if any(sv.split("-")[-1] in resolved_short for sv in df_svs):
            return 2
        # Tier 2: high CRS_DAC count → aggregate dev-finance query
        if len(df_svs) >= QRE_MAX_CONFIRM_CANDIDATES // 2:
            return 1
        return 0

    def resolve(
        self,
        *,
        shape: "ShapeDraft",
        bindings: "list[SlotBindingDraft]",
        recipient_dcid: str | None,
        donor_dcid: str | None,
        graph: "EngineGraphClient",
        date_request: "DateRequest | None" = None,
    ) -> "Materialised | NoDataDraft":
        """Resolve dev-finance slots to confirmed SVs and observation facets.

        Strategy:
          1. Try construct_sv_dcid(scheme, purpose, recipient) + confirm. This
             is the exact materialise path preserving df-04, df-09, and df-12
             behaviour.
          2. If construct yields nothing, fall through to
             discover.graph_confirm_resolve as a general fallback.
        """
        # --- Construct path (the primary dev-finance path) ---
        result = _construct_resolve(
            bindings=bindings,
            recipient_dcid=recipient_dcid,
            donor_dcid=donor_dcid,
            graph=graph,
            date_request=date_request,
        )
        if result is not None:
            return result

        # --- General fallback: graph-confirm via arc facts on the shape ---
        # Reached when construct_sv_dcid cannot run (e.g. purpose genuinely
        # unresolvable, or no slot bindings at all).
        from qre.engine.discover import graph_confirm_resolve  # noqa: PLC0415

        return graph_confirm_resolve(
            shape=shape,
            bindings=bindings,
            recipient_dcid=recipient_dcid,
            donor_dcid=donor_dcid,
            graph=graph,
            date_request=date_request,
            facet_label="donors",
            obs_label="years",
        )


DEV_FINANCE_RESOLVER = _DevFinanceResolver()

# ---------------------------------------------------------------------------
# FamilyRule for dev-finance
# ---------------------------------------------------------------------------

DEV_FINANCE_RULE = FamilyRule(
    label="development finance flows (CRS DAC)",
    namespace=_SV_PREFIX,
    resolver=DEV_FINANCE_RESOLVER,
    shape_id=FAMILY_ID,          # "dev_finance_crs_dac" — stable for spec_id computation
    axis_pins=DEV_FINANCE_AXIS_PINS,
)
