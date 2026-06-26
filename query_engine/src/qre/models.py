"""QRE v1 contract: all typed models, ordered leaf-to-root so no forward references are needed.

The four frozen enums are defined as module-level type aliases so the test suite
can import them directly. Every field carries a description; these descriptions are
public (they appear in the runtime OpenAPI and MCP schemas generated from these
models), so they describe the contract for external consumers and carry no internal
roadmap or implementation detail.
"""
from __future__ import annotations

from typing import Annotated, Literal, Union

from pydantic import BaseModel, Field, RootModel, model_validator

SCHEMA_VERSION = "1.0"

# ---------------------------------------------------------------------------
# The four frozen enums (contract.md "The four frozen enums").
# Any change to these values is a MAJOR version bump by contract.
# ---------------------------------------------------------------------------
StatusLiteral = Literal["definite", "candidates", "no_data"]
BindingKind = Literal["value", "set", "unbound", "absent"]
Axis = Literal["what", "how", "where", "when", "source"]
CoverageKind = Literal["exact", "breadth", "bare"]

# ---------------------------------------------------------------------------
# Other closed (but not "frozen" in the contract's sense) enums.
# ---------------------------------------------------------------------------
EntryPath = Literal["raw_text", "parsed", "spec_resubmit"]
Direction = Literal["from", "to"]
ValueKind = Literal["entity", "enum_value", "time_window", "source", "literal"]
PipelineStepName = Literal["extract", "recall", "shape", "bind", "materialise", "answer"]
DateSource = Literal["query", "default", "coverage_clamp"]
NoDataReason = Literal[
    "no_observations",
    "entity_not_resolved",
    "variable_not_resolved",
    "denominator_not_available",
]
WarningSeverity = Literal["info", "warn", "degraded"]
CoverageOption = Literal["auto", "prefer_exact", "breadth_only", "none"]
Ordering = Literal["broadest_first"]


# ---------------------------------------------------------------------------
# Leaf types
# ---------------------------------------------------------------------------


class GraphRef(BaseModel):
    """Every graph identifier in the contract."""

    dcid: str = Field(description="the graph identifier")
    label: str = Field(description="plain-language display name, read from the graph")


class TimeWindow(BaseModel):
    """A date window. Absence of a bound means open-ended in that direction."""

    start_year: int | None = Field(default=None, description="absent means open-ended start")
    end_year: int | None = Field(
        default=None, description="absent means open-ended end (to latest)"
    )

    @model_validator(mode="after")
    def at_least_one_bound(self) -> TimeWindow:
        if self.start_year is None and self.end_year is None:
            raise ValueError(
                "TimeWindow needs at least one of start_year or end_year."
            )
        return self


def in_window(date_str: str, window: TimeWindow | None) -> bool:
    """Return True if date_str (e.g. '2018') falls within the given window.

    Shared cross-module: imported by engine/graph.py and eval/graph.py.
    No leading underscore — it is a deliberately shared utility, not module-private.
    """
    if window is None:
        return True
    try:
        year = int(str(date_str)[:4])
    except (ValueError, TypeError):
        return True  # non-year date strings: include by default
    if window.start_year is not None and year < window.start_year:
        return False
    if window.end_year is not None and year > window.end_year:
        return False
    return True


class SlotValue(BaseModel):
    """One taxonomy member or facet value bound to a slot."""

    ref: GraphRef | None = Field(
        default=None,
        description="the graph object bound, when the value is a graph object",
    )
    value_kind: ValueKind = Field(
        description="what kind of value this is"
    )
    time_window: TimeWindow | None = Field(
        default=None, description="present iff value_kind == time_window"
    )
    literal: str | None = Field(
        default=None, description="present iff value_kind == literal"
    )


class SlotKey(BaseModel):
    """A slot's typed identity: axis, constraint property, and human label."""

    axis: Axis = Field(description="the slot grammar axis")
    property: GraphRef | None = Field(
        default=None,
        description=(
            "the constraint property that defines this slot when one exists; "
            "null for when, source, and the from-direction subject slot"
        ),
    )
    label: str = Field(description="human label, e.g. purpose, donor")


