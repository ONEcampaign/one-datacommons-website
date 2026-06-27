"""QRE v1 public surface.

Import the two roots and all nested types from here::

    from qre import ResolveRequest, ResolveResponse

Validate a response payload::

    resp = ResolveResponse.model_validate(obj)
    variant = resp.root   # DefiniteResponse | CandidatesResponse | NoDataResponse
    variant.status        # "definite" | "candidates" | "no_data"

Note: ``Warning`` is importable as ``qre.Warning`` or via explicit
``from qre import Warning``, but is omitted from ``__all__`` so that
``from qre import *`` does not shadow the Python builtin ``Warning``.
"""
from qre.models import (  # noqa: I001
    SCHEMA_VERSION,
    Axis,
    Binding,
    BindingAbsent,
    BindingKind,
    BindingSet,
    BindingUnbound,
    BindingValue,
    BreadthDim,
    CandidateSet,
    CandidatesResponse,
    Coverage,
    CoverageBare,
    CoverageBreadth,
    CoverageExact,
    CoverageKind,
    DefiniteResponse,
    Diagnostics,
    Direction,
    Entity,
    EntityRole,
    EntityRoleDirectional,
    EntityRoleSubject,
    GraphRef,
    NoData,
    NoDataReason,
    NoDataResponse,
    ParsedInput,
    PipelineStep,
    QueryEcho,
    RawTextInput,
    ResolutionTrace,
    ResolvedFilter,
    ResolveInput,
    ResolveOptions,
    ResolveRequest,
    ResolveResponse,
    Shape,
    Slot,
    SlotKey,
    SlotValue,
    Spec,
    SpecResubmitInput,
    StatVar,
    StatVarSlotValue,
    StatusLiteral,
    Timing,
    TimeWindow,
    ValueKind,
    Warning,  # noqa: F401 — importable as qre.Warning; omitted from __all__ to avoid shadowing builtin
)
from qre.render import no_data_phrase, render_candidates_summary, render_sentence


__all__ = [
    "SCHEMA_VERSION",
    "Axis",
    "Binding",
    "BindingAbsent",
    "BindingKind",
    "BindingSet",
    "BindingUnbound",
    "BindingValue",
    "BreadthDim",
    "CandidateSet",
    "CandidatesResponse",
    "Coverage",
    "CoverageBare",
    "CoverageBreadth",
    "CoverageExact",
    "CoverageKind",
    "DefiniteResponse",
    "Diagnostics",
    "Direction",
    "Entity",
    "EntityRole",
    "EntityRoleDirectional",
    "EntityRoleSubject",
    "GraphRef",
    "NoData",
    "NoDataReason",
    "NoDataResponse",
    "ParsedInput",
    "PipelineStep",
    "QueryEcho",
    "RawTextInput",
    "no_data_phrase",
    "render_candidates_summary",
    "render_sentence",
    "ResolutionTrace",
    "ResolvedFilter",
    "ResolveInput",
    "ResolveOptions",
    "ResolveRequest",
    "ResolveResponse",
    "Shape",
    "Slot",
    "SlotKey",
    "SlotValue",
    "Spec",
    "SpecResubmitInput",
    "StatVar",
    "StatVarSlotValue",
    "StatusLiteral",
    "Timing",
    "TimeWindow",
    "ValueKind",
    # Warning intentionally omitted from __all__ to avoid shadowing the Python builtin.
]
