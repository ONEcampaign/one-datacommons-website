"""Shared fixtures for the QRE test suite.

``worked_example_response`` is the contract's worked example from contract.md
("health ODA grants to Ethiopia since 2015"), transcribed as a Python dict
valid for a complete ``DefiniteResponse``. It is the primary round-trip subject.
"""
import pytest

from qre.models import (
    GraphRef,
    SlotKey,
    SlotValue,
    StatVarSlotValue,
)


def minimal_spec(spec_id: str = "s1") -> dict:
    """A contract-valid minimal Spec dict, shared across the test suite."""
    return {
        "spec_id": spec_id,
        "shape": {
            "shape_id": "sh1",
            "label": "test shape",
            "population_type": {"dcid": "Pop", "label": "Pop"},
            "measured_property": {"dcid": "Prop", "label": "Prop"},
            "stat_type": {"dcid": "measuredValue", "label": "measured value"},
            "slot_keys": [],
            "member_count": 1,
        },
        "slots": [],
        "stat_vars": [],
        "entities": [],
        "coverage": {"kind": "bare", "has_data": True},
        "resolution": {
            "resolved_stat_vars": [],
            "resolved_entities": [],
            "resolved_sources": [],
            "slot_filters": [],
            "pipeline_trace": [],
        },
    }


def base_response(**extra) -> dict:
    """A minimal valid response envelope; pass status + body via kwargs."""
    return {
        "schema_version": "1.0",
        "query_echo": {
            "entry_path": "raw_text",
            "variable_text": ["test"],
            "extract_skipped": False,
        },
        "diagnostics": {"engine_build": "test-build", "warnings": []},
        **extra,
    }


_STAT_VAR_DCID = "ONE/CRS_DAC/Health-ODAGrants-ETH"
_STAT_VAR_LABEL = "ODA grants, health total, to Ethiopia"
_STAT_VAR_REF = {"dcid": _STAT_VAR_DCID, "label": _STAT_VAR_LABEL}
_ETH_REF = {"dcid": "country/ETH", "label": "Ethiopia"}
_SCHEME_PROP = {"dcid": "DevelopmentFinanceScheme", "label": "flow type"}
_PURPOSE_PROP = {"dcid": "DevelopmentFinancePurpose", "label": "purpose"}
_RECIPIENT_PROP = {"dcid": "DevelopmentFinanceRecipient", "label": "recipient"}
_ODA_GRANTS_REF = {"dcid": "ODAGrants", "label": "ODA grants"}
_DAC_HEALTH_REF = {"dcid": "DAC/Health", "label": "health (total)"}


def _minimal_resolution() -> dict:
    """A contract-valid ResolutionTrace with all required fields populated."""
    return {
        "resolved_stat_vars": [_STAT_VAR_REF],
        "resolved_entities": [_ETH_REF],
        "resolved_sources": [],
        "slot_filters": [
            {
                "key": {"axis": "what", "property": _SCHEME_PROP, "label": "flow type"},
                "binding_kind": "value",
                "refs": [_ODA_GRANTS_REF],
            },
            {
                "key": {"axis": "how", "property": _PURPOSE_PROP, "label": "purpose"},
                "binding_kind": "value",
                "refs": [_DAC_HEALTH_REF],
            },
            {
                "key": {"axis": "where", "property": _RECIPIENT_PROP, "label": "recipient"},
                "binding_kind": "value",
                "refs": [_ETH_REF],
            },
            {
                "key": {"axis": "when", "label": "period"},
                "binding_kind": "value",
                "refs": [],
            },
            {
                "key": {"axis": "source", "label": "source"},
                "binding_kind": "unbound",
                "refs": [],
            },
        ],
        "applied_window": {"start_year": 2015},
        "date_source": "query",
        "pipeline_trace": [
            {"step": "extract", "ran": True},
            {"step": "recall", "ran": True},
            {"step": "shape", "ran": True},
            {"step": "bind", "ran": True},
            {"step": "materialise", "ran": True},
            {"step": "answer", "ran": True},
        ],
    }