# ---------------------------------------------------------------------------
# The four Binding arms
# ---------------------------------------------------------------------------


class BindingValue(BaseModel):
    """A slot bound to exactly one taxonomy member."""

    kind: Literal["value"] = Field(default="value", description="binding kind discriminator")
    value: SlotValue = Field(description="exactly one taxonomy member")


class BindingSet(BaseModel):
    """A slot bound to two or more taxonomy members, all in scope."""

    kind: Literal["set"] = Field(default="set", description="binding kind discriminator")
    values: Annotated[list[SlotValue], Field(min_length=2)] = Field(
        description="two or more members, all in scope (a union)"
    )
    set_ref: GraphRef | None = Field(
        default=None,
        description="the group the set was drawn from, when one exists",
    )


class BindingUnbound(BaseModel):
    """A slot explicitly open to the whole slot in scope, NOT null or missing."""

    kind: Literal["unbound"] = Field(
        default="unbound", description="explicit whole slot in scope, NOT null/missing"
    )


class BindingAbsent(BaseModel):
    """A slot whose constraint property is not present on the resolved StatVars."""

    kind: Literal["absent"] = Field(
        default="absent",
        description="the slot's constraint property is not present on the resolved StatVars",
    )


Binding = Annotated[
    Union[BindingValue, BindingSet, BindingUnbound, BindingAbsent],
    Field(discriminator="kind"),
]


class Slot(BaseModel):
    """One slot in the chosen shape, with its current binding state."""

    key: SlotKey = Field(description="the slot's typed identity (axis + property + label)")
    binding: Binding = Field(description="exactly one of the four binding states")


# ---------------------------------------------------------------------------
# Shape
# ---------------------------------------------------------------------------


class Shape(BaseModel):
    """The structural family chosen for the query."""

    shape_id: str = Field(description="stable id of this structural family")
    label: str = Field(
        description="plain-language family name, e.g. development finance flows"
    )
    population_type: GraphRef = Field(description="the graph populationType")
    measured_property: GraphRef = Field(description="the graph measuredProperty")
    stat_type: GraphRef = Field(
        description="the graph statType, e.g. measuredValue"
    )
    measurement_qualifier: GraphRef | None = Field(
        default=None,
        description="structural anchor; present only when the measured property is qualified",
    )
    measurement_denominator: GraphRef | None = Field(
        default=None,
        description="structural anchor; present only on a ratio or per-capita measure",
    )
    slot_keys: list[SlotKey] = Field(
        description="the constraint and facet slots this family exposes, in display order"
    )
    member_count: int = Field(
        description="how many candidate StatVars fell into this family"
    )


# ---------------------------------------------------------------------------
# StatVar
# ---------------------------------------------------------------------------


class StatVarSlotValue(BaseModel):
    """A (key, value) pair recording one slot member that a StatVar realises."""

    key: SlotKey = Field(description="the slot identity this StatVar realises")
    value: SlotValue = Field(description="the slot member value this StatVar carries")


class StatVar(BaseModel):
    """A resolved StatVar inside the region."""

    ref: GraphRef = Field(
        description="the resolved StatVar; exists in the graph"
    )
    shape_id: str = Field(description="back-reference to its Shape family")
    slot_values: list[StatVarSlotValue] = Field(
        description="the slot members this StatVar realises, one per bound slot"
    )


# ---------------------------------------------------------------------------
# Entity and EntityRole
# ---------------------------------------------------------------------------


class EntityRoleSubject(BaseModel):
    """The entity is the primary subject of the data."""

    kind: Literal["subject"] = Field(
        default="subject", description="the entity the data is about (default)"
    )


class EntityRoleDirectional(BaseModel):
    """The entity is a directional endpoint (from or to) in a flow."""

    kind: Literal["directional"] = Field(
        default="directional", description="directional flow endpoint role discriminator"
    )
    role: GraphRef = Field(description="the directional role, e.g. recipient")
    direction: Direction = Field(description="from or to")


EntityRole = Annotated[
    Union[EntityRoleSubject, EntityRoleDirectional],
    Field(discriminator="kind"),
]


