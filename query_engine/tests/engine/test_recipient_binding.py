"""Regression: the recipient (where) slot binds deterministically regardless of the
bind LLM's output.

The bind prompt intentionally omits the raw query, so the LLM has no place name in the
variable phrase to match and either returns the recipient unbound or drops the row
entirely. Either way the engine must set the where slot from the deterministically
resolved recipient dcid, so the interpretation carries the recipient and the answer
materialises.
"""
from __future__ import annotations

import asyncio

import pytest

from qre.engine.bind import SlotBindingDraft, _BindOutput
from qre.engine.core import resolve_async
from qre.engine.extract import Extraction
from qre.models import RawTextInput, ResolveRequest
from tests.fixtures import FakeGraph


class _FakeBindLLM:
    """Mimics live: extract finds Ethiopia; bind handles the where row per where_mode.

    where_mode "unbound" returns the offered where row as kind=unbound; "omit" drops
    the where row entirely (the LLM ignoring "one binding per offered slot").
    """

    def __init__(self, where_mode: str):
        self._where_mode = where_mode

    def generate_structured(self, *, prompt, system, schema):
        name = schema.__name__
        if name == "Extraction":
            return Extraction(variables=["health ODA grants"], entities=["Ethiopia"])
        if name == "_BindOutput":
            rows = [
                SlotBindingDraft(
                    axis="what", property_dcid="DevelopmentFinanceScheme",
                    kind="value", value_dcids=["ODAGrants"],
                ),
                SlotBindingDraft(
                    axis="how", property_dcid="DevelopmentFinancePurpose",
                    kind="value", value_dcids=["DAC/Health"],
                ),
            ]
            if self._where_mode == "unbound":
                rows.append(
                    SlotBindingDraft(
                        axis="where", property_dcid="DevelopmentFinanceRecipient",
                        kind="unbound", value_dcids=[],
                    )
                )
            return _BindOutput(bindings=rows)
        raise AssertionError(f"unexpected schema {name}")


@pytest.mark.parametrize("where_mode", ["unbound", "omit"])
def test_recipient_bound_regardless_of_llm(where_mode):
    req = ResolveRequest(input=RawTextInput(query="health ODA grants to Ethiopia"))
    resp = asyncio.run(resolve_async(req, graph=FakeGraph(), llm=_FakeBindLLM(where_mode)))

    assert resp.root.status == "definite", resp.root
    where = next(s for s in resp.root.interpretation.slots if s.key.axis == "where")
    assert where.binding.kind == "value"
    assert where.binding.value.ref.dcid == "country/ETH"