_WORKED_SLOT_VALUE = StatVarSlotValue(
    key=SlotKey(
        axis="what",
        property=GraphRef(dcid="DevelopmentFinanceScheme", label="flow type"),
        label="flow type",
    ),
    value=SlotValue(
        value_kind="enum_value",
        ref=GraphRef(dcid="ODAGrants", label="ODA grants"),
    ),
)


@pytest.fixture
def worked_example_response() -> dict:
    """The contract's worked example as a valid DefiniteResponse dict."""
    return {
        "schema_version": "1.0",
        "status": "definite",
        "query_echo": {
            "entry_path": "raw_text",
            "raw_query": "health ODA grants to Ethiopia since 2015",
            "normalized_query": "health ODA grants Ethiopia 2015",
            "variable_text": ["health ODA grants"],
            "extract_skipped": False,
        },
        "diagnostics": {
            "engine_build": "qre-2026.06.18",
            "warnings": [],
        },
        "interpretation": {
            "spec_id": "df-flow:ODAGrants:DAC/Health:country/ETH:2015:",
            "shape": {
                "shape_id": "df-flow",
                "label": "development finance flows",
                "population_type": {"dcid": "DevelopmentFinance", "label": "development finance"},
                "measured_property": {
                    "dcid": "DevelopmentFinanceFlow",
                    "label": "development finance flow",
                },
                "stat_type": {"dcid": "measuredValue", "label": "measured value"},
                "slot_keys": [
                    {"axis": "what", "property": _SCHEME_PROP, "label": "flow type"},
                    {"axis": "how", "property": _PURPOSE_PROP, "label": "purpose"},
                    {"axis": "where", "property": _RECIPIENT_PROP, "label": "recipient"},
                    {"axis": "when", "label": "period"},
                    {"axis": "source", "label": "source"},
                ],
                "member_count": 27,
            },
            "slots": [
                {
                    "key": {"axis": "what", "property": _SCHEME_PROP, "label": "flow type"},
                    "binding": {
                        "kind": "value",
                        "value": {"ref": _ODA_GRANTS_REF, "value_kind": "enum_value"},
                    },
                },
                {
                    "key": {"axis": "how", "property": _PURPOSE_PROP, "label": "purpose"},
                    "binding": {
                        "kind": "value",
                        "value": {"ref": _DAC_HEALTH_REF, "value_kind": "enum_value"},
                    },
                },
                {
                    "key": {"axis": "where", "property": _RECIPIENT_PROP, "label": "recipient"},
                    "binding": {
                        "kind": "value",
                        "value": {"ref": _ETH_REF, "value_kind": "entity"},
                    },
                },
                {
                    "key": {"axis": "when", "label": "period"},
                    "binding": {
                        "kind": "value",
                        "value": {"value_kind": "time_window", "time_window": {"start_year": 2015}},
                    },
                },
                {
                    "key": {"axis": "source", "label": "source"},
                    "binding": {"kind": "unbound"},
                },
            ],
            "stat_vars": [
                {
                    "ref": _STAT_VAR_REF,
                    "shape_id": "df-flow",
                    "slot_values": [_WORKED_SLOT_VALUE.model_dump()],
                }
            ],
            "entities": [
                {
                    "ref": _ETH_REF,
                    "entity_type": {"dcid": "Country", "label": "Country"},
                    "role": {
                        "kind": "directional",
                        "role": _RECIPIENT_PROP,
                        "direction": "to",
                    },
                }
            ],
            "coverage": {
                "kind": "breadth",
                "has_data": True,
                "dimensions": [
                    {"label": "donors", "count": 32},
                    {"label": "years", "count": 9},
                ],
                "window": {"start_year": 2015, "end_year": 2023},
            },
            "resolution": _minimal_resolution(),
        },
    }