class Entity(BaseModel):
    """A resolved entity the data is about."""

    ref: GraphRef = Field(
        description="the resolved entity; exists in the graph"
    )
    entity_type: GraphRef | None = Field(
        default=None,
        description="e.g. Country, AdministrativeArea1, Organization",
    )
    role: EntityRole = Field(
        description="always present; defaults to the subject role"
    )


# ---------------------------------------------------------------------------
# Coverage arms
# ---------------------------------------------------------------------------


class BreadthDim(BaseModel):
    """One breadth dimension with its cardinality."""

    label: str = Field(
        description="the dimension, e.g. donors, years, methods, sources"
    )
    count: int = Field(description="e.g. 36, 22, 3")


class CoverageExact(BaseModel):
    """Observation footprint with an exact count."""

    kind: Literal["exact"] = Field(default="exact", description="coverage kind discriminator")
    has_data: bool = Field(description="whether the region has any observations")
    observation_count: int = Field(
        description=(
            "distinct (date, facet) observation pairs for resolved stat_vars and "
            "entities within the window"
        )
    )
    dimensions: list[BreadthDim] | None = Field(
        default=None,
        description="may ride along when cheap",
    )
    window: TimeWindow | None = Field(
        default=None, description="the date window this count applies to"
    )


class CoverageBreadth(BaseModel):
    """Observation footprint expressed as structured breadth dimensions."""

    kind: Literal["breadth"] = Field(default="breadth", description="coverage kind discriminator")
    has_data: bool = Field(description="whether the region has any observations")
    dimensions: Annotated[list[BreadthDim], Field(min_length=1)] = Field(
        description="non-empty on breadth; structured dimension counts"
    )
    window: TimeWindow | None = Field(
        default=None, description="the date window this breadth applies to"
    )


class CoverageBare(BaseModel):
    """Observation footprint with no count or dimensions; an honest could-not-count."""

    kind: Literal["bare"] = Field(default="bare", description="coverage kind discriminator")
    has_data: bool = Field(description="whether the region has any observations")
    window: TimeWindow | None = Field(
        default=None, description="the date window, when known"
    )


Coverage = Annotated[
    Union[CoverageExact, CoverageBreadth, CoverageBare],
    Field(discriminator="kind"),
]


# ---------------------------------------------------------------------------
# ResolutionTrace
# ---------------------------------------------------------------------------


class ResolvedFilter(BaseModel):
    """One applied bind decision, slot by slot."""

    key: SlotKey = Field(description="the slot identity")
    binding_kind: BindingKind = Field(
        description="which binding state was applied"
    )
    refs: list[GraphRef] = Field(
        description="the bound graph objects (empty for unbound)"
    )


class PipelineStep(BaseModel):
    """One step in the resolution pipeline, with whether it ran and its latency."""

    step: PipelineStepName = Field(description="which pipeline step this record describes")
    ran: bool = Field(description="false when this step was skipped for the request")
    ms: int | None = Field(default=None, description="step latency, when measured")


class ResolutionTrace(BaseModel):
    """Consolidated record of how the query was understood."""

    resolved_stat_vars: list[GraphRef] = Field(
        description="every StatVar behind this spec"
    )
    resolved_entities: list[GraphRef] = Field(description="every entity")
    resolved_sources: list[GraphRef] = Field(
        description="every dataset/source (a Data Commons provenance)"
    )
    slot_filters: list[ResolvedFilter] = Field(
        description="each applied bind decision, slot by slot"
    )
    applied_window: TimeWindow | None = Field(
        default=None, description="the date signal actually applied"
    )
    date_source: DateSource | None = Field(
        default=None, description="why that window was applied"
    )
    pipeline_trace: list[PipelineStep] = Field(
        description="the pipeline steps, in order, with skip and timing"
    )


# ---------------------------------------------------------------------------
# Spec
# ---------------------------------------------------------------------------


