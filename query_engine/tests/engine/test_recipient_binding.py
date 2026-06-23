"""Regression: the recipient (where) slot binds deterministically even when the bind
LLM returns it unbound.

Reproduces the live drift where the bind LLM, given only the variable phrase (the raw
query is intentionally not echoed into the bind prompt), returns the recipient unbound.
The engine must override that from the deterministically-resolved recipient dcid, so the
interpretation still carries the recipient and the answer materialises.
"""
from __future__ import annotations

import asyncio

from qre.engine.bind import SlotBindingDraft, _BindOutput
from qre.engine.core import resolve_async
from qre.engine.extract import Extraction
from qre.models import RawTextInput, ResolveRequest
from tests.fixtures import FakeGraph


class _RecipientUnboundLLM:
    """Mimics live: extract finds Ethiopia; bind returns the recipient unbound."""

    def generate_structured(self, *, prompt, system, schema):
        name = schema.__name__
        if name == "Extraction":
            return Extraction(variables=["health ODA grants"], entities=["Ethiopia"])
        if name == "_BindOutput":
            return _BindOutput(
                bindings=[
                    SlotBindingDraft(
                        axis="what", property_dcid="DevelopmentFinanceScheme",
                        kind="value", value_dcids=["ODAGrants"],
                    ),
                    SlotBindingDraft(
                        axis="how", property_dcid="DevelopmentFinancePurpose",
                        kind="value", value_dcids=["DAC/Health"],
                    ),
                    SlotBindingDraft(
                        axis="where", property_dcid="DevelopmentFinanceRecipient",
                        kind="unbound", value_dcids=[],
                    ),
                ]
            )
        raise AssertionError(f"unexpected schema {name}")


def test_recipient_bound_when_llm_returns_unbound():
    req = ResolveRequest(input=RawTextInput(query="health ODA grants to Ethiopia"))
    resp = asyncio.run(resolve_async(req, graph=FakeGraph(), llm=_RecipientUnboundLLM()))

    assert resp.root.status == "definite", resp.root
    where = next(s for s in resp.root.interpretation.slots if s.key.axis == "where")
    assert where.binding.kind == "value"
    assert where.binding.value.ref.dcid == "country/ETH"