class Spec(BaseModel):
    """The unit of meaning: one named region of the graph.

    Reused as the definite answer, each candidate, and each nearest-real suggestion.
    """

    spec_id: str = Field(
        description=(
            "deterministic hash of (shape_id + canonical slot bindings); "
            "permalink-safe, dedup key, golden-comparison handle, cache key"
        )
    )
    shape: Shape = Field(description="the structural family chosen")
    slots: list[Slot] = Field(
        description="one entry per slot the shape defines, each in a binding state"
    )
    stat_vars: list[StatVar] = Field(
        description="resolved StatVar(s) inside the region; >= 1 when grounded"
    )
    entities: list[Entity] = Field(
        description="resolved entities the data is about; may be empty if where is unbound"
    )
    coverage: Coverage = Field(description="observation footprint")
    resolution: ResolutionTrace = Field(
        description="consolidated record of how the query was understood"
    )


# ---------------------------------------------------------------------------
# CandidateSet and NoData
# ---------------------------------------------------------------------------


class CandidateSet(BaseModel):
    """A set of 2..N competing Specs, ordered broadest-first."""

    ordering: Ordering = Field(
        default="broadest_first",
        description="constant in v1; named so the contract is self-describing",
    )
    max_candidates: int = Field(description="the maximum number of candidates applied")
    specs: Annotated[list[Spec], Field(min_length=2)] = Field(
        description="2 <= len <= max_candidates; full Specs, broadest to narrowest"
    )


class NoData(BaseModel):
    """Outcome when no grounded result can be returned."""

    reason: NoDataReason = Field(description="why nothing could be returned")
    nearest_real: list[Spec] | None = Field(
        default=None,
        description=(
            "optional; real, grounded specs with data adjacent to the request"
        ),
    )


# ---------------------------------------------------------------------------
# QueryEcho, Warning, Timing, Diagnostics
# ---------------------------------------------------------------------------


class QueryEcho(BaseModel):
    """What the response was understood to be answering."""

    entry_path: EntryPath = Field(description="which input was used")
    raw_query: str | None = Field(
        default=None, description="the original query text, when one was supplied"
    )
    normalized_query: str | None = Field(
        default=None,
        description="normalised query text, when extraction ran",
    )
    variable_text: list[str] = Field(
        description="the variable text resolved, after conjunction split"
    )
    extract_skipped: bool = Field(
        description="true when the extraction step was skipped for this request"
    )


class Warning(BaseModel):
    """A run-health signal. Note: this name shadows the Python builtin Warning."""

    code: str = Field(
        description=(
            "stable machine code, e.g. DATE_COVERAGE_PARTIAL, COUNT_UNAVAILABLE, "
            "CONJUNCTION_CROSS_SHAPE"
        )
    )
    severity: WarningSeverity = Field(description="the severity level")
    message: str = Field(description="human-readable explanation of the warning")


class Timing(BaseModel):
    """Latency breakdown for the response."""

    total: int = Field(description="total run latency in milliseconds")
    by_step: dict[str, int] | None = Field(
        default=None, description="per-step latency in ms"
    )


class Diagnostics(BaseModel):
    """Run health, warnings, timing, and build stamp."""

    engine_build: str = Field(
        description="engine build id; distinct from schema_version"
    )
    warnings: list[Warning] = Field(
        description="degraded-mode, applied-signal, partial-graph signals"
    )
    timing_ms: Timing | None = Field(default=None, description="latency breakdown")


# ---------------------------------------------------------------------------
# Response root: three variants on a shared base, discriminated on status
# ---------------------------------------------------------------------------


class _ResponseBase(BaseModel):
    """Shared envelope fields present on every response variant."""

    schema_version: Literal["1.0"] = Field(
        default=SCHEMA_VERSION,
        description='semver MAJOR.MINOR, "1.0" for v1. Required on every response.',
    )
    query_echo: QueryEcho = Field(
        description="what the response was understood to be answering"
    )
    diagnostics: Diagnostics = Field(
        description="run health, warnings, timing, build stamp"
    )


class DefiniteResponse(_ResponseBase):
    """One unambiguous answer was resolved."""

    status: Literal["definite"] = Field(
        default="definite", description="one answer was resolved"
    )
    interpretation: Spec = Field(
        description="the single resolved Spec; present iff status==definite"
    )
    additional_interpretations: list[Spec] | None = Field(
        default=None,
        description=(
            "present only on definite, only for a genuine cross-shape conjunction; "
            "each a full grounded Spec. "
            "None (field absent) is the normal single-region answer; "
            "the empty list [] signals a cross-shape conjunction detected but not yet resolved; "
            "a populated list carries the resolved parts."
        ),
    )


class CandidatesResponse(_ResponseBase):
    """The query was genuinely ambiguous; two or more distinct specs compete."""

    status: Literal["candidates"] = Field(
        default="candidates", description="the query was genuinely ambiguous"
    )
    candidates: CandidateSet = Field(
        description="2..N competing Specs; present iff status==candidates"
    )


class NoDataResponse(_ResponseBase):
    """Nothing could be returned for this query."""

    status: Literal["no_data"] = Field(default="no_data", description="nothing to return")
    no_data: NoData = Field(
        description="the reason; present iff status==no_data"
    )


class ResolveResponse(
    RootModel[
        Annotated[
            Union[DefiniteResponse, CandidatesResponse, NoDataResponse],
            Field(discriminator="status"),
        ]
    ]
):
    """A resolved response, discriminated on status.

    Usage::

        resp = ResolveResponse.model_validate(obj)
        variant = resp.root   # DefiniteResponse | CandidatesResponse | NoDataResponse
        variant.status        # "definite" | "candidates" | "no_data"
    """


# ---------------------------------------------------------------------------
# Request root: three input arms, discriminated on kind
# ---------------------------------------------------------------------------


class RawTextInput(BaseModel):
    """Natural-language query input. The engine extracts, resolves, and grounds it."""

    kind: Literal["raw_text"] = Field(
        default="raw_text", description="discriminator for natural-language query input"
    )
    query: str = Field(description="the user's natural-language request")


class ParsedInput(BaseModel):
    """Pre-parsed input whose fields are supplied directly by the caller.

    Not supported in v1; an engine rejects kind=parsed with HTTP 400.
    """

    kind: Literal["parsed"] = Field(
        default="parsed",
        description=(
            "discriminator for pre-parsed input; not supported in v1, "
            "rejected with HTTP 400"
        ),
    )
    variable_text: list[str] = Field(
        description="one item per variable; conjunctions pre-split"
    )
    entity_text: list[str] | None = Field(
        default=None, description="extracted entity text fragments, when available"
    )
    time_text: str | None = Field(
        default=None, description="extracted time expression, when available"
    )
    source_text: str | None = Field(
        default=None, description="extracted source expression, when available"
    )
    raw_query: str | None = Field(
        default=None,
        description="original query text, for echo only; does not affect resolution",
    )


class SpecResubmitInput(BaseModel):
    """Re-resolve from a chosen shape and slot bindings: refine, promote a candidate, or explore."""

    kind: Literal["spec_resubmit"] = Field(
        default="spec_resubmit",
        description="discriminator for re-resolution from a shape and slot bindings",
    )
    shape_id: str = Field(description="the shape to re-materialise")
    slots: list[Slot] = Field(
        description="the slot bindings to apply; the client may edit any binding"
    )


ResolveInput = Annotated[
    Union[RawTextInput, ParsedInput, SpecResubmitInput],
    Field(discriminator="kind"),
]


class ResolveOptions(BaseModel):
    """Optional resolution parameters the caller may supply."""

    max_candidates: int | None = Field(
        default=None, description="requested maximum candidates; clamped to the server ceiling"
    )
    coverage: CoverageOption | None = Field(
        default=None,
        description="cost/latency knob; default auto",
    )
    place_as_constraint: bool | None = Field(
        default=None,
        description="overrides the server default for place-as-constraint handling",
    )


class ResolveRequest(BaseModel):
    """The single request type for the QRE resolve endpoint."""

    schema_version: Literal["1.0"] = Field(
        default=SCHEMA_VERSION,
        description="contract version the caller targets",
    )
    input: ResolveInput = Field(description="tagged union; kind discriminates")
    options: ResolveOptions | None = Field(
        default=None, description="optional resolution options"
    )
